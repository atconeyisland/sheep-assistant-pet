import functools
import platform
import queue
import random
import threading
import time
import tkinter as tk

SYSTEM = platform.system()
FONT_FAMILY = {"Windows": "Consolas", "Darwin": "Menlo"}.get(SYSTEM, "DejaVu Sans Mono")

KEY = "#ff00ff"        # this colour becomes see-through on Windows
TICK_MS = 100
WALK_TICK_MS = 60      # smoother movement while walking
ACCESSORY = "none"     # "none" or "bow"

# Game-style dialog colours
PANEL = "#1b2038"
EDGE_DARK = "#0a0d1c"
EDGE_GOLD = "#f2d98b"
EDGE_BLUE = "#3d4a86"
TEXT = "#f4f1de"
TAB = "#f2b84b"

# ---------------------------------------------------------------
# Sprite colours
# ---------------------------------------------------------------
PALETTE = {
    "W": "#fff8e7",   # wool
    "H": "#ffffff",   # wool highlight
    "w": "#f3e2c2",   # wool shade
    "S": "#eec88e",   # belly shade
    "F": "#3e2f3c",   # face
    "f": "#6a5461",   # muzzle
    "E": "#ffffff",   # eye
    "c": "#cbb6e6",   # closed-eye line
    "G": "#f2b84b",   # golden ear
    "P": "#ff8fa8",   # blush
    "m": "#d98a9f",   # smile
    "M": "#e5607a",   # open mouth
    "L": "#3e2f3c",   # near legs
    "l": "#5a4658",   # far legs
    "O": "#4a3a47",   # outline
    "R": "#ff5a6e",   # bow
    "r": "#c93a52",   # bow knot
    "B": "#5b7cfa",   # nightcap
    "b": "#e8f0ff",   # nightcap band
    "Q": "#ffffff",   # pom-pom
    "D": "#33406e",   # headphone cup
    "d": "#6c7fc4",   # headphone band
}

# ---------------------------------------------------------------
# Sprite shape: a 20x16 side-view sheep facing LEFT.
# Wool is a rounded blob, the head is drawn on top, legs are added below.
# ---------------------------------------------------------------
WIDTH, HEIGHT = 20, 16

WOOL = {   # row: (first column, last column)
    0: (8, 15), 1: (6, 17), 2: (5, 18), 3: (5, 18), 4: (3, 18),
    5: (6, 18), 6: (6, 18), 7: (6, 18), 8: (6, 18), 9: (6, 18),
    10: (6, 18), 11: (5, 17), 12: (7, 16),
}
HIGHLIGHT = {(1, 8), (1, 9), (2, 7), (2, 8)}
CURLS = [(10, 3), (14, 4), (11, 6), (16, 6), (9, 9), (13, 8)]

HEAD = [        # rows 5..11, columns 0..5
    "..FFFF",
    ".FFEEF",
    ".FFEEF",
    "fFFFFF",
    "fFFPPF",
    ".mmFFF",
    "..FFF.",
]

CAP = [         # nightcap, top-left at column 3, row 1
    ".BB......",
    "BBBB.....",
    "BBBBBB...",
    "bbbbbbB..",
    ".......BQ",
]

# Legs: (column, colour, walking group). Diagonal pairs lift together when walking.
LEGS = ((7, "L", "a"), (9, "l", "b"), (14, "L", "b"), (16, "l", "a"))
LEG_ROWS = {"stand": 3, "short": 2, "tuck": 1, "none": 0}

# Jump cycle: (legs, height in sprite pixels, eyes, mouth)
JUMP = [
    ("short", -1, "open", "smile"),
    ("stand", 2, "happy", "open"),
    ("tuck", 5, "happy", "open"),
    ("tuck", 6, "happy", "smile"),
    ("tuck", 4, "happy", "smile"),
    ("stand", 1, "open", "smile"),
    ("short", -1, "open", "smile"),
    ("stand", 0, "open", "smile"),
]

# Tiny 3x5 pixel icons for status marks above the head
GLYPHS = {
    "?": ["###", "..#", ".##", "...", ".#."],
    "!": [".#.", ".#.", ".#.", "...", ".#."],
    "Z": ["###", "..#", ".#.", "#..", "###"],
    "heart": ["##.##", "#####", ".###.", "..#.."],
    "drop": [".#.", ".#.", "###", "###"],
    "bell": ["..#..", ".###.", ".###.", "#####", "..#.."],
}


