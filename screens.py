"""
screens.py
One Frame subclass per sidebar tool, matching the wireframes.
"""

import os
import re
import subprocess
import sys
import threading
import webbrowser
from fractions import Fraction
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
from tkinter import font as tkfont

import osu_parser
import tools_logic as logic

DIVISORS = ["1/1", "1/2", "1/4", "1/6", "1/12", "1/24", "1/36", "1/48"]

# Light-card palette used by individually-restyled screens (FrontPage,
# MetadataManagerFrame) that opt into the shell's light look — every other
# tool screen keeps the plain default ttk look.
FRONT_BG = "#fbfbfe"
FRONT_CARD_BG = "#ffffff"
FRONT_BORDER = "#ececf3"
FRONT_TEXT = "#22252f"
FRONT_TEXT_MUTED = "#8b8fa3"
FRONT_GRADIENT_START = (139, 92, 246)   # purple
FRONT_GRADIENT_END = (79, 70, 229)      # indigo
LIGHT_ACCENT = "#4f46e5"
LIGHT_ACCENT_HOVER = "#433bd0"
LIGHT_ACCENT_SOFT = "#eef0fd"


def _style_light_body(base_frame):
    """Overrides `base_frame`'s (a BaseToolFrame) scrolling canvas + body
    background to the light-card palette above, instead of the default
    ttk frame background every tool screen gets otherwise. Call once at
    the top of a screen's __init__ to opt it into the light look."""
    base_frame._scroll_canvas.configure(bg=FRONT_BG)
    ttk.Style().configure("FrontBody.TFrame", background=FRONT_BG)
    base_frame.body.configure(style="FrontBody.TFrame")


def _make_light_entry(parent, **kwargs):
    """Returns a `LightEntry` (see its own class docstring, further down
    this file) — kept as a separate factory function rather than having
    every call site construct `LightEntry` directly, purely for historical
    continuity: this used to build a plain flat-bordered `tk.Entry`, and
    every existing call site already goes through this function."""
    return LightEntry(parent, **kwargs)


def _make_accent_button(parent, text, command, **kwargs):
    defaults = dict(font=("Segoe UI", 11, "bold"), bg=LIGHT_ACCENT,
                     activebackground=LIGHT_ACCENT_HOVER, fg="#ffffff",
                     activeforeground="#ffffff", relief="flat", bd=0,
                     cursor="hand2", padx=18, pady=9)
    defaults.update(kwargs)
    return tk.Button(parent, text=text, command=command, **defaults)


def _make_ghost_button(parent, text, command, **kwargs):
    defaults = dict(font=("Segoe UI", 10, "bold"), bg=LIGHT_ACCENT_SOFT,
                     activebackground=LIGHT_ACCENT_SOFT, fg=LIGHT_ACCENT,
                     activeforeground=LIGHT_ACCENT, relief="flat", bd=0,
                     cursor="hand2", padx=14, pady=8)
    defaults.update(kwargs)
    return tk.Button(parent, text=text, command=command, **defaults)


def _render_gradient_text(text, font_size=30, bold=True,
                           color1=FRONT_GRADIENT_START, color2=FRONT_GRADIENT_END):
    """Renders `text` as a left-to-right gradient image (purple -> indigo),
    for FrontPage's title. A one-row gradient stretched to full height is
    used instead of a per-pixel double loop, since only the gradient's
    width actually varies. Returns None (caller falls back to a plain
    solid-color ttk.Label) if PIL or a usable font isn't available."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        font_path = "segoeuib.ttf" if bold else "segoeui.ttf"
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            font = ImageFont.load_default()
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bbox = probe.textbbox((0, 0), text, font=font)
        pad = 4
        w = bbox[2] - bbox[0] + pad * 2
        h = bbox[3] - bbox[1] + pad * 2
        gradient_row = Image.new("RGBA", (w, 1))
        for x in range(w):
            t = x / max(1, w - 1)
            r = int(color1[0] + (color2[0] - color1[0]) * t)
            g = int(color1[1] + (color2[1] - color1[1]) * t)
            b = int(color1[2] + (color2[2] - color1[2]) * t)
            gradient_row.putpixel((x, 0), (r, g, b, 255))
        gradient = gradient_row.resize((w, h))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=255)
        result = Image.composite(gradient, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask)
        return ImageTk.PhotoImage(result)
    except Exception:
        return None


# =============================================================================
class InfoIcon(tk.Label):
    """A small "(i)" icon that shows a floating tooltip with help text on
    hover. Drop one next to any control that could use a short explanation.
    `align="right"` lines the tooltip's right edge up with the icon's right
    edge instead of its left — useful for icons sitting near the right
    edge of a window, where a left-aligned tooltip could run off-screen."""

    def __init__(self, master, text: str, align: str = "left"):
        super().__init__(master, text="(i)", fg="#3366cc", font=("Segoe UI", 10, "bold"),
                          cursor="hand2", padx=2)
        self.help_text = text
        self.align = align
        self._tip = None
        self.bind("<Enter>", self._show)
        self.bind("<Leave>", self._hide)
        self.bind("<Destroy>", self._hide)

    def _show(self, _event=None):
        if self._tip is not None:
            return
        self._tip = tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self.help_text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=340, font=("Segoe UI", 12),
                 padx=8, pady=6).pack()
        tw.update_idletasks()
        y = self.winfo_rooty() + self.winfo_height() + 4
        if self.align == "right":
            x = self.winfo_rootx() + self.winfo_width() - tw.winfo_reqwidth()
        else:
            x = self.winfo_rootx() + 16
        tw.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _add_hover_tooltip(widget, text):
    """Same floating-tooltip behaviour as InfoIcon, but attachable to any
    existing widget directly (no separate "(i)" icon needed) — used for
    things like showing a pattern's millisecond duration only on hover, to
    keep the normal view uncluttered."""
    state = {"tip": None}

    def show(_event=None):
        if state["tip"] is not None:
            return
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=260, font=("Segoe UI", 11),
                 padx=6, pady=4).pack()
        tw.update_idletasks()
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw.wm_geometry(f"+{x}+{y}")
        state["tip"] = tw

    def hide(_event=None):
        if state["tip"] is not None:
            state["tip"].destroy()
            state["tip"] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)
    widget.bind("<Destroy>", hide)


def _position_over_window(win, reference_widget, width=None, height=None):
    """Positions `win` near wherever the app's main window currently is,
    rather than letting the OS/window manager fall back to whichever
    monitor it considers "primary" — matters if the main window has been
    moved to a different monitor. Centers horizontally over the main
    window and sits a bit below its top edge."""
    top = reference_widget.winfo_toplevel()
    top.update_idletasks()
    px, py = top.winfo_x(), top.winfo_y()
    pw, ph = top.winfo_width(), top.winfo_height()
    win.update_idletasks()
    ww = width if width is not None else win.winfo_reqwidth()
    wh = height if height is not None else win.winfo_reqheight()
    x = px + max(0, (pw - ww) // 2)
    y = py + max(0, (ph - wh) // 3)
    win.geometry(f"{ww}x{wh}+{x}+{y}")


def show_toast(widget, message: str, bg="#b5e61d", fg="#000000",
               display_ms=2000, fade_ms=250, steps=20):
    """A small borderless banner that fades in over the main window, holds
    for `display_ms`, then fades back out and destroys itself — used for
    routine 'it worked' confirmations so the user doesn't have to click a
    dialog closed every time they hit Apply. Deliberately not `-topmost`
    (an earlier version was) — that made it float above every other
    window on the desktop, including other apps, the instant it fired
    even if the user had already switched away from this app. `transient`
    keeps it stacked above its owner window without doing that. It never
    calls `grab_set()` either, so it was never modal — clicking through to
    the rest of the app while a toast is fading was already fine."""
    top = widget.winfo_toplevel()
    toast = tk.Toplevel(top)
    toast.overrideredirect(True)
    toast.transient(top)
    # `overrideredirect` opts this window out of window-manager handling
    # entirely — including the "new windows come to the front" behavior a
    # normal window gets automatically. Without `-topmost` masking it (see
    # above), that meant the toast could spawn stacked *behind* the main
    # window and stay fully invisible for its whole lifetime unless
    # something else happened to raise it — confirmed for real: a fresh
    # toast rendered with a valid geometry and alpha but never appeared
    # on screen. `transient` alone only sets an ownership relationship
    # (taskbar grouping, minimize-together); it doesn't affect initial
    # stacking order the way `lift()` does.
    toast.lift()
    toast.attributes("-alpha", 0.0)
    toast.configure(bg=bg)
    tk.Label(toast, text=message, bg=bg, fg=fg, font=("Segoe UI", 14, "bold"),
             padx=24, pady=10).pack()

    toast.update_idletasks()
    tw, th = toast.winfo_reqwidth(), toast.winfo_reqheight()
    top.update_idletasks()
    px, py, pw = top.winfo_x(), top.winfo_y(), top.winfo_width()
    x = px + max(0, (pw - tw) // 2)
    y = py + 40
    toast.geometry(f"{tw}x{th}+{x}+{y}")

    interval = max(1, fade_ms // steps)

    def set_alpha(a):
        if toast.winfo_exists():
            toast.attributes("-alpha", a)

    def fade(step, reverse, on_done):
        if not toast.winfo_exists():
            return
        set_alpha((steps - step) / steps if reverse else step / steps)
        if step >= steps:
            on_done()
            return
        toast.after(interval, lambda: fade(step + 1, reverse, on_done))

    def hold():
        toast.after(display_ms, lambda: fade(0, True, destroy))

    def destroy():
        if toast.winfo_exists():
            toast.destroy()

    fade(0, False, hold)


def _reveal_in_explorer(path: str):
    """Opens the OS file explorer with `path` pre-selected — used after
    exporting a re-encoded external audio file so the user immediately
    sees the result sitting next to the source file they picked, rather
    than having to go find it themselves. Best-effort: swallows failures
    (e.g. explorer.exe missing on a stripped-down system) rather than
    surfacing a second error on top of an already-successful export."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError:
        pass


class _DialogChoiceButton(tk.Canvas):
    """One full-width rounded-rect choice button for `_ask_choice_dialog`
    — text-only (no icon; see CLAUDE.md/this class's own history — an
    earlier version drew a hand-drawn icon per button, removed on request
    since ttk.Button's plain text-button look was preferred here).
    `primary` renders the option as the highlighted/recommended choice
    (soft indigo fill, bold indigo label) the way the mockup this was
    built from always highlights the first/safest option; every other
    option gets the same soft highlight only on hover, as ordinary hover
    feedback rather than a "recommended" marker."""

    HEIGHT = 46
    RADIUS = 12

    def __init__(self, parent, label: str, primary: bool, command):
        super().__init__(parent, height=self.HEIGHT, highlightthickness=0,
                          bg=FRONT_CARD_BG, cursor="hand2")
        self._label = label
        self._primary = primary
        self._command = command
        self._hover = False
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self._command())

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.HEIGHT
        if w <= 1:
            return
        highlighted = self._primary or self._hover
        fill = LIGHT_ACCENT_SOFT if highlighted else FRONT_CARD_BG
        outline = LIGHT_ACCENT_SOFT if highlighted else FRONT_BORDER
        text_color = LIGHT_ACCENT if self._primary else FRONT_TEXT
        font = ("Segoe UI", 11, "bold") if self._primary else ("Segoe UI", 11)
        _draw_rounded_rect(self, 1, 1, w - 1, h - 1, self.RADIUS,
                            fill=fill, outline=outline, width=1.4)
        self.create_text(w / 2, h / 2, text=self._label, fill=text_color, font=font)


def _ask_choice_dialog(parent, title: str, message: str, options: list) -> str:
    """Modal prompt styled as a light card: a bold title + close control,
    a divider, the message, then one full-width rounded button per
    (label, value) in `options` — plain `messagebox` dialogs don't
    support custom button labels, so this is a small dedicated Toplevel
    instead. Blocks until closed (via `wait_window`) and returns
    whichever value was clicked; closing via the close control or Escape
    returns `options[-1][1]` (every caller puts "cancel" last, matching
    how a dismissed dialog should always read as "cancel")."""
    cancel_value = options[-1][1]
    result = {"choice": cancel_value}
    WRAP = 360
    top = parent.winfo_toplevel()
    # Whichever widget had keyboard focus before this dialog opened (e.g.
    # the search box that triggered it) — restored explicitly once the
    # dialog closes, below. Needed because of a real Tk/Windows gotcha:
    # destroying an `overrideredirect` Toplevel that held `grab_set()` and
    # OS keyboard focus does not reliably hand focus back to the owner
    # window on its own — confirmed for real (search box became untypeable
    # after dismissing the "No songs matched" alert, staying that way even
    # after clicking back into it, until the window was refocused some
    # other way). `focus_get()` can itself raise if the current focus
    # owner is a widget on a different, already-destroyed Toplevel.
    try:
        previously_focused = top.focus_get()
    except tk.TclError:
        previously_focused = None

    win = tk.Toplevel(parent)
    win.withdraw()
    win.overrideredirect(True)
    win.configure(bg=FRONT_CARD_BG, highlightthickness=1, highlightbackground=FRONT_BORDER)

    def choose(value):
        result["choice"] = value
        win.destroy()

    header = tk.Frame(win, bg=FRONT_CARD_BG)
    header.pack(fill="x", padx=20, pady=(18, 0))
    tk.Label(header, text=title, font=("Segoe UI", 14, "bold"), bg=FRONT_CARD_BG,
             fg=FRONT_TEXT).pack(side="left")

    close_btn = tk.Label(header, text="✕", font=("Segoe UI", 11), bg=FRONT_CARD_BG,
                          fg=FRONT_TEXT_MUTED, cursor="hand2")
    close_btn.pack(side="right", anchor="n")
    close_btn.bind("<Button-1>", lambda _e: choose(cancel_value))
    close_btn.bind("<Enter>", lambda _e: close_btn.configure(fg=FRONT_TEXT))
    close_btn.bind("<Leave>", lambda _e: close_btn.configure(fg=FRONT_TEXT_MUTED))

    tk.Frame(win, bg=FRONT_BORDER, height=1).pack(fill="x", padx=20, pady=(16, 16))

    tk.Label(win, text=message, font=("Segoe UI", 11), bg=FRONT_CARD_BG, fg=FRONT_TEXT,
             wraplength=WRAP, justify="left", anchor="w").pack(fill="x", padx=20)

    btn_col = tk.Frame(win, bg=FRONT_CARD_BG)
    btn_col.pack(fill="x", padx=20, pady=(20, 20))
    for i, (label, value) in enumerate(options):
        btn = _DialogChoiceButton(btn_col, label, i == 0, lambda v=value: choose(v))
        btn.pack(fill="x", pady=(0, 8) if i < len(options) - 1 else 0)

    win.protocol("WM_DELETE_WINDOW", lambda: choose(cancel_value))
    win.bind("<Escape>", lambda e: choose(cancel_value))
    win.transient(parent.winfo_toplevel())
    _position_over_window(win, parent)
    win.deiconify()
    win.lift()
    win.focus_force()
    win.grab_set()
    parent.wait_window(win)
    top.focus_force()
    if previously_focused is not None:
        try:
            previously_focused.focus_set()
        except tk.TclError:
            pass  # the previously-focused widget no longer exists
    return result["choice"]


def _ask_silence_quality_choice(parent) -> str:
    """Shown when Add Silence is checked but ffmpeg and/or ffprobe aren't
    resolvable (see `logic.audio_tools_fully_available`). Returns
    "install", "proceed", or "cancel"."""
    return _ask_choice_dialog(
        parent, "Audio quality",
        "You need to install ffmpeg and ffprobe to preserve quality. "
        "Otherwise, audio will receive quality loss.",
        [("Install automatically", "install"),
         ("Proceed with quality loss", "proceed"),
         ("Cancel", "cancel")])


def _ask_ffmpeg_required_choice(parent, feature_name: str = "This feature") -> str:
    """Shown when a feature needing ffmpeg (the Taiko Video Resizer, Audio
    Re-encode) is used but ffmpeg isn't resolvable (see
    `logic.ffmpeg_available`) — unlike Add Silence there's no degraded-
    quality fallback to proceed with, so this only offers "install" or
    "cancel". Returns "install" or "cancel". `feature_name` names whichever
    feature triggered this, e.g. "The Taiko Video Resizer"/"Audio
    Re-encode" — read as the start of a sentence."""
    return _ask_choice_dialog(
        parent, "FFmpeg required",
        f"{feature_name} needs ffmpeg, which wasn't found on this system.",
        [("Install automatically", "install"), ("Cancel", "cancel")])


def _ask_vlc_required_choice(parent) -> str:
    """Shown when the video offset live preview is opened but VLC isn't
    resolvable (see `logic.vlc_available`). Returns "install" or
    "cancel"."""
    return _ask_choice_dialog(
        parent, "VLC required",
        "The live video preview needs VLC, which wasn't found on this system.",
        [("Install automatically", "install"), ("Cancel", "cancel")])


def _show_alert(parent, title: str, message: str, ok_label: str = "OK"):
    """The light-theme replacement for `messagebox.showinfo`/
    `showwarning`/`showerror` — all three collapsed into one plain
    single-button card, since dropping the per-severity icon (see
    `_ask_choice_dialog`'s own history) removed the only visual reason to
    tell them apart; the title/message text alone already carries
    whichever of "it worked"/"needs attention"/"failed" applies (e.g.
    "Invalid value", "No map selected", "Error"). Built directly on
    `_ask_choice_dialog` with a single option, so it shares its exact
    look. Blocks until dismissed; the return value is never meaningful
    (mirrors messagebox's own show* functions, whose return value every
    caller here already ignored)."""
    _ask_choice_dialog(parent, title, message, [(ok_label, "ok")])


def _ask_yesno(parent, title: str, message: str, yes_label: str = "Yes", no_label: str = "No") -> bool:
    """The light-theme replacement for `messagebox.askyesno`. Returns
    True/False; closing via the close control or Escape returns False,
    matching `askyesno`'s own falsy-on-dismiss behavior."""
    return _ask_choice_dialog(parent, title, message, [(yes_label, True), (no_label, False)])


def _ask_okcancel(parent, title: str, message: str, ok_label: str = "OK") -> bool:
    """The light-theme replacement for `messagebox.askokcancel`. Returns
    True/False; closing via the close control or Escape returns False."""
    return _ask_choice_dialog(parent, title, message, [(ok_label, True), ("Cancel", False)])


def _ask_yesnocancel(parent, title: str, message: str):
    """The light-theme replacement for `messagebox.askyesnocancel`.
    Returns True/False/None (Cancel); closing via the close control or
    Escape returns None, matching `askyesnocancel`'s own dismiss-is-
    Cancel behavior."""
    return _ask_choice_dialog(parent, title, message, [("Yes", True), ("No", False), ("Cancel", None)])


def _scale_value_at_x(scale, x: int) -> float:
    """The value a horizontal Scale *should* jump to for a click at pixel
    x — ttk.Scale's own default trough-click behavior only steps by a
    tiny increment rather than jumping to the clicked position, so this
    is used to override that and make a click land exactly where you'd
    expect."""
    width = scale.winfo_width()
    from_ = float(scale.cget("from"))
    to_ = float(scale.cget("to"))
    frac = max(0.0, min(1.0, x / max(1, width)))
    return from_ + frac * (to_ - from_)


# A valid osu! timestamp is "mm:ss:mmm" (matching osu! stable's own editor
# — see parse_osu_cursor_timestamp in tools_logic.py) or a plain ms number
# ("x") — this matches any *prefix* of that shape, so it can gate a text
# field's keystrokes as they're typed (rejecting letters/extra separators
# immediately) while still allowing the value to be built up one character
# at a time. Shared by every "restrict to a time range" field.
_PARTIAL_TIME_RE = re.compile(r"^\d*(:\d{0,2}(:\d{0,3})?)?$")


def _validate_partial_time(P: str) -> bool:
    return len(P) <= 15 and bool(_PARTIAL_TIME_RE.match(P))


def _paste_time_field(widget, var: tk.StringVar):
    """Overrides the default paste behaviour for a timestamp field: rather
    than dumping the raw clipboard text in (which the strict key validator
    above would usually reject outright), pulls out just the timestamp/ms
    number it contains — e.g. pasting "01:02.003 (copied from editor)"
    leaves "01:02.003"."""
    try:
        clip = widget.clipboard_get()
    except tk.TclError:
        return "break"
    result = logic.parse_time_input(clip)
    if result is not None:
        _, cleaned = result
        var.set(cleaned[:15])
    return "break"


def add_header(parent, title: str, info_text: str = None):
    """Adds a big bold section header at the top of a tool screen, with an
    optional InfoIcon describing the tool as a whole right next to it."""
    row = ttk.Frame(parent)
    row.pack(fill="x", padx=10, pady=(10, 4))
    ttk.Label(row, text=title, font=("Segoe UI", 20, "bold")).pack(side="left")
    if info_text:
        InfoIcon(row, info_text).pack(side="left", padx=(6, 0))
    return row


class CoordinateEditorWindow(tk.Toplevel):
    """A 17x13-line grid (16x12 cells, each worth 32 osu! coordinate units —
    the full 0-512 x 0-384 playfield space) with 2 or 4 draggable circles
    representing note types, used by Map Cleaner's "Change Coordinate"
    buttons. Circles snap to grid intersections continuously while being
    dragged. Apply saves the resulting coordinates and closes; the window
    is modal so it can't lose focus to the main window while open."""

    CELL_PX = 45
    GRID_COLS = 16   # -> 17 vertical lines
    GRID_ROWS = 12   # -> 13 horizontal lines
    UNIT_PER_CELL = 32
    MARGIN = 24

    def __init__(self, master, title: str, points_spec: list, initial_coords: dict, on_apply):
        """points_spec: list of {"key", "label", "color", "big"} dicts.
        initial_coords: dict key -> (x, y) in osu! units.
        on_apply(dict key -> (x, y)) is called when Apply is clicked."""
        super().__init__(master)
        self.title(title)
        self.configure(bg=FRONT_BG)
        self.resizable(False, False)
        self.on_apply = on_apply
        self.points_spec = points_spec
        self.coords = {p["key"]: tuple(initial_coords.get(p["key"], (256, 192))) for p in points_spec}
        self._point_items = {}
        self._dragging_key = None

        grid_w = self.GRID_COLS * self.CELL_PX
        grid_h = self.GRID_ROWS * self.CELL_PX

        top = tk.Frame(self, bg=FRONT_BG)
        top.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(top, text=title, bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        _make_accent_button(top, "Apply", self._apply).pack(side="right")

        labels_row = tk.Frame(self, bg=FRONT_BG)
        labels_row.pack(fill="x", padx=16, pady=(6, 10))
        per_col = 2 if len(points_spec) > 2 else len(points_spec)
        num_cols = -(-len(points_spec) // per_col)  # ceil division
        col_frames = []
        for _c in range(num_cols):
            cf = tk.Frame(labels_row, bg=FRONT_BG)
            cf.pack(side="left", padx=(0, 30))
            col_frames.append(cf)

        # Label text is colored to match its point's own dot color, tying
        # the numeric readout directly to the corresponding circle on the
        # grid below instead of leaving every label the same neutral color.
        self.label_vars = {}
        for i, p in enumerate(points_spec):
            var = tk.StringVar()
            self.label_vars[p["key"]] = var
            tk.Label(col_frames[i // per_col], textvariable=var, bg=FRONT_BG, fg=p["color"],
                     font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.canvas = tk.Canvas(self, width=grid_w + 2 * self.MARGIN, height=grid_h + 2 * self.MARGIN,
                                 bg=FRONT_CARD_BG, highlightthickness=1, highlightbackground=FRONT_BORDER)
        self.canvas.pack(padx=16, pady=(0, 16))

        self._draw_grid()
        self._create_points()
        self._update_labels()

        self.canvas.bind("<Button-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.bind("<Escape>", lambda e: self.destroy())

        _position_over_window(self, master)
        self.transient(master)
        self.lift()
        self.focus_force()
        self.grab_set()

    def _unit_to_canvas(self, x, y):
        return (self.MARGIN + (x / self.UNIT_PER_CELL) * self.CELL_PX,
                self.MARGIN + (y / self.UNIT_PER_CELL) * self.CELL_PX)

    def _canvas_to_unit_snapped(self, cx, cy):
        gx = round((cx - self.MARGIN) / self.CELL_PX)
        gy = round((cy - self.MARGIN) / self.CELL_PX)
        gx = max(0, min(self.GRID_COLS, gx))
        gy = max(0, min(self.GRID_ROWS, gy))
        return gx * self.UNIT_PER_CELL, gy * self.UNIT_PER_CELL

    def _draw_grid(self):
        grid_w = self.GRID_COLS * self.CELL_PX
        grid_h = self.GRID_ROWS * self.CELL_PX
        for i in range(self.GRID_COLS + 1):
            x = self.MARGIN + i * self.CELL_PX
            self.canvas.create_line(x, self.MARGIN, x, self.MARGIN + grid_h, fill="#cccccc")
        for j in range(self.GRID_ROWS + 1):
            y = self.MARGIN + j * self.CELL_PX
            self.canvas.create_line(self.MARGIN, y, self.MARGIN + grid_w, y, fill="#cccccc")
        # Bold center crosshair, matching the reference wireframes.
        cx, _ = self._unit_to_canvas(256, 0)
        _, cy = self._unit_to_canvas(0, 192)
        self.canvas.create_line(cx, self.MARGIN, cx, self.MARGIN + grid_h, fill=LIGHT_ACCENT, width=2)
        self.canvas.create_line(self.MARGIN, cy, self.MARGIN + grid_w, cy, fill=LIGHT_ACCENT, width=2)

    def _create_points(self):
        # Drawn in a fixed stacking order (small over big, red over blue)
        # rather than points_spec's own order — that order only matters
        # for label layout, not for which circle ends up on top when they
        # overlap. Canvas items drawn later appear on top, so this sorts
        # ascending by "should end up underneath" -> "should end up on top".
        def z_key(p):
            size_rank = 0 if p["big"] else 1       # small (1) drawn after big (0) -> on top
            color_rank = 1 if p["color"] == "red" else 0  # red (1) drawn after blue (0) -> on top
            return (size_rank, color_rank)

        for p in sorted(self.points_spec, key=z_key):
            x, y = self.coords[p["key"]]
            cx, cy = self._unit_to_canvas(x, y)
            # Diameter = 2 grid squares for the big circle, 1 grid square
            # for the small one — radius is half of that.
            r = self.CELL_PX if p["big"] else self.CELL_PX / 2
            item = self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                            fill=p["color"], outline="black", width=1)
            self._point_items[p["key"]] = (item, r)

    def _update_labels(self):
        for p in self.points_spec:
            x, y = self.coords[p["key"]]
            self.label_vars[p["key"]].set(f"{p['label']}: {int(x)},{int(y)}")

    def _on_canvas_press(self, event):
        closest_key, closest_dist = None, None
        for p in self.points_spec:
            item, r = self._point_items[p["key"]]
            box = self.canvas.coords(item)
            ccx, ccy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            dist = ((event.x - ccx) ** 2 + (event.y - ccy) ** 2) ** 0.5
            if dist <= r * 1.5 and (closest_dist is None or dist < closest_dist):
                closest_dist, closest_key = dist, p["key"]
        self._dragging_key = closest_key
        if closest_key:
            self._move_point(closest_key, event.x, event.y)

    def _on_canvas_drag(self, event):
        if self._dragging_key:
            self._move_point(self._dragging_key, event.x, event.y)

    def _move_point(self, key, event_x, event_y):
        ux, uy = self._canvas_to_unit_snapped(event_x, event_y)
        self.coords[key] = (ux, uy)
        cx, cy = self._unit_to_canvas(ux, uy)
        item, r = self._point_items[key]
        self.canvas.coords(item, cx - r, cy - r, cx + r, cy + r)
        self._update_labels()

    def _apply(self):
        self.on_apply(dict(self.coords))
        self.destroy()


def make_scrollable_toplevel_body(win, bg=None):
    """Wraps a Toplevel's content in a vertically-scrolling canvas — for a
    window whose content can end up taller than the screen, so whatever's
    below the fold (often an Apply/Restart row) stays reachable via
    scrollbar/mouse wheel instead of running off-screen with no way back.
    The scrollbar only appears once content actually overflows the window,
    so a window that already fits looks exactly as it did before. Returns a
    `ttk.Frame` to parent content into, as a drop-in replacement for a plain
    `ttk.Frame(win)` — including a `padding` option to reproduce whatever
    padx/pady margin the caller used to apply on the old frame's own
    `.pack()` call, since that now has to live on the frame itself rather
    than on how it's packed into `win`. Mouse wheel is bound only while the
    pointer is over `win` (the same Enter/Leave-toggled bind_all trick as
    SongSearchResultsWindow — needed since the canvas itself ends up almost
    entirely covered by embedded content, so binding wheel scroll on the
    bare canvas alone doesn't work in practice).

    `bg`, if given, paints the scrolling canvas and the returned frame that
    color instead of leaving them at the default ttk frame background — for
    a caller opting into the light-card theme (see Settings)."""
    outer = ttk.Frame(win)
    outer.pack(fill="both", expand=True)

    canvas_bg = bg if bg is not None else (ttk.Style().lookup("TFrame", "background") or None)
    canvas = tk.Canvas(outer, highlightthickness=0, bg=canvas_bg)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)

    body = ttk.Frame(canvas)
    if bg is not None:
        style_name = f"ScrollBody{id(body)}.TFrame"
        ttk.Style().configure(style_name, background=bg)
        body.configure(style=style_name)
    window_id = canvas.create_window((0, 0), window=body, anchor="nw")

    def _update_scroll_state(_event=None):
        bbox = canvas.bbox("all")
        canvas.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))
        content_h = bbox[3] if bbox else 0
        if content_h > canvas.winfo_height():
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
        elif scrollbar.winfo_ismapped():
            scrollbar.pack_forget()

    def _on_canvas_configure(event):
        canvas.itemconfig(window_id, width=event.width)
        _update_scroll_state()

    body.bind("<Configure>", _update_scroll_state)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _on_wheel(event):
        # Nothing to scroll if content already fits — recomputed fresh
        # rather than trusting the last Configure-triggered state (see the
        # matching guard in BaseToolFrame for why).
        _update_scroll_state()
        if not scrollbar.winfo_ismapped():
            return
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            canvas.yview_scroll(3, "units")

    def _bind_wheel(_event=None):
        win.bind_all("<MouseWheel>", _on_wheel)
        win.bind_all("<Button-4>", _on_wheel)
        win.bind_all("<Button-5>", _on_wheel)

    def _unbind_wheel(_event=None):
        win.unbind_all("<MouseWheel>")
        win.unbind_all("<Button-4>")
        win.unbind_all("<Button-5>")

    win.bind("<Enter>", _bind_wheel)
    win.bind("<Leave>", _unbind_wheel)
    win.bind("<Destroy>", _unbind_wheel)

    return body


class SongSearchResultsWindow(tk.Toplevel):
    """A scrollable list of search results, each showing a small background
    thumbnail plus "Artist - Title", so the user can quickly spot and pick
    the song they meant even among similarly-named results. Clicking a row
    selects that map and closes the window."""

    THUMB_W, THUMB_H = 64, 36
    MAX_ROWS_VISIBLE = 8
    ROW_BG = FRONT_CARD_BG
    ROW_HOVER_BG = LIGHT_ACCENT_SOFT

    def __init__(self, app, matches, on_select):
        super().__init__(app)
        # Stay hidden until every row is built and the window is
        # positioned — otherwise the WM maps it at a default spot/size
        # first, so it visibly flashes there and jumps into place
        # ("ghost window"). Shown via deiconify() at the very end.
        self.withdraw()
        self.title(f"Search results ({len(matches)})")
        self.configure(bg=FRONT_BG)
        self.resizable(False, True)
        self.on_select = on_select
        self._thumb_images = []  # keep references so Tk doesn't GC them
        self._row_labels = []    # (entry, text_label) pairs, for the あ toggle
        self.use_romanised = False

        top = tk.Frame(self, bg=FRONT_BG)
        top.pack(fill="x")
        n = len(matches)
        tk.Label(top, text=f"{n} result{'s' if n != 1 else ''}", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 12, "bold")).pack(side="left", padx=12, pady=10)
        _make_ghost_button(top, "あ", self.toggle_display, width=3).pack(side="right", padx=10, pady=8)

        tk.Frame(self, bg=FRONT_BORDER, height=1).pack(fill="x")

        outer = tk.Frame(self, bg=FRONT_BG)
        outer.pack(fill="both", expand=True)

        self._canvas = canvas = tk.Canvas(outer, width=480, highlightthickness=0, bg=FRONT_BG)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=FRONT_BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling (Windows/Mac send <MouseWheel>; X11 sends
        # <Button-4>/<Button-5>). Binding only on the bare canvas doesn't
        # work in practice — the canvas is almost entirely covered by the
        # embedded row content, so the pointer is essentially never
        # directly over the canvas itself. bind_all while the pointer is
        # anywhere over this window (toggled on Enter/Leave) catches wheel
        # events regardless of which child widget is actually under it.
        def _on_wheel(event):
            if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
                canvas.yview_scroll(3, "units")

        def _bind_wheel(_event=None):
            self.bind_all("<MouseWheel>", _on_wheel)
            self.bind_all("<Button-4>", _on_wheel)
            self.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_event=None):
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")

        self.bind("<Enter>", _bind_wheel)
        self.bind("<Leave>", _unbind_wheel)
        self.bind("<Destroy>", _unbind_wheel)
        self.bind("<Escape>", lambda e: self.destroy())

        for entry in matches:
            self._add_row(inner, entry)

        row_h = 58
        visible_rows = min(len(matches), self.MAX_ROWS_VISIBLE)
        _position_over_window(self, app, width=500, height=visible_rows * row_h + 48)

        # Bring the window to front and give it real keyboard/focus so it
        # doesn't open silently behind the main window, and so the mouse
        # wheel binding above (scoped to <Enter>) actually gets a chance
        # to engage as soon as the pointer is over it. grab_set() keeps it
        # focused/on top for as long as it's open, matching the other
        # popups (Settings, BG/Video preview, troubleshoot).
        self.transient(app)
        self.deiconify()
        self.lift()
        self.focus_force()
        self.grab_set()

    def toggle_display(self):
        """Switches every result row between original (unicode) and
        romanised artist/title, matching the main window's あ button."""
        self.use_romanised = not self.use_romanised
        for entry, label in self._row_labels:
            text = entry.get("display_romanised") if self.use_romanised else entry.get("display")
            label.configure(text=text or entry.get("display", ""))

    def _add_row(self, parent, entry):
        row = tk.Frame(parent, cursor="hand2", bg=self.ROW_BG)
        row.pack(fill="x", pady=(0, 1))

        thumb_label = tk.Label(row, bg="black")
        thumb_label.pack(side="left", padx=6, pady=6)
        # Always set a same-size placeholder image *before* attempting the
        # real thumbnail — a bare tk.Label's width/height are in character/
        # line units until an image is actually assigned, so without this
        # a failed or missing thumbnail left the label sized as "64
        # characters by 36 text lines", a huge black rectangle that could
        # cover the whole row's text.
        self._set_placeholder_thumbnail(thumb_label)
        self._load_thumbnail(thumb_label, entry.get("bg_path"))

        initial_text = entry.get("display_romanised") if self.use_romanised else entry.get("display")
        text = tk.Label(row, text=initial_text or entry.get("display", ""), anchor="w", justify="left",
                         bg=self.ROW_BG, fg=FRONT_TEXT, font=("Segoe UI", 12))
        text.pack(side="left", fill="x", expand=True, padx=6)
        self._row_labels.append((entry, text))

        def select(_event=None, folder=entry["folder"]):
            self.on_select(folder)
            self.destroy()

        for widget in (row, thumb_label, text):
            widget.bind("<Button-1>", select)
            widget.bind("<Enter>", lambda e, r=row, t=text: (r.configure(bg=self.ROW_HOVER_BG),
                                                              t.configure(bg=self.ROW_HOVER_BG)))
            widget.bind("<Leave>", lambda e, r=row, t=text: (r.configure(bg=self.ROW_BG),
                                                              t.configure(bg=self.ROW_BG)))

    def _set_placeholder_thumbnail(self, label):
        from PIL import Image, ImageTk
        blank = Image.new("RGB", (self.THUMB_W, self.THUMB_H), (0, 0, 0))
        tk_img = ImageTk.PhotoImage(blank)
        self._thumb_images.append(tk_img)
        label.configure(image=tk_img, width=self.THUMB_W, height=self.THUMB_H)

    def _load_thumbnail(self, label, bg_path):
        if not bg_path or not os.path.exists(bg_path):
            return  # keep the pixel-sized placeholder already set
        try:
            from PIL import Image, ImageTk
            img = Image.open(bg_path).convert("RGB")
            img.thumbnail((self.THUMB_W, self.THUMB_H))
            canvas_img = Image.new("RGB", (self.THUMB_W, self.THUMB_H), (0, 0, 0))
            paste_x = (self.THUMB_W - img.width) // 2
            paste_y = (self.THUMB_H - img.height) // 2
            canvas_img.paste(img, (paste_x, paste_y))
            tk_img = ImageTk.PhotoImage(canvas_img)
            self._thumb_images.append(tk_img)
            label.configure(image=tk_img, width=self.THUMB_W, height=self.THUMB_H)
        except Exception:
            pass  # keep the (already correctly-sized) placeholder on any read/decode error


