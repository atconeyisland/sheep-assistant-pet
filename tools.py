import ast
import difflib
import json
import operator
import os
import config
import platform
import random
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"

# ---------------------------------------------------------------
# Tool registry. To add a tool: write a function with the @tool
# decorator. Nothing else to edit.
#   name     : tool name the LLM fallback will use
#   doc      : one line describing the tool for the LLM (and `help`)
#   patterns : regexes (named groups become the function arguments)
#   confirm  : True = ask y/n before running
#
# ORDER MATTERS: the first matching pattern wins, so specific tools
# are defined first and the catch-all "open <anything>" is last.
# ---------------------------------------------------------------
TOOLS = {}

UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)"


def tool(name, doc, patterns=(), confirm=False):
    def deco(fn):
        TOOLS[name] = {
            "fn": fn,
            "doc": doc,
            "patterns": [re.compile(p, re.IGNORECASE) for p in patterns],
            "confirm": confirm,
        }
        return fn
    return deco


# ---------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------

def _press_key(vk, times=1):
    """Windows only: press a virtual key (used for volume and media keys)."""
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 1, 0)   # key down (extended key)
        user32.keybd_event(vk, 0, 3, 0)   # key up (extended + key-up flags)
        time.sleep(0.002)

VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3


def _open_path(path):
    """Open a file or folder with the default app."""
    if SYSTEM == "Windows":
        os.startfile(str(path))
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _http_get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": f"{config.NAME}-local/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------
# Help
# ---------------------------------------------------------------

@tool(
    "help",
    "help(): list everything Jarvis can do",
    patterns=[r"^(?:help|commands|list commands|what can you do|what do you do|menu)$"],
)
def help_tool():
    lines = ["Here is what I can do (examples in quotes):", ""]
    for name, meta in TOOLS.items():
        if name == "help":
            continue
        lines.append(f"  - {meta['doc']}")
    lines.append("")
    lines.append(f'You can phrase things naturally: "hey {config.NAME}, can you open chrome please".')
    return "\n".join(lines)

# ---------------------------------------------------------------
# Greetings (so "hey sheep" never has to wake the LLM)
# ---------------------------------------------------------------

_N = re.escape(config.NAME)


@tool(
    "greet",
    'greet(): say hello back. "hi", "hey sheep"',
    patterns=[
        r"^(?:(?:hi+|hello+|hey+|yo+|ok|okay|sup)\s+)?" + _N + r"(?:\s+(?:wa?ss?up+|what'?s ?up|you there|are you there))?$",
        r"^(?:hi+|hello+|hey+|yo+|sup|wa?ss?up+|what'?s ?up|good (?:morning|afternoon|evening))$",
    ],
)
def greet():
    return random.choice([
        "Baa! What can I do for you?",
        "Baa-ck and ready. What do you need?",
        f"Hey! {config.NAME.capitalize()} reporting for duty.",
    ])

# ---------------------------------------------------------------
# Folders, websites, search
# ---------------------------------------------------------------

@tool(
    "open_folder",
    'open_folder(folder: str): open downloads, documents, desktop, pictures, music, videos or home. "open downloads"',
    patterns=[r"^(?:open|show|go to)\s+(?:my\s+)?(?:the\s+)?(?P<folder>downloads|documents|desktop|pictures|music|videos|home)(?:\s+folder)?$"],
)
def open_folder(folder):
    folder = folder.lower().strip()
    path = Path.home() if folder == "home" else Path.home() / folder.capitalize()
    if not path.exists():
        return f"I can't find the {folder} folder."
    _open_path(path)
    return f"Opened {path}"


@tool(
    "open_website",
    'open_website(url: str): open a website address. "open github.com"',
    patterns=[
        r"^(?:open|go to|goto|visit)\s+(?P<url>(?!\S+\.(?:txt|pdf|docx?|xlsx?|pptx?|py|exe|png|jpe?g|zip|csv|md)\b)\S+\.\S+)$"
    ],
)
def open_website(url):
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


@tool(
    "youtube",
    'youtube(query: str): search YouTube. "play lofi beats", "youtube python tutorial"',
    patterns=[r"^(?:play|search youtube for|youtube|watch)\s+(?P<query>.+?)(?:\s+on\s+youtube)?$"],
)
def youtube(query):
    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Searching YouTube for: {query}"


