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
from tkinter import ttk, messagebox, filedialog, simpledialog

import osu_parser
import tools_logic as logic

DIVISORS = ["1/1", "1/2", "1/4", "1/6", "1/12", "1/24", "1/36", "1/48"]


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
    dialog closed every time they hit Apply."""
    top = widget.winfo_toplevel()
    toast = tk.Toplevel(top)
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
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


def _ask_choice_dialog(parent, title: str, message: str, options: list) -> str:
    """Generic modal prompt with a message and one full-width button per
    (label, value) in `options` — plain `messagebox` dialogs don't support
    custom button labels, so this is a small dedicated Toplevel instead.
    Blocks until closed (via `wait_window`) and returns whichever value
    was clicked; closing via the window's X button or Escape returns
    `options[-1][1]` (every caller puts "cancel" last, matching how a
    dismissed dialog should always read as "cancel")."""
    cancel_value = options[-1][1]
    result = {"choice": cancel_value}
    win = tk.Toplevel(parent)
    win.title(title)
    win.resizable(False, False)

    body = ttk.Frame(win)
    body.pack(fill="both", expand=True, padx=18, pady=16)
    ttk.Label(body, text=message, wraplength=360, justify="left").pack(anchor="w", pady=(0, 14))

    def choose(value):
        result["choice"] = value
        win.destroy()

    for i, (label, value) in enumerate(options):
        ttk.Button(body, text=label, command=lambda v=value: choose(v)).pack(
            fill="x", pady=(2, 0) if i == len(options) - 1 else 2)

    win.protocol("WM_DELETE_WINDOW", lambda: choose(cancel_value))
    win.bind("<Escape>", lambda e: choose(cancel_value))
    win.transient(parent.winfo_toplevel())
    _position_over_window(win, parent, width=400)
    win.lift()
    win.focus_force()
    win.grab_set()
    parent.wait_window(win)
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


VLC_DOWNLOAD_URL = "https://www.videolan.org/vlc/"
FFMPEG_MANUAL_URL = "https://ffmpeg.org/download.html"


def _make_inline_link(parent, url):
    """A small clickable label meant to sit inline within a sentence built
    from several packed Labels — the URL itself is never shown, only the
    word/phrase this is attached to."""
    link = tk.Label(parent, fg="#0066cc", cursor="hand2")
    f = link.cget("font")
    link.configure(font=(f, 11, "underline") if isinstance(f, str) else f)
    link.bind("<Button-1>", lambda e: webbrowser.open(url))
    return link


def _run_bundled_install(master, win, button, install_fn, tool_name, busy_msg):
    """Runs one of tools_logic's `install_*_bundled` functions on a worker
    thread from inside `show_troubleshoot_window`. `master` is always the
    `BaseToolFrame` the troubleshoot window was opened from (its `.body`
    IS the frame itself — see `BaseToolFrame.__init__` — so `.app` is
    always reachable for the shared busy overlay), and `button` is
    disabled with its own text swapped to "Installing..." for the
    duration so a second click can't start an overlapping install.
    Reports the result via a plain `messagebox` — this popup doesn't use
    `notify_done`'s toast since it isn't tied to any particular tool
    screen being visible."""
    button.configure(state="disabled", text="Installing...")
    original_text = f"Install {tool_name} automatically"

    def work(cancel_event):
        install_fn()

    def reset_button():
        if button.winfo_exists():
            button.configure(state="normal", text=original_text)

    def on_success(_result):
        reset_button()
        if win.winfo_exists():
            messagebox.showinfo(tool_name, f"{tool_name} installed successfully.")

    def on_error(err_msg):
        reset_button()
        if win.winfo_exists():
            messagebox.showerror(tool_name, err_msg)

    master.app.run_cancellable_job(busy_msg, work, on_success=on_success, on_error=on_error,
                                    on_cancel=reset_button, cancelled_toast="Installation Cancelled!")


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
        self.resizable(False, False)
        self.on_apply = on_apply
        self.points_spec = points_spec
        self.coords = {p["key"]: tuple(initial_coords.get(p["key"], (256, 192))) for p in points_spec}
        self._point_items = {}
        self._dragging_key = None

        grid_w = self.GRID_COLS * self.CELL_PX
        grid_h = self.GRID_ROWS * self.CELL_PX

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)

        labels_frame = ttk.Frame(top)
        labels_frame.pack(side="left", fill="x", expand=True)
        per_col = 2 if len(points_spec) > 2 else len(points_spec)
        num_cols = -(-len(points_spec) // per_col)  # ceil division
        col_frames = []
        for _c in range(num_cols):
            cf = ttk.Frame(labels_frame)
            cf.pack(side="left", padx=(0, 30))
            col_frames.append(cf)

        self.label_vars = {}
        for i, p in enumerate(points_spec):
            var = tk.StringVar()
            self.label_vars[p["key"]] = var
            ttk.Label(col_frames[i // per_col], textvariable=var, font=("Segoe UI", 14)).pack(anchor="w")

        ttk.Button(top, text="Apply", command=self._apply).pack(side="right", anchor="n")

        self.canvas = tk.Canvas(self, width=grid_w + 2 * self.MARGIN, height=grid_h + 2 * self.MARGIN,
                                 bg="white", highlightthickness=1, highlightbackground="black")
        self.canvas.pack(padx=12, pady=(0, 12))

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
        self.canvas.create_line(cx, self.MARGIN, cx, self.MARGIN + grid_h, fill="black", width=2)
        self.canvas.create_line(self.MARGIN, cy, self.MARGIN + grid_w, cy, fill="black", width=2)

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


def show_troubleshoot_window(master):
    """Behind every "The tool is not working?" link — the video preview
    and taiko video resizer both depend on external programs (VLC, FFmpeg)
    that aren't bundled, so this is where we point people at getting those
    installed."""
    existing = getattr(master, "_troubleshoot_win", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return
    win = tk.Toplevel(master)
    win.title("The tool is not working?")

    body = ttk.Frame(win)
    body.pack(fill="both", expand=True, padx=16, pady=16)

    ttk.Label(body, text="The video offset preview is not working!",
              font=("Segoe UI", 18, "bold"), wraplength=580, justify="left").pack(anchor="w", pady=(0, 6))
    ttk.Label(body, text="VLC is required for this. Install it here:",
              wraplength=580, justify="left").pack(anchor="w", pady=(0, 6))
    vlc_btn = ttk.Button(body, text="Install VLC automatically")
    vlc_btn.configure(command=lambda: _run_bundled_install(
        master, win, vlc_btn, logic.install_vlc_bundled, "VLC",
        "Installing VLC (may take a few minutes)... Please wait..."))
    vlc_btn.pack(anchor="w", pady=(0, 4))
    vlc_manual_link = _make_inline_link(body, VLC_DOWNLOAD_URL)
    vlc_manual_link.configure(text="Or download it manually")
    vlc_manual_link.pack(anchor="w")

    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)

    ttk.Label(body, text="The taiko video resizer is not working!",
              font=("Segoe UI", 18, "bold"), wraplength=580, justify="left").pack(anchor="w", pady=(0, 6))
    ttk.Label(body, text="FFmpeg is required to use this feature. Install it here:",
              wraplength=580, justify="left").pack(anchor="w", pady=(0, 6))
    ffmpeg_btn = ttk.Button(body, text="Install FFmpeg automatically")
    ffmpeg_btn.configure(command=lambda: _run_bundled_install(
        master, win, ffmpeg_btn, logic.install_ffmpeg_suite_bundled, "FFmpeg",
        "Installing ffmpeg + ffprobe (may take a few minutes)... Please wait..."))
    ffmpeg_btn.pack(anchor="w", pady=(0, 4))
    manual_link = _make_inline_link(body, FFMPEG_MANUAL_URL)
    manual_link.configure(text="Or download it manually")
    manual_link.pack(anchor="w")

    master._troubleshoot_win = win
    win.bind("<Escape>", lambda e: win.destroy())
    win.transient(master)
    _position_over_window(win, master, width=620, height=440)
    win.lift()
    win.focus_force()
    win.grab_set()


def make_scrollable_toplevel_body(win):
    """Wraps a Toplevel's content in a vertically-scrolling canvas — for a
    window whose content can end up taller than the screen (most notably
    Settings, once the font-size option is cranked way up — see Settings
    section 3), so whatever's below the fold (often the Apply/Restart row)
    stays reachable via scrollbar/mouse wheel instead of running off-screen
    with no way back. The scrollbar only appears once content actually
    overflows the window, so a window that already fits looks exactly as
    it did before. Returns a `ttk.Frame` to parent content into, as a
    drop-in replacement for a plain `ttk.Frame(win)` — including a
    `padding` option to reproduce whatever padx/pady margin the caller
    used to apply on the old frame's own `.pack()` call, since that now
    has to live on the frame itself rather than on how it's packed into
    `win`. Mouse wheel is bound only while the pointer is over `win` (the
    same Enter/Leave-toggled bind_all trick as SongSearchResultsWindow —
    needed since the canvas itself ends up almost entirely covered by
    embedded content, so binding wheel scroll on the bare canvas alone
    doesn't work in practice)."""
    outer = ttk.Frame(win)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, highlightthickness=0,
                        bg=ttk.Style().lookup("TFrame", "background") or None)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)

    body = ttk.Frame(canvas)
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