def _rounded_rect_points(x1, y1, x2, y2, r):
    """Point list for a smoothed create_polygon rounded rectangle — the
    standard tkinter recipe (a polygon with a corner point pulled in by
    the radius on each side, then smoothed so Tk bows each corner pair
    into an arc)."""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _draw_rounded_rect(canvas, x1, y1, x2, y2, r=12, **kwargs):
    return canvas.create_polygon(_rounded_rect_points(x1, y1, x2, y2, r), smooth=True, **kwargs)


class _IndexProgressBar(tk.Canvas):
    """A thin rounded-pill determinate progress bar — plain ttk.Progressbar
    can't get real rounded ends or a custom track/fill color pair without
    fighting native theme chrome (same "native chrome can't be restyled"
    wall this theme hits everywhere else), so this draws two stacked
    rounded rects on a Canvas instead. Used by main.py's compact indexing
    indicator; lives here (not in main.py) purely so it can share
    `_draw_rounded_rect` directly instead of importing a lone function for
    one shape. Pass `width` for a fixed-size bar packed with `side=`
    (e.g. sitting inline in a title bar corner); omit it to have the bar
    size itself to whatever width its parent hands it via `fill="x"`."""

    HEIGHT = 10
    TRACK_COLOR = "#e7e8f0"
    FILL_COLOR = "#22c55e"

    def __init__(self, parent, bg, width=None):
        super().__init__(parent, width=width, height=self.HEIGHT, highlightthickness=0, bg=bg)
        self._frac = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_progress(self, frac: float):
        self._frac = max(0.0, min(1.0, frac))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.HEIGHT
        if w <= 1:
            return
        r = h / 2
        _draw_rounded_rect(self, 0, 0, w, h, r, fill=self.TRACK_COLOR, outline="")
        fill_w = w * self._frac
        if fill_w >= 1:
            _draw_rounded_rect(self, 0, 0, max(fill_w, h), h, r, fill=self.FILL_COLOR, outline="")


# Module-level "currently open" tracking for _show_light_context_menu, mirroring
# LightDropdown._open_instance — only one of these popups is ever open at a
# time, and the outside-click dismiss handler below is bound to bind_all
# exactly ONCE, permanently (add="+"), never unbound. Per CLAUDE.md's own
# warning, Tk's unbind_all clears *every* handler for a sequence on the "all"
# bindtag, not just this one — calling it here would silently wipe out other
# unrelated app-wide Button-1 handlers (main.py's click-unfocus, LightDropdown's
# own dismiss handler, etc.), so this never unbinds; it just no-ops once no
# menu is open.
_open_context_menu = {"popup": None, "close": None}
_context_menu_global_bound = False


def _close_any_open_context_menu():
    if _open_context_menu["popup"] is not None:
        close = _open_context_menu["close"]
        if close is not None:
            close()


def _on_any_click_for_context_menu(event):
    popup = _open_context_menu["popup"]
    if popup is None:
        return
    w = event.widget
    try:
        if str(w).startswith(str(popup)):
            return  # a row click on the popup itself — its own handler deals with it
    except Exception:
        pass
    _close_any_open_context_menu()


def _show_light_context_menu(parent, x_root, y_root, items):
    """A rounded, custom-drawn right-click context menu positioned at
    (x_root, y_root) — replaces a plain tk.Menu (native OS chrome that
    can't be restyled to match this theme's rounded white cards, same
    "native chrome ignores styling" reasoning as LightDropdown/
    LightCheckbox elsewhere in this file).

    `items` is a list where each entry is either:
      - "separator" — a thin horizontal divider
      - (label, command) — a normal row in the default text color
      - (label, command, color) — a row in a custom color (e.g. a
        destructive "Delete" row)

    Dismissed by clicking a row (which also runs its command), clicking
    anywhere else, or Escape/losing focus — no grab_set(), same reasoning
    as LightDropdown's own popup: grabbing would block the very outside
    click this relies on to close."""
    _close_any_open_context_menu()

    ROW_H = 32
    SEP_H = 11
    PAD_Y = 6
    PAD_X = 16
    RADIUS = 10
    font = ("Segoe UI", 11)
    fnt = tkfont.Font(font=font)

    rows = []
    y = PAD_Y
    max_text_w = 0
    for item in items:
        if item == "separator":
            rows.append({"sep": True, "y1": y, "y2": y + SEP_H})
            y += SEP_H
        else:
            label, command = item[0], item[1]
            color = item[2] if len(item) > 2 else FRONT_TEXT
            max_text_w = max(max_text_w, fnt.measure(label))
            rows.append({"label": label, "command": command, "color": color,
                         "y1": y, "y2": y + ROW_H})
            y += ROW_H
    total_h = y + PAD_Y
    width = max(160, max_text_w + PAD_X * 2)

    popup = tk.Toplevel(parent)
    popup.overrideredirect(True)
    popup.attributes("-topmost", True)
    screen_w = popup.winfo_screenwidth()
    screen_h = popup.winfo_screenheight()
    x = max(0, min(x_root, screen_w - width - 4))
    y_pos = max(0, min(y_root, screen_h - total_h - 4))
    popup.geometry(f"{width}x{total_h}+{x}+{y_pos}")

    canvas = tk.Canvas(popup, width=width, height=total_h, highlightthickness=0, bg=FRONT_BG)
    canvas.pack()

    state = {"hover": None, "closed": False}

    def redraw():
        canvas.delete("all")
        _draw_rounded_rect(canvas, 1, 1, width - 1, total_h - 1, RADIUS,
                            fill=FRONT_CARD_BG, outline=FRONT_BORDER, width=1.5)
        for i, row in enumerate(rows):
            if row.get("sep"):
                ym = (row["y1"] + row["y2"]) // 2
                canvas.create_line(PAD_X, ym, width - PAD_X, ym, fill=FRONT_BORDER)
                continue
            if i == state["hover"]:
                _draw_rounded_rect(canvas, 5, row["y1"] + 1, width - 5, row["y2"] - 1, 8,
                                    fill=LIGHT_ACCENT_SOFT, outline="")
            canvas.create_text(PAD_X, (row["y1"] + row["y2"]) // 2, text=row["label"],
                                anchor="w", fill=row["color"], font=font)

    def row_at(y):
        for i, row in enumerate(rows):
            if not row.get("sep") and row["y1"] <= y <= row["y2"]:
                return i
        return None

    def on_motion(event):
        idx = row_at(event.y)
        if idx != state["hover"]:
            state["hover"] = idx
            redraw()

    def close():
        if state["closed"]:
            return
        state["closed"] = True
        if _open_context_menu["popup"] is popup:
            _open_context_menu["popup"] = None
            _open_context_menu["close"] = None
        popup.destroy()

    def on_click(event):
        idx = row_at(event.y)
        close()
        if idx is not None:
            rows[idx]["command"]()

    canvas.bind("<Motion>", on_motion)
    canvas.bind("<Leave>", lambda _e: (state.update(hover=None), redraw()))
    canvas.bind("<Button-1>", on_click)
    popup.bind("<Escape>", lambda _e: close())
    popup.bind("<FocusOut>", lambda _e: close())

    global _context_menu_global_bound
    if not _context_menu_global_bound:
        _context_menu_global_bound = True
        parent.bind_all("<Button-1>", _on_any_click_for_context_menu, add="+")

    _open_context_menu["popup"] = popup
    _open_context_menu["close"] = close
    redraw()
    popup.focus_force()


# =============================================================================
class RoundedCard(ttk.Frame):
    """A generic rounded white card container for the light theme — the
    building block behind every "grouped box" the light theme needs
    (DiffCheckList, CopySection, and any future one). Built on a Canvas
    since ttk has no real rounded-corner/shadow support and native
    LabelFrame/theme chrome can't be reskinned to match on this theme (see
    DiffCheckList's docstring for the specifics that were confirmed the
    hard way).

    Pack arbitrary content into `.body` (bg=FRONT_CARD_BG) — the card
    resizes to fit body's own requested height automatically on every
    canvas resize. Call `.redraw()` after changing body's content (e.g.
    adding/removing rows) so the card catches up immediately instead of
    waiting for the next resize event to notice."""

    RADIUS = 14
    PAD_X = 18
    PAD_Y_TOP = 12
    PAD_Y_BOTTOM = 14

    def __init__(self, master, page_bg=FRONT_BG):
        ttk.Frame.__init__(self, master)
        self._canvas = tk.Canvas(self, highlightthickness=0, bg=page_bg)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=FRONT_CARD_BG)
        self._window_item = self._canvas.create_window(
            self.PAD_X, self.PAD_Y_TOP, anchor="nw", window=self.body)
        self._canvas.bind("<Configure>", lambda e: self.redraw())

    def redraw(self):
        canvas = self._canvas
        canvas.update_idletasks()
        w = canvas.winfo_width()
        if w <= 1:
            return
        h = self.body.winfo_reqheight() + self.PAD_Y_TOP + self.PAD_Y_BOTTOM
        canvas.configure(height=h)
        canvas.delete("card_bg")
        _draw_rounded_rect(canvas, 1, 1, w - 2, h - 1, self.RADIUS,
                            fill=FRONT_CARD_BG, outline=FRONT_BORDER, tags="card_bg")
        canvas.tag_lower("card_bg")
        canvas.itemconfig(self._window_item, width=w - self.PAD_X * 2)


# =============================================================================
_checkbox_icon_cache = {}


def _render_checkbox_icon(checked: bool, enabled: bool, size: int, accent: str):
    """Renders a smooth, anti-aliased rounded-square checkbox glyph via
    PIL — supersampled at 4x then downsampled with LANCZOS. Replaces an
    earlier version that drew the box+tick straight on a `tk.Canvas`
    (`create_polygon`/`create_line`, no anti-aliasing at all): confirmed
    via a raw-pixel screenshot zoom that it rendered as a near-square box
    (the corner radius was too small relative to the box to read as a
    curve at all at 16px) with a visibly stair-stepped, blocky checkmark
    — not a rendering bug exactly, just the hard ceiling of drawing
    diagonal lines on an unantialiased Canvas at that size. Same
    "Tk can't do this, fall back to PIL" reasoning as `_render_gradient_text`
    above. Cached by (checked, enabled, size, accent): this app only ever
    uses a handful of distinct combinations, so every LightCheckbox
    sharing the same state reuses the same rendered image."""
    key = (checked, enabled, size, accent)
    cached = _checkbox_icon_cache.get(key)
    if cached is not None:
        return cached
    from PIL import Image, ImageDraw, ImageTk
    supersample = 4
    s = size * supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = round(s * 0.28)
    if checked:
        fill = accent if enabled else "#eceef4"
        draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=fill)
        tick_color = "#ffffff" if enabled else "#c7c7d6"
        pts = [(0.24 * s, 0.52 * s), (0.42 * s, 0.70 * s), (0.76 * s, 0.30 * s)]
        draw.line(pts, fill=tick_color, width=round(s * 0.11), joint="curve")
    else:
        border = max(1, round(s * 0.09))
        fill = "#ffffff" if enabled else "#f7f7fa"
        draw.rounded_rectangle([border / 2, border / 2, s - 1 - border / 2, s - 1 - border / 2],
                                radius=radius, fill=fill, outline=FRONT_BORDER, width=border)
    img = img.resize((size, size), Image.LANCZOS)
    icon = ImageTk.PhotoImage(img)
    _checkbox_icon_cache[key] = icon
    return icon


class LightCheckbox(tk.Frame):
    """A custom checkbox (PIL-rendered icon Label + text Label), used by
    DiffCheckList in light mode instead of tk.Checkbutton. Tk's classic
    Checkbutton indicator on Windows draws its checkmark glyph in a fixed
    color that ignores every color-related option (confirmed empirically:
    `selectcolor` does recolor the box fill, as used before this widget
    existed, but there is no equivalent for the tick itself) — the same
    "native chrome ignores styling" wall hit elsewhere in this redesign,
    so a fully custom box+tick is the only reliable way to get a legible
    white tick against the indigo fill. See `_render_checkbox_icon` for
    why the box itself is a rendered image rather than Canvas drawing.

    `command`, if given, is called (no args) after every toggle — same
    shape as ttk.Checkbutton's own `command=`, used for the recurring
    "child options disabled until parent checked" pattern (see CLAUDE.md).
    `set_enabled(bool)` grays the box/text out and ignores clicks while
    disabled, mirroring `.configure(state="disabled")` on the native
    ttk.Checkbutton this replaces — MapCleanerFrame's various `_sync_*`
    methods call this instead."""

    SIZE = 18
    DISABLED_FG = FRONT_TEXT_MUTED

    def __init__(self, master, text, variable, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 accent=LIGHT_ACCENT, font=("Segoe UI", 11), command=None):
        super().__init__(master, bg=bg)
        self.variable = variable
        self.accent = accent
        self.command = command
        self._fg = fg
        self.enabled = True
        self.icon = tk.Label(self, bg=bg, bd=0, cursor="hand2")
        self.icon.pack(side="left", padx=(0, 8))
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font, cursor="hand2")
        self.label.pack(side="left")
        self.icon.bind("<Button-1>", self._toggle)
        self.label.bind("<Button-1>", self._toggle)
        # Redraws whenever the variable changes for *any* reason, not just
        # this checkbox's own click — needed for e.g. a "mutually exclusive
        # checkboxes" pattern (see VideoOffsetShifterFrame's Resizer/SB
        # Code checkboxes) where one checkbox's command handler sets
        # *another* checkbox's variable directly. Without this trace, that
        # other checkbox's own widget is never told to redraw and stays
        # visually stuck on its last-clicked state even though the
        # variable underneath it already changed (confirmed for real: the
        # variable was correctly False but the box still rendered checked).
        self._trace_id = variable.trace_add("write", lambda *_a: self._redraw())
        self.bind("<Destroy>", self._on_destroy)
        self._redraw()

    def _on_destroy(self, _event=None):
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _toggle(self, _event=None):
        if not self.enabled:
            return
        self.variable.set(not self.variable.get())  # redraw happens via the trace above
        if self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        cursor = "hand2" if enabled else "arrow"
        self.icon.configure(cursor=cursor)
        self.label.configure(cursor=cursor, fg=self._fg if enabled else self.DISABLED_FG)
        self._redraw()

    def _redraw(self):
        img = _render_checkbox_icon(self.variable.get(), self.enabled, self.SIZE, self.accent)
        self.icon.configure(image=img)
        self._icon_ref = img  # extra reference alongside the module-level cache


# =============================================================================
class LightRadiobutton(tk.Frame):
    """Custom-drawn radio button (Canvas circle + Label) — same rationale
    as LightCheckbox: a native ttk.Radiobutton's indicator dot on this
    theme ignores color styling the same way the checkbox tick does, so a
    legible indigo-filled dot needs a fully custom-drawn circle instead.

    Every radio button in a group must share the same `variable` (a
    StringVar) — a trace on it redraws this button whenever a *sibling*
    radio button changes it too, matching how a native radio group
    deselects the others automatically. `command`/`set_enabled` mirror
    LightCheckbox's own (see its docstring)."""

    SIZE = 16

    def __init__(self, master, text, variable, value, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 accent=LIGHT_ACCENT, font=("Segoe UI", 11), command=None):
        super().__init__(master, bg=bg)
        self.variable = variable
        self.value = value
        self.accent = accent
        self.command = command
        self._fg = fg
        self.enabled = True
        self.canvas = tk.Canvas(self, width=self.SIZE, height=self.SIZE,
                                 bg=bg, highlightthickness=0, cursor="hand2")
        self.canvas.pack(side="left", padx=(0, 8))
        self.label = tk.Label(self, text=text, bg=bg, fg=fg, font=font, cursor="hand2")
        self.label.pack(side="left")
        self.canvas.bind("<Button-1>", self._select)
        self.label.bind("<Button-1>", self._select)
        self._trace_id = variable.trace_add("write", lambda *_a: self._redraw())
        self.bind("<Destroy>", self._on_destroy)
        self._redraw()

    def _on_destroy(self, _event=None):
        try:
            self.variable.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def _select(self, _event=None):
        if not self.enabled:
            return
        self.variable.set(self.value)
        if self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        cursor = "hand2" if enabled else "arrow"
        self.canvas.configure(cursor=cursor)
        self.label.configure(cursor=cursor, fg=self._fg if enabled else LightCheckbox.DISABLED_FG)
        self._redraw()

    def _redraw(self):
        self.canvas.delete("all")
        selected = self.variable.get() == self.value
        if self.enabled:
            outline = self.accent if selected else FRONT_BORDER
            dot = self.accent
        else:
            outline = FRONT_BORDER
            dot = "#c7c7d6"
        self.canvas.create_oval(1, 1, self.SIZE - 1, self.SIZE - 1,
                                 fill="#ffffff", outline=outline, width=1.5)
        if selected:
            pad = 4
            self.canvas.create_oval(pad, pad, self.SIZE - pad, self.SIZE - pad,
                                     fill=dot, outline="")


