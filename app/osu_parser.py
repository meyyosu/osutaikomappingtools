"""
osu_parser.py
Lightweight parser/serializer for .osu beatmap files (osu!file format v14),
preserving section order and unknown lines as much as possible.

This is intentionally a *practical* parser: it is line/section based, not a
full grammar, but it correctly round-trips the sections that osu!taiko
Technical Helper needs to touch: General, Metadata, Difficulty, Events,
TimingPoints and HitObjects.
"""

from __future__ import annotations
import os
import re
import math
from dataclasses import dataclass, field
from typing import List, Optional


# ----------------------------------------------------------------------------
# Rounding helper — osu!stable uses "round half away from zero", NOT
# banker's rounding (which is what Python's built-in round() / lazer use).
# ----------------------------------------------------------------------------
def stable_round(x: float) -> int:
    if x >= 0:
        return int(x + 0.5)
    return -int(-x + 0.5)


@dataclass
class TimingPoint:
    time: float
    beat_length: float
    meter: int
    sample_set: int
    sample_index: int
    volume: int
    uninherited: int  # 1 = red (BPM) line, 0 = green (inherited) line
    effects: int
    raw_extra: str = ""  # anything trailing we don't understand, appended verbatim

    def to_line(self) -> str:
        return "{},{},{},{},{},{},{},{}".format(
            stable_round(self.time),
            self._fmt_beat_length(),
            self.meter,
            self.sample_set,
            self.sample_index,
            self.volume,
            self.uninherited,
            self.effects,
        )

    def _fmt_beat_length(self):
        # osu! stores beatLength as a float with up to ~6 sig decimals
        bl = self.beat_length
        if abs(bl - round(bl)) < 1e-9:
            return str(int(round(bl)))
        return ("%.12f" % bl).rstrip("0").rstrip(".")

    @staticmethod
    def parse(line: str) -> Optional["TimingPoint"]:
        parts = line.strip().split(",")
        if len(parts) < 8:
            return None
        try:
            return TimingPoint(
                time=float(parts[0]),
                beat_length=float(parts[1]),
                meter=int(float(parts[2])),
                sample_set=int(float(parts[3])),
                sample_index=int(float(parts[4])),
                volume=int(float(parts[5])),
                uninherited=int(float(parts[6])),
                effects=int(float(parts[7])),
            )
        except ValueError:
            return None


@dataclass
class HitObject:
    x: int
    y: int
    time: float
    obj_type: int
    hit_sound: int
    remainder: str  # everything after hitSound, kept as raw string (extras/curve data etc.)

    def to_line(self) -> str:
        tail = "," + self.remainder if self.remainder else ""
        return "{},{},{},{},{}{}".format(
            self.x, self.y, stable_round(self.time), self.obj_type, self.hit_sound, tail
        )

    @staticmethod
    def parse(line: str) -> Optional["HitObject"]:
        parts = line.strip().split(",")
        if len(parts) < 5:
            return None
        try:
            return HitObject(
                x=int(float(parts[0])),
                y=int(float(parts[1])),
                time=float(parts[2]),
                obj_type=int(float(parts[3])),
                hit_sound=int(float(parts[4])),
                remainder=",".join(parts[5:]),
            )
        except ValueError:
            return None


# HitSound bitmask
HS_NORMAL = 0
HS_WHISTLE = 2
HS_FINISH = 4
HS_CLAP = 8


