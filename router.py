import difflib
import inspect
import json
import re

import requests

import config
from tools import TOOLS
from pathlib import Path

# Filler words stripped from the start/end of what you type, so
# "hey jarvis can you please open chrome for me" becomes "open chrome".
PREFIXES = [
    "hey jarvis", "ok jarvis", "okay jarvis", "jarvis", "hey", "please", "pls",
    "can you", "could you", "would you", "will you", "can u", "kindly",
    "i want you to", "i want to", "i wanna", "i need to", "i'd like to",
    "id like to", "let's", "lets", "go ahead and", "just", "also", "quickly",
]
SUFFIXES = [
    "please", "pls", "for me", "now", "right now", "thanks", "thank you",
    "real quick", "quickly",
]

UNMATCHED_LOG = Path.home() / f"{config.NAME}_unmatched.txt"

def normalize(text):
    """Clean up filler words but keep the original capitalization."""
    t = re.sub(r"\s+", " ", text.strip())
    t = re.sub(r",\s+", " ", t)
    t = t.strip(" .,!?;:")
    changed = True
    while changed:
        changed = False
        for p in PREFIXES:
            if t.lower().startswith(p + " "):
                t = t[len(p):].strip(" ,:")
                changed = True
        for s in SUFFIXES:
            if t.lower().endswith(" " + s):
                t = t[: -len(s)].strip(" ,")
                changed = True
    return t


def match_rules(text):
    """Fast path: regex matching. Returns (tool_name, args) or None."""
    t = normalize(text)
    for name, meta in TOOLS.items():
        for pattern in meta["patterns"]:
            m = pattern.search(t)
            if m:
                args = {k: v.strip() for k, v in m.groupdict().items() if v}
                return name, args
    return None


# A few examples make even a tiny model much more reliable.
EXAMPLES = [
    ("make it louder", {"tool": "volume_up", "args": {}}),
    ("i want to watch some cat videos", {"tool": "youtube", "args": {"query": "cat videos"}}),
    ("how much battery do i have left", {"tool": "system_status", "args": {}}),
    ("remind me to drink water in 20 minutes",
     {"tool": "set_timer", "args": {"amount": 20, "unit": "minutes", "label": "drink water"}}),
    ("how are you", {"tool": "none", "args": {"reply": "Doing great! What do you need?"}}),
]


def _build_prompt():
    docs = "\n".join(f"- {meta['doc']}" for name, meta in TOOLS.items() if name != "help")
    return f"""You are the command router for a laptop assistant.
Convert the user's request into ONE tool call.

Available tools:
{docs}

Respond ONLY with JSON in exactly this shape:
{{"tool": "<tool name>", "args": {{...}}}}
Use the exact tool names (the word before the parenthesis).
If no tool fits, respond with:
{{"tool": "none", "args": {{"reply": "<short helpful reply>"}}}}
"""


def fix_call(name, args):
    """Repair common small-model mistakes: wrong tool name, extra arguments."""
    if name != "none" and name not in TOOLS:
        close = difflib.get_close_matches(name, list(TOOLS), n=1, cutoff=0.6)
        if close:
            name = close[0]
    if name in TOOLS:
        params = inspect.signature(TOOLS[name]["fn"]).parameters
        args = {k: v for k, v in args.items() if k in params}
    return name, args


def ask_llm(text):
    """Slow path: ask the local model. Returns (tool_name, args)."""
    messages = [{"role": "system", "content": _build_prompt()}]
    for user_text, answer in EXAMPLES:
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": json.dumps(answer)})
    messages.append({"role": "user", "content": text})

    payload = {
        "model": config.MODEL,
        "stream": False,
        "format": "json",
        "keep_alive": config.KEEP_ALIVE,
        "options": {
            "temperature": 0,
            "num_ctx": config.NUM_CTX,
            "num_thread": config.NUM_THREAD,
        },
        "messages": messages,
    }
    r = requests.post(config.OLLAMA_URL, json=payload, timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    data = json.loads(r.json()["message"]["content"])
    return fix_call(data.get("tool", "none"), data.get("args") or {})



def _log_unmatched(text):
    """Remember phrases the rules missed, so we can add patterns for them."""
    try:
        with open(UNMATCHED_LOG, "a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
    except Exception:
        pass


def route(text):
    """Returns (tool_name, args, source) where source is 'rules' or 'llm'."""
    hit = match_rules(text)
    if hit:
        return hit[0], hit[1], "rules"

    if not config.USE_LLM_FALLBACK:
        return "none", {"reply": "I didn't get that. Type 'help' to see what I can do."}, "rules"

    _log_unmatched(text)
    try:
        name, args = ask_llm(text)
        return name, args, "llm"
    except requests.exceptions.ConnectionError:
        return "none", {"reply": "Ollama isn't running, so I can only do rule-based commands. Start it with: ollama serve"}, "llm"
    except Exception as e:
        return "none", {"reply": f"Brain error: {e}"}, "llm"