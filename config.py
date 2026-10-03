import os

# What your assistant is called. Change this one line to rename it.
NAME = "sheep"

OLLAMA_URL = "http://localhost:11434/api/chat"

# Small model so an older laptop doesn't suffer.
# The LLM is only a fallback and unloads after use, so "qwen2.5:3b"
# is worth trying if the 1.5b one picks wrong tools too often.
MODEL = "qwen2.5:1.5b"

# Unload the model from RAM this long after the last use
KEEP_ALIVE = "20s"

# Context window. The tool list + examples need ~1500 tokens, so 2048.
NUM_CTX = 2048

# Use at most half your CPU threads so the rest of your PC stays snappy
NUM_THREAD = max(1, (os.cpu_count() or 2) // 2)

# Set False to run in rules-only mode (zero AI, zero RAM)
USE_LLM_FALLBACK = True

# Seconds to wait for the model before giving up
LLM_TIMEOUT = 90

# True = append "[via LLM]" to replies the LLM handled (handy for debugging)
DEBUG_SHOW_ROUTE = False

# ---- Desktop pet ----
# Set False to run terminal-only with no pet window
SHOW_PET = True

# Height of the sheep in centimetres (auto-sized from your screen's DPI)
PET_HEIGHT_CM = 2.5

# Force a pixel size instead (0 = automatic). Bigger number = bigger sheep.
PET_PIXEL_SIZE = 0

# Seconds standing around before it yawns and falls asleep
PET_SLEEP_AFTER = 20

# Chance (0 to 1) that it takes a walk along the bottom of the screen
# before going to sleep. 0 = never walks, 1 = always walks.
PET_WALK_CHANCE = 0.6

# Walking speed in centimetres per second
PET_WALK_SPEED = 2.0

# ---- Voice ----
# "off"  = no voice
# "push" = click the sheep, then talk (mic only open when you click)
# "wake" = say "hey sheep ..." any time (mic stays open, cheap while it's quiet)
VOICE_MODE = "wake"

# Speech model for your commands. "base.en" is more accurate, "tiny.en" is faster.
VOICE_MODEL = "base.en"

# Tiny fast model used only to spot "hey sheep" while you're still talking
VOICE_WAKE_MODEL = "tiny.en"

# Seconds of silence that end a command (lower = faster reply, but may cut you off)
VOICE_PAUSE = 0.6

# Speak replies out loud when you used your voice (False = silent replies)
VOICE_REPLY = True

# Speaking speed
VOICE_RATE = 175

# Voice pitch. 1.0 = the normal voice, 1.3 = higher (default), 1.5 = very squeaky.
VOICE_PITCH = 1.3

# Pick a voice by part of its name, e.g. "zira" or "samantha" ("" = automatic).
# To list your voices run:
#   python -c "import pyttsx3; [print(v.name) for v in pyttsx3.init().getProperty('voices')]"
VOICE_TTS_NAME = ""

# Mic sensitivity. Lower it if Sheep can't hear you, raise it if it keeps
# reacting to background noise. (It also adapts to room noise by itself.)
VOICE_THRESHOLD = 0.02

# Microphone device number (None = system default). To list devices run:
#   python -m sounddevice
VOICE_DEVICE = None

# Words that count as the wake word (Whisper sometimes mishears "sheep")
VOICE_WAKE_WORDS = ["sheep", "sheeps", "ship", "cheap", "sheap", "shep"]

# Extra words you often say (app names etc.) so speech recognition spells them right
# Example: ["Spotify", "VS Code", "Discord"]
VOICE_HINT_WORDS = ["Spotify", "VS Code", "Discord", "YouTube", "Google", "Zoom","Edge", "Excel", "PowerPoint", "Word", "Affinity", "Visual Studio", "Notepad", "D/: drive", "Pinterest", "Whatsapp"]

# Free the speech models from RAM after this many idle seconds (0 = never, fastest)
VOICE_UNLOAD_AFTER = 0