def wool_color(r, c, last):
    if r >= 11:
        return "S"
    if r == 10:
        return "w"
    if (r, c) in HIGHLIGHT:
        return "H"
    if c >= last - 1 and 2 <= r <= 9:
        return "w"
    return "W"


def with_outline(g):
    """Pad by one pixel and draw an outline (legs get none, so they stay separate)."""
    h, w = len(g), len(g[0])
    pad = [["."] * (w + 2) for _ in range(h + 2)]
    for y in range(h):
        for x in range(w):
            pad[y + 1][x + 1] = g[y][x]
    out = [row[:] for row in pad]
    for y in range(h + 2):
        for x in range(w + 2):
            if pad[y][x] != ".":
                continue
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h + 2 and 0 <= nx < w + 2 and pad[ny][nx] not in ".Ll":
                    out[y][x] = "O"
                    break
    return out


@functools.lru_cache(maxsize=256)
def sprite(eyes, mouth, ear, tail, legs, acc, big_blush):
    """Build one frame of the sheep. Returns a tuple of row strings."""
    g = [["."] * WIDTH for _ in range(HEIGHT)]

    for r, (a, b) in WOOL.items():
        for c in range(a, b + 1):
            g[r][c] = wool_color(r, c, b)
    for c, r in CURLS:
        if g[r][c] == "W":
            g[r][c] = "w"

    for i in range(2):                                   # tail tuft
        g[6 + tail + i][19] = "W" if i == 0 else "w"

    for i, line in enumerate(HEAD):                      # face
        for c, ch in enumerate(line):
            if ch != ".":
                g[5 + i][c] = ch

    if eyes == "closed":
        for c in (3, 4):
            g[6][c] = "F"
            g[7][c] = "c"
    elif eyes == "happy":                                # ^ shaped eye
        for c in (3, 4):
            g[6][c] = "F"
            g[7][c] = "F"
        g[6][3] = "c"
        g[7][2] = "c"
        g[7][4] = "c"
    elif eyes == "wide":
        for c in (3, 4):
            g[5][c] = "E"
    elif eyes == "squint":
        for c in (3, 4):
            g[6][c] = "F"

    if mouth == "open":
        for r, c in ((9, 1), (9, 2), (10, 1), (10, 2)):
            g[r][c] = "M"
    elif mouth == "yawn":
        for r, c in ((9, 1), (9, 2), (10, 1), (10, 2), (11, 2), (11, 3)):
            g[r][c] = "M"
    elif mouth == "wavy":
        g[10][2] = "F"
        g[9][2] = "m"
        g[10][3] = "m"

    if big_blush:
        g[9][2] = "P"
        g[9][5] = "P"

    base = 5 + ear                                       # golden ear (-1 up, +1 down)
    for rr, cols in ((base, (6,)), (base + 1, (6, 7)), (base + 2, (6, 7)), (base + 3, (6,))):
        for c in cols:
            g[rr][c] = "G"

    for col, ch, group in LEGS:                          # legs
        if legs == "walkA":
            n = 3 if group == "a" else 1
        elif legs == "walkB":
            n = 1 if group == "a" else 3
        else:
            n = LEG_ROWS[legs]
        for i in range(n):
            g[13 + i][col] = ch

    if acc == "bow":
        for r, c, ch in ((3, 3, "R"), (3, 5, "R"), (4, 3, "R"), (4, 4, "r"), (4, 5, "R")):
            g[r][c] = ch
    elif acc == "cap":
        for dy, line in enumerate(CAP):
            for dx, ch in enumerate(line):
                if ch != ".":
                    g[1 + dy][3 + dx] = ch
    elif acc == "phones":
        for r, line in enumerate(("DDD", "dDD", "dDD", "DDD"), start=5):
            for dx, ch in enumerate(line):
                g[r][5 + dx] = ch
        for r, c in ((4, 6), (3, 6), (2, 7), (1, 8), (0, 9)):
            g[r][c] = "d"

    return tuple("".join(row) for row in with_outline(g))


