# osu!taiko Mapping Tools

A Windows desktop tool for osu!taiko mappers, built from the wireframes:
metadata manager, volume/kiai copier, map cleaner, offset shifter, BG
setting, video setting, and file name checker.

This was built and tested in a Linux sandbox (Python + tkinter, headless
Xvfb + unit tests for every tool's logic). I could not compile an actual
Windows `.exe` from here — you'll build that yourself on Windows with the
included script (takes about a minute, one-time).

## Quick start (run from source, fastest way to try it)

1. Install **Python 3.10+** from python.org (tick "Add to PATH" during install).
2. Install **ffmpeg** and make sure `ffmpeg` is on PATH (needed for the video
   resizer and audio spectrogram). Easiest on Windows: `winget install ffmpeg`,
   or download a build from https://www.gyan.dev/ffmpeg/builds/ and add its
   `bin` folder to PATH.
3. **Optional, for the live video-offset preview** (plays the map's audio and
   video together with a seeker + quick offset buttons): install
   [VLC Media Player](https://www.videolan.org/vlc/download-windows.html)
   matching your Python's bitness (64-bit VLC for 64-bit Python, which is the
   default for almost everyone). `python-vlc` (in requirements.txt) then
   auto-detects your VLC install through the registry — no extra PATH setup
   needed. Without VLC installed, every other tool still works fully; the
   Video Setting's Preview button will just tell you it's unavailable
   and you can type the Offset value directly instead.
4. Open a terminal in the `app` folder and run:
   ```
   pip install -r requirements.txt
   python main.py
   ```

## Building a standalone .exe

From the `app` folder on Windows:
```
build_exe.bat
```
This installs dependencies and runs PyInstaller, producing
`dist\osu_taiko_helper.exe`. Copy `ffmpeg.exe` next to it (or keep ffmpeg on
PATH) so the video/audio tools keep working outside your dev machine.

## What's implemented and how

- **Metadata Manager** — reads/writes `[Metadata]`/`[General]` fields
  (Artist ↔ `ArtistUnicode`, Romanised Artist ↔ `Artist`, same for
  Title/Romanised Title, plus Source, Mapper/`Creator`, Tags, Preview Point),
  applied to whichever difficulties you tick.
- **Volume/Kiai Copier** — two separate sections with identical layouts
  (source dropdown + Apply-to checklist):
  - *Volume Copier* — copies the `volume` field onto matching timing points
    (by closest time) in the target diffs, and also adds a new green line
    for any source green line that genuinely *changes* the volume (compared
    to whatever volume was already in effect right before it) and has no
    counterpart at all in the target — so a real volume change gets
    reproduced even if the target has nothing at that timestamp, but a
    green line that doesn't actually change anything isn't copied over as
    clutter. New lines preserve whatever scroll velocity is already in
    effect in the target at that point — this only ever changes
    volume/sampleset, never SV.
  - *Kiai Copier* — same idea, for the kiai bit specifically (effects & 1)
    instead of volume. Only the kiai bit is ever touched, on both matched
    existing timing points and newly-inserted ones — sample set, volume,
    and scroll velocity in the target are always left exactly as they
    already were.
- **Map Cleaner**, per selected diff:
  - *Resnap all notes* / *Resnap all green lines* — snaps to the chosen
    divisor (1/1…1/48) using **osu!stable's "round half away from zero"**
    rounding, not banker's rounding, to pick the nearest beat. Compound
    divisors (1/12, 1/24, 1/36, 1/48) snap to the union of their listed
    base-divisor ticks, matching stable's editor tick colouring. The final
    snapped time is reconstructed as `trunc(trunc(red.time) + beatIndex *
    beatLength)` — truncating the governing red line's own time *before*
    adding whole multiples of the beat length, rather than rounding the
    combined value as a single float at the end. A red line's time isn't
    always a whole number (e.g. computed from a non-integer BPM, or
    imported from analysis tooling), and rounding the combined sum could
    land 1ms off from where osu!stable's own snapping would put it, even
    when the correct nearest beat was identified.
  - *Remove unused green lines* — drops an inherited (green) line only if
    its volume, scroll velocity, and kiai toggle all match whatever is
    already in effect (volume/kiai from the most recent timing point of
    either color, SV from the most recent green line or osu!'s implicit
    1.0x/-100 default). This is compared purely against that running
    state, **not** against whether a note happens to sit under the line —
    taiko still triggers hitsounds and SV-driven visuals (barlines,
    spinner fade-in, finishers, etc.) on green lines with nothing directly
    beneath them, so "no note here" was never a safe signal that a line
    does nothing. A green line sharing its timestamp with a red line is
    always kept regardless, since matching a red line explicitly is
    treated as intentional. Scroll velocity comparison uses a small
    tolerance (0.01) rather than exact equality, since real maps often
    carry recurring-decimal SV values (e.g. a 1.4x slider multiplier at
    certain BPMs) that pick up harmless floating-point noise across saves
    — exact comparison would treat every "duplicate" as a genuine change
    and remove nothing.
  - *Turn all Kat's whistle to clap* — flips the Whistle hitsound bit to Clap
    wherever Clap isn't already set.
  - *Set all lines to Normal Sampleset*.
  - *Resolve red/green line conflicts* — where a red and green line share a
    timestamp, the red line's kiai/volume are made to follow the green line.
  - *Reposition all notes in playfield* — three mutually-exclusive radio
    options underneath (grayed out until the parent is checked):
    - *All notes in center* — every note goes to (256, 192).
    - *Separate finishers* — notes carrying the Finish hitsound go to one
      configurable position, everything else to another.
    - *Separate note types* — Don, Kat, Don Finisher, and Kat Finisher
      (categorized by the Finish bit and whether Whistle/Clap is set) each
      get their own configurable position.

    The latter two each have their own "Change Coordinate" button, grayed
    out unless that specific radio option is currently selected. It opens
    a modal window with a 17×13-line grid (16×12 cells, each worth 32
    osu! coordinate units — the full 512×384 playfield space) and 2 or 4
    draggable circles matching the option — the "big" circle (finisher/
    don finisher/kat finisher) is 2 grid squares across, the "small" one
    (normal/don/kat) is 1 grid square across, and where circles overlap,
    small always draws over big, and red always draws over blue. Circles
    snap to grid intersections continuously while dragging, with live
    "Label: x,y" readouts above the grid; Apply saves the positions
    (persisted for next time) and closes the window. Defaults: Finisher
    (256,128) / Normal (256,192) for the 2-circle version; Don (192,128) /
    Kat (192,256) / Don Finisher (320,128) / Kat Finisher (320,256) for
    the 4-circle version.
- **Audio/Offset Settings** — linked Current/New/Change fields (editing either
  updates the other), shifts timing points, hit objects, breaks, video
  event time, and Preview Point together.
- **BG Setting** — 960×540 live preview built on osu!'s own
  canonical 854×480 widescreen pixel space: the playfield/HUD bar occupies
  the top 252px (solid black here, with a "y : N" offset readout
  overlaid), leaving the bottom 228px as the visible band. The background
  fills the full width (not scaled down small/pillarboxed) and is cropped
  to that band; **y:0 means the image is centered within the band —
  evenly offscreen on both sides** (equal amounts hidden under the bar
  above and past the bottom edge below) when it's taller than the band,
  not anchored to either edge of it. Titled "BG Offset Preview", with an
  **(i)** icon next to Apply explaining the drag/scroll/arrow-key controls.
  Drag relatively — dragging up moves the image up (each pixel of mouse
  movement nudges the offset by 1; it won't jump to wherever you first
  click) — scroll the mouse wheel (±5 per notch), or use the arrow keys
  (±1); the main panel's New Offset field also has ±1 spinner arrows.
  Apply writes the offset as literal `x,y` numbers into the background
  event line (`0,0,"bg.jpg",0,y` — an official part of the .osu format)
  rather than modifying the image file at all, and can separately convert
  `.png` → `.jpg` (a plain re-save, no cropping).
- **Video Setting** — "Taiko Video Resizer" re-encodes to 1280×720
  `.avi` with no audio, content scaled to 333px height at bottom-center,
  optional blurred side fill (checked by default, even before the resizer
  itself is turned on, so it's ready to go the moment it's needed). A
  read-only **Current Offset** field shows
  whatever's already saved in the `Video` event's start time; **New
  Offset** is what you're changing it to — Apply **replaces** the stored
  value outright (applying again with the same value is a no-op, not a
  double-shift), and New Offset prefills from Current so a blind Apply
  can't accidentally zero it out. The **Preview** button opens a
  1440×840 "Video Sync Offset Preview" window (video area now fills
  almost the whole window, with the seeker bar, quick-offset buttons, and
  volume slider all moved to the bottom instead of leaving dead space
  below a small fixed video area) that plays the song's audio and the
  chosen video together (via VLC — see install steps above), with a
  "Current Video Offset: N" readout and an **(i)** icon next to Apply
  (both on the top-right, with nothing else competing for that space)
  explaining the offset/sync/seek controls, plus quick-offset buttons
  (±10/50/100/200/1000 ms); each click nudges a running offset and
  restarts playback so you can hear/see it land, and Apply writes that
  total into the New Offset field. Next to the timer: ⏪/play-pause/⏩
  buttons, also controllable with the Left/Right arrow keys and Space.
  The volume slider sits at the bottom-right of the window, below the
  quick-offset buttons. Clicking (or dragging) anywhere on the seek bar
  or the volume bar jumps instantly to that exact position — `ttk.Scale`'s
  own default trough-click behavior only steps by a tiny increment rather
  than jumping to where you clicked, so both are overridden to compute the
  position themselves. The
  seek bar tracks real playback position and drag/click actually land
  where you release, rather than freezing after the first update (a
  separate `ttk.Scale` quirk where its `command` callback fired on *any*
  value change, including our own programmatic position refreshes, not
  just real user drags — that made it impossible to tell the two apart, so
  the slider's range never updated past a placeholder and every real seek
  landed somewhere in that first second instead of where you dragged).
  If VLC isn't installed, the button explains that and you can still type
  an offset value directly.
- **File Name Checker** — flags `Artist - Title (Mapper) [Diff].osu`
  capitalisation mismatches against the map's own metadata, with a one-click
  rename-to-match. The rename is safe on Windows for case-only changes
  (e.g. `oni.osu` → `Oni.osu`) — Windows/NTFS treats those as the *same*
  file already existing, so a direct rename fails; this renames through a
  temporary intermediate name instead, which works around it correctly.

## Title bar buttons

- 🔍 **Search** — placeholder reads "Search osu! map..."; type an artist,
  title, mapper, or tag and hit Enter (or click the magnifying glass) to
  search your whole Songs folder. Results show a background thumbnail plus
  "Artist - Title"; click one to select it. Indexing only considers
  **taiko** mapsets (checks each diff's `Mode:1`) —
  osu!standard/catch/mania-only sets are skipped entirely. On startup,
  only the 100 most recently modified taiko mapsets are indexed (a
  reasonable proxy for "latest downloaded") — once that finishes, an
  "Index Full Library" button appears right where the progress indicator
  was (hover it for a reminder of what it does), if you want everything
  searchable. A small progress indicator next to the search box shows
  "Index in progress" while indexing (hover the bar or its label for a
  reminder that this is why), then "Index Complete!" for a couple seconds
  once it's done; search retries automatically once indexing finishes, so
  you don't need to search twice. The results window now
  actually takes keyboard/mouse focus when it opens and the scroll wheel
  works anywhere over it — both were broken before (mouse wheel was only
  bound to the canvas's own bare surface, which is almost entirely covered
  by the row content, so it essentially never fired in practice).
- 📁 **Browse for a beatmap folder** — opens the file explorer directly so
  you can manually pick any beatmap folder, no detection involved.
- ⬆ **Pick up current map** — tries to auto-detect the map open in a
  running osu! stable client (see below); falls back to the same manual
  browse dialog if that isn't possible.
- あ **Toggle metadata display** — switches the "Now Selecting" label
  between the map's original (unicode) artist/title and its romanised
  (ASCII) versions. Search results have their own separate あ button (top
  of the results window) that does the same for every result at once.
- 📂 **Open currently selected song folder** — opens the current map's
  folder in the OS file explorer (`os.startfile` on Windows, `open` on
  macOS, `xdg-open` on Linux). Shows the same "Please select a map first!"
  warning as everywhere else if nothing's selected.
- ⚙ **Settings** — nothing here takes effect until you click Apply or
  Restart; Set, the index-mode radio buttons, and the text-size slider all
  only *stage* a choice:
  - **Set osu! Song Folder** — shows a red "osu! Songs folder is not set
    yet." if it isn't, or a black "osu! Songs folder is set to..." once a
    choice is staged. The folder must actually be an osu! Songs folder —
    the path has to end in `osu!\Songs` (case-insensitive) or Set rejects
    it with an explanation. Browse Folder fills the field and enables Set;
    Set validates and stages the choice (updating the status text) but
    doesn't save or rebuild the index until Apply/Restart.
  - **Song Index on Startup** — three radio buttons controlling what
    happens to search indexing when the app launches:
    - *Manual Index* — nothing indexes automatically; a "Start Indexing"
      button appears next to the search box (where the progress
      bar/"Index Full Library" button normally live) until you click it,
      which indexes the 100 most recently imported taiko mapsets — after
      that, "Index Full Library" takes over that spot as usual for
      indexing everything else. Until something's actually been indexed,
      the search box and its magnifying-glass button are grayed out, with
      a tooltip pointing at "Start Indexing".
    - *Partial Index (Default)* — automatically indexes the 100 most
      recently imported taiko mapsets on startup (today's existing
      behavior), with "Index Full Library" available afterward.
    - *Full Index* — automatically indexes your entire Songs folder on
      startup.
  - **Text Size** — a 5-notch slider (Small/Default/Medium/Large/Grandma
    → 10/14/17/20/25pt), each name positioned to line up with the
    slider's actual stop for that value (accounting for the thumb's own
    width, not just spread evenly across the row — the endpoints anchor
    by their edge rather than their center so "Grandma", the widest name,
    doesn't overflow past the row and get clipped). Dragging it only
    updates a "Selected: ..." preview (just the name, no point size shown)
    — nothing changes live, and nothing saves until Apply/Restart.
  - Bottom-right, left to right: **Restart**, then **Apply**.
  - **Apply** saves whatever's staged (folder + index mode + text size)
    and shows a quick "Setting applied." confirmation. Since Tk's
    named-font system genuinely supports live updates, most fields and
    buttons pick up a new text size immediately — but a few things with a fixed literal size
    (tool headers, the sidebar, tooltips) don't, and need a restart to
    fully match.
  - **Restart** does the same save, then actually restarts the app after
    you confirm a "this will save and restart" prompt — by spawning a
    genuinely new process and exiting this one (`subprocess.Popen` +
    `os._exit`), not `os.execv`. That more obvious approach reliably
    crashed the packaged .exe with "Failed to import encodings module":
    PyInstaller's `--onefile` bootloader sets an internal `_MEIPASS2`
    environment variable pointing at its temp extraction folder, and
    `execv` replaces the current process in place without letting the
    bootloader clean up first — the re-exec'd process inherits that
    variable pointing at a folder that's about to vanish, and can't find
    its own bundled Python standard library. Spawning a separate process
    with `_MEIPASS2` stripped avoids all of that.
  - Closing the window with anything staged but not applied prompts to
    save, discard, or cancel instead of silently losing the change — a
    re-entrancy guard keeps repeated close-button clicks from stacking up
    duplicate copies of that prompt.

Settings, the BG/Video offset preview windows, and the "The tool is not
working?" popup are all modal (`grab_set()`) — the main window can't be
clicked into while one of them is open, so you don't end up interacting
with stale content behind a popup.

Settings, search results, the BG/Video offset preview windows, and the
Map Cleaner coordinate editor all open positioned relative to wherever
the main window currently is (centered over it, a bit below its top
edge) instead of defaulting to whatever the OS considers the "primary"
monitor — matters if the main window has been moved to a different
monitor. Settings and the coordinate editor also can't be resized by
dragging their corners.

All three of 🔍/📁/⬆ share one consistent icon size/style now
(`Icon.TButton`) and show a short tooltip on hover ("Search", "Open map
manually", "Open map from osu!"); あ and ⚙ have their own hover tooltips
too ("Romanisation Toggle" and "Settings" respectively). Tooltips near the
right edge of a window — "Index Full Library", and the **(i)** icons in
the BG/Video offset preview windows — right-align to their button instead
of left-aligning, so they don't run off-screen.

The title bar's hexagon "set osu! Songs folder" button was removed, but
that's now covered by the ⚙ Settings window's first section instead — the
app still finds your Songs folder automatically on first launch
(`guess_osu_stable_songs_folder()` in `main.py`, checking the usual
install locations), and Settings is where to point it at a different one
if that guess was wrong.

There are no custom minimize/maximize/close buttons in the title bar —
your OS's own window chrome already provides those, so duplicating them
would just be redundant.

## In-app help

Every tool screen has a big header naming the tool, usually with a small
blue **(i)** icon next to it — hover over one for a short floating
explanation of what the tool does. Several individual options (each
checkbox in Map Cleaner, the source dropdowns in Volume/Kiai Copier, the
video resizer/blur checkboxes) have their own **(i)** icon too, right next
to that specific control. The Video Setting's Apply button also has
a "The tool is not working?" link next to it, covering the two most likely
culprits — both the video preview and the Taiko Video Resizer depend on
external programs this app doesn't bundle. Neither URL is shown as raw
text — both are hidden behind inline hyperlinks ("here" for VLC, "Or
download it manually" for FFmpeg) instead of being spelled out:
- The VLC download page, linked from the word "here".
- A button that tries to install FFmpeg automatically via `winget` (`winget
  install -e --id Gyan.FFmpeg`), since that's on Windows 10 1809+/11 by
  default — with a manual-download link ("Or download it manually")
  alongside it in case winget isn't available or you're on macOS/Linux.

## Auto-detecting the currently open map (the lightning-bolt button)

This tries to detect which beatmap is currently open
in a running **osu! stable** client by reading its process memory, the
same technique community tools like OsuMemoryDataProvider/gosumemory use —
ported from
[Piotrekol's ProcessMemoryDataFinder](https://github.com/Piotrekol/ProcessMemoryDataFinder)
(GPL-3.0). This needs `pip install pymem` and only works on Windows with
osu! stable actually running; **osu! lazer isn't supported** (different
memory layout entirely). If detection fails for *any* reason — wrong OS,
`pymem` not installed, osu! not running, Windows blocking the memory read,
a future osu! update shifting the offsets — it silently falls back to
asking you to pick the folder yourself, so nothing breaks either way.

I ported and unit-tested the pointer-chasing/string-decoding algorithm
itself against a synthetic fake memory layout (see `osu_memory.py`) and
confirmed it resolves correctly, but I have no way to test the real
Windows memory-reading path without an actual osu! process and a Windows
machine — if it doesn't pick up your currently-open map, the manual
folder picker is always right there as a fallback, and I'd take a bug
report with your osu! version if you want to help track down an offset
that changed.

## Refreshing in osu!

Every save renames the `.osu` file away and immediately back
(`osu_parser.touch_reload`) right after writing it. A same-name content
edit alone isn't always picked up by osu! stable's file watcher while a
set is already loaded at song select, but a rename/create event reliably
is — so after using any tool, a plain **F5** at song select should be
enough to pick up the changes, without needing to fully back out and
re-enter the map.

## Layout notes

The "Apply to" difficulty checklist is sorted into the standard taiko
difficulty progression — Kantan, Futsuu, Muzukashii, Oni, Inner/Outer/Ura/
Hell/Heavenly Oni, in that order, matched by substring so the longest/most
specific name wins if a diff name overlaps more than one (e.g. "Inner Oni"
doesn't get lumped in with plain "Oni") — anything that doesn't match any
of those sorts to the bottom alphabetically. It wraps into a new column
after 3 entries, so a set with lots of difficulties fans out sideways
instead of running down the screen.

Body text is set to 14pt app-wide (`TkDefaultFont`/`TkTextFont`, which
cascades to every default-styled label/button/entry/checkbox/combobox
automatically), with headers, tooltips, and other explicitly-sized text
scaled to match. The sidebar widened slightly (180px → 230px) to give
labels a bit more breathing room.

## Known limitations (things you may want to extend later)

- The video/BG "preview" windows are static/interactive image previews;
  they don't play synced audio+video like the in-game editor would (except
  the video offset shifter's own VLC-backed preview, which does).
- The map cleaner's "unused green line" detection is a solid heuristic
  (no notes under it, no audible change vs. the previous line) rather than
  a byte-for-byte reproduction of the osu! editor's internal logic.

## Project layout
```
app/
  main.py          entry point + app shell (sidebar, title bar)
  screens.py        one Frame per tool, matches the wireframes
  osu_parser.py      .osu file reader/writer, snap-to-beat logic
  tools_logic.py     the actual behaviour behind each tool
  osu_memory.py      best-effort live osu! process memory reader (Windows)
  icon.png           app icon (window/taskbar, via iconphoto at runtime)
  icon.ico           same icon, multi-resolution, baked into the .exe by
                     build_exe.bat's --icon flag
  requirements.txt
  build_exe.bat
```

## Sidebar

Top to bottom: Metadata Manager, Volume/Kiai Copier, Map Cleaner,
Audio/Offset Settings, BG Setting, Video Setting, Early Volume Setting,
File Name Checker. "Early Volume Setting" is a placeholder for now (just
a header and a "Coming soon." — no functionality yet). The old "About
This Program..." entry has been removed.

## Search bar behavior

The search entry and 🔍 button are disabled while the Songs folder is
being indexed — hovering either while disabled shows a tooltip explaining
why ("Your osu! songs folder is currently being indexed...") instead of
the normal "Search" tooltip; both re-enable automatically the moment
indexing finishes. The search results window is modal like the other
popups (Settings, BG/Video preview, troubleshoot) — it stays focused and
on top for as long as it's open rather than losing focus to the main
window.