def add_apply_row(parent, command, button_text="Apply"):
    """Packs a right-anchored row with a 'The tool is not working?' help
    link next to the Apply button — the standard pattern at the bottom of
    every tool screen."""
    row = ttk.Frame(parent)
    row.pack(anchor="e", fill="x", padx=10, pady=10)
    ttk.Button(row, text=button_text, command=command).pack(side="right")
    link = tk.Label(row, text="The tool is not working?", fg="#0066cc", cursor="hand2")
    link.pack(side="right", padx=(0, 12))
    f = link.cget("font")
    link.configure(font=(f, 10, "underline") if isinstance(f, str) else f)
    link.bind("<Button-1>", lambda e: show_troubleshoot_window(parent))
    return row


class SongSearchResultsWindow(tk.Toplevel):
    """A scrollable list of search results, each showing a small background
    thumbnail plus "Artist - Title", so the user can quickly spot and pick
    the song they meant even among similarly-named results. Clicking a row
    selects that map and closes the window."""

    THUMB_W, THUMB_H = 64, 36
    MAX_ROWS_VISIBLE = 8

    def __init__(self, app, matches, on_select):
        super().__init__(app)
        self.title(f"Search results ({len(matches)})")
        self.resizable(False, True)
        self.on_select = on_select
        self._thumb_images = []  # keep references so Tk doesn't GC them
        self._row_labels = []    # (entry, text_label) pairs, for the あ toggle
        self.use_romanised = False

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="あ", width=3, command=self.toggle_display).pack(side="right", padx=4, pady=4)

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)

        self._canvas = canvas = tk.Canvas(outer, width=480, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
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
        _position_over_window(self, app, width=500, height=visible_rows * row_h + 30)

        # Bring the window to front and give it real keyboard/focus so it
        # doesn't open silently behind the main window, and so the mouse
        # wheel binding above (scoped to <Enter>) actually gets a chance
        # to engage as soon as the pointer is over it. grab_set() keeps it
        # focused/on top for as long as it's open, matching the other
        # popups (Settings, BG/Video preview, troubleshoot).
        self.transient(app)
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
        row = tk.Frame(parent, cursor="hand2", bg="white")
        row.pack(fill="x", pady=1)

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
                         bg="white", font=("Segoe UI", 12))
        text.pack(side="left", fill="x", expand=True, padx=6)
        self._row_labels.append((entry, text))

        def select(_event=None, folder=entry["folder"]):
            self.on_select(folder)
            self.destroy()

        for widget in (row, thumb_label, text):
            widget.bind("<Button-1>", select)
            widget.bind("<Enter>", lambda e, r=row, t=text: (r.configure(bg="#e6f0ff"),
                                                              t.configure(bg="#e6f0ff")))
            widget.bind("<Leave>", lambda e, r=row, t=text: (r.configure(bg="white"),
                                                              t.configure(bg="white")))

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


# =============================================================================
class DiffCheckList(ttk.LabelFrame):
    """The recurring 'Apply to: [x] Diff1 [x] Diff2 ...' widget, ticked by
    default. Displays each difficulty's [Version] name (e.g. "Oni") rather
    than the full filename, for compactness."""

    def __init__(self, master, app, label="Apply to:"):
        super().__init__(master, text=label)
        self.app = app
        self.vars = {}          # display label -> BooleanVar
        self.label_to_file = {}  # display label -> filename
        self.inner = ttk.Frame(self)
        self.inner.pack(fill="both", expand=True)

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
            cb = ttk.Checkbutton(self.inner, text=label, variable=v)
            row = i % self.MAX_ROWS_PER_COLUMN
            col = i // self.MAX_ROWS_PER_COLUMN
            cb.grid(row=row, column=col, sticky="w", padx=(0, 16), pady=1)
            self.vars[label] = v

    def selected(self):
        return [self.label_to_file[label] for label, v in self.vars.items() if v.get()]