class Beatmap:
    """Represents one .osu difficulty file."""

    def __init__(self, path: str):
        self.path = path
        self.format_version_line = "osu file format v14"
        self.sections: dict[str, List[str]] = {}
        self.section_order: List[str] = []
        self.timing_points: List[TimingPoint] = []
        self.hit_objects: List[HitObject] = []
        self._load()

    # ------------------------------------------------------------------
    def _load(self):
        with open(self.path, "r", encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()

        if lines and lines[0].lower().startswith("osu file format"):
            self.format_version_line = lines[0].strip()
            lines = lines[1:]

        current = None
        for raw in lines:
            line = raw.rstrip("\n")
            m = re.match(r"^\s*\[(\w+)\]\s*$", line)
            if m:
                current = m.group(1)
                self.section_order.append(current)
                self.sections[current] = []
                continue
            if current is None:
                continue
            self.sections.setdefault(current, []).append(line)

        # Parse TimingPoints
        for line in self.sections.get("TimingPoints", []):
            if not line.strip():
                continue
            tp = TimingPoint.parse(line)
            if tp:
                self.timing_points.append(tp)

        # Parse HitObjects
        for line in self.sections.get("HitObjects", []):
            if not line.strip():
                continue
            ho = HitObject.parse(line)
            if ho:
                self.hit_objects.append(ho)

    # ------------------------------------------------------------------
    # Key/value helpers for General / Metadata / Difficulty
    # ------------------------------------------------------------------
    def get_kv(self, section: str, key: str) -> Optional[str]:
        for line in self.sections.get(section, []):
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() == key:
                    return v.strip()
        return None

    def set_kv(self, section: str, key: str, value: str):
        lines = self.sections.setdefault(section, [])
        if section not in self.section_order:
            self.section_order.append(section)
        for i, line in enumerate(lines):
            if ":" in line:
                k, _ = line.split(":", 1)
                if k.strip() == key:
                    lines[i] = f"{key}:{value}"
                    return
        lines.append(f"{key}:{value}")

    # Convenience metadata accessors -----------------------------------
    def get_metadata(self) -> dict:
        return {
            "Artist": self.get_kv("Metadata", "ArtistUnicode") or self.get_kv("Metadata", "Artist") or "",
            "RomanisedArtist": self.get_kv("Metadata", "Artist") or "",
            "Title": self.get_kv("Metadata", "TitleUnicode") or self.get_kv("Metadata", "Title") or "",
            "RomanisedTitle": self.get_kv("Metadata", "Title") or "",
            "Source": self.get_kv("Metadata", "Source") or "",
            "Mapper": self.get_kv("Metadata", "Creator") or "",
            "Tags": self.get_kv("Metadata", "Tags") or "",
            "Version": self.get_kv("Metadata", "Version") or "",
            "PreviewTime": self.get_kv("General", "PreviewTime") or "",
        }

    def apply_metadata(self, meta: dict):
        if meta.get("Artist") is not None:
            self.set_kv("Metadata", "ArtistUnicode", meta["Artist"])
        if meta.get("RomanisedArtist") is not None:
            self.set_kv("Metadata", "Artist", meta["RomanisedArtist"])
        if meta.get("Title") is not None:
            self.set_kv("Metadata", "TitleUnicode", meta["Title"])
        if meta.get("RomanisedTitle") is not None:
            self.set_kv("Metadata", "Title", meta["RomanisedTitle"])
        if meta.get("Source") is not None:
            self.set_kv("Metadata", "Source", meta["Source"])
        if meta.get("Mapper") is not None:
            self.set_kv("Metadata", "Creator", meta["Mapper"])
        if meta.get("Tags") is not None:
            self.set_kv("Metadata", "Tags", meta["Tags"])
        if meta.get("PreviewTime") is not None and str(meta["PreviewTime"]).strip() != "":
            self.set_kv("General", "PreviewTime", str(meta["PreviewTime"]))

    def get_beatmapset_id(self) -> Optional[int]:
        """The set's osu! website ID, or None if the map is unsubmitted
        (BeatmapSetID missing or -1, the value osu! writes for local-only
        maps)."""
        val = self.get_kv("Metadata", "BeatmapSetID")
        try:
            n = int(val)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    # Background / video events -----------------------------------------
    def _events_content_start(self) -> int:
        """Index of the first non-comment line in [Events] — osu!'s own
        '//Section name' comment headers are cosmetic only, but a newly
        inserted Background/Video event line still reads more naturally
        landing after them than jammed in front."""
        lines = self.sections.get("Events", [])
        i = 0
        while i < len(lines) and lines[i].strip().startswith("//"):
            i += 1
        return i

    def _background_line_index(self) -> Optional[int]:
        for i, line in enumerate(self.sections.get("Events", [])):
            parts = line.split(",")
            if len(parts) >= 3 and parts[0].strip().strip('"') == "0":
                return i
        return None

    def get_background_filename(self) -> Optional[str]:
        idx = self._background_line_index()
        if idx is None:
            return None
        parts = self.sections["Events"][idx].split(",")
        return parts[2].strip().strip('"')

    def set_background_filename(self, new_name: str):
        lines = self.sections.setdefault("Events", [])
        if "Events" not in self.section_order:
            self.section_order.append("Events")
        idx = self._background_line_index()
        if idx is not None:
            parts = lines[idx].split(",")
            parts[2] = f'"{new_name}"'
            lines[idx] = ",".join(parts)
            return
        # No background event line at all yet (e.g. a diff that never had
        # one) — append a fresh one instead of silently doing nothing.
        # set_background_offset, called right after by every caller, then
        # finds this line and fills in the real x/y.
        lines.insert(self._events_content_start(), f'0,0,"{new_name}",0,0')

    def get_background_offset(self) -> "tuple[int, int]":
        """Reads the x,y offset fields from the background event line
        (format: 0,0,"file.jpg",x,y) — an official part of the .osu format
        that the game itself uses to position the background, no image
        editing required."""
        idx = self._background_line_index()
        if idx is None:
            return 0, 0
        parts = self.sections["Events"][idx].split(",")
        try:
            x = int(float(parts[3])) if len(parts) > 3 and parts[3].strip() != "" else 0
        except ValueError:
            x = 0
        try:
            y = int(float(parts[4])) if len(parts) > 4 and parts[4].strip() != "" else 0
        except ValueError:
            y = 0
        return x, y

    def set_background_offset(self, x: int, y: int):
        idx = self._background_line_index()
        if idx is None:
            return  # nothing to offset without a filename — see set_background_filename
        lines = self.sections["Events"]
        parts = lines[idx].split(",")
        while len(parts) < 5:
            parts.append("0")
        parts[3] = str(int(x))
        parts[4] = str(int(y))
        lines[idx] = ",".join(parts)

    def get_video_filename(self) -> Optional[str]:
        for line in self.sections.get("Events", []):
            p = line.strip()
            if p.startswith("Video,") or p.startswith("1,"):
                parts = p.split(",")
                if len(parts) >= 3:
                    return parts[2].strip().strip('"')
        return None

    def get_video_time(self) -> Optional[float]:
        for line in self.sections.get("Events", []):
            p = line.strip()
            if p.startswith("Video,") or p.startswith("1,"):
                parts = p.split(",")
                if len(parts) >= 2:
                    try:
                        return float(parts[1])
                    except ValueError:
                        return None
        return None

    def set_video_filename(self, new_name: str):
        lines = self.sections.setdefault("Events", [])
        if "Events" not in self.section_order:
            self.section_order.append("Events")
        idx = self._video_line_index()
        if idx is not None:
            parts = lines[idx].split(",")
            if len(parts) >= 3:
                parts[2] = f'"{new_name}"'
                lines[idx] = ",".join(parts)
            return
        # No Video event line at all yet (e.g. a diff that never had one) —
        # append a fresh one instead of silently doing nothing. Time
        # defaults to 0; set_video_time, called right after by every
        # caller, then fills in the real offset. Conventionally sits right
        # after the Background line when there is one.
        bg_idx = self._background_line_index()
        insert_at = bg_idx + 1 if bg_idx is not None else self._events_content_start()
        lines.insert(insert_at, f'Video,0,"{new_name}"')

    def shift_video_time(self, delta_ms: float):
        lines = self.sections.get("Events", [])
        for i, line in enumerate(lines):
            p = line.strip()
            if p.startswith("Video,") or p.startswith("1,"):
                parts = p.split(",")
                if len(parts) >= 2:
                    try:
                        parts[1] = str(stable_round(float(parts[1]) + delta_ms))
                    except ValueError:
                        pass
                    lines[i] = ",".join(parts)

    def set_video_time(self, absolute_ms: float):
        """Sets the Video event's start time directly, replacing whatever
        was there — unlike shift_video_time, which adds a relative delta
        (used by the general Offset Shifter, which needs to move
        everything together by an amount)."""
        lines = self.sections.get("Events", [])
        for i, line in enumerate(lines):
            p = line.strip()
            if p.startswith("Video,") or p.startswith("1,"):
                parts = p.split(",")
                if len(parts) >= 2:
                    parts[1] = str(stable_round(absolute_ms))
                    lines[i] = ",".join(parts)

    def _video_line_index(self) -> Optional[int]:
        for i, line in enumerate(self.sections.get("Events", [])):
            p = line.strip()
            if p.startswith("Video,") or p.startswith("1,"):
                return i
        return None

    def _video_sb_block_range(self) -> "Optional[tuple[int, int]]":
        """(start, end) exclusive indices of the indented block directly
        beneath the Video event line — where set_video_sb_commands writes
        its S/F/F/MY 'Taiko Video SB Code' commands. None if there's no
        Video event at all."""
        idx = self._video_line_index()
        if idx is None:
            return None
        lines = self.sections.get("Events", [])
        end = idx + 1
        while end < len(lines) and lines[end][:1] in (" ", "\t"):
            end += 1
        return idx + 1, end

    def clear_video_sb_commands(self):
        """Removes any 'Taiko Video SB Code' block previously written by
        set_video_sb_commands — used when the video offset is (re)applied
        without that option on, so a stale block from an earlier apply
        doesn't linger under the Video line."""
        rng = self._video_sb_block_range()
        if rng is None:
            return
        start, end = rng
        del self.sections["Events"][start:end]

    def set_video_sb_commands(self, start_time: float, end_time: float, scale: float, y_position: float):
        """Writes (replacing any block it previously wrote) the 'Taiko
        Video SB Code' — S(cale)/F(ade)/MY (move-Y) storyboard commands
        directly beneath the Video event line. This is a hybrid-mapset
        trick that fakes the Taiko Video Resizer's crop+shrink live via
        the storyboard instead of a separately re-encoded video file: the
        video scales to `scale` and shifts vertically by `y_position` at
        `start_time`, fades in over the same instant (so it doesn't pop in
        already-shrunk before the offset), then fades back out at
        `end_time` (the map's own final note)."""
        rng = self._video_sb_block_range()
        if rng is None:
            return
        start, end = rng
        st = stable_round(start_time)
        et = stable_round(end_time)
        scale_str = f"{round(scale, 3):g}"
        y_str = str(stable_round(y_position))
        block = [
            f"    S,0,{st},{st},{scale_str}",
            f"    F,0,{st},{st},0,1",
            f"    F,0,{et},{et},1,0",
            f"    MY,0,{st},{st},{y_str},{y_str}",
        ]
        self.sections["Events"][start:end] = block

    def shift_break_times(self, delta_ms: float):
        lines = self.sections.get("Events", [])
        for i, line in enumerate(lines):
            p = line.strip()
            if p.startswith("2,") or p.startswith("Break,"):
                parts = p.split(",")
                if len(parts) >= 3:
                    try:
                        parts[1] = str(stable_round(float(parts[1]) + delta_ms))
                        parts[2] = str(stable_round(float(parts[2]) + delta_ms))
                    except ValueError:
                        pass
                    lines[i] = ",".join(parts)

    # ------------------------------------------------------------------
    def shift_all_times(self, delta_ms: float):
        for tp in self.timing_points:
            tp.time += delta_ms
        for ho in self.hit_objects:
            ho.time += delta_ms
            if ho.obj_type & 8 or ho.obj_type & 0x80:  # spinner or hold note
                # Unlike a slider's length (spatial), a spinner/hold's
                # remainder starts with its EndTime as a raw absolute
                # millisecond value — it must shift along with everything
                # else or the note ends up with the wrong duration.
                parts = ho.remainder.split(",", 1)
                if parts and parts[0]:
                    try:
                        end_time = stable_round(float(parts[0]) + delta_ms)
                        rest = "," + parts[1] if len(parts) > 1 else ""
                        ho.remainder = f"{end_time}{rest}"
                    except ValueError:
                        pass
        self.shift_break_times(delta_ms)
        self.shift_video_time(delta_ms)
        pt = self.get_kv("General", "PreviewTime")
        if pt is not None:
            try:
                self.set_kv("General", "PreviewTime", str(stable_round(float(pt) + delta_ms)))
            except ValueError:
                pass

    # ------------------------------------------------------------------
    def save(self, path: Optional[str] = None):
        path = path or self.path
        out_lines = [self.format_version_line, ""]

        # write TimingPoints/HitObjects back from structured lists
        self.sections["TimingPoints"] = [tp.to_line() for tp in sorted(self.timing_points, key=lambda t: t.time)]
        self.sections["HitObjects"] = [ho.to_line() for ho in sorted(self.hit_objects, key=lambda h: h.time)]

        for section in self.section_order:
            out_lines.append(f"[{section}]")
            out_lines.extend(self.sections.get(section, []))
            out_lines.append("")

        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(out_lines).rstrip() + "\n")

        touch_reload(path)


