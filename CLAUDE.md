# CLAUDE.md — osu!taiko Mapping Tools

Developer reference for AI coding agents working on this codebase.

---

## Project Overview

A Windows desktop utility for **osu!taiko mappers**, built with Python + tkinter.
It reads and writes `.osu` beatmap files directly, and can optionally read a running
osu!stable client's process memory (best-effort) to auto-detect the current map,
the current editor selection, and the current diff — no osu! API involved anywhere.

**Entry point:** `app/main.py` → run with `python main.py` from the `app/` directory.

---

## File Map

| File | Role |
|---|---|
| `app/main.py` | App shell: sidebar navigation, title bar buttons, search, settings, window layout, config persistence |
| `app/screens.py` | One `Frame` subclass per tool — all UI for each tool lives here, plus shared GUI widgets/helpers |
| `app/osu_parser.py` | `.osu` file parser/serializer + snap-to-beat logic + diff sorting. **No GUI.** |
| `app/tools_logic.py` | All tool behaviour (map mutations, pattern capture/insert, clipboard parsing). **No GUI, no file I/O beyond osu_parser.** |
| `app/osu_memory.py` | Best-effort osu! stable process memory reader (Windows only, optional) — currently only reads the *currently open beatmap folder/filename*, nothing deeper (see "What osu_memory.py can and can't do" below) |
| `app/requirements.txt` | `Pillow`, `pyinstaller`, optional `python-vlc`, optional `pymem` |
| `app/build_exe.bat` | PyInstaller one-shot build → `dist/osu_taiko_helper.exe` |