def make_transparent(win):
    """Make a window's background see-through. Returns the background colour to use."""
    if SYSTEM == "Windows":
        win.config(bg=KEY)
        win.attributes("-transparentcolor", KEY)
        return KEY
    if SYSTEM == "Darwin":
        win.attributes("-transparent", True)
        win.config(bg="systemTransparent")
        return "systemTransparent"
    win.config(bg="#dfe8ff")        # most Linux setups can't do per-pixel transparency
    return "#dfe8ff"


def notched(c, x0, y0, x1, y1, n, fill):
    """Rectangle with stair-stepped (pixel) corners."""
    pts = [x0 + n, y0, x1 - n, y0, x1 - n, y0 + n, x1, y0 + n,
           x1, y1 - n, x1 - n, y1 - n, x1 - n, y1, x0 + n, y1,
           x0 + n, y1 - n, x0, y1 - n, x0, y0 + n, x0 + n, y0 + n]
    c.create_polygon(pts, fill=fill, outline="")


def enable_dpi_awareness():
    if SYSTEM == "Windows":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


class SheepPet:
    def __init__(self, name, on_command, on_talk=None, on_toggle_mic=None,
                 sleep_after=20, height_cm=2.5, pixel_size=0,
                 walk_chance=0.6, walk_speed=2.0):
        enable_dpi_awareness()
        self.name = name
        self.on_command = on_command          # called in a worker thread with typed text
        self.on_talk = on_talk                # called when you click the sheep
        self.on_toggle_mic = on_toggle_mic
        self.sleep_after = sleep_after
        self.walk_chance = walk_chance
        self.walk_speed = walk_speed          # cm per second

        self.events = queue.Queue()           # thread-safe messages from other threads
        self.mode = "idle"
        self.mode_started = time.time()
        self.mode_until = None
        self.frame = 0
        self.last_activity = time.time()
        self.last_sig = None

        self.walk = None                      # details of the current stroll
        self.home_x = 0                       # where the sheep lives (its corner)
        self.per_cm = 38.0

        self.bubble = None
        self._bubble_gen = 0
        self.cmd_box = None
        self.drag = None
        self._click_job = None

        self._build_window(height_cm, pixel_size)
        self.root.after(TICK_MS, self._tick)

    # -----------------------------------------------------------
    # Public, thread-safe API (call from any thread)
    # -----------------------------------------------------------

    def set_mode(self, mode, seconds=None):
        self.events.put(("mode", mode, seconds))

    def say(self, text, seconds=6):
        self.events.put(("say", text, seconds))

    def ask_confirm(self, prompt):
        """Yes/no popup. Blocks the calling (non-GUI) thread until answered."""
        box = {"event": threading.Event(), "answer": False}
        self.events.put(("confirm", prompt, box))
        box["event"].wait()
        return box["answer"]

    def quit(self):
        self.events.put(("quit",))

    def run(self):
        self.root.mainloop()

    # -----------------------------------------------------------
    # Window setup
    # -----------------------------------------------------------

    def _build_window(self, height_cm, pixel_size):
        root = tk.Tk()
        self.root = root
        root.title(self.name)
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        try:
            self.per_cm = float(root.winfo_fpixels("1c"))
        except Exception:
            self.per_cm = 38.0
        # The sheep is 18 sprite-pixels tall (with outline)
        self.px = int(pixel_size) if pixel_size else max(3, min(10, round(height_cm * self.per_cm / 18)))
        px = self.px
        self.x0, self.y0 = 5 * px, 13 * px          # sprite origin inside the window
        self.win_w, self.win_h = 31 * px, 34 * px   # extra room for jumps and status marks

        bg = make_transparent(root)
        self.canvas = tk.Canvas(root, width=self.win_w, height=self.win_h, bg=bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack()

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.home_x = sw - self.win_w - 16
        root.geometry(f"{self.win_w}x{self.win_h}+{self.home_x}+{sh - self.win_h - 60}")

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double)

        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Talk to me", command=self._single_click)
        menu.add_command(label="Type a command", command=self._open_cmd_box)
        menu.add_command(label="Mic on/off", command=self._toggle_mic)
        menu.add_separator()
        menu.add_command(label="Go to sleep", command=lambda: self._set_mode("sleeping", None))
        menu.add_command(label="Wake up", command=self._wake)
        menu.add_separator()
        menu.add_command(label="Quit", command=root.destroy)
        self.canvas.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
        self.canvas.bind("<Button-2>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    # -----------------------------------------------------------
    # Mouse: drag to move, click to talk, double-click to type
    # -----------------------------------------------------------

    def _press(self, e):
        if self.mode == "walking":
            self._set_mode("idle", None)
        self.drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y(),
                     e.x_root, e.y_root)

    def _move(self, e):
        if self.drag:
            self.root.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def _release(self, e):
        if not self.drag:
            return
        moved = abs(e.x_root - self.drag[2]) + abs(e.y_root - self.drag[3])
        self.drag = None
        if moved >= 5:
            self.home_x = self.root.winfo_x()     # wherever you drop it becomes home
        else:
            if self._click_job is not None:
                self.root.after_cancel(self._click_job)
            self._click_job = self.root.after(260, self._single_click)   # wait: maybe a double-click

    def _double(self, e):
        if self._click_job is not None:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        self.drag = None
        self._open_cmd_box()

    def _single_click(self):
        self._click_job = None
        self._wake()
        if self.on_talk:
            self.on_talk()

    def _toggle_mic(self):
        if self.on_toggle_mic:
            self.on_toggle_mic()

    def _wake(self):
        if self.mode in ("sleeping", "yawn"):
            self._set_mode("idle", None)

    # -----------------------------------------------------------
    # Game-style command box, speech bubble and yes/no popup
    # -----------------------------------------------------------

    def _place_above(self, win, w, h):
        sw = self.root.winfo_screenwidth()
        head_x = self.root.winfo_x() + self.x0 + 3 * self.px
        x = max(4, min(head_x - w // 2, sw - w - 4))
        y = self.root.winfo_y() + self.y0 - h - 7 * self.px
        if y < 0:
            y = self.root.winfo_y() + self.win_h + 4
        win.geometry(f"+{x}+{y}")

    def _open_cmd_box(self):
        if self.cmd_box is not None:
            self.cmd_box.focus_force()
            return
        self._hide_bubble()
        box = tk.Toplevel(self.root)
        box.overrideredirect(True)
        box.attributes("-topmost", True)
        box.config(bg=EDGE_DARK)
        gold = tk.Frame(box, bg=EDGE_GOLD, padx=3, pady=3)
        gold.pack(padx=3, pady=3)
        inner = tk.Frame(gold, bg=PANEL, padx=8, pady=6)
        inner.pack()
        tk.Label(inner, text=">", bg=PANEL, fg=TAB, font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        entry = tk.Entry(inner, width=32, font=(FONT_FAMILY, 11, "bold"), bg="#0f1328", fg=TEXT,
                         insertbackground=EDGE_GOLD, relief="flat", bd=4)
        entry.pack(side="left", padx=(6, 0))
        box.update_idletasks()
        self._place_above(box, box.winfo_width(), box.winfo_height())
        entry.bind("<Return>", lambda e: self._submit(entry))
        entry.bind("<Escape>", lambda e: self._close_cmd_box())
        box.after(60, entry.focus_force)
        self.cmd_box = box

    def _close_cmd_box(self):
        if self.cmd_box is not None:
            self.cmd_box.destroy()
            self.cmd_box = None

    def _submit(self, entry):
        text = entry.get().strip()
        self._close_cmd_box()
        if text:
            threading.Thread(target=self.on_command, args=(text,), daemon=True).start()

    def _hide_bubble(self):
        self._bubble_gen += 1
        if self.bubble is not None:
            try:
                self.bubble.destroy()
            except Exception:
                pass
            self.bubble = None

    def _show_bubble(self, text, seconds):
        text = text.strip()
        if not text:
            return
        self._hide_bubble()
        gen = self._bubble_gen
        if len(text) > 260:
            text = text[:260].rsplit(" ", 1)[0] + "..."

        font = (FONT_FAMILY, 10, "bold")
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        bg = make_transparent(win)
        cv = tk.Canvas(win, bg=bg, highlightthickness=0, bd=0)
        cv.pack()

        # Measure the text first so the box fits it exactly
        probe = cv.create_text(0, 0, text=text, font=font, width=300, anchor="nw")
        bx0, by0, bx1, by1 = cv.bbox(probe)
        cv.delete(probe)
        tw, th = bx1 - bx0, by1 - by0
        top = 12                                   # room for the name tab
        W = max(tw + 40, 110)
        H = top + th + 32

        sw = self.root.winfo_screenwidth()
        head_x = self.root.winfo_x() + self.x0 + 3 * self.px
        x = max(4, min(head_x - W // 2, sw - W - 4))
        y = self.root.winfo_y() + self.y0 - (H + 14) - 7 * self.px
        if y < 0:
            y = self.root.winfo_y() + self.win_h + 4
        tx = max(16, min(head_x - x, W - 16))      # where the pointer tail aims

        cv.config(width=W, height=H + 14)
        notched(cv, 0, top, W, H, 6, EDGE_DARK)
        notched(cv, 3, top + 3, W - 3, H - 3, 4, EDGE_GOLD)
        notched(cv, 6, top + 6, W - 6, H - 6, 3, EDGE_BLUE)
        notched(cv, 8, top + 8, W - 8, H - 8, 2, PANEL)
        cv.create_polygon(tx - 9, H - 1, tx + 9, H - 1, tx, H + 12, fill=EDGE_DARK, outline="")
        cv.create_polygon(tx - 6, H - 1, tx + 6, H - 1, tx, H + 8, fill=EDGE_GOLD, outline="")

        label = self.name.upper()
        tab_w = len(label) * 8 + 18
        notched(cv, 14, 0, 14 + tab_w, 20, 2, EDGE_DARK)
        notched(cv, 16, 2, 12 + tab_w, 18, 2, TAB)
        cv.create_text(14 + tab_w // 2, 10, text=label, font=(FONT_FAMILY, 8, "bold"), fill=PANEL)

        item = cv.create_text(20, top + 16, text="", font=font, fill=TEXT, anchor="nw", width=300)
        arrow = cv.create_polygon(W - 28, H - 22, W - 16, H - 22, W - 22, H - 14,
                                  fill=EDGE_GOLD, outline="", state="hidden")
        cv.bind("<Button-1>", lambda e: self._hide_bubble())
        win.geometry(f"{W}x{H + 14}+{x}+{y}")
        self.bubble = win

        state = {"i": 0}

        def blink(n):
            if gen != self._bubble_gen:
                return
            cv.itemconfig(arrow, state="normal" if n % 2 == 0 else "hidden")
            self.root.after(400, lambda: blink(n + 1))

        def type_step():
            if gen != self._bubble_gen:
                return
            state["i"] = min(len(text), state["i"] + 2)
            cv.itemconfig(item, text=text[:state["i"]])
            if state["i"] < len(text):
                self.root.after(25, type_step)
            else:
                blink(0)
                self.root.after(int(seconds * 1000),
                                lambda: self._hide_bubble() if gen == self._bubble_gen else None)

        type_step()

    def _confirm_dialog(self, prompt):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.config(bg=EDGE_DARK)
        gold = tk.Frame(win, bg=EDGE_GOLD, padx=3, pady=3)
        gold.pack(padx=3, pady=3)
        inner = tk.Frame(gold, bg=PANEL, padx=14, pady=10)
        inner.pack()
        tk.Label(inner, text=self.name.upper(), bg=PANEL, fg=TAB,
                 font=(FONT_FAMILY, 9, "bold")).pack(anchor="w")
        tk.Label(inner, text=prompt, bg=PANEL, fg=TEXT, font=(FONT_FAMILY, 10, "bold"),
                 wraplength=280, justify="left").pack(pady=(4, 8))
        var = tk.IntVar(value=-1)
        row = tk.Frame(inner, bg=PANEL)
        row.pack()
        tk.Button(row, text="YES", width=7, bg=TAB, fg=PANEL, activebackground="#ffd166",
                  relief="flat", font=(FONT_FAMILY, 10, "bold"),
                  command=lambda: var.set(1)).pack(side="left", padx=4)
        tk.Button(row, text="NO", width=7, bg=EDGE_BLUE, fg=TEXT, activebackground="#5566aa",
                  activeforeground=TEXT, relief="flat", font=(FONT_FAMILY, 10, "bold"),
                  command=lambda: var.set(0)).pack(side="left", padx=4)
        win.update_idletasks()
        self._place_above(win, win.winfo_width(), win.winfo_height())
        win.focus_force()
        self.root.wait_variable(var)
        win.destroy()
        return var.get() == 1

    # -----------------------------------------------------------
    # State machine
    # -----------------------------------------------------------

    def _set_mode(self, mode, seconds):
        now = time.time()
        self.mode = mode
        self.mode_started = now
        self.mode_until = now + seconds if seconds else None
        if mode != "walking":
            self.walk = None
        if mode != "sleeping":
            self.last_activity = now

    def _process_events(self):
        """Handle messages queued by other threads. Returns False when quitting."""
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                return True
            kind = ev[0]
            if kind == "mode":
                self._set_mode(ev[1], ev[2])
            elif kind == "say":
                self._show_bubble(ev[1], ev[2])
            elif kind == "confirm":
                ev[2]["answer"] = self._confirm_dialog(ev[1])
                ev[2]["event"].set()
            elif kind == "quit":
                self.root.destroy()
                return False

    # -----------------------------------------------------------
    # Walking along the bottom of the screen
    # -----------------------------------------------------------

    def _start_walk(self, go_home=False):
        """Begin a stroll. Returns False if there isn't enough room to walk."""
        sw = self.root.winfo_screenwidth()
        x = self.root.winfo_x()
        max_x = sw - self.win_w
        if go_home:
            target = self.home_x
            direction = 1 if target > x else -1
            phase = "back"
        else:
            room_left, room_right = x, max_x - x
            direction = -1 if room_left >= room_right else 1     # toward the side with more room
            room = room_left if direction < 0 else room_right
            if room < 5 * self.per_cm:
                return False
            target = x + direction * random.uniform(0.4, 0.9) * room
            phase = "out"
        self._hide_bubble()
        self._set_mode("walking", None)
        self.walk = {"phase": phase, "target": target, "dir": direction, "until": 0.0}
        return True

    def _walk_step(self, now):
        w = self.walk
        if w["phase"] == "pause":                    # stops to look around, then heads home
            if now >= w["until"]:
                w["phase"] = "back"
                w["target"] = self.home_x
                w["dir"] = 1 if self.home_x > self.root.winfo_x() else -1
            return

        step = max(2, int(self.per_cm * self.walk_speed * WALK_TICK_MS / 1000))
        x, y = self.root.winfo_x(), self.root.winfo_y()
        sw = self.root.winfo_screenwidth()
        nx = x + w["dir"] * step
        arrived = nx <= w["target"] if w["dir"] < 0 else nx >= w["target"]
        if arrived:
            nx = int(w["target"])
        nx = max(0, min(sw - self.win_w, nx))
        self.root.geometry(f"+{nx}+{y}")

        if arrived:
            if w["phase"] == "out":
                w["phase"] = "pause"
                w["until"] = now + random.uniform(1.0, 2.2)
            else:
                self._set_mode("yawn", 1.6)          # home again: yawn, then sleep

    def _step(self):
        if not self._process_events():
            return False
        now = time.time()

        if self.mode_until is not None and now >= self.mode_until:
            self._set_mode("sleeping" if self.mode == "yawn" else "idle", None)

        # Been idle long enough: maybe take a walk, then yawn and sleep
        if (self.mode == "idle" and now - self.last_activity > self.sleep_after
                and self.cmd_box is None):
            away = abs(self.root.winfo_x() - self.home_x) > 2 * self.px
            started = False
            if away:
                started = self._start_walk(go_home=True)
            elif random.random() < self.walk_chance:
                started = self._start_walk()
            if not started:
                self._set_mode("yawn", 1.6)

        if self.mode == "walking" and self.walk is not None:
            self._walk_step(now)

        self.frame += 1
        pose = self._pose()
        sig = tuple(pose.values())
        if sig != self.last_sig:            # only redraw when something actually changed
            self.last_sig = sig
            self._draw(pose)
        return True

    def _tick(self):
        try:
            alive = self._step()
        except tk.TclError:
            return                          # window was closed
        except Exception as e:
            print(f"[pet] {e}")
            alive = True
        if alive:
            self.root.after(WALK_TICK_MS if self.mode == "walking" else TICK_MS, self._tick)

    # -----------------------------------------------------------
    # Poses: what the sheep looks like in each mode
    # -----------------------------------------------------------

    def _jump(self, p, f):
        idx = f % len(JUMP)
        legs, h, eyes, mouth = JUMP[idx]
        p["legs"], p["eyes"], p["mouth"] = legs, eyes, mouth
        p["dy"] = -h * self.px
        p["ear"] = -1 if 1 <= idx <= 3 else (1 if 4 <= idx <= 6 else 0)
        p["tail"] = -1 if 1 <= idx <= 3 else (1 if 4 <= idx <= 5 else 0)
        p["shadow"] = max(0, 3 - max(0, h) // 2)
        p["dust"] = idx in (6, 7)

    def _pose(self):
        f, px = self.frame, self.px
        t = time.time() - self.mode_started
        m = self.mode
        p = {"eyes": "open", "mouth": "smile", "ear": 0, "tail": 0, "legs": "stand",
             "acc": ACCESSORY, "dx": 0, "dy": 0, "blush": False, "marks": (),
             "shadow": 3, "dust": False, "flip": False}

        if m == "idle":                                   # standing sideways, facing left
            if f % 40 in (38, 39):
                p["eyes"] = "closed"                      # blink
            if f % 70 in (20, 21, 22):
                p["ear"] = -1                             # ear flick
            if f % 90 < 14:
                p["tail"] = (-1, 0, 1, 0)[(f // 2) % 4]   # tail wag
            p["dy"] = (px // 2) if (f // 10) % 2 else 0   # breathing

        elif m == "walking":                              # strolling along the bottom
            w = self.walk or {}
            moving = w.get("phase") != "pause"
            p["flip"] = w.get("dir", -1) > 0              # face the way it's going
            if moving:
                p["legs"] = "walkA" if (f // 3) % 2 else "walkB"
                p["dy"] = (px // 2) if (f // 3) % 2 else 0
                p["tail"] = (-1, 0, 1, 0)[(f // 3) % 4]
            if f % 40 in (38, 39):
                p["eyes"] = "closed"

        elif m == "working":                              # hop hop hop
            self._jump(p, f)

        elif m == "happy":
            self._jump(p, f)
            p["eyes"], p["blush"] = "happy", True
            p["marks"] = (("heart", (f // 2) % 4),)

        elif m == "listening":
            p.update(eyes="wide", ear=-1, acc="phones", dy=(px // 2) if (f // 4) % 2 else 0)
            p["marks"] = (("bars", f % 4, (f + 1) % 4, (f + 2) % 4),)

        elif m == "talking":
            p["mouth"] = "open" if (f // 2) % 2 else "smile"
            if f % 40 in (38, 39):
                p["eyes"] = "closed"
            p["dy"] = (px // 2) if (f // 3) % 2 else 0

        elif m == "confused":
            p.update(eyes="wide", mouth="wavy", ear=1)
            if t < 1.5:
                p["dx"] = (px // 2) if (f // 2) % 2 else -(px // 2)
            p["marks"] = (("?",), ("drop",))

        elif m == "alert":                                # timer rang
            heights = (0, 2, 3, 2)
            h = heights[f % 4]
            p.update(eyes="wide", mouth="open", ear=-1, legs="tuck" if h else "stand",
                     dy=-h * px, dx=(px // 2) if f % 2 else -(px // 2),
                     shadow=3 if h < 2 else 2)
            p["marks"] = (("bell", f % 2), ("!",))

        elif m == "yawn":
            p.update(eyes="squint", mouth="yawn", ear=1)

        elif m == "sleeping":                             # curled up with a nightcap
            p.update(eyes="closed", legs="none", ear=1, acc="cap",
                     dy=3 * px + ((px // 2) if (f // 12) % 2 else 0))
            p["marks"] = (("zzz", (f // 10) % 3 + 1),
                          ("snore", (0, 1, 2, 3, 2, 1)[(f // 5) % 6]))
        return p

    # -----------------------------------------------------------
    # Drawing
    # -----------------------------------------------------------

    def _glyph(self, rows, x, y, scale, color):
        c = self.canvas
        for fill, off in (("#2a2438", max(1, scale // 3)), (color, 0)):   # drop shadow, then colour
            for yy, row in enumerate(rows):
                for xx, ch in enumerate(row):
                    if ch == "#":
                        c.create_rectangle(x + xx * scale + off, y + yy * scale + off,
                                           x + (xx + 1) * scale + off, y + (yy + 1) * scale + off,
                                           fill=fill, outline="")

    def _draw_shadow(self, s, flip):
        px = self.px
        # Body centre is about column 12.5 when facing left, 9.5 when mirrored
        cx = self.x0 + (px * 19 // 2 if flip else px * 25 // 2)
        ground = self.y0 + 17 * px
        w = (3 + s) * px
        h = int(px * (0.8 + 0.3 * s))
        kw = {"fill": "#6b6478", "outline": ""}
        if SYSTEM != "Darwin":
            kw["stipple"] = "gray50"                     # dithered, retro soft shadow
        self.canvas.create_oval(cx - w, ground - h // 2, cx + w, ground + h, **kw)

    def _draw(self, p):
        c = self.canvas
        px = self.px
        c.delete("all")
        ox, oy = self.x0 + p["dx"], self.y0 + p["dy"]

        self._draw_shadow(p["shadow"], p["flip"])

        rows = sprite(p["eyes"], p["mouth"], p["ear"], p["tail"], p["legs"], p["acc"], p["blush"])
        if p["flip"]:
            rows = tuple(r[::-1] for r in rows)          # mirror it to face right
        for y, row in enumerate(rows):
            x = 0
            while x < len(row):
                ch = row[x]
                if ch == ".":
                    x += 1
                    continue
                start = x
                while x < len(row) and row[x] == ch:
                    x += 1
                c.create_rectangle(ox + start * px, oy + y * px, ox + x * px, oy + (y + 1) * px,
                                   fill=PALETTE[ch], outline="")

        if p["dust"]:                                    # little puffs when landing
            ground = self.y0 + 17 * px
            for gx, gy in ((6, 1), (5, 2), (18, 1), (19, 2)):
                c.create_rectangle(self.x0 + gx * px, ground - gy * px,
                                   self.x0 + (gx + 1) * px, ground - gy * px + px * 3 // 4,
                                   fill="#e9e2d0", outline="")

        self._draw_marks(p["marks"], ox, oy)

    def _draw_marks(self, marks, ox, oy):
        c = self.canvas
        px = self.px
        ms = max(2, px * 3 // 4)
        for m in marks:
            kind = m[0]
            if kind == "?":
                self._glyph(GLYPHS["?"], ox + 2 * px, oy - 5 * px, ms, "#ffd166")
            elif kind == "!":
                self._glyph(GLYPHS["!"], ox + 2 * px, oy - 5 * px, ms, "#ff9f43")
            elif kind == "drop":
                self._glyph(GLYPHS["drop"], ox + 8 * px, oy + 2 * px, ms, "#7fd6ff")
            elif kind == "heart":
                self._glyph(GLYPHS["heart"], ox + 3 * px, oy - (4 + m[1]) * px, ms, "#ff5c7a")
            elif kind == "bell":
                self._glyph(GLYPHS["bell"], ox + px + (px // 2 if m[1] else -(px // 2)),
                            oy - 6 * px, ms, "#ffd166")
            elif kind == "zzz":
                for i in range(m[1]):
                    self._glyph(GLYPHS["Z"], ox + (5 + i * 3) * px, oy - (3 + i * 3) * px,
                                max(2, ms - 1 + i), "#8fb8ff")
            elif kind == "snore":
                r = max(2, (1 + m[1]) * px // 2)
                cx = ox + px // 2 - r
                cy = oy + 10 * px
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#eaf6ff",
                              outline="#7fb2e5", width=2)
            elif kind == "bars":
                for i, v in enumerate(m[1:]):
                    h = (v + 1) * px
                    x = ox - 4 * px + i * (px * 3 // 2)
                    base = oy + 10 * px
                    c.create_rectangle(x, base - h, x + px, base, fill="#7ae582", outline="#1f6b3a")