def touch_reload(path: str):
    """Renames the file away and immediately back after writing it, so
    osu!'s file-system watcher sees a rename event rather than just a
    content change. osu! stable reliably prompts to reload a beatmap set
    on a rename/create event at song select (just press F5), whereas a
    same-name content edit alone is not always picked up while a set is
    already loaded — so this lets a simple F5 be enough instead of having
    to fully re-enter the map. Best-effort: the file is already saved
    correctly either way, so any failure here (permissions, the file being
    open elsewhere) is silently ignored."""
    try:
        tmp = path + ".taiko_helper_tmp"
        os.rename(path, tmp)
        os.rename(tmp, path)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# Beat snap divisor sets (per spec: some divisors are the UNION of ticks
# from several base divisors, matching osu!stable's editor tick colouring)
# ----------------------------------------------------------------------------
DIVISOR_BASES = {
    "1/1": [1],
    "1/2": [2],
    "1/4": [4],
    "1/6": [6],
    "1/12": [4, 6],
    "1/24": [8, 12],
    "1/36": [4, 6, 9],
    "1/48": [12, 16],
}


def _governing_timing_point(time: float, uninherited_points: List[TimingPoint]) -> Optional[TimingPoint]:
    if not uninherited_points:
        return None
    governing = uninherited_points[0]
    for tp in uninherited_points:
        if tp.time <= time + 1e-6:
            governing = tp
        else:
            break
    return governing


