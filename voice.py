import collections
import ctypes
import difflib
import gc
import importlib.util
import os
import platform
import queue
import re
import tempfile
import threading
import time
import wave

import config

RATE = 16000     # Whisper wants 16 kHz mono
BLOCK = 1600     # 0.1 s of audio per block
PAUSE_BLOCKS = max(3, round(config.VOICE_PAUSE / 0.1))

# A little vocabulary hint makes Whisper much better at "sheep" and commands
PROMPT = " ".join(
    [
        f"{config.NAME.capitalize()}. Hey {config.NAME}. Open Chrome. Volume up. "
        "Set a timer for 5 minutes. What time is it. Take a screenshot."
    ]
    + list(config.VOICE_HINT_WORDS)
)
WAKE_PROMPT = f"Hey {config.NAME}."

# Voices we prefer for the sheep (lighter, higher voices first)
PREFERRED_VOICES = ("zira", "hazel", "susan", "samantha", "victoria", "karen", "tessa", "female")

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUM_CONTEXT = re.compile(
    r"\b(?:timer|remind|volume|brightness|sound|calculate|calc|plus|minus|"
    r"times|divided|percent|seconds?|minutes?|hours?)\b",
    re.IGNORECASE,
)


def numberize(text):
    """Turn spoken numbers into digits ("five minutes" -> "5 minutes"),
    but only when the sentence is about timers, volume, maths, etc."""
    text = re.sub(r"\b(?:a|an)\s+(second|minute|hour)\b", r"1 \1", text, flags=re.IGNORECASE)
    if not _NUM_CONTEXT.search(text):
        return text
    words = text.split()
    out = []
    i = 0
    while i < len(words):
        w = re.sub(r"[^\w-]", "", words[i].lower())
        parts = w.split("-")
        val = None
        used = 1
        if len(parts) == 2 and parts[0] in _TENS and parts[1] in _UNITS and 0 < _UNITS[parts[1]] < 10:
            val = _TENS[parts[0]] + _UNITS[parts[1]]
        elif w in _TENS:
            val = _TENS[w]
            if i + 1 < len(words):
                nxt = re.sub(r"[^\w]", "", words[i + 1].lower())
                if nxt in _UNITS and 0 < _UNITS[nxt] < 10:
                    val += _UNITS[nxt]
                    used = 2
        elif w in _UNITS:
            val = _UNITS[w]
        elif w == "hundred":
            val = 100
        if val is None:
            out.append(words[i])
            i += 1
        else:
            out.append(str(val))
            i += used
    return " ".join(out)