**Per-user config files** (all in `%USERPROFILE%`, plain text/JSON, written by `main.py`'s `load_*`/`save_*` pairs — see "Adding a new persisted setting" below):
`.osu_taiko_helper_config.txt` (Songs folder), `.osu_taiko_helper_fontsize.txt`, `.osu_taiko_helper_indexmode.txt`, `.osu_taiko_helper_confirmdelete.txt`, `.osu_taiko_helper_livesync.txt`, `.osu_taiko_helper_coords.json` (Map Cleaner coordinate presets), `.osu_taiko_helper_geometry.txt` (main window size/position, saved on close — see `App._on_close_request`), `.osu_taiko_helper_patterns.json` (Pattern Gallery library — see below).

---

## Architecture

```
main.py  ──────────────────────  App shell
   │  creates / switches between
   ▼
screens.py  ────────────────────  Tool UIs (one Frame per tool)
   │  calls
   ▼
tools_logic.py  ────────────────  Pure logic, operates on Beatmap objects
   │  uses
   ▼
osu_parser.py  ─────────────────  Beatmap read/write, TimingPoint/HitObject types, diff sorting
```

`osu_memory.py` sits outside this chain — it's only ever called from `tools_logic.py` (lazily, `import osu_memory` inside the function that needs it) or `main.py`, wrapped in `try/except`, and every entry point fails closed (returns `None`) rather than raising if osu! isn't running, `pymem` isn't installed, or the memory layout doesn't match what's expected.

**Key rule:** `tools_logic.py` has no tkinter imports, no GUI code, and does no file I/O itself — it only mutates `Beatmap` objects passed to it. The `screens.py` frame calls the logic, then calls `bm.save()`.

---

## Domain Knowledge

### .osu File Format

The `.osu` file is a plain-text format with section headers: `[General]`, `[Metadata]`, `[Difficulty]`, `[Events]`, `[TimingPoints]`, `[HitObjects]`.

**Timing points** (`[TimingPoints]` section), CSV format:
```
time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects
```
- `uninherited == 1` → **red line** (BPM / timing anchor)
- `uninherited == 0` → **green line** (inherited: SV, volume, kiai, sampleset)
- `beatLength` for green lines is negative: `-100 / SV_multiplier`. For example, `-71.4286...` = 1.4x SV.
- `effects & 1` → kiai is active

**Hit objects** (`[HitObjects]` section), CSV format:
```
x,y,time,type,hitSound,remainder...
```
- `type` bits: `1`=circle, `2`=slider, `4`=new combo, `8`=spinner, `0x80`=hold note (mania only, rarely relevant here).
- A **spinner or hold note**'s `remainder` starts with its `EndTime` as a raw absolute millisecond value (`"endTime,hitSample..."`) — unlike a slider's length (spatial, doesn't need adjusting when the object moves in time), this does. See "Pattern Gallery" below for where this matters.
- `hitSound` bits: `HS_WHISTLE=2`, `HS_FINISH=4`, `HS_CLAP=8`. In taiko: **kat** = whistle or clap set, **don** = neither; **finisher** = the finish bit, independent of don/kat.

**Background event** in `[Events]`:
```
0,0,"bg.jpg",xOffset,yOffset
```
- `yOffset=0` means the image is centered vertically within the visible band.
- Positive y shifts the image down (reveals more of the top); negative shifts up.

**SliderMultiplier** in `[Difficulty]` — this is the base SV (also called "base scroll velocity"). A value of `1.4` is standard for many taiko maps.

**BeatmapSetID** in `[Metadata]` — `osu_parser.Beatmap.get_beatmapset_id()` reads it, returning `None` if missing or `-1` (osu!'s marker for an unsubmitted/local-only map). Used to build the `https://osu.ppy.sh/beatmapsets/{id}` link for the 🔗 title-bar button.

### Rounding

osu!stable uses **"round half away from zero"** (not Python's banker's rounding). Always use `osu_parser.stable_round()` when computing times that must match the game's own snapping.

### Snap-to-beat

`osu_parser.snap_time(time, timing_points, divisor_key)` snaps a timestamp to the nearest grid tick for the given divisor. Compound divisors (1/12, 1/24, 1/36, 1/48) snap to the union of their component ticks — matching osu!'s own editor coloring. `DIVISOR_BASES` in `osu_parser.py` defines the base subdivisions per key; `_governing_timing_point(time, uninherited_points)` finds the active red line for a given time (also reused by Pattern Gallery's BPM-matching, see below).

### Taiko difficulty sort order

`osu_parser.taiko_diff_sort_key(label)` ranks a `[Version]` name against `TAIKO_DIFF_PRIORITY` (`Kantan → Futsuu → Muzukashii → Oni → Inner Oni → Outer Oni → Ura Oni → Hell Oni → Heavenly Oni`), matched by substring with the longest/most specific match winning. Two extra tiers below that:
1. Anything matching **none** of the priority list (e.g. "Extra") — alphabetical.
2. A label that only matches the generic `"Oni"` entry by *containing* the word inside a custom name (e.g. "Devil Oni") — **always sorts last**, alphabetical among itself. This is deliberately distinguished from the real "Oni" difficulty, which must match *exactly* (case-insensitive, trimmed). **Exempted from this demotion:** a *possessive* name directly before "Oni" — "Itsuki's Oni", "Ceras' Oni" — sorts at the **same tier as plain "Oni"** instead, since it still reads as someone's version of that difficulty rather than an unrelated custom name that happens to contain the word. Detected by `_POSSESSIVE_ONI_RE` (`osu_parser.py`): a word character immediately followed by `'`/`’` (optionally `'s`), whitespace, then "oni".

Used by **`DiffCheckList`** (multi-select "Apply to:" checklists) and **`DiffRadioList`** (single-select "Apply to:", used by Pattern Gallery — see below) — both sort with this key. Single-select `ttk.Combobox` dropdowns elsewhere (Map Cleaner's "Selected diff", the "Copy from" combo) also use it now; don't reintroduce plain alphabetical-by-filename sorting for a diff picker.

### TimingPoint is a dataclass — it is unhashable

`TimingPoint` uses `@dataclass` which auto-generates `__eq__`, making instances **unhashable** (can't be put in a `set` or used as a `dict` key directly). Use `id(tp)` when you need to track specific `TimingPoint` instances in a set:

```python
# WRONG — raises TypeError: unhashable type: 'TimingPoint'
kiai_set = set()
kiai_set.add(tp)

# CORRECT
kiai_set = set()
kiai_set.add(id(tp))
# ...
if id(tp) in kiai_set: ...
```

This same pattern is used in `push_green_lines`, `resnap_important_green_lines`, and `run_early_volume_setting`.

### Kiai "toggle" green lines

A kiai-toggle green line is one where the kiai state *changes* compared to the last kiai state seen. To identify them, iterate timing points in time order and track `last_kiai`:

```python
kiai_changing_ids = set()
last_kiai = None
for tp in sorted(bm.timing_points, key=lambda t: t.time):
    kiai = tp.effects & 1
    if tp.uninherited == 0 and (last_kiai is None or kiai != last_kiai):
        kiai_changing_ids.add(id(tp))
    last_kiai = kiai
```

### "Red-line-supported" green lines

A green line is red-line-supported if it shares a timestamp (within ~1ms) with a red line. Always keep/preserve these:

```python
red_times = {tp.time for tp in bm.timing_points if tp.uninherited == 1}
is_red_supported = any(abs(rt - tp.time) < 1e-3 for rt in red_times)
```

### touch_reload

After every `bm.save()`, `osu_parser.touch_reload()` renames the file away and back. This triggers osu!stable's file watcher so pressing **F5** at song select picks up the changes without needing to navigate away.

### osu! stable timestamp format — always `mm:ss:mmm` (colon), not `mm:ss.mmm`

Every "type or paste a timestamp" field in the app (Map Cleaner's resnap section range, Early Volume Setting's From/To, Pattern Gallery's Target time) shares `parse_time_input()` (`tools_logic.py`) and `_validate_partial_time`/`_paste_time_field` (`screens.py`). The canonical, enforced format is **`mm:ss:mmm`** — a colon before the milliseconds — matching what osu!stable's own editor actually puts on the clipboard, *not* the `mm:ss.mmm` (period) form used by some other tools/docs. `parse_time_input` still tolerantly *accepts* a period on paste, but always normalizes the redisplayed text to colon form, and the per-keystroke typing validator (`_PARTIAL_TIME_RE` in `screens.py`) only allows colons. Don't reintroduce a period-based validator.

### osu! stable's editor clipboard (Ctrl+C) is a *timestamp string*, not hit object data

Pressing Ctrl+C in the editor puts one of these on the **OS clipboard**:
- Nothing selected: `"mm:ss:mmm - "` (just the cursor position)
- Objects selected: `"mm:ss:mmm (n1,n2,...) - "` — the leading timestamp is the **first (earliest) selected object's time**; the parenthesized numbers are per-object display indices that **reset periodically and are not globally unique** (the same number can recur later in the map) — only their *count* is meaningful, telling you how many objects are selected.

This is a documentation-only convenience feature of osu! itself (for pasting timestamped links into chat/forums) — it is **not** how osu!'s actual "copy/paste hit objects" works internally (that goes through a separate in-process object buffer, not the OS clipboard; confirmed by inspecting a decompiled reference tool, `Karoo13/EditorReader`, which reads it via `pClipboardL`/`pSelectedL` in editor memory — a completely different, version-fragile pointer chain, not attempted here). **Do not build a feature that assumes writing to the OS clipboard can make osu! paste real hit objects** — it can't.

What this clipboard string *is* reliably good for (and what Pattern Gallery is built on):
- `tools_logic.parse_editor_clipboard_selection(text)` → `(anchor_ms, count)` for a selection.
- `tools_logic.parse_osu_cursor_timestamp(text)` → `ms` — a **strict** matcher (exact format only, no surrounding junk tolerated) used specifically to auto-detect "the user just copied a timestamp in osu!" while polling the clipboard, so it won't false-trigger on unrelated clipboard content that merely contains digits. Contrast with the lenient `parse_time_input`, used for explicit paste actions into a field.
- Combined with `osu_memory.resolve_folder_and_filename()` (which beatmap file is actually open), the anchor time + count is enough to look up the **exact selected objects directly from the parsed `.osu` file** — no memory reading of hit object data needed at all. This assumes a **contiguous** selection (a scattered/non-contiguous selection isn't really "a pattern" anyway).

### What osu_memory.py can and can't do

Only `resolve_folder_and_filename()` (which beatmap folder + `.osu` filename is currently open) is implemented, ported from Piotrekol's `ProcessMemoryDataFinder` signatures (see the module docstring for full attribution) — this part is solid and used in several places (auto-pickup, Pattern Gallery's live-diff preselection and capture target). **Reading which hit objects are selected in the live editor, or the current editor cursor time, is *not* implemented** — that would need a completely different, much deeper, and version-fragile memory structure (confirmed via a live reverse-engineering session against a real running client: a promising-looking signature almost matched but resolved to stale/cached objects, and a direct struct-offset guess based on a decompiled reference tool didn't match this build's HitObject layout either). If asked to extend this, the clipboard-based approach above is dramatically more robust — prefer it.

**Forcing a save in the live client was tried and abandoned.** A `save_current_map_in_osu()` was built that synthesized a Ctrl+S keypress (`SendInput`, correctly-sized `INPUT` union, real scan codes via `MapVirtualKeyW`) aimed at the osu!.exe window, using `AttachThreadInput` + an Alt-tap to get past `SetForegroundWindow` restrictions. Confirmed via live testing against a real client: `SetForegroundWindow` and `SendInput` both fully succeeded (window became foreground, all 4 key events accepted, no UIPI rejection) — osu! still never saved, even though a real physical Ctrl+S on the same client saved fine. This is the fingerprint of osu! (or something in its input stack) distinguishing synthetic/injected input from real hardware input and ignoring the former, most likely a low-level anti-macro measure. Getting past that would need driver/HID-level input emulation indistinguishable from real hardware — not something to build here: it's the same mechanism a macro/cheat tool would need, directly fights a protection osu! is apparently applying on purpose, and is a much bigger fragility/maintenance commitment than this app takes on anywhere else. **Do not re-attempt synthetic-keystroke save-forcing** — if this comes up again, the fallback is what Pattern Gallery uses now: an `InfoIcon` reminder telling the user to save in osu! (Ctrl+S) themselves before Capture/Insert.

---

## GUI Conventions

### InfoIcon

Defined in `screens.py`. Drop one anywhere you want a hoverable `(i)` help tooltip:

```python
InfoIcon(parent, "Help text here.").pack(side="left", padx=(2, 0))
```
Supports `align="right"` for tooltips near screen edges.

### _add_hover_tooltip (screens.py)

For attaching the same floating-tooltip behavior directly to an *existing* widget (no separate "(i)" icon) — e.g. showing a value only on hover:
```python
_add_hover_tooltip(some_label, "Extra detail shown on hover")
```

### _add_tooltip (main.py)

For title-bar icon buttons:
```python
_add_tooltip(widget, "Label text")                   # left-aligned (default)
_add_tooltip(widget, "Label text", align="right")    # right-aligned
_add_tooltip(widget, "Label text", align="center")   # centered under button
```

### BaseToolFrame

All tool frames inherit from `BaseToolFrame` (defined in `screens.py`). Override these hooks:
- `on_shown()` — called when the user navigates to this tool
- `on_map_changed()` — called when a new map is loaded (fires for *every* map-selection path: osu! auto-pickup, manual folder browse, and search — all funnel through `main.py`'s `_select_map_folder`)
- `on_hidden()` — called when the user navigates **away** from this tool (added for Pattern Gallery's clipboard polling — see `main.py`'s `show_frame`, which tracks `_current_frame_name` and calls this on whichever frame was active before switching). Optional; most frames don't need it — only implement it if you started something in `on_shown()` that needs cleanup (a polling `after()` job, a `bind_all`, etc.).

Use `self.require_map()` at the start of any Apply handler — it shows a warning and returns `False` if no map is loaded.
Use `self.notify_done("Message")` to show a **toast banner** (see below) — not a dialog.
Use `self.body` as the parent for all widgets (scrollable content area).

### Toast notifications, not messagebox, for "it worked"

`notify_done()` calls `show_toast(self, message)` (`screens.py`) — a small lime-green borderless `Toplevel` that fades in (~250ms), holds for 2s, fades out, and destroys itself, positioned centered near the top of the main window. This **replaced** `messagebox.showinfo("Done", message)` everywhere — every tool's Apply confirmation is non-blocking now; the user never has to click a dialog closed for a routine success. Don't add a new `messagebox.showinfo` for a "success" case — call `self.notify_done(...)` instead. `messagebox.showwarning`/`showerror`/`askyesno` are still correct for actual warnings/errors/confirmations.

### Modal Toplevel windows must guard against duplicate opens

Every button that opens a `Toplevel` (Settings, BG/Video offset preview, the coordinate editors, the troubleshoot popup, search results) follows this pattern — check `main.py`'s `open_settings` for the canonical example:

```python
def open_thing(self):
    existing = getattr(self, "_thing_win", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        return
    win = tk.Toplevel(self)
    ...
    win.transient(parent)
    win.lift()
    win.focus_force()   # order matters: lift, then focus_force, then grab_set
    win.grab_set()
    self._thing_win = win
```

This exists because rapid double-clicks / held Enter/Space on the triggering button used to spawn multiple copies of the same window — the dedup guard plus `lift()`+`focus_force()` (which was previously missing on some windows, letting the *button* keep receiving repeat keypresses instead of the new window stealing focus) fixes both the root cause and the symptom. **Any new Toplevel-spawning button must follow this exact pattern.**

### DiffCheckList vs DiffRadioList

Both live in `screens.py`, both read from `self.app.get_diff_files()` and render one row per difficulty (ticked/labeled by `osu_parser.taiko_diff_sort_key` order):
- **`DiffCheckList`** — checkboxes, multi-select, all ticked by default. `.selected()` → `List[str]` (filenames). Use for "apply this change to several diffs at once" (most tools).
- **`DiffRadioList`** — radio buttons, single-select. `.selected()` → `str | None` (one filename). `.refresh(preselect_file=...)` accepts a specific filename to preselect instead of always defaulting to the first by priority — Pattern Gallery uses this to preselect whichever diff is *actually open in a live osu! editor* (via `osu_memory`), re-checked every time the tab is shown. Use for "this only ever targets one diff at a time."

### Shared timestamp field helpers (screens.py)

Any "type or paste a timestamp" `ttk.Entry` should reuse these three, not reimplement validation:
```python
vcmd_time = (self.register(_validate_partial_time), "%P")
entry = ttk.Entry(row, textvariable=var, width=15, validate="key", validatecommand=vcmd_time)
entry.bind("<<Paste>>", lambda e: _paste_time_field(self, var))
```
`_validate_partial_time` gates keystrokes (only digits/colons forming a *prefix* of `mm:ss:mmm` or a bare number, ≤15 chars). `_paste_time_field` intercepts `<<Paste>>` and replaces the raw clipboard text with just the recognized timestamp/number via `logic.parse_time_input`, instead of dumping raw (likely rejected) text in. On Apply, parse the field with `logic.parse_time_input(var.get())` and show `messagebox.showwarning("Warning", "Invalid timestamp input")` on `None` — matches the existing convention across all three tools that use this.

### Spinbox validation pattern

For numeric spinboxes that must reject text and auto-clamp on focus-out:

```python
# Registration (do once in __init__)
vcmd_int   = (self.register(self._validate_int),   "%P")
vcmd_float = (self.register(self._validate_float), "%P")

# Spinbox
sb = tk.Spinbox(..., validate="key", validatecommand=vcmd_int)
sb.bind("<FocusOut>", self._on_sb_focus_out)

# Validators
def _validate_int(self, P):
    return P == "" or P.isdigit()

def _validate_float(self, P):
    if P == "" or P.count('.') > 1: return P == ""
    return all(c.isdigit() or c == '.' for c in P)

# Clamp on focus-out
def _on_sb_focus_out(self, _event=None):
    try:
        val = int(self.var.get())
    except ValueError:
        val = DEFAULT
    self.var.set(str(max(MIN, min(MAX, val))))
```

### Child options (disabled until parent checked)

Pattern used by "Set base SV" → "Other" child, "Resnap all notes" → "Apply to this section only" child, and similar:
```python
def _sync_child_state(self):
    state = "normal" if self.parent_var.get() else "disabled"
    self.child_widget.configure(state=state)
```
Bind to `command=self._sync_child_state` on the parent checkbutton. If the child itself gates further grandchildren (e.g. the section-only checkbox gating its own From/To fields), chain the sync calls — see `MapCleanerFrame._sync_resnap_notes_state` → `_sync_resnap_section_state`.

---

## Adding a new persisted setting

Follow the existing `confirm_pattern_delete` example in `main.py` exactly:
1. A `..._CONFIG_PATH` constant (`os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_x.txt")`).
2. `load_x_config()` / `save_x_config(value)` module-level functions — `load_` always returns a sane default and swallows `OSError`.
3. `self.x = load_x_config()` in `App.__init__`.
4. In `open_settings()`: add `"x": self.x` to the `pending` dict, add a numbered section to the Settings window body (renumber subsequent sections!), stage changes into `pending["x"]` via the widget's `command=`, and add the diff-check to both `has_unsaved_changes()` and `do_apply()`.

---

## Map Cleaner — Adding New Options

When adding a new option to the Map Cleaner:

1. **`screens.py`** — `MapCleanerFrame.__init__`:
   - Add a `tk.BooleanVar` (or `StringVar`) in the variable block
   - Add a row (`rN = ttk.Frame(opts)`) with a `ttk.Checkbutton` and optional `InfoIcon`
   - Add the key to the `options` dict in `apply()`

2. **`tools_logic.py`** — `run_map_cleaner`:
   - Add `if options.get("your_key"):` → call your logic function
   - Update the docstring

3. Write the logic function in `tools_logic.py` taking `bm: Beatmap` as first arg. Mutate `bm.timing_points` or `bm.hit_objects` in place — `bm.save()` is called by the screen after `run_map_cleaner` returns.

---

## External Binary Dependencies (ffmpeg / ffprobe / VLC)

None of these three are guaranteed to be on the end user's PATH. `tools_logic.py`'s binary resolution (`_bundled_dir()` / `_resolve_binary(name)`) prefers a copy of `ffmpeg.exe`/`ffprobe.exe` sitting **directly next to the running app** (the frozen exe's own folder via `sys.frozen`/`sys.executable`, or this `.py` file's folder when running from source) over whatever's on PATH — so a distribution that ships both binaries alongside the exe works out of the box regardless of what (if anything) is installed system-wide. Every ffmpeg/ffprobe subprocess call in `tools_logic.py` goes through `_resolve_binary("ffmpeg")`/`_resolve_binary("ffprobe")` — never a bare `"ffmpeg"` string — so a fresh install is picked up immediately without needing an app restart. VLC's resolution works differently — see its own subsection below, since python-vlc has no equivalent "check next to the exe" behavior built in.

- `ffmpeg_available()` / `ffprobe_available()` / `audio_tools_fully_available()` (both) — presence checks, bundled-first-then-PATH via `_binary_available`.
- **ffprobe is optional, not required** — `_probe_audio()` tries `_ffprobe_probe()` (structured JSON, `-show_entries` + `-of json`) first since it's strictly more reliable than parsing human-readable text, but falls back to `_ffmpeg_probe()` (regexing `ffmpeg -i`'s own stderr banner for the `Stream #.../Audio:.../Hz.../kb/s` line) when ffprobe isn't resolvable — so the app degrades gracefully rather than failing outright when only `ffmpeg.exe` is present. `_probe_audio_stream_info()` (codec/sample-rate/channels) and `get_audio_bitrate_kbps()` are both built on `_probe_audio()`.
- **`_download_to_file(url, dest_path)`** — the shared low-level downloader every direct-download path below is built on. Follows real HTTP redirects (which `urlopen` already does) *and* "meta refresh" HTML redirect pages, which it doesn't — confirmed for real during development that VideoLAN's mirror selector sometimes answers a direct download URL with `200 OK` + an HTML page containing `<meta http-equiv="refresh" content="5;URL='...'">` instead of a proper `3xx`, which silently produced a 29KB HTML file named `vlc.zip` before this was added. Sends a browser-like `User-Agent` — some hosts 503/403 Python's default `urllib` UA (confirmed for ffmpeg's gyan.dev source too).
- **`install_ffmpeg_suite_bundled()`** — meant to run on a worker thread (blocks for as long as winget/the download takes, up to 600s for winget). If both binaries are already resolvable via PATH (a prior separate install), just copies them into `_bundled_dir()` directly — no reinstall. Otherwise, **if winget is on PATH**, runs `winget install -e --id Gyan.FFmpeg --silent` (this package's own build includes `ffmpeg.exe` *and* `ffprobe.exe` together, which is exactly why this specific package id was chosen over any ffmpeg-only source), then locates the result — first via a fresh `shutil.which` (works if this process's env happens to pick up the PATH change), falling back to `_find_winget_ffmpeg_suite()` which globs winget's own package directory (`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\**\ffmpeg.exe`) directly, since a PATH change made by an external process doesn't propagate into this already-running one. **If winget isn't available at all** (confirmed to happen in practice — not every Windows install has it) **or its install still didn't yield both binaries**, falls through to `_download_and_extract_ffmpeg_zip()` instead of just giving up. Raises `RuntimeError` with a user-facing message on any failure — every caller shows this string directly via `messagebox.showerror`.
- **`_download_and_extract_ffmpeg_zip()`** — the winget-free path. Tries each URL in `FFMPEG_DIRECT_DOWNLOAD_URLS` in order (currently gyan.dev's stable "essentials" build URL, then BtbN's GitHub-hosted rolling "latest" release as a second source) via `_download_and_extract_ffmpeg_zip_from()`, stopping at the first success. **Two independent sources, not one** — confirmed for real during development that a single host can be a bad bet: gyan.dev returned a plain `503 Service Unavailable` with `Retry-After: 600` at one point (an ordinary transient overload, nothing wrong with the URL itself), which would have made "Install automatically" fail outright with only one source wired up. Each attempt downloads the zip to a temp file, then pulls just `ffmpeg.exe`/`ffprobe.exe` out of its `bin/` subfolder — found by *suffix* match (`.../bin/ffmpeg.exe`) since the zip's top-level folder is version-named (e.g. `ffmpeg-7.1-essentials_build/`) and would otherwise need updating every release. Verified end-to-end against the real BtbN URL (gyan.dev happened to be down during testing, so this incidentally also verified the fallback triggers correctly): downloaded, extracted, and ran both resulting binaries' `-version` successfully.

### VLC bundling — why it needs an env-var override instead of just copying files

Read directly from the installed `python-vlc` package's own source (`vlc.py`'s `find_lib()`): unlike ffmpeg, it **never checks next to the running app's own exe**. On Windows it resolves libvlc in this order: the `PYTHON_VLC_LIB_PATH`/`PYTHON_VLC_MODULE_PATH` env vars, `ctypes.util.find_library` (≈ PATH), the `Software\VideoLAN\VLC` registry key (`InstallDir` value — this is why a normal *system* VLC install just works with zero effort from this app, confirmed against a real system install during development), then a couple of hardcoded Program Files paths. So simply copying `libvlc.dll` next to the app the way `ffmpeg.exe` gets copied would silently do nothing — `find_lib()` would never look there. `configure_bundled_vlc_env()` instead points the two officially-supported env vars at a bundled copy (`os.environ.setdefault`, so an explicit user-set value always wins), and **must run before the first `import vlc` anywhere in the process**, since `find_lib()` only runs once, at that first import's module-execution time. `vlc_available()` is the one function that both performs this setup *and* does the check (`configure_bundled_vlc_env()` then attempts `import vlc`) — every other call site should go through it rather than importing `vlc` directly, to guarantee the env vars are always in place first. Verified end-to-end during development: downloaded a real portable build with zero system VLC install present, extracted it, then successfully created a `vlc.Instance()` and `media_player_new()` purely through this override.

- **`install_vlc_bundled()`** — returns early (`"system"`) if `vlc_available()` already holds (nothing to do). Otherwise, if winget is on PATH, runs `winget install -e --id VideoLAN.VLC --silent`; **unlike ffmpeg, a successful winget install needs nothing copied afterward** — VLC's real installer (what winget runs under the hood) sets the registry key `find_lib()` already checks fresh on every import, so winget succeeding is already a complete fix by itself (confirmed via `vlc_available()` immediately afterward). Only falls through to `_download_and_extract_vlc_zip()` (a portable copy next to the app, wired up via `configure_bundled_vlc_env`) when winget isn't available, or a winget "success" still doesn't leave VLC detectable.
- **`_download_and_extract_vlc_zip()`** — `_find_latest_vlc_zip_url()` first scrapes `https://get.videolan.org/vlc/last/win64/`'s directory listing for the current version-named zip filename (e.g. `vlc-3.0.23-win64.zip` — there's no fixed "always current" filename the way ffmpeg's sources provide), then downloads and extracts `libvlc.dll`/`libvlccore.dll` plus the entire `plugins/` folder into the bundled dir — everything `configure_bundled_vlc_env` points at. The zip's top-level folder is version-named (`vlc-3.0.23/...`) so both the DLLs and the plugins subtree are found by locating that folder via `libvlc.dll`'s own path first, same suffix-search spirit as the ffmpeg zip handling.

### The "you need X, want to install it?" gate — `_ask_choice_dialog` and its three uses

`_ask_choice_dialog(parent, title, message, options)` (`screens.py`) is a generic modal `Toplevel` with one full-width button per `(label, value)` pair — `messagebox` can't do custom button labels, so this exists instead of three near-identical dialogs. Closing via the X button or Escape always returns `options[-1][1]`, which is why every caller puts `("Cancel", "cancel")` last. Three thin wrappers, each gating a different tool right where its Apply/Preview action starts, *before* anything else runs:

- **`_ask_silence_quality_choice()`** — gates `OffsetShifterFrame.apply()`'s "Add silence" checkbox on `logic.audio_tools_fully_available()`. **Three** options, since there's a real degraded-but-working fallback here (see `add_silence_to_audio` below): Install automatically / **Proceed with quality loss** / Cancel.
- **`_ask_ffmpeg_required_choice()`** — gates the Taiko Video Resizer's "Resize for taiko" checkbox (inside `VideoOffsetShifterFrame.apply()`) on `logic.ffmpeg_available()`. **Two** options only — resizing can't happen at all without ffmpeg, so there's no "proceed anyway" to offer: Install automatically / Cancel.
- **`_ask_vlc_required_choice()`** — gates `VideoOffsetShifterFrame.open_preview()` on `logic.vlc_available()`, same two-option shape as ffmpeg's — no VLC, no preview, nothing to degrade to.

All three "Install automatically" paths run the relevant `install_*_bundled()` call on a worker thread with the shared busy overlay (`self.app.set_busy(True, "Installing ... Please wait...")`), then — on success — continue straight into the original action (Apply / open the preview window) without needing a second click. On failure, the error is surfaced via `messagebox.showerror` using a `str(e)` captured *before* scheduling the deferred lambda (see the Common Gotchas entry on this — getting it wrong here silently swallows the error instead of showing it).

`show_troubleshoot_window()` (behind every "The tool is not working?" link) is a *separate*, always-available path to the same installers — not gated on anything currently being missing, just always offers both "Install FFmpeg automatically" and "Install VLC automatically" buttons alongside their manual-download links. Both route through the shared `_run_bundled_install(master, win, button, install_fn, tool_name, busy_msg)` helper, which disables the clicked button and swaps its text to "Installing..." for the duration (so a second click can't start an overlapping install) and reports the result via a plain `messagebox` (not `notify_done`'s toast, since this popup isn't tied to any particular tool screen being visible).

### `add_silence_to_audio` — why it needs both ffmpeg and ffprobe, and what happens without them

Prepending silence forces *some* re-encode — pure lossless silence-prepending (stream-copying the original audio bit-for-bit, only touching a separately-generated silent lead-in) is provably safe for **.mp3 only**: confirmed by direct testing (decoding the result and diffing PCM samples against the original) that ffmpeg's concat demuxer with `-c copy` produces a byte-identical mp3 elementary stream, since raw MP3 frame concatenation has no container to worry about. **The same trick was tried for `.ogg` and is NOT safe** — an Ogg file has its own container framing (serial numbers, page sequencing, granule positions), and naively concatenating two independently-encoded Ogg files this way produces an invalid *chained* bitstream that decodes with continuous `Overread N bits` errors from the second segment onward, i.e. it actively **corrupts** the real song audio rather than just risking it. `_SILENCE_CODEC_EXT = {"mp3": ".mp3"}` reflects exactly this — mp3 gets the lossless concat path (`_concat_file_line`'s `file 'file:...'` list format sidesteps ffmpeg misreading a Windows drive letter as a URL scheme), everything else (ogg included) falls through to the old `adelay`-filter re-encode, at least bitrate-matched to the source via `get_audio_bitrate_kbps` so the one unavoidable lossy pass is as close to transparent as achievable — this is the fallback that's silently much worse (encoder-default quality, often far below the source's real bitrate) if neither `get_audio_bitrate_kbps` nor the fallback banner-parse can determine the source bitrate at all, which is exactly the scenario the quality-loss gate above exists to head off.

---

## Audio Re-encode

A separate section within Audio/Offset Settings (`OffsetShifterFrame`, `screens.py`) — its own header, its own **Apply** button, entirely decoupled from the offset-shift/Add-silence Apply above it (an earlier version bundled it into that same apply via `apply_offset`'s own `reencode_bitrate_kbps` param; that coupling is gone — `apply_offset` no longer takes a reencode argument at all). Two source radio buttons:

- **"Use audio from the currently selected song"** (default) — `logic.apply_audio_reencode_to_map(folder, bitrate_kbps)`. Re-encodes the current map's own audio file **in place**: same filename unless the extension changes (see `_reencode_target_ext` below), in which case the old file is deleted and `AudioFilename` is rewritten across **every** diff in the set (`osu_parser.list_difficulty_files`, not just whichever diffs happen to be selected elsewhere in the screen — this section has no diff checklist of its own, since the audio file is shared map-wide regardless).
- **"Use other audio file"** — a **Browse...** button (grayed out unless this radio is selected, via `_sync_reencode_source_state`, the standard "child options" pattern) opens `filedialog.askopenfilename` with a broad audio-extension filter (mp3/ogg/wav/flac/m4a/aac/wma/opus/aiff/alac/ac3 plus "All files") — picking a file shows its name next to the button. This path is **not** map-scoped at all: `logic.apply_audio_reencode_external(src, bitrate_kbps)` re-encodes the picked file and writes the result *alongside* it (same folder as the source, not the map folder) as `"<original base>_<bitrate>kbps<ext>"`, deliberately never touching/overwriting the original — since the whole point is exporting a converted copy the user can grab, not silently mutating a file they picked from anywhere on disk. On success, `_reveal_in_explorer(out_path)` opens that folder with the exported file pre-selected (`explorer /select,<path>` on Windows), so the user immediately sees the result sitting next to the file they chose. `apply()`'s "map" path doesn't do this — there's no arbitrary external folder to reveal, and the existing toast notification is enough for an in-place map change.

Both paths share `_reencode_target_ext(source_ext, bitrate_kbps)`, which decides the output extension:
```python
return ".ogg" if (source_ext.lower() != ".mp3" or bitrate_kbps == 208) else ".mp3"
```
i.e. a `.mp3` source stays `.mp3` **unless** 208kbps is picked (208 isn't a real mp3 bitrate, so that combination always converts to `.ogg`); every other source format — `.ogg` already, or anything else ffmpeg can read (wav, flac, m4a, ...) — always becomes `.ogg` regardless of which bitrate is picked. Verified end-to-end against real ffmpeg for all four combinations: mp3+128 (stays mp3), mp3+208 (→ ogg, `AudioFilename` updated in every diff), external wav+128 (→ `..._128kbps.ogg`, original wav untouched).

**No over-encode guard** — an earlier version rejected re-encoding above the source's own detected bitrate (`_validate_audio_reencode`, "You cannot over-encode your audio"), but `get_audio_bitrate_kbps`'s detection proved unreliable enough across other audio file types (non-mp3/ogg sources) that it was blocking legitimate re-encodes; removed rather than patched further. Picking a bitrate above the source's real one now just produces a larger file for no quality gain — the user's call, not the app's to block.

---

## Early Volume Setting

Pushes a volume-changing green line earlier so it's audible before an early hit. Core logic in `tools_logic.run_early_volume_setting(bm, volume_threshold_pct, early_threshold_ms, section=None)`:

- **Detection window** per note: `(note_time − early_threshold, note_time + 5]`. A green line qualifies if its volume change vs. whatever was in effect before it is `>= volume_threshold_pct` (percentage points, not relative %) and its time falls in some note's window.
- **Normal case**: move the qualifying line directly to `note_time − early_threshold`.
- **Pinned case** (the line shares a timestamp with a red line, or is itself a kiai-toggle — can't be moved without side effects): leave the original in place, **insert a new green line** at `original_time − early_threshold` instead, inheriting SV+volume from the pinned line and kiai from whatever was active immediately before it (`_kiai_state_before` — this naturally "skips" a red line at the exact same timestamp just by using strict `<` rather than `<=`).
- A line with no note in range, or a change under the threshold, is left untouched.

---

## Taiko Video SB Code

A storyboard-based alternative to the Taiko Video Resizer (`VideoOffsetShifterFrame`, `screens.py`), commonly used in hybrid mapsets: instead of re-encoding the video with ffmpeg, it writes S(cale)/F(ade)/MY (move-Y) storyboard commands directly under the `Video` event so the video shrinks and shifts into the visible band beneath the taiko playfield bar live, in the client itself, with no separate output file. **Mutually exclusive with "Taiko Video Resizer"** — checking one unchecks the other (`_sync_video_option_state`), since they're two different ways of achieving the same visual result and only one should ever be active on a given diff. **No preview UI** — an earlier calibration preview window was tried and scrapped as often useless for actually calibrating video positioning; Apply just computes and writes the block directly.

- **The block** (`osu_parser.Beatmap.set_video_sb_commands`) is written as four indented lines directly beneath the `Video` line:
  ```
  Video,<startTime>,"<videoFileName>"
      S,0,<startTime>,<startTime>,<videoScale>
      F,0,<startTime>,<startTime>,0,1
      F,0,<endTime>,<endTime>,1,0
      MY,0,<startTime>,<startTime>,125,125
  ```
  `startTime` is the same value written as the video's own offset (`Beatmap.set_video_time`). `endTime` is that specific diff's own final note — a slider/spinner/hold's **tail**, not just its head (`tools_logic.get_map_end_time_ms`/`_hit_object_end_time_ms`) — computed independently per selected diff, since different difficulties can end at different times. A slider's tail needs real physics, the same formula `insert_pattern_into_map` uses for the reverse direction (Pattern Gallery): `duration = slides * length / (SliderMultiplier * 100 * SV) * beatLength`, using that diff's own `SliderMultiplier` and whichever green line's SV is active at the slider's own start. The `MY` vertical position is a fixed constant (`logic.VIDEO_SB_Y_POSITION = 125`), not user-configurable.
- **`videoScale` is computed automatically** (`apply_video_sb_code`, `tools_logic.py`) from the actual selected video file's own native pixel height: `logic.compute_video_sb_scale(height) = round((440.0 / height) / 2, 3)`. `height` comes from `logic.probe_video_height` (`os.path.join(folder, video_file)`) — ffprobe's structured JSON first (`_ffprobe_probe_video_height`, `-select_streams v:0 -show_entries stream=height`), falling back to regexing ffmpeg's own `-i` banner for the video stream's `WxH` (`_ffmpeg_probe_video_height`) when ffprobe isn't resolvable, same probe-then-fallback shape as the existing audio probing (`_probe_audio`). Falls back to `VIDEO_SB_SCALE_DEFAULT = 0.305` if neither is resolvable or the file can't be found. Computed once per Apply (not per diff — every selected diff shares the same video file).
- **Idempotent by construction**: `set_video_sb_commands` always replaces whatever indented block (if any) already sits directly under the `Video` line (`Beatmap._video_sb_block_range`), rather than appending — reapplying doesn't pile up duplicate blocks. Applying with the SB checkbox **off** instead calls `clear_video_sb_commands()` (via `apply_video_offset`), so a stale block from an earlier SB-enabled apply doesn't linger once you switch back to the plain offset (or the resizer).

---

## Pattern Gallery

Captures a rhythm pattern (don/kat/finisher sequence) from notes selected in a running osu!stable editor, stores it in a local JSON library, and can re-insert it into any map later — optionally rescaled to match a different BPM. Built entirely on the clipboard mechanism described above ("osu! stable's editor clipboard (Ctrl+C) is a *timestamp string*") — **not** live memory reading of hit objects, after that was tried and found too version-fragile (see "What osu_memory.py can and can't do").

### Capture (`tools_logic.py`)

1. User selects note(s) in osu!'s editor, presses Ctrl+C.
2. `PatternGalleryFrame.capture()` first shows a `messagebox.askokcancel("Save your map first", ...)` reminding the user to save in osu! (Ctrl+S) before continuing — Capture reads whatever's currently on disk, and there's no reliable way to force a save from here (see "What osu_memory.py can and can't do"). Cancelling aborts the capture entirely.
3. `capture_pattern_from_osu_selection(name, clipboard_text, songs_folder)`: resolves the currently-open beatmap via `osu_memory.resolve_folder_and_filename`, parses that `.osu` file, and calls `extract_selected_hit_objects(bm, clipboard_text)` — which uses `parse_editor_clipboard_selection` to get `(anchor_ms, count)`, finds the object at `anchor_ms` in the parsed file, and takes the next `count` objects chronologically (assumes contiguous selection).
4. `_drop_concurrent_hit_objects(hit_objects)` — taiko is a single-lane rhythm, so two or more objects sharing the same timestamp (within ~1ms, the same tolerance used elsewhere for "same time" comparisons) aren't a real playable pattern, just stacked/duplicate notes. Keeps only the first object encountered (chronological order) at each distinct time and drops the rest. Returns `(kept_objects, had_concurrent: bool)`.
5. `_truncate_hit_objects_to_beats(hit_objects, timing_points, CAPTURED_PATTERN_MAX_BEATS)` caps the capture at 4 beats (matching `ManualPatternWindow`'s own default timeline length) — an over-eager selection in the osu! editor shouldn't produce an unwieldy "pattern". Drops any object whose offset from the first *starts* beyond 4 beats (measured the same beat-relative way step 4 below does); a slider/spinner that starts within the limit but whose own duration runs slightly past it is left as captured rather than also clipping its length, which would need the source map's `SliderMultiplier`/SV (see `insert_pattern_into_map`'s own note on this) — accepted as a minor imprecision rather than solving that here too. `capture_pattern_from_osu_selection` returns `(entry, truncated: bool, had_concurrent: bool)`; `PatternGalleryFrame.capture()` shows a `messagebox.showwarning` for either flag that's true, on top of the usual success toast.
6. `add_pattern_to_gallery(name, hit_objects, timing_points)` stores each note **position-independently** (taiko gameplay never depends on x/y — see `center_notes_to_playfield`): `offset_ms` (relative to the first note), `obj_type`, `hit_sound`, and `remainder` (slider length data, if any). A spinner/hold note's absolute `EndTime` is split out of `remainder` into its own `end_offset_ms` so it can be re-anchored correctly on insert (`_split_absolute_end_time`/`_has_absolute_end_time`).
7. Each note's offset is **also** converted to beats (`offset_beats = offset_ms / beat_length`, using `_governing_beat_length` — the actual red-line BPM active at that note's original time, not a green line's SV-encoded value) — a BPM-independent position used both to detect the pattern's snap divisor (`_detect_snap_divisor`, checks 1/1 → 1/48 coarsest-first, same set as Map Cleaner's resnap) and to rescale on insert. **Patterns captured before this existed have no `offset_beats`/`snap_divisor` and show "Unknown"** — there's no way to recover that retroactively.

**Leaving the name field blank** (both here and in the manual editor, add *or* edit) auto-names the pattern via `tools_logic.default_pattern_name()` — `"Untitled Pattern"`, or `"Untitled Pattern 2"`/`"3"`/… if that's taken, built on the same `_next_available_pattern_name` helper the Duplicate actions use.

### Manual entry (`screens.ManualPatternWindow`, `tools_logic.add_manual_pattern_to_gallery`)

The "Manually Add Pattern" button next to Capture opens a modal beat-grid editor for building a pattern without ever touching osu! — useful for a rhythm you're designing rather than lifting from an existing map.

- **Also doubles as the "Edit" context-menu action** (see "Context menu — `PatternCard`" below): `ManualPatternWindow.__init__(master, gallery, existing_name=None)` — when `existing_name` is given, `_notes_from_entry` converts the stored entry's `objects` back into this editor's internal note-dict format (`_offset_to_fraction`: `offset_beats`/`end_offset_beats` are authoritative when recorded — exactly what this editor already works in — falling back to `offset_ms / MANUAL_PATTERN_REFERENCE_BEAT_LENGTH` for older patterns or a real capture's own unrelated BPM, then `Fraction(val).limit_denominator(48)` to snap either onto a grid position this editor actually supports), and pre-selects the divisor from the pattern's own recorded `snap_divisor` (`_initial_divisor`, falling back to `"1/4"` if it's `"Unknown"` or otherwise not one of `DIVISOR_OPTIONS`). **`BEATS_SHOWN` is always the fixed class default (4) — every editor instance shows the exact same timeline length, whether adding fresh or editing existing** (an earlier version widened it to fit whatever was loaded; that was deliberately reverted — a 4-, 6-, 8-beat timeline depending on what you happened to open was exactly the inconsistency this was built to remove). A pattern longer than 4 beats gets truncated *on load* instead: an object starting beyond beat 4 is dropped entirely, and a span object (slider/spinner) that starts within range but would otherwise *end* beyond it has its tail clamped to fit (both in `_notes_from_entry`, mirroring `tools_logic._truncate_hit_objects_to_beats`'s same "drop by start, don't try to clip a slider's real length" philosophy from the Capture flow — see the note there) — `messagebox.showwarning("Pattern truncated", ...)` fires once the window's built, same "tell the user, don't silently discard" convention Capture already uses. A slider *captured* from a real map with no recorded duration (see `PatternCard._draw_span`'s own note on this) gets a placeholder one-tick tail in the editor rather than a zero-length object with nothing to grab. `_save()` then calls `tools_logic.update_manual_pattern_in_gallery(self._editing_original_name, name, notes)` instead of `add_manual_pattern_to_gallery` — replacing the pattern **in place** (same list position, so editing doesn't reshuffle it to the end of the gallery) — and the "name taken" check excludes `self._editing_original_name` specifically, so keeping the same name (or even renaming it right there in the editor) doesn't false-positive against itself.
- **Grid positions are `fractions.Fraction`, not float**, so a note placed under one snap divisor stays exactly aligned if the divisor is changed afterward (no float drift comparing e.g. `4/8` against `1/2`), and so notes are trivially identifiable by exact position (`_find_note_at`). Only converted to `float` for `offset_beats` at Save time.
- **Tick coloring** (`_level_for_pos`): black = beat boundary (1/1), and for the fractional part reduced to lowest terms, the reduced denominator picks the color — `2`→red, `4`→blue, `8`→yellow, `12`→gray, `3` or `6`→orange (1/3 and 1/6 share orange since neither aligns with the 1/2 grid; the 1/12-exclusive positions get their own gray tier rather than also being lumped into orange). This is computed straight from a position's own `Fraction`, independent of whichever divisor is currently on display, so an existing note keeps its original color even after the divisor view changes. `DIVISOR_OPTIONS`/`DIVISOR_DENOMS` include `"1/12"` alongside 1/2, 1/3, 1/4, 1/6, 1/8.
- **Divisor changes live-apply** — there's no Apply button. `self.divisor_var` has a `trace_add("write", self._on_divisor_changed)`, so the dropdown, the ‹›-steppers (which just call `divisor_var.set()`), and Ctrl+scroll over the timeline canvas (`_on_ctrl_scroll`, bound to `<Control-MouseWheel>`/`<Control-Button-4/5>`) all funnel through the same trace and immediately redraw the grid.
- **Notes sit on a single fixed row** — `_note_y()` always returns the vertical midpoint of the 1/1 (black, tallest) tick, *not* each note's own tick height. An earlier version positioned each note at its own tick's midpoint (height varying by snap tier), which made a row of notes zigzag up and down depending on divisor — this fixed row keeps every note horizontally aligned regardless of which tick it's on.
- **Note sprites are 1.5× `PatternCard`'s own radii** (`NORMAL_RADIUS`/`FINISHER_RADIUS` class constants here, not `PatternCard.NORMAL_RADIUS`/`FINISHER_RADIUS` directly) — bigger than the gallery card thumbnails since this editor's beat grid is more spread out and benefits from an easier click target. Colors are still reused directly from `PatternCard` (only size was scaled).
- **Timeline shows 4 beats** (`BEATS_SHOWN`) at 160px/beat — enough for a typical short pattern without the window getting unwieldy.
- **Three mutually exclusive modes** (`self.mode`, backed by `self.mode_var` and a `trace_add("write", self._on_mode_changed)` — Radiobuttons styled `"Toolbutton"` sharing that var give a segmented-button look), switchable via the buttons or hotkeys **1/2/3**, bound window-wide and guarded by `_guard_focus()` (`_set_mode_hotkey`) so typing "1" in the name field doesn't switch modes. Switching *away from* Select mode always clears the selection (`_on_mode_changed`) — Note/Special have no selection concept, so nothing should be left looking selected once you leave Select. `_on_press(pos)` is the single `<Button-1>` entry point for all three modes; it dispatches entirely on `self.mode`:
  - **Note** (default, hotkey 2): a "brush" (Don/Kat × Finisher/normal) is the current placement type — **R** toggles Don↔Kat, **E** toggles Finisher (both apply to future placements only). Clicking an **empty** tick places a note with the brush (`_place_or_replace`); clicking an **existing** note *replaces* it with the brush instead of selecting it — there's no selection concept in this mode at all. **Right-click** (`_on_right_click`) deletes whichever single note is directly under the cursor.
  - **Select** (hotkey 1): no placement at all. Clicking an **existing** note selects it, mirroring `PatternGalleryFrame`'s card selection (Ctrl+click toggles a multi-selection, Shift+click range-selects via `_ordered_note_ids`/`_range_anchor_id`, same anchor-stays-put semantics as the gallery's `shift_select`). Pressing on an **empty** tick instead starts a **box-select drag** (`_begin_bg_drag`/`_on_bg_drag_motion`/`_on_bg_drag_release`, also bound via `bind_all`) — every object whose head falls between the drag's start position and wherever the cursor currently is gets selected live as you drag, shown by a light-gray translucent overlay rectangle (`stipple="gray25"`, drawn last in `_redraw` so it tints everything underneath, `state="disabled"` so it can't swallow clicks) spanning that same range; released without ever moving instead falls back to a plain deselect (`_deselect_all`, also on Ctrl+D) — this mirrors `PatternGalleryFrame.begin_bg_drag`'s own "touched nothing → deselect" fallback, just continuous-range instead of card-by-card. **Pressing and dragging** an existing note (`_begin_note_drag`/`_on_note_drag_motion`/`_on_note_drag_release`, bound via `bind_all` for the same "`_redraw()` rebuilds every canvas item on each motion tick" reason as the box-select drag) moves it to a new position, snapped to the current divisor (`_pos_from_x`, shared with the phantom preview) and refusing to drop it onto an already-occupied tick. **R/E** in this mode instead retype *every currently selected* note in place (not a brush for later). **Delete** removes the whole current selection. **Right-click** (`_on_right_click`) is more surgical: right-clicking a *specific* object (`_delete_object`) deletes just that one — selected or not, and without touching the rest of the selection either way — no need to select it first; right-clicking *empty* space instead falls back to `_delete_selected` (the whole current selection), so bulk delete via Ctrl/Shift-select (or a box-select drag) + right-click still works when that's what you actually want.
  - **Special** (hotkeys **3 and 4**, both bound to `_on_special_hotkey`): places a **slider** (drumroll) or **spinner** (balloon) — whichever `self.special_kind` currently is — via a **click, move, click** gesture instead of one click, since a span object needs two positions. The first press (`_on_press`/`_on_tail_press` → `_begin_span_placement`) sets the head, appends a note with a provisional one-tick-long tail, and stores it in `self._placing_note`; while that's set, `_on_canvas_motion` routes to `_update_placing_tail` instead of the normal hover/phantom logic, live-updating `end_pos` to follow the cursor (snapped via `_pos_from_x`) on every redraw — so the object itself *is* the live preview, not a separate phantom (`_refresh_phantom` bails out immediately while `_placing_note` is set). **Any** press anywhere while `_placing_note` is set — regardless of mode or which item it lands on, checked before anything else in `_on_press`/`_on_tail_press` — just finalizes it (clears `_placing_note`, leaving `end_pos` wherever it last was); **right-click** instead cancels it (`_cancel_placing`, removing the provisional note entirely) rather than deleting normally. If the cursor is at or left of the head, `_update_placing_tail` clamps the tail to the closest snap to the *right* of the head instead of following — a span object can't have zero/negative length or run "backwards" — and that clamped position is exactly what a click lands on too, since finalizing just stops following rather than computing anything new. Pressing 3 or 4 from a *different* mode just enters Special (defaulting to slider); pressing either one again *while already in* Special toggles `self.special_kind` between `"slider"`/`"spinner"` — both this and any mode switch via `_on_mode_changed` call `_cancel_placing()` first, so switching away mid-placement abandons it rather than leaving an orphaned object. Clicking an **existing** object's head/body/tail restarts a placement at that head (discarding the old object once the new one finalizes) rather than a plain instant replace, since Special mode has no single-click replace anymore. **E** toggles `brush_finisher` same as Note mode (applies to a slider placed next; meaningless — and never applied — for a spinner, since spinners have no finisher variant). **R** does nothing here (Don/Kat isn't a concept for either kind).

  A slider/spinner is a `{"kind": "slider"|"spinner", "pos", "end_pos", "is_kat": False, "is_finisher"}` dict — `"end_pos"` (the tail) is `None` for a plain `"kind": "note"`. `_find_note_at(pos)` still only ever matches on the **head** — clicking the head *or* the body (both bound to `note["pos"]`, reusing the exact same dispatch a plain note uses) is indistinguishable from clicking a plain note; only the **tail** needs its own path (`_on_tail_press`/`_bind_tail_handlers`, keyed by the note object directly rather than by position) since dragging/resizing it is a different operation than moving the head/body.

  Selection itself is tracked as `id(note)` in `self.selected_note_ids`, the same unhashable-dict-by-identity pattern as `TimingPoint` elsewhere in this codebase (see "Common Gotchas"), since notes are plain dicts. Selected notes get a thicker gold (`SELECTED_OUTLINE`) outline instead of the default thin black one.
- **Dragging in Select mode** (`_begin_note_drag`/`_on_note_drag_motion` for head/body, `_begin_tail_drag`/`_on_tail_drag_motion` for the tail; both share `_on_note_drag_release` for cleanup) — bound via `bind_all` rather than on the canvas directly, same reasoning as `PatternGalleryFrame`'s card-drag: `_redraw()` rebuilds every canvas item on each motion tick, so relying on the pressed item's own implicit button-grab surviving that isn't safe. Dragging a **plain note**'s single hit target, or a slider/spinner's **head/body**, translates `pos` (and `end_pos` too, by the same delta, for a span object — preserving its length) snapped via `_pos_from_x` (shared with the phantom preview and `_update_placing_tail`), refusing to land on another object's head. Dragging a slider/spinner's **tail** instead only changes `end_pos`, clamped to at least one tick's length past the head and refusing to land exactly on another object's head — the same clamp-not-reverse rule `_update_placing_tail` applies during initial placement.
- **Sprites**: a plain note is one circle (don=`PatternCard.DON_COLOR`/kat=`PatternCard.KAT_COLOR`, bigger if finisher). A slider/spinner (`_draw_span_sprite`) is a **head** circle + a deliberately **smaller** tail circle (`TAIL_RADIUS_SCALE`, currently 0.6× — so the tail reads as a secondary marker rather than visually competing with the head) joined by a paler body bar (`SLIDER_BODY_COLOR`/`SPINNER_BODY_COLOR`, standing in for real alpha the same way the phantom's stipple does). The tail's actual click/drag target stays full-size regardless — an invisible `tail_hit` circle (white fill, matching the tick grid's own "invisible white blends with the canvas background" trick) sits under the smaller visible one, and **both** get bound to `_bind_tail_handlers` in `_redraw`. That invisible circle is created with the *same* `state=` as everything else in the sprite (not left at Tk's default "normal") — an enabled item with no bindings, if topmost, still silently swallows clicks even though it's invisible, the identical gotcha the phantom preview itself has to avoid. Only a slider's head/tail grow to `FINISHER_RADIUS` when `is_finisher`, since spinners have no finisher variant. `SPINNER_HEAD_COLOR`/`SPINNER_BODY_COLOR` are mid/light **gray**, not white — pure white was all but invisible against the canvas's own white background even with a black outline.
- **Cursor-follow phantom preview** (`_refresh_phantom`, driven by `<Motion>`/`<Leave>` on the canvas): a translucent preview snapped to the nearest tick at the current divisor. Translucency is done by **blending the fill color toward the canvas's white background** (`_blend_toward_bg(hex_color, alpha)`, `PHANTOM_ALPHA = 0.45`) into a solid color, not canvas `stipple` — `stipple="gray25"` was the original approach (plain `tk.Canvas` has no real alpha; Tk's fixed-density built-in bitmaps at 12/25/50/75% are the usual workaround) but was observed rendering the phantom essentially opaque on this app's target platform (Windows), so `_draw_span_sprite` takes an `alpha=` param instead of `stipple=` and blends `head_color`/`body_color` before drawing when set. Shown in **Note or Special mode** only, and only over an **empty** tick within either, and only while **not** already mid-placement (`self._placing_note is None` — once placement starts, the real object being stretched *is* the preview) — Select mode never shows it at all, since its clicks select/drag rather than place and a placement preview there would be misleading; these are the first checks `_refresh_phantom` makes. In Note mode it's a plain circle; in Special mode (before the first click) it's a full slider/spinner sprite at the default one-tick length, via the same `_draw_span_sprite` real objects use, just blended and `state="disabled"`. Every phantom item is created with `state="disabled"` specifically so it can never become the canvas's "current" item (same reasoning as the tail's invisible hit-region above). `_redraw()` deletes and rebuilds the entire canvas, so it re-shows the phantom at the last known hover position at the end of every redraw — otherwise it'd stay missing until the next mouse move. `_phantom_item` holds either a single item id (Note mode) or a 4-tuple (Special mode's body/head/tail_hit/tail) — `_hide_phantom` handles both. Real (non-phantom) rendering in `_redraw` draws `self.notes` **sorted by `pos` ascending**, not insertion order, so a note further right on the timeline always ends up layered on top of one further left, regardless of placement/drag order.
- **Slider length needs real physics, unlike a spinner's endTime** (`insert_pattern_into_map`, `tools_logic.py`): a spinner/hold's duration is just an absolute `endTime` in its `remainder`, so the existing BPM-independent `end_offset_beats` → `target_time_ms + end_offset_beats * target_beat_length` rescaling (already built for captured spinners) works unchanged for a manually-placed one too. A slider's duration instead comes from its pixel `length` combined with the *target map's own* `SliderMultiplier` and whichever green line's SV is active at the insertion point (`duration = length / (SliderMultiplier * 100 * SV) * beatLength`) — so a manually-authored slider (identified by `obj_type & 2` *and* having `end_offset_ms` recorded — a real captured slider never has that field, since its `remainder` already carries a meaningful length from the source map and is used as-is) gets a **freshly computed** `length` at insert time, solving that formula backwards from the intended duration, rather than reusing some placeholder value that would give the wrong duration on whatever map it lands on. The curve itself is a throwaway minimal straight line (`L|266:192`, single slide, default hitsounds/samples) since taiko doesn't render slider curves — only `length` matters for gameplay timing.
- **Canvas hit-testing gotcha**: a `create_rectangle(..., fill="")` (no fill) only catches clicks on its outline, not its interior — the invisible click target per tick is instead `fill="white"` (blends with the canvas background) so the whole slot is clickable. It's drawn *before* the tick's colored line so the line stays visible on top. The rectangle, the line, and the note's own circle (drawn last, on top of both) are all bound to the same three click handlers (`_bind_click_handlers`) — otherwise a click exactly on the (sometimes 2px-wide) line, or squarely on an existing note, would hit an unbound item and silently do nothing.
- **Save** (`add_manual_pattern_to_gallery`) builds the same entry shape `add_pattern_to_gallery` does, but has no real beatmap to derive a beatLength from — it uses a fixed 500ms/beat (120 BPM) reference (`MANUAL_PATTERN_REFERENCE_BEAT_LENGTH`) purely so `offset_ms`/`end_offset_ms` have plausible fallback values for `match_bpm=False`; `offset_beats`/`end_offset_beats` (computed directly from grid position) are what actually matter wherever `match_bpm=True`. `entry["duration_ms"]` is `max()` over *every* object's own end point (a plain note's `offset_beats`, or a span object's `end_offset_beats`) — not just the last object by head position — since a slider/spinner's tail can reach further than any note placed after its head.
- **No "Clear All" button** — removed; there was no dedicated undo and it was one accidental click away from wiping an entire in-progress pattern. Delete/right-click one object (or a whole selection) at a time instead.

### Insert (`insert_pattern_into_map`)

`insert_pattern_into_map`'s own `bm.save()` → `touch_reload()` forces osu! to reload the file from disk — if the live editor has unsaved edits on the target diff, this will discard them, since there's no reliable way to force a save from here first (see "What osu_memory.py can and can't do"). `PatternGalleryFrame.insert_selected()` shows a `messagebox.askokcancel("Save your map first", ...)` reminding the user to save in osu! (Ctrl+S) first, right after validating the target time and before calling `insert_pattern_into_map` — cancelling aborts the insert entirely.

Places every note at playfield center (256, 192) at `target_time_ms + offset`, into whichever diffs are given. If `match_bpm=True` (the default, a checkbox in the UI) **and** the pattern has `offset_beats` recorded, the offset used is `offset_beats * target_beat_length` (the *target* map's BPM at the insertion point) instead of the literal captured `offset_ms` — so a 1/6 triplet captured at 120 BPM still reads as a clean 1/6 triplet if inserted somewhere at 200 BPM. Falls back to literal `offset_ms` per-object if `offset_beats` is missing (old patterns) or `match_bpm=False`.

On success, `insert_selected()` shows `self.notify_done("Success! Press Ctrl + L in the editor to load the pattern")` — Ctrl+L is osu!'s in-editor "reload from disk" hotkey, distinct from F5's song-select-level reload that `touch_reload()` otherwise relies on (see the "osu!stable file watcher" note); telling the user the specific in-editor hotkey saves them from having to leave and re-enter the editor just to see the inserted pattern.

### Target time auto-fill

While the Pattern Gallery tab is active, `_poll_clipboard()` (started in `on_shown`, stopped in `on_hidden` — see the `BaseToolFrame.on_hidden` note above) checks the clipboard every 500ms; if it's new *and* matches `parse_osu_cursor_timestamp` (the strict matcher), Target time auto-fills. Just press Ctrl+C in osu! (selected or not) with this tab open.

### PatternCard — the visual gallery

`screens.PatternCard` (a `tk.Frame`) draws one pattern as a small card: a `tk.Canvas` row of shapes (plain notes as circles — red=don, blue=kat, bigger radius=finisher, checked via `hit_sound & (HS_WHISTLE|HS_CLAP)` / `& HS_FINISH`; sliders/spinners as head+body+tail sprites, see below), the name, and the snap divisor in small gray text below it (hover the divisor text for a duration-in-ms tooltip via `_add_hover_tooltip` — deliberately not shown by default, to keep the card uncluttered).

- **Note spacing is proportional to real timing**, not evenly spaced by index (`PatternCard._layout_positions`): scaled so the *closest* pair of points-of-interest sits exactly `MIN_GAP` px apart (currently 15px — deliberately **less** than `2*NORMAL_RADIUS`, ~20px, so a dense cluster like a 1/4 stream visibly *overlaps* rather than just touching edge-to-edge), then compressed further if the whole card would exceed `MAX_WIDTH`. A slider/spinner's tail (`end_offset_ms`, when known) is folded into this same layout pass alongside every object's own `offset_ms` (`offset_to_x` in `PatternCard.__init__`), so its body renders at full proportional length instead of collapsing onto the head.
- **Slider/spinner rendering** (`_draw_span`): a slider (drumroll) is yellow, a spinner (balloon) is gray — `SLIDER_HEAD_COLOR`/`SLIDER_BODY_COLOR`/`SPINNER_HEAD_COLOR`/`SPINNER_BODY_COLOR`/`TAIL_RADIUS_SCALE` are defined once here as the canonical source and reused by `ManualPatternWindow` the exact same way it already reuses `DON_COLOR`/`KAT_COLOR`, so the two never drift apart visually. If `end_offset_ms` is known (always true for a *manually*-built slider/spinner — see `tools_logic.add_manual_pattern_to_gallery` — never true for a slider *captured* from a real map, whose duration lives in its remainder's osu!-pixel `length` field instead, needing the source map's own `SliderMultiplier`/SV to convert to a time span that isn't available here), it draws a full head+body+smaller-tail sprite; otherwise just a head circle in the matching color — flagging "this is a slider" honestly via color rather than fabricating a length the card doesn't actually know.
- **Delete badge**: a small red square+white-X `tk.Canvas` in the exact top-right corner (`place(relx=1.0, rely=0.0, x=0, y=0, anchor="ne")`), filling its own canvas edge-to-edge (`create_rectangle(0,0,16,16,...)`, no margin) — a square badge exactly matches the canvas bounds, so (unlike an earlier circle version) there's no leftover flat-colored corner sliver to work around. **Hidden by default**, shown only on `<Enter>`/hidden on `<Leave>` (bound on every sub-widget of the card, with a 50ms debounced hide via `after()`, to avoid Tk's Leave→Enter flicker as the pointer crosses between sibling widgets inside the same card) — and **never shown at all while 2+ cards are selected** (`_on_enter` checks `len(gallery.selected_pattern_names) < 2`; `_refresh_selection_visuals` also proactively calls `hide_badge()` on every card the instant multi-select is entered, in case one was already showing from a stale hover). **Its `<Button-1>` binding (delete) must stay separate from the card's shared drag-start loop** — the badge used to be included in that loop, and since `tk.Canvas.bind()` overwrites rather than stacks handlers for the same sequence, that silently replaced the delete click with the drag-start handler, breaking the badge entirely. The badge only shares `<Enter>`/`<Leave>` with the rest of the card, not `<Button-1>`/`<Control-Button-1>`.

### Context menu (`PatternCard._show_context_menu`)

Right-click (`<Button-3>`, bound alongside the card's other click handlers in the same shared per-widget loop) pops a `tk.Menu` that **always targets that specific card**, independent of whatever else is currently selected — unlike `ManualPatternWindow`'s Select-mode right-click, nothing here is a bulk/"act on the whole selection" action. Layout, top to bottom: **Rename**, a separator, **Edit** / **Duplicate** / **Duplicate Inverted** / **Duplicate Reversed** (note the capitalization — menu *labels* are capitalized, but the *generated pattern names* stay lowercase, `"<name> inverted"`/`"<name> reversed"`, unaffected), another separator, **Delete** (`foreground="#d32f2f"`, matching the delete badge's own red).

**Suppressed entirely with 2+ cards selected** — `_show_context_menu` and `_on_double_click` (the latter's shortcut-to-Edit behavior) both no-op if `len(self.gallery.selected_pattern_names) >= 2`, since every action here is single-pattern and there's no sensible target once the click would otherwise silently act on just one of several selected cards. Same "hidden during multi-select" reasoning as the delete badge above; the ordinary `<Button-1>` toggle/range-select handling is untouched either way.

- **Rename** (`_on_rename`) uses `tkinter.simpledialog.askstring` — a standard, already-modal library dialog, so it doesn't need this codebase's usual "Modal Toplevel windows must guard against duplicate opens" dedup-guard pattern (that's for custom async `Toplevel`s; `askstring` blocks synchronously and can't be duplicate-opened the same way). No-ops on cancel (`None`) or an unchanged/empty name; warns (doesn't rename) on a collision with a *different* existing pattern, via `tools_logic.rename_pattern_in_gallery`.
- **Edit** (`_on_edit`) calls `PatternGalleryFrame.open_manual_pattern_editor(existing_name=self.name)` — reuses the exact same dedup-guarded `self._manual_pattern_win` slot the "Manually Add Pattern" button uses, so only one manual-pattern window (whichever purpose) can be open system-wide at a time, consistent with every other modal window in this app. See `ManualPatternWindow`'s own "Also doubles as the Edit context-menu action" note above for how loading works — including that it defaults to **Select mode** rather than Note mode, since you're presumably reviewing/adjusting what's already there rather than about to place fresh notes (a brand new "Manually Add Pattern" window still defaults to Note mode).
- **Double-clicking a card is a shortcut straight to Edit** (`_on_double_click`, bound as `<Double-Button-1>` alongside the rest). **Tk fires *both* `<Button-1>` and `<Double-Button-1>` for a double-click's 2nd press** — confirmed empirically, not just from the docs — so `begin_card_drag` (the plain `<Button-1>` handler) *also* runs and starts its own `bind_all`-based drag-tracking on every double-click, immediately before `_on_double_click` opens the modal editor. Since the upcoming `ButtonRelease-1` that would normally end that tracking may get redirected to the new window's `grab_set()` instead of ever reaching the gallery, `_on_double_click` calls `PatternGalleryFrame._cancel_card_drag()` first — proactively unbinding `<B1-Motion>`/`<ButtonRelease-1>` and resetting `_card_drag_name`/`_card_drag_moved` — rather than waiting for a release that might not arrive. Skipping this would leave those `bind_all` bindings (and a stale `_card_drag_name`) dangling for the rest of the session.
- **Duplicate** / **Duplicate Inverted** / **Duplicate Reversed** (`tools_logic.duplicate_pattern`/`duplicate_pattern_inverted`/`duplicate_pattern_reversed`) each `copy.deepcopy` the source entry and name the result via `_next_available_pattern_name` — `"<name> 2"` (then `"<name> 3"`, …) for a plain duplicate, `"<name> inverted"`/`"<name> reversed"` (then `" 2"`, `" 3"`, … appended if *that's* also taken) for the transformed ones. **Inverted** only touches `obj_type == 1` (plain note) objects — sliders/spinners have no Don/Kat concept in taiko (any drum face hits them), so their hitsound/finisher status is left untouched; a note's finisher status is preserved either way, only the Don↔Kat bit flips (normalized onto `HS_WHISTLE` regardless of whether the original used `HS_WHISTLE` or `HS_CLAP` for Kat — a rare distinction only a real capture could have, and gameplay-meaningless either way). **Reversed** mirrors every object's `offset_ms` (and `offset_beats`, if recorded — independently, not re-derived from `offset_ms`, since a *captured* pattern's `offset_beats` reflects the *source map's* BPM at capture time rather than any fixed reference the way a manually-built one's does) around the pattern's own total span — e.g. a `dkDK` stream becomes `KDkd`. A slider/spinner's head and tail swap roles under this mirror (both endpoints mirror independently, so the object keeps its own duration but moves to the mirrored position in the timeline).
- **Delete** (`_on_delete_via_menu`) just calls the existing `request_delete_single(self.name)` — same `confirm_pattern_delete`-respecting path the delete badge already uses, nothing menu-specific about it.

### Selection and drag model (`PatternGalleryFrame`)

Two entirely different drag behaviors depending on where the press starts:
- **Press-and-drag *on a card*** → **reorders** it (`begin_card_drag`/`_on_card_drag_motion`/`_on_card_drag_release`). Motion/Release are bound via `self.bind_all(...)` for the drag's duration (not per-widget) — the dragged card is unpacked (`pack_forget`) for the whole drag, so an unmapped widget's own implicit button-grab can't be relied on to keep delivering events, and routing through a stable, never-unmapped bindtag (`bind_all`) sidesteps that. Past a small dead-zone (`_card_drag_moved`, ~4px, so a plain click doesn't flash any of this for a frame), the drag shows two more always-on-top overlay `Toplevel`s (built once up front in `__init__`, toggled with `.deiconify()`/`.withdraw()` per-drag, `WS_EX_NOACTIVATE`'d via `_make_overlay_noactivate` — same pattern as `_bg_drag_overlay` below, for the same reasons): a **translucent ghost** (`_create_card_drag_ghost`/`_show_card_drag_ghost`, ~60% `-alpha`) that's a `PIL.ImageGrab` screenshot of the real card's pixels at drag-start, resized into a `Label` and moved with `.geometry()` to track the cursor (`_move_card_drag_ghost`, offset so it doesn't jump to the card's top-left on press); and a **giant "I"-beam indicator** (`_create_card_drag_indicator`/`_update_card_drag_indicator`, a vertical bar with top/bottom caps, colored `#2e9fd0`) marking the gap the card would land in if dropped right now, positioned from `_compute_drop_index(x_root)` — pure `winfo_rootx`/`winfo_width` geometry over the *other* cards (i.e. `_ordered_names` minus the one being dragged), immune to the overlays sitting visually on top for the same reason `_card_at` is (see below). **The ghost is kept stacked above the indicator by deiconify *order*, not `.lift()`** — every motion tick calls `_update_card_drag_indicator` (deiconify) then `_move_card_drag_ghost` (deiconify) in that order, so the ghost is always the more-recently-shown of the two topmost windows. An earlier version called `.lift()` on both instead, which reintroduced exactly the "a topmost window appearing/restacking mid-drag can interrupt Windows' mouse-button capture" risk `_bg_drag_overlay` was already built around (see its own note just below) — even though these overlays already exist and are merely being *repositioned*, not created, `.lift()` specifically (unlike the plain `.deiconify()` that overlay already calls every tick without issue) was enough to freeze `<B1-Motion>` delivery mid-drag. **Don't reintroduce `.lift()` on either of these overlays** — reorder the deiconify calls instead if the stacking needs to change. **`_ordered_names` itself isn't touched until release** — dragging only moves the ghost/indicator; `_on_card_drag_release` inserts the dragged name at whatever index the indicator last pointed to, `_repack_cards()`s, and persists the new order to the pattern library JSON. The cursor nearing either edge of `gallery_canvas`'s own visible width (within `AUTOSCROLL_MARGIN`, 40px) starts a repeating `self.after()` tick (`_autoscroll_check`/`_start_autoscroll`/`_autoscroll_step`/`_stop_autoscroll`) that scrolls the canvas and re-evaluates the indicator/ghost against the last-known cursor position each tick, since the cards' own screen positions just moved even though the cursor didn't. Released without ever passing the dead-zone = ordinary single-select click; `_cancel_card_drag` (used when a double-click's 2nd press pre-empts an in-progress drag — see `PatternCard._on_double_click`) tears down the same overlays and, since `_ordered_names` was never touched, just `_repack_cards()`s the unpacked card straight back where it already was.
- **Press-and-drag from *empty gallery background*** → **range-selects** whichever cards get swept over (`begin_bg_drag`/`_on_bg_drag_motion`/`_on_bg_drag_release`), anchored at the first card touched; released without ever touching a card = plain deselecting click. Shown live via a light-gray translucent overlay band spanning from the drag's start x to the current cursor x (`_update_bg_drag_overlay`), matching `ManualPatternWindow`'s own box-select overlay — but implemented as a small borderless, `-alpha`-transparent `Toplevel` (`self._bg_drag_overlay`), **not** a canvas rectangle: `PatternCard` is a real embedded Tk widget (`create_window`), and Tk always stacks "window" canvas items on top of ordinary drawn items regardless of creation order or `tag_raise`/`tag_lower` — a plain rectangle drawn on `gallery_canvas` would end up hidden *behind* every card instead of visually tinting them. `-alpha` is safe to rely on since this app only ever targets Windows. **Created once in `__init__` (`_create_bg_drag_overlay`), kept withdrawn between drags** rather than created/destroyed per-drag — showing a brand-new always-on-top window *while the mouse button is physically held down* risks interrupting the mouse-button capture Tk/Windows relies on to keep delivering `<B1-Motion>` events smoothly, which made the very first version of this feature feel unreliable mid-drag. `_make_overlay_noactivate` also applies `WS_EX_NOACTIVATE` (+ `WS_EX_TOOLWINDOW`) via raw `ctypes` calls on top of whatever `-alpha` already set (`WS_EX_LAYERED`), so showing/repositioning it can never steal focus/activation either — confirmed empirically that `winfo_id()` on an `overrideredirect` Toplevel resolves to the real top-level HWND here, and that the style bits actually stick (`GetWindowLongW` round-trip). `.deiconify()`/`.withdraw()` (not create/destroy) toggle visibility per-drag; geometry is clamped to `gallery_canvas`'s own screen bounds so it doesn't bleed into surrounding UI.
- **Ctrl+click** a card → toggles it into/out of the current selection, independent of the two drag behaviors above.
- **Shift+click** a card (`shift_select`) → file-explorer-style range select: extends/replaces the selection with every card between `self._range_anchor` (the last plain- or ctrl-clicked card) and the one just clicked, via the same `_apply_range_selection` the background-drag path uses. The anchor itself doesn't move on repeated shift-clicks — only a plain click, ctrl-click, or starting a new background-drag range moves it — so shift-clicking a different card grows/shrinks the range relative to the *original* anchor, not the previous shift-click. Falls back to a plain single-select if the anchor's gone stale (e.g. `None`, or its pattern got deleted).
- **Click empty space, or Ctrl+D** (bound via `bind_all` only while this tab is shown) → deselects everything (and clears `_range_anchor`).
- `_card_at(x_root, y_root)` — checks each card's own `winfo_rootx/y/width/height` directly rather than asking Tk "what's here" — used by the background range-select drag to figure out what's under the pointer (the card-reorder drag has its own analogous `_compute_drop_index`, since it needs an insertion index among the *other* cards rather than a single hit). **Deliberately not `winfo_containing()`** (which it used to be): `winfo_containing()` does real OS-level window hit-testing, and the box-select overlay above is a genuine separate `Toplevel` sitting exactly over the region being dragged across — `-alpha` only affects *rendering*, not hit-testing, so `winfo_containing()` would find the overlay itself (or nothing) instead of the card underneath it. This was a real, shipped bug: selection went erratic ("jumps randomly") mid-drag the moment the overlay existed, because `_card_at` kept losing track of the actual card under the cursor. Pure geometry math sidesteps window stacking entirely and is immune to any future overlay/window doing the same thing — the same reason the card-reorder drag's ghost/indicator overlays don't confuse `_compute_drop_index` either.

**Insert rejects multi-selection** (`insert_selected` shows a warning if `len(selected_pattern_names) != 1`) — a pattern can only be inserted one at a time. **Delete** has three paths: a single card's own badge (`request_delete_single`, always available regardless of selection state), the **Delete key** (`_on_delete_key`, bound via `bind_all` only while this tab is shown — routes to `request_delete_single` or `request_delete_selected` depending on selection size, and no-ops if focus is in a text `Entry`/`Spinbox`/`Text` widget so it doesn't hijack normal text editing), and a floating red "Delete N patterns" button that only appears once 2+ are selected (`request_delete_selected`, packed/`pack_forget()`'d in `_update_bulk_delete_button`, sitting in its own dedicated row so toggling it doesn't jump other widgets around). All three respect the `confirm_pattern_delete` setting (Settings #3 — see "Adding a new persisted setting") via `_confirm_delete()`.

---

## BG Preview Coordinate System

The preview renders in osu!'s canonical **854×480** pixel space:
- Bottom `255px` (`BAND_H`, `tools_logic.py`) = visible background band
- Top `225px` (`PLAYFIELD_H = OSU_H - BAND_H`) = playfield/HUD bar (drawn solid black), the remainder of the 480px height

`y=0` in the stored offset means the image is **centered** within the band (not anchored to top/bottom). Positive y moves the image down; negative moves it up.

The preview window renders at **960×540** (upscaled from 854×480). The coordinate transform is in `tools_logic.py`: `_process_bg_native()`, `_crop_to_band()`, `_compose_native_frame()`, `render_bg_preview()`.

There is no separate calibration fudge-factor applied on top of this — `_crop_to_band`/`get_offset_bounds` work directly off `PLAYFIELD_H`/`band_h`. (An earlier version applied a +7px calibration offset here; it was removed once the playfield/band split above was recalibrated.)

---

## Common Gotchas

- **Never call ffmpeg/ffprobe with a bare `"ffmpeg"`/`"ffprobe"` string.** Always `_resolve_binary("ffmpeg")` / `_resolve_binary("ffprobe")` (`tools_logic.py`) — a bare string skips the bundled-next-to-app lookup and only ever finds a PATH install, silently defeating the whole point of bundling. See "Audio Processing (ffmpeg / ffprobe)" above.
- **Never close over `except ... as e:`'s `e` in a callback that runs later** (a `lambda` passed to `self.after(0, ...)` from a worker thread, most likely). Python implicitly deletes `e` the instant the `except` block exits (PEP 3110) — but a `self.after(0, ...)`-scheduled callback only actually runs later, on the main thread, well after that block has exited. The lambda then raises a bare `NameError` the moment Tk finally invokes it, and since Tk's default exception reporter just prints to stderr (invisible in a windowed/no-console build), the *entire error vanishes* — this shipped for real in `OffsetShifterFrame._start_apply_thread` and `TaikoVideoResizerFrame`'s resize error handler, and looked exactly like "the busy banner flashes and then nothing happens" with no visible error at all. Fix: capture `err_msg = str(e)` as a plain string *before* scheduling the callback, and close over `err_msg` instead — see either of those two call sites for the pattern.
- **A Tk geometry string's coordinate can carry *two* sign characters, not one.** `main.App`'s window-geometry persistence (`_GEOMETRY_RE` in `main.py`) validates the saved string before trusting it — the first version used `[+-]\d+` per coordinate (one delimiter, then digits), which looks right but rejects a real value Tk itself produces: on a multi-monitor setup with a monitor positioned left of/above the primary, a window's absolute position is genuinely negative, and Tk encodes that as e.g. `+-1960` — the usual `+`/`-` *delimiter* (its own "measured from this edge" convention), immediately followed by a literal `-` for the actual negative number. The over-strict regex silently rejected this as invalid on every load and fell back to the default geometry — confirmed for real: a `.osu_taiko_helper_geometry.txt` on a multi-monitor dev machine held a genuine saved value that never actually got applied on the next launch. Fixed by allowing an optional second `-` after the delimiter (`[+-]-?\d+`); if you touch this regex again, re-verify against a *negative* coordinate, not just a positive one.
- **Never use `set()` or `dict` with `TimingPoint` objects directly.** Use `id(tp)`. See "TimingPoint is unhashable" above.
- **`stable_round()` not `round()`** for any time value that goes into a `.osu` file.
- **`bm.save()` is called by the screen, not by `tools_logic`**. The logic functions only mutate the `Beatmap` object.
- **`bm.timing_points` is a live list.** When sorting for iteration (e.g. building kiai state), sort a copy — `sorted(bm.timing_points, ...)` — rather than sorting in place, to avoid changing the order before `save()` re-sorts it.
- **`resnap_important_green_lines` is the current resnap function** — it only snaps kiai toggles and red-line-supported green lines. There is no longer a "resnap all green lines" option.
- **`push_green_lines` subtracts time** (`tp.time -= push_ms`). "Push" = earlier in the map = lower ms value. Kiai toggles and red-line-supported green lines are excluded.
- **Timestamp format is `mm:ss:mmm` (colon), not `mm:ss.mmm`.** See the domain-knowledge section above — this was a deliberate fix, don't revert it.
- **osu!'s editor Ctrl+C ≠ hit object clipboard.** It's a timestamp string on the OS clipboard, unrelated to osu!'s own internal (memory-only) object copy/paste buffer. Don't build a "paste directly into osu!" feature assuming otherwise — see the domain-knowledge section above.
- **Tooltip alignment**: `align="center"` is available in `_add_tooltip` (added for the 📂 button). The `InfoIcon` class only supports `"left"` and `"right"`.
- **Window modality**: Settings, BG/Video offset preview, search results, coordinate editors, and troubleshoot popup are all `grab_set()` modal *and* dedup-guarded (see "Modal Toplevel windows must guard against duplicate opens" above). Don't open a new Toplevel from inside one of these without thinking about the grab stack, and don't add a new modal-spawning button without the dedup guard.
- **osu!stable file watcher**: Always call `bm.save()` (not a raw `open()` write) so that `touch_reload()` fires and osu! picks up the change on F5.
- **Encoding**: `.osu` files are read with `encoding="utf-8-sig"` (strips BOM) and written as UTF-8 with `\r\n` line endings.
- **Success feedback is `self.notify_done(...)` (toast), not `messagebox.showinfo`.** See "Toast notifications" above.
- **A plain `tk.Canvas` can't do real transparency.** Don't try to "fix" the Pattern Gallery delete badge's corner rendering further than `create_oval` filling the canvas edge-to-edge — that's the practical ceiling without a much bigger architecture change.
- **An `-alpha`-transparent `Toplevel` still blocks `winfo_containing()`.** Transparency is rendering-only, not hit-testing — a translucent overlay window sitting over other widgets will make `winfo_containing()` return the overlay itself (or nothing) instead of whatever's underneath. `PatternGalleryFrame._card_at` hit this for real (see "Selection and drag model" above) once the box-select overlay was added; the fix was checking each candidate widget's own `winfo_rootx/y/width/height` directly instead of asking Tk what's at a screen point. Keep this in mind before adding any other transparent/overlay `Toplevel` near widgets that do their own hit-testing.
- **`create_window`-embedded widgets always draw on top of plain canvas items**, regardless of creation order or `tag_raise`/`tag_lower` — a real Tk canvas limitation, not a bug in this codebase. This is why the Pattern Gallery's box-select overlay is a separate `Toplevel` instead of a canvas rectangle (unlike `ManualPatternWindow`'s own overlay, which *can* just be a rectangle since nothing on that canvas is an embedded widget).
- **Never create/show a new `Toplevel` mid-drag while a mouse button is held down.** On Windows, a fresh always-on-top window appearing while the button is physically down risks interrupting the mouse-capture Tk relies on to keep delivering `<B1-Motion>` events smoothly, and by default a new Toplevel can also steal focus/activation outright. The Pattern Gallery's box-select overlay is created once up front and toggled with `.deiconify()`/`.withdraw()` per-drag instead of created/destroyed each time, with `WS_EX_NOACTIVATE` applied via `ctypes` (`PatternGalleryFrame._make_overlay_noactivate`) so showing/moving it can never take activation. Follow the same pattern for any future drag-feedback overlay.

---

## Running the App

```powershell
cd app
pip install -r requirements.txt
python main.py
```

## Building the Exe

```powershell
cd app
./build_exe.bat
```
Output: `app/dist/osu_taiko_helper.exe`. Copy `ffmpeg.exe` **and `ffprobe.exe`** alongside it if distributing — both are auto-detected there first (see "Audio Processing (ffmpeg / ffprobe)" above), before falling back to PATH or the in-app installer.

## Syntax Checking (PowerShell)

```powershell
python -m py_compile app/screens.py
python -m py_compile app/tools_logic.py
python -m py_compile app/osu_parser.py
python -m py_compile app/main.py
python -m py_compile app/osu_memory.py
```