def _barline_times(uninherited_points: List[TimingPoint], end_time: float) -> List[float]:
    """All barline timestamps implied by the map's time signatures, from
    each red line's own time up to the next red line (or `end_time` for
    the last segment). A 3/4 segment gets a barline every 3 beats, a 4/4
    segment every 4, etc. Barlines are what taiko's SV/kiai visuals key
    off of even when no note sits directly under the governing line."""
    times = []
    ordered = sorted(uninherited_points, key=lambda t: t.time)
    for i, red in enumerate(ordered):
        bar_length = red.beat_length * red.meter
        if bar_length <= 0:
            continue
        seg_end = ordered[i + 1].time if i + 1 < len(ordered) else end_time
        t = red.time
        while t <= seg_end + 1e-6:
            times.append(t)
            t += bar_length
    return times


def snap_time(time: float, timing_points: List[TimingPoint], divisor_key: str) -> float:
    """Snap `time` (ms) to the nearest tick defined by divisor_key.

    The snapped time is reconstructed as trunc(trunc(red.time) + beatIndex
    * beatLength) — truncating the governing red line's own time *before*
    adding whole multiples of the beat length, rather than rounding the
    final (red.time + beatIndex*beatLength) sum as a single float. A red
    line's time isn't always a whole number (e.g. computed from a
    non-integer BPM, or imported from analysis tooling), and rounding the
    combined value at the end could round to a different integer than
    osu!stable's own snapping does — landing a note 1ms off from where the
    editor itself would place it, even though the "nearest beat" being
    targeted was correctly identified."""
    uninherited = sorted([tp for tp in timing_points if tp.uninherited == 1], key=lambda t: t.time)
    gov = _governing_timing_point(time, uninherited)
    if gov is None:
        return time

    red_time_trunc = math.trunc(gov.time)
    bases = DIVISOR_BASES.get(divisor_key, [4])
    best_time = time
    best_dist = float("inf")
    for d in bases:
        beat_frac = gov.beat_length / d
        if beat_frac <= 0:
            continue
        beat_index = stable_round((time - gov.time) / beat_frac)
        candidate = math.trunc(red_time_trunc + beat_index * beat_frac)
        dist = abs(candidate - time)
        if dist < best_dist:
            best_dist = dist
            best_time = candidate
    return float(best_time)