@tool(
    "web_search",
    'web_search(query: str): search Google. "search best laptops 2025"',
    patterns=[r"^(?:search|google|look up|lookup)(?:\s+for)?\s+(?P<query>.+)$"],
)
def web_search(query):
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Searching for: {query}"


# ---------------------------------------------------------------
# Volume, media, brightness
# ---------------------------------------------------------------

def _linux_volume(change):
    if shutil.which("pactl"):
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", change])
    elif shutil.which("amixer"):
        if change[0] in "+-":
            change = change[1:] + change[0]      # amixer wants "10%+" not "+10%"
        subprocess.run(["amixer", "-D", "pulse", "sset", "Master", change])


def _step_volume(direction, amount=None):
    """direction is +1 or -1. amount is a percentage (default 10)."""
    amount = 10 if amount is None else max(1, min(100, int(amount)))
    if SYSTEM == "Windows":
        _press_key(VK_VOLUME_UP if direction > 0 else VK_VOLUME_DOWN, max(1, amount // 2))
    elif SYSTEM == "Darwin":
        sign = "+" if direction > 0 else "-"
        subprocess.run(["osascript", "-e",
                        f"set volume output volume ((output volume of (get volume settings)) {sign} {amount})"])
    else:
        _linux_volume(f"{'+' if direction > 0 else '-'}{amount}%")


@tool(
    "set_volume",
    'set_volume(level: int): set volume to a percentage. "set volume to 40", "volume 70"',
    patterns=[
        r"^(?:set\s+|turn\s+|put\s+|make\s+)?(?:the\s+)?(?:volume|sound)\s+(?:to\s+|at\s+)?(?P<level>\d{1,3})\s*(?:%|percent)?$",
        r"^(?:increase|raise|decrease|lower|reduce|turn up|turn down|turn|set|put|make)\s+(?:the\s+)?(?:volume|sound)\s+(?:up\s+|down\s+)?(?:to|at)\s+(?P<level>\d{1,3})\s*(?:%|percent)?$",
        r"^(?P<level>\d{1,3})\s*(?:%|percent)\s+volume$",
    ],
)
def set_volume(level):
    level = max(0, min(100, int(level)))
    if SYSTEM == "Windows":
        _press_key(VK_VOLUME_DOWN, 50)          # all the way down
        _press_key(VK_VOLUME_UP, level // 2)    # each press is about 2%
    elif SYSTEM == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {level}"])
    else:
        _linux_volume(f"{level}%")
    return f"Volume set to about {level}%"


@tool(
    "volume_up",
    'volume_up(amount: int): raise the volume, amount in percent is optional. "louder", "increase volume by 20"',
    patterns=[
        r"^(?=.*\b(?:volume|sound|audio)\b)(?=.*\b(?:up|increase|raise|higher|louder|boost)\b)(?!.*\b(?:to|at)\s+\d)(?:.*?\b(?:by\s+)?(?P<amount>\d{1,3})\b)?.*$",
        r"^(?:turn\s+it\s+up|louder|volume\s*\+|make it louder)$",
    ],
)
def volume_up(amount=None):
    _step_volume(+1, amount)
    return "Volume up"


@tool(
    "volume_down",
    'volume_down(amount: int): lower the volume, amount in percent is optional. "quieter", "decrease volume by 20"',
    patterns=[
        r"^(?=.*\b(?:volume|sound|audio)\b)(?=.*\b(?:down|decrease|lower|reduce|quieter|softer)\b)(?!.*\b(?:to|at)\s+\d)(?:.*?\b(?:by\s+)?(?P<amount>\d{1,3})\b)?.*$",
        r"^(?:turn\s+it\s+down|quieter|softer|volume\s*-|make it quieter)$",
    ],
)
def volume_down(amount=None):
    _step_volume(-1, amount)
    return "Volume down"


@tool(
    "volume_mute",
    'volume_mute(): mute or unmute. "mute", "unmute"',
    patterns=[r"^(?:mute|unmute|toggle mute|mute (?:the )?(?:sound|volume|laptop))$"],
)
def volume_mute():
    if SYSTEM == "Windows":
        _press_key(VK_VOLUME_MUTE)
    elif SYSTEM == "Darwin":
        subprocess.run(["osascript", "-e",
                        "set volume output muted not (output muted of (get volume settings))"])
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return "Toggled mute"

@tool(
    "media_play_pause",
    'media_play_pause(): play or pause music/video. "pause", "resume", "play"',
    patterns=[r"^(?:play|pause|resume|play\s*/\s*pause|(?:pause|resume|play|stop)\s+(?:the\s+)?(?:music|song|media|video))$"],
)
def media_play_pause():
    if SYSTEM == "Windows":
        _press_key(VK_MEDIA_PLAY_PAUSE)
    elif SYSTEM == "Linux" and shutil.which("playerctl"):
        subprocess.run(["playerctl", "play-pause"])
    else:
        return "Media keys aren't supported on this system."
    return "Toggled play/pause"


@tool(
    "media_next",
    'media_next(): next track. "next song", "skip"',
    patterns=[r"^(?:next|skip)(?:\s+(?:song|track|music))?$"],
)
def media_next():
    if SYSTEM == "Windows":
        _press_key(VK_MEDIA_NEXT)
    elif SYSTEM == "Linux" and shutil.which("playerctl"):
        subprocess.run(["playerctl", "next"])
    else:
        return "Media keys aren't supported on this system."
    return "Next track"


@tool(
    "media_previous",
    'media_previous(): previous track. "previous song"',
    patterns=[r"^(?:previous|prev)(?:\s+(?:song|track|music))?$", r"^go back a song$"],
)
def media_previous():
    if SYSTEM == "Windows":
        _press_key(VK_MEDIA_PREV)
    elif SYSTEM == "Linux" and shutil.which("playerctl"):
        subprocess.run(["playerctl", "previous"])
    else:
        return "Media keys aren't supported on this system."
    return "Previous track"


@tool(
    "set_brightness",
    'set_brightness(level: int): set screen brightness percent. "brightness 50"',
    patterns=[r"^(?:set\s+)?(?:the\s+)?(?:screen\s+)?brightness\s+(?:to\s+)?(?P<level>\d{1,3})\s*%?$"],
)
def set_brightness(level):
    level = max(0, min(100, int(level)))
    try:
        if SYSTEM == "Windows":
            cmd = ("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                   f".WmiSetBrightness(1,{level})")
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=15)
        elif SYSTEM == "Linux" and shutil.which("brightnessctl"):
            subprocess.run(["brightnessctl", "set", f"{level}%"])
        else:
            return "Brightness control isn't supported on this system."
        return f"Brightness set to {level}%"
    except Exception as e:
        return f"Couldn't change brightness: {e}"


# ---------------------------------------------------------------
# Timers and reminders
# ---------------------------------------------------------------

NOTIFY = None   # main.py sets this so the pet can react when a timer rings


@tool(
    "set_timer",
    'set_timer(amount: int, unit: "seconds"|"minutes"|"hours", label: str): countdown or reminder. "timer for 5 minutes", "remind me to stretch in 30 minutes"',
    patterns=[
        r"^(?:(?:set|start)\s+)?(?:a\s+)?timer\s+(?:for\s+)?(?P<amount>\d+)\s*(?P<unit>" + UNIT + r")$",
        r"^(?:(?:set|start)\s+)?(?:a\s+)?(?P<amount>\d+)\s*(?P<unit>" + UNIT + r")\s+timer$",
        r"^remind me to\s+(?P<label>.+?)\s+in\s+(?P<amount>\d+)\s*(?P<unit>" + UNIT + r")$",
        r"^remind me in\s+(?P<amount>\d+)\s*(?P<unit>" + UNIT + r")\s+to\s+(?P<label>.+)$",
        r"^(?:remind me|wake me)\s+in\s+(?P<amount>\d+)\s*(?P<unit>" + UNIT + r")$",
    ],
)
def set_timer(amount, unit="seconds", label="Timer"):
    amount = int(amount)
    u = str(unit).lower()
    mult = 3600 if u.startswith("h") else 60 if u.startswith("m") else 1
    seconds = amount * mult

    def ring():
        time.sleep(seconds)
        print(f"\n*** {label} is done! ***\n{config.NAME}> ", end="", flush=True)
        if NOTIFY:
            try:
                NOTIFY("Timer", f"{label} is done!")
            except Exception:
                pass
        try:
            if SYSTEM == "Windows":
                import winsound
                winsound.Beep(1000, 800)
            else:
                print("\a", end="", flush=True)
        except Exception:
            pass

    threading.Thread(target=ring, daemon=True).start()
    return f"Timer set for {amount} {unit} ({label})"

# ---------------------------------------------------------------
# Screen and power
# ---------------------------------------------------------------

@tool(
    "take_screenshot",
    "take_screenshot(): capture the screen and save it to Pictures. \"screenshot\"",
    patterns=[r"^(?:take\s+(?:a\s+)?)?(?:screenshot|screen\s*shot)$", r"^capture\s+(?:the\s+)?screen$"],
)
def take_screenshot():
    try:
        from PIL import ImageGrab
        folder = Path.home() / "Pictures"
        folder.mkdir(exist_ok=True)
        path = folder / f"shot_{int(time.time())}.png"
        ImageGrab.grab().save(path)
        return f"Screenshot saved to {path}"
    except Exception as e:
        return f"Screenshot failed: {e}"


@tool(
    "show_desktop",
    'show_desktop(): minimize all windows. "show desktop"',
    patterns=[r"^(?:show|go to)\s+(?:the\s+)?desktop$", r"^minimi[sz]e\s+(?:all|everything)(?:\s+windows)?$"],
)
def show_desktop():
    if SYSTEM != "Windows":
        return "Show desktop is only supported on Windows for now."
    import ctypes
    VK_LWIN, VK_D = 0x5B, 0x44
    u = ctypes.windll.user32
    u.keybd_event(VK_LWIN, 0, 0, 0)
    u.keybd_event(VK_D, 0, 0, 0)
    u.keybd_event(VK_D, 0, 2, 0)
    u.keybd_event(VK_LWIN, 0, 2, 0)
    return "Showing desktop"


@tool(
    "lock_screen",
    'lock_screen(): lock the computer. "lock"',
    patterns=[r"^lock(?:\s+(?:the\s+)?(?:screen|laptop|pc|computer))?$"],
    confirm=True,
)
def lock_screen():
    try:
        if SYSTEM == "Windows":
            subprocess.Popen("rundll32.exe user32.dll,LockWorkStation")
        elif SYSTEM == "Darwin":
            subprocess.Popen(["pmset", "displaysleepnow"])
        else:
            subprocess.Popen(["loginctl", "lock-session"])
        return "Locking screen"
    except Exception as e:
        return f"Couldn't lock: {e}"


@tool(
    "power",
    'power(action: "shutdown"|"restart"|"sleep"): power controls. "shutdown", "restart", "sleep"',
    patterns=[r"^(?P<action>shut\s*down|power\s*off|restart|reboot|sleep|hibernate)(?:\s+(?:the\s+)?(?:laptop|pc|computer|system))?$"],
    confirm=True,
)
def power(action):
    a = action.lower().replace(" ", "")
    if a in ("shutdown", "poweroff"):
        kind = "shutdown"
    elif a in ("restart", "reboot"):
        kind = "restart"
    else:
        kind = "sleep"
    try:
        if SYSTEM == "Windows":
            if kind == "shutdown":
                subprocess.Popen(["shutdown", "/s", "/t", "5"])
            elif kind == "restart":
                subprocess.Popen(["shutdown", "/r", "/t", "5"])
            else:
                subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
        elif SYSTEM == "Darwin":
            verb = {"shutdown": "shut down", "restart": "restart", "sleep": "sleep"}[kind]
            subprocess.Popen(["osascript", "-e", f'tell app "System Events" to {verb}'])
        else:
            verb = {"shutdown": "poweroff", "restart": "reboot", "sleep": "suspend"}[kind]
            subprocess.Popen(["systemctl", verb])
        return f"Doing {kind} now" + (" (in 5 seconds)" if SYSTEM == "Windows" and kind != "sleep" else "")
    except Exception as e:
        return f"Couldn't {kind}: {e}"


# ---------------------------------------------------------------
# System info
# ---------------------------------------------------------------

@tool(
    "system_status",
    'system_status(): show CPU, RAM and battery. "status", "battery"',
    patterns=[r"^(?:system status|status|battery|cpu|ram|how(?:'s| is) (?:my )?(?:laptop|pc|system))$"],
)
def system_status():
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        lines = [
            f"CPU: {cpu}%",
            f"RAM: {mem.percent}% used ({mem.used / 1e9:.1f} / {mem.total / 1e9:.1f} GB)",
        ]
        batt = psutil.sensors_battery()
        if batt:
            plug = "charging" if batt.power_plugged else "on battery"
            lines.append(f"Battery: {batt.percent:.0f}% ({plug})")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't read system status: {e}"


@tool(
    "top_processes",
    'top_processes(): show the apps using the most RAM. "what is eating my ram"',
    patterns=[
        r"^(?:what(?:'s|\s+is)\s+)?(?:eating|using|hogging|taking)\s+(?:up\s+)?(?:my\s+)?(?:ram|memory|cpu)$",
        r"^(?:top|heavy|biggest)\s+(?:apps|processes|memory hogs)$",
        r"^(?:show\s+)?(?:running\s+)?(?:processes|memory hogs)$",
    ],
)
def top_processes():
    try:
        import psutil
        totals = {}
        for p in psutil.process_iter(["name", "memory_info"]):
            info = p.info
            if not info.get("memory_info") or not info.get("name"):
                continue
            name = info["name"]
            totals[name] = totals.get(name, 0) + info["memory_info"].rss
        top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:6]
        lines = ["Top RAM users:"]
        for name, rss in top:
            lines.append(f"  {name}: {rss / 1e6:.0f} MB")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't read processes: {e}"


@tool(
    "disk_space",
    'disk_space(): show free disk space. "disk space"',
    patterns=[r"^(?:disk(?:\s+space)?|storage|free\s+space|how much (?:disk\s+)?(?:space|storage)(?:\s+(?:is\s+)?(?:left|free))?)$"],
)
def disk_space():
    root = Path.home().anchor or "/"
    total, used, free = shutil.disk_usage(root)
    return f"Disk {root}: {free / 1e9:.0f} GB free of {total / 1e9:.0f} GB ({used / total * 100:.0f}% used)"


@tool(
    "ip_address",
    'ip_address(): show this computer\'s local IP. "my ip"',
    patterns=[r"^(?:what(?:'s|\s+is)\s+)?(?:my\s+)?(?:ip|ip address)$"],
)
def ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"Local IP: {ip}"
    except Exception:
        return "Couldn't determine IP (are you offline?)."


@tool(
    "tell_time",
    'tell_time(): say the current date and time. "what time is it"',
    patterns=[
        r"^(?:what(?:'s|\s+is)?\s+(?:the\s+)?)?(?:current\s+)?(?:time|date)(?:\s+now|\s+is\s+it|\s+today)?$",
        r"^what\s+day\s+is\s+(?:it|today)$",
    ],
)
def tell_time():
    return time.strftime("It is %I:%M %p on %A, %d %B %Y")


# ---------------------------------------------------------------
# Calculator (safe: no eval)
# ---------------------------------------------------------------

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("unsupported expression")


@tool(
    "calculate",
    'calculate(expr: str): do math. "calculate 25 * 4", "what is 12 plus 7"',
    patterns=[
        r"^(?:calculate|calc|compute|solve|what(?:'s|\s+is)|whats)\s+(?P<expr>(?=.*\d).+)$",
        r"^(?P<expr>\d[\d\s\.\+\-\*/\(\)x×÷^%]*\d)$",
        r"^(?P<expr>\d+(?:\.\d+)?\s*(?:plus|minus|times|divided by|multiplied by|over)\s*\d.*)$",
    ],
)
def calculate(expr):
    e = expr.lower().strip().rstrip("=?")
    for word, sym in [("multiplied by", "*"), ("divided by", "/"), ("to the power of", "**"),
                      ("plus", "+"), ("minus", "-"), ("times", "*"), ("over", "/"),
                      ("mod", "%"), ("×", "*"), ("÷", "/"), ("^", "**")]:
        e = e.replace(word, sym)
    e = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", e)
    e = e.replace(",", "")
    if not re.fullmatch(r"[\d\s\.\+\-\*/\(\)%]+", e):
        return "That doesn't look like a math expression."
    try:
        result = _safe_eval(ast.parse(e.strip(), mode="eval"))
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expr.strip()} = {result}"
    except ZeroDivisionError:
        return "Can't divide by zero."
    except Exception:
        return "Couldn't work that out."


# ---------------------------------------------------------------
# Weather (free, no key, via wttr.in)
# ---------------------------------------------------------------

@tool(
    "weather",
    'weather(city: str): current weather, city optional. "weather in Pune"',
    patterns=[r"^(?:what(?:'s|\s+is)\s+the\s+)?weather(?:\s+(?:like\s+)?(?:in|at|for)\s+(?P<city>.+?))?(?:\s+(?:today|now|right now))?$"],
)
def weather(city=""):
    try:
        place = urllib.parse.quote(city.strip()) if city else ""
        url = f"https://wttr.in/{place}?format=%l:+%c+%C,+%t+(feels+like+%f),+humidity+%h"
        return _http_get(url).strip()
    except Exception as e:
        return f"Couldn't get the weather (offline?): {e}"


# ---------------------------------------------------------------
# Tasks and notes
# ---------------------------------------------------------------

TASKS_FILE = Path.home() / f"{config.NAME}_tasks.json"
NOTES_FILE = Path.home() / f"{config.NAME}_notes.txt"

def _load_tasks():
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


@tool(
    "task_add",
    'task_add(text: str): add a to-do. "add task finish report"',
    patterns=[
        r"^(?:add\s+(?:a\s+)?(?:task|todo|to-do)|todo|task)[:\s]+(?P<text>.+)$",
        r"^add\s+(?P<text>.+?)\s+to\s+(?:my\s+)?(?:todo|to-do|tasks?)(?:\s+list)?$",
    ],
)
def task_add(text):
    tasks = _load_tasks()
    tasks.append(text.strip())
    _save_tasks(tasks)
    return f"Added task #{len(tasks)}: {text.strip()}"


@tool(
    "task_list",
    'task_list(): show my to-do list. "show tasks", "my todos"',
    patterns=[r"^(?:(?:show|list|view|what(?:'s|\s+are))\s+)?(?:my\s+)?(?:todos?|to-dos?|tasks?)(?:\s+list)?$"],
)
def task_list():
    tasks = _load_tasks()
    if not tasks:
        return "Your to-do list is empty."
    return "To-do:\n" + "\n".join(f"  {i}. {t}" for i, t in enumerate(tasks, 1))


@tool(
    "task_done",
    'task_done(number: int): finish and remove a to-do by its number. "done task 2"',
    patterns=[
        r"^(?:done|finish(?:ed)?|complete(?:d)?|remove|delete)\s+(?:task|todo)\s+(?P<number>\d+)$",
        r"^(?:mark\s+)?(?:task\s+)?(?P<number>\d+)\s+(?:as\s+)?done$",
    ],
)
def task_done(number):
    tasks = _load_tasks()
    n = int(number)
    if not 1 <= n <= len(tasks):
        return f"There's no task #{n}."
    removed = tasks.pop(n - 1)
    _save_tasks(tasks)
    return f"Done and removed: {removed}"


@tool(
    "notes_show",
    'notes_show(): show all saved notes. "show notes"',
    patterns=[r"^(?:show|read|list)\s+(?:my\s+)?notes$"],
)
def notes_show():
    if not NOTES_FILE.exists():
        return "No notes yet."
    return NOTES_FILE.read_text(encoding="utf-8").strip() or "No notes yet."


@tool(
    "notes_add",
    'notes_add(text: str): save a quick note. "note buy milk"',
    patterns=[r"^(?:note|remember)(?:\s+that)?[:\s]+(?P<text>.+)$"],
)
def notes_add(text):
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with open(NOTES_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {text}\n")
    return "Saved note."


# ---------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------

@tool(
    "copy_text",
    'copy_text(text: str): copy text to the clipboard. "copy hello world"',
    patterns=[r"^copy\s+(?P<text>.+?)(?:\s+to\s+(?:the\s+)?clipboard)?$"],
)
def copy_text(text):
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return "Copied to clipboard."
    except Exception as e:
        return f"Couldn't use the clipboard: {e}"


@tool(
    "show_clipboard",
    'show_clipboard(): show what is on the clipboard. "show clipboard"',
    patterns=[r"^(?:show|read|what(?:'s|\s+is)\s+(?:on|in))\s+(?:my\s+)?(?:the\s+)?clipboard$"],
)
def show_clipboard():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        text = r.clipboard_get()
        r.destroy()
        return text if len(text) < 500 else text[:500] + "..."
    except Exception:
        return "The clipboard is empty or not text."


# ---------------------------------------------------------------
# Files
# ---------------------------------------------------------------

@tool(
    "find_file",
    'find_file(query: str): search your home folder for files by name. "find resume"',
    patterns=[r"^(?:find|locate|where(?:'s|\s+is))\s+(?:the\s+)?(?:file\s+)?(?P<query>.+)$"],
)
def find_file(query):
    query = query.lower().strip()
    skip = {"node_modules", "__pycache__", "AppData", "Library", "site-packages"}
    results = []
    deadline = time.time() + 8  # never search longer than 8 seconds
    for root, dirs, files in os.walk(Path.home()):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip]
        for fname in files:
            if query in fname.lower():
                results.append(os.path.join(root, fname))
                if len(results) >= 10:
                    return "Found:\n" + "\n".join(results)
        if time.time() > deadline:
            break
    if not results:
        return f"No files matching '{query}' found."
    return "Found:\n" + "\n".join(results)


@tool(
    "organize_downloads",
    "organize_downloads(): sort the Downloads folder into subfolders by file type. \"organize downloads\"",
    patterns=[r"^(?:organi[sz]e|clean(?:\s*up)?|sort)\s+(?:my\s+)?downloads$"],
    confirm=True,
)
def organize_downloads():
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return "No Downloads folder found."
    groups = {
        "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"},
        "Documents": {".pdf", ".docx", ".doc", ".txt", ".pptx", ".xlsx", ".csv"},
        "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
        "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb"},
        "Media": {".mp3", ".mp4", ".mkv", ".wav", ".mov"},
    }
    moved = 0
    for f in downloads.iterdir():
        if not f.is_file():
            continue
        for folder, exts in groups.items():
            if f.suffix.lower() in exts:
                dest = downloads / folder
                dest.mkdir(exist_ok=True)
                target = dest / f.name
                if not target.exists():
                    f.rename(target)
                    moved += 1
                break
    return f"Organized {moved} files in Downloads"


