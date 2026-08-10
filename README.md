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

