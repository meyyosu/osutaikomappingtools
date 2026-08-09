"""
osu!taiko Mapping Tools
Main application entry point.

A Windows desktop utility for osu!taiko mappers, matching the tool set:
metadata manager, volume/kiai copier, map cleaner, offset shifter, bg offset
shifter, video offset shifter, file name checker.
"""

import os
import re
import sys
import glob
import json
import shutil
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import osu_parser
import tools_logic as logic


def _relaunch_process():
    """Starts a fresh instance of the app and ends this one. Used instead
    of the more obvious `os.execv(sys.executable, ...)` because that fails
    with "Failed to import encodings module" on a PyInstaller --onefile
    build: sys.executable there is the bootloader .exe itself, and execv
    replaces the current process image in place without letting the
    bootloader's own cleanup run — the re-exec'd process inherits the
    outgoing one's PyInstaller-internal environment variables (which point
    at its temp extraction folder, among other bootloader bookkeeping),
    which are about to be invalidated, so it can't find its own bundled
    Python standard library.

    Spawning a genuinely new, separate process instead avoids that — but
    every PyInstaller-internal variable needs stripping from the child's
    environment for this to actually work every time, not just _MEIPASS2:
    an instance that was itself already relaunched this way (i.e. this is
    the *second* restart in a row) has more of these set in its own
    os.environ than the original, user-launched instance did, since it's
    itself running as an already-extracted bootloader child — stripping
    only _MEIPASS2 fixed the first restart but left the second one broken
    for exactly that reason. Stripping anything with a recognized
    PyInstaller-internal prefix (regardless of the exact variable name,
    which can vary by version/platform) is the robust fix."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    if getattr(sys, "frozen", False):
        # Frozen executable: sys.executable is the .exe; sys.argv already
        # excludes it, so it doesn't need to be re-added.
        subprocess.Popen([sys.executable] + sys.argv[1:], env=env)
    else:
        subprocess.Popen([sys.executable] + sys.argv, env=env)
    os._exit(0)  # hard exit — skips Tcl/Tk teardown that a half-destroyed
                 # root window could otherwise get stuck on

APP_TITLE = "osu!taiko Mapping Tools"


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


def _add_tooltip(widget, text, align: str = "left"):
    """A small floating tooltip on hover, for title-bar icon buttons whose
    symbol alone isn't self-explanatory. `align="right"` lines the
    tooltip's right edge up with the button's right edge instead of its
    left — useful for buttons sitting near the right edge of the window,
    where a left-aligned tooltip could run off-screen. `text` can be a
    plain string, or a zero-arg callable returning the text to show right
    now (returning None suppresses the tooltip for that hover) — used
    where the same widget needs a different message depending on its
    current state, e.g. search being disabled while indexing."""
    tip = {"window": None}

    def show(_event=None):
        if tip["window"] is not None:
            return
        display_text = text() if callable(text) else text
        if not display_text:
            return
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=display_text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=340, font=("Segoe UI", 12),
                 padx=6, pady=3).pack()
        tw.update_idletasks()
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        if align == "right":
            x = widget.winfo_rootx() + widget.winfo_width() - tw.winfo_reqwidth()
        elif align == "center":
            x = widget.winfo_rootx() + (widget.winfo_width() - tw.winfo_reqwidth()) // 2
        else:
            x = widget.winfo_rootx() + 4
        tw.wm_geometry(f"+{x}+{y}")
        tip["window"] = tw

    def hide(_event=None):
        if tip["window"] is not None:
            tip["window"].destroy()
            tip["window"] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)
    widget.bind("<Destroy>", hide)


SIDEBAR_ITEMS = [
    "Metadata Manager",
    "Volume/Kiai Copier",
    "Map Cleaner",
    "Audio/Offset Settings",
    "BG Settings",
    "Video Settings",
    "Early Volume Settings",
    "Pattern Gallery",
    "File Name Checker",
]

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_config.txt")
FONT_SIZE_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_fontsize.txt")
SONG_INDEX_MODE_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_indexmode.txt")
CONFIRM_PATTERN_DELETE_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_confirmdelete.txt")
LIVE_SYNC_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_livesync.txt")
COORD_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_coords.json")
WINDOW_GEOMETRY_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_geometry.txt")
FIRST_RUN_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_firstrun.txt")

# Explicit position (not just size) — leaving this to the window manager's
# default centering risked the bottom of a 950px-tall window running under
# the taskbar on common 1080p-and-smaller displays. Opening near the
# top-left instead keeps the whole window, including its bottom edge,
# clear of the taskbar. Used whenever there's no saved geometry yet (first
# run) or the saved value doesn't parse.
DEFAULT_WINDOW_GEOMETRY = "1500x950+50+20"
# Each coordinate is a +/- delimiter (Tk's own "from this edge" convention)
# optionally followed by a literal minus sign for a genuinely negative
# absolute position — confirmed for real on a multi-monitor setup where a
# window sitting on a monitor to the left of the primary saved as
# "...+-1960+19" (delimiter "+", value "-1960"), which a naive
# single-sign-per-coordinate regex rejected as invalid, silently falling
# back to the default every single launch.
_GEOMETRY_RE = re.compile(r"^\d+x\d+[+-]-?\d+[+-]-?\d+$")

TEXT_SIZE_OPTIONS = [("Small", 10), ("Default", 14), ("Medium", 17), ("Large", 20), ("Grandma", 25)]
SONG_INDEX_MODE_OPTIONS = [
    ("manual", "Manual Index"),
    ("partial", "Partial Index"),
    ("full", "Full Index"),
]
VALID_SONG_INDEX_MODES = tuple(m for m, _label in SONG_INDEX_MODE_OPTIONS)


def load_osu_folder_config() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_osu_folder_config(path: str):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(path)
    except OSError:
        pass


def load_font_size_config() -> int:
    try:
        with open(FONT_SIZE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 14


def save_font_size_config(size: int):
    try:
        with open(FONT_SIZE_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(str(size))
    except OSError:
        pass


def load_song_index_mode() -> str:
    try:
        with open(SONG_INDEX_MODE_CONFIG_PATH, "r", encoding="utf-8") as f:
            mode = f.read().strip()
            return mode if mode in VALID_SONG_INDEX_MODES else "partial"
    except OSError:
        return "partial"


def save_song_index_mode(mode: str):
    try:
        with open(SONG_INDEX_MODE_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(mode)
    except OSError:
        pass


def load_confirm_pattern_delete() -> bool:
    try:
        with open(CONFIRM_PATTERN_DELETE_CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() != "0"
    except OSError:
        return True


def save_confirm_pattern_delete(value: bool):
    try:
        with open(CONFIRM_PATTERN_DELETE_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("1" if value else "0")
    except OSError:
        pass


def load_live_sync_config() -> bool:
    try:
        with open(LIVE_SYNC_CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() != "0"
    except OSError:
        return True


def save_live_sync_config(value: bool):
    try:
        with open(LIVE_SYNC_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("1" if value else "0")
    except OSError:
        pass


def load_window_geometry_config() -> str:
    try:
        with open(WINDOW_GEOMETRY_CONFIG_PATH, "r", encoding="utf-8") as f:
            geometry = f.read().strip()
    except OSError:
        return DEFAULT_WINDOW_GEOMETRY
    return geometry if _GEOMETRY_RE.match(geometry) else DEFAULT_WINDOW_GEOMETRY


def save_window_geometry_config(geometry: str):
    try:
        with open(WINDOW_GEOMETRY_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(geometry)
    except OSError:
        pass


def load_first_run_done() -> bool:
    try:
        with open(FIRST_RUN_CONFIG_PATH, "r", encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def save_first_run_done():
    try:
        with open(FIRST_RUN_CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass


def _coords_to_lists(coords: dict) -> dict:
    return {k: list(v) for k, v in coords.items()}


def _coords_to_tuples(coords: dict) -> dict:
    return {k: tuple(v) for k, v in coords.items()}


def load_coord_config() -> "tuple[dict, dict]":
    """Returns (finisher_coords, note_type_coords), falling back to the
    defaults (matching the reference images) for anything missing or if
    the config file doesn't exist / is corrupt."""
    finisher_coords = dict(logic.DEFAULT_FINISHER_COORDS)
    note_type_coords = dict(logic.DEFAULT_NOTE_TYPE_COORDS)
    try:
        with open(COORD_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("finisher_coords"), dict):
            finisher_coords.update(_coords_to_tuples(data["finisher_coords"]))
        if isinstance(data.get("note_type_coords"), dict):
            note_type_coords.update(_coords_to_tuples(data["note_type_coords"]))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        pass
    return finisher_coords, note_type_coords