# ---------------------------------------------------------------
# Closing apps
# ---------------------------------------------------------------

PROTECTED = {"explorer", "system", "svchost", "winlogon", "csrss", "services",
             "lsass", "wininit", "smss", "dwm", "python", "pythonw", "ollama"}


@tool(
    "close_app",
    'close_app(name: str): close a running app. "close chrome"',
    patterns=[r"^(?:close|quit|kill)\s+(?P<name>.+)$"],
    confirm=True,
)
def close_app(name):
    try:
        import psutil
        key = name.lower().strip()
        killed = 0
        for p in psutil.process_iter(["name"]):
            pname = (p.info.get("name") or "").lower()
            base = pname.rsplit(".", 1)[0]
            if key in pname and base not in PROTECTED and p.pid != os.getpid():
                try:
                    p.terminate()
                    killed += 1
                except Exception:
                    pass
        return f"Closed {killed} process(es) matching '{name}'." if killed else f"Nothing running matches '{name}'."
    except Exception as e:
        return f"Couldn't close {name}: {e}"


# ---------------------------------------------------------------
# Fun and knowledge (free, no LLM needed)
# ---------------------------------------------------------------

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "A SQL query walks into a bar, sees two tables and asks: can I join you?",
    "I would tell you a UDP joke, but you might not get it.",
    "Why did the developer go broke? He used up all his cache.",
]