def list_difficulty_files(folder: str) -> List[str]:
    """Return .osu files in a beatmap folder, sorted."""
    if not folder or not os.path.isdir(folder):
        return []
    return sorted([f for f in os.listdir(folder) if f.lower().endswith(".osu")])


def read_basic_metadata(path: str) -> "Optional[dict]":
    """Fast, partial read of just [Metadata] and the background filename —
    stops at [HitObjects] so scanning an entire Songs folder (thousands of
    files) for search purposes doesn't have to parse every note/timing
    point. Returns None if the file can't be read at all."""
    artist = title = artist_unicode = title_unicode = creator = tags = mode = version = None
    bg_filename = None
    section = None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                m = re.match(r"^\[(\w+)\]$", line)
                if m:
                    section = m.group(1)
                    if section == "HitObjects":
                        break
                    continue
                if section == "General" and ":" in line:
                    k, v = line.split(":", 1)
                    if k.strip() == "Mode":
                        mode = v.strip()
                elif section == "Metadata" and ":" in line:
                    k, v = line.split(":", 1)
                    k, v = k.strip(), v.strip()
                    if k == "Artist":
                        artist = v
                    elif k == "ArtistUnicode":
                        artist_unicode = v
                    elif k == "Title":
                        title = v
                    elif k == "TitleUnicode":
                        title_unicode = v
                    elif k == "Creator":
                        creator = v
                    elif k == "Tags":
                        tags = v
                    elif k == "Version":
                        version = v
                elif section == "Events" and bg_filename is None:
                    parts = line.split(",")
                    if len(parts) >= 3 and parts[0].strip().strip('"') == "0":
                        bg_filename = parts[2].strip().strip('"')
    except OSError:
        return None

    return {
        "Artist": artist_unicode or artist or "",
        "Title": title_unicode or title or "",
        "RomanisedArtist": artist or artist_unicode or "",
        "RomanisedTitle": title or title_unicode or "",
        "Mapper": creator or "",
        "Tags": tags or "",
        "Version": version or "",
        "BackgroundFile": bg_filename,
        "Mode": mode,  # "0"=osu!, "1"=taiko, "2"=catch, "3"=mania; None if absent (defaults to osu!)
    }