def save_coord_config(finisher_coords: dict, note_type_coords: dict):
    try:
        with open(COORD_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "finisher_coords": _coords_to_lists(finisher_coords),
                "note_type_coords": _coords_to_lists(note_type_coords),
            }, f)
    except OSError:
        pass


def is_valid_osu_songs_folder(path: str) -> bool:
    """The folder must actually be an osu! Songs folder — i.e. the path's
    last two components must be "osu!" then "Songs" (case-insensitive),
    not just any directory the user happens to point at."""
    if not path or not os.path.isdir(path):
        return False
    normalized = os.path.normpath(path)
    parts = normalized.split(os.sep)
    if len(parts) < 2:
        return False
    return parts[-1].lower() == "songs" and parts[-2].lower() == "osu!"


def guess_osu_stable_songs_folder() -> str:
    candidates = []
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        candidates.append(os.path.join(localappdata, "osu!", "Songs"))
    for drive in ["C:\\", "D:\\"]:
        candidates.append(os.path.join(drive, "osu!", "Songs"))
        candidates.append(os.path.join(drive, "Games", "osu!", "Songs"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        # Restores wherever the window was sized/positioned last time it
        # closed (see save_window_geometry_config in _on_close_request);
        # DEFAULT_WINDOW_GEOMETRY only applies on first run or if that
        # file's missing/corrupt.
        self.geometry(load_window_geometry_config())
        self.minsize(900, 550)
        self._set_app_icon()
        self.current_font_size = load_font_size_config()
        self._scale_up_fonts(self.current_font_size)

        self.osu_songs_folder = load_osu_folder_config() or guess_osu_stable_songs_folder()
        self.song_index_mode = load_song_index_mode()
        self.confirm_pattern_delete = load_confirm_pattern_delete()
        self.finisher_coords, self.note_type_coords = load_coord_config()
        self.current_map_folder = tk.StringVar(value="")
        self.now_selecting_var = tk.StringVar(value="Now Selecting: (no map selected)")
        self._current_map_meta = None
        self.current_diff_filename = None
        self.use_romanised_display = False
        self.song_index = []          # list of dicts, see build_song_index()
        self._indexing = False
        self._indexed_once = False
        self._indexed_full = False
        self.live_sync_enabled = load_live_sync_config()
        self._last_live_sync_key = None

        self._busy_depth = 0
        self._busy_win = None
        self._busy_configure_binding = None
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

        self._build_titlebar()
        self._build_body()

        self.frames = {}
        self._build_frames()
        self.show_frame("front")

        if self.osu_songs_folder and os.path.isdir(self.osu_songs_folder):
            self._start_indexing_per_mode()
        self._refresh_manual_index_button()
        self.after(1000, self._poll_live_osu_map)
        self.after(200, self._maybe_show_first_time_setup)

    def _on_close_request(self):
        """The default WM_DELETE_WINDOW handler is overridden so a running
        background job (see set_busy) can veto the close — otherwise the OS
        title-bar X button (and Alt+F4) could tear down the window mid-
        ffmpeg-encode, e.g. while "Add silence" or the taiko video resizer
        is still writing an output file."""
        if self._busy_depth > 0:
            return
        save_window_geometry_config(self.geometry())
        self.destroy()

    def set_busy(self, busy: bool, message: str = "Processing... Please wait..."):
        """Shows/hides the fading busy banner and blocks all input to the
        rest of the app while a long-running background job (currently:
        ffmpeg encodes for Add Silence / the taiko video resizer) is in
        flight. Depth-counted so nested/overlapping calls can't hide the
        banner out from under a still-running job. Must be called from the
        main thread — worker threads should route through self.after(0, ...)."""
        if busy:
            self._busy_depth += 1
            if self._busy_depth == 1:
                self._show_busy_overlay(message)
        else:
            self._busy_depth = max(0, self._busy_depth - 1)
            if self._busy_depth == 0:
                self._hide_busy_overlay()

    def _show_busy_overlay(self, message: str):
        existing = self._busy_win
        if existing is not None and existing.winfo_exists():
            return
        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        bg = "#ffc107"
        win.configure(bg=bg)
        tk.Label(win, text=message, bg=bg, fg="#000000",
                 font=("Segoe UI", 14, "bold"), pady=8).pack(fill="x")
        self._busy_win = win
        self._position_busy_overlay()
        self._busy_configure_binding = self.bind(
            "<Configure>", lambda e: self._position_busy_overlay(), add="+")
        # Order matters: lift, then focus_force, then grab_set (see the
        # "Modal Toplevel windows must guard against duplicate opens"
        # convention this mirrors) — grab_set is what actually blocks
        # clicks/keys reaching the rest of the app while this is up.
        win.transient(self)
        win.lift()
        win.focus_force()
        win.grab_set()
        self._fade_busy_overlay(0, 15, reverse=False)

    def _position_busy_overlay(self):
        win = self._busy_win
        if win is None or not win.winfo_exists():
            return
        self.update_idletasks()
        px, py, pw = self.winfo_x(), self.winfo_y(), self.winfo_width()
        win.update_idletasks()
        wh = win.winfo_reqheight()
        win.geometry(f"{pw}x{wh}+{px}+{py}")

    def _fade_busy_overlay(self, step: int, steps: int, reverse: bool):
        win = self._busy_win
        if win is None or not win.winfo_exists():
            return
        alpha = (steps - step) / steps if reverse else step / steps
        win.attributes("-alpha", alpha)
        if step < steps:
            win.after(max(1, 250 // steps), lambda: self._fade_busy_overlay(step + 1, steps, reverse))
        elif reverse:
            win.grab_release()
            if self._busy_configure_binding is not None:
                self.unbind("<Configure>", self._busy_configure_binding)
                self._busy_configure_binding = None
            win.destroy()
            self._busy_win = None

    def _hide_busy_overlay(self):
        win = self._busy_win
        if win is None or not win.winfo_exists():
            return
        self._fade_busy_overlay(0, 15, reverse=True)

    def update_finisher_coords(self, coords: dict):
        self.finisher_coords = coords
        save_coord_config(self.finisher_coords, self.note_type_coords)

    def update_note_type_coords(self, coords: dict):
        self.note_type_coords = coords
        save_coord_config(self.finisher_coords, self.note_type_coords)

    def _start_indexing_per_mode(self):
        """Kicks off indexing the way "Song Index on Startup" (in Settings)
        says to — used both at app launch and whenever the Songs folder
        actually changes."""
        if self.song_index_mode == "manual":
            return  # the user has to click the Start Indexing button
        elif self.song_index_mode == "full":
            self.build_song_index(full=True)
        else:  # "partial" (default)
            self.build_song_index()

    def _refresh_manual_index_button(self):
        """Shows the "Start Indexing" button in the same spot the progress
        bar/"Index Full Library" button normally live, but only when
        Manual mode is selected and nothing has been indexed yet — once
        indexing has happened once, this button's job is done and the
        usual "Index Full Library" button (for a full re-index) takes over
        that spot instead."""
        if self.song_index_mode == "manual" and not self._indexed_once and not self._indexing:
            if not self.index_status_frame.winfo_ismapped():
                self.index_status_frame.pack(side="left", padx=(4, 0))
            self.start_indexing_button.pack(side="left")
        else:
            self.start_indexing_button.pack_forget()
        self._sync_search_availability()

    def _sync_search_availability(self):
        """Search only makes sense once there's something to search — grays
        out the search box and its magnifying-glass button (with an
        explanatory tooltip) while actively indexing, or while Manual
        Index mode is selected and nothing has been indexed yet at all."""
        if self._indexing:
            state = "disabled"
        elif self.song_index_mode == "manual" and not self._indexed_once:
            state = "disabled"
        else:
            state = "normal"
        self.search_entry.configure(state=state)
        self.search_btn.configure(state=state)

    def _on_start_indexing_clicked(self):
        self.start_indexing_button.pack_forget()
        self.build_song_index()

    # ------------------------------------------------------------------
    def _set_app_icon(self):
        """Sets the window/taskbar icon from icon.png next to this file.
        iconphoto works cross-platform (Tk 8.6+ loads PNG natively); on
        Windows the packaged .exe additionally gets icon.ico baked in at
        build time via PyInstaller's --icon flag (see build_exe.bat) for
        the taskbar/file-explorer icon before the window even opens.
        sys._MEIPASS is where PyInstaller's --onefile mode extracts
        bundled data files to at runtime — falls back to this file's own
        folder when just running from source."""
        try:
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            icon_path = os.path.join(base_dir, "icon.png")
            self._icon_image = tk.PhotoImage(file=icon_path)  # kept alive on self
            self.iconphoto(True, self._icon_image)
        except (tk.TclError, OSError):
            pass  # missing/unreadable icon file — not worth failing startup over

    def _scale_up_fonts(self, size=14):
        """Sets the default UI text size app-wide. Tk's named fonts
        (TkDefaultFont, TkTextFont, ...) are what most ttk widgets — Label,
        Button, Checkbutton, Entry, Combobox, Spinbox — use internally
        unless a widget sets its own explicit font, so changing these
        cascades everywhere at once instead of having to touch every
        individual widget. Widgets that set an explicit literal font (tool
        headers, the sidebar, tooltips, etc.) don't pick this up live —
        those need a restart to match."""
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass

    def _build_titlebar(self):
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x")

        self.search_var = tk.StringVar(value="Search osu! map...")
        search_entry = ttk.Entry(bar, width=20, textvariable=self.search_var)
        search_entry.pack(side="left", padx=(6, 4), pady=4)
        search_entry.bind("<Return>", lambda e: self._on_search())
        search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry = search_entry

        icon_font = ("Segoe UI Emoji", 12)
        search_btn = ttk.Button(bar, text="🔍", width=3, command=self._on_search)
        search_btn.pack(side="left")
        search_btn.configure(style="Icon.TButton")
        self.search_btn = search_btn

        def _search_tooltip_text():
            if self._indexing:
                return ("Your osu! songs folder is currently being indexed. "
                         "Search will become available once indexing is complete.")
            if self.song_index_mode == "manual" and not self._indexed_once:
                return "Search requires indexing your osu! Songs folder. Click \u201cStart Indexing\u201d to begin."
            return "Search"

        _add_tooltip(search_btn, _search_tooltip_text)
        _add_tooltip(search_entry, _search_tooltip_text)

        # Indexing status (progress bar + label), hidden until a Songs
        # folder index build actually starts. The "index full library"
        # button lives in this same spot when nothing else needs it.
        self.index_status_frame = ttk.Frame(bar)
        self.index_progress = ttk.Progressbar(self.index_status_frame, mode="indeterminate", length=80)
        self.index_status_var = tk.StringVar(value="")
        self.index_status_label = ttk.Label(self.index_status_frame, textvariable=self.index_status_var,
                                             foreground="#666666")
        self.index_status_label.pack(side="left")

        def _index_tooltip_text():
            # Only relevant while actually indexing — stays quiet during
            # the "Index Complete!" cooldown or once idle.
            if self._indexing:
                return "Currently indexing your osu! Songs folder for search. Please wait…"
            return None

        _add_tooltip(self.index_progress, _index_tooltip_text, align="right")
        _add_tooltip(self.index_status_label, _index_tooltip_text, align="right")
        self.index_full_button = ttk.Button(self.index_status_frame, text="Index Full Library",
                                             command=lambda: self.build_song_index(full=True))
        _add_tooltip(self.index_full_button,
                     "100 most recently imported osu!taiko maps have been "
                     "indexed for search.\nClick this button to fully index "
                     "your entire osu! song folder.", align="right")

        self.start_indexing_button = ttk.Button(self.index_status_frame, text="Start Indexing",
                                                 command=self._on_start_indexing_clicked)
        _add_tooltip(self.start_indexing_button,
                     "Click this button to index the 100 most recently "
                     "imported taiko maps", align="right")

        folder_btn = ttk.Button(bar, text="📁", width=3, command=self.browse_for_map_folder,
                                 style="Icon.TButton")
        folder_btn.pack(side="left", padx=2)
        _add_tooltip(folder_btn, "Open map manually", align="center")

        lightning_btn = ttk.Button(bar, text="📡", width=3, command=self.pickup_current_map,
                                    style="Icon.TButton")
        lightning_btn.pack(side="left", padx=2)
        _add_tooltip(lightning_btn, "Open map from osu!", align="center")

        style = ttk.Style()
        style.configure("Icon.TButton", font=icon_font)

        title_lbl = ttk.Label(bar, textvariable=self.now_selecting_var, anchor="w",
                               relief="sunken", padding=4)
        title_lbl.pack(side="left", fill="x", expand=True, padx=4)

        romanise_btn = ttk.Button(bar, text="あ", width=3, command=self.toggle_metadata_display)
        romanise_btn.pack(side="left", padx=(0, 4))
        _add_tooltip(romanise_btn, "Romanisation Toggle", align="center")

        open_folder_btn = ttk.Button(bar, text="📂", width=3, command=self.open_current_map_folder,
                                      style="Icon.TButton")
        open_folder_btn.pack(side="left", padx=(0, 4))
        _add_tooltip(open_folder_btn, "Open currently selected song folder", align="center")

        beatmap_link_btn = ttk.Button(bar, text="🔗", width=3, command=self.open_beatmap_page,
                                       style="Icon.TButton")
        beatmap_link_btn.pack(side="left", padx=(0, 4))
        _add_tooltip(beatmap_link_btn, "Open beatmap page", align="center")

        settings_btn = ttk.Button(bar, text="⚙", width=3, command=self.open_settings, style="Icon.TButton")
        settings_btn.pack(side="left", padx=(10, 4))
        _add_tooltip(settings_btn, "Settings", align="center")

        # No custom minimize/maximize/close buttons — the OS window chrome
        # already provides these; duplicating them here was redundant.

    def open_settings(self):
        if getattr(self, "_settings_win", None) is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        _position_over_window(win, self, width=560, height=780)

        # Staged values — nothing here actually takes effect (or gets
        # written to disk) until Apply/Restart is clicked.
        pending = {
            "folder": self.osu_songs_folder or "",
            "font_size": getattr(self, "current_font_size", 14),
            "index_mode": self.song_index_mode,
            "confirm_pattern_delete": self.confirm_pattern_delete,
            "live_sync": self.live_sync_enabled,
        }

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=16, pady=16)

        # ------------------------------------------------------------------
        # 1. Set osu! Song Folder
        # ------------------------------------------------------------------
        ttk.Label(body, text="1. Set osu! Song Folder", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 4))

        status_var = tk.StringVar()
        status_label = tk.Label(body, textvariable=status_var, fg="red", wraplength=520, justify="left")
        status_label.pack(anchor="w", pady=(0, 8))

        def refresh_status():
            if pending["folder"] and os.path.isdir(pending["folder"]):
                status_var.set(f"osu! Songs folder is set to {pending['folder']}")
                status_label.configure(fg="black")
            else:
                status_var.set("osu! Songs folder is not set yet.")
                status_label.configure(fg="red")

        refresh_status()

        folder_row = ttk.Frame(body)
        folder_row.pack(fill="x", pady=(0, 12))
        folder_var = tk.StringVar(value=pending["folder"])
        folder_entry = ttk.Entry(folder_row, textvariable=folder_var)
        folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        set_btn = ttk.Button(folder_row, text="Set", state="disabled")

        def do_set():
            path = folder_var.get().strip()
            if not is_valid_osu_songs_folder(path):
                messagebox.showwarning(
                    "Invalid folder",
                    "That doesn't look like an osu! Songs folder — the path must "
                    "end in \"osu!\\Songs\" (e.g. C:\\osu!\\Songs). Pick the actual "
                    "Songs folder inside your osu! install.",
                )
                return
            pending["folder"] = path
            refresh_status()
            set_btn.configure(state="disabled")

        set_btn.configure(command=do_set)

        def browse():
            chosen = filedialog.askdirectory(title="Select your osu! Songs folder",
                                              initialdir=folder_var.get() or None)
            if chosen:
                folder_var.set(chosen)
                set_btn.configure(state="normal")

        ttk.Button(folder_row, text="Browse Folder", command=browse).pack(side="left", padx=(0, 6))
        set_btn.pack(side="left")

        # Typing directly in the field should also re-enable Set, in case
        # someone pastes/edits a path instead of using Browse.
        folder_var.trace_add("write", lambda *a: set_btn.configure(state="normal"))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # ------------------------------------------------------------------
        # 2. Live-sync song select
        # ------------------------------------------------------------------
        ttk.Label(body, text="2. Live-sync song select",
                  font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 4))

        live_sync_var = tk.StringVar(value="auto" if pending["live_sync"] else "manual")

        def on_live_sync_change():
            pending["live_sync"] = (live_sync_var.get() == "auto")

        ttk.Radiobutton(body, text="Automatically detect selected song in osu!", value="auto",
                         variable=live_sync_var, command=on_live_sync_change).pack(anchor="w", pady=1)
        ttk.Radiobutton(body, text="Load song manually", value="manual",
                         variable=live_sync_var, command=on_live_sync_change).pack(anchor="w", pady=1)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # ------------------------------------------------------------------
        # 3. Song Index on Startup
        # ------------------------------------------------------------------
        from screens import InfoIcon

        index_header_row = ttk.Frame(body)
        index_header_row.pack(anchor="w", pady=(0, 4))
        ttk.Label(index_header_row, text="3. Song Index on Startup",
                  font=("Segoe UI", 18, "bold")).pack(side="left")
        InfoIcon(index_header_row,
                 "- Manual: Disable auto-indexing\n"
                 "- Partial: Auto-index 100 most recent taiko maps\n"
                 "- Full: Auto-index your entire songs folder").pack(side="left", padx=(6, 0))

        index_mode_var = tk.StringVar(value=pending["index_mode"])

        def on_index_mode_change():
            pending["index_mode"] = index_mode_var.get()

        for value, label in SONG_INDEX_MODE_OPTIONS:
            ttk.Radiobutton(body, text=label, value=value, variable=index_mode_var,
                             command=on_index_mode_change).pack(anchor="w", pady=1)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # ------------------------------------------------------------------
        # 4. Confirm Gallery Pattern Deletion
        # ------------------------------------------------------------------
        ttk.Label(body, text="4. Confirm Gallery Pattern Deletion", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 4))

        disable_warning_var = tk.BooleanVar(value=not pending["confirm_pattern_delete"])

        def on_disable_warning_change():
            pending["confirm_pattern_delete"] = not disable_warning_var.get()

        ttk.Checkbutton(body, text="Disable warning when deleting patterns in gallery.", variable=disable_warning_var,
                         command=on_disable_warning_change).pack(anchor="w", pady=(0, 4))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # ------------------------------------------------------------------
        # 5. Download resources
        # ------------------------------------------------------------------
        download_header_row = ttk.Frame(body)
        download_header_row.pack(anchor="w", pady=(0, 4))
        ttk.Label(download_header_row, text="5. Download resources",
                  font=("Segoe UI", 18, "bold")).pack(side="left")
        InfoIcon(download_header_row,
                 "Including\n\n"
                 "- ffmpeg + ffprobe\n"
                 "- VLC Player").pack(side="left", padx=(6, 0))

        ttk.Label(body, text="They are required to use certain tools.").pack(anchor="w", pady=(0, 6))

        download_btn = ttk.Button(body, text="Install Automatically")

        def do_install_resources():
            original_text = "Install Automatically"

            # Checked independently — audio_tools_fully_available() only
            # says "is anything missing", not *which* binary, so a system
            # with ffmpeg but no ffprobe (or vice versa) needs to still
            # trigger an install that fetches just the missing one, not
            # get short-circuited by treating the pair as one unit.
            need_ffmpeg_bin = not logic.ffmpeg_available()
            need_ffprobe_bin = not logic.ffprobe_available()
            need_vlc = not logic.vlc_available()
            if not need_ffmpeg_bin and not need_ffprobe_bin and not need_vlc:
                messagebox.showinfo("Download resources", "ffmpeg, ffprobe and VLC are already installed.")
                return

            download_btn.configure(state="disabled", text="Installing...")

            def work():
                self.after(0, self.set_busy, True,
                           "Installing ffmpeg + ffprobe and VLC (may take a few minutes)... Please wait...")
                errors = []
                installed = []
                if need_ffmpeg_bin or need_ffprobe_bin:
                    try:
                        logic.install_ffmpeg_suite_bundled(need_ffmpeg_bin, need_ffprobe_bin)
                        names = [n for need, n in ((need_ffmpeg_bin, "ffmpeg"), (need_ffprobe_bin, "ffprobe")) if need]
                        installed.append(" + ".join(names))
                    except Exception as e:
                        errors.append(f"FFmpeg: {e}")
                if need_vlc:
                    try:
                        logic.install_vlc_bundled()
                        installed.append("VLC")
                    except Exception as e:
                        errors.append(f"VLC: {e}")
                self.after(0, self.set_busy, False)

                def finish():
                    if download_btn.winfo_exists():
                        download_btn.configure(state="normal", text=original_text)
                    if win.winfo_exists():
                        if errors:
                            messagebox.showerror("Download resources", "\n\n".join(errors))
                        else:
                            messagebox.showinfo("Download resources", f"{' and '.join(installed)} installed successfully.")

                self.after(0, finish)

            threading.Thread(target=work, daemon=True).start()

        download_btn.configure(command=do_install_resources)
        download_btn.pack(anchor="w")

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # ------------------------------------------------------------------
        # 6. Text Size
        # ------------------------------------------------------------------
        ttk.Label(body, text="6. Text Size", font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(0, 4))

        default_idx = 1
        for i, (_name, size) in enumerate(TEXT_SIZE_OPTIONS):
            if size == pending["font_size"]:
                default_idx = i
                break

        current_label_var = tk.StringVar()

        def _label_for(idx):
            name, _size = TEXT_SIZE_OPTIONS[idx]
            return f"Selected: {name}"

        current_label_var.set(_label_for(default_idx))
        ttk.Label(body, textvariable=current_label_var).pack(anchor="w", pady=(0, 4))

        # showvalue/tickinterval off — the numeric 0..4 index means nothing
        # to the user; the Small/Default/Medium/Large/Grandma row below the
        # slider is the only labeling that should show.
        scale = tk.Scale(body, from_=0, to=len(TEXT_SIZE_OPTIONS) - 1, resolution=1,
                          orient="horizontal", showvalue=False, length=440, tickinterval=0)
        scale.set(default_idx)
        scale.pack(anchor="w")

        names_row = tk.Frame(body)
        names_row.pack(anchor="w")
        name_labels = []
        for name, _size in TEXT_SIZE_OPTIONS:
            lbl = tk.Label(names_row, text=name, font=("Segoe UI", 10))
            name_labels.append(lbl)

        def position_name_labels():
            """Places each name label so its horizontal center lines up
            with the slider's actual tick position for that value — not
            just evenly spread across the row, which doesn't match where
            the slider itself actually stops at each notch (its ends are
            inset by half the thumb's width). The first/last labels anchor
            by their edge instead of their center, so "Grandma" (the
            widest name) doesn't overflow past the row's right edge and
            get clipped — it still lines up with that tick, just growing
            inward instead of spilling off both sides of it.
            """
            scale.update_idletasks()
            width = scale.winfo_width()
            thumb = 30  # tk.Scale's default sliderlength
            names_row.configure(width=width, height=22)
            names_row.pack_propagate(False)
            usable = max(1, width - thumb)
            n = len(TEXT_SIZE_OPTIONS)
            for i, lbl in enumerate(name_labels):
                x = thumb / 2 + i * usable / (n - 1)
                if i == 0:
                    lbl.place(x=x, y=0, anchor="nw")
                elif i == n - 1:
                    lbl.place(x=x, y=0, anchor="ne")
                else:
                    lbl.place(x=x, y=0, anchor="n")

        win.after(1, position_name_labels)

        def on_slider_move(val):
            # Only stages the choice and updates the preview label — no
            # live font change and no save until Apply/Restart.
            idx = int(round(float(val)))
            current_label_var.set(_label_for(idx))
            pending["font_size"] = TEXT_SIZE_OPTIONS[idx][1]

        scale.configure(command=on_slider_move)

        # ------------------------------------------------------------------
        # Apply / Restart
        # ------------------------------------------------------------------
        def has_unsaved_changes():
            return (pending["folder"] != (self.osu_songs_folder or "")
                    or pending["font_size"] != getattr(self, "current_font_size", 14)
                    or pending["index_mode"] != self.song_index_mode
                    or pending["confirm_pattern_delete"] != self.confirm_pattern_delete
                    or pending["live_sync"] != self.live_sync_enabled)

        def do_apply():
            changed_folder = pending["folder"] != (self.osu_songs_folder or "")
            mode_changed = pending["index_mode"] != self.song_index_mode
            if mode_changed:
                self.song_index_mode = pending["index_mode"]
                save_song_index_mode(pending["index_mode"])
            if changed_folder and pending["folder"]:
                self.osu_songs_folder = pending["folder"]
                save_osu_folder_config(pending["folder"])
                self.song_index = []
                self._indexed_once = False
                self._indexed_full = False
                self._start_indexing_per_mode()
            elif mode_changed and not self._indexed_once and not self._indexing:
                # Folder didn't change, but the mode did — if there's no
                # index built yet, kick one off now to match the newly
                # selected mode instead of waiting for some other trigger.
                self._start_indexing_per_mode()
            self._refresh_manual_index_button()
            if pending["font_size"] != getattr(self, "current_font_size", 14):
                self.current_font_size = pending["font_size"]
                self._scale_up_fonts(pending["font_size"])
                save_font_size_config(pending["font_size"])
            if pending["confirm_pattern_delete"] != self.confirm_pattern_delete:
                self.confirm_pattern_delete = pending["confirm_pattern_delete"]
                save_confirm_pattern_delete(pending["confirm_pattern_delete"])
            if pending["live_sync"] != self.live_sync_enabled:
                self._set_live_sync_enabled(pending["live_sync"])

        def apply_and_notify():
            do_apply()
            from screens import show_toast
            show_toast(win, "Settings Applied", display_ms=1000)

        def do_restart():
            if not messagebox.askyesno(
                "Restart to apply",
                "This will save your settings and restart the app. Continue?",
            ):
                return
            do_apply()
            self.destroy()
            _relaunch_process()

        def on_close():
            # Guards against duplicate confirmation dialogs if the close
            # button gets clicked again while the first one is still up —
            # without this, repeated clicks could queue up multiple
            # WM_DELETE_WINDOW events before the first dialog's own grab
            # fully engages, spawning several stacked prompts.
            if getattr(win, "_closing", False):
                return
            win._closing = True
            try:
                if has_unsaved_changes():
                    resp = messagebox.askyesnocancel(
                        "Unsaved changes",
                        "You have unsaved settings changes. Save before closing?",
                    )
                    if resp is None:
                        return  # Cancel — stay open
                    if resp:
                        do_apply()
                win.destroy()
            finally:
                win._closing = False

        btn_row = ttk.Frame(body)
        btn_row.pack(anchor="e", pady=(16, 0))
        ttk.Button(btn_row, text="Apply", command=apply_and_notify).pack(side="right")
        ttk.Button(btn_row, text="Restart", command=do_restart).pack(side="right", padx=(0, 6))

        # ------------------------------------------------------------------
        # Footer credit
        # ------------------------------------------------------------------
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(12, 0))
        tk.Label(footer, text="From Amasugi.", font=("Segoe UI", 8)).pack(anchor="center")
        credit_link = tk.Label(footer, text="App Icon", font=("Segoe UI", 8, "underline"),
                                fg="#3366cc", cursor="hand2")
        credit_link.pack(anchor="center")
        credit_link.bind(
            "<Button-1>",
            lambda e: webbrowser.open(
                "https://www.deviantart.com/shingaishima/art/Taiko-no-Tatsujin-Don-and-Katsu-618160106"
            ),
        )

        # Re-position/re-size using the body's *actual* required height now
        # that every section is built, instead of the rough guess passed
        # above — a hand-picked pixel height silently clips whichever
        # section ends up at the bottom (the footer) as content is added or
        # font metrics change, since this window is fixed-size/non-scrolling.
        # A small buffer is added on top of the raw requested height since
        # it can otherwise land a couple pixels short of what's actually
        # needed once the window manager's own border/title-bar chrome is
        # accounted for, clipping the last widget packed (the footer link).
        win.update_idletasks()
        _position_over_window(win, self, width=560, height=win.winfo_reqheight() + 16)

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.bind("<Escape>", lambda e: on_close())
        win.transient(self)
        win.lift()
        win.focus_force()
        win.grab_set()
        self._settings_win = win

    # ------------------------------------------------------------------
    # First-time setup
    # ------------------------------------------------------------------
    def _maybe_show_first_time_setup(self):
        if not load_first_run_done():
            self.open_first_time_setup()

    def open_first_time_setup(self):
        existing = getattr(self, "_first_time_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        win = tk.Toplevel(self)
        win.title("First time setup")
        win.resizable(False, False)

        pending = {"folder": self.osu_songs_folder or ""}

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=20, pady=20)

        ttk.Label(body, text="First time setup", font=("Segoe UI", 20, "bold")).pack(anchor="w", pady=(0, 16))

        # ------------------------------------------------------------------
        # 1. Choose your osu! Songs folder
        # ------------------------------------------------------------------
        ttk.Label(body, text="1. Choose your osu! Songs folder",
                  font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 8))

        folder_row = ttk.Frame(body)
        folder_row.pack(fill="x", pady=(0, 6))
        folder_var = tk.StringVar(value=pending["folder"])
        folder_entry = ttk.Entry(folder_row, textvariable=folder_var)
        folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        set_btn = ttk.Button(folder_row, text="Set", state="disabled")

        status_var = tk.StringVar()
        status_label = tk.Label(body, textvariable=status_var, fg="red", wraplength=540, justify="left")
        status_label.pack(anchor="w", pady=(0, 16))

        def refresh_status():
            if pending["folder"] and os.path.isdir(pending["folder"]):
                status_var.set(f"osu! Songs folder has been set to {pending['folder']}")
                status_label.configure(fg="black")
            else:
                status_var.set("")

        refresh_status()

        def do_set():
            path = folder_var.get().strip()
            if not is_valid_osu_songs_folder(path):
                messagebox.showwarning(
                    "Invalid folder",
                    "That doesn't look like an osu! Songs folder — the path must "
                    "end in \"osu!\\Songs\" (e.g. C:\\osu!\\Songs). Pick the actual "
                    "Songs folder inside your osu! install.",
                )
                return
            pending["folder"] = path
            refresh_status()
            set_btn.configure(state="disabled")

        set_btn.configure(command=do_set)

        def browse():
            chosen = filedialog.askdirectory(title="Select your osu! Songs folder",
                                              initialdir=folder_var.get() or None)
            if chosen:
                folder_var.set(chosen)
                set_btn.configure(state="normal")

        ttk.Button(folder_row, text="Browse", command=browse).pack(side="left", padx=(0, 6))
        set_btn.pack(side="left")

        # Typing directly in the field should also re-enable Set, in case
        # someone pastes/edits a path instead of using Browse.
        folder_var.trace_add("write", lambda *a: set_btn.configure(state="normal"))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, 16))

        # ------------------------------------------------------------------
        # 2. Download extra resources
        # ------------------------------------------------------------------
        from screens import InfoIcon, show_toast

        download_header_row = ttk.Frame(body)
        download_header_row.pack(anchor="w", pady=(0, 4))
        ttk.Label(download_header_row, text="2. Download extra resources",
                  font=("Segoe UI", 14, "bold")).pack(side="left")
        InfoIcon(download_header_row,
                 "Including:\n\n"
                 "- ffmpeg + ffprobe\n"
                 "- VLC Player").pack(side="left", padx=(6, 0))

        ttk.Label(body, text="These are required to run certain features in this tool. "
                              "You can install them now or later in Settings.",
                  wraplength=540, justify="left").pack(anchor="w", pady=(0, 8))

        download_btn = ttk.Button(body, text="Install Automatically")

        def do_install_resources():
            original_text = "Install Automatically"

            # Checked independently — audio_tools_fully_available() only
            # says "is anything missing", not *which* binary, so a system
            # with ffmpeg but no ffprobe (or vice versa) needs to still
            # trigger an install that fetches just the missing one, not
            # get short-circuited by treating the pair as one unit.
            need_ffmpeg_bin = not logic.ffmpeg_available()
            need_ffprobe_bin = not logic.ffprobe_available()
            need_vlc = not logic.vlc_available()
            if not need_ffmpeg_bin and not need_ffprobe_bin and not need_vlc:
                messagebox.showinfo("Download resources", "ffmpeg, ffprobe and VLC are already installed.")
                return

            download_btn.configure(state="disabled", text="Installing...")

            def work():
                self.after(0, self.set_busy, True,
                           "Installing ffmpeg + ffprobe and VLC (may take a few minutes)... Please wait...")
                errors = []
                installed = []
                if need_ffmpeg_bin or need_ffprobe_bin:
                    try:
                        logic.install_ffmpeg_suite_bundled(need_ffmpeg_bin, need_ffprobe_bin)
                        names = [n for need, n in ((need_ffmpeg_bin, "ffmpeg"), (need_ffprobe_bin, "ffprobe")) if need]
                        installed.append(" + ".join(names))
                    except Exception as e:
                        errors.append(f"FFmpeg: {e}")
                if need_vlc:
                    try:
                        logic.install_vlc_bundled()
                        installed.append("VLC")
                    except Exception as e:
                        errors.append(f"VLC: {e}")
                self.after(0, self.set_busy, False)

                def finish():
                    if download_btn.winfo_exists():
                        download_btn.configure(state="normal", text=original_text)
                    if win.winfo_exists():
                        if errors:
                            messagebox.showerror("Download resources", "\n\n".join(errors))
                        else:
                            messagebox.showinfo("Download resources", f"{' and '.join(installed)} installed successfully.")

                self.after(0, finish)

            threading.Thread(target=work, daemon=True).start()

        download_btn.configure(command=do_install_resources)
        download_btn.pack(anchor="w")

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(16, 16))

        # ------------------------------------------------------------------
        # All set!
        # ------------------------------------------------------------------
        def do_all_set():
            if not is_valid_osu_songs_folder(pending["folder"]):
                messagebox.showwarning(
                    "osu! Songs folder not set",
                    "You must set osu! Songs folder before proceeding.",
                )
                return
            if pending["folder"] != (self.osu_songs_folder or ""):
                self.osu_songs_folder = pending["folder"]
                save_osu_folder_config(pending["folder"])
                self.song_index = []
                self._indexed_once = False
                self._indexed_full = False
                self._start_indexing_per_mode()
                self._refresh_manual_index_button()
            save_first_run_done()
            win.destroy()
            self._first_time_win = None
            show_toast(self, "Settings saved!")

        ttk.Button(body, text="All set!", command=do_all_set).pack(anchor="center")

        win.update_idletasks()
        _position_over_window(win, self, width=600, height=win.winfo_reqheight() + 16)

        win.transient(self)
        win.lift()
        win.focus_force()
        win.grab_set()
        self._first_time_win = win

    def _search_tools(self, query):
        query = query.strip().lower()
        for item in SIDEBAR_ITEMS:
            if query and query in item.lower():
                self.show_frame(item)
                return True
        return False

    def _on_search_focus_in(self, _event=None):
        if self.search_var.get() == "Search osu! map...":
            self.search_var.set("")

    def _on_search(self):
        query = self.search_var.get().strip()
        if not query or query == "Search osu! map...":
            return
        # A tool name takes priority (cheap, instant); otherwise treat it as
        # a song search over the indexed Songs folder.
        if self._search_tools(query):
            return
        self.search_songs(query)

    def set_osu_folder(self):
        folder = filedialog.askdirectory(title="Select your osu! Songs folder")
        if folder:
            self.osu_songs_folder = folder
            save_osu_folder_config(folder)
            messagebox.showinfo(APP_TITLE, f"osu! Songs folder set to:\n{folder}")
            # A new folder needs its own fresh index — don't carry over
            # "already fully indexed" from whatever folder was set before.
            self.song_index = []
            self._indexed_once = False
            self._indexed_full = False
            self._start_indexing_per_mode()
            self._refresh_manual_index_button()

    def browse_for_map_folder(self):
        """Manually browse for any beatmap folder with the file explorer —
        handy when auto-detection (lightning button) isn't available or
        you want to work on a map that isn't currently open in osu!."""
        if not self.osu_songs_folder or not os.path.isdir(self.osu_songs_folder):
            messagebox.showwarning(APP_TITLE, "Set your osu! folder first (the hexagon button).")
            return
        folder = filedialog.askdirectory(
            title="Select a beatmap folder",
            initialdir=self.osu_songs_folder,
        )
        if folder:
            self._select_map_folder(folder)
            if self.live_sync_enabled:
                # Manually picking a map and live-sync fighting back over it
                # a second later (whenever osu!'s own selection next moves)
                # would be surprising — turn it off and tell the user why
                # their choice didn't stick if they don't see this toast.
                self._set_live_sync_enabled(False)
                from screens import show_toast
                show_toast(self, "Live-sync song select is disable! Visit settings to enable it again",
                           bg="#ff9800", fg="#000000", display_ms=3000)

    def pickup_current_map(self):
        """Tries to auto-detect the beatmap currently open in a running
        osu! stable client by reading its process memory (Windows only,
        best-effort — see osu_memory.py). Falls back to asking the user to
        pick the folder manually if that isn't possible for any reason."""
        if not self.osu_songs_folder or not os.path.isdir(self.osu_songs_folder):
            messagebox.showwarning(APP_TITLE, "Set your osu! folder first (the hexagon button).")
            return

        detected = None
        detected_filename = None
        try:
            import osu_memory
            result = osu_memory.get_current_beatmap_folder_and_diff(self.osu_songs_folder)
            if result:
                detected, detected_filename = result
        except Exception:
            detected = None
            detected_filename = None

        if detected:
            self._select_map_folder(detected, diff_filename=detected_filename)
            return

        folder = filedialog.askdirectory(
            title="Select the beatmap folder currently open in osu!"
            " (auto-detect unavailable — see README)",
            initialdir=self.osu_songs_folder,
        )
        if folder:
            self._select_map_folder(folder)

    def _set_live_sync_enabled(self, enabled: bool):
        self.live_sync_enabled = enabled
        save_live_sync_config(self.live_sync_enabled)
        if self.live_sync_enabled:
            # Forces the next poll to treat whatever osu! currently reports
            # as "new" even if it's unchanged since sync was last on, so
            # re-enabling snaps back to osu! immediately instead of waiting
            # for the next actual song-select change.
            self._last_live_sync_key = None

    def _poll_live_osu_map(self):
        """Runs for the app's whole lifetime (not tied to any one tab) so
        the "Now Selecting" label mirrors whichever map/diff is open in a
        running osu! stable client — including scrolling through song
        select — without needing the pickup (↑) button. Only acts when the
        detected map actually changed since the last poll, so it never
        fights a manual browse/search selection unless osu!'s own selection
        moves on."""
        if not self.winfo_exists():
            return
        if self.live_sync_enabled and self.osu_songs_folder and os.path.isdir(self.osu_songs_folder):
            try:
                import osu_memory
                result = osu_memory.get_current_beatmap_folder_and_diff(self.osu_songs_folder)
            except Exception:
                result = None
            if result and result != self._last_live_sync_key:
                self._last_live_sync_key = result
                self._select_map_folder(result[0], diff_filename=result[1])
        self.after(1000, self._poll_live_osu_map)

    # ------------------------------------------------------------------
    # Song search
    # ------------------------------------------------------------------
    def build_song_index(self, full: bool = False, limit: int = 100):
        """Builds a lightweight searchable index (artist/title/tags/mapper/
        background per beatmap set) in a background thread so the UI never
        freezes, even for large Songs folders. By default only indexes the
        `limit` most recently modified mapsets (a reasonable proxy for
        "latest downloaded") — call with full=True to index everything."""
        if self._indexing or not self.osu_songs_folder or not os.path.isdir(self.osu_songs_folder):
            return
        self._indexing = True
        self._refresh_manual_index_button()
        self._show_index_status("Index in progress")

        def work():
            index = []
            try:
                names = os.listdir(self.osu_songs_folder)
            except OSError:
                names = []

            # Determine which folders are taiko mapsets *before* limiting to
            # the most recent N — otherwise a library with lots of non-taiko
            # sets could yield far fewer than `limit` taiko entries. Checking
            # each diff's [General] Mode field is cheap (the reader stops
            # well before parsing notes/timing points), so doing this for
            # every folder up front is still fast overall.
            taiko_entries = []  # (mtime, name, folder, meta)
            for name in names:
                folder = os.path.join(self.osu_songs_folder, name)
                if not os.path.isdir(folder):
                    continue
                osu_files = osu_parser.list_difficulty_files(folder)
                if not osu_files:
                    continue
                meta = None
                for osu_file in osu_files:
                    candidate_meta = osu_parser.read_basic_metadata(os.path.join(folder, osu_file))
                    if candidate_meta and candidate_meta.get("Mode") == "1":
                        meta = candidate_meta
                        break
                if not meta:
                    continue
                try:
                    mtime = os.path.getmtime(folder)
                except OSError:
                    mtime = 0
                taiko_entries.append((mtime, name, folder, meta))

            if full:
                taiko_entries.sort(key=lambda e: e[1])  # alphabetical when doing everything
            else:
                taiko_entries.sort(key=lambda e: e[0], reverse=True)  # most recently modified first
                taiko_entries = taiko_entries[:limit]

            for _mtime, name, folder, meta in taiko_entries:
                artist, title = meta["Artist"], meta["Title"]
                romanised_artist = meta.get("RomanisedArtist", "") or artist
                romanised_title = meta.get("RomanisedTitle", "") or title
                display = f"{artist} - {title}".strip(" -") or name
                display_romanised = f"{romanised_artist} - {romanised_title}".strip(" -") or name
                blob = " ".join([
                    artist, title, romanised_artist, romanised_title,
                    meta.get("Mapper", ""), meta.get("Tags", ""), name,
                ]).lower()
                bg_path = None
                if meta.get("BackgroundFile"):
                    candidate = os.path.join(folder, meta["BackgroundFile"])
                    if os.path.exists(candidate):
                        bg_path = candidate
                index.append({
                    "folder": folder, "display": display, "display_romanised": display_romanised,
                    "blob": blob, "bg_path": bg_path,
                })
            self.song_index = index
            self._indexing = False
            self._indexed_once = True
            if full:
                self._indexed_full = True
            self.after(0, self._on_index_complete)

        threading.Thread(target=work, daemon=True).start()

    def _show_index_status(self, text: str):
        self.index_status_var.set(text)
        self.index_full_button.pack_forget()
        if not self.index_status_frame.winfo_ismapped():
            self.index_status_frame.pack(side="left", padx=(4, 0))
        self.index_progress.pack(side="left", padx=(0, 4))
        self.index_progress.start(10)
        self.search_entry.configure(state="disabled")
        self.search_btn.configure(state="disabled")

    def _on_index_complete(self):
        self.index_progress.stop()
        self.index_status_var.set("Index Complete!")
        self._refresh_manual_index_button()
        # Clear the status a couple seconds later so it doesn't linger
        # forever, but only if another index build hasn't started since.
        self.after(2500, self._clear_index_status_if_idle)

    def _clear_index_status_if_idle(self):
        if self._indexing:
            return
        self.index_progress.pack_forget()
        self.index_status_var.set("")
        if self._indexed_once and not self._indexed_full:
            # A full re-index is still worth offering — show the button
            # right where the progress bar was.
            self.index_full_button.pack(side="left")
        else:
            self.index_status_frame.pack_forget()

    def search_songs(self, query, _retries=0):
        existing = getattr(self, "_search_win", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return

        if self._indexing:
            if _retries > 100:  # ~20s safety cap
                messagebox.showinfo(APP_TITLE, "Still indexing — try searching again in a moment.")
                return
            self.after(200, lambda: self.search_songs(query, _retries + 1))
            return
        if not self.song_index:
            if not self.osu_songs_folder:
                messagebox.showwarning(APP_TITLE, "Set your osu! Songs folder first (⚙ Settings).")
                return
            if self._indexed_once:
                messagebox.showinfo(APP_TITLE, "No beatmaps found in your Songs folder.")
                return
            if self.song_index_mode == "manual":
                messagebox.showinfo(
                    APP_TITLE,
                    "Song Index on Startup is set to Manual Index — click "
                    "Start Indexing (next to the search box) first.",
                )
                return
            self.build_song_index()
            self.after(200, lambda: self.search_songs(query, _retries + 1))
            return

        q = query.strip().lower()
        matches = [entry for entry in self.song_index if q in entry["blob"]]
        if not matches:
            messagebox.showinfo(APP_TITLE, f'No songs matched "{query}".')
            return
        from screens import SongSearchResultsWindow
        self._search_win = SongSearchResultsWindow(self, matches, self._select_map_folder)

    def _select_map_folder(self, folder, diff_filename=None):
        """diff_filename, if given, is the specific difficulty to show in
        the "Now Selecting" label (e.g. whichever one is actually open in a
        live osu! editor, from pickup_current_map's auto-detect). Falls back
        to the highest-priority difficulty by taiko_diff_sort_key — matching
        the default DiffCheckList/DiffRadioList selection — for callers that
        only know the folder (manual browse, search)."""
        self.current_map_folder.set(folder)
        diffs = osu_parser.list_difficulty_files(folder)
        if diffs:
            chosen = diff_filename if diff_filename in diffs else None
            if chosen is None:
                display_map = osu_parser.get_diff_display_map(folder, diffs)
                ordered_labels = sorted(display_map.keys(), key=osu_parser.taiko_diff_sort_key)
                chosen = display_map[ordered_labels[0]] if ordered_labels else diffs[0]
            bm = osu_parser.Beatmap(os.path.join(folder, chosen))
            self._current_map_meta = bm.get_metadata()
            self.current_diff_filename = chosen
        else:
            self._current_map_meta = None
            self.current_diff_filename = None
        self._refresh_now_selecting_label(folder, diffs)
        for frame in self.frames.values():
            if hasattr(frame, "on_map_changed"):
                frame.on_map_changed()

    def _refresh_now_selecting_label(self, folder, diffs):
        if self._current_map_meta:
            meta = self._current_map_meta
            if self.use_romanised_display:
                artist = meta["RomanisedArtist"] or meta["Artist"]
                title = meta["RomanisedTitle"] or meta["Title"]
            else:
                artist, title = meta["Artist"], meta["Title"]
            label = f"Now Selecting: {artist} - {title}"
            if meta["Version"]:
                label += f" [{meta['Version']}]"
        else:
            label = f"Now Selecting: {os.path.basename(folder)} (no .osu files found)"
        self.now_selecting_var.set(label)

    def toggle_metadata_display(self):
        """Switches the "Now Selecting" label between original (unicode)
        and romanised metadata for the current map."""
        self.use_romanised_display = not self.use_romanised_display
        folder = self.current_map_folder.get()
        if folder:
            diffs = osu_parser.list_difficulty_files(folder)
            self._refresh_now_selecting_label(folder, diffs)

    def open_current_map_folder(self):
        """Opens the currently selected map's folder in the OS file
        explorer — the same "no map selected" message used everywhere
        else in the app if nothing's selected yet."""
        folder = self.current_map_folder.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("No map selected", "Please select a map first!")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as e:
            messagebox.showwarning(APP_TITLE, f"Couldn't open the folder:\n{e}")

    def open_beatmap_page(self):
        """Opens the currently selected map's page on the osu! website, if
        it's a submitted map (BeatmapSetID present in the .osu file)."""
        folder, diffs = self.get_diff_files()
        if not folder or not diffs:
            messagebox.showwarning("No map selected", "Please select a map first!")
            return
        bm = osu_parser.Beatmap(os.path.join(folder, diffs[0]))
        set_id = bm.get_beatmapset_id()
        if set_id is None:
            messagebox.showinfo(APP_TITLE, "This map has no beatmap page — it looks unsubmitted.")
            return
        webbrowser.open(f"https://osu.ppy.sh/beatmapsets/{set_id}")

    # ------------------------------------------------------------------
    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        self.sidebar = ttk.Frame(body, width=230)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.sidebar_buttons = {}
        n_items = len(SIDEBAR_ITEMS)
        for i, item in enumerate(SIDEBAR_ITEMS):
            btn = tk.Button(self.sidebar, text=item, anchor="w", relief="flat",
                             bg="white", activebackground="#e0e0e0",
                             font=("Segoe UI", 15), padx=10,
                             command=lambda i=item: self.show_frame(i))
            # weight each row equally so the whole column of buttons
            # stretches to fill the sidebar's full height
            self.sidebar.grid_rowconfigure(i, weight=1)
            btn.grid(row=i, column=0, sticky="nsew")
            self.sidebar_buttons[item] = btn
        self.sidebar.grid_columnconfigure(0, weight=1)

        self.container = ttk.Frame(body)
        self.container.pack(side="left", fill="both", expand=True)

    def _build_frames(self):
        from screens import (
            FrontPage, MetadataManagerFrame, VolumeKiaiCopierFrame, MapCleanerFrame,
            OffsetShifterFrame, BgOffsetShifterFrame, VideoOffsetShifterFrame,
            EarlyVolumeSettingFrame, PatternGalleryFrame, FileNameCheckerFrame,
        )
        tool_classes = [
            ("front", FrontPage),
            ("Metadata Manager", MetadataManagerFrame),
            ("Volume/Kiai Copier", VolumeKiaiCopierFrame),
            ("Map Cleaner", MapCleanerFrame),
            ("Audio/Offset Settings", OffsetShifterFrame),
            ("BG Settings", BgOffsetShifterFrame),
            ("Video Settings", VideoOffsetShifterFrame),
            ("Early Volume Settings", EarlyVolumeSettingFrame),
            ("Pattern Gallery", PatternGalleryFrame),
            ("File Name Checker", FileNameCheckerFrame),
        ]

        for name, cls in tool_classes:
            tool = cls(self.container, self)
            self.frames[name] = tool
            tool.place(x=0, y=0, relwidth=1, relheight=1)

    def show_frame(self, name):
        frame = self.frames.get(name)
        if frame is None:
            return
        prev_name = getattr(self, "_current_frame_name", None)
        if prev_name and prev_name != name:
            prev_frame = self.frames.get(prev_name)
            if prev_frame is not None and hasattr(prev_frame, "on_hidden"):
                prev_frame.on_hidden()
        self._current_frame_name = name
        frame.lift()
        for key, btn in self.sidebar_buttons.items():
            btn.configure(bg="#d0d0d0" if key == name else "white")
        if hasattr(frame, "on_shown"):
            frame.on_shown()

    # ------------------------------------------------------------------
    def get_diff_files(self):
        folder = self.current_map_folder.get()
        return folder, osu_parser.list_difficulty_files(folder)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