# =============================================================================
class LightSpinner(tk.Frame):
    """A numeric Entry + up/down stepper, replacing tk.Spinbox on the light
    theme. tk.Spinbox keeps a visible native seam/border around its
    button area no matter how flat its `buttonuprelief`/`buttondownrelief`
    are configured (confirmed empirically: even fully flattened, the
    button area still reads as a separate glued-on box next to the entry,
    sticking out against the rest of the flat light-themed card) — so
    this wraps a single shared border around a plain Entry plus two tiny
    Canvas-drawn triangle buttons instead, rendering as one seamless box
    like every other light-themed input.

    `fmt`, if given, is an old-style `%`-format string (e.g. "%.1f",
    "%d") applied to the value after each step, matching tk.Spinbox's own
    `format=` option. `on_change`, if given, is called (no args) after
    every step. `validate=(mode, command)` is forwarded to the inner
    Entry's own `validate`/`validatecommand`, same shape as tk.Spinbox's
    own key-validation. `.entry` is exposed directly for callers that
    need to bind their own events (e.g. `<FocusOut>` clamping) the way
    they would on a plain Entry — **always pass `add="+"`** when doing so,
    since this widget already binds its own `<FocusIn>`/`<FocusOut>` on
    `.entry` (for the focus-highlight border below) using `add="+"`
    itself; a plain non-additive `.bind()` would silently replace *both*
    bindings with just the caller's own.

    The border is drawn on a `tk.Canvas` (rounded rect, same construction
    as `RoundedCard`/`LightEntry`) rather than via a plain
    `highlightthickness` square border — `tk.Spinbox`'s square corners
    were never the actual reason this widget exists (see the class's own
    reasoning above about the seam/border around Spinbox's button area),
    but leaving this one square while every text field around it went
    rounded would look inconsistent."""

    RADIUS = 8
    BOX_H = 36
    # See LightEntry.TEXT_Y_NUDGE/BOX_SHIFT — a real tk.Entry embedded on
    # a canvas and centered by pure geometry renders visibly higher than
    # a sibling Label centered the same way; this widget embeds a real
    # Entry the same way LightEntry does, so it needs the identical
    # two-part correction (and the same place()-based BOX_SHIFT technique
    # to move the box itself without a caller's own centering halving it).
    TEXT_Y_NUDGE = 0
    BOX_SHIFT = 0

    def __init__(self, master, textvariable, from_, to, increment=1, width=5,
                 fmt=None, bg=FRONT_CARD_BG, fg=FRONT_TEXT, accent=LIGHT_ACCENT,
                 font=("Segoe UI", 11), on_change=None, validate=None):
        super().__init__(master, bg=bg, highlightthickness=0)
        self.bg = bg
        self.accent = accent
        self._border_color = FRONT_BORDER
        self.textvariable = textvariable
        self.from_ = from_
        self.to = to
        self.increment = increment
        self.fmt = fmt
        self.on_change = on_change
        self.enabled = True

        self.canvas = tk.Canvas(self, height=self.BOX_H, highlightthickness=0, bg=bg)
        inner = tk.Frame(self.canvas, bg=bg)

        entry_kwargs = dict(textvariable=textvariable, width=width, relief="flat", bd=0,
                             bg=bg, fg=fg, insertbackground=fg, font=font, highlightthickness=0)
        if validate is not None:
            entry_kwargs["validate"] = validate[0]
            entry_kwargs["validatecommand"] = validate[1]
        self.entry = tk.Entry(inner, **entry_kwargs)
        self.entry.pack(side="left", padx=(4, 2))

        arrows = tk.Frame(inner, bg=bg)
        arrows.pack(side="left", padx=(0, 2))
        self.up_canvas = tk.Canvas(arrows, width=12, height=8, bg=bg, highlightthickness=0, cursor="hand2")
        self.up_canvas.pack(side="top")
        self.down_canvas = tk.Canvas(arrows, width=12, height=8, bg=bg, highlightthickness=0, cursor="hand2")
        self.down_canvas.pack(side="top")
        self.up_canvas.bind("<Button-1>", lambda e: self._step(1))
        self.down_canvas.bind("<Button-1>", lambda e: self._step(-1))
        self._draw_arrows()

        inner.update_idletasks()
        self._inner_w = inner.winfo_reqwidth()
        self.canvas.create_window(8, self.BOX_H // 2 + self.TEXT_Y_NUDGE, window=inner, anchor="w")
        box_w = self._inner_w + 16
        self.canvas.configure(width=box_w)
        self.configure(width=box_w, height=self.BOX_H)
        self._position_canvas()
        # See LightEntry._position_canvas for why this re-centers on every
        # real <Configure> instead of trusting a fixed y=BOX_SHIFT offset
        # — a caller's `ipady` on its own grid()/pack() call genuinely
        # resizes this widget beyond BOX_H, same as it does for LightEntry.
        self.bind("<Configure>", lambda _e: self._position_canvas())

        self.entry.bind("<FocusIn>", lambda _e: self._set_focused(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._set_focused(False), add="+")
        self._redraw()

    def _position_canvas(self):
        self.update_idletasks()
        y = max(0, (self.winfo_height() - self.BOX_H) // 2) + self.BOX_SHIFT
        self.canvas.place(x=0, y=y)

    def _set_focused(self, focused):
        self._border_color = self.accent if focused else FRONT_BORDER
        self._redraw()

    def _redraw(self):
        c = self.canvas
        w = self._inner_w + 16
        c.delete("box")
        _draw_rounded_rect(c, 1, 1, w - 1, self.BOX_H - 1, self.RADIUS,
                            fill=self.bg, outline=self._border_color, width=1.5, tags="box")
        c.tag_lower("box")

    def _draw_arrows(self):
        for c in (self.up_canvas, self.down_canvas):
            c.delete("all")
        color = FRONT_TEXT_MUTED if self.enabled else "#d7d7e0"
        self.up_canvas.create_polygon(1, 7, 6, 1, 11, 7, fill=color, outline="")
        # Not a literal mirror of the up triangle's coordinates (1,1 / 6,7 /
        # 11,1) — confirmed empirically (screenshot + per-row pixel count)
        # that Tk's polygon scan-fill rasterizes a flat-top triangle
        # differently from a flat-bottom one: the exact mirror rendered as
        # 6 filled rows / 35px versus the up triangle's 5 rows / 25px, a
        # visibly "bigger" down arrow despite identical-looking coordinates.
        # These coordinates (base narrowed to 1.5-10.5, base row at y=2
        # instead of y=1) were the one candidate, out of several tried,
        # that rasterized to an exact pixel-for-pixel mirror of the up
        # triangle (25px, 5 rows, row-width profile 9,7,5,3,1 vs up's
        # 1,3,5,7,9) — don't "simplify" this back to a clean mirror of the
        # up coordinates, that's the version confirmed to look bigger.
        self.down_canvas.create_polygon(1.5, 2, 6, 7, 10.5, 2, fill=color, outline="")

    def _step(self, direction):
        if not self.enabled:
            return
        try:
            val = float(self.textvariable.get())
        except ValueError:
            val = self.from_
        val = round(val + direction * self.increment, 6)
        val = max(self.from_, min(self.to, val))
        if self.fmt:
            text = self.fmt % val
        elif val == int(val):
            # Without an explicit fmt, `val` is still a float internally
            # (see the float() conversion above) even for an
            # integer-only field like a plain +/-1 offset stepper — left
            # as str(val) this prints a stray ".0" on every whole number
            # (confirmed for real: stepping "New offset" turned 17500 into
            # "17500.0"). Only reaching for int() when the value actually
            # *is* whole keeps a genuinely fractional value (e.g. an
            # increment=0.1 field with no fmt given) printing normally.
            text = str(int(val))
        else:
            text = str(val)
        self.textvariable.set(text)
        if self.on_change:
            self.on_change()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        self.entry.configure(state="normal" if enabled else "disabled")
        cursor = "hand2" if enabled else "arrow"
        self.up_canvas.configure(cursor=cursor)
        self.down_canvas.configure(cursor=cursor)
        self._draw_arrows()


# =============================================================================
class LightDropdown(tk.Frame):
    """Read-only dropdown/picker, replacing ttk.Combobox(state="readonly")
    on the light theme. Every ttk.Combobox this replaces was already
    read-only (a picker, not free-text entry) — this widget only supports
    that mode, same "custom-drawn because native chrome can't be
    controlled" reasoning as LightCheckbox/LightRadiobutton: ttk's native
    popdown listbox is a plain unstyled rectangle (selectbackground/
    selectforeground/font are about the only things you can touch) with
    no rounded corners and no per-row hover highlight, so matching a
    rounded-corner panel with a lavender hover bar needs a fully custom
    popup instead.

    Backed by a `textvariable` StringVar the same way ttk.Combobox is
    (`.get()`/`.set()` on it work exactly the same), and generates the
    same `<<ComboboxSelected>>` virtual event on selection so an existing
    `.bind("<<ComboboxSelected>>", ...)` call site doesn't need to change.
    `.set_values(values)` replaces ttk.Combobox's `combo["values"] = ...`
    bracket-assignment idiom — "values" isn't a real Tk widget option, so
    plain item assignment doesn't apply to a hand-built widget like this.

    Only one dropdown's popup can be open at a time app-wide — opening a
    second closes whichever one was already open, tracked via the
    `_open_instance` class attribute (mirrors a native `<select>`).

    Dismissed the same way a native `<select>` would be, via three
    independent mechanisms since no single Tk event reliably covers every
    case (see `_ensure_global_handlers`):
    - Selecting a row, or `<Escape>`/`<FocusOut>` on the popup itself.
    - Clicking anywhere else *in this app* — a permanent, app-wide
      `bind_all("<Button-1>", ..., add="+")` installed once (not per
      dropdown) and never removed, so it can't ever clash with or clobber
      main.py's own pre-existing global click-unfocus handler.
    - The window *hosting the dropdown* losing OS-level activation (e.g.
      Alt-Tab, or clicking a different application) — `<FocusOut>` on the
      borderless popup itself was found not to fire reliably for this in
      practice, so each real (decorated) Toplevel that ever hosts a
      dropdown also gets a `<Deactivate>` binding, lazily, the first time
      one is created inside it."""

    ROW_H = 32
    RADIUS = 10
    PAD_Y = 6

    _open_instance = None
    _global_click_bound = False
    _deactivate_bound_toplevels = set()  # str(toplevel) already wired up

    def __init__(self, master, textvariable, values=(), width=20,
                 bg=FRONT_CARD_BG, page_bg=FRONT_BG, fg=FRONT_TEXT,
                 accent=LIGHT_ACCENT, font=("Segoe UI", 11)):
        super().__init__(master, bg=page_bg, highlightthickness=0)
        self.textvariable = textvariable
        self.values = list(values)
        self.bg = bg
        self.page_bg = page_bg
        self.fg = fg
        self.accent = accent
        self.font = font
        self._is_open = False
        self._popup = None
        self._popup_canvas = None
        self._hover_index = None
        self._current_idx = None
        self._box_h = 32
        # This widget draws its own box on a Canvas (for real rounded
        # corners, unlike a plain Entry/ttk.Combobox) rather than wrapping
        # a real Entry, so it has no built-in "width in characters" the
        # way tk.Entry does — approximated here from the given char count
        # instead, close enough to match how wide the ttk.Combobox call
        # sites this replaces used to size themselves.
        self._box_w = max(60, width * 8 + 34)

        self.canvas = tk.Canvas(self, width=self._box_w, height=self._box_h,
                                 highlightthickness=0, bg=page_bg, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._toggle)
        self._trace_id = textvariable.trace_add("write", lambda *_a: self._redraw())
        self.bind("<Destroy>", self._on_destroy)
        self._ensure_global_handlers()
        self._redraw()

    def _ensure_global_handlers(self):
        cls = LightDropdown
        if not cls._global_click_bound:
            cls._global_click_bound = True
            # add="+" so this coexists with any other app-wide Button-1
            # handler (main.py's App already installs one for a different
            # reason) rather than overwriting it — and since this is only
            # ever *added*, never removed, there's no matching unbind_all
            # to ever risk wiping that other handler out either (unlike
            # bind_all, Tk's unbind_all clears *every* handler for a
            # sequence on the "all" bindtag, not just this one — see
            # CLAUDE.md).
            self.bind_all("<Button-1>", LightDropdown._on_any_click, add="+")
        top = self.winfo_toplevel()
        key = str(top)
        if key not in cls._deactivate_bound_toplevels:
            cls._deactivate_bound_toplevels.add(key)
            top.bind("<Deactivate>", lambda _e: LightDropdown._close_open_instance(), add="+")

    @staticmethod
    def _close_open_instance():
        inst = LightDropdown._open_instance
        if inst is not None:
            inst._close_popup()

    @staticmethod
    def _on_any_click(event):
        inst = LightDropdown._open_instance
        if inst is None:
            return
        w = event.widget
        if w is inst.canvas:
            return  # the box's own <Button-1> handler already deals with this click
        if inst._popup is not None:
            try:
                if str(w).startswith(str(inst._popup)):
                    return  # a row click — the popup canvas's own handler deals with it
            except Exception:
                pass
        inst._close_popup()

    def _on_destroy(self, _event=None):
        try:
            self.textvariable.trace_remove("write", self._trace_id)
        except Exception:
            pass
        self._close_popup()

    def set_values(self, values):
        self.values = list(values)

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        border = self.accent if self._is_open else FRONT_BORDER
        _draw_rounded_rect(c, 1, 1, self._box_w - 1, self._box_h - 1, self.RADIUS,
                            fill=self.bg, outline=border, width=1.5)
        c.create_text(12, self._box_h // 2, text=self.textvariable.get(), anchor="w",
                       fill=self.fg, font=self.font)
        # Chevron flips to point up while the popup is open, matching a
        # native <select>'s own convention for signalling open/closed.
        cx, cy = self._box_w - 18, self._box_h // 2
        pts = (cx - 5, cy + 2, cx + 5, cy + 2, cx, cy - 3) if self._is_open \
            else (cx - 5, cy - 2, cx + 5, cy - 2, cx, cy + 3)
        c.create_polygon(pts, fill=FRONT_TEXT_MUTED, outline="")

    def _toggle(self, _event=None):
        if self._is_open:
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self):
        if LightDropdown._open_instance is not None and LightDropdown._open_instance is not self:
            LightDropdown._open_instance._close_popup()
        if not self.values:
            return
        self._is_open = True
        LightDropdown._open_instance = self
        self._redraw()

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        self._popup = popup

        w = self._box_w
        h = len(self.values) * self.ROW_H + self.PAD_Y * 2
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self._box_h + 4
        popup.geometry(f"{w}x{h}+{x}+{y}")

        canvas = tk.Canvas(popup, width=w, height=h, highlightthickness=0, bg=self.page_bg)
        canvas.pack()
        self._popup_canvas = canvas
        self._hover_index = None
        try:
            self._current_idx = self.values.index(self.textvariable.get())
        except ValueError:
            self._current_idx = None
        self._draw_popup_rows()

        canvas.bind("<Motion>", self._on_popup_motion)
        canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        canvas.bind("<Button-1>", self._on_popup_click)
        # No grab_set() — this is a transient popup, not a modal dialog;
        # grabbing would block the outside click this relies on to close.
        popup.bind("<Escape>", lambda _e: self._close_popup())
        popup.bind("<FocusOut>", lambda _e: self._close_popup())
        popup.focus_force()

    def _draw_popup_rows(self):
        c = self._popup_canvas
        c.delete("all")
        w = self._box_w
        h = len(self.values) * self.ROW_H + self.PAD_Y * 2
        _draw_rounded_rect(c, 1, 1, w - 1, h - 1, self.RADIUS,
                            fill=self.bg, outline=FRONT_BORDER, width=1.5)
        for i, val in enumerate(self.values):
            y1 = self.PAD_Y + i * self.ROW_H
            y2 = y1 + self.ROW_H
            if i == self._hover_index:
                _draw_rounded_rect(c, 5, y1 + 2, w - 5, y2 - 2, 8,
                                    fill=LIGHT_ACCENT_SOFT, outline="")
            selected = i == self._current_idx
            text_color = self.accent if selected else self.fg
            text_font = (self.font[0], self.font[1], "bold") if selected else self.font
            c.create_text(16, (y1 + y2) // 2, text=str(val), anchor="w",
                           fill=text_color, font=text_font)

    def _on_popup_motion(self, event):
        idx = (event.y - self.PAD_Y) // self.ROW_H
        self._set_hover(idx if 0 <= idx < len(self.values) else None)

    def _set_hover(self, idx):
        if idx != self._hover_index:
            self._hover_index = idx
            self._draw_popup_rows()

    def _on_popup_click(self, event):
        idx = (event.y - self.PAD_Y) // self.ROW_H
        if 0 <= idx < len(self.values):
            self.textvariable.set(self.values[idx])
            self._close_popup()
            self.event_generate("<<ComboboxSelected>>")

    def _close_popup(self):
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
            self._popup_canvas = None
        if self._is_open:
            self._is_open = False
            self._redraw()
        if LightDropdown._open_instance is self:
            LightDropdown._open_instance = None


# =============================================================================
class LightEntry(tk.Frame):
    """Rounded-corner text field, replacing the flat `highlightthickness`-
    bordered `tk.Entry` that `_make_light_entry` used to return directly.
    A plain `tk.Entry` has no border-radius option of its own — same
    structural limit as everything else on this theme — so this wraps a
    real, borderless `tk.Entry` inside a `tk.Canvas`-drawn rounded
    rectangle instead, the same "Canvas background + embedded real
    widget" construction `RoundedCard`/`LightSpinner` already use. Applies
    equally to a typeable field (`state="normal"`, the default) and an
    untypeable one (`state="readonly"`/`"disabled"`) — the rounded box
    itself doesn't care which; only the embedded `Entry`'s own state does.

    Drop-in replacement for what `_make_light_entry` used to return —
    every existing call site already only ever used `.get()`/`.insert()`/
    `.delete()`/`.bind()` (all forwarded here to the real inner `Entry`,
    `.entry`) and `.configure(state=...)` (the one configure kwarg any
    call site actually used post-construction, forwarded the same way).
    `.pack()`/`.grid()` work as normal since this is a real `tk.Frame`.

    Supports being stretched via `.grid(sticky="we")` in a weighted
    column (used by Metadata Manager's tag fields) — the rounded rect and
    the embedded `Entry`'s own width both track the canvas's actual
    allocated width on `<Configure>`, the same resize-tracking
    `RoundedCard` already needs for its own body.

    Border turns `accent`-colored while the `Entry` has keyboard focus,
    matching `LightSpinner`/`LightDropdown`'s own focus-highlight
    convention."""

    RADIUS = 8
    PAD_X = 10
    BOX_H = 36
    # A real tk.Entry, embedded on a canvas via create_window and centered
    # purely by geometry (BOX_H // 2), renders its text visibly *higher*
    # than a tk.Label centered the same way with the same font — Tk's two
    # widget implementations don't vertically position text within their
    # own box identically. TEXT_Y_NUDGE closes *that* gap on its own,
    # independently of BOX_SHIFT below, which moves the box (and this
    # already-centered text riding along inside it) as one rigid unit
    # relative to a sibling Label, purely for visual taste. Don't "solve"
    # BOX_SHIFT by adjusting this instead (or vice versa) — that
    # reintroduces the very gap TEXT_Y_NUDGE exists to close, just
    # relative to the box instead of the Label; keep the two independent.
    # Tuned by real screenshot + pixel measurement each time, not guessed
    # — most recently nudged back up slightly (6 -> 4) per direct visual
    # feedback that text sat a touch low within the box's own borders.
    TEXT_Y_NUDGE = 0
    # Shifts the drawn box (and everything on its canvas — border *and*
    # embedded Entry together) down within this widget's own row-alignment
    # bounding box, via `place()` rather than pack()'d padding. A caller's
    # grid/pack centers *this whole widget* against a sibling Label using
    # this widget's own declared height (BOX_H, unchanged) — so ordinary
    # padding added on just one side gets *halved* by that same centering
    # (half the added space ends up above the old center, half below,
    # diluting a desired D-pixel shift down to only D/2). `place()` here
    # sidesteps that: the canvas is deliberately allowed to render
    # BOX_SHIFT px past this Frame's own declared bottom edge, which
    # doesn't disturb the Frame's own declared size (still exactly BOX_H,
    # so it keeps centering against the Label exactly as it always did)
    # while still visibly moving the box itself down by the full,
    # undiluted BOX_SHIFT. Since this Frame has no visible content of its
    # own (bg matches the canvas's), the overflow is seamless. Purely a
    # deliberate visual offset from the Label's exact center, requested
    # directly — the box+text unit still keeps its own internal centering
    # (via TEXT_Y_NUDGE above) exactly as before.
    BOX_SHIFT = 0

    def __init__(self, parent, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 accent=LIGHT_ACCENT, font=("Segoe UI", 11), **entry_kwargs):
        super().__init__(parent, bg=bg, highlightthickness=0)
        self.bg = bg
        self.accent = accent
        self._border_color = FRONT_BORDER

        self.canvas = tk.Canvas(self, height=self.BOX_H, highlightthickness=0, bg=bg)

        defaults = dict(relief="flat", bd=0, highlightthickness=0,
                         bg=bg, fg=fg, font=font, insertbackground=fg,
                         disabledbackground=bg, readonlybackground=bg)
        defaults.update(entry_kwargs)
        self.entry = tk.Entry(self.canvas, **defaults)
        self.entry.update_idletasks()
        self._entry_w = self.entry.winfo_reqwidth()
        self._window_id = self.canvas.create_window(
            self.PAD_X, self.BOX_H // 2 + self.TEXT_Y_NUDGE, window=self.entry, anchor="w")

        self._current_w = self._entry_w + self.PAD_X * 2
        self.canvas.configure(width=self._current_w)
        self.configure(width=self._current_w, height=self.BOX_H)
        self._position_canvas()

        # bind() is overridden below to forward to the inner Entry (so
        # external callers can do e.g. `light_entry.bind("<<Paste>>", ...)`
        # without reaching into `.entry` themselves) — this Frame-level
        # <Configure> binding needs the real tk.Frame.bind underneath that
        # override, not the override itself.
        tk.Frame.bind(self, "<Configure>", self._on_configure)
        # add="+" so an external `.bind("<FocusOut>", ...)` call (e.g. a
        # numeric field clamping its value on focus-out) coexists with
        # this instead of silently replacing it — see LightSpinner's own
        # docstring note about the identical hazard.
        self.entry.bind("<FocusIn>", lambda _e: self._set_focused(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._set_focused(False), add="+")
        self._redraw()

    def _position_canvas(self, width=None):
        # Centers the canvas within this widget's *actual* current height
        # before applying BOX_SHIFT — NOT a hardcoded y=BOX_SHIFT assuming
        # self stays exactly BOX_H tall. A caller's `ipady` on its own
        # grid()/pack() call genuinely *resizes* this widget (confirmed
        # for real: unlike pady, which only adds external margin around an
        # unchanged-size widget, ipady inflates the widget's own allocated
        # rectangle) — Metadata Manager's fields use ipady=6, making self
        # 48px tall, not 36. A fixed y=BOX_SHIFT then anchored the canvas
        # near self's own top instead of its middle, pushing the box
        # *above* the Label's center instead of the intended tiny bit
        # below it. Recomputing from self.winfo_height() every time keeps
        # this correct regardless of whether the caller uses ipady or not.
        self.update_idletasks()
        y = max(0, (self.winfo_height() - self.BOX_H) // 2) + self.BOX_SHIFT
        kwargs = {"x": 0, "y": y}
        if width is not None:
            kwargs["width"] = width
        self.canvas.place(**kwargs)

    def _on_configure(self, event):
        self._current_w = event.width
        self._position_canvas(width=event.width)
        self.canvas.itemconfig(self._window_id,
                                width=max(self._entry_w, event.width - self.PAD_X * 2))
        self._redraw()

    def _set_focused(self, focused):
        self._border_color = self.accent if focused else FRONT_BORDER
        self._redraw()

    def _redraw(self):
        c = self.canvas
        c.delete("box")
        _draw_rounded_rect(c, 1, 1, self._current_w - 1, self.BOX_H - 1, self.RADIUS,
                            fill=self.bg, outline=self._border_color, width=1.5, tags="box")
        c.tag_lower("box")

    # --- Entry-compatible surface, forwarded to the real inner Entry ---
    def get(self, *args, **kwargs):
        return self.entry.get(*args, **kwargs)

    def insert(self, *args, **kwargs):
        return self.entry.insert(*args, **kwargs)

    def delete(self, *args, **kwargs):
        return self.entry.delete(*args, **kwargs)

    def bind(self, *args, **kwargs):
        return self.entry.bind(*args, **kwargs)

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.entry.configure(state=kwargs.pop("state"))
        if kwargs:
            super().configure(**kwargs)

    config = configure


# =============================================================================
class DiffCheckList(ttk.LabelFrame):
    """The recurring 'Apply to: [x] Diff1 [x] Diff2 ...' widget, ticked by
    default. Displays each difficulty's [Version] name (e.g. "Oni") rather
    than the full filename, for compactness.

    `light=True` (opt-in, default off so every other caller is unaffected)
    replaces the native ttk.LabelFrame chrome entirely with a RoundedCard,
    plus tk.Checkbutton (not ttk) rows with an indigo `selectcolor` — a
    native-themed ttk.Checkbutton's indicator glyph, and a ttk.LabelFrame's
    own border/label, are drawn by Windows' own UxTheme under the "vista"
    theme and ignore style color/geometry overrides (confirmed empirically
    elsewhere in this redesign — see main.py's title bar / sidebar, which
    hit the same wall), so matching the light theme's card look needs
    fully custom rendering instead of trying to reskin the native widgets.

    `label_inside=False` skips drawing the bold "Apply to" label inside
    the card — for a caller (e.g. CopySection) that wants to put its own
    label above the card instead, as part of a larger enclosing layout."""

    def __init__(self, master, app, label="Apply to:", light=False, label_inside=True, nested=False):
        self.light = light
        if light:
            # Deliberately calls ttk.Frame.__init__ instead of going through
            # super()/LabelFrame — even with text="", a real ttk::labelframe
            # Tk widget still reserves a label-margin strip above its content
            # on this theme (confirmed empirically: a persistent gray band
            # appeared above the card no matter what padding/borderwidth was
            # configured), since that margin is baked into the "labelframe"
            # layout itself, not conditional on the label text being empty.
            # A plain ttk::frame widget has no such reserved region.
            ttk.Frame.__init__(self, master)
            if nested:
                # `nested=True` (opt-in — every pre-existing caller is
                # unaffected) is for a caller whose own master is already
                # sitting inside another RoundedCard's body (CopySection,
                # for Volume/Kiai Copier) — a second RoundedCard here would
                # just be a card drawn inside a card, adding a whole extra
                # ring of padding for no visual benefit, and it was real,
                # measurable bloat: Volume/Kiai Copier stacks two full
                # CopySection blocks, each double-carded this way, and the
                # combined height didn't fit the app's own default window
                # size — a scrollbar appeared on a fresh launch even
                # before any pattern/diff content grew it further. A plain
                # Frame matching the surrounding card's own background
                # draws nothing extra and needs no separate redraw() call.
                self._card = None
                card_body = tk.Frame(self, bg=FRONT_CARD_BG)
                card_body.pack(fill="both", expand=True)
            else:
                self._card = RoundedCard(self)
                self._card.pack(fill="both", expand=True)
                card_body = self._card.body
            if label_inside:
                tk.Label(card_body, text=label.rstrip(":").strip(), bg=FRONT_CARD_BG,
                         fg=FRONT_TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
            self.inner = tk.Frame(card_body, bg=FRONT_CARD_BG)
            self.inner.pack(fill="both", expand=True)
        else:
            super().__init__(master, text=label)
            self.inner = ttk.Frame(self)
            self.inner.pack(fill="both", expand=True)
        self.app = app
        self.vars = {}          # display label -> BooleanVar
        self.label_to_file = {}  # display label -> filename

    MAX_ROWS_PER_COLUMN = 3

    def refresh(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.vars.clear()
        folder, diffs = self.app.get_diff_files()
        self.label_to_file = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        ordered_labels = sorted(self.label_to_file.keys(), key=osu_parser.taiko_diff_sort_key)
        for i, label in enumerate(ordered_labels):
            v = tk.BooleanVar(value=True)
            if self.light:
                cb = LightCheckbox(self.inner, label, v, font=("Segoe UI", 11))
            else:
                cb = ttk.Checkbutton(self.inner, text=label, variable=v)
            row = i % self.MAX_ROWS_PER_COLUMN
            col = i // self.MAX_ROWS_PER_COLUMN
            cb.grid(row=row, column=col, sticky="w", padx=(0, 16), pady=1)
            self.vars[label] = v
        if self.light and self._card is not None:
            self._card.redraw()

    def selected(self):
        return [self.label_to_file[label] for label, v in self.vars.items() if v.get()]


class DiffRadioList(ttk.LabelFrame):
    """Single-select variant of DiffCheckList — radio buttons instead of
    checkboxes, for a tool that only ever targets one difficulty at a
    time (unlike the usual "apply to several diffs at once" pattern
    DiffCheckList is for). Supports preselecting a specific difficulty
    (e.g. whichever one is actually open in a live osu! editor) instead
    of always defaulting to the alphabetically/priority-first one.

    `light=True` (opt-in, default off so every other caller is unaffected)
    renders as a RoundedCard with LightRadiobutton rows instead of a
    native LabelFrame+ttk.Radiobutton — same rationale as DiffCheckList's
    own light mode (see its docstring)."""

    def __init__(self, master, app, label="Apply to:", light=False, label_inside=True):
        self.light = light
        if light:
            ttk.Frame.__init__(self, master)
            self._card = RoundedCard(self)
            self._card.pack(fill="both", expand=True)
            if label_inside:
                tk.Label(self._card.body, text=label.rstrip(":").strip(), bg=FRONT_CARD_BG,
                         fg=FRONT_TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
            self.inner = tk.Frame(self._card.body, bg=FRONT_CARD_BG)
            self.inner.pack(fill="both", expand=True)
        else:
            super().__init__(master, text=label)
            self.inner = ttk.Frame(self)
            self.inner.pack(fill="both", expand=True)
        self.app = app
        self.label_to_file = {}
        self.var = tk.StringVar()

    MAX_ROWS_PER_COLUMN = 3

    def refresh(self, preselect_file: str = None):
        for w in self.inner.winfo_children():
            w.destroy()
        folder, diffs = self.app.get_diff_files()
        self.label_to_file = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        ordered_labels = sorted(self.label_to_file.keys(), key=osu_parser.taiko_diff_sort_key)
        for i, label in enumerate(ordered_labels):
            if self.light:
                rb = LightRadiobutton(self.inner, label, self.var, label)
            else:
                rb = ttk.Radiobutton(self.inner, text=label, value=label, variable=self.var)
            row = i % self.MAX_ROWS_PER_COLUMN
            col = i // self.MAX_ROWS_PER_COLUMN
            rb.grid(row=row, column=col, sticky="w", padx=(0, 16), pady=1)

        preselect_label = None
        if preselect_file:
            for label, fname in self.label_to_file.items():
                if fname == preselect_file:
                    preselect_label = label
                    break
        if preselect_label is None and ordered_labels:
            preselect_label = ordered_labels[0]
        self.var.set(preselect_label or "")
        if self.light:
            self._card.redraw()

    def selected(self):
        return self.label_to_file.get(self.var.get())


class BaseToolFrame(ttk.Frame):
    """Common scaffolding: `self.body` (where subclasses parent their
    widgets) plus hooks that refresh diff lists when shown. `body` sits in
    a vertically-scrolling canvas — a tool with enough options (or a large
    enough font size, see Settings #3) that its content no longer fits the
    window stays fully reachable via scrollbar/mouse wheel instead of
    clipping whatever falls below the bottom edge. The scrollbar only
    appears once content actually overflows (see _update_scroll_state), so
    a tool that already fits looks exactly as it did before."""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app

        bg = ttk.Style().lookup("TFrame", "background") or None
        canvas = tk.Canvas(self, highlightthickness=0, bg=bg)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        self._scroll_canvas = canvas
        self._scrollbar = scrollbar

        self.body = ttk.Frame(canvas)
        self._body_window = canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", lambda _e: self._update_scroll_state())
        canvas.bind("<Configure>", self._on_scroll_canvas_configure)

        def _on_wheel(event):
            # Only the tool actually on top should react — every
            # BaseToolFrame instance stays alive (place()'d, never
            # destroyed) behind whichever one is currently shown, so this
            # guards against a hidden tool's own binding stealing the
            # scroll.
            if self.app.frames.get(getattr(self.app, "_current_frame_name", None)) is not self:
                return
            # Defer to a widget that already owns wheel scrolling itself
            # (e.g. Pattern Gallery's horizontally-scrolling card row) —
            # only its own instance-level binding counts here, not this
            # bind_all fallback, so this check can't see itself.
            if event.widget.bind("<MouseWheel>"):
                return
            # Nothing to scroll if this tool's content already fits —
            # recomputed fresh rather than trusting the last Configure-
            # triggered state, since a width change (from resizing the
            # window) can reflow wrapped labels and change body's actual
            # height before its own <Configure> has a chance to catch up.
            # Without this, a tool that fits could still creep a few
            # pixels on every wheel tick even with the scrollbar hidden.
            self._update_scroll_state()
            if not self._scrollbar.winfo_ismapped():
                return
            if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
                canvas.yview_scroll(3, "units")

        def _bind_wheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_wheel)
            canvas.bind_all("<Button-4>", _on_wheel)
            canvas.bind_all("<Button-5>", _on_wheel)

        def _unbind_wheel(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        self.bind("<Enter>", _bind_wheel)
        self.bind("<Leave>", _unbind_wheel)

    def _on_scroll_canvas_configure(self, event):
        # Stretch body to the canvas's own width so content only ever
        # needs to scroll vertically, never horizontally.
        self._scroll_canvas.itemconfig(self._body_window, width=event.width)
        self._update_scroll_state()

    def _update_scroll_state(self):
        canvas = self._scroll_canvas
        canvas_h = canvas.winfo_height()
        # body is stretched to at least the canvas's own visible height
        # (not just its own natural content height) so a tool with sparse
        # content and its own side="bottom"-anchored widgets (e.g.
        # FrontPage's footer) still sits at the true bottom of the visible
        # area — matching how it looked before body lived inside a
        # scrolling canvas. Content genuinely taller than the canvas is
        # unaffected, since max() just leaves it at its own larger height.
        target_h = max(self.body.winfo_reqheight(), canvas_h)
        canvas.itemconfig(self._body_window, height=target_h)
        bbox = canvas.bbox("all")
        canvas.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))
        content_h = bbox[3] if bbox else 0
        if content_h > canvas_h:
            if not self._scrollbar.winfo_ismapped():
                self._scrollbar.pack(side="right", fill="y")
        elif self._scrollbar.winfo_ismapped():
            self._scrollbar.pack_forget()
            canvas.yview_moveto(0)

    def on_shown(self):
        pass

    def on_map_changed(self):
        pass

    def require_map(self) -> bool:
        folder, diffs = self.app.get_diff_files()
        if not folder:
            _show_alert(self, "No map selected", "Please select a map first!")
            return False
        if not diffs:
            _show_alert(self, "No difficulties found", f"No .osu files found in:\n{folder}")
            return False
        return True

    def notify_done(self, message: str):
        """Shows the standard completion banner. Every save already
        renames the file away and back (see osu_parser.touch_reload) so
        osu!'s file watcher picks it up as a change — a simple F5 at song
        select is enough to see it, no extra reminder needed here."""
        show_toast(self, message)


# =============================================================================
class FrontPage(BaseToolFrame):
    """Splash screen shown once at launch (see App.__init__ ->
    show_frame("front")). Not in SIDEBAR_ITEMS/sidebar_buttons, so it has no
    nav button and no highlight state — the only way to see it again is to
    restart the app."""

    CREDIT_URL = ("https://www.deviantart.com/shingaishima/art/"
                  "Taiko-no-Tatsujin-Don-and-Katsu-618160106")

    def __init__(self, master, app):
        super().__init__(master, app)

        # This screen's canvas/body normally inherit the default ttk frame
        # background (see BaseToolFrame) — overridden here, and only here,
        # to the FrontPage-specific light card palette (FRONT_* above).
        self._scroll_canvas.configure(bg=FRONT_BG)
        ttk.Style().configure("FrontBody.TFrame", background=FRONT_BG)
        self.body.configure(style="FrontBody.TFrame")

        footer = tk.Frame(self.body, bg=FRONT_BG)
        footer.pack(side="bottom", pady=(0, 24))
        card = tk.Frame(footer, bg=FRONT_CARD_BG, highlightthickness=1,
                         highlightbackground=FRONT_BORDER, highlightcolor=FRONT_BORDER)
        card.pack()
        credit_row = tk.Frame(card, bg=FRONT_CARD_BG)
        credit_row.pack(padx=28, pady=(16, 2))
        tk.Label(credit_row, text="From Amasugi! ❤ ", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        credit_link = tk.Label(credit_row, text="App Icon", font=("Segoe UI", 10, "underline"),
                                bg=FRONT_CARD_BG, fg="#4f46e5", cursor="hand2")
        credit_link.pack(side="left")
        credit_link.bind("<Button-1>", lambda e: webbrowser.open(self.CREDIT_URL))
        tk.Label(card, text=f"App Version: {getattr(app, 'app_version', '?')}",
                 bg=FRONT_CARD_BG, fg=FRONT_TEXT_MUTED, font=("Segoe UI", 9)).pack(pady=(0, 16))

        content = tk.Frame(self.body, bg=FRONT_BG)
        content.pack(expand=True, pady=(40, 0))

        logo = self._load_logo_image()
        if logo is not None:
            self._logo_image = logo  # kept alive on self, not just this scope
            tk.Label(content, image=logo, bg=FRONT_BG).pack(pady=(0, 24))

        title_image = _render_gradient_text("osu!taiko Mapping Tools", font_size=30)
        if title_image is not None:
            self._title_image = title_image  # kept alive on self
            tk.Label(content, image=title_image, bg=FRONT_BG).pack()
        else:
            tk.Label(content, text="osu!taiko Mapping Tools", bg=FRONT_BG, fg=FRONT_TEXT,
                      font=("Segoe UI", 26, "bold")).pack()

        tk.Label(content, text="Made by osu!taiko mapper, for osu!taiko mappers",
                 bg=FRONT_BG, fg=FRONT_TEXT_MUTED, font=("Segoe UI", 12)).pack(pady=(8, 0))
        tk.Label(content, text="────  ✦  ────",
                 bg=FRONT_BG, fg=FRONT_BORDER, font=("Segoe UI", 10)).pack(pady=(10, 0))

    def _load_logo_image(self):
        """icon.png doubles as the app's window/taskbar icon (see
        App._set_app_icon) and the splash mascot art — same base_dir
        resolution (sys._MEIPASS under PyInstaller, this file's folder from
        source) so it resolves correctly in both a frozen build and a
        source run. Downscaled via Pillow (already a hard dependency) for a
        smoother result than tk.PhotoImage's integer-only subsample/zoom."""
        try:
            from PIL import Image, ImageTk
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            icon_path = os.path.join(base_dir, "icon.png")
            img = Image.open(icon_path).convert("RGBA")
            img.thumbnail((220, 220), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None


# =============================================================================
class MetadataManagerFrame(BaseToolFrame):
    # osu!'s own Tags field has no hard server-side cap this app is aware
    # of — 1000 is a generous, deliberately round local limit so the field
    # can't grow unboundedly, not a value mirrored from the game itself.
    TAGS_MAX_CHARS = 1000

    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="Metadata Manager", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        info_icon = InfoIcon(header_row,
                              "Import metadata from selected mapset, and apply changed "
                              "metadata to all difficulties in the set (or a few of "
                              "them, if you're into it)")
        info_icon.configure(bg=FRONT_BG)
        info_icon.pack(side="left", padx=(6, 0))

        row0 = tk.Frame(self.body, bg=FRONT_BG)
        row0.pack(anchor="w", padx=24, pady=(4, 16))
        _make_ghost_button(row0, "Import Meta", self.import_meta).pack(side="left")
        self.autofill_var = tk.BooleanVar(value=self.app.metadata_autofill)
        LightCheckbox(row0, "Auto-fill", self.autofill_var, bg=FRONT_BG,
                      command=self._on_autofill_changed).pack(side="left", padx=(12, 0))

        form = tk.Frame(self.body, bg=FRONT_BG)
        form.pack(fill="x", padx=24)
        self.fields = {}
        labels = [
            ("Artist", "Artist"),
            ("Romanised Artist", "RomanisedArtist"),
            ("Title", "Title"),
            ("Romanised Title", "RomanisedTitle"),
            ("Source", "Source"),
            ("Mapper", "Mapper"),
        ]
        for i, (label, key) in enumerate(labels):
            tk.Label(form, text=label, bg=FRONT_BG, fg=FRONT_TEXT,
                     font=("Segoe UI", 11)).grid(row=i, column=0, sticky="w", pady=8)
            e = _make_light_entry(form, width=60)
            e.grid(row=i, column=1, sticky="we", pady=8, padx=(12, 0), ipadx=8, ipady=6)
            self.fields[key] = e
        form.columnconfigure(1, weight=1)

        # Tags gets its own multi-line box (room for ~5 lines of text)
        tags_row = len(labels)
        tk.Label(form, text="Tags", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).grid(row=tags_row, column=0, sticky="nw", pady=8)
        tags_frame = tk.Frame(form, bg=FRONT_CARD_BG, highlightthickness=1,
                               highlightbackground=FRONT_BORDER, highlightcolor=LIGHT_ACCENT)
        tags_frame.grid(row=tags_row, column=1, sticky="we", pady=8, padx=(12, 0))
        self.tags_text = tk.Text(tags_frame, width=60, height=5, wrap="word", font=("Segoe UI", 11),
                                  relief="flat", bd=0, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                                  insertbackground=FRONT_TEXT, padx=8, pady=6)
        tags_scroll = ttk.Scrollbar(tags_frame, orient="vertical", command=self.tags_text.yview)
        self.tags_text.configure(yscrollcommand=tags_scroll.set)
        self.tags_text.pack(side="left", fill="both", expand=True)
        tags_scroll.pack(side="right", fill="y")
        self.tags_text.bind("<<Modified>>", self._on_tags_modified)

        self.tags_count_var = tk.StringVar(value=f"0 / {self.TAGS_MAX_CHARS}")
        tk.Label(form, textvariable=self.tags_count_var, bg=FRONT_BG, fg=FRONT_TEXT_MUTED,
                 font=("Segoe UI", 9)).grid(row=tags_row + 1, column=1, sticky="e", pady=(2, 8))

        # Preview Point shares the same grid/column as Artist/Title/etc.
        # (rather than its own separately-packed row) so its box's left
        # edge lines up with every other field's — a pack-based row here
        # sized itself off "Preview Point" 's own label width instead of
        # the grid's shared column-0 width, which visibly misaligned it
        # against the rest once every field got a distinct rounded box.
        preview_row = tags_row + 2
        tk.Label(form, text="Preview Point", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).grid(row=preview_row, column=0, sticky="w", pady=(4, 8))
        self.preview_point = _make_light_entry(form, width=15)
        self.preview_point.grid(row=preview_row, column=1, sticky="w", pady=(4, 8), padx=(12, 0))

        self.diff_list = DiffCheckList(self.body, app, light=True)
        self.diff_list.pack(fill="both", expand=True, padx=24, pady=(16, 8))

        apply_row = tk.Frame(self.body, bg=FRONT_BG)
        apply_row.pack(fill="x", padx=24, pady=(4, 20))
        _make_accent_button(apply_row, "Apply", self.apply).pack(side="right")

    def _on_tags_modified(self, _event=None):
        """Live character counter + hard cap for the Tags box — Text
        widgets have no built-in maxlength/validate option the way Entry
        does, so this reacts to <<Modified>> instead: over the cap, the
        overflow is trimmed back off immediately. edit_modified(False) at
        the end resets the flag so the next real edit fires this again
        (Tk only re-fires <<Modified>> on a False->True transition, so the
        delete/insert used to trim doesn't recursively re-trigger this
        while it's already running)."""
        if not self.tags_text.edit_modified():
            return
        text = self.tags_text.get("1.0", "end-1c")
        if len(text) > self.TAGS_MAX_CHARS:
            text = text[: self.TAGS_MAX_CHARS]
            self.tags_text.delete("1.0", "end")
            self.tags_text.insert("1.0", text)
        self.tags_count_var.set(f"{len(text)} / {self.TAGS_MAX_CHARS}")
        self.tags_text.edit_modified(False)

    def on_shown(self):
        self.diff_list.refresh()
        self._auto_import()

    def on_map_changed(self):
        self.diff_list.refresh()
        self._auto_import()

    def _on_autofill_changed(self):
        self.app.save_metadata_autofill(self.autofill_var.get())

    def _auto_import(self):
        """Silently fills the fields from whatever map is currently
        loaded — called whenever this tool is shown or the loaded map
        changes (picked up from osu!, browsed to manually, or found via
        search), so the fields are never stale/empty without the user
        having to remember to click Import Meta. Does nothing (no warning
        popup) if no map is loaded yet, or if the "Auto-fill" checkbox is
        unticked — Import Meta still works manually either way."""
        if not self.autofill_var.get():
            return
        folder, diffs = self.app.get_diff_files()
        if not folder or not diffs:
            return
        self._fill_fields(logic.import_metadata_from(folder, diffs[0]))

    def import_meta(self):
        if not self.require_map():
            return
        folder, diffs = self.app.get_diff_files()
        self._fill_fields(logic.import_metadata_from(folder, diffs[0]))

    def _fill_fields(self, meta):
        for key, entry in self.fields.items():
            entry.delete(0, "end")
            entry.insert(0, meta.get(key, ""))
        self.tags_text.delete("1.0", "end")
        self.tags_text.insert("1.0", meta.get("Tags", ""))
        self.preview_point.delete(0, "end")
        self.preview_point.insert(0, meta.get("PreviewTime", ""))

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        if not targets:
            _show_alert(self, "Nothing selected", "Tick at least one difficulty.")
            return
        meta = {k: e.get() for k, e in self.fields.items()}
        # Tags is stored as a single line in the .osu file; collapse any
        # newlines the user typed into the multi-line box down to spaces.
        tags_raw = self.tags_text.get("1.0", "end").strip()
        meta["Tags"] = " ".join(tags_raw.split())
        meta["PreviewTime"] = self.preview_point.get()
        logic.apply_metadata_to_diffs(folder, targets, meta)
        self.notify_done(f"Metadata applied to {len(targets)} difficulty file(s).")


# =============================================================================
class CopySection(ttk.Frame):
    """Reusable 'big header (i) / Copy X from: [dropdown] / Apply to:
    [checklist] / Apply' block — same layout used for both volume and kiai
    copying, each as its own section.

    `light=True` (opt-in, default off so every other caller is unaffected)
    renders the section as a RoundedCard instead of a native
    ttk.Frame(relief="groove") box — same rationale as DiffCheckList's own
    light mode (see its docstring)."""

    def __init__(self, master, owner, title, source_label_text, copy_func, noun,
                 info_text=None, light=False):
        self.light = light
        if light:
            ttk.Frame.__init__(self, master)
            self._card = RoundedCard(self)
            self._card.pack(fill="both", expand=True)
            body = self._card.body
        else:
            super().__init__(master, relief="groove", borderwidth=1)
            body = self
        self.owner = owner  # hosting BaseToolFrame (for require_map/notify_done/app)
        self.copy_func = copy_func
        self.noun = noun

        if light:
            header_row = tk.Frame(body, bg=FRONT_CARD_BG)
            header_row.pack(fill="x", pady=(0, 6))
            tk.Label(header_row, text=title, bg=FRONT_CARD_BG, fg=LIGHT_ACCENT,
                     font=("Segoe UI", 13, "bold")).pack(side="left")
            if info_text:
                info_icon = InfoIcon(header_row, info_text)
                info_icon.configure(bg=FRONT_CARD_BG)
                info_icon.pack(side="left", padx=(6, 0))

            row = tk.Frame(body, bg=FRONT_CARD_BG)
            row.pack(fill="x", pady=(0, 8))
            tk.Label(row, text=source_label_text, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                     font=("Segoe UI", 11)).pack(side="left")
            self.source_var = tk.StringVar()
            self.source_combo = LightDropdown(row, self.source_var, width=25, page_bg=FRONT_CARD_BG)
            self.source_combo.pack(side="left", padx=(10, 0))
            self.diff_map = {}

            tk.Label(body, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 4))
            # nested=True — this section already sits inside its own outer
            # RoundedCard (self._card, above), so the diff list doesn't
            # need its own separate nested card too. See DiffCheckList's
            # own docstring for why that mattered here specifically.
            self.diff_list = DiffCheckList(body, owner.app, light=True, label_inside=False, nested=True)
            self.diff_list.pack(fill="both", expand=True, pady=(0, 8))

            btn_row = tk.Frame(body, bg=FRONT_CARD_BG)
            btn_row.pack(fill="x")
            _make_accent_button(btn_row, "Apply", self.apply).pack(side="right")
        else:
            add_header(self, title, info_text)

            row = ttk.Frame(self)
            row.pack(fill="x", padx=10, pady=(0, 10))
            ttk.Label(row, text=source_label_text).pack(side="left")
            self.source_var = tk.StringVar()
            self.source_combo = ttk.Combobox(row, textvariable=self.source_var, state="readonly", width=25)
            self.source_combo.pack(side="left", padx=5)
            self.diff_map = {}

            self.diff_list = DiffCheckList(self, owner.app)
            self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

            ttk.Button(self, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

    def refresh(self):
        folder, diffs = self.owner.app.get_diff_files()
        self.diff_map = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        labels = sorted(self.diff_map.keys(), key=osu_parser.taiko_diff_sort_key)
        if self.light:
            self.source_combo.set_values(labels)
        else:
            self.source_combo["values"] = labels
        if labels and self.source_var.get() not in labels:
            self.source_var.set(labels[0])
        self.diff_list.refresh()
        if self.light:
            # The outer card's own body just resized (the diff list is no
            # longer its own separate card — see the nested=True note
            # above — so there's nothing further to redraw() for it here).
            self._card.redraw()

    def apply(self):
        if not self.owner.require_map():
            return
        folder, _ = self.owner.app.get_diff_files()
        source_label = self.source_var.get()
        source = self.diff_map.get(source_label)
        targets = self.diff_list.selected()
        if not source:
            _show_alert(self, "No source", f"Choose a difficulty to copy {self.noun.lower()} from.")
            return
        if not targets:
            _show_alert(self, "Nothing selected", "Tick at least one difficulty.")
            return
        self.copy_func(folder, source, targets)
        self.owner.notify_done(f"{self.noun} copied from {source_label} to {len(targets)} difficulty file(s).")


# =============================================================================
class VolumeKiaiCopierFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        self.volume_section = CopySection(
            self.body, self, "Volume Copier", "Copy volume from:", logic.copy_volumes, "Volume",
            info_text="Copy the volume changes of a difficulty and apply them "
                       "to any difficulties in the set.",
            light=True)
        self.volume_section.pack(fill="both", expand=True, padx=24, pady=(10, 4))
        self.kiai_section = CopySection(
            self.body, self, "Kiai Copier", "Copy kiai from:", logic.copy_kiai, "Kiai",
            info_text="Copy the kiai portions of a difficulty and apply them "
                       "to any difficulties in the set.",
            light=True)
        self.kiai_section.pack(fill="both", expand=True, padx=24, pady=(4, 10))

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def refresh(self):
        self.volume_section.refresh()
        self.kiai_section.refresh()


# =============================================================================
class MapCleanerFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="Map Cleaner", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row,
                                "Make the map looks prettier and snappier in the editor "
                                "without fundamentally changing how the map is played.")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        diff_row = tk.Frame(self.body, bg=FRONT_BG)
        diff_row.pack(fill="x", padx=24, pady=(4, 16))
        tk.Label(diff_row, text="Selected diff:", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.diff_var = tk.StringVar()
        self.diff_combo = LightDropdown(diff_row, self.diff_var, width=25)
        self.diff_combo.pack(side="left", padx=(10, 0))
        self.diff_map = {}

        self.resnap_notes_var = tk.BooleanVar(value=False)
        self.snap_divisor_var = tk.StringVar(value="1/4")
        self.resnap_section_only_var = tk.BooleanVar(value=False)
        self.resnap_from_var = tk.StringVar()
        self.resnap_to_var = tk.StringVar()
        self.remove_unused_green_var = tk.BooleanVar(value=False)
        self.resnap_important_green_var = tk.BooleanVar(value=False)
        self.kat_var = tk.BooleanVar(value=False)
        self.sampleset_var = tk.BooleanVar(value=False)
        self.conflicts_var = tk.BooleanVar(value=False)
        self.center_notes_var = tk.BooleanVar(value=False)

        self.set_base_sv_var = tk.BooleanVar(value=False)
        self.base_sv_other_var = tk.BooleanVar(value=False)
        self.base_sv_val_var = tk.StringVar(value="1.4")
        self.push_green_var = tk.BooleanVar(value=False)
        self.push_green_ms_var = tk.StringVar(value="5")

        vcmd_int = (self.register(self._validate_int), "%P")
        vcmd_float = (self.register(self._validate_float), "%P")
        vcmd_time = (self.register(_validate_partial_time), "%P")

        card = RoundedCard(self.body)
        card.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        opts = card.body

        def info(parent, text, **kw):
            icon = InfoIcon(parent, text, **kw)
            icon.configure(bg=FRONT_CARD_BG)
            return icon

        def row(indent=0, pady=6):
            r = tk.Frame(opts, bg=FRONT_CARD_BG)
            r.pack(fill="x", anchor="w", padx=(indent, 0), pady=pady)
            return r

        def label(parent, text, **kw):
            defaults = dict(bg=FRONT_CARD_BG, fg=FRONT_TEXT, font=("Segoe UI", 11))
            defaults.update(kw)
            return tk.Label(parent, text=text, **defaults)

        r1 = row()
        LightCheckbox(r1, "Resnap all notes", self.resnap_notes_var,
                      command=self._sync_resnap_notes_state).pack(side="left")
        self.divisor_combo = LightDropdown(r1, self.snap_divisor_var, values=DIVISORS,
                                            width=8, page_bg=FRONT_CARD_BG)
        self.divisor_combo.pack(side="left", padx=8)
        info(r1, "- 1/12 = 1/4 + 1/6\n- 1/24 = 1/8 + 1/12\n- 1/36 = 1/4 + 1/6 + 1/9\n"
                 "- 1/48 = 1/12 + 1/16").pack(side="left")

        # Resnap Child Option: Apply to this section only
        r1_section = row(indent=24)
        self.resnap_section_only_cb = LightCheckbox(
            r1_section, "Apply to this section only", self.resnap_section_only_var,
            command=self._sync_resnap_section_state)
        self.resnap_section_only_cb.pack(side="left")
        self.resnap_section_only_cb.set_enabled(False)

        r1_section_fields = row(indent=24)
        self.resnap_from_label = label(r1_section_fields, "From", fg=FRONT_TEXT_MUTED)
        self.resnap_from_label.pack(side="left")
        self.resnap_from_entry = _make_light_entry(
            r1_section_fields, textvariable=self.resnap_from_var, width=15,
            validate="key", validatecommand=vcmd_time, state="disabled")
        self.resnap_from_entry.pack(side="left", padx=5, ipady=4)
        self.resnap_from_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.resnap_from_var))
        self.resnap_to_label = label(r1_section_fields, "to", fg=FRONT_TEXT_MUTED)
        self.resnap_to_label.pack(side="left")
        self.resnap_to_entry = _make_light_entry(
            r1_section_fields, textvariable=self.resnap_to_var, width=15,
            validate="key", validatecommand=vcmd_time, state="disabled")
        self.resnap_to_entry.pack(side="left", padx=5, ipady=4)
        self.resnap_to_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.resnap_to_var))

        r2 = row()
        LightCheckbox(r2, "Remove unused green lines", self.remove_unused_green_var).pack(side="left")
        info(r2, "Remove all redundant lines that have no meaningful "
                 "effect on the map.").pack(side="left", padx=(6, 0))

        r3 = row()
        LightCheckbox(r3, "Snap Kiai Toggles", self.resnap_important_green_var).pack(side="left")
        info(r3, "Resolve the occasional kiai unsnaps.").pack(side="left", padx=(6, 0))

        r4 = row()
        LightCheckbox(r4, "Turn all Kat's whistle to clap", self.kat_var).pack(side="left")
        info(r4, "Remove all whistle hitsounds and replace them with claps.").pack(side="left", padx=(6, 0))

        r5 = row()
        LightCheckbox(r5, "Set all green/red lines to Normal Sampleset", self.sampleset_var).pack(side="left")
        info(r5, "Make sure the entire map is in Normal Sampleset so it "
                 "won't play funny hitsounds for some skins. Be careful "
                 "when using custom hitsounds.").pack(side="left", padx=(6, 0))

        r6 = row()
        LightCheckbox(r6, "Resolve red/green line conflicts", self.conflicts_var).pack(side="left")
        info(r6, "Resolve the mismatched kiai and volume setting between "
                 "green line and red line on the same timestamp. Green "
                 "line takes priority over red line.").pack(side="left", padx=(6, 0))

        # Base SV Option
        r_base_sv = row()
        self.set_base_sv_cb = LightCheckbox(r_base_sv, "Set base SV setting to 1.4", self.set_base_sv_var,
                                             command=self._sync_base_sv_state)
        self.set_base_sv_cb.pack(side="left")
        info(r_base_sv, "Use this tool if your map's base SV got rounding error.").pack(side="left", padx=(6, 10))

        # Base SV Child Option: Other
        r_base_sv_other = row(indent=24)
        self.base_sv_other_cb = LightCheckbox(r_base_sv_other, "Other", self.base_sv_other_var,
                                               command=self._sync_base_sv_state)
        self.base_sv_other_cb.pack(side="left", padx=(0, 5))
        self.base_sv_other_cb.set_enabled(False)

        self.base_sv_spinbox = LightSpinner(
            r_base_sv_other, self.base_sv_val_var, from_=0.4, to=3.6, increment=0.1,
            width=5, fmt="%.1f", validate=("key", vcmd_float))
        self.base_sv_spinbox.pack(side="left")
        self.base_sv_spinbox.set_enabled(False)
        self.base_sv_spinbox.entry.bind("<FocusOut>", self._on_base_sv_focus_out, add="+")

        # Push Green Option
        r_push_green = row()
        self.push_green_cb = LightCheckbox(r_push_green, "Push all green lines by ", self.push_green_var,
                                            command=self._sync_push_green_state)
        self.push_green_cb.pack(side="left")
        self.push_green_spinbox = LightSpinner(
            r_push_green, self.push_green_ms_var, from_=5, to=20, increment=1,
            width=4, fmt="%d", validate=("key", vcmd_int))
        self.push_green_spinbox.pack(side="left", padx=2)
        self.push_green_spinbox.set_enabled(False)
        self.push_green_spinbox.entry.bind("<FocusOut>", self._on_push_green_focus_out, add="+")
        label(r_push_green, " ms").pack(side="left")
        info(r_push_green, "This does not affect kiai toggles and red-line-supported green lines."
             ).pack(side="left", padx=(5, 0))

        r7 = row(pady=(14, 6))
        LightCheckbox(r7, "Reposition all notes in playfield", self.center_notes_var,
                      command=self._sync_center_children_state).pack(side="left")

        self.note_position_mode_var = tk.StringVar(value="default")

        r_center = row(indent=24, pady=3)
        self.center_radio = LightRadiobutton(
            r_center, "All notes in center", self.note_position_mode_var, "default",
            command=self._sync_coord_buttons_state)
        self.center_radio.pack(side="left")
        self.center_radio.set_enabled(False)
        info(r_center, "Move all the notes scattered around the screen to the "
                       "clean middle of the screen.").pack(side="left", padx=(2, 0))

        r8 = row(indent=24, pady=3)
        self.separate_finishers_radio = LightRadiobutton(
            r8, "Separate finishers", self.note_position_mode_var, "separate_finishers",
            command=self._sync_coord_buttons_state)
        self.separate_finishers_radio.pack(side="left")
        self.separate_finishers_radio.set_enabled(False)
        info(r8, "Finisher notes will be placed at their own position "
                 "for easier differentiation.").pack(side="left", padx=(2, 6))
        self.finisher_coord_btn = _make_ghost_button(r8, "Change Coordinate", self._open_finisher_coord_editor)
        self.finisher_coord_btn.configure(state="disabled")
        self.finisher_coord_btn.pack(side="left")

        r9 = row(indent=24, pady=3)
        self.separate_note_types_radio = LightRadiobutton(
            r9, "Separate note types", self.note_position_mode_var, "separate_note_types",
            command=self._sync_coord_buttons_state)
        self.separate_note_types_radio.pack(side="left")
        self.separate_note_types_radio.set_enabled(False)
        info(r9, "Don, Kat, Don Finisher, and Kat Finisher notes will "
                 "each be placed at their own position for easier "
                 "differentiation.").pack(side="left", padx=(2, 6))
        self.note_type_coord_btn = _make_ghost_button(r9, "Change Coordinate", self._open_note_type_coord_editor)
        self.note_type_coord_btn.configure(state="disabled")
        self.note_type_coord_btn.pack(side="left")

        apply_row = tk.Frame(self.body, bg=FRONT_BG)
        apply_row.pack(fill="x", padx=24, pady=(0, 20))
        _make_accent_button(apply_row, "Apply", self.apply).pack(side="right")

    def _sync_resnap_notes_state(self):
        self.resnap_section_only_cb.set_enabled(self.resnap_notes_var.get())
        self._sync_resnap_section_state()

    def _sync_resnap_section_state(self):
        enabled = self.resnap_notes_var.get() and self.resnap_section_only_var.get()
        state = "normal" if enabled else "disabled"
        self.resnap_from_entry.configure(state=state)
        self.resnap_to_entry.configure(state=state)
        fg = FRONT_TEXT if enabled else FRONT_TEXT_MUTED
        self.resnap_from_label.configure(fg=fg)
        self.resnap_to_label.configure(fg=fg)

    def _sync_center_children_state(self):
        enabled = self.center_notes_var.get()
        self.center_radio.set_enabled(enabled)
        self.separate_finishers_radio.set_enabled(enabled)
        self.separate_note_types_radio.set_enabled(enabled)
        self._sync_coord_buttons_state()

    def _sync_coord_buttons_state(self):
        enabled = self.center_notes_var.get()
        mode = self.note_position_mode_var.get()
        self.finisher_coord_btn.configure(
            state="normal" if (enabled and mode == "separate_finishers") else "disabled")
        self.note_type_coord_btn.configure(
            state="normal" if (enabled and mode == "separate_note_types") else "disabled")

    def _sync_base_sv_state(self):
        if self.set_base_sv_var.get():
            self.base_sv_other_cb.set_enabled(True)
            self.base_sv_spinbox.set_enabled(self.base_sv_other_var.get())
        else:
            self.base_sv_other_cb.set_enabled(False)
            self.base_sv_spinbox.set_enabled(False)

    def _sync_push_green_state(self):
        self.push_green_spinbox.set_enabled(self.push_green_var.get())

    def _validate_int(self, P):
        if P == "":
            return True
        return P.isdigit()

    def _validate_float(self, P):
        if P == "":
            return True
        if P.count('.') > 1:
            return False
        return all(c.isdigit() or c == '.' for c in P)

    def _on_base_sv_focus_out(self, _event=None):
        try:
            val = float(self.base_sv_val_var.get())
        except ValueError:
            val = 1.4
        val = max(0.4, min(3.6, val))
        self.base_sv_val_var.set(f"{val:.1f}")

    def _on_push_green_focus_out(self, _event=None):
        try:
            val = int(float(self.push_green_ms_var.get()))
        except ValueError:
            val = 5
        val = max(5, min(20, val))
        self.push_green_ms_var.set(str(val))

    def _open_finisher_coord_editor(self):
        existing = getattr(self, "_finisher_coord_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        points_spec = [
            {"key": "finisher", "label": "Finisher", "color": "red", "big": True},
            {"key": "normal", "label": "Normal", "color": "red", "big": False},
        ]
        self._finisher_coord_win = CoordinateEditorWindow(
            self, "Change Coordinate — Separate Finishers", points_spec,
            self.app.finisher_coords, self.app.update_finisher_coords)

    def _open_note_type_coord_editor(self):
        existing = getattr(self, "_note_type_coord_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        points_spec = [
            {"key": "don", "label": "Don", "color": "red", "big": False},
            {"key": "kat", "label": "Kat", "color": "#00AEEF", "big": False},
            {"key": "don_finisher", "label": "Don Finisher", "color": "red", "big": True},
            {"key": "kat_finisher", "label": "Kat Finisher", "color": "#00AEEF", "big": True},
        ]
        self._note_type_coord_win = CoordinateEditorWindow(
            self, "Change Coordinate — Separate Note Types", points_spec,
            self.app.note_type_coords, self.app.update_note_type_coords)

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh(sync_to_current=True)

    def refresh(self, sync_to_current=False):
        folder, diffs = self.app.get_diff_files()
        self.diff_map = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        labels = sorted(self.diff_map.keys(), key=osu_parser.taiko_diff_sort_key)
        self.diff_combo.set_values(labels)

        target_label = None
        if sync_to_current:
            current_fname = getattr(self.app, "current_diff_filename", None)
            if current_fname:
                for label, fname in self.diff_map.items():
                    if fname == current_fname:
                        target_label = label
                        break

        if target_label:
            self.diff_var.set(target_label)
        elif labels and self.diff_var.get() not in labels:
            self.diff_var.set(labels[0])

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        fname = self.diff_map.get(self.diff_var.get())
        if not fname:
            _show_alert(self, "No difficulty", "Choose a difficulty to clean.")
            return

        # Validate base SV
        base_sv_val = 1.4
        if self.set_base_sv_var.get():
            if self.base_sv_other_var.get():
                try:
                    val = float(self.base_sv_val_var.get())
                    val = round(val, 1)
                    if not (0.4 <= val <= 3.6):
                        raise ValueError()
                    base_sv_val = val
                except ValueError:
                    _show_alert(self, "Invalid value", "Base SV must be a number between 0.4 and 3.6.")
                    return
            else:
                base_sv_val = 1.4

        # Validate push green ms
        push_green_ms = 5
        if self.push_green_var.get():
            try:
                val = int(float(self.push_green_ms_var.get()))
                if not (5 <= val <= 20):
                    raise ValueError()
                push_green_ms = val
            except ValueError:
                _show_alert(self, "Invalid value", "Push milliseconds must be an integer between 5 and 20.")
                return

        # Validate resnap section range
        resnap_section = None
        if self.resnap_notes_var.get() and self.resnap_section_only_var.get():
            from_result = logic.parse_time_input(self.resnap_from_var.get())
            to_result = logic.parse_time_input(self.resnap_to_var.get())
            if from_result is None or to_result is None:
                _show_alert(self, "Warning", "Invalid timestamp input")
                return
            from_ms, from_clean = from_result
            to_ms, to_clean = to_result
            self.resnap_from_var.set(from_clean)
            self.resnap_to_var.set(to_clean)
            resnap_section = (min(from_ms, to_ms), max(from_ms, to_ms))

        options = {
            "resnap_notes": self.resnap_notes_var.get(),
            "snap_divisor": self.snap_divisor_var.get(),
            "resnap_notes_section": resnap_section,
            "remove_unused_green": self.remove_unused_green_var.get(),
            "resnap_important_green": self.resnap_important_green_var.get(),
            "kat_whistle_to_clap": self.kat_var.get(),
            "normal_sampleset": self.sampleset_var.get(),
            "resolve_conflicts": self.conflicts_var.get(),
            "center_notes": self.center_notes_var.get(),
            "note_position_mode": self.note_position_mode_var.get(),
            "finisher_coords": self.app.finisher_coords,
            "note_type_coords": self.app.note_type_coords,
            "set_base_sv_val": base_sv_val if self.set_base_sv_var.get() else None,
            "push_green_ms": push_green_ms if self.push_green_var.get() else None,
        }
        bm = osu_parser.Beatmap(os.path.join(folder, fname))
        logic.run_map_cleaner(bm, options)
        bm.save()
        self.notify_done(f"Map cleaner applied to {fname}.")


# =============================================================================
class OffsetShifterFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        def info(parent, text, **kw):
            icon = InfoIcon(parent, text, **kw)
            icon.configure(bg=FRONT_CARD_BG)
            return icon

        # ---- Section 1: Audio/Offset Settings ------------------------------
        self.card1 = RoundedCard(self.body)
        self.card1.pack(fill="both", expand=True, padx=24, pady=(20, 12))
        body1 = self.card1.body

        header1 = tk.Frame(body1, bg=FRONT_CARD_BG)
        header1.pack(fill="x", pady=(0, 14))
        tk.Label(header1, text="Audio/Offset Settings", bg=FRONT_CARD_BG, fg=LIGHT_ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        info(header1, "Change offset and resnap all notes and preview point "
                       "for all difficulties.").pack(side="left", padx=(6, 0))

        form = tk.Frame(body1, bg=FRONT_CARD_BG)
        form.pack(fill="x", pady=(0, 12))
        tk.Label(form, text="Current offset:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w", pady=6)
        self.current_entry = _make_light_entry(form, width=15, state="readonly")
        self.current_entry.grid(row=0, column=1, sticky="w", pady=6, padx=(10, 0), ipady=4, ipadx=6)

        tk.Label(form, text="New offset:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w", pady=6)
        self.new_var = tk.StringVar()
        self.new_entry = LightSpinner(form, self.new_var, from_=-10_000_000, to=10_000_000,
                                       increment=1, width=13)
        self.new_entry.grid(row=1, column=1, sticky="w", pady=6, padx=(10, 0))
        self.new_var.trace_add("write", self._on_new_changed)

        tk.Label(form, text="Change:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).grid(row=2, column=0, sticky="w", pady=6)
        self.change_var = tk.StringVar(value="0")
        self.change_entry = LightSpinner(form, self.change_var, from_=-10_000_000, to=10_000_000,
                                          increment=1, width=13)
        self.change_entry.grid(row=2, column=1, sticky="w", pady=6, padx=(10, 0))
        self.change_var.trace_add("write", self._on_change_changed)

        self._updating = False
        self.base_offset = 0

        self.add_silence_var = tk.BooleanVar(value=False)
        r_silence = tk.Frame(body1, bg=FRONT_CARD_BG)
        r_silence.pack(fill="x", anchor="w", pady=(0, 12))
        LightCheckbox(r_silence, "Add silence", self.add_silence_var).pack(side="left")
        info(r_silence, "Add 1000ms of silence to the beginning of the "
                         "song to avoid first-note lag.").pack(side="left", padx=(6, 0))

        tk.Label(body1, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.diff_list = DiffCheckList(body1, app, light=True, label_inside=False)
        self.diff_list.pack(fill="both", expand=True, pady=(0, 16))

        btn_row1 = tk.Frame(body1, bg=FRONT_CARD_BG)
        btn_row1.pack(fill="x")
        _make_accent_button(btn_row1, "Apply", self.apply).pack(side="right")

        # ---- Section 2: Audio Re-encode ------------------------------------
        card2 = RoundedCard(self.body)
        card2.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        body2 = card2.body

        header2 = tk.Frame(body2, bg=FRONT_CARD_BG)
        header2.pack(fill="x", pady=(0, 14))
        tk.Label(header2, text="Audio Re-encode", bg=FRONT_CARD_BG, fg=LIGHT_ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        info(header2, "Re-encode audio down to a lower bitrate to reduce file size."
             ).pack(side="left", padx=(6, 0))

        self.reencode_source_var = tk.StringVar(value="map")
        r_src_map = tk.Frame(body2, bg=FRONT_CARD_BG)
        r_src_map.pack(fill="x", anchor="w", pady=(0, 6))
        LightRadiobutton(r_src_map, "Use audio from the currently selected song",
                          self.reencode_source_var, "map",
                          command=self._sync_reencode_source_state).pack(side="left")

        r_src_other = tk.Frame(body2, bg=FRONT_CARD_BG)
        r_src_other.pack(fill="x", anchor="w", pady=(0, 12))
        LightRadiobutton(r_src_other, "Use other audio file", self.reencode_source_var, "other",
                          command=self._sync_reencode_source_state).pack(side="left")
        self.reencode_browse_btn = _make_ghost_button(r_src_other, "Browse...", self._browse_reencode_audio)
        self.reencode_browse_btn.pack(side="left", padx=(10, 8))
        self.reencode_other_path = None
        self.reencode_other_name_var = tk.StringVar(value="")
        tk.Label(r_src_other, textvariable=self.reencode_other_name_var, bg=FRONT_CARD_BG,
                 fg=FRONT_TEXT_MUTED, font=("Segoe UI", 10)).pack(side="left")

        self.reencode_bitrate_var = tk.StringVar(value="192")
        r_bitrate = tk.Frame(body2, bg=FRONT_CARD_BG)
        r_bitrate.pack(fill="x", anchor="w", padx=(24, 0), pady=(0, 16))
        for blabel, value in (("208kbps", "208"), ("192kbps", "192"),
                               ("160kbps", "160"), ("128kbps", "128")):
            LightRadiobutton(r_bitrate, blabel, self.reencode_bitrate_var, value
                              ).pack(side="left", padx=(0, 16))
        self._sync_reencode_source_state()

        btn_row2 = tk.Frame(body2, bg=FRONT_CARD_BG)
        btn_row2.pack(fill="x")
        _make_accent_button(btn_row2, "Apply", self.apply_reencode).pack(side="right")

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def _sync_reencode_source_state(self):
        state = "normal" if self.reencode_source_var.get() == "other" else "disabled"
        self.reencode_browse_btn.configure(state=state)

    def _browse_reencode_audio(self):
        path = filedialog.askopenfilename(
            title="Select audio file",
            filetypes=[
                ("Audio files", "*.mp3 *.ogg *.oga *.wav *.flac *.m4a *.aac "
                                 "*.wma *.opus *.aiff *.aif *.alac *.ac3"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.reencode_other_path = path
        self.reencode_other_name_var.set(os.path.basename(path))

    def refresh(self):
        self.diff_list.refresh()
        # The diff checklist just resized its own nested card to fit
        # however many diffs there are — this outer card wrapping it needs
        # to catch up too, or its rounded-rect boundary stays drawn at the
        # stale (pre-refresh) height while the actual content below it
        # grows/shrinks, producing a visible seam (see CopySection.refresh
        # for the same pattern).
        self.card1.redraw()
        folder, diffs = self.app.get_diff_files()
        if diffs:
            bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
            tp = sorted(bm.timing_points, key=lambda t: t.time)
            self.base_offset = int(tp[0].time) if tp else 0
        else:
            self.base_offset = 0
        self.current_entry.configure(state="normal")
        self.current_entry.delete(0, "end")
        self.current_entry.insert(0, str(self.base_offset))
        self.current_entry.configure(state="readonly")
        self._updating = True
        self.new_var.set(str(self.base_offset))
        self.change_var.set("0")
        self._updating = False

    def _on_new_changed(self, *args):
        if self._updating:
            return
        try:
            new_val = int(float(self.new_var.get()))
        except ValueError:
            return
        self._updating = True
        self.change_var.set(str(new_val - self.base_offset))
        self._updating = False

    def _on_change_changed(self, *args):
        if self._updating:
            return
        try:
            change = int(float(self.change_var.get()))
        except ValueError:
            return
        self._updating = True
        self.new_var.set(str(self.base_offset + change))
        self._updating = False

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        if not targets:
            _show_alert(self, "Nothing selected", "Tick at least one difficulty.")
            return
        try:
            delta = int(float(self.change_var.get()))
        except ValueError:
            _show_alert(self, "Invalid value", "Change must be a number.")
            return
        add_silence = self.add_silence_var.get()
        if delta == 0 and not add_silence:
            _show_alert(self, "No change", "Change is 0 — nothing to apply.")
            return

        install_first = False
        if add_silence and not logic.audio_tools_fully_available():
            choice = _ask_silence_quality_choice(self)
            if choice == "cancel":
                return
            install_first = (choice == "install")

        self._start_apply_thread(folder, targets, delta, add_silence, install_first)

    def _start_apply_thread(self, folder, targets, delta, add_silence, install_first):
        def finish_ok():
            total = delta + (logic.SILENCE_LEAD_IN_MS if add_silence else 0)
            msg = f"Shifted {len(targets)} difficulty file(s) by {total} ms."
            if add_silence:
                msg += " Added 1000ms of silence to the audio."
            self.notify_done(msg)
            self.refresh()

        need_busy = install_first or add_silence
        if not need_busy:
            # Plain metadata-only shift — no ffmpeg install, no audio
            # encode, nothing worth a busy banner (let alone a Cancel
            # button) over.
            def work():
                try:
                    logic.apply_offset(folder, targets, delta, add_silence)
                except Exception as e:
                    # See the matching comment on the cancellable path
                    # below re: capturing `e` as a string before deferring.
                    err_msg = str(e)
                    self.after(0, lambda: _show_alert(self, "Error", err_msg))
                    return
                self.after(0, finish_ok)

            threading.Thread(target=work, daemon=True).start()
            return

        def work(cancel_event):
            if install_first:
                logic.install_ffmpeg_suite_bundled()
                if cancel_event.is_set():
                    return
                self.after(0, lambda: self.notify_done(
                    "ffmpeg + ffprobe installed — continuing with full quality."))
            logic.apply_offset(folder, targets, delta, add_silence)

        busy_msg = ("Installing ffmpeg + ffprobe (may take a few minutes)... Please wait..."
                     if install_first else "Processing... Please wait...")

        def on_error(err_msg):
            # Capture the message as a plain str now — `e` itself is
            # implicitly deleted the moment its except block exits (PEP
            # 3110), but this callback only runs later, once Tkinter gets
            # around to it — run_cancellable_job already does this
            # capture for us before calling on_error.
            _show_alert(self, "Error", err_msg)

        self.app.run_cancellable_job(busy_msg, work, on_success=lambda _r: finish_ok(),
                                      on_error=on_error)

    def apply_reencode(self):
        bitrate = int(self.reencode_bitrate_var.get())
        if self.reencode_source_var.get() == "other":
            src_path = self.reencode_other_path
            if not src_path:
                _show_alert(self, "No file selected", "Pick an audio file to re-encode first.")
                return
            folder, external_path = None, src_path
        else:
            if not self.require_map():
                return
            folder, _ = self.app.get_diff_files()
            external_path = None

        install_first = False
        if not logic.ffmpeg_available():
            choice = _ask_ffmpeg_required_choice(self, "Audio Re-encode")
            if choice == "cancel":
                return
            install_first = True  # only "install" remains as a non-cancel choice

        self._start_reencode_thread(bitrate, folder=folder, external_path=external_path,
                                     install_first=install_first)

    def _start_reencode_thread(self, bitrate, folder=None, external_path=None, install_first=False):
        def do_reencode():
            if external_path:
                return logic.apply_audio_reencode_external(external_path, bitrate)
            return logic.apply_audio_reencode_to_map(folder, bitrate)

        def work(cancel_event):
            if install_first:
                logic.install_ffmpeg_suite_bundled()
                if cancel_event.is_set():
                    return None
                self.after(0, lambda: self.notify_done(
                    "ffmpeg + ffprobe installed — continuing."))
            return do_reencode()

        def on_success(result):
            if external_path:
                self.notify_done(f"Audio re-encoded to {bitrate}kbps.")
                _reveal_in_explorer(result)
            else:
                self.notify_done(f"Audio re-encoded to {bitrate}kbps ({result}).")

        def on_error(err_msg):
            _show_alert(self, "Error", err_msg)

        busy_msg = ("Installing ffmpeg + ffprobe (may take a few minutes)... Please wait..."
                     if install_first else "Re-encoding audio... Please wait...")
        self.app.run_cancellable_job(busy_msg, work, on_success=on_success, on_error=on_error,
                                      cancelled_toast="Re-encode Cancelled!")


# =============================================================================
class BgOffsetShifterFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="BG Settings", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row,
                                "Preview and set BG offset without having to manually "
                                "repeatedly type and guess the desired number.")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        bg_row = tk.Frame(self.body, bg=FRONT_BG)
        bg_row.pack(fill="x", padx=24, pady=(4, 16))
        tk.Label(bg_row, text="BG File:", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.bg_var = tk.StringVar()
        self.bg_combo = LightDropdown(bg_row, self.bg_var, width=30)
        self.bg_combo.pack(side="left", padx=(10, 0))
        self.bg_combo.bind("<<ComboboxSelected>>", self._on_bg_selected)
        _make_ghost_button(bg_row, "Browse...", self.browse_bg).pack(side="left", padx=(10, 0))

        self.card = RoundedCard(self.body)
        self.card.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        body = self.card.body

        row2 = tk.Frame(body, bg=FRONT_CARD_BG)
        row2.pack(fill="x", pady=(0, 12))
        tk.Label(row2, text="New Offset:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.offset_var = tk.StringVar(value="0")
        LightSpinner(row2, self.offset_var, from_=-10_000, to=10_000, increment=1,
                     width=8, fmt="%d").pack(side="left", padx=(10, 10))
        _make_ghost_button(row2, "Preview", self.open_preview).pack(side="left")

        self.convert_jpg_var = tk.BooleanVar(value=False)
        LightCheckbox(body, "Convert to .jpg", self.convert_jpg_var).pack(anchor="w", pady=(0, 12))

        tk.Label(body, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.diff_list = DiffCheckList(body, app, light=True, label_inside=False)
        self.diff_list.pack(fill="both", expand=True, pady=(0, 16))

        btn_row = tk.Frame(body, bg=FRONT_CARD_BG)
        btn_row.pack(fill="x")
        _make_accent_button(btn_row, "Apply", self.apply).pack(side="right")

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def refresh(self):
        folder, diffs = self.app.get_diff_files()
        images = logic.list_song_folder_images(folder) if folder else []
        self.bg_combo.set_values(images)
        current_bg = None
        if folder and diffs:
            bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
            current_bg = bm.get_background_filename()
        if current_bg and current_bg in images:
            self.bg_var.set(current_bg)
        elif images:
            self.bg_var.set(images[0])
        else:
            self.bg_var.set("")
        self.diff_list.refresh()
        # See OffsetShifterFrame.refresh for why this outer card needs an
        # explicit redraw after the nested diff checklist's own height
        # changes — the diff_list's own card only resyncs itself.
        self.card.redraw()
        self._prefill_current_offset()

    def _on_bg_selected(self, _event=None):
        self._prefill_current_offset()

    def browse_bg(self):
        folder, _ = self.app.get_diff_files()
        if not folder:
            _show_alert(self, "No map loaded", "Load a map before browsing for a background image.")
            return
        path = filedialog.askopenfilename(
            title="Select background image",
            filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
        )
        if not path:
            return
        final_name = logic.import_external_bg_image(folder, path)
        self.refresh()
        self.bg_var.set(final_name)
        self._prefill_current_offset()

    def _prefill_current_offset(self):
        """Loads whatever y offset is already saved in the map for the
        selected background, matching the reference tool's behavior of
        opening on the current value rather than always starting at 0."""
        folder, diffs = self.app.get_diff_files()
        if not folder or not diffs:
            return
        bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
        _, y = bm.get_background_offset()
        self.offset_var.set(str(y))

    def open_preview(self):
        existing = getattr(self, "_preview_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        folder, _ = self.app.get_diff_files()
        if not folder or not self.bg_var.get():
            _show_alert(self, "No background", "Pick a background image first.")
            return
        try:
            offset = int(float(self.offset_var.get()))
        except ValueError:
            offset = 0
        self._preview_win = PreviewWindow(self, folder, self.bg_var.get(), offset, self._set_offset)

    def _set_offset(self, value):
        self.offset_var.set(str(value))

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        if not self.bg_var.get():
            _show_alert(self, "No background", "Pick a background image.")
            return
        if not targets:
            _show_alert(self, "Nothing selected", "Tick at least one difficulty.")
            return
        try:
            offset = int(float(self.offset_var.get()))
        except ValueError:
            _show_alert(self, "Invalid value", "Offset must be a number.")
            return
        try:
            final_name = logic.apply_bg_offset(folder, targets, self.bg_var.get(), offset,
                                                self.convert_jpg_var.get())
        except Exception as e:
            _show_alert(self, "Error", str(e))
            return
        self.notify_done(f"Background updated ({final_name}) and applied to {len(targets)} difficulty file(s).")
        self.refresh()


class PreviewWindow(tk.Toplevel):
    """Preview approximating the actual taiko playfield (see
    render_bg_preview): full-width background, cropped vertically, with a
    "y : N" offset readout overlaid on the black playfield bar, matching
    the reference BG-setter-style tool. Drag relatively (click and move —
    each pixel of mouse movement nudges the offset, it doesn't jump to an
    absolute position), scroll the mouse wheel, or use arrow keys; Apply
    writes the offset back into the New Offset field."""

    CANVAS_W = logic.PREVIEW_CANVAS_W
    CANVAS_H = logic.PREVIEW_CANVAS_H

    def __init__(self, parent_frame, folder, bg_file, initial_offset, on_apply):
        super().__init__(parent_frame)
        self.title("BG Offset Preview")
        _position_over_window(self, parent_frame, width=self.CANVAS_W, height=self.CANVAS_H + 56)
        self.resizable(False, False)
        self.folder = folder
        self.bg_file = bg_file
        self.offset = initial_offset
        self.on_apply = on_apply
        self._drag_last_y = None
        # Dragging happens in display pixels, but the stored offset is in
        # osu!'s canonical 854x480 pixel space (what actually gets saved to
        # the .osu file) — convert so 1 canvas pixel of drag feels like 1
        # visual pixel regardless of the preview's display scale.
        self._disp_to_osu = logic.OSU_H / self.CANVAS_H
        self.reverse_var = tk.BooleanVar(value=False)

        self.configure(bg=FRONT_BG)
        top = tk.Frame(self, bg=FRONT_BG)
        top.pack(fill="x")
        _make_accent_button(top, "Apply", self._apply).pack(side="right", padx=10, pady=8)
        top_info = InfoIcon(top, "- Drag the BG vertically to adjust its "
                                  "position and find the ideal offset for your map. "
                                  "You can also use the mouse wheel for finer "
                                  "adjustments.\n"
                                  "- For precise positioning, use the ↑ and ↓ buttons "
                                  "to fine-tune the offset.", align="right")
        top_info.configure(bg=FRONT_BG)
        top_info.pack(side="right", padx=(0, 4), pady=8)
        LightCheckbox(top, "Reverse control", self.reverse_var, bg=FRONT_BG).pack(
            side="left", padx=10, pady=8)

        tk.Frame(self, bg=FRONT_BORDER, height=1).pack(fill="x")

        self.canvas = tk.Canvas(self, width=self.CANVAS_W, height=self.CANVAS_H,
                                 bg="black", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        # Mouse wheel: Windows/Mac send <MouseWheel> with event.delta;
        # X11/Linux sends <Button-4> (up) / <Button-5> (down) instead.
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-4>", self._on_scroll)
        self.canvas.bind("<Button-5>", self._on_scroll)
        self.bind("<Up>", lambda e: self._nudge(self._sign()))
        self.bind("<Down>", lambda e: self._nudge(-self._sign()))
        self.bind("<Escape>", lambda e: self.destroy())

        self._clamp_offset()
        self._render()
        self.transient(parent_frame)
        self.lift()
        self.focus_force()
        self.grab_set()

    def _clamp_offset(self):
        img_path = os.path.join(self.folder, self.bg_file)
        lo, hi = logic.get_offset_bounds(img_path)
        self.offset = int(round(max(lo, min(hi, self.offset))))

    def _render(self):
        from PIL import ImageTk
        img_path = os.path.join(self.folder, self.bg_file)
        pil_img = logic.render_bg_preview(img_path, self.offset, self.CANVAS_W, self.CANVAS_H)
        self.tk_img = ImageTk.PhotoImage(pil_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        lane_h = round(self.CANVAS_H * logic.TAIKO_LANE_FRAC)
        self.canvas.create_text(self.CANVAS_W // 2, lane_h // 2, text=f"y : {self.offset}",
                                 fill="white", font=("Segoe UI", 24))

    def _on_press(self, event):
        self._drag_last_y = event.y

    def _on_drag(self, event):
        if self._drag_last_y is None:
            self._drag_last_y = event.y
            return
        # Relative movement since the last event, not an absolute jump.
        # Dragging up (event.y decreasing) moves the image up — like
        # grabbing a photo and pulling it up with your finger, revealing
        # more of what's below. That means *decreasing* the offset here
        # (see _crop_to_band: a smaller offset shifts the crop window
        # further down the source image, which is what "the image moved
        # up on screen" looks like).
        delta = self._drag_last_y - event.y
        self._drag_last_y = event.y
        if delta:
            self.offset -= round(delta * self._disp_to_osu) * self._sign()
            self._clamp_offset()
            self._render()

    def _on_scroll(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self._nudge(5 * self._sign())
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            self._nudge(-5 * self._sign())

    def _sign(self):
        return -1 if self.reverse_var.get() else 1

    def _nudge(self, amount):
        self.offset += amount
        self._clamp_offset()
        self._render()

    def _apply(self):
        self.on_apply(self.offset)
        self.destroy()


class _TransportButton(tk.Canvas):
    """Small canvas-drawn rewind/play/pause/forward icon button, used by
    VideoPreviewWindow's seek row instead of the Unicode "media control"
    glyphs (ffff/f8/e9) — those render with a built-in square frame baked
    right into the glyph itself on Windows (Segoe UI Emoji), which a
    ttk.Button's own border used to mask but a borderless flat button
    exposes as an unwanted box around each icon. Drawing the triangles/bars
    directly sidesteps the font entirely, matching the rest of this theme's
    "native rendering can't be controlled, draw it ourselves" approach
    (see LightCheckbox/LightRadiobutton)."""

    def __init__(self, parent, kind, command, size=26, bg=FRONT_BG,
                 fg=FRONT_TEXT, hover_fg=LIGHT_ACCENT):
        super().__init__(parent, width=size, height=size, bg=bg,
                          highlightthickness=0, cursor="hand2")
        self.kind = kind
        self.fg = fg
        self.hover_fg = hover_fg
        self._draw(fg)
        self.bind("<Button-1>", lambda _e: command())
        self.bind("<Enter>", lambda _e: self._draw(self.hover_fg))
        self.bind("<Leave>", lambda _e: self._draw(self.fg))

    def set_kind(self, kind):
        self.kind = kind
        self._draw(self.fg)

    def _draw(self, color):
        self.delete("icon")
        cx, cy = int(self["width"]) / 2, int(self["height"]) / 2
        if self.kind == "back":
            self.create_polygon(cx + 1, cy - 7, cx + 1, cy + 7, cx - 6, cy,
                                 fill=color, outline=color, tags="icon")
            self.create_polygon(cx + 8, cy - 7, cx + 8, cy + 7, cx + 1, cy,
                                 fill=color, outline=color, tags="icon")
        elif self.kind == "forward":
            self.create_polygon(cx - 8, cy - 7, cx - 8, cy + 7, cx - 1, cy,
                                 fill=color, outline=color, tags="icon")
            self.create_polygon(cx - 1, cy - 7, cx - 1, cy + 7, cx + 6, cy,
                                 fill=color, outline=color, tags="icon")
        elif self.kind == "play":
            self.create_polygon(cx - 5, cy - 7, cx - 5, cy + 7, cx + 6, cy,
                                 fill=color, outline=color, tags="icon")
        elif self.kind == "pause":
            self.create_rectangle(cx - 6, cy - 7, cx - 2, cy + 7, fill=color,
                                   outline=color, tags="icon")
            self.create_rectangle(cx + 2, cy - 7, cx + 6, cy + 7, fill=color,
                                   outline=color, tags="icon")


class VideoPreviewWindow(tk.Toplevel):
    """480x270 live preview: plays the song's audio and the chosen video at
    the same time via VLC, with a seeker and quick offset buttons
    (-1000/-500/-200/-100/-50/-20/-10/-5/-1/+1/+5/+10/+20/+50/+100/+200/
    +500/+1000 ms). Each button nudges a running offset total and restarts
    playback with that offset applied; Apply writes the total back into
    the Offset field."""

    QUICK_OFFSETS = [-1000, -500, -200, -100, -50, -20, -10, -5, -1,
                      1, 5, 10, 20, 50, 100, 200, 500, 1000]

    def __init__(self, parent_frame, folder, video_file, audio_file, initial_offset, on_apply):
        super().__init__(parent_frame)
        import vlc

        self.title("Video Sync Offset Preview")
        self.configure(bg=FRONT_BG)
        _position_over_window(self, parent_frame, width=1440, height=840)
        self.resizable(False, False)
        self.folder = folder
        self.video_file = video_file
        self.audio_file = audio_file
        self.offset = initial_offset
        self.on_apply = on_apply
        self._video_delay_job = None
        self._poll_job = None
        self._seeking = False

        ttk.Style().configure("Light.Horizontal.TScale", background=FRONT_BG,
                               troughcolor=FRONT_BORDER)

        top = tk.Frame(self, bg=FRONT_BG)
        top.pack(side="top", fill="x")
        self.offset_label = tk.Label(top, text=f"Current Video Offset: {self.offset}",
                                      bg=FRONT_BG, fg=FRONT_TEXT, font=("Segoe UI", 12, "bold"))
        self.offset_label.pack(side="left", padx=14, pady=10)

        _make_accent_button(top, "Apply", self._apply).pack(side="right", padx=14, pady=8)
        top_info = InfoIcon(top, "- Use the offset controls to adjust the video's timing "
                      "until it is properly synchronized with the audio. Do "
                      "note that every time the offset is adjusted, the "
                      "video will restart.\n"
                      "- If the video appears ahead of the music, increase "
                      "the offset by applying a positive value.\n"
                      "- Use ← and → buttons to seek backward or forward "
                      "through the video, and press the Space key to play "
                      "or pause playback.", align="right")
        top_info.configure(bg=FRONT_BG)
        top_info.pack(side="right", padx=(0, 4), pady=8)

        tk.Frame(self, bg=FRONT_BORDER, height=1).pack(fill="x")

        # Bottom-anchored controls packed first (each subsequent side="bottom"
        # widget stacks above the previous), so the volume row ends up right
        # at the bottom edge, the quick-offset buttons just above it, and the
        # seek bar above that — then the video surface fills whatever space
        # remains, using the freed-up room instead of leaving dead space
        # below a small fixed-size video area.
        vol_row = tk.Frame(self, bg=FRONT_BG)
        vol_row.pack(side="bottom", fill="x", padx=14, pady=(4, 10))
        tk.Label(vol_row, text="Vol", bg=FRONT_BG, fg=FRONT_TEXT_MUTED,
                 font=("Segoe UI", 10)).pack(side="right", padx=(6, 0))
        self.volume_var = tk.IntVar(value=50)
        self.volume_scale = ttk.Scale(vol_row, from_=0, to=100, orient="horizontal", length=110,
                                       style="Light.Horizontal.TScale", command=self._on_volume_change)
        self.volume_scale.set(50)
        self.volume_scale.pack(side="right")
        # Same fix as the seek bar: clicking anywhere on the trough should
        # jump the volume exactly there, not just step it by a tiny amount.
        self.volume_scale.bind("<Button-1>", self._on_volume_click)
        self.volume_scale.bind("<B1-Motion>", self._on_volume_click)

        def _offset_button(parent, delta):
            text = f"+{delta}" if delta > 0 else str(delta)
            return tk.Button(parent, text=text, width=5, font=("Segoe UI", 9, "bold"),
                              bg=LIGHT_ACCENT_SOFT, activebackground=LIGHT_ACCENT_SOFT,
                              fg=LIGHT_ACCENT, activeforeground=LIGHT_ACCENT,
                              relief="flat", bd=0, cursor="hand2", padx=2, pady=6,
                              command=lambda: self._nudge(delta))

        btn_row = tk.Frame(self, bg=FRONT_BG)
        btn_row.pack(side="bottom", pady=8)
        prev_delta = None
        for delta in self.QUICK_OFFSETS:
            if prev_delta is not None and prev_delta < 0 < delta:
                tk.Frame(btn_row, bg=FRONT_BORDER, width=1).pack(side="left", fill="y", padx=8, pady=2)
            _offset_button(btn_row, delta).pack(side="left", padx=1)
            prev_delta = delta

        seek_row = tk.Frame(self, bg=FRONT_BG)
        seek_row.pack(side="bottom", fill="x", padx=14, pady=(8, 0))
        self.time_label = tk.Label(seek_row, text="00:00", width=6, bg=FRONT_BG,
                                    fg=FRONT_TEXT, font=("Segoe UI", 10))
        self.time_label.pack(side="left")

        _TransportButton(seek_row, "back", lambda: self._seek_relative(-5000)).pack(
            side="left", padx=(6, 0))
        self.play_pause_button = _TransportButton(seek_row, "pause", self._toggle_play_pause)
        self.play_pause_button.pack(side="left", padx=2)
        _TransportButton(seek_row, "forward", lambda: self._seek_relative(5000)).pack(
            side="left", padx=(0, 6))

        self.seek_scale = ttk.Scale(seek_row, from_=0, to=1000, orient="horizontal",
                                     style="Light.Horizontal.TScale")
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=8)
        # Bound directly to real mouse press/drag/release, not `command=` —
        # that fires on ANY value change including our own programmatic
        # position updates during playback, which made it impossible to
        # tell a real user drag apart from us just refreshing the display.
        self.seek_scale.bind("<Button-1>", self._on_seek_press)
        self.seek_scale.bind("<B1-Motion>", self._on_seek_press)
        self.seek_scale.bind("<ButtonRelease-1>", self._on_seek_release)

        self._paused = False
        self.bind("<space>", lambda e: self._toggle_play_pause())
        self.bind("<Left>", lambda e: self._seek_relative(-5000))
        self.bind("<Right>", lambda e: self._seek_relative(5000))
        self.bind("<Escape>", lambda e: self._on_close())

        self.video_surface = tk.Frame(self, width=960, height=540, bg="black")
        self.video_surface.pack(side="top", fill="both", expand=True)
        self.video_surface.pack_propagate(False)

        self.vlc_instance = vlc.Instance("--no-xlib") if os.name != "nt" else vlc.Instance()
        self.video_player = self.vlc_instance.media_player_new()
        self.audio_player = self.vlc_instance.media_player_new() if audio_file else None

        self.update_idletasks()
        self._embed_video_output()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_media_and_play()
        self._poll_job = self.after(200, self._poll_position)
        self.transient(parent_frame)
        self.lift()
        self.focus_force()
        self.grab_set()

    def _embed_video_output(self):
        handle = self.video_surface.winfo_id()
        if sys.platform.startswith("linux"):
            self.video_player.set_xwindow(handle)
        elif sys.platform == "win32":
            self.video_player.set_hwnd(handle)
        elif sys.platform == "darwin":
            self.video_player.set_nsobject(handle)

    def _load_media_and_play(self):
        video_path = os.path.join(self.folder, self.video_file)
        self.video_player.set_media(self.vlc_instance.media_new(video_path))
        if self.audio_player and self.audio_file:
            audio_path = os.path.join(self.folder, self.audio_file)
            if os.path.exists(audio_path):
                self.audio_player.set_media(self.vlc_instance.media_new(audio_path))
            else:
                self.audio_player = None
        self._apply_volume()
        self._restart_playback()

    def _apply_volume(self):
        vol = int(self.volume_scale.get())
        try:
            self.video_player.audio_set_volume(0 if self.audio_player else vol)
            if self.audio_player:
                self.audio_player.audio_set_volume(vol)
        except Exception:
            pass

    def _apply_volume_soon(self):
        """libVLC only actually creates a player's audio output once it
        reaches the Playing state — calling audio_set_volume() right after
        play()/set_time() can land before that happens and get silently
        dropped, which is why sound sometimes stayed muted until something
        else (dragging the seek bar or volume slider) reapplied it later.
        Reapplying a few more times over the following second closes that
        race without needing to guess a single "safe" delay."""
        self._apply_volume()
        for delay in (100, 300, 600, 1000):
            self.after(delay, self._apply_volume)

    def _on_volume_change(self, _value):
        self._apply_volume()

    def _on_volume_click(self, event):
        # Same reasoning as the seek bar's press handler: jump exactly to
        # the clicked position instead of ttk.Scale's default tiny step.
        value = _scale_value_at_x(self.volume_scale, event.x)
        self.volume_scale.set(value)  # triggers _on_volume_change via `command=`
        return "break"

    def _restart_playback(self):
        if self._video_delay_job:
            self.after_cancel(self._video_delay_job)
            self._video_delay_job = None
        self.video_player.stop()
        if self.audio_player:
            self.audio_player.stop()

        if self.audio_player:
            self.audio_player.play()

        if self.offset >= 0:
            # Video starts `offset` ms after the audio does.
            self._video_delay_job = self.after(max(0, int(self.offset)), self.video_player.play)
        else:
            # Video started earlier than the audio: start it now, already
            # partway through, so audio (starting "now" too) stays in sync.
            self.video_player.play()
            self.after(80, lambda: self.video_player.set_time(int(-self.offset)))
        self._apply_volume_soon()
        self._paused = False
        self.play_pause_button.set_kind("pause")

    def _nudge(self, delta):
        self.offset += delta
        self.offset_label.configure(text=f"Current Video Offset: {self.offset}")
        self._restart_playback()

    def _poll_position(self):
        try:
            ref_player = self.audio_player or self.video_player
            length = ref_player.get_length()
            pos = ref_player.get_time()
            if length and length > 0 and not self._seeking:
                self.seek_scale.configure(to=length)
                self.seek_scale.set(max(0, pos))
            secs = max(0, pos) // 1000
            self.time_label.configure(text=f"{secs // 60:02d}:{secs % 60:02d}")
        except Exception:
            pass
        self._poll_job = self.after(200, self._poll_position)

    def _on_seek_press(self, event):
        # ttk.Scale's own trough-click behavior only steps by a tiny
        # increment, not a jump to the clicked position — so the value is
        # computed and set explicitly here instead, making a single click
        # skip straight to that exact point, not just where a drag ends.
        # "break" stops the class's own default handler from also nudging
        # it by that tiny increment afterward.
        self._seeking = True
        value = _scale_value_at_x(self.seek_scale, event.x)
        self.seek_scale.set(value)
        self._seek_to(int(value))
        return "break"

    def _on_seek_release(self, _event):
        target_ms = int(self.seek_scale.get())
        self._seek_to(target_ms)
        self._seeking = False

    def _seek_relative(self, delta_ms: int):
        """Rewind/forward by `delta_ms` from the current playback position
        (used by the ⏪/⏩ buttons and the Left/Right arrow keys)."""
        ref_player = self.audio_player or self.video_player
        try:
            current = ref_player.get_time()
        except Exception:
            current = 0
        self._seek_to(max(0, current) + delta_ms)

    def _toggle_play_pause(self):
        """Play/pause both players together (bound to the button and the
        Space key)."""
        self._paused = not self._paused
        try:
            if self._paused:
                self.video_player.set_pause(1)
                if self.audio_player:
                    self.audio_player.set_pause(1)
            else:
                self.video_player.set_pause(0)
                if self.audio_player:
                    self.audio_player.set_pause(0)
        except Exception:
            pass
        self.play_pause_button.set_kind("play" if self._paused else "pause")

    def _seek_to(self, audio_time_ms: int):
        """Moves playback to `audio_time_ms` on the audio timeline while
        preserving the current offset — recomputed fresh each time, so it
        works correctly whether or not the video has started playing yet
        (e.g. it's still waiting out its initial delay for a positive
        offset). This replaces relying on a possibly-still-pending delayed
        play() call, which was the cause of seeks appearing to reset the
        offset."""
        audio_time_ms = max(0, int(audio_time_ms))

        # Any previously scheduled "start video after N ms" call is now
        # stale — we're taking direct control of playback position.
        if self._video_delay_job:
            self.after_cancel(self._video_delay_job)
            self._video_delay_job = None

        if self.audio_player:
            if not self.audio_player.is_playing():
                self.audio_player.play()
            self.audio_player.set_time(audio_time_ms)

        video_time_ms = audio_time_ms - self.offset
        if video_time_ms < 0:
            # At this point on the timeline the video hasn't started yet
            # (still within its initial offset delay) — pause it and
            # schedule it to begin after the remaining delay.
            self.video_player.stop()
            self._video_delay_job = self.after(int(-video_time_ms), self.video_player.play)
        else:
            if not self.video_player.is_playing():
                self.video_player.play()
            self.video_player.set_time(int(video_time_ms))
        self._apply_volume_soon()

    def _apply(self):
        self.on_apply(self.offset)
        self._on_close()

    def _on_close(self):
        if self._poll_job:
            self.after_cancel(self._poll_job)
        if self._video_delay_job:
            self.after_cancel(self._video_delay_job)
        try:
            self.video_player.stop()
            if self.audio_player:
                self.audio_player.stop()
        except Exception:
            pass
        self.destroy()


# =============================================================================
class VideoOffsetShifterFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="Video Settings", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row,
                                "Preview video offset to sync to the music without "
                                "having to manually repeatedly type and guess the "
                                "desired number.")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        video_row = tk.Frame(self.body, bg=FRONT_BG)
        video_row.pack(fill="x", padx=24, pady=(4, 16))
        tk.Label(video_row, text="Video file:", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.video_var = tk.StringVar()
        self.video_combo = LightDropdown(video_row, self.video_var, width=30)
        self.video_combo.pack(side="left", padx=(10, 0))
        _make_ghost_button(video_row, "Browse...", self.browse_video).pack(side="left", padx=(10, 0))

        self.card = RoundedCard(self.body)
        self.card.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        body = self.card.body

        def info(parent, text, **kw):
            icon = InfoIcon(parent, text, **kw)
            icon.configure(bg=FRONT_CARD_BG)
            return icon

        # "Current Offset:"/"New Offset:" are independently packed rows,
        # not a shared grid — same label width on both (fixed to the
        # longer text's own character count, left-anchored) so the two
        # entry boxes' left edges still line up despite that, same fix as
        # MetadataManagerFrame's Preview Point misalignment (see CLAUDE.md).
        LABEL_W = len("Current Offset:")

        row2 = tk.Frame(body, bg=FRONT_CARD_BG)
        row2.pack(fill="x", pady=(0, 10))
        tk.Label(row2, text="Current Offset:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11), width=LABEL_W, anchor="w").pack(side="left")
        self.current_entry = _make_light_entry(row2, width=10, state="readonly")
        self.current_entry.pack(side="left", padx=(10, 0), ipady=4, ipadx=6)

        row3 = tk.Frame(body, bg=FRONT_CARD_BG)
        row3.pack(fill="x", pady=(0, 12))
        tk.Label(row3, text="New Offset:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11), width=LABEL_W, anchor="w").pack(side="left")
        self.offset_var = tk.StringVar(value="0")
        _make_light_entry(row3, textvariable=self.offset_var, width=10).pack(
            side="left", padx=(10, 10), ipady=4, ipadx=6)
        _make_ghost_button(row3, "Preview", self.open_preview).pack(side="left")

        self.resizer_var = tk.BooleanVar(value=False)
        self.blur_var = tk.BooleanVar(value=True)
        resizer_row = tk.Frame(body, bg=FRONT_CARD_BG)
        resizer_row.pack(fill="x", anchor="w", pady=(0, 6))
        LightCheckbox(resizer_row, "Taiko Video Resizer", self.resizer_var,
                      command=lambda: self._sync_video_option_state("resizer")).pack(side="left")
        info(resizer_row, "Resize the full size video to fit under the "
                           "taiko playfield.").pack(side="left", padx=(6, 0))

        blur_row = tk.Frame(body, bg=FRONT_CARD_BG)
        blur_row.pack(fill="x", anchor="w", padx=(24, 0), pady=(0, 10))
        self.blur_check = LightCheckbox(blur_row, "Blur", self.blur_var)
        self.blur_check.pack(side="left")
        self.blur_check.set_enabled(False)
        info(blur_row, "Aesthetic blur for video.").pack(side="left", padx=(6, 0))

        # Taiko Video SB Code — a storyboard-based alternative to the
        # resizer above (can't be used together: checking one unchecks the
        # other, see _sync_video_option_state) that fakes the same
        # crop+shrink live via storyboard commands instead of a separately
        # re-encoded video file. No preview UI (it was often useless for
        # calibrating video positioning) — Apply computes and writes the
        # SB code directly, see apply().
        self.sb_var = tk.BooleanVar(value=False)
        sb_row = tk.Frame(body, bg=FRONT_CARD_BG)
        sb_row.pack(fill="x", anchor="w", pady=(0, 12))
        LightCheckbox(sb_row, "Taiko Video SB Code", self.sb_var,
                      command=lambda: self._sync_video_option_state("sb")).pack(side="left")
        info(sb_row, "Commonly used in hybrid mapsets.").pack(side="left", padx=(6, 0))

        tk.Label(body, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.diff_list = DiffCheckList(body, app, light=True, label_inside=False)
        self.diff_list.pack(fill="both", expand=True, pady=(0, 16))

        btn_row = tk.Frame(body, bg=FRONT_CARD_BG)
        btn_row.pack(fill="x")
        _make_accent_button(btn_row, "Apply", self.apply).pack(side="right")

    def _sync_video_option_state(self, source):
        """Taiko Video Resizer and Taiko Video SB Code are mutually
        exclusive — checking one unchecks the other — then syncs the
        resizer's own Blur sub-control to its checkbox."""
        if source == "resizer" and self.resizer_var.get():
            self.sb_var.set(False)
        elif source == "sb" and self.sb_var.get():
            self.resizer_var.set(False)
        self.blur_check.set_enabled(self.resizer_var.get())

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def refresh(self):
        folder, diffs = self.app.get_diff_files()
        videos = logic.list_song_folder_videos(folder) if folder else []
        self.video_combo.set_values(videos)
        current_video = None
        if folder and diffs:
            bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
            current_video = bm.get_video_filename()
        if current_video and current_video in videos:
            self.video_var.set(current_video)
        elif videos:
            self.video_var.set(videos[0])
        else:
            self.video_var.set("")
        self.diff_list.refresh()
        # See OffsetShifterFrame.refresh for why this outer card needs an
        # explicit redraw after the nested diff checklist's own height
        # changes — the diff_list's own card only resyncs itself.
        self.card.redraw()
        self._prefill_current_offset(folder, diffs)

    def _prefill_current_offset(self, folder, diffs):
        """Loads whatever video offset is already saved in the map into the
        read-only Current Offset field, and also prefills New Offset with
        it — since Apply replaces the stored value rather than adding to
        it, starting New Offset from a blind "0" could otherwise silently
        zero out an existing offset the first time someone clicks Apply."""
        if not folder or not diffs:
            return
        bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
        current = bm.get_video_time()
        current_str = str(int(round(current))) if current is not None else ""
        self.current_entry.configure(state="normal")
        self.current_entry.delete(0, "end")
        self.current_entry.insert(0, current_str)
        self.current_entry.configure(state="readonly")
        if current is not None:
            self.offset_var.set(current_str)

    def browse_video(self):
        folder, _ = self.app.get_diff_files()
        if not folder:
            _show_alert(self, "No map loaded", "Load a map before browsing for a video file.")
            return
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[("Video files", "*.mp4 *.m4v *.avi *.mov *.flv *.wmv *.webm"), ("All files", "*.*")],
        )
        if not path:
            return
        final_name = logic.import_external_video_file(folder, path)
        self.refresh()
        self.video_var.set(final_name)

    def open_preview(self):
        existing = getattr(self, "_preview_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        folder, _ = self.app.get_diff_files()
        if not folder or not self.video_var.get():
            _show_alert(self, "No video", "Pick a video file first.")
            return

        if logic.vlc_available():
            self._launch_preview_window(folder)
            return

        choice = _ask_vlc_required_choice(self)
        if choice == "cancel":
            return

        def work(cancel_event):
            logic.install_vlc_bundled()

        def on_success(_result):
            self.notify_done("VLC installed — opening preview.")
            self._launch_preview_window(folder)

        def on_error(err_msg):
            _show_alert(self, "Error", err_msg)

        self.app.run_cancellable_job(
            "Installing VLC (may take a few minutes)... Please wait...",
            work, on_success=on_success, on_error=on_error,
            cancelled_toast="Installation Cancelled!",
        )

    def _launch_preview_window(self, folder):
        audio_file = logic.get_audio_filename(folder)
        try:
            initial_offset = int(float(self.offset_var.get()))
        except ValueError:
            initial_offset = 0

        self._preview_win = VideoPreviewWindow(self, folder, self.video_var.get(), audio_file,
                                                initial_offset, self._set_offset)

    def _set_offset(self, value):
        self.offset_var.set(str(value))

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        if not targets:
            _show_alert(self, "Nothing selected", "Tick at least one difficulty.")
            return
        try:
            delta = int(float(self.offset_var.get()))
        except ValueError:
            _show_alert(self, "Invalid value", "Offset must be a number.")
            return

        video_file = self.video_var.get() or None
        wants_resize = self.resizer_var.get() and video_file
        wants_sb = self.sb_var.get() and video_file

        install_first = False
        if wants_resize and not logic.ffmpeg_available():
            choice = _ask_ffmpeg_required_choice(self, "The Taiko Video Resizer")
            if choice == "cancel":
                return
            install_first = True  # only "install" remains as a non-cancel choice

        def finish_ok(final_video):
            msg = f"Video offset applied to {len(targets)} difficulty file(s)."
            if final_video != video_file:
                msg += f"\nResized video saved as {final_video}"
            if wants_sb:
                msg += "\nTaiko Video SB Code written under the Video event."
            self.notify_done(msg)
            self.refresh()

        def apply_offset_step(final_video):
            if wants_sb:
                logic.apply_video_sb_code(folder, targets, final_video, delta)
            else:
                logic.apply_video_offset(folder, targets, final_video, delta)
            return final_video

        need_busy = install_first or wants_resize
        if not need_busy:
            # No ffmpeg install and no resize — just an .osu edit, nothing
            # worth a busy banner (let alone a Cancel button) over.
            def work():
                try:
                    final_video = apply_offset_step(video_file)
                except Exception as e:
                    err_msg = str(e)
                    self.after(0, lambda: _show_alert(self, "ffmpeg error", err_msg))
                    return
                self.after(0, lambda: finish_ok(final_video))

            threading.Thread(target=work, daemon=True).start()
            return

        def work(cancel_event):
            if install_first:
                logic.install_ffmpeg_suite_bundled()
                if cancel_event.is_set():
                    return None
                self.after(0, lambda: self.notify_done("ffmpeg + ffprobe installed — continuing."))
            final_video = video_file
            if wants_resize:
                final_video = logic.resize_taiko_video(folder, video_file, self.blur_var.get())
            return apply_offset_step(final_video)

        busy_msg = ("Installing ffmpeg + ffprobe (may take a few minutes)... Please wait..."
                     if install_first else "Processing... Please wait...")

        def on_error(err_msg):
            # See the matching comment in OffsetShifterFrame._start_apply_thread —
            # `e` is gone by the time this deferred callback runs.
            _show_alert(self, "ffmpeg error", err_msg)

        self.app.run_cancellable_job(busy_msg, work, on_success=finish_ok, on_error=on_error)


class PatternCard(tk.Frame):
    """One pattern's thumbnail in the Pattern Gallery's filmstrip: its
    notes drawn as a row of don/kat circles (red/blue, matching taiko's
    own colours — bigger for a finisher), the pattern's name, and its
    snap divisor in smaller text below (millisecond duration is
    deliberately not shown here — it's on a hover tooltip over the snap
    text instead, to keep the card itself uncluttered). A small red badge
    in the top-right corner deletes just this pattern. A plain click
    selects just this card, ctrl+click toggles it into/out of a
    multi-selection, and *dragging* the card reorders it — dragging from
    empty gallery space instead is what range-selects several cards (see
    PatternGalleryFrame.begin_card_drag/begin_bg_drag)."""

    DON_COLOR = "#e2434f"
    KAT_COLOR = "#2e9fd0"
    BORDER_IDLE = "#cccccc"
    BORDER_HOVER = LIGHT_ACCENT
    BORDER_SELECTED = LIGHT_ACCENT
    # Reserved ring widths — see __init__'s nested-Frame border comment.
    # Fixed forever once the card is built; only the colors toggle.
    BASE_THICKNESS = 1
    HALO_THICKNESS = 2
    NORMAL_RADIUS = 10
    FINISHER_RADIUS = 14
    PX_PER_BEAT = 60  # primary layout scale (_layout_positions_by_beats):
                       # a fixed px-per-beat conversion, not normalized to
                       # this card's own closest gap — chosen so an
                       # adjacent-1/4-tick gap (0.25 beat) renders at the
                       # same ~15px MIN_GAP below already looked right at,
                       # while a coarser divisor (e.g. 1/2, 0.5 beat)
                       # renders visibly wider and a finer one narrower —
                       # different snap divisors read as different card
                       # layouts instead of all collapsing to the same look.
    MIN_GAP = 15      # closest two adjacent notes are ever drawn, in px, in
                       # the legacy ms-offset fallback (_layout_positions,
                       # used only for patterns predating offset_beats —
                       # see PatternCard.__init__) — deliberately less than
                       # 2*NORMAL_RADIUS so a dense cluster overlaps
                       # slightly rather than just sitting edge-to-edge
    MAX_WIDTH = 400   # caps how wide one card can get regardless of gaps

    # Slider (drumroll) is yellow, spinner (balloon) is a mid-gray — not
    # pure white, which all but disappears against the card's own white
    # background. A pale tint of each fills the body bar between head and
    # tail; the tail itself is drawn smaller than the head (TAIL_RADIUS_SCALE)
    # so it reads as a secondary marker rather than competing with it. The
    # canonical source for these — ManualPatternWindow reuses them the same
    # way it already reuses DON_COLOR/KAT_COLOR above, instead of
    # duplicating its own copies.
    SLIDER_HEAD_COLOR = "#ffd400"
    SLIDER_BODY_COLOR = "#fff0b3"
    SPINNER_HEAD_COLOR = "#9e9e9e"
    SPINNER_BODY_COLOR = "#dcdcdc"
    TAIL_RADIUS_SCALE = 0.6

    def __init__(self, master, entry, gallery):
        # The border is nested plain-color Frames (a "colored padding as
        # border" trick), not highlightthickness/highlightbackground on
        # this Frame directly — highlightthickness briefly WAS used here,
        # but it's unreliable for a widget embedded in a scrollable Canvas
        # via create_window: confirmed for real, toggling it on selection
        # could render a stretched ring reaching toward the gallery's far
        # edge even though winfo_width() reported the correct size the
        # whole time (a native rendering bug, not a geometry one — Tk's
        # highlight-ring renderer, not this app's own layout math, drew
        # the wrong thing). Nested Frames sidestep it entirely: every
        # layer's padding below is reserved once, here, at construction,
        # and never changes again — only .configure(bg=...) toggles
        # afterward (see _refresh_border_color), which never triggers a
        # geometry renegotiation. `halo` is the extra ring that only
        # becomes visible (colored) once selected, layered just outside
        # the always-present `border` ring — both render the same color
        # when selected, so they read as one continuous thicker ring
        # rather than two nested boxes.
        super().__init__(master, bg=FRONT_CARD_BG, highlightthickness=0, cursor="hand2")
        self.halo = tk.Frame(self, bg=FRONT_CARD_BG, cursor="hand2")
        self.halo.pack(fill="both", expand=True)
        self.border = tk.Frame(self.halo, bg=self.BORDER_IDLE, cursor="hand2")
        self.border.pack(fill="both", expand=True,
                          padx=self.HALO_THICKNESS, pady=self.HALO_THICKNESS)
        self.content = tk.Frame(self.border, bg="white", cursor="hand2", padx=8, pady=6)
        self.content.pack(fill="both", expand=True,
                           padx=self.BASE_THICKNESS, pady=self.BASE_THICKNESS)
        self.entry = entry
        self.name = entry["name"]
        self.gallery = gallery
        self._is_selected = False
        self._is_hovering = False
        self._border_hover_job = None

        notes = entry["objects"]
        # Circle x-positions are proportional to each note's actual
        # *beat*-relative offset at a fixed px-per-beat scale
        # (_layout_positions_by_beats) rather than evenly spaced by index
        # — so a dense cluster (e.g. "kkk") visibly bunches together while
        # a bigger rhythmic gap actually looks bigger. Using a FIXED scale
        # (not normalized to whatever the closest pair happens to be in
        # THIS card) is deliberate: the same "ddd"-shaped pattern captured
        # at 1/2 vs 1/4 snap has a real, different beat gap between notes,
        # and previously both collapsed to an identical-looking card once
        # normalized — now the 1/2 version reads visibly wider. Only
        # patterns predating offset_beats (see add_pattern_to_gallery)
        # fall back to the old ms-offset/closest-gap-normalized method
        # (_layout_positions), since they have no beat data to scale by.
        # A slider/spinner's tail (end_offset_ms/end_offset_beats) is
        # folded into the same layout pass as its own point in time, so
        # its body renders at full length instead of collapsing to the
        # head's position.
        offsets_of_interest = set()
        beats_by_offset = {}
        has_all_beats = True
        for n in notes:
            offsets_of_interest.add(n["offset_ms"])
            if "offset_beats" in n:
                beats_by_offset[n["offset_ms"]] = n["offset_beats"]
            else:
                has_all_beats = False
            if n.get("end_offset_ms") is not None:
                offsets_of_interest.add(n["end_offset_ms"])
                if n.get("end_offset_beats") is not None:
                    beats_by_offset[n["end_offset_ms"]] = n["end_offset_beats"]
                else:
                    has_all_beats = False
        sorted_offsets = sorted(offsets_of_interest)
        if has_all_beats and sorted_offsets:
            sorted_positions = self._layout_positions_by_beats([beats_by_offset[o] for o in sorted_offsets])
        else:
            sorted_positions = self._layout_positions(sorted_offsets)
        offset_to_x = dict(zip(sorted_offsets, sorted_positions))

        pad = self.FINISHER_RADIUS + 5
        canvas_h = 2 * self.FINISHER_RADIUS + 6
        canvas_w = (sorted_positions[-1] if sorted_positions else 0) + 2 * pad
        canvas = tk.Canvas(self.content, width=canvas_w, height=canvas_h, bg="white", highlightthickness=0)
        canvas.pack()
        cy = canvas_h // 2
        for note in notes:
            cx = offset_to_x[note["offset_ms"]] + pad
            obj_type = note.get("obj_type", 1)
            if obj_type & 2:
                self._draw_span(canvas, cx, cy, note, offset_to_x, pad, "slider")
            elif obj_type & 8:
                self._draw_span(canvas, cx, cy, note, offset_to_x, pad, "spinner")
            else:
                hit_sound = note.get("hit_sound", 0)
                is_kat = bool(hit_sound & (osu_parser.HS_WHISTLE | osu_parser.HS_CLAP))
                is_finisher = bool(hit_sound & osu_parser.HS_FINISH)
                radius = self.FINISHER_RADIUS if is_finisher else self.NORMAL_RADIUS
                color = self.KAT_COLOR if is_kat else self.DON_COLOR
                canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                    fill=color, outline="black")

        name_label = tk.Label(self.content, text=entry["name"], bg="white", font=("Segoe UI", 12, "bold"))
        name_label.pack()
        snap_label = tk.Label(self.content, text=entry.get("snap_divisor", "Unknown"),
                               bg="white", fg="#777777", font=("Segoe UI", 9))
        snap_label.pack()
        _add_hover_tooltip(snap_label, f'Duration: {entry["duration_ms"]} ms')

        # A square fills the canvas edge-to-edge exactly, unlike the old
        # circle — no leftover flat-colored corners to worry about.
        self.badge = tk.Canvas(self.content, width=16, height=16, bg="white", highlightthickness=0, cursor="hand2")
        self.badge.create_rectangle(0, 0, 16, 16, fill="#d32f2f", outline="")
        self.badge.create_line(4, 4, 12, 12, fill="white", width=2)
        self.badge.create_line(12, 4, 4, 12, fill="white", width=2)
        self.badge.bind("<Button-1>", lambda _e: self.gallery.request_delete_single(self.name))
        self.badge.bind("<MouseWheel>", self.gallery._on_gallery_scroll)
        self._hover_hide_job = None
        # Starts hidden — only revealed on hover (see _on_enter/_on_leave
        # below), and never at all while multiple cards are selected
        # (that's what the floating bulk-delete button is for instead).

        # A plain press here starts a potential *reorder* drag (see
        # PatternGalleryFrame.begin_card_drag) — motion/release for it are
        # bound globally for the drag's duration rather than per-widget,
        # so reordering (which re-packs cards, including this one) can't
        # break the event stream partway through.
        #
        # Enter/Leave are bound on every one of these widgets (badge
        # included) rather than just the card frame, because X11/Tk fires
        # Leave-then-Enter pairs as the pointer crosses from the frame's
        # own background onto each child widget sitting on top of it —
        # binding everywhere and debouncing the hide (see _on_leave) keeps
        # the badge from flickering while the mouse moves around inside
        # the same card.
        for widget in (self, self.halo, self.border, self.content, canvas, name_label, snap_label):
            widget.bind("<Button-1>", lambda e: self.gallery.begin_card_drag(self.name, e))
            widget.bind("<Control-Button-1>", lambda _e: self.gallery.toggle_select(self.name))
            widget.bind("<Shift-Button-1>", lambda _e: self.gallery.shift_select(self.name))
            widget.bind("<Double-Button-1>", self._on_double_click)
            widget.bind("<Button-3>", self._show_context_menu)
            widget.bind("<MouseWheel>", self.gallery._on_gallery_scroll)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        # The badge owns its own <Button-1> (delete, bound above) — folding
        # it into the loop above would silently replace that handler with
        # the drag-start one (tk.bind() overwrites, it doesn't stack),
        # which is exactly why the delete click stopped working. Only the
        # hover behavior is shared with the rest of the card.
        self.badge.bind("<Enter>", self._on_enter)
        self.badge.bind("<Leave>", self._on_leave)

    def _on_enter(self, _e=None):
        if self._hover_hide_job is not None:
            self.after_cancel(self._hover_hide_job)
            self._hover_hide_job = None
        if len(self.gallery.selected_pattern_names) < 2:
            self.badge.place(relx=1.0, rely=0.0, x=0, y=0, anchor="ne")
        if self._border_hover_job is not None:
            self.after_cancel(self._border_hover_job)
            self._border_hover_job = None
        self._set_hover_border(True)

    def _on_leave(self, _e=None):
        self._hover_hide_job = self.after(50, self.hide_badge)
        # Same debounce reasoning as the badge above — Enter/Leave fire in
        # rapid pairs as the pointer crosses from the card's own background
        # onto each child widget sitting on top of it, so an undebounced
        # reset here would flicker the border color the same way an
        # undebounced badge would flicker its visibility.
        self._border_hover_job = self.after(50, self._clear_hover_border)

    def hide_badge(self):
        self._hover_hide_job = None
        self.badge.place_forget()

    def _clear_hover_border(self):
        self._border_hover_job = None
        self._set_hover_border(False)

    def _set_hover_border(self, hovering: bool):
        self._is_hovering = hovering
        self._refresh_border_color()

    def _refresh_border_color(self):
        # Selection takes priority over hover, which takes priority over
        # the plain idle color — a single source of truth so set_selected
        # and the hover handlers never fight over which one last touched
        # either ring's color. Only .configure(bg=...) happens here, on
        # already-fixed-size Frames (see __init__) — no geometry change,
        # ever, so this can't trigger the highlightthickness rendering bug
        # __init__'s comment describes. `halo` only becomes visible
        # (colored) when selected; it stays blended into the surrounding
        # gallery background otherwise, so idle/hover show just the thin
        # `border` ring while selected shows both rings as one thicker one.
        if self._is_selected:
            halo_color = self.BORDER_SELECTED
            border_color = self.BORDER_SELECTED
        elif self._is_hovering:
            halo_color = FRONT_CARD_BG
            border_color = self.BORDER_HOVER
        else:
            halo_color = FRONT_CARD_BG
            border_color = self.BORDER_IDLE
        self.halo.configure(bg=halo_color)
        self.border.configure(bg=border_color)

    # ------------------------------------------------------------------
    # Right-click context menu — always targets this specific card,
    # independent of whatever else is currently selected (unlike e.g.
    # ManualPatternWindow's Select-mode right-click, this menu doesn't
    # have any "act on the whole selection" bulk behavior; every action
    # here is inherently single-pattern).
    # ------------------------------------------------------------------
    def _show_context_menu(self, event):
        # Every action here (rename/edit/duplicate/delete) is inherently
        # single-pattern — with 2+ cards selected there's no sensible
        # target, so suppress the menu entirely rather than have it act on
        # just this one card and silently ignore the rest of the selection.
        if len(self.gallery.selected_pattern_names) >= 2:
            return
        _show_light_context_menu(self, event.x_root, event.y_root, [
            ("Rename", self._on_rename),
            "separator",
            ("Edit", self._on_edit),
            ("Duplicate", self._on_duplicate),
            ("Duplicate Inverted", self._on_duplicate_inverted),
            ("Duplicate Reversed", self._on_duplicate_reversed),
            "separator",
            ("Delete", self._on_delete_via_menu, "#d32f2f"),
        ])

    def _on_rename(self):
        new_name = simpledialog.askstring("Rename Pattern", "New name:",
                                           initialvalue=self.name, parent=self.winfo_toplevel())
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or new_name == self.name:
            return
        if any(p["name"] == new_name for p in logic.load_pattern_library()):
            _show_alert(self, "Name taken", f'A pattern named "{new_name}" already exists.')
            return
        logic.rename_pattern_in_gallery(self.name, new_name)
        self.gallery.refresh()

    def _on_edit(self):
        self.gallery.open_manual_pattern_editor(existing_name=self.name)

    def _on_double_click(self, _event=None):
        # Same reasoning as _show_context_menu — jumping straight to Edit
        # only makes sense for a single card. With 2+ selected, the 2nd
        # press's plain <Button-1> (toggle/range-select, already handled by
        # the ordinary binding) is left to stand on its own instead.
        if len(self.gallery.selected_pattern_names) >= 2:
            return
        # A double-click's 2nd press ALSO fires the ordinary <Button-1>
        # binding just above (begin_card_drag) — cancel that pending drag
        # tracking before opening the modal editor, see
        # PatternGalleryFrame._cancel_card_drag's own docstring for why.
        self.gallery._cancel_card_drag()
        self._on_edit()

    def _on_duplicate(self):
        logic.duplicate_pattern(self.name)
        self.gallery.refresh()

    def _on_duplicate_inverted(self):
        logic.duplicate_pattern_inverted(self.name)
        self.gallery.refresh()

    def _on_duplicate_reversed(self):
        logic.duplicate_pattern_reversed(self.name)
        self.gallery.refresh()

    def _on_delete_via_menu(self):
        self.gallery.request_delete_single(self.name)

    def set_selected(self, selected: bool):
        self._is_selected = selected
        self._refresh_border_color()

    def _draw_span(self, canvas, head_x, cy, note, offset_to_x, pad, kind):
        """Renders a slider/spinner: head + body + tail if a duration is
        known (end_offset_ms), else just a head circle in the matching
        color. A *manually*-built slider/spinner always has end_offset_ms
        (see tools_logic.add_manual_pattern_to_gallery); a slider *captured*
        from a real map never does — its duration lives in its remainder's
        osu!-pixel `length` field instead, which needs the source map's own
        SliderMultiplier/SV to convert to a time span, neither of which is
        available here. Better to show "this is a slider" honestly via
        color than fabricate a length this card doesn't actually know."""
        is_finisher = kind == "slider" and bool(note.get("hit_sound", 0) & osu_parser.HS_FINISH)
        radius = self.FINISHER_RADIUS if is_finisher else self.NORMAL_RADIUS
        head_color = self.SLIDER_HEAD_COLOR if kind == "slider" else self.SPINNER_HEAD_COLOR
        end_offset_ms = note.get("end_offset_ms")
        if end_offset_ms is None:
            canvas.create_oval(head_x - radius, cy - radius, head_x + radius, cy + radius,
                                fill=head_color, outline="black")
            return
        body_color = self.SLIDER_BODY_COLOR if kind == "slider" else self.SPINNER_BODY_COLOR
        tail_x = offset_to_x[end_offset_ms] + pad
        tail_radius = radius * self.TAIL_RADIUS_SCALE
        body_h = radius * 1.2
        canvas.create_rectangle(head_x, cy - body_h / 2, tail_x, cy + body_h / 2,
                                 fill=body_color, outline="")
        canvas.create_oval(head_x - radius, cy - radius, head_x + radius, cy + radius,
                            fill=head_color, outline="black")
        canvas.create_oval(tail_x - tail_radius, cy - tail_radius, tail_x + tail_radius, cy + tail_radius,
                            fill=head_color, outline="black")

    @classmethod
    def _layout_positions_by_beats(cls, beats):
        """Primary layout: maps each note's beat-relative offset to an
        x-position at the FIXED PX_PER_BEAT scale — deliberately NOT
        normalized to whatever the closest pair happens to be in this
        particular card (that's what _layout_positions, the legacy
        fallback below, does, and why it made every divisor look the
        same). Only compressed afterward, uniformly, if the whole card
        would still exceed MAX_WIDTH — never stretched, so a short/dense
        pattern isn't artificially blown up to fill the width."""
        if not beats:
            return []
        if len(beats) == 1:
            return [0.0]
        base = beats[0]
        positions = [(b - base) * cls.PX_PER_BEAT for b in beats]
        span = positions[-1]
        if span > cls.MAX_WIDTH:
            scale = cls.MAX_WIDTH / span
            positions = [p * scale for p in positions]
        return positions

    @classmethod
    def _layout_positions(cls, offsets):
        """Legacy fallback for patterns captured before offset_beats
        existed (see PatternCard.__init__) — no beat data to scale by, so
        this instead maps each note's raw ms offset to an x-position
        scaled so the smallest gap between consecutive notes becomes
        exactly MIN_GAP, then compresses further if that would make the
        whole card wider than MAX_WIDTH. Note this normalizes to
        whatever's closest *in this card specifically*, so unlike
        _layout_positions_by_beats it can't distinguish "the same shape at
        a different snap divisor" — acceptable only because these old
        patterns have no divisor info to draw that distinction from
        anyway."""
        if not offsets:
            return []
        if len(offsets) == 1:
            return [0.0]
        gaps = [b - a for a, b in zip(offsets, offsets[1:]) if b > a]
        min_gap = min(gaps) if gaps else 1
        scale = cls.MIN_GAP / min_gap
        span = (offsets[-1] - offsets[0]) * scale
        if span > cls.MAX_WIDTH:
            scale *= cls.MAX_WIDTH / span
        return [(o - offsets[0]) * scale for o in offsets]


class ManualPatternWindow(tk.Toplevel):
    """Modal editor behind Pattern Gallery's "Manually Add Pattern" button
    — builds a pattern by clicking directly on an abstract beat-grid
    timeline instead of capturing a selection from a running osu! editor.
    Grid positions are tracked as exact `Fraction`s (not floats) so a note
    placed under one snap divisor still lines up correctly if the divisor
    is changed afterward — only float when handing offsets off to
    tools_logic at Save time.

    Three mutually exclusive modes (self.mode / self.mode_var, hotkeys 1/2,
    3-and-4 for Special, guarded against hijacking text fields — see
    _guard_focus):

    - **Note** (default, hotkey 2): the phantom cursor preview is shown.
      Clicking an empty tick places a note with the current "brush"
      (Don/Kat × Finisher/normal); clicking an existing note REPLACES it
      with the brush instead of selecting it. Right-click deletes
      whichever note is under the cursor. R/W (Don/Kat) and E (Finisher)
      toggle the brush for notes placed from now on.
    - **Select** (hotkey 1): no phantom preview. Clicking a note selects
      it (yellow border) — Ctrl+click toggles a multi-selection,
      Shift+click range-selects, same model as PatternGalleryFrame's card
      selection. Pressing and dragging a note's head/body moves it (both
      endpoints, preserving length, for a slider/spinner); dragging a
      slider/spinner's tail instead resizes it. Delete or right-click
      removes the current selection; Ctrl+D or clicking an empty tick
      deselects. R/W (Don/Kat) and E (Finisher) retype whichever notes are
      currently selected, in place (skipping fields that don't apply to a
      given kind).
    - **Special** (hotkeys 3 and 4): places a slider (drumroll) or spinner
      (balloon) via a click/move/click gesture instead of one click — the
      first click sets the head and the object stretches live to follow
      the cursor (see _begin_span_placement/_update_placing_tail) until a
      second click (anywhere) sets the tail; right-click cancels a
      placement in progress instead. Pressing 3 or 4 again while already
      in Special toggles which kind it places.

    The snap divisor (dropdown/‹›/Ctrl+scroll over the timeline)
    live-applies to the grid immediately — no separate Apply step."""

    BEATS_SHOWN = 4
    PX_PER_BEAT = 160
    MARGIN = 30
    CANVAS_H = 140
    TICK_BASE_Y = 120     # ticks rise upward from this baseline

    # Taller/bolder = coarser subdivision, matching the editor's own
    # snap-color convention (see osu_parser.DIVISOR_BASES) as closely as a
    # 6-color simplification can: 1/1 is always shown (black); 1/3 and 1/6
    # share orange since neither aligns with the 1/2 grid; 1/12-exclusive
    # positions (not shared with 1/2/1/3/1/4/1/6) get their own gray tier.
    TICK_HEIGHTS = {"black": 70, "red": 55, "orange": 45, "blue": 40, "yellow": 25, "gray": 20}
    TICK_COLORS = {"black": "black", "red": PatternCard.DON_COLOR, "orange": "#f5a623",
                   "blue": PatternCard.KAT_COLOR, "yellow": "#d4b106", "gray": "#999999"}
    SELECTED_OUTLINE = "#f2c200"

    # 1.5x PatternCard's own note radii — this editor's sprites are
    # deliberately bigger than the gallery card thumbnails for easier
    # clicking on a spread-out beat grid.
    NORMAL_RADIUS = PatternCard.NORMAL_RADIUS * 1.5
    FINISHER_RADIUS = PatternCard.FINISHER_RADIUS * 1.5
    # A slider/spinner's tail is drawn smaller than its head so it reads
    # as a secondary marker instead of visually competing with the head —
    # its click/drag target stays full-size underneath (see _draw_span_sprite).
    # Colors and this scale are canonically defined on PatternCard (see its
    # own class docstring/constants) — reused here the same way DON_COLOR/
    # KAT_COLOR already are, so the gallery thumbnail and this editor never
    # drift out of sync with each other.
    TAIL_RADIUS_SCALE = PatternCard.TAIL_RADIUS_SCALE
    SLIDER_HEAD_COLOR = PatternCard.SLIDER_HEAD_COLOR
    SLIDER_BODY_COLOR = PatternCard.SLIDER_BODY_COLOR
    SPINNER_HEAD_COLOR = PatternCard.SPINNER_HEAD_COLOR
    SPINNER_BODY_COLOR = PatternCard.SPINNER_BODY_COLOR

    # Fraction of the real color kept when blending the phantom preview
    # toward the canvas background (see _blend_toward_bg) — lower = more
    # see-through.
    PHANTOM_ALPHA = 0.45

    DIVISOR_OPTIONS = ["1/2", "1/3", "1/4", "1/6", "1/8", "1/12"]
    DIVISOR_DENOMS = {"1/2": 2, "1/3": 3, "1/4": 4, "1/6": 6, "1/8": 8, "1/12": 12}

    def __init__(self, master, gallery, existing_name=None):
        super().__init__(master)
        self.gallery = gallery
        # Set when opened via a PatternCard's "Edit" context-menu action —
        # _save() then overwrites this pattern in place (same list position
        # in the library) instead of adding a new one. None for the
        # ordinary "Manually Add Pattern" button flow.
        self._editing_original_name = existing_name
        existing_entry = logic.get_pattern(existing_name) if existing_name else None
        if existing_entry is not None:
            header_text = f"Edit Pattern — {existing_entry['name']}"
        else:
            header_text = "Manually Add Pattern"
        self.title(header_text)
        self.configure(bg=FRONT_BG)
        self.resizable(False, False)

        # Each note is {"kind": "note"|"slider"|"spinner", "pos": Fraction,
        # "is_kat": bool, "is_finisher": bool, "end_pos": Fraction|None}.
        # "end_pos" (the tail) is only set for slider/spinner; "is_kat"
        # only means anything for a plain "note"; "is_finisher" only means
        # anything for "note"/"slider" — spinners have no finisher variant.
        load_truncated = False
        if existing_entry is not None:
            self.notes, load_truncated = self._notes_from_entry(existing_entry)
        else:
            self.notes = []
        self.selected_note_ids = set()   # id(note) — notes are unhashable dicts, see CLAUDE.md
        self._range_anchor_id = None     # id of the last plain/ctrl-clicked note, for Shift-click
        self._phantom_item = None
        self._hover_pos = None           # last hovered tick position, for restoring the phantom after a redraw
        self._drag_note = None           # note currently being dragged/resized in Select mode, if any
        self._drag_moved = False
        self._bg_drag_start = None       # Fraction: where a Select-mode background (box-select) drag began
        self._bg_drag_current = None     # Fraction: its current cursor position, for the overlay/range
        self._bg_drag_moved = False
        self.brush_kat = False
        self.brush_finisher = False
        self.current_divisor = self._initial_divisor(existing_entry)
        self.divisor_var = tk.StringVar(value=self.current_divisor)
        # Editing an existing pattern defaults to Select mode (you're most
        # likely reviewing/adjusting what's already there, not immediately
        # placing new notes that could replace one by accident); a fresh
        # "Manually Add Pattern" still defaults to Note mode.
        self.mode = "select" if existing_entry is not None else "note"
        self.mode_var = tk.StringVar(value=self.mode)
        self.special_kind = "slider"     # which kind Special mode currently places: "slider" or "spinner"
        self._placing_note = None        # the slider/spinner currently being stretched to its tail, if any
        # BEATS_SHOWN is always the class default (4) here — every editor,
        # whether adding fresh or editing an existing pattern, shows
        # exactly the same fixed-length timeline. A pattern loaded for
        # editing that ran longer than this gets truncated to fit (see
        # _notes_from_entry) rather than the window growing to match it.

        header_row = tk.Frame(self, bg=FRONT_BG)
        header_row.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(header_row, text=header_text, bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 14, "bold")).pack(side="left")

        top = tk.Frame(self, bg=FRONT_BG)
        top.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(top, text="Pattern name:", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.name_var = tk.StringVar(value=existing_entry["name"] if existing_entry is not None else "")
        _make_light_entry(top, textvariable=self.name_var, width=26).pack(
            side="left", padx=(6, 16), ipady=3)

        divisor_row = tk.Frame(top, bg=FRONT_BG)
        divisor_row.pack(side="right")
        _make_ghost_button(divisor_row, "‹", lambda: self._step_divisor(-1),
                            width=2, padx=8, pady=4).pack(side="left")
        self.divisor_combo = LightDropdown(divisor_row, self.divisor_var,
                                            values=self.DIVISOR_OPTIONS, width=5)
        self.divisor_combo.pack(side="left", padx=4)
        _make_ghost_button(divisor_row, "›", lambda: self._step_divisor(1),
                            width=2, padx=8, pady=4).pack(side="left")
        # Live-applies as soon as the divisor changes — via the dropdown,
        # the ‹›  steppers (which just call divisor_var.set()), or
        # Ctrl+scroll on the canvas — no separate Apply button anymore.
        self.divisor_var.trace_add("write", self._on_divisor_changed)

        # Mode selector — a small segmented-button row built from plain
        # tk.Button rather than ttk.Radiobutton + the "Toolbutton" style
        # the row used before: Windows' native theme renders Toolbutton
        # chrome (sunken/raised) the same way it renders a Checkbutton's
        # tick — ignoring color style overrides (see CLAUDE.md "Why custom
        # widgets instead of ttk styles"). Clicking one still just sets
        # mode_var directly, same as before; the existing trace below still
        # drives the actual mode switch, this one only repaints colors.
        mode_row = tk.Frame(self, bg=FRONT_BG)
        mode_row.pack(fill="x", padx=16, pady=(0, 6))
        self._mode_buttons = {}
        for value, label in (("select", "Select (1)"), ("note", "Note (2)"), ("special", "Special (3/4)")):
            btn = tk.Button(mode_row, text=label, font=("Segoe UI", 10, "bold"),
                             relief="flat", bd=0, cursor="hand2", padx=14, pady=6,
                             command=lambda v=value: self.mode_var.set(v))
            btn.pack(side="left", padx=(0, 6))
            self._mode_buttons[value] = btn
        self.mode_var.trace_add("write", self._on_mode_changed)
        self.mode_var.trace_add("write", self._sync_mode_buttons)
        self._sync_mode_buttons()

        status_row = tk.Frame(self, bg=FRONT_BG)
        status_row.pack(fill="x", padx=16, pady=(0, 8))
        self.status_var = tk.StringVar()
        tk.Label(status_row, textvariable=self.status_var, bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        status_info = InfoIcon(status_row, "The control is the same as on osu!stable's "
                                            "default.")
        status_info.configure(bg=FRONT_BG)
        status_info.pack(side="left", padx=(6, 0))

        canvas_w = self.MARGIN * 2 + self.BEATS_SHOWN * self.PX_PER_BEAT
        # bg stays literal "white" (== FRONT_CARD_BG) — deliberately not
        # themed any further: _blend_toward_bg's phantom-preview blending
        # and the invisible tick/tail hit-test rectangles drawn in _redraw/
        # _draw_span_sprite both assume this canvas's background is
        # exactly #ffffff (see their own docstrings) and would silently
        # break — the hit rectangles would become visible, and the phantom
        # would blend toward the wrong color — if this became any other
        # shade. Only the outer highlight ring is safe to recolor.
        self.canvas = tk.Canvas(self, width=canvas_w, height=self.CANVAS_H, bg="white",
                                 highlightthickness=1, highlightbackground=FRONT_BORDER, cursor="hand2")
        self.canvas.pack(padx=16, pady=(0, 10))
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_scroll)
        self.canvas.bind("<Control-Button-4>", self._on_ctrl_scroll)
        self.canvas.bind("<Control-Button-5>", self._on_ctrl_scroll)

        btn_row = tk.Frame(self, bg=FRONT_BG)
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        tk.Label(btn_row, text="E: finisher toggle - R or W: don/kat toggle", bg=FRONT_BG,
                 fg=FRONT_TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left")
        _make_accent_button(btn_row, "Save Pattern", self._save).pack(side="right")

        self.bind("<r>", self._on_r_key)
        self.bind("<R>", self._on_r_key)
        self.bind("<w>", self._on_r_key)
        self.bind("<W>", self._on_r_key)
        self.bind("<e>", self._on_e_key)
        self.bind("<E>", self._on_e_key)
        self.bind("<Delete>", self._on_delete_key)
        self.bind("<Control-d>", self._on_ctrl_d)
        self.bind("<Control-D>", self._on_ctrl_d)
        self.bind("<Key-1>", lambda e: self._set_mode_hotkey("select"))
        self.bind("<Key-2>", lambda e: self._set_mode_hotkey("note"))
        # 3 and 4 both enter Special mode; pressing either one again while
        # already there toggles what it places (slider <-> spinner) instead
        # of just re-selecting the same mode.
        self.bind("<Key-3>", self._on_special_hotkey)
        self.bind("<Key-4>", self._on_special_hotkey)
        self.bind("<Escape>", lambda e: self.destroy())

        self._update_status()
        self._redraw()

        if load_truncated:
            _show_alert(self,
                "Pattern truncated",
                f'"{existing_entry["name"]}" had notes beyond this editor\'s '
                f"{self.BEATS_SHOWN}-beat timeline — they've been removed so "
                f"everything loaded fits on the grid.")

        _position_over_window(self, master)
        self.transient(master)
        self.lift()
        self.focus_force()
        self.grab_set()

    # ------------------------------------------------------------------
    # Loading an existing pattern for editing (PatternCard's "Edit" action)
    # ------------------------------------------------------------------
    def _notes_from_entry(self, entry: dict) -> tuple:
        """Converts a stored pattern entry's objects back into this
        editor's internal note-dict format (see the class docstring).
        Every editor instance shows the same fixed BEATS_SHOWN-beat
        timeline (see __init__) — an object starting beyond that is
        dropped entirely, and a span object (slider/spinner) that starts
        within range but would otherwise *end* beyond it gets its tail
        clamped to fit instead, so nothing loaded ever sits off the
        visible (and clickable) grid. Returns (notes, truncated: bool)."""
        notes = []
        truncated = False
        limit = Fraction(self.BEATS_SHOWN)
        for obj in entry["objects"]:
            if obj["obj_type"] & 2:
                kind = "slider"
            elif obj["obj_type"] & 8:
                kind = "spinner"
            else:
                kind = "note"
            pos = self._offset_to_fraction(obj, use_end=False)
            if pos > limit:
                truncated = True
                continue
            is_kat = kind == "note" and bool(obj["hit_sound"] & (osu_parser.HS_WHISTLE | osu_parser.HS_CLAP))
            is_finisher = kind != "spinner" and bool(obj["hit_sound"] & osu_parser.HS_FINISH)
            end_pos = None
            if kind != "note":
                if obj.get("end_offset_ms") is not None:
                    end_pos = self._offset_to_fraction(obj, use_end=True)
                else:
                    # A slider captured from a real map with no recorded
                    # duration (see PatternCard._draw_span's own note on
                    # this) — give it some visible, editable length rather
                    # than a zero-length object with nothing to grab.
                    denom = self.DIVISOR_DENOMS[self.current_divisor] if hasattr(self, "current_divisor") else 4
                    end_pos = pos + Fraction(1, denom)
                if end_pos > limit:
                    end_pos = limit
                    truncated = True
                    if end_pos <= pos:
                        continue  # no room left for even a minimal tail — drop it
            notes.append({"kind": kind, "pos": pos, "is_kat": is_kat,
                          "is_finisher": is_finisher, "end_pos": end_pos})
        return notes, truncated

    @staticmethod
    def _offset_to_fraction(obj: dict, use_end: bool) -> Fraction:
        """offset_beats (when recorded) is authoritative — it's exactly
        what this editor itself works in. Falls back to offset_ms divided
        by the manual-pattern reference tempo for older patterns that
        predate offset_beats, or to the source map's own (unrelated) BPM
        for a real capture; either way, limit_denominator snaps the
        result onto the nearest grid position this editor actually
        supports, since a real capture's timing won't generally land on a
        clean fraction the way something built in this editor already does."""
        beats_key = "end_offset_beats" if use_end else "offset_beats"
        ms_key = "end_offset_ms" if use_end else "offset_ms"
        if obj.get(beats_key) is not None:
            val = obj[beats_key]
        else:
            val = obj[ms_key] / logic.MANUAL_PATTERN_REFERENCE_BEAT_LENGTH
        return Fraction(val).limit_denominator(48)

    def _initial_divisor(self, entry) -> str:
        if entry is not None:
            divisor = entry.get("snap_divisor")
            if divisor in self.DIVISOR_OPTIONS:
                return divisor
        return "1/4"

    def _beats_to_x(self, beats) -> float:
        return self.MARGIN + float(beats) * self.PX_PER_BEAT

    def _pos_from_x(self, canvas_x: float) -> Fraction:
        """Snaps a canvas-local x coordinate to the nearest tick at the
        current divisor, clamped to the visible range — shared by the
        hover-preview (_on_canvas_motion) and note-dragging (Select mode)
        so both snap identically."""
        denom = self.DIVISOR_DENOMS[self.current_divisor]
        beats = (canvas_x - self.MARGIN) / self.PX_PER_BEAT
        total_ticks = max(0, min(self.BEATS_SHOWN * denom, round(beats * denom)))
        return Fraction(total_ticks, denom)

    def _level_for_pos(self, pos: Fraction) -> str:
        """Snap-color tier for an arbitrary beat position, independent of
        which divisor is currently on display — a note keeps the color it
        was placed at even if you later switch to a coarser/finer divisor
        view (see the class docstring re: exact Fraction positions)."""
        frac = pos - int(pos)
        if frac == 0:
            return "black"
        d = frac.denominator
        if d == 2:
            return "red"
        if d == 4:
            return "blue"
        if d == 8:
            return "yellow"
        if d == 12:
            return "gray"
        return "orange"  # d in (3, 6) — the only other denominators offered

    def _note_y(self) -> float:
        """A single fixed row — the vertical midpoint of the 1/1 (black,
        tallest) tick — for every note regardless of which tick it's on.
        Positioning each note at its own tick's (varying) height instead
        made placed notes zigzag up and down depending on their snap tier,
        which read as messy rather than as a clean row of notes."""
        return self.TICK_BASE_Y - self.TICK_HEIGHTS["black"] / 2

    def _step_divisor(self, delta):
        i = self.DIVISOR_OPTIONS.index(self.divisor_var.get())
        i = max(0, min(len(self.DIVISOR_OPTIONS) - 1, i + delta))
        self.divisor_var.set(self.DIVISOR_OPTIONS[i])  # triggers _on_divisor_changed via the trace

    def _on_divisor_changed(self, *_args):
        self.current_divisor = self.divisor_var.get()
        self._redraw()

    def _on_ctrl_scroll(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self._step_divisor(1)
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            self._step_divisor(-1)

    def _guard_focus(self) -> bool:
        """True while a text-entry-ish widget has keyboard focus — used to
        keep R/W/E/Delete/the 1-2-3 mode hotkeys from hijacking normal
        typing (e.g. in the name field)."""
        return isinstance(self.focus_get(), (tk.Entry, tk.Spinbox, tk.Text, ttk.Combobox))

    def _set_mode_hotkey(self, mode: str):
        if self._guard_focus():
            return
        self.mode_var.set(mode)  # triggers _on_mode_changed via the trace

    def _on_special_hotkey(self, _event=None):
        """3 and 4 both share this: entering Special mode is the first
        press's job; a second press, once already there, instead toggles
        which kind it places — same spirit as _set_mode_hotkey but with an
        extra action layered on for a mode that has two sub-kinds."""
        if self._guard_focus():
            return
        if self.mode == "special":
            self._cancel_placing()  # switching kind mid-placement abandons it, same as switching modes
            self.special_kind = "spinner" if self.special_kind == "slider" else "slider"
            self._redraw()
            self._refresh_phantom()
        else:
            self.mode_var.set("special")

    def _on_mode_changed(self, *_args):
        self._cancel_placing()
        self.mode = self.mode_var.get()
        # Selection only means anything in Select mode — leaving it means
        # dropping it, so switching away doesn't leave a stale yellow
        # border showing (or a stale selection Delete/right-click could
        # act on) in a mode that no longer has a selection concept.
        if self.mode != "select":
            self.selected_note_ids = set()
            self._range_anchor_id = None
        self._redraw()

    def _sync_mode_buttons(self, *_args):
        """Repaints the mode_row segmented buttons so the active mode
        reads as a solid indigo-filled button and the other two as soft
        ghost buttons — the color half of the "Toolbutton" look this row
        used to get for free from ttk, now done by hand (see __init__)."""
        active = self.mode_var.get()
        for value, btn in self._mode_buttons.items():
            if value == active:
                btn.configure(bg=LIGHT_ACCENT, fg="#ffffff",
                               activebackground=LIGHT_ACCENT_HOVER, activeforeground="#ffffff")
            else:
                btn.configure(bg=LIGHT_ACCENT_SOFT, fg=LIGHT_ACCENT,
                               activebackground=LIGHT_ACCENT_SOFT, activeforeground=LIGHT_ACCENT)

    def _on_r_key(self, _event=None):
        if self._guard_focus():
            return
        if self.mode == "note":
            self.brush_kat = not self.brush_kat
            self._refresh_phantom()
        elif self.mode == "select":
            # Don/Kat only means anything for a plain note — a slider or
            # spinner in the current selection is left untouched.
            for note in self.notes:
                if id(note) in self.selected_note_ids and note["kind"] == "note":
                    note["is_kat"] = not note["is_kat"]
        self._redraw()

    def _on_e_key(self, _event=None):
        if self._guard_focus():
            return
        if self.mode in ("note", "special"):
            # Shared brush toggle: sets the finisher for whatever gets
            # placed next — a plain note or a slider in Note/Special mode
            # respectively. Meaningless for spinners (no finisher variant)
            # but harmless to track regardless of which kind is current.
            self.brush_finisher = not self.brush_finisher
            self._refresh_phantom()
        elif self.mode == "select":
            # Spinners have no finisher variant, so they're skipped here
            # even if selected alongside notes/sliders that do.
            for note in self.notes:
                if id(note) in self.selected_note_ids and note["kind"] != "spinner":
                    note["is_finisher"] = not note["is_finisher"]
        self._redraw()

    def _on_ctrl_d(self, _event=None):
        self._deselect_all()

    def _on_delete_key(self, _event=None):
        if self._guard_focus():
            return
        self._delete_selected()

    def _delete_selected(self):
        if not self.selected_note_ids:
            return
        self.notes = [n for n in self.notes if id(n) not in self.selected_note_ids]
        self.selected_note_ids = set()
        self._range_anchor_id = None
        self._redraw()

    def _delete_object(self, note: dict):
        """Removes exactly this one object, independent of selection — it
        doesn't need to be selected first, and deleting it doesn't select
        (or need to touch) anything else. Only cleans up self.selected_note_ids/
        _range_anchor_id if this object happened to already be part of them,
        so nothing stale is left pointing at a note that no longer exists."""
        if note in self.notes:
            self.notes.remove(note)
        self.selected_note_ids.discard(id(note))
        if self._range_anchor_id == id(note):
            self._range_anchor_id = None
        self._redraw()

    def _update_status(self):
        if self.mode == "note":
            kind = "Kat" if self.brush_kat else "Don"
            suffix = " + Finisher" if self.brush_finisher else ""
            self.status_var.set(f"Note mode — placing: {kind}{suffix}")
        elif self.mode == "select":
            n = len(self.selected_note_ids)
            self.status_var.set(f"Select mode — {n} selected" if n else "Select mode — nothing selected")
        else:
            kind = "Slider" if self.special_kind == "slider" else "Spinner"
            suffix = " + Finisher" if (self.special_kind == "slider" and self.brush_finisher) else ""
            self.status_var.set(f"Special mode — placing: {kind}{suffix}")

    def _find_note_at(self, pos: Fraction):
        for note in self.notes:
            if note["pos"] == pos:
                return note
        return None

    def _deselect_all(self):
        if self.selected_note_ids:
            self.selected_note_ids = set()
            self._redraw()
        self._range_anchor_id = None

    def _ordered_note_ids(self):
        return [id(n) for n in sorted(self.notes, key=lambda n: n["pos"])]

    # ------------------------------------------------------------------
    # Click handling — behavior forks entirely on the current mode:
    #
    # Note mode: a plain press always places-or-replaces a plain note (see
    # _place_or_replace). Ctrl/Shift+click and dragging don't apply, since
    # this mode has no selection concept. Right-click deletes whatever
    # single note is under the cursor.
    #
    # Special mode places a slider/spinner via a *click, move, click*
    # gesture instead of one click — see _begin_span_placement/
    # _update_placing_tail/_finalize_placing below. While self._placing_note
    # is set, ANY press anywhere (regardless of mode or which item it
    # landed on — that's why this is checked first, before the mode
    # dispatch) just finalizes it; right-click instead cancels it.
    #
    # Select mode: a plain press on an existing note/slider/spinner
    # selects it (and starts a potential drag — see _begin_note_drag); on
    # an empty tick it deselects everything instead of placing anything.
    # Ctrl/Shift+click adjust a multi-selection, mirroring
    # PatternGalleryFrame's card selection model. Right-click deletes the
    # current selection outright (not just whatever's under the cursor).
    #
    # For a slider/spinner, _find_note_at(pos) matches on its HEAD
    # position, so clicking the head or the body (both bound to the same
    # pos — see _redraw) is indistinguishable from clicking a plain note
    # here; only the TAIL needs its own path (_on_tail_press) since
    # dragging/resizing it means "resize", not "move".
    # ------------------------------------------------------------------
    def _on_press(self, pos: Fraction):
        if self._placing_note is not None:
            self._finalize_placing()
            return
        if self.mode == "note":
            self._place_or_replace(pos)
            return
        if self.mode == "special":
            self._begin_span_placement(pos)
            return
        if self.mode != "select":
            return
        existing = self._find_note_at(pos)
        if existing is None:
            self._begin_bg_drag(pos)
            return
        self.selected_note_ids = {id(existing)}
        self._range_anchor_id = id(existing)
        self._redraw()
        self._begin_note_drag(existing)

    def _on_tail_press(self, note: dict):
        """The tail (end point) of a slider/spinner — same as clicking
        anywhere else on it in Note/Special mode (replaces/restarts the
        whole object), but in Select mode it resizes instead of moving."""
        if self._placing_note is not None:
            self._finalize_placing()
            return
        if self.mode == "note":
            self._place_or_replace(note["pos"])
            return
        if self.mode == "special":
            self._begin_span_placement(note["pos"])
            return
        if self.mode != "select":
            return
        self.selected_note_ids = {id(note)}
        self._range_anchor_id = id(note)
        self._redraw()
        self._begin_tail_drag(note)

    def _place_or_replace(self, pos: Fraction):
        """Note mode only — Special mode's placement is the click/move/
        click gesture below (_begin_span_placement etc.) instead."""
        data = {"kind": "note", "pos": pos, "is_kat": self.brush_kat,
                "is_finisher": self.brush_finisher, "end_pos": None}
        existing = self._find_note_at(pos)
        if existing is None:
            self.notes.append(data)
        else:
            self.notes[self.notes.index(existing)] = data
        self._redraw()

    # ------------------------------------------------------------------
    # Special-mode slider/spinner placement: click sets the head, the
    # object then stretches live to follow the cursor (_update_placing_tail,
    # driven by _on_canvas_motion) until a second click finalizes the tail
    # wherever it currently is. If the cursor is at or to the left of the
    # head, the tail clamps to the closest snap to the *right* of the head
    # instead of following — a slider/spinner can't have zero or negative
    # length, and there's no "reverse" direction to place one in.
    # ------------------------------------------------------------------
    def _begin_span_placement(self, pos: Fraction):
        denom = self.DIVISOR_DENOMS[self.current_divisor]
        end_pos = pos + Fraction(1, denom)
        if end_pos > Fraction(self.BEATS_SHOWN):
            return  # no room to start a span object here
        kind = self.special_kind
        is_finisher = self.brush_finisher if kind == "slider" else False
        note = {"kind": kind, "pos": pos, "is_kat": False, "is_finisher": is_finisher, "end_pos": end_pos}
        existing = self._find_note_at(pos)
        if existing is None:
            self.notes.append(note)
        else:
            self.notes[self.notes.index(existing)] = note
        self._placing_note = note
        self._redraw()

    def _update_placing_tail(self, canvas_x: float):
        note = self._placing_note
        if note is None:
            return
        denom = self.DIVISOR_DENOMS[self.current_divisor]
        cursor_pos = self._pos_from_x(canvas_x)
        min_end = note["pos"] + Fraction(1, denom)
        new_end = cursor_pos if cursor_pos > note["pos"] else min_end
        new_end = min(new_end, Fraction(self.BEATS_SHOWN))
        if new_end < min_end:
            new_end = min_end
        if new_end == note["end_pos"]:
            return
        occupant = self._find_note_at(new_end)
        if occupant is not None and occupant is not note:
            return  # keep the last valid length rather than overlapping another object's head
        note["end_pos"] = new_end
        self._redraw()

    def _finalize_placing(self):
        self._placing_note = None
        self._redraw()

    def _cancel_placing(self):
        if self._placing_note is None:
            return
        if self._placing_note in self.notes:
            self.notes.remove(self._placing_note)
        self._placing_note = None

    def _on_ctrl_click(self, pos: Fraction):
        if self.mode != "select":
            return
        existing = self._find_note_at(pos)
        if existing is None:
            return
        nid = id(existing)
        if nid in self.selected_note_ids:
            self.selected_note_ids.discard(nid)
        else:
            self.selected_note_ids.add(nid)
        self._range_anchor_id = nid
        self._redraw()

    def _on_shift_click(self, pos: Fraction):
        if self.mode != "select":
            return
        existing = self._find_note_at(pos)
        if existing is None:
            return
        target_id = id(existing)
        ordered_ids = self._ordered_note_ids()
        if self._range_anchor_id is None or self._range_anchor_id not in ordered_ids:
            self.selected_note_ids = {target_id}
            self._range_anchor_id = target_id
        else:
            i1 = ordered_ids.index(self._range_anchor_id)
            i2 = ordered_ids.index(target_id)
            lo, hi = min(i1, i2), max(i1, i2)
            self.selected_note_ids = set(ordered_ids[lo:hi + 1])
        self._redraw()

    def _on_right_click(self, pos: Fraction):
        if self._placing_note is not None:
            self._cancel_placing()
            self._redraw()
            return
        if self.mode == "select":
            # Right-clicking a specific object deletes just that one,
            # whether or not it's currently selected — no need to select
            # first. Right-clicking empty space instead falls back to
            # deleting the whole current selection (if any), so bulk
            # delete via Ctrl/Shift-select + right-click still works.
            existing = self._find_note_at(pos)
            if existing is not None:
                self._delete_object(existing)
            else:
                self._delete_selected()
            return
        if self.mode not in ("note", "special"):
            return
        existing = self._find_note_at(pos)
        if existing is None:
            return
        self.notes.remove(existing)
        self._redraw()

    # ------------------------------------------------------------------
    # Dragging a selected note (Select mode only) to a new snap position.
    # Bound via bind_all rather than on the canvas directly, matching
    # PatternGalleryFrame's card-drag — _redraw() rebuilds every canvas
    # item on each motion tick, and relying on the pressed item's own
    # implicit button-grab to survive that isn't something to bank on.
    # ------------------------------------------------------------------
    def _begin_note_drag(self, note: dict):
        """Head/body press: moves the whole object (both endpoints, for a
        slider/spinner) together, preserving its length."""
        self._drag_note = note
        self._drag_moved = False
        self.bind_all("<B1-Motion>", self._on_note_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_note_drag_release)

    def _on_note_drag_motion(self, event):
        note = self._drag_note
        if note is None:
            return
        canvas_x = event.x_root - self.canvas.winfo_rootx()
        new_pos = self._pos_from_x(canvas_x)
        if new_pos == note["pos"]:
            return
        if note["kind"] == "note":
            occupant = self._find_note_at(new_pos)
            if occupant is not None and occupant is not note:
                return  # don't drop one note on top of another
            note["pos"] = new_pos
        else:
            length = note["end_pos"] - note["pos"]
            if new_pos < 0:
                return  # don't let the head go negative
            new_end = new_pos + length
            if new_end > Fraction(self.BEATS_SHOWN):
                return  # don't let the tail run off the visible timeline
            occupant = self._find_note_at(new_pos)
            if occupant is not None and occupant is not note:
                return  # don't drop the head onto another object's head
            note["pos"] = new_pos
            note["end_pos"] = new_end
        self._drag_moved = True
        self.selected_note_ids = {id(note)}
        self._redraw()

    def _begin_tail_drag(self, note: dict):
        """Tail press (slider/spinner only): resizes by moving only
        end_pos, leaving the head (pos) fixed."""
        self._drag_note = note
        self._drag_moved = False
        self.bind_all("<B1-Motion>", self._on_tail_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_note_drag_release)

    def _on_tail_drag_motion(self, event):
        note = self._drag_note
        if note is None:
            return
        canvas_x = event.x_root - self.canvas.winfo_rootx()
        new_end = self._pos_from_x(canvas_x)
        denom = self.DIVISOR_DENOMS[self.current_divisor]
        min_end = note["pos"] + Fraction(1, denom)
        if new_end < min_end:
            new_end = min_end  # can't shrink to zero/negative length
        if new_end == note["end_pos"]:
            return
        occupant = self._find_note_at(new_end)
        if occupant is not None and occupant is not note:
            return  # don't let the tail land exactly on another object's head
        note["end_pos"] = new_end
        self._drag_moved = True
        self.selected_note_ids = {id(note)}
        self._redraw()

    def _on_note_drag_release(self, _event):
        """Shared release handler for both the move drag (_on_note_drag_motion)
        and the resize drag (_on_tail_drag_motion) — neither needs anything
        different done at release time."""
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._drag_note = None
        self._drag_moved = False

    # ------------------------------------------------------------------
    # Select mode: press-and-drag from an EMPTY tick box-selects every
    # object between where the drag started and wherever the cursor
    # currently is, mirroring PatternGalleryFrame's own bg-drag range
    # select. Released without ever moving counts as a plain empty-tick
    # click instead (deselect). Bound via bind_all for the same reason
    # every other drag here is — _redraw() rebuilds the canvas each tick.
    # ------------------------------------------------------------------
    def _begin_bg_drag(self, pos: Fraction):
        self._bg_drag_start = pos
        self._bg_drag_current = pos
        self._bg_drag_moved = False
        self.bind_all("<B1-Motion>", self._on_bg_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_bg_drag_release)

    def _on_bg_drag_motion(self, event):
        if self._bg_drag_start is None:
            return
        canvas_x = event.x_root - self.canvas.winfo_rootx()
        current = self._pos_from_x(canvas_x)
        if self._bg_drag_moved and current == self._bg_drag_current:
            return
        self._bg_drag_moved = True
        self._bg_drag_current = current
        lo, hi = sorted((self._bg_drag_start, current))
        self.selected_note_ids = {id(n) for n in self.notes if lo <= n["pos"] <= hi}
        self._redraw()

    def _on_bg_drag_release(self, _event):
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        if not self._bg_drag_moved:
            self._bg_drag_start = None
            self._bg_drag_current = None
            self._deselect_all()
            return
        # Whatever was at the drag's start tick (if anything) becomes the
        # anchor for a subsequent Shift-click, same as a plain/ctrl click
        # would set — falls back to None (stale-anchor behavior) if the
        # drag started on genuinely empty space.
        anchor = self._find_note_at(self._bg_drag_start)
        self._range_anchor_id = id(anchor) if anchor is not None else None
        self._bg_drag_start = None
        self._bg_drag_current = None
        self._bg_drag_moved = False
        self._redraw()  # clears the overlay — _redraw only draws it while a drag is in progress

    def _bind_click_handlers(self, item, pos: Fraction):
        self.canvas.tag_bind(item, "<Button-1>", lambda _e, p=pos: self._on_press(p))
        self.canvas.tag_bind(item, "<Control-Button-1>", lambda _e, p=pos: self._on_ctrl_click(p))
        self.canvas.tag_bind(item, "<Shift-Button-1>", lambda _e, p=pos: self._on_shift_click(p))
        self.canvas.tag_bind(item, "<Button-3>", lambda _e, p=pos: self._on_right_click(p))

    def _bind_tail_handlers(self, item, note: dict):
        self.canvas.tag_bind(item, "<Button-1>", lambda _e, n=note: self._on_tail_press(n))
        self.canvas.tag_bind(item, "<Control-Button-1>", lambda _e, n=note: self._on_ctrl_click(n["pos"]))
        self.canvas.tag_bind(item, "<Shift-Button-1>", lambda _e, n=note: self._on_shift_click(n["pos"]))
        self.canvas.tag_bind(item, "<Button-3>", lambda _e, n=note: self._on_right_click(n["pos"]))

    # ------------------------------------------------------------------
    # Cursor-follow phantom preview — a translucent (color-blended toward
    # the canvas background; plain tk.Canvas has no real alpha, and Tk's
    # stipple bitmaps rendered near-opaque on this platform — see
    # _blend_toward_bg) note showing exactly where a click
    # would place one. Note mode only — Select mode has no placement
    # click to preview, and a phantom implying "clicking here places a
    # note" would be actively misleading there. Only shown over EMPTY
    # ticks in Note mode too, for the same reason (clicking an occupied
    # one replaces, not places). Uses state="disabled" so it never becomes
    # the "current" item under the cursor — otherwise, being drawn on top,
    # it would silently swallow clicks meant for the tick/note beneath it.
    # ------------------------------------------------------------------
    def _on_canvas_motion(self, event):
        if self._placing_note is not None:
            self._update_placing_tail(event.x)
            return
        pos = self._pos_from_x(event.x)
        if pos == self._hover_pos:
            return
        self._hover_pos = pos
        self._refresh_phantom()

    def _on_canvas_leave(self, _event=None):
        self._hover_pos = None
        self._hide_phantom()

    def _hide_phantom(self):
        if self._phantom_item is not None:
            # A slider/spinner phantom is 3 items (body/head/tail); a plain
            # note phantom is just 1 — _phantom_item holds a tuple for the
            # former, a bare item id for the latter.
            items = self._phantom_item if isinstance(self._phantom_item, tuple) else (self._phantom_item,)
            for item in items:
                self.canvas.delete(item)
            self._phantom_item = None

    @staticmethod
    def _blend_toward_bg(hex_color: str, alpha: float, bg: str = "#ffffff") -> str:
        """Blends hex_color toward bg by alpha (1=hex_color, 0=bg) and
        returns a solid hex color. Used for the phantom preview instead of
        canvas stipple — Tk's built-in stipple bitmaps (gray12/25/50/75)
        are the usual way to fake alpha on a plain tk.Canvas, but on this
        app's target platform (Windows) they were observed rendering the
        phantom essentially opaque rather than translucent. A literal
        blended solid color has no such platform dependency."""
        h1, h2 = hex_color.lstrip("#"), bg.lstrip("#")
        r1, g1, b1 = int(h1[0:2], 16), int(h1[2:4], 16), int(h1[4:6], 16)
        r2, g2, b2 = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        r = round(r1 * alpha + r2 * (1 - alpha))
        g = round(g1 * alpha + g2 * (1 - alpha))
        b = round(b1 * alpha + b2 * (1 - alpha))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _refresh_phantom(self):
        self._hide_phantom()
        if self.mode not in ("note", "special"):
            return
        if self._placing_note is not None:
            return  # the in-progress object IS the live preview now — see _update_placing_tail
        if self._hover_pos is None or self._find_note_at(self._hover_pos) is not None:
            return
        if self.mode == "note":
            x = self._beats_to_x(self._hover_pos)
            y = self._note_y()
            radius = self.FINISHER_RADIUS if self.brush_finisher else self.NORMAL_RADIUS
            color = PatternCard.KAT_COLOR if self.brush_kat else PatternCard.DON_COLOR
            faded = self._blend_toward_bg(color, self.PHANTOM_ALPHA)
            self._phantom_item = self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=faded, outline="", state="disabled")
        else:
            denom = self.DIVISOR_DENOMS[self.current_divisor]
            end_pos = self._hover_pos + Fraction(1, denom)
            if end_pos > Fraction(self.BEATS_SHOWN):
                return  # no room for a span object here — matches _place_or_replace's own check
            is_finisher = self.brush_finisher if self.special_kind == "slider" else False
            self._phantom_item = self._draw_span_sprite(
                self.special_kind, self._hover_pos, end_pos, is_finisher,
                outline="", width=0, alpha=self.PHANTOM_ALPHA, state="disabled")

    def _draw_span_sprite(self, kind, pos, end_pos, is_finisher, outline="black", width=1,
                           alpha=None, state="normal"):
        """Draws a slider/spinner sprite: a body bar between `pos` and
        `end_pos`, a full-size circle at the head, and a smaller circle
        (TAIL_RADIUS_SCALE) at the tail so it reads as a secondary marker
        rather than competing visually with the head — an invisible
        full-size circle sits under that smaller one purely as a click/drag
        target, same trick the tick grid uses for its own (much thinner)
        click targets. Colors match `kind` (see SLIDER_*/SPINNER_*
        colors). Shared by both the real (self.notes) rendering in
        _redraw and the Special-mode phantom preview — the phantom just
        passes alpha (blending the colors toward the canvas background,
        see _blend_toward_bg) and state="disabled" (see _refresh_phantom /
        the phantom docs above _on_canvas_motion).
        Returns (body_item, head_item, tail_hit_item, tail_item)."""
        head_x = self._beats_to_x(pos)
        tail_x = self._beats_to_x(end_pos)
        y = self._note_y()
        radius = self.FINISHER_RADIUS if (kind == "slider" and is_finisher) else self.NORMAL_RADIUS
        tail_radius = radius * self.TAIL_RADIUS_SCALE
        head_color = self.SLIDER_HEAD_COLOR if kind == "slider" else self.SPINNER_HEAD_COLOR
        body_color = self.SLIDER_BODY_COLOR if kind == "slider" else self.SPINNER_BODY_COLOR
        if alpha is not None:
            head_color = self._blend_toward_bg(head_color, alpha)
            body_color = self._blend_toward_bg(body_color, alpha)
        body_h = radius * 1.2
        body = self.canvas.create_rectangle(head_x, y - body_h / 2, tail_x, y + body_h / 2,
                                             fill=body_color, outline="", state=state)
        head = self.canvas.create_oval(head_x - radius, y - radius, head_x + radius, y + radius,
                                        fill=head_color, outline=outline, width=width, state=state)
        # state=state (not always "normal") matters here even though this
        # is invisible — an enabled item with no bindings still silently
        # swallows clicks if it's topmost, the same gotcha the phantom
        # preview itself has to avoid (see _refresh_phantom).
        tail_hit = self.canvas.create_oval(tail_x - radius, y - radius, tail_x + radius, y + radius,
                                            fill="white", outline="", state=state)
        tail = self.canvas.create_oval(tail_x - tail_radius, y - tail_radius, tail_x + tail_radius, y + tail_radius,
                                        fill=head_color, outline=outline, width=width, state=state)
        return body, head, tail_hit, tail

    def _redraw(self):
        self.canvas.delete("all")
        self._phantom_item = None
        denom = self.DIVISOR_DENOMS[self.current_divisor]
        self.canvas.create_line(self.MARGIN, self.TICK_BASE_Y, self._beats_to_x(self.BEATS_SHOWN),
                                 self.TICK_BASE_Y, fill="#cccccc")

        # Half the pixel gap between adjacent ticks at the current divisor,
        # used as an invisible click target around each one — wide enough
        # to comfortably click without needing pixel-perfect precision on
        # the (sometimes 2px-wide) tick line itself.
        half_slot = (self.PX_PER_BEAT / denom) / 2 - 1
        for beat in range(self.BEATS_SHOWN + 1):
            ks = range(denom) if beat < self.BEATS_SHOWN else [0]
            for k in ks:
                pos = Fraction(beat) + Fraction(k, denom)
                level = self._level_for_pos(pos)
                x = self._beats_to_x(pos)
                # Drawn UNDER the tick line (white fill blends with the
                # canvas background) so it doesn't visually cover the grid,
                # but still catches clicks across its full width — a plain
                # unfilled rectangle wouldn't register clicks in its
                # interior at all, only right on its outline.
                hit = self.canvas.create_rectangle(x - half_slot, 0, x + half_slot, self.CANVAS_H,
                                                    outline="", fill="white")
                line = self.canvas.create_line(x, self.TICK_BASE_Y, x, self.TICK_BASE_Y - self.TICK_HEIGHTS[level],
                                                fill=self.TICK_COLORS[level], width=3 if level == "black" else 2)
                self._bind_click_handlers(hit, pos)
                self._bind_click_handlers(line, pos)

        # Drawn left-to-right by position (not placement order) so a note
        # further right always ends up layered on top of one further left,
        # regardless of which was placed/moved first.
        for note in sorted(self.notes, key=lambda n: n["pos"]):
            selected = id(note) in self.selected_note_ids
            outline = self.SELECTED_OUTLINE if selected else "black"
            if note["kind"] == "note":
                x = self._beats_to_x(note["pos"])
                y = self._note_y()
                radius = self.FINISHER_RADIUS if note["is_finisher"] else self.NORMAL_RADIUS
                color = PatternCard.KAT_COLOR if note["is_kat"] else PatternCard.DON_COLOR
                item = self.canvas.create_oval(
                    x - radius, y - radius, x + radius, y + radius,
                    fill=color, outline=outline, width=3 if selected else 1)
                # Bound directly on the note circle too — it's drawn on top
                # of the tick's own hit rectangle/line, so without this a
                # click squarely on an existing note (the most common case:
                # selecting it) would hit the unbound oval and do nothing.
                self._bind_click_handlers(item, note["pos"])
            else:
                body, head, tail_hit, tail = self._draw_span_sprite(
                    note["kind"], note["pos"], note["end_pos"], note["is_finisher"],
                    outline=outline, width=3 if selected else 2)
                # Head and body both move the whole object (same pos-based
                # dispatch a plain note uses); the tail (both its visible
                # circle and the invisible full-size hit target under it)
                # gets its own path since dragging it resizes instead of
                # moving — see _on_press vs _on_tail_press.
                self._bind_click_handlers(body, note["pos"])
                self._bind_click_handlers(head, note["pos"])
                self._bind_tail_handlers(tail_hit, note)
                self._bind_tail_handlers(tail, note)

        # Box-select overlay — drawn last (on top of everything) so it
        # visually tints the ticks/notes underneath it, light gray and
        # translucent like the reference mockup. state="disabled" so it
        # can't become the canvas's "current" item mid-drag (same
        # click-swallowing gotcha the phantom preview has to avoid).
        if self._bg_drag_start is not None and self._bg_drag_moved:
            lo, hi = sorted((self._bg_drag_start, self._bg_drag_current))
            x1, x2 = self._beats_to_x(lo), self._beats_to_x(hi)
            self.canvas.create_rectangle(x1, 0, x2, self.CANVAS_H, fill="#bfbfbf",
                                          outline="", stipple="gray25", state="disabled")

        # Restore the hover preview after a full rebuild (e.g. right after
        # a click) instead of leaving it missing until the next mouse move.
        self._refresh_phantom()
        self._update_status()

    def _save(self):
        name = self.name_var.get().strip()
        if not name:
            name = logic.default_pattern_name()
        # When editing, keeping the same name (or renaming to a name no
        # other pattern has) is fine — only a collision with some *other*
        # pattern is an actual conflict.
        if any(p["name"] == name and p["name"] != self._editing_original_name
               for p in logic.load_pattern_library()):
            _show_alert(self, "Name taken", f'A pattern named "{name}" already exists.')
            return
        if not self.notes:
            _show_alert(self, "No notes", "Place at least one note first.")
            return
        notes = [{"offset_beats": float(n["pos"]), "kind": n["kind"], "is_kat": n["is_kat"],
                  "is_finisher": n["is_finisher"],
                  "end_offset_beats": float(n["end_pos"]) if n["end_pos"] is not None else None}
                 for n in self.notes]
        if self._editing_original_name is not None:
            logic.update_manual_pattern_in_gallery(self._editing_original_name, name, notes)
        else:
            logic.add_manual_pattern_to_gallery(name, notes)
        self.gallery.refresh()
        self.destroy()


# =============================================================================
class PatternGalleryFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="Pattern Gallery", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row, "Build up your own reusable pattern library")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        # ---- Library card: name/capture controls + the card gallery -------
        self.card1 = RoundedCard(self.body)
        self.card1.pack(fill="x", padx=24, pady=(0, 12))
        body1 = self.card1.body

        # Split across two rows — at a narrow enough window, cramming the
        # name field, both buttons, and the info icon onto one line pushed
        # "Manually Add Pattern" (and sometimes "Capture from osu!" too)
        # out past the tool body's actual visible width with no way to
        # reach it. `width=20` (down from the original 30) is still safely
        # under the available width even at the app's minimum window size
        # — no need for fill/expand to guarantee that here, so the field
        # can stay a normal, un-stretched size instead of ballooning to
        # fill whatever room a wide window leaves over.
        name_row = tk.Frame(body1, bg=FRONT_CARD_BG)
        name_row.pack(fill="x", pady=(0, 12))
        tk.Label(name_row, text="Pattern name:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.name_var = tk.StringVar()
        self.name_entry = _make_light_entry(name_row, textvariable=self.name_var, width=20)
        self.name_entry.pack(side="left", padx=(10, 0), ipady=4, ipadx=6)

        capture_row = tk.Frame(body1, bg=FRONT_CARD_BG)
        capture_row.pack(fill="x", pady=(0, 16))
        _make_ghost_button(capture_row, "Capture from osu!", self.capture).pack(side="left")
        capture_info = InfoIcon(capture_row, "To capture pattern from osu!, copy it and "
                                              "save the map. Then click the button to "
                                              "import pattern to your gallery")
        capture_info.configure(bg=FRONT_CARD_BG)
        capture_info.pack(side="left", padx=(8, 0))
        _make_ghost_button(capture_row, "Manually Add Pattern",
                            self.open_manual_pattern_editor).pack(side="left", padx=(15, 0))

        gallery_frame = tk.Frame(body1, bg=FRONT_CARD_BG)
        gallery_frame.pack(fill="x")
        # takefocus=0 + explicit highlightcolor: a plain click anywhere in
        # this canvas (even on an embedded PatternCard) gives it keyboard
        # focus by Tk's own default click behavior, and a focused widget's
        # highlightthickness ring switches from highlightbackground to
        # highlightcolor — which was never set here, so it fell back to
        # Tk's stock black focus color. Confirmed for real: selecting a
        # card drew a bold black rectangle around the *entire* gallery
        # strip, unrelated to the card's own border entirely. Matching
        # highlightcolor to highlightbackground makes focused/unfocused
        # render identically; takefocus=0 additionally stops this canvas
        # from taking focus at all, since it has no keyboard interaction.
        self.gallery_canvas = tk.Canvas(gallery_frame, height=140, bg=FRONT_CARD_BG,
                                         highlightthickness=1, highlightbackground=FRONT_BORDER,
                                         highlightcolor=FRONT_BORDER, takefocus=0)
        self.gallery_canvas.pack(side="top", fill="x")
        gallery_scroll = ttk.Scrollbar(gallery_frame, orient="horizontal", command=self.gallery_canvas.xview)
        gallery_scroll.pack(side="top", fill="x")
        self.gallery_canvas.configure(xscrollcommand=gallery_scroll.set)
        self.gallery_canvas.bind("<MouseWheel>", self._on_gallery_scroll)
        # Pressing on the gallery's empty background (not a card) starts a
        # range-select drag — see begin_bg_drag — or, if released without
        # ever moving over a card, just deselects (same as Ctrl+D). Bound
        # on both the canvas and the inner frame since either one's own
        # bare background can be under the press.
        self.gallery_canvas.bind("<Button-1>", self.begin_bg_drag)

        self.gallery_inner = tk.Frame(self.gallery_canvas, bg=FRONT_CARD_BG)
        self.gallery_canvas.create_window((0, 0), window=self.gallery_inner, anchor="nw")
        self.gallery_inner.bind(
            "<Configure>",
            lambda _e: self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_inner.bind("<Button-1>", self.begin_bg_drag)

        self.pattern_cards = {}        # pattern name -> PatternCard
        self.selected_pattern_names = set()
        self._ordered_names = []       # current display/library order
        self._card_drag_name = None    # name of the card currently being reorder-dragged
        self._card_drag_moved = False
        self._card_drag_start_x_root = None  # press position, to distinguish a plain click from a real drag
        self._card_drag_start_y_root = None
        self._card_drag_last_x_root = None   # most recent cursor position, reused by autoscroll ticks
        self._card_drag_last_y_root = None
        self._card_drag_offset = (0, 0)      # cursor position relative to the card's own top-left at press
        self._card_drag_target_idx = None    # insertion index (within the *other* cards) if dropped now
        self._card_drag_scroll_job = None    # after() job id for the autoscroll-while-dragging repeat
        self._card_drag_scroll_dir = None
        self._card_drag_ghost_photo = None   # keeps the ghost's PhotoImage alive (Tk drops GC'd images)
        self._bg_drag_anchor = None    # first card touched during a background-started drag
        self._bg_drag_start_x_root = None   # screen x where the background drag began, for the overlay
        # All three overlay Toplevels below are created once, up front,
        # rather than per-drag — showing a brand new always-on-top window
        # mid-drag (while the mouse button is physically held down) risks
        # interrupting the mouse capture Tk/Windows relies on to keep
        # delivering <B1-Motion> smoothly. See _create_bg_drag_overlay's
        # own docstring for the rest of it.
        self._bg_drag_overlay = self._create_bg_drag_overlay()
        self._card_drag_ghost = self._create_card_drag_ghost()
        self._card_drag_indicator = self._create_card_drag_indicator()
        self._range_anchor = None      # last plain/ctrl-clicked card — shift-click ranges from here

        # A red multi-delete button that only appears once 2+ cards are
        # selected (see _update_bulk_delete_button) — deleting a single
        # card instead uses the little badge in its own top-right corner.
        self.bulk_delete_row = tk.Frame(body1, bg=FRONT_CARD_BG)
        self.bulk_delete_row.pack(fill="x", pady=(8, 0))
        self.bulk_delete_btn = tk.Button(
            self.bulk_delete_row, bg="#d32f2f", fg="white", relief="flat",
            padx=10, pady=6, font=("Segoe UI", 10, "bold"),
            activebackground="#b71c1c", activeforeground="white",
            command=self.request_delete_selected)

        # Clicking anywhere else in the tool that isn't some other
        # interactive control also deselects (Tk doesn't bubble clicks to
        # parents, so this only fires when the click lands on body's own
        # bare background, never on a button/entry/card sitting on it).
        self.body.bind("<Button-1>", lambda _e: self.deselect_all())

        # ---- Insert-into-map card ------------------------------------------
        self.card2 = RoundedCard(self.body)
        self.card2.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        body2 = self.card2.body

        insert_header = tk.Frame(body2, bg=FRONT_CARD_BG)
        insert_header.pack(fill="x", pady=(0, 14))
        tk.Label(insert_header, text="Insert into map", bg=FRONT_CARD_BG, fg=LIGHT_ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        insert_info = InfoIcon(insert_header, "Append your pattern in your gallery to a map.")
        insert_info.configure(bg=FRONT_CARD_BG)
        insert_info.pack(side="left", padx=(6, 0))

        target_row = tk.Frame(body2, bg=FRONT_CARD_BG)
        target_row.pack(fill="x", anchor="w", pady=(0, 10))
        tk.Label(target_row, text="Target time:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        vcmd_time = (self.register(_validate_partial_time), "%P")
        self.target_time_var = tk.StringVar()
        self.target_time_entry = _make_light_entry(
            target_row, textvariable=self.target_time_var, width=15,
            validate="key", validatecommand=vcmd_time)
        self.target_time_entry.pack(side="left", padx=(10, 0), ipady=4, ipadx=6)
        self.target_time_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.target_time_var))
        target_info = InfoIcon(target_row, "This field is auto-filled after copying a timestamp.")
        target_info.configure(bg=FRONT_CARD_BG)
        target_info.pack(side="left", padx=(6, 0))

        self.match_bpm_var = tk.BooleanVar(value=True)
        match_bpm_row = tk.Frame(body2, bg=FRONT_CARD_BG)
        match_bpm_row.pack(fill="x", anchor="w", pady=(0, 12))
        LightCheckbox(match_bpm_row, "Match target map's BPM", self.match_bpm_var).pack(side="left")

        tk.Label(body2, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.diff_list = DiffRadioList(body2, app, light=True, label_inside=False)
        self.diff_list.pack(fill="both", expand=False, pady=(0, 16))

        btn_row = tk.Frame(body2, bg=FRONT_CARD_BG)
        btn_row.pack(fill="x")
        _make_accent_button(btn_row, "Insert Selected Pattern", self.insert_selected).pack(side="right")

        self._clipboard_poll_job = None
        self._last_clipboard_seen = None

    def on_shown(self):
        self.refresh()
        self.diff_list.refresh(preselect_file=self._live_diff_filename())
        self.card2.redraw()
        self._last_clipboard_seen = None
        self._poll_clipboard()
        self.bind_all("<Control-d>", self._on_ctrl_d)
        self.bind_all("<Control-D>", self._on_ctrl_d)
        self.bind_all("<Delete>", self._on_delete_key)

    def on_hidden(self):
        if self._clipboard_poll_job is not None:
            self.after_cancel(self._clipboard_poll_job)
            self._clipboard_poll_job = None
        self.unbind_all("<Control-d>")
        self.unbind_all("<Control-D>")
        self.unbind_all("<Delete>")

    def _on_ctrl_d(self, _event=None):
        self.deselect_all()

    def _on_delete_key(self, _event=None):
        # bind_all fires this even while focus is in a text field (e.g. the
        # capture-name entry or a timestamp field) — skip it there so
        # Delete still just deletes a character like normal, instead of
        # also nuking whatever gallery cards happen to be selected.
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, tk.Spinbox, tk.Text)):
            return
        # Also skip it whenever a *different* window currently holds the
        # grab — e.g. double-clicking a card to Edit it opens
        # ManualPatternWindow (grab_set()) without deselecting the card
        # behind it, so without this check, pressing Delete while that
        # editor window is focused (even on a non-text widget inside it,
        # like the divisor dropdown) would delete the very pattern being
        # edited, out from under the open editor. Checked via
        # grab_current() rather than focus_get()'s toplevel, because
        # focus_get() queried from a widget *outside* an active grab
        # reliably comes back None instead of naming the real focused
        # widget — confirmed for real, a focus_get()-based version of this
        # guard silently never fired while the grabbing editor was open.
        grabbed = self.grab_current()
        if grabbed is not None and grabbed.winfo_toplevel() is not self.winfo_toplevel():
            return
        if not self.selected_pattern_names:
            return
        if len(self.selected_pattern_names) == 1:
            self.request_delete_single(next(iter(self.selected_pattern_names)))
        else:
            self.request_delete_selected()

    def on_map_changed(self):
        self.diff_list.refresh(preselect_file=self._live_diff_filename())
        self.card2.redraw()

    def _live_diff_filename(self):
        """Best-effort: the filename of whichever difficulty is currently
        open in a running osu! stable editor, or None if that can't be
        determined (osu! not running, pymem missing, etc.) — used to
        preselect the right radio button instead of always defaulting to
        the first difficulty."""
        try:
            import osu_memory
            reader = osu_memory._get_pymem_reader()
            if reader is None:
                return None
            result = osu_memory.resolve_folder_and_filename(reader)
            return result[1] if result else None
        except Exception:
            return None

    def _poll_clipboard(self):
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            clip = None
        if clip != self._last_clipboard_seen:
            self._last_clipboard_seen = clip
            if clip and logic.parse_osu_cursor_timestamp(clip) is not None:
                result = logic.parse_time_input(clip)
                if result is not None:
                    _, cleaned = result
                    self.target_time_var.set(cleaned)
        self._clipboard_poll_job = self.after(500, self._poll_clipboard)

    def refresh(self):
        for w in self.gallery_inner.winfo_children():
            w.destroy()
        self.pattern_cards.clear()

        library = logic.load_pattern_library()
        self._ordered_names = [entry["name"] for entry in library]
        self.selected_pattern_names &= set(self._ordered_names)

        for entry in library:
            card = PatternCard(self.gallery_inner, entry, self)
            card.pack(side="left", padx=6, pady=6)
            card.set_selected(entry["name"] in self.selected_pattern_names)
            self.pattern_cards[entry["name"]] = card

        self._update_bulk_delete_button()

    def _on_gallery_scroll(self, event):
        self.gallery_canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")

    # ------------------------------------------------------------------
    # Two different drags, depending on where the press started:
    #
    # - On a card: reorders it. The card itself is unpacked and replaced
    #   by a translucent screen-grabbed "ghost" (a small overlay Toplevel,
    #   see _create_card_drag_ghost) that follows the cursor, plus a
    #   giant "I"-beam overlay (_create_card_drag_indicator) marking the
    #   gap it would land in if dropped right now; the cursor nearing
    #   either edge of the gallery's visible area auto-scrolls it
    #   (_autoscroll_check). Nothing is actually reordered until release —
    #   see _on_card_drag_release. Motion/Release are bound globally
    #   (bind_all) for the drag's duration rather than on the card itself,
    #   since the card is unpacked (and thus unmapped) for most of it, and
    #   relying on an unmapped widget's own implicit button-grab to keep
    #   delivering events isn't safe.
    # - On empty gallery space: range-selects whichever cards the drag
    #   sweeps over, anchored at the first one touched. Released without
    #   ever touching a card, it's a plain deselecting click instead.
    # ------------------------------------------------------------------
    AUTOSCROLL_MARGIN = 40  # px from the canvas's visible left/right edge that triggers autoscroll

    def begin_card_drag(self, name, event):
        self._card_drag_name = name
        self._card_drag_moved = False
        self._card_drag_target_idx = None
        self._card_drag_start_x_root = event.x_root
        self._card_drag_start_y_root = event.y_root
        self._card_drag_last_x_root = event.x_root
        self._card_drag_last_y_root = event.y_root
        card = self.pattern_cards.get(name)
        if card is not None:
            self._card_drag_offset = (event.x_root - card.winfo_rootx(), event.y_root - card.winfo_rooty())
        else:
            self._card_drag_offset = (0, 0)
        self.bind_all("<B1-Motion>", self._on_card_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_card_drag_release)

    def _cancel_card_drag(self):
        """Clears any in-progress card-reorder-drag tracking begun by
        begin_card_drag, without waiting for the matching ButtonRelease-1.
        Needed before opening a modal window from within the same click
        cycle (double-click-to-edit's 2nd press ALSO fires the ordinary
        <Button-1> binding that starts a drag) — the impending release may
        get redirected to the new window's grab instead of reaching us,
        which would otherwise leave the bind_all bindings (and
        _card_drag_name) dangling indefinitely. Also tears down whatever
        drag visuals were already showing and, since nothing is applied to
        _ordered_names until a real release, just re-packs the card that
        got unpacked at drag start straight back where it already is."""
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._stop_autoscroll()
        self._hide_card_drag_ghost()
        self._hide_card_drag_indicator()
        if self._card_drag_moved:
            self._repack_cards()
        self._card_drag_name = None
        self._card_drag_moved = False
        self._card_drag_target_idx = None
        self._card_drag_last_x_root = None
        self._card_drag_last_y_root = None

    def _on_card_drag_motion(self, event):
        self._card_drag_last_x_root = event.x_root
        self._card_drag_last_y_root = event.y_root
        if not self._card_drag_moved:
            # Small dead zone so a plain click (press+release without real
            # movement) doesn't flash the ghost/indicator for a frame.
            if abs(event.x_root - self._card_drag_start_x_root) < 4 and \
               abs(event.y_root - self._card_drag_start_y_root) < 4:
                return
            self._card_drag_moved = True
            self._start_card_drag_visuals()
        self._autoscroll_check(event.x_root)
        idx = self._compute_drop_index(event.x_root)
        self._card_drag_target_idx = idx
        # Indicator first, ghost last — _move_card_drag_ghost lifts the
        # ghost above it, so the translucent card reads as sitting on top
        # of the "I" beam rather than the beam cutting through it.
        self._update_card_drag_indicator(idx)
        self._move_card_drag_ghost(event.x_root, event.y_root)

    def _on_card_drag_release(self, _event):
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._stop_autoscroll()
        self._hide_card_drag_ghost()
        self._hide_card_drag_indicator()
        if self._card_drag_moved:
            others = [n for n in self._ordered_names if n != self._card_drag_name]
            idx = self._card_drag_target_idx if self._card_drag_target_idx is not None else len(others)
            idx = max(0, min(idx, len(others)))
            others.insert(idx, self._card_drag_name)
            self._ordered_names = others
            self._repack_cards()
            library = logic.load_pattern_library()
            by_name = {p["name"]: p for p in library}
            reordered = [by_name[n] for n in self._ordered_names if n in by_name]
            logic.save_pattern_library(reordered)
        else:
            self._set_selection({self._card_drag_name})
            self._range_anchor = self._card_drag_name
        self._card_drag_name = None
        self._card_drag_moved = False
        self._card_drag_target_idx = None
        self._card_drag_last_x_root = None
        self._card_drag_last_y_root = None

    def _start_card_drag_visuals(self):
        """Fires once a card-press turns into a real drag (past the dead
        zone in _on_card_drag_motion): snapshots the card's current pixels
        into the ghost overlay, then unpacks the real card — leaving a gap
        in the filmstrip — for the rest of the drag. _ordered_names is
        deliberately left untouched here; the actual reorder only happens
        in _on_card_drag_release, based on wherever the indicator last
        pointed."""
        card = self.pattern_cards.get(self._card_drag_name)
        if card is None:
            return
        self._show_card_drag_ghost(card)
        card.pack_forget()

    def _compute_drop_index(self, x_root):
        """Where the dragged card would land (as an index into the *other*
        cards, i.e. self._ordered_names minus the one being dragged) if
        dropped at this screen x — the first other card whose horizontal
        center the cursor is still left of, or the very end if it's past
        all of them. Pure geometry off each card's own winfo_rootx/width,
        same reasoning as _card_at: the ghost/indicator overlays sitting
        on top would confuse a hit-test-based lookup, but they don't
        affect stored widget geometry at all."""
        others = [n for n in self._ordered_names if n != self._card_drag_name]
        for i, name in enumerate(others):
            card = self.pattern_cards.get(name)
            if card is None:
                continue
            center = card.winfo_rootx() + card.winfo_width() / 2
            if x_root < center:
                return i
        return len(others)

    # -- Autoscroll while dragging near the gallery's visible edge -------
    def _autoscroll_check(self, x_root):
        canvas_x1 = self.gallery_canvas.winfo_rootx()
        canvas_x2 = canvas_x1 + self.gallery_canvas.winfo_width()
        if x_root < canvas_x1 + self.AUTOSCROLL_MARGIN:
            self._start_autoscroll(-1)
        elif x_root > canvas_x2 - self.AUTOSCROLL_MARGIN:
            self._start_autoscroll(1)
        else:
            self._stop_autoscroll()

    def _start_autoscroll(self, direction):
        if self._card_drag_scroll_job is not None and self._card_drag_scroll_dir == direction:
            return  # already autoscrolling this way
        self._stop_autoscroll()
        self._card_drag_scroll_dir = direction
        self._autoscroll_step()

    def _autoscroll_step(self):
        self.gallery_canvas.xview_scroll(self._card_drag_scroll_dir, "units")
        # Cards' own screen positions just shifted with the scroll even
        # though the cursor didn't move, so the indicator/ghost need a
        # refresh here too rather than waiting for the next real
        # <B1-Motion> (which may not come until the cursor leaves the
        # margin, autoscroll's whole reason for existing).
        if self._card_drag_last_x_root is not None:
            idx = self._compute_drop_index(self._card_drag_last_x_root)
            self._card_drag_target_idx = idx
            self._update_card_drag_indicator(idx)
            self._move_card_drag_ghost(self._card_drag_last_x_root, self._card_drag_last_y_root)
        self._card_drag_scroll_job = self.after(30, self._autoscroll_step)

    def _stop_autoscroll(self):
        if self._card_drag_scroll_job is not None:
            self.after_cancel(self._card_drag_scroll_job)
            self._card_drag_scroll_job = None
        self._card_drag_scroll_dir = None

    # -- The floating "ghost" card that follows the cursor ---------------
    def _create_card_drag_ghost(self):
        """Built once up front and toggled with deiconify/withdraw per-
        drag, same reasoning as _create_bg_drag_overlay. Alpha'd to ~60%
        opacity so it visibly reads as "being dragged" rather than a
        second solid copy of the card."""
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-alpha", 0.6)
        win.attributes("-topmost", True)
        label = tk.Label(win, bg="white", bd=0)
        label.pack(fill="both", expand=True)
        self._card_drag_ghost_label = label
        win.withdraw()
        self._make_overlay_noactivate(win)
        return win

    def _show_card_drag_ghost(self, card):
        """Grabs the real card's current on-screen pixels (PIL ImageGrab —
        Pillow's already a hard dependency, see requirements.txt) into the
        ghost overlay so it's a faithful stand-in — notes, name, snap
        divisor and all — rather than a generic placeholder. Falls back to
        a plain text label if the grab ever fails (e.g. some display-
        scaling edge case); the drag still works either way, just less
        pretty."""
        from PIL import ImageGrab, ImageTk
        x1, y1 = card.winfo_rootx(), card.winfo_rooty()
        w, h = card.winfo_width(), card.winfo_height()
        photo = None
        try:
            snap = ImageGrab.grab(bbox=(x1, y1, x1 + w, y1 + h))
            photo = ImageTk.PhotoImage(snap)
        except Exception:
            photo = None
        self._card_drag_ghost_photo = photo  # keep alive — Tk drops a GC'd PhotoImage from the label
        if photo is not None:
            self._card_drag_ghost_label.configure(image=photo, text="", bg="white")
        else:
            self._card_drag_ghost_label.configure(image="", text=self._card_drag_name, bg="white", fg="black")
        self._card_drag_ghost.geometry(f"{w}x{h}+{x1}+{y1}")
        self._card_drag_ghost.deiconify()

    def _move_card_drag_ghost(self, x_root, y_root):
        dx, dy = self._card_drag_offset
        self._card_drag_ghost.geometry(f"+{x_root - dx}+{y_root - dy}")
        # deiconify() (not lift() — see _update_card_drag_indicator's own
        # note on why) re-asserts this on top of the indicator each frame,
        # since it's always called after the indicator's own deiconify
        # within the same motion/autoscroll tick.
        self._card_drag_ghost.deiconify()

    def _hide_card_drag_ghost(self):
        self._card_drag_ghost.withdraw()
        self._card_drag_ghost_label.configure(image="", text="")
        self._card_drag_ghost_photo = None

    # -- The "I"-beam drop-position indicator -----------------------------
    def _create_card_drag_indicator(self):
        """A slim always-on-top 'I'-beam overlay (a vertical bar with caps
        top and bottom, like a text-insertion cursor) marking the gap the
        dragged card would land in if dropped right now. A separate
        overlay Toplevel rather than a canvas line for the same reason as
        _bg_drag_overlay: create_window-embedded PatternCard widgets
        always draw on top of plain canvas items regardless of stacking
        order, so a line drawn on gallery_canvas itself could end up
        hidden behind a card instead of visibly marking the gap next to
        it. Drawn once at a fixed height matching gallery_canvas's own
        fixed height — only its x position changes per drag."""
        h = 140
        w = 14
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="white")
        canvas = tk.Canvas(win, width=w, height=h, bg="white", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        color = "#2e9fd0"
        canvas.create_line(w // 2, 4, w // 2, h - 4, fill=color, width=3)
        canvas.create_line(2, 4, w - 2, 4, fill=color, width=3)
        canvas.create_line(2, h - 4, w - 2, h - 4, fill=color, width=3)
        win.withdraw()
        self._make_overlay_noactivate(win)
        return win

    def _update_card_drag_indicator(self, idx):
        others = [n for n in self._ordered_names if n != self._card_drag_name]
        canvas_x1 = self.gallery_canvas.winfo_rootx()
        canvas_x2 = canvas_x1 + self.gallery_canvas.winfo_width()
        canvas_y1 = self.gallery_canvas.winfo_rooty()
        if not others:
            x = canvas_x1 + 10
        elif idx <= 0:
            card = self.pattern_cards.get(others[0])
            x = card.winfo_rootx() - 4 if card is not None else canvas_x1 + 10
        elif idx >= len(others):
            card = self.pattern_cards.get(others[-1])
            x = card.winfo_rootx() + card.winfo_width() + 4 if card is not None else canvas_x2 - 10
        else:
            left = self.pattern_cards.get(others[idx - 1])
            right = self.pattern_cards.get(others[idx])
            if left is not None and right is not None:
                x = (left.winfo_rootx() + left.winfo_width() + right.winfo_rootx()) // 2
            else:
                x = canvas_x1 + 10
        x = max(canvas_x1, min(x, canvas_x2))
        w = 14
        self._card_drag_indicator.geometry(f"+{x - w // 2}+{canvas_y1}")
        # deiconify(), not lift() — repeatedly calling .lift() during a
        # live drag was interrupting Windows' mouse-button capture and
        # freezing the ghost mid-drag (see _move_card_drag_ghost's own
        # note). _bg_drag_overlay's own per-motion refresh already proved
        # deiconify() alone is safe to call every tick; stacking order
        # between this and the ghost instead comes from which of the two
        # was deiconified more recently — this one always runs first, see
        # _on_card_drag_motion / _autoscroll_step.
        self._card_drag_indicator.deiconify()

    def _hide_card_drag_indicator(self):
        self._card_drag_indicator.withdraw()

    def _repack_cards(self):
        for name in self._ordered_names:
            card = self.pattern_cards.get(name)
            if card is not None:
                card.pack_forget()
        for name in self._ordered_names:
            card = self.pattern_cards.get(name)
            if card is not None:
                card.pack(side="left", padx=6, pady=6)

    def _create_bg_drag_overlay(self):
        """Builds the box-select overlay Toplevel once, up front, kept
        withdrawn until a drag actually needs it — see the state comment
        where this is called from __init__. A light-gray translucent band
        matching ManualPatternWindow's own box-select overlay, drawn as a
        borderless, alpha-transparent Toplevel rather than a canvas
        rectangle: PatternCard is a real embedded Tk widget
        (create_window), and Tk always stacks "window" canvas items on top
        of ordinary drawn items regardless of creation order or
        tag_raise/tag_lower — a plain rectangle here would end up hidden
        behind every card instead of tinting them. -alpha/-topmost are
        fine to rely on since this app only ever targets Windows."""
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-alpha", 0.35)
        win.attributes("-topmost", True)
        win.configure(bg="#808080")
        win.withdraw()
        self._make_overlay_noactivate(win)
        return win

    @staticmethod
    def _make_overlay_noactivate(win):
        """WS_EX_NOACTIVATE keeps showing/moving this overlay from ever
        stealing focus or activation — without it, an always-on-top
        window appearing mid-drag risks interrupting the mouse-button
        capture Tk/Windows relies on to keep delivering <B1-Motion>
        smoothly, which is exactly what made the box-select drag feel
        unreliable before this. WS_EX_TOOLWINDOW just keeps it out of the
        taskbar/Alt-Tab. Best-effort: swallows any failure (e.g. if
        winfo_id() ever doesn't map to a real top-level HWND) since this
        is a cosmetic robustness improvement, not required for correctness."""
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            win.update_idletasks()
            hwnd = win.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def begin_bg_drag(self, event=None):
        self._bg_drag_anchor = None
        self._bg_drag_start_x_root = event.x_root if event is not None else None
        self.bind_all("<B1-Motion>", self._on_bg_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_bg_drag_release)

    def _on_bg_drag_motion(self, event):
        self._update_bg_drag_overlay(event.x_root)
        target = self._card_at(event.x_root, event.y_root)
        if target is None:
            return
        if self._bg_drag_anchor is None:
            self._bg_drag_anchor = target
            self._range_anchor = target
            self._set_selection({target})
        else:
            self._apply_range_selection(self._bg_drag_anchor, target)

    def _on_bg_drag_release(self, _event):
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._bg_drag_overlay.withdraw()
        if self._bg_drag_anchor is None:
            self.deselect_all()
        self._bg_drag_anchor = None
        self._bg_drag_start_x_root = None

    def _update_bg_drag_overlay(self, current_x_root):
        if self._bg_drag_start_x_root is None:
            return
        canvas_x1 = self.gallery_canvas.winfo_rootx()
        canvas_x2 = canvas_x1 + self.gallery_canvas.winfo_width()
        x1 = max(canvas_x1, min(self._bg_drag_start_x_root, current_x_root))
        x2 = min(canvas_x2, max(self._bg_drag_start_x_root, current_x_root))
        if x2 <= x1:
            self._bg_drag_overlay.withdraw()
            return
        y1 = self.gallery_canvas.winfo_rooty()
        height = self.gallery_canvas.winfo_height()
        self._bg_drag_overlay.geometry(f"{x2 - x1}x{height}+{x1}+{y1}")
        self._bg_drag_overlay.deiconify()

    def toggle_select(self, name):
        if name in self.selected_pattern_names:
            self.selected_pattern_names.discard(name)
        else:
            self.selected_pattern_names.add(name)
        self._range_anchor = name
        self._refresh_selection_visuals()

    def shift_select(self, name):
        """Range-selects from the last plain/ctrl-clicked card up through
        `name` — matching file-explorer shift-click. Repeated shift-clicks
        stay anchored to that same starting card, not the previous
        shift-click's target, so the range can grow or shrink freely."""
        if self._range_anchor is None or self._range_anchor not in self._ordered_names:
            self._set_selection({name})
            self._range_anchor = name
            return
        self._apply_range_selection(self._range_anchor, name)

    def deselect_all(self):
        self._range_anchor = None
        if self.selected_pattern_names:
            self.selected_pattern_names.clear()
            self._refresh_selection_visuals()

    def _apply_range_selection(self, anchor_name, target_name):
        try:
            i1 = self._ordered_names.index(anchor_name)
            i2 = self._ordered_names.index(target_name)
        except ValueError:
            return
        lo, hi = min(i1, i2), max(i1, i2)
        self._set_selection(set(self._ordered_names[lo:hi + 1]))

    def _set_selection(self, names):
        self.selected_pattern_names = set(names)
        self._refresh_selection_visuals()

    def _refresh_selection_visuals(self):
        multi = len(self.selected_pattern_names) >= 2
        for name, card in self.pattern_cards.items():
            card.set_selected(name in self.selected_pattern_names)
            if multi:
                card.hide_badge()
        self._update_bulk_delete_button()

    def _update_bulk_delete_button(self):
        n = len(self.selected_pattern_names)
        if n >= 2:
            self.bulk_delete_btn.configure(text=f"Delete {n} patterns")
            self.bulk_delete_btn.pack(anchor="e")
        else:
            self.bulk_delete_btn.pack_forget()
        # This row's own visibility just changed card1's total content
        # height (see OffsetShifterFrame.refresh for why the outer card
        # needs an explicit redraw whenever something inside it resizes).
        self.card1.redraw()

    def _card_at(self, x_root, y_root):
        """Pure geometry lookup rather than winfo_containing() — the
        box-select overlay (_update_bg_drag_overlay) is a real Toplevel
        sitting exactly over the region being dragged across, and -alpha
        transparency only affects rendering, not OS-level hit-testing, so
        winfo_containing() would find *that* window instead of the card
        underneath it (this was a real bug: selection went erratic mid-drag
        because _card_at kept returning None/the overlay while the cursor
        was over its own translucent overlay). Checking each card's own
        stored geometry directly sidesteps window stacking entirely."""
        for name in self._ordered_names:
            card = self.pattern_cards.get(name)
            if card is None or not card.winfo_ismapped():
                continue
            cx1 = card.winfo_rootx()
            cy1 = card.winfo_rooty()
            cx2 = cx1 + card.winfo_width()
            cy2 = cy1 + card.winfo_height()
            if cx1 <= x_root <= cx2 and cy1 <= y_root <= cy2:
                return name
        return None

    def _confirm_delete(self, message):
        return (not self.app.confirm_pattern_delete) or _ask_yesno(self, "Delete pattern", message)

    def request_delete_single(self, name):
        if not self._confirm_delete(f'Delete pattern "{name}"?'):
            return
        logic.delete_pattern_from_gallery(name)
        self.selected_pattern_names.discard(name)
        self.refresh()

    def request_delete_selected(self):
        names = sorted(self.selected_pattern_names)
        if not names:
            return
        listing = "\n".join(f"- {n}" for n in names)
        if not self._confirm_delete(f"Delete {len(names)} patterns?\n\n{listing}"):
            return
        for name in names:
            logic.delete_pattern_from_gallery(name)
        self.selected_pattern_names.clear()
        self.refresh()

    def open_manual_pattern_editor(self, existing_name=None):
        existing = getattr(self, "_manual_pattern_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        self._manual_pattern_win = ManualPatternWindow(self, self, existing_name=existing_name)

    def capture(self):
        name = self.name_var.get().strip()
        if not name:
            name = logic.default_pattern_name()
        if any(p["name"] == name for p in logic.load_pattern_library()):
            _show_alert(self, "Name taken", f'A pattern named "{name}" already exists.')
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            _show_alert(self, "Clipboard empty", "Nothing to read from the clipboard.")
            return
        songs_folder = getattr(self.app, "osu_songs_folder", None)
        if not songs_folder:
            _show_alert(self, "No Songs folder", "Set your osu! Songs folder first (Settings).")
            return
        if not _ask_okcancel(self,
                "Save your map first",
                "Save your map (Ctrl+S) before proceeding!"):
            return
        try:
            _entry, truncated, had_concurrent = logic.capture_pattern_from_osu_selection(name, clip, songs_folder)
        except ValueError as e:
            _show_alert(self, "Couldn't capture pattern", str(e))
            return
        self.name_var.set("")
        self.refresh()
        if had_concurrent:
            _show_alert(self,
                "Concurrent notes dropped",
                f'"{name}" had two or more notes sharing the same timestamp — '
                f"only one was kept at each of those times, the rest were discarded.")
        if truncated:
            max_beats = int(logic.CAPTURED_PATTERN_MAX_BEATS)
            _show_alert(self,
                "Pattern truncated",
                f'"{name}" was longer than {max_beats} beats — notes past that '
                f"point were discarded so the pattern stays a short, reusable snippet.")
        self.notify_done(f'Captured pattern "{name}".')

    def delete_selected(self):
        name = self.selected_pattern_name
        if not name:
            _show_alert(self, "Nothing selected", "Select a pattern to delete first.")
            return
        if not _ask_yesno(self, "Delete pattern", f'Delete pattern "{name}"?'):
            return
        logic.delete_pattern_from_gallery(name)
        self.refresh()

    def insert_selected(self):
        if not self.require_map():
            return
        if not self.selected_pattern_names:
            _show_alert(self, "Nothing selected", "Select a pattern to insert first.")
            return
        if len(self.selected_pattern_names) > 1:
            _show_alert(self, "Multiple patterns selected", "Select exactly one pattern to insert.")
            return
        name = next(iter(self.selected_pattern_names))
        pattern = logic.get_pattern(name)
        if pattern is None:
            _show_alert(self, "Not found", "That pattern no longer exists.")
            self.refresh()
            return

        target_file = self.diff_list.selected()
        if not target_file:
            _show_alert(self, "Nothing selected", "Choose a difficulty to insert into.")
            return

        target_result = logic.parse_time_input(self.target_time_var.get())
        if target_result is None:
            _show_alert(self, "Warning", "Invalid timestamp input")
            return
        target_ms, cleaned = target_result
        self.target_time_var.set(cleaned)

        if not _ask_okcancel(self,
                "Save your map first",
                "Save your map (Ctrl+S) before proceeding!"):
            return

        folder, _ = self.app.get_diff_files()
        logic.insert_pattern_into_map(folder, [target_file], pattern, target_ms, self.match_bpm_var.get())
        self.notify_done("Success! Press Ctrl + L in the editor to load the pattern")


# =============================================================================
class FileNameCheckerFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="File Name Checker", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row,
                                "Check and rename the mismatched capitalisation in file "
                                "name compared to the map's metadata.")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        tk.Label(self.body, text="Only use this tool if your file names' capitalisation is "
                                  "mismatched from the map's metadata.",
                 bg=FRONT_BG, fg=FRONT_TEXT_MUTED, font=("Segoe UI", 11),
                 justify="left").pack(anchor="w", padx=24, pady=(4, 16))

        self.card = RoundedCard(self.body)
        self.card.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        body = self.card.body

        tk.Label(body, text="Apply to:", bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        self.diff_list = DiffCheckList(body, app, light=True, label_inside=False)
        self.diff_list.pack(fill="both", expand=True, pady=(0, 16))

        # "word" wrap can't break a single unbroken "word" — and a file
        # name here often has no spaces (underscores instead), so a long
        # one would just run past the widget's right edge instead of
        # wrapping. "char" guarantees a wrap point regardless.
        result_frame = tk.Frame(body, bg=FRONT_CARD_BG, highlightthickness=1,
                                 highlightbackground=FRONT_BORDER, highlightcolor=FRONT_BORDER)
        result_frame.pack(fill="both", expand=False, pady=(0, 16))
        self.result_text = tk.Text(result_frame, height=10, wrap="char", font=("Segoe UI", 11),
                                    relief="flat", bd=0, bg=FRONT_CARD_BG, fg=FRONT_TEXT,
                                    insertbackground=FRONT_TEXT, padx=10, pady=8)
        self.result_text.pack(fill="both", expand=True)

        btns = tk.Frame(body, bg=FRONT_CARD_BG)
        btns.pack(fill="x")
        _make_accent_button(btns, "Rename", self.apply).pack(side="right")
        _make_ghost_button(btns, "Check", self.check).pack(side="right", padx=(0, 8))

    def on_shown(self):
        self.diff_list.refresh()
        self.card.redraw()
        self.result_text.delete("1.0", "end")

    def on_map_changed(self):
        self.diff_list.refresh()
        self.card.redraw()

    def check(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        results = logic.check_filenames(folder, targets)
        self.result_text.delete("1.0", "end")
        for r in results:
            if r["ok"]:
                self.result_text.insert("end", f"OK: {r['file']}\n")
            else:
                self.result_text.insert("end", f"MISMATCH: {r['file']}\n")
                for issue in r["issues"]:
                    self.result_text.insert("end", f"    - {issue}\n")
                self.result_text.insert("end", f"    expected: {r['expected']}\n")

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        targets = self.diff_list.selected()
        renamed = logic.apply_filename_fixes(folder, targets)
        self.notify_done(f"Renamed {renamed} file(s) to match metadata capitalisation.")
        self.diff_list.refresh()
        self.card.redraw()
        self.result_text.delete("1.0", "end")


# =============================================================================
class EarlyVolumeSettingFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        _style_light_body(self)

        header_row = tk.Frame(self.body, bg=FRONT_BG)
        header_row.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(header_row, text="Early Volume Settings", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 20, "bold")).pack(side="left")
        header_info = InfoIcon(header_row,
                                "Shift the volume lines backward to keep hitsounds "
                                "synchronized when players hit notes too early.")
        header_info.configure(bg=FRONT_BG)
        header_info.pack(side="left", padx=(6, 0))

        vcmd_time = (self.register(_validate_partial_time), "%P")

        diff_row = tk.Frame(self.body, bg=FRONT_BG)
        diff_row.pack(fill="x", padx=24, pady=(4, 16))
        tk.Label(diff_row, text="Selected diff:", bg=FRONT_BG, fg=FRONT_TEXT,
                 font=("Segoe UI", 11)).pack(side="left")
        self.diff_var = tk.StringVar()
        self.diff_combo = LightDropdown(diff_row, self.diff_var, width=25)
        self.diff_combo.pack(side="left", padx=(10, 0))
        self.diff_map = {}

        # Restores whatever was last set here (see _persist_settings) —
        # this whole form otherwise reset to the same hardcoded defaults
        # every relaunch, forcing a re-enter of thresholds/section range
        # that's usually the same across sessions for a given mapper.
        saved = self.app.early_volume_settings
        self.volume_threshold_var = tk.StringVar(value=saved["volume_threshold"])
        self.early_threshold_var = tk.StringVar(value=saved["early_threshold"])
        self.section_only_var = tk.BooleanVar(value=saved["section_only"])
        self.from_var = tk.StringVar(value=saved["from"])
        self.to_var = tk.StringVar(value=saved["to"])
        for var in (self.volume_threshold_var, self.early_threshold_var,
                    self.section_only_var, self.from_var, self.to_var):
            var.trace_add("write", self._persist_settings)

        card = RoundedCard(self.body)
        card.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        opts = card.body

        def info(parent, text, **kw):
            icon = InfoIcon(parent, text, **kw)
            icon.configure(bg=FRONT_CARD_BG)
            return icon

        def row(indent=0, pady=6):
            r = tk.Frame(opts, bg=FRONT_CARD_BG)
            r.pack(fill="x", anchor="w", padx=(indent, 0), pady=pady)
            return r

        def label(parent, text, **kw):
            defaults = dict(bg=FRONT_CARD_BG, fg=FRONT_TEXT, font=("Segoe UI", 11))
            defaults.update(kw)
            return tk.Label(parent, text=text, **defaults)

        # Independently packed rows, not a shared grid — same label width
        # on both (fixed to the longer text's own character count,
        # left-anchored) so the two dropdowns' left edges still line up
        # despite that, same fix as MetadataManagerFrame's Preview Point
        # misalignment (see CLAUDE.md).
        THRESHOLD_LABEL_W = len("Volume change threshold")

        r1 = row()
        label(r1, "Volume change threshold", width=THRESHOLD_LABEL_W, anchor="w").pack(side="left")
        LightDropdown(r1, self.volume_threshold_var, values=logic.VOLUME_THRESHOLD_CHOICES,
                      width=6, page_bg=FRONT_CARD_BG).pack(side="left", padx=8)
        info(r1, "Sets the minimum volume change required to move a "
                 "volume line backward.").pack(side="left")

        r2 = row()
        label(r2, "Early volume threshold", width=THRESHOLD_LABEL_W, anchor="w").pack(side="left")
        LightDropdown(r2, self.early_threshold_var, values=logic.EARLY_THRESHOLD_CHOICES,
                      width=6, page_bg=FRONT_CARD_BG).pack(side="left", padx=8)
        info(r2, "Sets how far a volume line is shifted backward "
                 "relative to the note.").pack(side="left")

        r3 = row(pady=(14, 6))
        self.section_only_cb = LightCheckbox(
            r3, "Apply to this section only", self.section_only_var,
            command=self._sync_section_state)
        self.section_only_cb.pack(side="left")

        r4 = row(indent=24)
        self.from_label = label(r4, "From", fg=FRONT_TEXT_MUTED)
        self.from_label.pack(side="left")
        self.from_entry = _make_light_entry(
            r4, textvariable=self.from_var, width=15,
            validate="key", validatecommand=vcmd_time, state="disabled")
        self.from_entry.pack(side="left", padx=5, ipady=4)
        self.from_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.from_var))
        self.to_label = label(r4, "to", fg=FRONT_TEXT_MUTED)
        self.to_label.pack(side="left")
        self.to_entry = _make_light_entry(
            r4, textvariable=self.to_var, width=15,
            validate="key", validatecommand=vcmd_time, state="disabled")
        self.to_entry.pack(side="left", padx=5, ipady=4)
        self.to_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.to_var))

        btn_row = tk.Frame(opts, bg=FRONT_CARD_BG)
        btn_row.pack(fill="x", pady=(8, 0))
        _make_accent_button(btn_row, "Apply", self.apply).pack(side="right")

        # section_only_cb's own command= only fires on a user click — this
        # syncs the From/To fields' enabled state to match whatever was
        # actually loaded from disk above, since that can now be True on a
        # fresh open with nothing clicked yet.
        self._sync_section_state()

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh(sync_to_current=True)

    def refresh(self, sync_to_current=False):
        folder, diffs = self.app.get_diff_files()
        self.diff_map = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        labels = sorted(self.diff_map.keys(), key=osu_parser.taiko_diff_sort_key)
        self.diff_combo.set_values(labels)

        target_label = None
        if sync_to_current:
            current_fname = getattr(self.app, "current_diff_filename", None)
            if current_fname:
                for label, fname in self.diff_map.items():
                    if fname == current_fname:
                        target_label = label
                        break

        if target_label:
            self.diff_var.set(target_label)
        elif labels and self.diff_var.get() not in labels:
            self.diff_var.set(labels[0])

    def _sync_section_state(self):
        enabled = self.section_only_var.get()
        state = "normal" if enabled else "disabled"
        self.from_entry.configure(state=state)
        self.to_entry.configure(state=state)
        fg = FRONT_TEXT if enabled else FRONT_TEXT_MUTED
        self.from_label.configure(fg=fg)
        self.to_label.configure(fg=fg)

    def _persist_settings(self, *_args):
        self.app.save_early_volume_settings({
            "volume_threshold": self.volume_threshold_var.get(),
            "early_threshold": self.early_threshold_var.get(),
            "section_only": self.section_only_var.get(),
            "from": self.from_var.get(),
            "to": self.to_var.get(),
        })

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        fname = self.diff_map.get(self.diff_var.get())
        if not fname:
            _show_alert(self, "No difficulty", "Choose a difficulty.")
            return
        targets = [fname]

        volume_threshold = float(self.volume_threshold_var.get().replace("%", ""))
        early_threshold = float(self.early_threshold_var.get().replace("ms", ""))

        section = None
        if self.section_only_var.get():
            from_result = logic.parse_time_input(self.from_var.get())
            to_result = logic.parse_time_input(self.to_var.get())
            if from_result is None or to_result is None:
                _show_alert(self, "Warning", "Invalid timestamp input")
                return
            from_ms, from_clean = from_result
            to_ms, to_clean = to_result
            self.from_var.set(from_clean)
            self.to_var.set(to_clean)
            section = (min(from_ms, to_ms), max(from_ms, to_ms))

        logic.apply_early_volume_setting(folder, targets, volume_threshold, early_threshold, section)
        self.notify_done("Early volume setting applied.")