def get_diff_display_map(folder: str, diff_files: List[str]) -> "dict[str, str]":
    """Maps a human-friendly display label (the diff's [Version] name) to its
    actual filename, so dropdowns can show just 'Oni' / 'Muzukashii' instead
    of the full 'Artist - Title (Mapper) [Oni].osu'. Falls back to the
    filename, and disambiguates duplicate version names by appending it."""
    display_map: dict[str, str] = {}
    seen_labels: dict[str, int] = {}
    for fname in diff_files:
        try:
            bm = Beatmap(os.path.join(folder, fname))
            label = bm.get_kv("Metadata", "Version") or fname
        except Exception:
            label = fname
        if label in seen_labels:
            seen_labels[label] += 1
            label = f"{label} ({fname})"
        else:
            seen_labels[label] = 1
        display_map[label] = fname
    return display_map


# Standard taiko difficulty progression, easiest to hardest. Used to sort
# difficulty lists so they read top-to-bottom in the order players expect,
# rather than whatever order the filesystem happens to return.
TAIKO_DIFF_PRIORITY = [
    "Kantan", "Futsuu", "Muzukashii", "Oni",
    "Inner Oni", "Outer Oni", "Ura Oni", "Hell Oni", "Heavenly Oni",
]
_GENERIC_TAIKO_DIFF_NAMES = {name.lower() for name in TAIKO_DIFF_PRIORITY}