class DiffRadioList(ttk.LabelFrame):
    """Single-select variant of DiffCheckList — radio buttons instead of
    checkboxes, for a tool that only ever targets one difficulty at a
    time (unlike the usual "apply to several diffs at once" pattern
    DiffCheckList is for). Supports preselecting a specific difficulty
    (e.g. whichever one is actually open in a live osu! editor) instead
    of always defaulting to the alphabetically/priority-first one."""

    def __init__(self, master, app, label="Apply to:"):
        super().__init__(master, text=label)
        self.app = app
        self.label_to_file = {}
        self.var = tk.StringVar()
        self.inner = ttk.Frame(self)
        self.inner.pack(fill="both", expand=True)

    MAX_ROWS_PER_COLUMN = 3

    def refresh(self, preselect_file: str = None):
        for w in self.inner.winfo_children():
            w.destroy()
        folder, diffs = self.app.get_diff_files()
        self.label_to_file = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        ordered_labels = sorted(self.label_to_file.keys(), key=osu_parser.taiko_diff_sort_key)
        for i, label in enumerate(ordered_labels):
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
            messagebox.showwarning("No map selected", "Please select a map first!")
            return False
        if not diffs:
            messagebox.showwarning("No difficulties found", f"No .osu files found in:\n{folder}")
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

        footer = ttk.Frame(self.body)
        footer.pack(side="bottom", pady=(0, 10))
        credit_row = ttk.Frame(footer)
        credit_row.pack()
        ttk.Label(credit_row, text="From Amasugi ❤ ", font=("Segoe UI", 9)).pack(side="left")
        credit_link = tk.Label(credit_row, text="App Icon", font=("Segoe UI", 9, "underline"),
                                fg="#3366cc", cursor="hand2")
        credit_link.pack(side="left")
        credit_link.bind("<Button-1>", lambda e: webbrowser.open(self.CREDIT_URL))
        ttk.Label(footer, text=f"App Version: {getattr(app, 'app_version', '?')}",
                  font=("Segoe UI", 8), foreground="#999999").pack(pady=(4, 0))

        content = ttk.Frame(self.body)
        content.pack(expand=True)

        logo = self._load_logo_image()
        if logo is not None:
            self._logo_image = logo  # kept alive on self, not just this scope
            ttk.Label(content, image=logo).pack(pady=(0, 20))

        ttk.Label(content, text="osu!taiko Mapping Tools",
                  font=("Segoe UI", 26)).pack()
        ttk.Label(content, text="Made by osu!taiko mapper, for osu!taiko mappers",
                  font=("Segoe UI", 12), foreground="#666666").pack(pady=(6, 0))

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
    def __init__(self, master, app):
        super().__init__(master, app)
        add_header(self.body, "Metadata Manager",
                   "Import metadata from selected mapset, and apply changed "
                   "metadata to all difficulties in the set (or a few of "
                   "them, if you're into it)")

        row0 = ttk.Frame(self.body)
        row0.pack(anchor="w", padx=10, pady=10)
        ttk.Button(row0, text="Import Meta", command=self.import_meta).pack(side="left")

        form = ttk.Frame(self.body)
        form.pack(fill="x", padx=10)
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
            ttk.Label(form, text=label + ":").grid(row=i, column=0, sticky="w", pady=3)
            e = ttk.Entry(form, width=60)
            e.grid(row=i, column=1, sticky="we", pady=3, padx=5)
            self.fields[key] = e
        form.columnconfigure(1, weight=1)

        # Tags gets its own multi-line box (room for ~5 lines of text)
        tags_row = len(labels)
        ttk.Label(form, text="Tags:").grid(row=tags_row, column=0, sticky="nw", pady=3)
        tags_frame = ttk.Frame(form)
        tags_frame.grid(row=tags_row, column=1, sticky="we", pady=3, padx=5)
        self.tags_text = tk.Text(tags_frame, width=60, height=5, wrap="word", font=("Segoe UI", 14))
        tags_scroll = ttk.Scrollbar(tags_frame, orient="vertical", command=self.tags_text.yview)
        self.tags_text.configure(yscrollcommand=tags_scroll.set)
        self.tags_text.pack(side="left", fill="both", expand=True)
        tags_scroll.pack(side="right", fill="y")

        pf = ttk.Frame(self.body)
        pf.pack(fill="x", padx=10, pady=5)
        ttk.Label(pf, text="Preview Point:").pack(side="left")
        self.preview_point = ttk.Entry(pf, width=15)
        self.preview_point.pack(side="left", padx=5)

        self.diff_list = DiffCheckList(self.body, app)
        self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Button(self.body, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

    def on_shown(self):
        self.diff_list.refresh()
        self._auto_import()

    def on_map_changed(self):
        self.diff_list.refresh()
        self._auto_import()

    def _auto_import(self):
        """Silently fills the fields from whatever map is currently
        loaded — called whenever this tool is shown or the loaded map
        changes (picked up from osu!, browsed to manually, or found via
        search), so the fields are never stale/empty without the user
        having to remember to click Import Meta. Does nothing (no warning
        popup) if no map is loaded yet."""
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
            messagebox.showwarning("Nothing selected", "Tick at least one difficulty.")
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
    copying, each as its own bordered section."""

    def __init__(self, master, owner, title, source_label_text, copy_func, noun, info_text=None):
        super().__init__(master, relief="groove", borderwidth=1)
        self.owner = owner  # hosting BaseToolFrame (for require_map/notify_done/app)
        self.copy_func = copy_func
        self.noun = noun

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
        self.source_combo["values"] = labels
        if labels and self.source_var.get() not in labels:
            self.source_var.set(labels[0])
        self.diff_list.refresh()

    def apply(self):
        if not self.owner.require_map():
            return
        folder, _ = self.owner.app.get_diff_files()
        source_label = self.source_var.get()
        source = self.diff_map.get(source_label)
        targets = self.diff_list.selected()
        if not source:
            messagebox.showwarning("No source", f"Choose a difficulty to copy {self.noun.lower()} from.")
            return
        if not targets:
            messagebox.showwarning("Nothing selected", "Tick at least one difficulty.")
            return
        self.copy_func(folder, source, targets)
        self.owner.notify_done(f"{self.noun} copied from {source_label} to {len(targets)} difficulty file(s).")


# =============================================================================
class VolumeKiaiCopierFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        add_header(self.body, "Volume/Kiai Copier")

        self.volume_section = CopySection(
            self.body, self, "Volume Copier", "Copy volume from:", logic.copy_volumes, "Volume",
            info_text="Copy the volume changes of a difficulty and apply them "
                       "to any difficulties in the set.")
        self.volume_section.pack(fill="both", expand=True, padx=10, pady=(10, 5))
        self.kiai_section = CopySection(
            self.body, self, "Kiai Copier", "Copy kiai from:", logic.copy_kiai, "Kiai",
            info_text="Copy the kiai portions of a difficulty and apply them "
                       "to any difficulties in the set.")
        self.kiai_section.pack(fill="both", expand=True, padx=10, pady=(5, 10))

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
        add_header(self.body, "Map Cleaner",
                   "Make the map looks prettier and snappier in the editor "
                   "without fundamentally changing how the map is played.")

        row = ttk.Frame(self.body)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="Selected diff:").pack(side="left")
        self.diff_var = tk.StringVar()
        self.diff_combo = ttk.Combobox(row, textvariable=self.diff_var, state="readonly", width=25)
        self.diff_combo.pack(side="left", padx=5)
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

        opts = ttk.Frame(self.body)
        opts.pack(fill="x", padx=10, pady=2, anchor="w")

        r1 = ttk.Frame(opts)
        r1.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r1, text="Resnap all notes", variable=self.resnap_notes_var,
                         command=self._sync_resnap_notes_state).pack(side="left")
        self.divisor_combo = ttk.Combobox(r1, textvariable=self.snap_divisor_var, values=DIVISORS,
                                           state="readonly", width=8)
        self.divisor_combo.pack(side="left", padx=8)
        InfoIcon(r1, "- 1/12 = 1/4 + 1/6\n- 1/24 = 1/8 + 1/12\n- 1/36 = 1/4 + 1/6 + 1/9\n"
                     "- 1/48 = 1/12 + 1/16").pack(side="left")

        # Resnap Child Option: Apply to this section only
        r1_section = ttk.Frame(opts)
        r1_section.pack(fill="x", anchor="w", padx=(24, 0), pady=2)
        self.resnap_section_only_cb = ttk.Checkbutton(
            r1_section, text="Apply to this section only",
            variable=self.resnap_section_only_var, command=self._sync_resnap_section_state,
            state="disabled")
        self.resnap_section_only_cb.pack(side="left")

        r1_section_fields = ttk.Frame(opts)
        r1_section_fields.pack(fill="x", anchor="w", padx=(24, 0), pady=2)
        self.resnap_from_label = ttk.Label(r1_section_fields, text="From", state="disabled")
        self.resnap_from_label.pack(side="left")
        self.resnap_from_entry = ttk.Entry(r1_section_fields, textvariable=self.resnap_from_var, width=15,
                                            validate="key", validatecommand=vcmd_time, state="disabled")
        self.resnap_from_entry.pack(side="left", padx=5)
        self.resnap_from_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.resnap_from_var))
        self.resnap_to_label = ttk.Label(r1_section_fields, text="to", state="disabled")
        self.resnap_to_label.pack(side="left")
        self.resnap_to_entry = ttk.Entry(r1_section_fields, textvariable=self.resnap_to_var, width=15,
                                          validate="key", validatecommand=vcmd_time, state="disabled")
        self.resnap_to_entry.pack(side="left", padx=5)
        self.resnap_to_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.resnap_to_var))

        r2 = ttk.Frame(opts)
        r2.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r2, text="Remove unused green lines", variable=self.remove_unused_green_var).pack(side="left")
        InfoIcon(r2, "Remove all redundant lines that have no meaningful "
                     "effect on the map.").pack(side="left", padx=(2, 0))

        r3 = ttk.Frame(opts)
        r3.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r3, text="Snap Kiai Toggles", variable=self.resnap_important_green_var).pack(side="left")
        InfoIcon(r3, "Resolve the occasional kiai unsnaps.").pack(side="left", padx=(2, 0))

        r4 = ttk.Frame(opts)
        r4.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r4, text="Turn all Kat's whistle to clap", variable=self.kat_var).pack(side="left")
        InfoIcon(r4, "Remove all whistle hitsounds and replace them with claps.").pack(side="left", padx=(2, 0))

        r5 = ttk.Frame(opts)
        r5.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r5, text="Set all green/red lines to Normal Sampleset",
                         variable=self.sampleset_var).pack(side="left")
        InfoIcon(r5, "Make sure the entire map is in Normal Sampleset so it "
                     "won't play funny hitsounds for some skins. Be careful "
                     "when using custom hitsounds.").pack(side="left", padx=(2, 0))

        r6 = ttk.Frame(opts)
        r6.pack(fill="x", anchor="w", pady=2)
        ttk.Checkbutton(r6, text="Resolve red/green line conflicts",
                         variable=self.conflicts_var).pack(side="left")
        InfoIcon(r6, "Resolve the mismatched kiai and volume setting between "
                     "green line and red line on the same timestamp. Green "
                     "line takes priority over red line.").pack(side="left", padx=(2, 0))

        # Base SV Option
        r_base_sv = ttk.Frame(opts)
        r_base_sv.pack(fill="x", anchor="w", pady=2)
        self.set_base_sv_cb = ttk.Checkbutton(
            r_base_sv, text="Set base SV setting to 1.4",
            variable=self.set_base_sv_var,
            command=self._sync_base_sv_state
        )
        self.set_base_sv_cb.pack(side="left")
        InfoIcon(r_base_sv, "Use this tool if your map's base SV got rounding error.").pack(side="left", padx=(2, 10))

        # Base SV Child Option: Other
        r_base_sv_other = ttk.Frame(opts)
        r_base_sv_other.pack(fill="x", anchor="w", padx=(24, 0), pady=2)
        self.base_sv_other_cb = ttk.Checkbutton(
            r_base_sv_other, text="Other",
            variable=self.base_sv_other_var,
            command=self._sync_base_sv_state,
            state="disabled"
        )
        self.base_sv_other_cb.pack(side="left", padx=(5, 5))

        self.base_sv_spinbox = tk.Spinbox(
            r_base_sv_other, from_=0.4, to=3.6, increment=0.1,
            textvariable=self.base_sv_val_var, width=5,
            state="disabled", format="%.1f",
            validate="key", validatecommand=vcmd_float
        )
        self.base_sv_spinbox.pack(side="left")
        self.base_sv_spinbox.bind("<FocusOut>", self._on_base_sv_focus_out)

        # Push Green Option
        r_push_green = ttk.Frame(opts)
        r_push_green.pack(fill="x", anchor="w", pady=2)
        self.push_green_cb = ttk.Checkbutton(
            r_push_green, text="Push all green lines by ",
            variable=self.push_green_var,
            command=self._sync_push_green_state
        )
        self.push_green_cb.pack(side="left")
        self.push_green_spinbox = tk.Spinbox(
            r_push_green, from_=5, to=20, increment=1,
            textvariable=self.push_green_ms_var, width=4,
            state="disabled",
            validate="key", validatecommand=vcmd_int
        )
        self.push_green_spinbox.pack(side="left", padx=2)
        self.push_green_spinbox.bind("<FocusOut>", self._on_push_green_focus_out)
        ttk.Label(r_push_green, text=" ms").pack(side="left")
        InfoIcon(r_push_green, "This does not affect kiai toggles and red-line-supported green lines.").pack(side="left", padx=(5, 0))

        r7 = ttk.Frame(opts)
        r7.pack(fill="x", anchor="w", pady=(8, 2))
        ttk.Checkbutton(r7, text="Reposition all notes in playfield", variable=self.center_notes_var,
                         command=self._sync_center_children_state).pack(side="left")

        self.note_position_mode_var = tk.StringVar(value="default")

        r_center = ttk.Frame(opts)
        r_center.pack(fill="x", anchor="w", padx=(24, 0))
        self.center_radio = ttk.Radiobutton(
            r_center, text="All notes in center", value="default",
            variable=self.note_position_mode_var, state="disabled",
            command=self._sync_coord_buttons_state)
        self.center_radio.pack(side="left")
        InfoIcon(r_center, "Move all the notes scattered around the screen to the "
                           "clean middle of the screen.").pack(side="left", padx=(2, 0))

        r8 = ttk.Frame(opts)
        r8.pack(fill="x", anchor="w", padx=(24, 0))
        self.separate_finishers_radio = ttk.Radiobutton(
            r8, text="Separate finishers", value="separate_finishers",
            variable=self.note_position_mode_var, state="disabled",
            command=self._sync_coord_buttons_state)
        self.separate_finishers_radio.pack(side="left")
        InfoIcon(r8, "Finisher notes will be placed at their own position "
                     "for easier differentiation.").pack(side="left", padx=(2, 6))
        self.finisher_coord_btn = ttk.Button(
            r8, text="Change Coordinate", state="disabled",
            command=self._open_finisher_coord_editor)
        self.finisher_coord_btn.pack(side="left")

        r9 = ttk.Frame(opts)
        r9.pack(fill="x", anchor="w", padx=(24, 0))
        self.separate_note_types_radio = ttk.Radiobutton(
            r9, text="Separate note types", value="separate_note_types",
            variable=self.note_position_mode_var, state="disabled",
            command=self._sync_coord_buttons_state)
        self.separate_note_types_radio.pack(side="left")
        InfoIcon(r9, "Don, Kat, Don Finisher, and Kat Finisher notes will "
                     "each be placed at their own position for easier "
                     "differentiation.").pack(side="left", padx=(2, 6))
        self.note_type_coord_btn = ttk.Button(
            r9, text="Change Coordinate", state="disabled",
            command=self._open_note_type_coord_editor)
        self.note_type_coord_btn.pack(side="left")

        ttk.Button(self.body, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

    def _sync_resnap_notes_state(self):
        self.resnap_section_only_cb.configure(state="normal" if self.resnap_notes_var.get() else "disabled")
        self._sync_resnap_section_state()

    def _sync_resnap_section_state(self):
        state = "normal" if (self.resnap_notes_var.get() and self.resnap_section_only_var.get()) else "disabled"
        self.resnap_from_entry.configure(state=state)
        self.resnap_to_entry.configure(state=state)
        self.resnap_from_label.configure(state=state)
        self.resnap_to_label.configure(state=state)

    def _sync_center_children_state(self):
        state = "normal" if self.center_notes_var.get() else "disabled"
        self.center_radio.configure(state=state)
        self.separate_finishers_radio.configure(state=state)
        self.separate_note_types_radio.configure(state=state)
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
            self.base_sv_other_cb.configure(state="normal")
            if self.base_sv_other_var.get():
                self.base_sv_spinbox.configure(state="normal")
            else:
                self.base_sv_spinbox.configure(state="disabled")
        else:
            self.base_sv_other_cb.configure(state="disabled")
            self.base_sv_spinbox.configure(state="disabled")

    def _sync_push_green_state(self):
        if self.push_green_var.get():
            self.push_green_spinbox.configure(state="normal")
        else:
            self.push_green_spinbox.configure(state="disabled")

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
        self.diff_combo["values"] = labels

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
            messagebox.showwarning("No difficulty", "Choose a difficulty to clean.")
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
                    messagebox.showerror("Invalid value", "Base SV must be a number between 0.4 and 3.6.")
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
                messagebox.showerror("Invalid value", "Push milliseconds must be an integer between 5 and 20.")
                return

        # Validate resnap section range
        resnap_section = None
        if self.resnap_notes_var.get() and self.resnap_section_only_var.get():
            from_result = logic.parse_time_input(self.resnap_from_var.get())
            to_result = logic.parse_time_input(self.resnap_to_var.get())
            if from_result is None or to_result is None:
                messagebox.showwarning("Warning", "Invalid timestamp input")
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

        # Two equal-height sections (offset/silence, then audio re-encode)
        # rather than one section sized to its content and a lot of dead
        # space below it — both of these Frames get `expand=True`, so
        # Tk's pack manager splits whatever leftover vertical space the
        # window has evenly between them.
        section_offset = ttk.Frame(self.body)
        section_offset.pack(fill="both", expand=True)

        add_header(section_offset, "Audio/Offset Settings",
                   "Change offset and resnap all notes and preview point "
                   "for all difficulties.")

        form = ttk.Frame(section_offset)
        form.pack(fill="x", padx=10, pady=10)
        ttk.Label(form, text="Current offset:").grid(row=0, column=0, sticky="w", pady=3)
        self.current_entry = ttk.Entry(form, width=15, state="readonly")
        self.current_entry.grid(row=0, column=1, sticky="w", pady=3)

        ttk.Label(form, text="New offset:").grid(row=1, column=0, sticky="w", pady=3)
        self.new_var = tk.StringVar()
        self.new_entry = tk.Spinbox(form, width=13, textvariable=self.new_var,
                                     from_=-10_000_000, to=10_000_000, increment=1)
        self.new_entry.grid(row=1, column=1, sticky="w", pady=3)
        self.new_var.trace_add("write", self._on_new_changed)

        ttk.Label(form, text="Change:").grid(row=2, column=0, sticky="w", pady=3)
        self.change_var = tk.StringVar(value="0")
        self.change_entry = tk.Spinbox(form, width=13, textvariable=self.change_var,
                                        from_=-10_000_000, to=10_000_000, increment=1)
        self.change_entry.grid(row=2, column=1, sticky="w", pady=3)
        self.change_var.trace_add("write", self._on_change_changed)

        self._updating = False
        self.base_offset = 0

        self.add_silence_var = tk.BooleanVar(value=False)
        r_silence = ttk.Frame(section_offset)
        r_silence.pack(fill="x", anchor="w", padx=10, pady=(0, 5))
        ttk.Checkbutton(r_silence, text="Add silence", variable=self.add_silence_var).pack(side="left")
        InfoIcon(r_silence, "Add 1000ms of silence to the beginning of the "
                             "song to avoid first-note lag.").pack(side="left", padx=(2, 0))

        self.diff_list = DiffCheckList(section_offset, app)
        self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Button(section_offset, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

        ttk.Separator(self.body, orient="horizontal").pack(fill="x", padx=10, pady=(0, 10))

        section_reencode = ttk.Frame(self.body)
        section_reencode.pack(fill="both", expand=True)

        add_header(section_reencode, "Audio Re-encode",
                   "Re-encode audio down to a lower bitrate to reduce file size.")

        self.reencode_source_var = tk.StringVar(value="map")
        r_src_map = ttk.Frame(section_reencode)
        r_src_map.pack(fill="x", anchor="w", padx=10, pady=(6, 2))
        ttk.Radiobutton(r_src_map, text="Use audio from the currently selected song",
                         variable=self.reencode_source_var, value="map",
                         command=self._sync_reencode_source_state).pack(side="left")

        r_src_other = ttk.Frame(section_reencode)
        r_src_other.pack(fill="x", anchor="w", padx=10, pady=(0, 2))
        ttk.Radiobutton(r_src_other, text="Use other audio file",
                         variable=self.reencode_source_var, value="other",
                         command=self._sync_reencode_source_state).pack(side="left")
        self.reencode_browse_btn = ttk.Button(r_src_other, text="Browse...",
                                               command=self._browse_reencode_audio)
        self.reencode_browse_btn.pack(side="left", padx=(8, 6))
        self.reencode_other_path = None
        self.reencode_other_name_var = tk.StringVar(value="")
        ttk.Label(r_src_other, textvariable=self.reencode_other_name_var,
                  foreground="#555555").pack(side="left")

        self.reencode_bitrate_var = tk.StringVar(value="192")
        r_bitrate = ttk.Frame(section_reencode)
        r_bitrate.pack(fill="x", anchor="w", padx=(30, 10), pady=(2, 5))
        for label, value in (("208kbps", "208"), ("192kbps", "192"),
                              ("160kbps", "160"), ("128kbps", "128")):
            ttk.Radiobutton(r_bitrate, text=label, value=value,
                             variable=self.reencode_bitrate_var).pack(side="left", padx=(0, 10))
        self._sync_reencode_source_state()

        ttk.Button(section_reencode, text="Apply", command=self.apply_reencode).pack(
            anchor="e", padx=10, pady=(0, 10))

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
            messagebox.showwarning("Nothing selected", "Tick at least one difficulty.")
            return
        try:
            delta = int(float(self.change_var.get()))
        except ValueError:
            messagebox.showerror("Invalid value", "Change must be a number.")
            return
        add_silence = self.add_silence_var.get()
        if delta == 0 and not add_silence:
            messagebox.showinfo("No change", "Change is 0 — nothing to apply.")
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
                    self.after(0, lambda: messagebox.showerror("Error", err_msg))
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
            messagebox.showerror("Error", err_msg)

        self.app.run_cancellable_job(busy_msg, work, on_success=lambda _r: finish_ok(),
                                      on_error=on_error)

    def apply_reencode(self):
        bitrate = int(self.reencode_bitrate_var.get())
        if self.reencode_source_var.get() == "other":
            src_path = self.reencode_other_path
            if not src_path:
                messagebox.showwarning("No file selected", "Pick an audio file to re-encode first.")
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
            messagebox.showerror("Error", err_msg)

        busy_msg = ("Installing ffmpeg + ffprobe (may take a few minutes)... Please wait..."
                     if install_first else "Re-encoding audio... Please wait...")
        self.app.run_cancellable_job(busy_msg, work, on_success=on_success, on_error=on_error,
                                      cancelled_toast="Re-encode Cancelled!")


# =============================================================================
class BgOffsetShifterFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        add_header(self.body, "BG Settings",
                   "Preview and set BG offset without having to manually "
                   "repeatedly type and guess the desired number.")

        row = ttk.Frame(self.body)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="BG File:").pack(side="left")
        self.bg_var = tk.StringVar()
        self.bg_combo = ttk.Combobox(row, textvariable=self.bg_var, state="readonly", width=30)
        self.bg_combo.pack(side="left", padx=5)
        self.bg_combo.bind("<<ComboboxSelected>>", self._on_bg_selected)

        row2 = ttk.Frame(self.body)
        row2.pack(fill="x", padx=10, pady=5)
        ttk.Label(row2, text="New Offset:").pack(side="left")
        self.offset_var = tk.StringVar(value="0")
        tk.Spinbox(row2, textvariable=self.offset_var, width=8,
                   from_=-10_000, to=10_000, increment=1).pack(side="left", padx=5)
        ttk.Button(row2, text="Preview", command=self.open_preview).pack(side="left", padx=10)

        self.convert_jpg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.body, text="Convert to .jpg", variable=self.convert_jpg_var).pack(anchor="w", padx=10)

        self.diff_list = DiffCheckList(self.body, app)
        self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

        ttk.Button(self.body, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def refresh(self):
        folder, diffs = self.app.get_diff_files()
        images = logic.list_song_folder_images(folder) if folder else []
        self.bg_combo["values"] = images
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
        self._prefill_current_offset()

    def _on_bg_selected(self, _event=None):
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
            messagebox.showwarning("No background", "Pick a background image first.")
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
            messagebox.showwarning("No background", "Pick a background image.")
            return
        if not targets:
            messagebox.showwarning("Nothing selected", "Tick at least one difficulty.")
            return
        try:
            offset = int(float(self.offset_var.get()))
        except ValueError:
            messagebox.showerror("Invalid value", "Offset must be a number.")
            return
        try:
            final_name = logic.apply_bg_offset(folder, targets, self.bg_var.get(), offset,
                                                self.convert_jpg_var.get())
        except Exception as e:
            messagebox.showerror("Error", str(e))
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
        _position_over_window(self, parent_frame, width=self.CANVAS_W, height=self.CANVAS_H + 40)
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

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Apply", command=self._apply).pack(side="right", padx=10, pady=5)
        ttk.Checkbutton(top, text="Reverse control", variable=self.reverse_var).pack(
            side="left", padx=10, pady=5)
        InfoIcon(top, "- Drag the BG vertically to adjust its "
                      "position and find the ideal offset for your map. "
                      "You can also use the mouse wheel for finer "
                      "adjustments.\n"
                      "- For precise positioning, use the ↑ and ↓ buttons "
                      "to fine-tune the offset.", align="right").pack(side="right", padx=(0, 4), pady=5)

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

        top = ttk.Frame(self)
        top.pack(side="top", fill="x")
        self.offset_label = ttk.Label(top, text=f"Current Video Offset: {self.offset}")
        self.offset_label.pack(side="left", padx=10, pady=5)

        ttk.Button(top, text="Apply", command=self._apply).pack(side="right", padx=10, pady=5)
        InfoIcon(top, "- Use the offset controls to adjust the video's timing "
                      "until it is properly synchronized with the audio. Do "
                      "note that every time the offset is adjusted, the "
                      "video will restart.\n"
                      "- If the video appears ahead of the music, increase "
                      "the offset by applying a positive value.\n"
                      "- Use ← and → buttons to seek backward or forward "
                      "through the video, and press the Space key to play "
                      "or pause playback.", align="right").pack(side="right", padx=(0, 4), pady=5)

        # Bottom-anchored controls packed first (each subsequent side="bottom"
        # widget stacks above the previous), so the volume row ends up right
        # at the bottom edge, the quick-offset buttons just above it, and the
        # seek bar above that — then the video surface fills whatever space
        # remains, using the freed-up room instead of leaving dead space
        # below a small fixed-size video area.
        vol_row = ttk.Frame(self)
        vol_row.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        ttk.Label(vol_row, text="Vol").pack(side="right", padx=(4, 0))
        self.volume_var = tk.IntVar(value=50)
        self.volume_scale = ttk.Scale(vol_row, from_=0, to=100, orient="horizontal", length=100,
                                       command=self._on_volume_change)
        self.volume_scale.set(50)
        self.volume_scale.pack(side="right")
        # Same fix as the seek bar: clicking anywhere on the trough should
        # jump the volume exactly there, not just step it by a tiny amount.
        self.volume_scale.bind("<Button-1>", self._on_volume_click)
        self.volume_scale.bind("<B1-Motion>", self._on_volume_click)

        btn_row = ttk.Frame(self)
        btn_row.pack(side="bottom", pady=8)
        prev_delta = None
        for delta in self.QUICK_OFFSETS:
            if prev_delta is not None and prev_delta < 0 < delta:
                tk.Label(btn_row, text="|", fg="#cccccc").pack(side="left", padx=6)
            text = f"+{delta}" if delta > 0 else str(delta)
            ttk.Button(btn_row, text=text, width=6,
                       command=lambda d=delta: self._nudge(d)).pack(side="left", padx=1)
            prev_delta = delta

        seek_row = ttk.Frame(self)
        seek_row.pack(side="bottom", fill="x", padx=10, pady=(5, 0))
        self.time_label = ttk.Label(seek_row, text="00:00", width=6)
        self.time_label.pack(side="left")

        ttk.Button(seek_row, text="⏪", width=5,
                   command=lambda: self._seek_relative(-5000)).pack(side="left", padx=(4, 0))
        self.play_pause_button = ttk.Button(seek_row, text="⏸", width=3,

                                             command=self._toggle_play_pause)
        self.play_pause_button.pack(side="left", padx=2)
        ttk.Button(seek_row, text="⏩", width=5,
                   command=lambda: self._seek_relative(5000)).pack(side="left", padx=(0, 4))

        self.seek_scale = ttk.Scale(seek_row, from_=0, to=1000, orient="horizontal")
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=5)
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
        self.play_pause_button.configure(text="⏸")

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
        self.play_pause_button.configure(text="▶" if self._paused else "⏸")

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
        add_header(self.body, "Video Settings",
                   "Preview video offset to sync to the music without "
                   "having to manually repeatedly type and guess the "
                   "desired number.")

        row = ttk.Frame(self.body)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="Video file:").pack(side="left")
        self.video_var = tk.StringVar()
        self.video_combo = ttk.Combobox(row, textvariable=self.video_var, state="readonly", width=30)
        self.video_combo.pack(side="left", padx=5)

        row2 = ttk.Frame(self.body)
        row2.pack(fill="x", padx=10, pady=5)
        ttk.Label(row2, text="Current Offset:").pack(side="left")
        self.current_entry = ttk.Entry(row2, width=10, state="readonly")
        self.current_entry.pack(side="left", padx=5)

        row3 = ttk.Frame(self.body)
        row3.pack(fill="x", padx=10, pady=5)
        ttk.Label(row3, text="New Offset:").pack(side="left")
        self.offset_var = tk.StringVar(value="0")
        ttk.Entry(row3, textvariable=self.offset_var, width=10).pack(side="left", padx=5)
        ttk.Button(row3, text="Preview", command=self.open_preview).pack(side="left", padx=10)

        self.resizer_var = tk.BooleanVar(value=False)
        self.blur_var = tk.BooleanVar(value=True)
        resizer_row = ttk.Frame(self.body)
        resizer_row.pack(fill="x", padx=10, pady=(5, 0), anchor="w")
        ttk.Checkbutton(resizer_row, text="Taiko Video Resizer", variable=self.resizer_var,
                         command=lambda: self._sync_video_option_state("resizer")).pack(side="left")
        InfoIcon(resizer_row, "Resize the full size video to fit under the "
                              "taiko playfield.").pack(side="left", padx=(2, 0))

        blur_row = ttk.Frame(self.body)
        blur_row.pack(fill="x", padx=30, anchor="w")
        self.blur_check = ttk.Checkbutton(blur_row, text="Blur", variable=self.blur_var, state="disabled")
        self.blur_check.pack(side="left")
        InfoIcon(blur_row, "Aesthetic blur for video.").pack(side="left", padx=(2, 0))

        # Taiko Video SB Code — a storyboard-based alternative to the
        # resizer above (can't be used together: checking one unchecks the
        # other, see _sync_video_option_state) that fakes the same
        # crop+shrink live via storyboard commands instead of a separately
        # re-encoded video file. No preview UI (it was often useless for
        # calibrating video positioning) — Apply computes and writes the
        # SB code directly, see apply().
        self.sb_var = tk.BooleanVar(value=False)
        sb_row = ttk.Frame(self.body)
        sb_row.pack(fill="x", padx=10, pady=(5, 0), anchor="w")
        ttk.Checkbutton(sb_row, text="Taiko Video SB Code", variable=self.sb_var,
                         command=lambda: self._sync_video_option_state("sb")).pack(side="left")
        InfoIcon(sb_row, "Commonly used in hybrid mapsets.").pack(side="left", padx=(2, 0))

        self.diff_list = DiffCheckList(self.body, app)
        self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

        add_apply_row(self.body, self.apply)

    def _sync_video_option_state(self, source):
        """Taiko Video Resizer and Taiko Video SB Code are mutually
        exclusive — checking one unchecks the other — then syncs the
        resizer's own Blur sub-control to its checkbox."""
        if source == "resizer" and self.resizer_var.get():
            self.sb_var.set(False)
        elif source == "sb" and self.sb_var.get():
            self.resizer_var.set(False)
        self.blur_check.configure(state="normal" if self.resizer_var.get() else "disabled")

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh()

    def refresh(self):
        folder, diffs = self.app.get_diff_files()
        videos = logic.list_song_folder_videos(folder) if folder else []
        self.video_combo["values"] = videos
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

    def open_preview(self):
        existing = getattr(self, "_preview_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        folder, _ = self.app.get_diff_files()
        if not folder or not self.video_var.get():
            messagebox.showwarning("No video", "Pick a video file first.")
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
            messagebox.showerror("Error", err_msg)

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
            messagebox.showwarning("Nothing selected", "Tick at least one difficulty.")
            return
        try:
            delta = int(float(self.offset_var.get()))
        except ValueError:
            messagebox.showerror("Invalid value", "Offset must be a number.")
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
                    self.after(0, lambda: messagebox.showerror("ffmpeg error", err_msg))
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
            messagebox.showerror("ffmpeg error", err_msg)

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
        super().__init__(master, relief="solid", borderwidth=1, bg="white",
                          padx=8, pady=6, cursor="hand2",
                          highlightthickness=2, highlightbackground="white")
        self.entry = entry
        self.name = entry["name"]
        self.gallery = gallery

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
        canvas = tk.Canvas(self, width=canvas_w, height=canvas_h, bg="white", highlightthickness=0)
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

        name_label = tk.Label(self, text=entry["name"], bg="white", font=("Segoe UI", 12, "bold"))
        name_label.pack()
        snap_label = tk.Label(self, text=entry.get("snap_divisor", "Unknown"),
                               bg="white", fg="#777777", font=("Segoe UI", 9))
        snap_label.pack()
        _add_hover_tooltip(snap_label, f'Duration: {entry["duration_ms"]} ms')

        # A square fills the canvas edge-to-edge exactly, unlike the old
        # circle — no leftover flat-colored corners to worry about.
        self.badge = tk.Canvas(self, width=16, height=16, bg="white", highlightthickness=0, cursor="hand2")
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
        for widget in (self, canvas, name_label, snap_label):
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

    def _on_leave(self, _e=None):
        self._hover_hide_job = self.after(50, self.hide_badge)

    def hide_badge(self):
        self._hover_hide_job = None
        self.badge.place_forget()

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
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Rename", command=self._on_rename)
        menu.add_separator()
        menu.add_command(label="Edit", command=self._on_edit)
        menu.add_command(label="Duplicate", command=self._on_duplicate)
        menu.add_command(label="Duplicate Inverted", command=self._on_duplicate_inverted)
        menu.add_command(label="Duplicate Reversed", command=self._on_duplicate_reversed)
        menu.add_separator()
        menu.add_command(label="Delete", command=self._on_delete_via_menu, foreground="#d32f2f")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_rename(self):
        new_name = simpledialog.askstring("Rename Pattern", "New name:",
                                           initialvalue=self.name, parent=self.winfo_toplevel())
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or new_name == self.name:
            return
        if any(p["name"] == new_name for p in logic.load_pattern_library()):
            messagebox.showwarning("Name taken", f'A pattern named "{new_name}" already exists.')
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
        self.configure(highlightbackground=self.KAT_COLOR if selected else "white")

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
            self.title(f"Edit Pattern — {existing_entry['name']}")
        else:
            self.title("Manually Add Pattern")
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

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(top, text="Pattern name:").pack(side="left")
        self.name_var = tk.StringVar(value=existing_entry["name"] if existing_entry is not None else "")
        ttk.Entry(top, textvariable=self.name_var, width=26).pack(side="left", padx=(4, 16))

        divisor_row = ttk.Frame(top)
        divisor_row.pack(side="right")
        ttk.Button(divisor_row, text="‹", width=2,
                   command=lambda: self._step_divisor(-1)).pack(side="left")
        self.divisor_combo = ttk.Combobox(divisor_row, textvariable=self.divisor_var,
                                           values=self.DIVISOR_OPTIONS, width=5, state="readonly")
        self.divisor_combo.pack(side="left", padx=2)
        ttk.Button(divisor_row, text="›", width=2,
                   command=lambda: self._step_divisor(1)).pack(side="left")
        # Live-applies as soon as the divisor changes — via the dropdown,
        # the ‹›  steppers (which just call divisor_var.set()), or
        # Ctrl+scroll on the canvas — no separate Apply button anymore.
        self.divisor_var.trace_add("write", self._on_divisor_changed)

        # Mode selector — Radiobuttons sharing mode_var give a segmented-
        # button look via the "Toolbutton" style (a checked one renders
        # sunken/pressed). Selecting one sets mode_var directly, which the
        # trace below applies immediately; no separate command= needed.
        mode_row = ttk.Frame(self)
        mode_row.pack(fill="x", padx=12, pady=(0, 4))
        for value, label in (("select", "Select (1)"), ("note", "Note (2)"), ("special", "Special (3/4)")):
            ttk.Radiobutton(mode_row, text=label, value=value, variable=self.mode_var,
                             style="Toolbutton").pack(side="left", padx=(0, 4))
        self.mode_var.trace_add("write", self._on_mode_changed)

        status_row = ttk.Frame(self)
        status_row.pack(fill="x", padx=12, pady=(0, 4))
        self.status_var = tk.StringVar()
        ttk.Label(status_row, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).pack(side="left")
        InfoIcon(status_row, "The control is the same as on osu!stable's "
                              "default.").pack(side="left", padx=(6, 0))

        canvas_w = self.MARGIN * 2 + self.BEATS_SHOWN * self.PX_PER_BEAT
        self.canvas = tk.Canvas(self, width=canvas_w, height=self.CANVAS_H, bg="white",
                                 highlightthickness=1, highlightbackground="#999999", cursor="hand2")
        self.canvas.pack(padx=12, pady=(0, 8))
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_scroll)
        self.canvas.bind("<Control-Button-4>", self._on_ctrl_scroll)
        self.canvas.bind("<Control-Button-5>", self._on_ctrl_scroll)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btn_row, text="Save Pattern", command=self._save).pack(side="right")
        ttk.Label(btn_row, text="E: finisher toggle - R or W: don/kat toggle",
                  foreground="#777777", font=("Segoe UI", 9)).pack(side="left")

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
            messagebox.showwarning(
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
            messagebox.showwarning("Name taken", f'A pattern named "{name}" already exists.')
            return
        if not self.notes:
            messagebox.showwarning("No notes", "Place at least one note first.")
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
        add_header(self.body, "Pattern Gallery",
                   "Build up your own reusable pattern library")

        # Split across two rows — at a big enough font size or narrow
        # enough window, cramming the name field, both buttons, and the
        # info icon onto one line pushed "Manually Add Pattern" (and
        # sometimes "Capture from osu!" too) out past the tool body's
        # actual visible width with no way to reach it. `width=20` (down
        # from the original 30) is still safely under the available width
        # even at the largest font-size setting and the app's minimum
        # window size — no need for fill/expand to guarantee that here,
        # so the field can stay a normal, un-stretched size instead of
        # ballooning to fill whatever room a wide window leaves over.
        name_row = ttk.Frame(self.body)
        name_row.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(name_row, text="Pattern name:").pack(side="left")
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(name_row, textvariable=self.name_var, width=20)
        self.name_entry.pack(side="left", padx=5)

        capture_row = ttk.Frame(self.body)
        capture_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(capture_row, text="Capture from osu!", command=self.capture).pack(side="left", padx=5)
        InfoIcon(capture_row, "To capture pattern from osu!, copy it and "
                               "save the map. Then click the button to "
                               "import pattern to your gallery").pack(side="left")
        ttk.Button(capture_row, text="Manually Add Pattern",
                   command=self.open_manual_pattern_editor).pack(side="left", padx=(15, 5))

        gallery_frame = ttk.Frame(self.body)
        gallery_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.gallery_canvas = tk.Canvas(gallery_frame, height=140, bg="white",
                                         highlightthickness=1, highlightbackground="#999999")
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

        self.gallery_inner = tk.Frame(self.gallery_canvas, bg="white")
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
        self.bulk_delete_row = ttk.Frame(self.body)
        self.bulk_delete_row.pack(fill="x", padx=10, pady=(0, 5))
        self.bulk_delete_btn = tk.Button(
            self.bulk_delete_row, bg="#d32f2f", fg="white", relief="flat",
            padx=10, pady=3, font=("Segoe UI", 10, "bold"),
            activebackground="#b71c1c", activeforeground="white",
            command=self.request_delete_selected)

        # Clicking anywhere else in the tool that isn't some other
        # interactive control also deselects (Tk doesn't bubble clicks to
        # parents, so this only fires when the click lands on body's own
        # bare background, never on a button/entry/card sitting on it).
        self.body.bind("<Button-1>", lambda _e: self.deselect_all())

        ttk.Separator(self.body, orient="horizontal").pack(fill="x", padx=10, pady=(0, 10))

        insert_header = ttk.Frame(self.body)
        insert_header.pack(fill="x", padx=10)
        ttk.Label(insert_header, text="Insert into map", font=("Segoe UI", 14, "bold")).pack(side="left")
        InfoIcon(insert_header, "Append your pattern in your gallery to a "
                                 "map.").pack(side="left", padx=(6, 0))

        target_row = ttk.Frame(self.body)
        target_row.pack(fill="x", padx=10, pady=(6, 2), anchor="w")
        ttk.Label(target_row, text="Target time:").pack(side="left")
        vcmd_time = (self.register(_validate_partial_time), "%P")
        self.target_time_var = tk.StringVar()
        self.target_time_entry = ttk.Entry(target_row, textvariable=self.target_time_var, width=15,
                                            validate="key", validatecommand=vcmd_time)
        self.target_time_entry.pack(side="left", padx=5)
        self.target_time_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.target_time_var))
        InfoIcon(target_row, "This field is auto-filled after copying a "
                              "timestamp.").pack(side="left", padx=(4, 0))

        self.match_bpm_var = tk.BooleanVar(value=True)
        match_bpm_row = ttk.Frame(self.body)
        match_bpm_row.pack(fill="x", padx=10, pady=(2, 5), anchor="w")
        ttk.Checkbutton(match_bpm_row, text="Match target map's BPM",
                         variable=self.match_bpm_var).pack(side="left")

        self.diff_list = DiffRadioList(self.body, app)
        self.diff_list.pack(fill="both", expand=False, padx=10, pady=5)

        ttk.Button(self.body, text="Insert Selected Pattern", command=self.insert_selected).pack(
            anchor="e", padx=10, pady=10)

        self._clipboard_poll_job = None
        self._last_clipboard_seen = None

    def on_shown(self):
        self.refresh()
        self.diff_list.refresh(preselect_file=self._live_diff_filename())
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
        if not self.selected_pattern_names:
            return
        if len(self.selected_pattern_names) == 1:
            self.request_delete_single(next(iter(self.selected_pattern_names)))
        else:
            self.request_delete_selected()

    def on_map_changed(self):
        self.diff_list.refresh(preselect_file=self._live_diff_filename())

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
        return (not self.app.confirm_pattern_delete) or messagebox.askyesno("Delete pattern", message)

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
            messagebox.showwarning("Name taken", f'A pattern named "{name}" already exists.')
            return
        try:
            clip = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Clipboard empty", "Nothing to read from the clipboard.")
            return
        songs_folder = getattr(self.app, "osu_songs_folder", None)
        if not songs_folder:
            messagebox.showwarning("No Songs folder", "Set your osu! Songs folder first (Settings).")
            return
        if not messagebox.askokcancel(
                "Save your map first",
                "Save your map (Ctrl+S) before proceeding!"):
            return
        try:
            _entry, truncated, had_concurrent = logic.capture_pattern_from_osu_selection(name, clip, songs_folder)
        except ValueError as e:
            messagebox.showerror("Couldn't capture pattern", str(e))
            return
        self.name_var.set("")
        self.refresh()
        if had_concurrent:
            messagebox.showwarning(
                "Concurrent notes dropped",
                f'"{name}" had two or more notes sharing the same timestamp — '
                f"only one was kept at each of those times, the rest were discarded.")
        if truncated:
            max_beats = int(logic.CAPTURED_PATTERN_MAX_BEATS)
            messagebox.showwarning(
                "Pattern truncated",
                f'"{name}" was longer than {max_beats} beats — notes past that '
                f"point were discarded so the pattern stays a short, reusable snippet.")
        self.notify_done(f'Captured pattern "{name}".')

    def delete_selected(self):
        name = self.selected_pattern_name
        if not name:
            messagebox.showwarning("Nothing selected", "Select a pattern to delete first.")
            return
        if not messagebox.askyesno("Delete pattern", f'Delete pattern "{name}"?'):
            return
        logic.delete_pattern_from_gallery(name)
        self.refresh()

    def insert_selected(self):
        if not self.require_map():
            return
        if not self.selected_pattern_names:
            messagebox.showwarning("Nothing selected", "Select a pattern to insert first.")
            return
        if len(self.selected_pattern_names) > 1:
            messagebox.showwarning("Multiple patterns selected", "Select exactly one pattern to insert.")
            return
        name = next(iter(self.selected_pattern_names))
        pattern = logic.get_pattern(name)
        if pattern is None:
            messagebox.showerror("Not found", "That pattern no longer exists.")
            self.refresh()
            return

        target_file = self.diff_list.selected()
        if not target_file:
            messagebox.showwarning("Nothing selected", "Choose a difficulty to insert into.")
            return

        target_result = logic.parse_time_input(self.target_time_var.get())
        if target_result is None:
            messagebox.showwarning("Warning", "Invalid timestamp input")
            return
        target_ms, cleaned = target_result
        self.target_time_var.set(cleaned)

        if not messagebox.askokcancel(
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
        add_header(self.body, "File Name Checker",
                   "Check and rename the mismatched capitalisation in file "
                   "name compared to the map's metadata.")

        ttk.Label(self.body, text="Only use this tool if your file names' capitalisation is "
                              "mismatched from the map's metadata.",
                  justify="left").pack(anchor="w", padx=10, pady=10)

        self.diff_list = DiffCheckList(self.body, app)
        self.diff_list.pack(fill="both", expand=True, padx=10, pady=5)

        # "word" wrap can't break a single unbroken "word" — and a file
        # name here often has no spaces (underscores instead), so a long
        # one would just run past the widget's right edge instead of
        # wrapping. "char" guarantees a wrap point regardless.
        self.result_text = tk.Text(self.body, height=10, wrap="char", font=("Segoe UI", 14))
        self.result_text.pack(fill="both", expand=False, padx=10, pady=5)

        btns = ttk.Frame(self.body)
        btns.pack(anchor="e", padx=10, pady=10)
        ttk.Button(btns, text="Check", command=self.check).pack(side="left", padx=5)
        ttk.Button(btns, text="Rename", command=self.apply).pack(side="left")

    def on_shown(self):
        self.diff_list.refresh()
        self.result_text.delete("1.0", "end")

    def on_map_changed(self):
        self.diff_list.refresh()

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
        self.result_text.delete("1.0", "end")


# =============================================================================
class EarlyVolumeSettingFrame(BaseToolFrame):
    def __init__(self, master, app):
        super().__init__(master, app)
        add_header(self.body, "Early Volume Settings",
                   "Shift the volume lines backward to keep hitsounds "
                   "synchronized when players hit notes too early.")

        vcmd_time = (self.register(_validate_partial_time), "%P")

        row = ttk.Frame(self.body)
        row.pack(fill="x", padx=10, pady=10)
        ttk.Label(row, text="Selected diff:").pack(side="left")
        self.diff_var = tk.StringVar()
        self.diff_combo = ttk.Combobox(row, textvariable=self.diff_var, state="readonly", width=25)
        self.diff_combo.pack(side="left", padx=5)
        self.diff_map = {}

        self.volume_threshold_var = tk.StringVar(value="10%")
        self.early_threshold_var = tk.StringVar(value="15ms")
        self.section_only_var = tk.BooleanVar(value=False)
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()

        opts = ttk.Frame(self.body)
        opts.pack(fill="x", padx=10, pady=2, anchor="w")

        r1 = ttk.Frame(opts)
        r1.pack(fill="x", anchor="w", pady=2)
        ttk.Label(r1, text="Volume change threshold").pack(side="left")
        ttk.Combobox(r1, textvariable=self.volume_threshold_var, values=logic.VOLUME_THRESHOLD_CHOICES,
                     state="readonly", width=6).pack(side="left", padx=8)
        InfoIcon(r1, "Sets the minimum volume change required to move a "
                     "volume line backward.").pack(side="left")

        r2 = ttk.Frame(opts)
        r2.pack(fill="x", anchor="w", pady=2)
        ttk.Label(r2, text="Early volume threshold").pack(side="left")
        ttk.Combobox(r2, textvariable=self.early_threshold_var, values=logic.EARLY_THRESHOLD_CHOICES,
                     state="readonly", width=6).pack(side="left", padx=8)
        InfoIcon(r2, "Sets how far a volume line is shifted backward "
                     "relative to the note.").pack(side="left")

        r3 = ttk.Frame(opts)
        r3.pack(fill="x", anchor="w", pady=(10, 2))
        self.section_only_cb = ttk.Checkbutton(
            r3, text="Apply to this section only",
            variable=self.section_only_var, command=self._sync_section_state)
        self.section_only_cb.pack(side="left")

        r4 = ttk.Frame(opts)
        r4.pack(fill="x", anchor="w", padx=(24, 0), pady=2)
        ttk.Label(r4, text="From").pack(side="left")
        self.from_entry = ttk.Entry(r4, textvariable=self.from_var, width=15,
                                     validate="key", validatecommand=vcmd_time, state="disabled")
        self.from_entry.pack(side="left", padx=5)
        self.from_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.from_var))
        ttk.Label(r4, text="to").pack(side="left")
        self.to_entry = ttk.Entry(r4, textvariable=self.to_var, width=15,
                                   validate="key", validatecommand=vcmd_time, state="disabled")
        self.to_entry.pack(side="left", padx=5)
        self.to_entry.bind("<<Paste>>", lambda e: _paste_time_field(self, self.to_var))

        ttk.Button(self.body, text="Apply", command=self.apply).pack(anchor="e", padx=10, pady=10)

    def on_shown(self):
        self.refresh()

    def on_map_changed(self):
        self.refresh(sync_to_current=True)

    def refresh(self, sync_to_current=False):
        folder, diffs = self.app.get_diff_files()
        self.diff_map = osu_parser.get_diff_display_map(folder, diffs) if folder else {}
        labels = sorted(self.diff_map.keys(), key=osu_parser.taiko_diff_sort_key)
        self.diff_combo["values"] = labels

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
        state = "normal" if self.section_only_var.get() else "disabled"
        self.from_entry.configure(state=state)
        self.to_entry.configure(state=state)

    def apply(self):
        if not self.require_map():
            return
        folder, _ = self.app.get_diff_files()
        fname = self.diff_map.get(self.diff_var.get())
        if not fname:
            messagebox.showwarning("No difficulty", "Choose a difficulty.")
            return
        targets = [fname]

        volume_threshold = float(self.volume_threshold_var.get().replace("%", ""))
        early_threshold = float(self.early_threshold_var.get().replace("ms", ""))

        section = None
        if self.section_only_var.get():
            from_result = logic.parse_time_input(self.from_var.get())
            to_result = logic.parse_time_input(self.to_var.get())
            if from_result is None or to_result is None:
                messagebox.showwarning("Warning", "Invalid timestamp input")
                return
            from_ms, from_clean = from_result
            to_ms, to_clean = to_result
            self.from_var.set(from_clean)
            self.to_var.set(to_clean)
            section = (min(from_ms, to_ms), max(from_ms, to_ms))

        logic.apply_early_volume_setting(folder, targets, volume_threshold, early_threshold, section)
        self.notify_done("Early volume setting applied.")