@tool(
    "joke",
    'joke(): tell a joke. "tell me a joke"',
    patterns=[r"^(?:tell\s+(?:me\s+)?(?:a\s+)?)?(?:joke|something funny|make me laugh)$"],
)
def joke():
    return random.choice(JOKES)


@tool(
    "wiki",
    'wiki(topic: str): short factual summary from Wikipedia. "who is Alan Turing", "what is photosynthesis"',
    patterns=[r"^(?:who\s+(?:is|was)|what\s+(?:is|are|was)|tell me about|define|explain|meaning of)\s+(?P<topic>.+)$"],
)
def wiki(topic):
    topic = re.sub(r"^(?:a|an|the)\s+", "", topic.strip(), flags=re.IGNORECASE)
    candidates = [topic[:1].upper() + topic[1:], topic.title()]
    for cand in dict.fromkeys(candidates):
        url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(cand.replace(" ", "_"))
        try:
            data = json.loads(_http_get(url))
        except urllib.error.HTTPError:
            continue
        except Exception as e:
            return f"Couldn't reach Wikipedia (offline?): {e}"
        if data.get("type") == "disambiguation":
            return f"'{topic}' could mean several things. Try being more specific."
        extract = data.get("extract", "").strip()
        if extract:
            if len(extract) > 500:
                cut = extract[:500]
                extract = cut[: cut.rfind(". ") + 1] if ". " in cut else cut + "..."
            return extract
    return f"I couldn't find anything on '{topic}'."