def is_generic_taiko_diff_name(label: str) -> bool:
    """True for an *exact* (case-insensitive, trimmed) match against one of
    the standard TAIKO_DIFF_PRIORITY names — "Oni", not "Devil Oni" or
    "Itsuki's Oni", which are custom names that merely contain the word.
    Used to keep generic difficulty names out of song search (see
    build_song_index in main.py) — searching "oni" shouldn't just match
    every mapset that happens to have a standard Oni difficulty."""
    return label.strip().lower() in _GENERIC_TAIKO_DIFF_NAMES


_NO_MATCH_RANK = len(TAIKO_DIFF_PRIORITY)
_CUSTOM_ONI_RANK = len(TAIKO_DIFF_PRIORITY) + 1

# Matches a possessive word immediately before "Oni" — "Itsuki's Oni",
# "Ceras' Oni" — so those still read as "someone's take on the Oni
# difficulty" rather than an unrelated custom top-diff name.
_POSSESSIVE_ONI_RE = re.compile(r"\w['’]s?\s+oni\b", re.IGNORECASE)


def taiko_diff_sort_key(label: str):
    """Sort key ranking a difficulty name by TAIKO_DIFF_PRIORITY. A label
    is matched by substring (case-insensitive) against every entry in the
    list; when more than one matches (e.g. "Inner Oni" contains "Oni" too),
    the longest/most specific match wins. Anything matching nothing sorts
    after the whole priority list, alphabetically among itself — except a
    label that only matches the generic "Oni" entry by containing the word
    somewhere in a custom name (e.g. a mapper's own top-diff name like
    "Devil Oni") isn't the real Oni difficulty, so it's pushed past even
    that "no match" group, always last, alphabetically among itself. A
    *possessive* custom name immediately before "Oni" — "Itsuki's Oni",
    "Ceras' Oni" — is exempted from that demotion and sorts at the same
    tier as plain "Oni", since it's still someone's version of that same
    difficulty rather than an unrelated custom name that merely contains
    the word."""
    label_lower = label.lower()
    matches = [(len(name), i) for i, name in enumerate(TAIKO_DIFF_PRIORITY)
               if name.lower() in label_lower]
    if not matches:
        return (_NO_MATCH_RANK, label_lower)
    matches.sort(key=lambda t: -t[0])
    _, rank = matches[0]
    if TAIKO_DIFF_PRIORITY[rank] == "Oni" and label_lower.strip() != "oni":
        if _POSSESSIVE_ONI_RE.search(label_lower):
            return (rank, label_lower)
        return (_CUSTOM_ONI_RANK, label_lower)
    return (rank, label_lower)