class Voice:
    """Listens on the mic, turns speech into text, runs it, and can speak back.

    on_command(text) is called (in the voice thread) with each command.
    on_state(name, info=None) is called with:
      woke      the wake word was just heard (while you're still talking)
      listening waiting for your command
      thinking  recording finished, working out what you said
      heard     the command text (info)
      cancel    false alarm, go back to normal
      timeout   nothing was heard
      loading / ready / error
    """

    def __init__(self, on_command, on_state=None):
        self.on_command = on_command
        self.on_state = on_state or (lambda state, info=None: None)
        self.mode = config.VOICE_MODE      # "push" or "wake"
        self.enabled = True
        self.running = False
        self.force = False                 # next utterance is a command, no wake word needed
        self.force_at = 0.0
        self.go = threading.Event()        # wakes the loop in push mode
        self.speaking = False
        self.noise = 0.005                 # running estimate of room noise
        self.models = {}
        self.model_used = time.time()
        self._model_lock = threading.Lock()
        self._warned_tts = False

    # -----------------------------------------------------------
    # Public API
    # -----------------------------------------------------------

    def start(self):
        missing = [m for m in ("sounddevice", "numpy", "faster_whisper")
                   if importlib.util.find_spec(m) is None]
        if missing:
            raise ImportError(
                "missing " + ", ".join(missing) + "  (run: pip install -r requirements.txt)"
            )
        self.running = True
        threading.Thread(target=self._preload, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()

    def toggle(self):
        """Mic on/off. Returns the new state."""
        self.enabled = not self.enabled
        self.force = False
        self.go.clear()
        return self.enabled

    def listen_now(self):
        """Start listening immediately (no wake word needed)."""
        if not self.running:
            return
        self.enabled = True
        self.force = True
        self.force_at = time.time()
        self.go.set()
        self.on_state("listening")

    # -----------------------------------------------------------
    # Speaking: a lighter voice, pitched up
    # -----------------------------------------------------------

    def _pick_voice(self, engine):
        try:
            voices = engine.getProperty("voices")
            wanted = [config.VOICE_TTS_NAME.lower()] if config.VOICE_TTS_NAME else list(PREFERRED_VOICES)
            for want in wanted:
                for v in voices:
                    if want and want in (v.name or "").lower():
                        engine.setProperty("voice", v.id)
                        return
            if platform.system() == "Linux":
                engine.setProperty("voice", "english+f4")   # espeak's higher female variant
        except Exception:
            pass

    def _play_shifted(self, path, pitch):
        """Play a WAV file sped up by `pitch` (raises the pitch). False if it can't."""
        try:
            import numpy as np
            import sounddevice as sd

            with wave.open(path, "rb") as wf:
                rate, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
                raw = wf.readframes(wf.getnframes())
            if width != 2 or not raw:
                return False
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if channels > 1:
                x = x.reshape(-1, channels).mean(axis=1)
            n_out = int(len(x) / pitch)
            if n_out < 100:
                return False
            y = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
            fade = min(300, n_out // 4)
            y[-fade:] *= np.linspace(1.0, 0.0, fade)       # soft fade-out, no click
            sd.play(y.astype(np.int16), samplerate=rate)
            sd.wait()
            return True
        except Exception:
            return False

    def speak(self, text):
        """Say something out loud. Blocks until finished."""
        text = (text or "").strip()
        if not text:
            return
        self.speaking = True
        com = False
        path = None
        try:
            if platform.system() == "Windows":
                ctypes.windll.ole32.CoInitialize(None)
                com = True
            import pyttsx3

            engine = pyttsx3.init()
            self._pick_voice(engine)
            pitch = max(0.8, min(1.8, float(config.VOICE_PITCH)))
            played = False
            if abs(pitch - 1.0) > 0.02:
                # Speak slower into a file, then play it faster: higher pitch, normal speed
                engine.setProperty("rate", int(config.VOICE_RATE / pitch))
                fd, path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                engine.save_to_file(text, path)
                engine.runAndWait()
                played = self._play_shifted(path, pitch)
            if not played:
                engine.setProperty("rate", config.VOICE_RATE)
                engine.say(text)
                engine.runAndWait()
            engine.stop()
        except Exception as e:
            if not self._warned_tts:
                print(f"\n[voice] can't speak (pip install pyttsx3): {e}")
                self._warned_tts = True
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
            if com:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
            time.sleep(0.3)   # let the room echo die so we don't hear ourselves
            self.speaking = False

    # -----------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------

    def _loop(self):
        while self.running:
            self._maybe_unload()

            if not self.enabled or self.speaking:
                time.sleep(0.2)
                continue

            if self.mode == "push" and not self.force:
                self.go.wait(0.5)
                continue

            try:
                audio, woke = self._capture()
            except Exception as e:
                print(f"\n[voice] microphone problem: {e}")
                self.enabled = False
                self.force = False
                self.go.clear()
                self.on_state("error")
                continue

            forced = self.force

            if audio is None:
                if forced and time.time() - self.force_at > 7:
                    self.force = False
                    self.go.clear()
                    self.on_state("timeout")
                continue

            self.force = False
            self.go.clear()
            if forced:
                self.on_state("thinking")

            text = self._transcribe(audio)
            if text is None:                    # recognition error, already reported
                if woke:
                    self.on_state("cancel")
                continue
            text = numberize(text)
            if not text.strip():
                if forced:
                    self.on_state("timeout")
                elif woke:
                    self.on_state("cancel")
                continue

            if forced:
                command = text
            else:
                command = self._strip_wake(text)
                if command is None:             # not said to the sheep
                    if woke:
                        self.on_state("cancel")  # the early guess was wrong
                    continue

            command = command.strip()
            if not command:                     # just "hey sheep": wait for the command
                self.force = True
                self.force_at = time.time()
                self.go.set()
                self.on_state("listening")
                continue

            self.on_state("heard", command)
            self.on_command(command)

    # -----------------------------------------------------------
    # Recording: wait for speech, record until a pause.
    # In wake mode it also checks for "hey sheep" while you're still talking.
    # -----------------------------------------------------------

    def _capture(self):
        import numpy as np
        import sounddevice as sd

        q = queue.Queue()

        def callback(indata, frames, time_info, status):
            q.put(indata[:, 0].copy())

        preroll = collections.deque(maxlen=3)   # keep the 0.3 s before speech started
        frames = []
        started = False
        silent = 0
        loud = 0
        checks = 0
        woke = False

        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK,
                            device=config.VOICE_DEVICE, callback=callback):
            while self.running and self.enabled and not self.speaking:
                try:
                    chunk = q.get(timeout=0.3)
                except queue.Empty:
                    continue

                rms = float(np.sqrt(np.mean(chunk * chunk)))
                limit = max(config.VOICE_THRESHOLD, self.noise * 3)

                if not started:
                    if rms < limit:
                        self.noise = 0.9 * self.noise + 0.1 * rms
                        preroll.append(chunk)
                        if self.force and time.time() - self.force_at > 7:
                            return None, False
                        continue
                    started = True
                    frames.extend(preroll)

                frames.append(chunk)
                if rms >= limit:
                    loud += 1
                    silent = 0
                else:
                    silent += 1

                # Early wake-word check: react while the person is still talking
                if (self.mode == "wake" and not self.force and not woke and checks < 2
                        and loud >= (6 if checks == 0 else 11)):
                    checks += 1
                    heard = self._quick_text(np.concatenate(frames))
                    if heard and self._strip_wake(heard) is not None:
                        woke = True
                        self.on_state("woke")

                if silent >= PAUSE_BLOCKS or len(frames) >= 150:   # pause, or 15 s max
                    break

        if not started or loud < 3:
            if woke:
                self.on_state("cancel")
            return None, False
        return np.concatenate(frames), woke

    # -----------------------------------------------------------
    # Speech to text
    # -----------------------------------------------------------

    def _get_model(self, name):
        with self._model_lock:
            model = self.models.get(name)
            if model is None:
                from faster_whisper import WhisperModel
                model = WhisperModel(name, device="cpu", compute_type="int8",
                                     cpu_threads=config.NUM_THREAD)
                self.models[name] = model
            self.model_used = time.time()
            return model

    def _preload(self):
        """Load the speech models at startup so the first command isn't slow."""
        try:
            self.on_state("loading")
            if self.mode == "wake":
                self._get_model(config.VOICE_WAKE_MODEL)
            self._get_model(config.VOICE_MODEL)
            self.on_state("ready")
        except Exception as e:
            print(f"\n[voice] couldn't load the speech model: {e}")
            self.on_state("error")

    def _quick_text(self, audio):
        """Fast rough transcript of the first moments of speech (for the wake word)."""
        try:
            model = self._get_model(config.VOICE_WAKE_MODEL)
            segments, _ = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=False,
                condition_on_previous_text=False, temperature=0.0,
                without_timestamps=True, initial_prompt=WAKE_PROMPT,
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            return ""

    def _transcribe(self, audio):
        try:
            model = self._get_model(config.VOICE_MODEL)
            segments, _ = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=False,
                condition_on_previous_text=False, temperature=0.0,
                initial_prompt=PROMPT,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:
            print(f"\n[voice] speech recognition failed: {e}")
            self.on_state("error")
            return None

        clean = re.sub(r"[^\w\s]", "", text.lower()).strip()
        junk = {"", "you", "thank you", "thanks", "thanks for watching", "bye", "the end"}
        if clean in junk:
            return ""
        prompt_clean = re.sub(r"[^\w\s]", "", PROMPT.lower())
        if difflib.SequenceMatcher(None, clean, prompt_clean).ratio() > 0.9:
            return ""       # Whisper just echoed our vocabulary hint
        return text

    def _strip_wake(self, text):
        """Return the command after the wake word, "" if only the wake word,
        or None if the wake word wasn't said."""
        words = text.split()
        aliases = {w.lower() for w in config.VOICE_WAKE_WORDS} | {config.NAME.lower()}
        for i in range(min(3, len(words))):
            w = re.sub(r"[^\w']", "", words[i].lower())
            if w in aliases:
                return " ".join(words[i + 1:]).strip(" ,.!?")
        return None

    def _maybe_unload(self):
        if (config.VOICE_UNLOAD_AFTER and self.models
                and time.time() - self.model_used > config.VOICE_UNLOAD_AFTER):
            with self._model_lock:
                self.models.clear()
            gc.collect()