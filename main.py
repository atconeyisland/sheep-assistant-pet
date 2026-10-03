import os
import re
import sys
import threading

import config
import tools
from router import route
from tools import TOOLS

RUN_LOCK = threading.Lock()   # so terminal, pet-box and voice commands never overlap

PROBLEM_WORDS = (
    "couldn't", "can't", "cannot", "failed", "error", "isn't running",
    "don't have", "not sure", "didn't get", "no files", "unknown",
    "doesn't look", "cancelled", "nothing running", "isn't supported",
    "aren't supported", "there's no",
)


def lower_priority():
    """Run at below-normal priority so the assistant never slows down your other apps."""
    try:
        import psutil
        p = psutil.Process()
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass


def fix_console_encoding():
    """So emoji from the weather tool don't crash the Windows console."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def is_problem(result):
    r = result.lower()
    return any(word in r for word in PROBLEM_WORDS)


def speakable(result):
    """Short version of a reply for text-to-speech (long lists stay in the box)."""
    lines = [l.strip() for l in result.strip().splitlines() if l.strip()]
    if not lines:
        return ""
    first = re.sub(r"[*_`#]", "", lines[0])
    if len(first) > 160:
        first = first[:160].rsplit(" ", 1)[0]
    if len(lines) > 1:
        first += ". The rest is in the box."
    return first


def bubble_seconds(result):
    return max(4.0, min(12.0, len(result) * 0.05))


def run_command(text, confirm=None):
    """Route and run one command. `confirm(prompt) -> bool` asks yes/no for risky tools;
    when omitted, it asks in the terminal."""
    name, args, source = route(text)

    if name == "none":
        return args.get("reply", "I'm not sure how to do that yet. Type 'help' to see my commands.")

    if name not in TOOLS:
        return f"I don't have a tool called '{name}'. Type 'help' to see my commands."

    meta = TOOLS[name]

    if meta["confirm"]:
        prompt = f"Run {name} {args or ''}?"
        if confirm is not None:
            ok = confirm(prompt)
        else:
            ok = input(f"{prompt} (y/n) ").strip().lower() == "y"
        if not ok:
            return "Cancelled."

    try:
        result = meta["fn"](**args)
    except TypeError as e:
        return f"I couldn't work out the details for {name}: {e}"
    except Exception as e:
        return f"{name} failed: {e}"

    tag = "  [via LLM]" if (config.DEBUG_SHOW_ROUTE and source == "llm") else ""
    return f"{result}{tag}"


def handle(text, pet=None, confirm=None, voice=None, spoken=False):
    """Run a command and animate the pet. Speaks the reply if it came from voice."""
    with RUN_LOCK:
        if pet:
            pet.set_mode("working")
        result = run_command(text, confirm)

    problem = is_problem(result)
    if pet:
        pet.say(result, bubble_seconds(result))
    if spoken and voice is not None and config.VOICE_REPLY:
        if pet:
            pet.set_mode("talking")
        voice.speak(speakable(result))
    if pet:
        pet.set_mode("confused" if problem else "happy", 2.5)
    return result


def repl(pet, voice):
    """Terminal loop. Runs in a background thread when the pet window is on."""
    print(f"{config.NAME.capitalize()} online. Type 'help' to see commands, 'exit' to quit.")
    if pet:
        print("Tip: click the sheep to talk, double-click to type, right-click for the menu.")
    if voice and config.VOICE_MODE == "wake":
        print(f"Say \"hey {config.NAME}, ...\" any time.")
    while True:
        try:
            text = input(f"{config.NAME}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit", "bye"}:
            break
        print(handle(text, pet))
    print("Bye!")
    if pet:
        pet.quit()


def pet_command(pet, voice, text):
    """Called when you type into the pet's command box."""
    result = handle(text, pet, confirm=pet.ask_confirm)
    print(f"\n[pet box] {text}\n{result}\n{config.NAME}> ", end="", flush=True)


def voice_command(pet, voice, text):
    """Called (in the voice thread) with each spoken command."""
    confirm = pet.ask_confirm if pet else (lambda prompt: False)   # no popup = deny risky actions
    result = handle(text, pet, confirm=confirm, voice=voice, spoken=True)
    print(f"\n[voice] {text}\n{result}\n{config.NAME}> ", end="", flush=True)


def on_voice_state(pet, state, info=None):
    if state == "heard":
        print(f"\n[heard] {info}\n{config.NAME}> ", end="", flush=True)
        return
    if pet is None:
        return
    if state in ("woke", "thinking"):
        pet.set_mode("working")           # starts jumping the moment it hears "hey sheep"
    elif state == "listening":
        pet.set_mode("listening")
    elif state == "cancel":
        pet.set_mode("idle")
    elif state == "loading":
        pet.say("Warming up my ears... the first time takes a bit.", 6)
    elif state == "ready":
        if config.VOICE_MODE == "wake":
            pet.say(f"Ears ready! Say 'hey {config.NAME}'.", 4)
    elif state == "timeout":
        pet.set_mode("idle")
        pet.say("I didn't hear anything.", 3)
    elif state == "error":
        pet.set_mode("confused", 3)
        pet.say("Mic or voice problem. Check the terminal.", 5)


def main():
    fix_console_encoding()
    lower_priority()

    pet = None
    voice = None

    def on_talk():
        if voice is None:
            pet.say("Voice is off. Set VOICE_MODE in config.py and install the voice packages.", 5)
        else:
            voice.listen_now()

    def on_toggle_mic():
        if voice is None:
            pet.say("Voice is off. Set VOICE_MODE in config.py.", 4)
            return
        on = voice.toggle()
        pet.say(f"Mic on. Say 'hey {config.NAME}'!" if on else "Mic off.", 3)

    if config.SHOW_PET:
        try:
            from pet import SheepPet
            pet = SheepPet(
                name=config.NAME,
                on_command=lambda text: pet_command(pet, voice, text),
                on_talk=on_talk,
                on_toggle_mic=on_toggle_mic,
                sleep_after=config.PET_SLEEP_AFTER,
                height_cm=config.PET_HEIGHT_CM,
                pixel_size=config.PET_PIXEL_SIZE,
                walk_chance=config.PET_WALK_CHANCE,
                walk_speed=config.PET_WALK_SPEED,
            )

            def on_notify(title, message):      # a timer rang
                pet.set_mode("alert", 6)
                pet.say(message, 8)

            tools.NOTIFY = on_notify
        except Exception as e:
            print(f"(Pet window disabled: {e})")
            pet = None

    if config.VOICE_MODE != "off":
        try:
            from voice import Voice
            voice = Voice(
                on_command=lambda text: voice_command(pet, voice, text),
                on_state=lambda state, info=None: on_voice_state(pet, state, info),
            )
            voice.start()
        except Exception as e:
            print(f"(Voice disabled: {e})")
            voice = None

    if pet is None:
        repl(None, voice)
    else:
        threading.Thread(target=repl, args=(pet, voice), daemon=True).start()
        pet.run()   # the pet window owns the main thread


if __name__ == "__main__":
    main()