# ---------------------------------------------------------------
# Opening apps (LAST: it is the catch-all "open <anything>")
# ---------------------------------------------------------------

SITES = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "instagram": "https://www.instagram.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
}

WINDOWS_ALIASES = {
    "calculator": "calc",
    "file explorer": "explorer",
    "explorer": "explorer",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "task manager": "taskmgr",
    "paint": "mspaint",
    "settings": "ms-settings:",
    "command prompt": "cmd",
    "cmd": "cmd",
    "terminal": "wt",
    "powershell": "powershell",
    "control panel": "control",
    "snipping tool": "snippingtool",
}

_START_INDEX = None


def _windows_start_menu_index():
    """Map lowercase app name -> Start Menu shortcut path (built once, cached)."""
    global _START_INDEX
    if _START_INDEX is not None:
        return _START_INDEX
    index = {}
    roots = [
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    for root in roots:
        if root.exists():
            for p in root.rglob("*.lnk"):
                index[p.stem.lower()] = p
    _START_INDEX = index
    return index


def _launch_windows(name):
    key = name.lower().strip()
    if key in WINDOWS_ALIASES:
        subprocess.Popen(f'start "" "{WINDOWS_ALIASES[key]}"', shell=True)
        return name
    index = _windows_start_menu_index()
    match = None
    if key in index:
        match = key
    else:
        contains = [k for k in index if key in k]
        if contains:
            match = min(contains, key=len)
        else:
            close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.6)
            if close:
                match = close[0]
    if match:
        os.startfile(str(index[match]))
        return match
    exe = shutil.which(name)
    if exe:
        subprocess.Popen([exe])
        return name
    return None


@tool(
    "open_app",
    'open_app(name: str): open an application or a well-known site. "open spotify", "open chrome", "open youtube"',
    patterns=[r"^(?:open|launch|start|run|fire up|boot up|open up)\s+(?P<name>.+)$"],
)
def open_app(name):
    name = name.strip()
    low = name.lower()
    if low in SITES:
        webbrowser.open(SITES[low])
        return f"Opened {name} in your browser"
    try:
        if SYSTEM == "Windows":
            launched = _launch_windows(name)
            if launched:
                return f"Opening {launched}"
            return f"Couldn't find an app called '{name}'."
        if SYSTEM == "Darwin":
            result = subprocess.run(["open", "-a", name], capture_output=True)
            if result.returncode == 0:
                return f"Opening {name}"
            return f"Couldn't find an app called '{name}'."
        exe = shutil.which(low) or shutil.which(name)
        if not exe:
            return f"Couldn't find an app called '{name}'."
        subprocess.Popen([exe])
        return f"Opening {name}"
    except Exception as e:
        return f"Could not open {name}: {e}"