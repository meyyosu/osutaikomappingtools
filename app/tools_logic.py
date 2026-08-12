"""
tools_logic.py
Implements the actual behaviour behind each tool in the sidebar, operating
on Beatmap objects from osu_parser.py. Kept separate from the GUI so it can
be unit-tested independently.
"""

from __future__ import annotations
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import List, Optional

import osu_parser
from osu_parser import (
    Beatmap, TimingPoint, HitObject, snap_time, stable_round,
    HS_NORMAL, HS_WHISTLE, HS_FINISH, HS_CLAP,
)

# ffmpeg.exe is a console-subsystem binary — without CREATE_NO_WINDOW a
# fresh console window pops up and sits there for the whole encode even
# though stdout/stderr are already redirected via capture_output.
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run_ffmpeg(cmd: List[str], timeout: int = 600):
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout,
                    creationflags=_SUBPROCESS_FLAGS)


# =============================================================================
# ffmpeg / ffprobe binary resolution
#
# Neither binary is guaranteed to be on PATH — this app has always relied
# on a separate system install (see screens.py's "Install FFmpeg
# automatically" / build_exe.bat's "copy ffmpeg.exe alongside it if
# distributing" note). `_resolve_binary` prefers a copy sitting directly
# next to the running app (a genuinely bundled distribution) over PATH, so
# a build that ships both .exe files works out of the box regardless of
# what's installed system-wide.
# =============================================================================
def _bundled_dir() -> str:
    """Directory the running app itself lives in — a frozen PyInstaller
    exe's own folder, or this .py file's folder when running from
    source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_binary(name: str) -> str:
    """Absolute path to `name` next to the app if it's there, else just
    `name` (left for `subprocess`/`shutil.which` to resolve via PATH)."""
    exe_name = f"{name}.exe" if os.name == "nt" else name
    local = os.path.join(_bundled_dir(), exe_name)
    return local if os.path.isfile(local) else name


def _binary_available(name: str) -> bool:
    b = _resolve_binary(name)
    return os.path.isfile(b) if os.path.isabs(b) else shutil.which(b) is not None


def ffmpeg_available() -> bool:
    return _binary_available("ffmpeg")


def ffprobe_available() -> bool:
    return _binary_available("ffprobe")


def audio_tools_fully_available() -> bool:
    """Both ffmpeg and ffprobe resolvable (bundled next to the app or on
    PATH) — the combination needed to keep audio quality-preserving
    wherever possible (see `add_silence_to_audio`'s own notes on why
    ffprobe's structured output is preferred over parsing ffmpeg's log)."""
    return ffmpeg_available() and ffprobe_available()


def _find_winget_ffmpeg_suite():
    """Best-effort discovery of a just-installed Gyan.FFmpeg's
    ffmpeg.exe/ffprobe.exe under winget's own package directory, for when
    this already-running process's PATH doesn't yet reflect an install
    winget just performed (PATH changes don't propagate to processes
    already running). Returns (ffmpeg_path, ffprobe_path), either half
    None if not found."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if not os.path.isdir(base):
        return None, None
    ffmpeg_hits = glob.glob(os.path.join(base, "Gyan.FFmpeg_*", "**", "ffmpeg.exe"), recursive=True)
    ffprobe_hits = glob.glob(os.path.join(base, "Gyan.FFmpeg_*", "**", "ffprobe.exe"), recursive=True)
    return (ffmpeg_hits[0] if ffmpeg_hits else None,
            ffprobe_hits[0] if ffprobe_hits else None)


# The same stable URL winget's own Gyan.FFmpeg package pulls its build
# from — always resolves to a current Windows "essentials" build (which
# includes ffmpeg.exe/ffprobe.exe/ffplay.exe). Used as a direct-download
# fallback when winget itself isn't available at all, since the earlier
# winget-only version of this function would just give up with a "install
# manually" error in that case rather than actually getting the user
# unblocked automatically. Two independent sources, tried in order — a
# single host being temporarily down (confirmed for real during
# development: gyan.dev returned a plain 503 with Retry-After: 600 at one
# point, an ordinary transient overload, nothing wrong with the URL
# itself) shouldn't leave this feature dead in the water.
FFMPEG_DIRECT_DOWNLOAD_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
]


def _download_to_file(url: str, dest_path: str, timeout: int = 60, max_hops: int = 4):
    """GETs `url` into `dest_path`, following not just real HTTP redirects
    (which `urlopen` already does on its own) but also "meta refresh" HTML
    redirect pages — confirmed for real during development that
    VideoLAN's mirror selector sometimes answers a direct download URL
    with `200 OK` + an HTML page containing
    `<meta http-equiv="refresh" content="5;URL='...'">` instead of a
    proper `3xx` redirect, which `urlopen` has no way to follow on its
    own. Raises RuntimeError (not caught here) if that happens more than
    `max_hops` times, or `OSError`/`urllib.error.URLError` on a genuine
    network failure — left to the caller, which already wraps its own
    zip-extraction logic in a broader try/except covering both."""
    for _ in range(max_hops):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            first = resp.read(4096)
            looks_html = (first.lstrip()[:15].lower().startswith(b"<!doctype html")
                          or first.lstrip()[:5].lower().startswith(b"<html"))
            if looks_html:
                text = first.decode("utf-8", "ignore")
                m = re.search(r'http-equiv=["\']refresh["\'][^>]*url=[\'"]([^\'"]+)[\'"]',
                               text, re.IGNORECASE)
                if not m:
                    raise RuntimeError(f"Got an HTML page instead of a file from {url}.")
                url = m.group(1)
                continue
            with open(dest_path, "wb") as f:
                f.write(first)
                shutil.copyfileobj(resp, f)
            return
    raise RuntimeError(f"Too many redirect hops downloading {url}.")


def _download_and_extract_ffmpeg_zip_from(url: str, dest_dir: str,
                                           need_ffmpeg: bool = True, need_ffprobe: bool = True):
    """One attempt: downloads `url` and pulls ffmpeg.exe/ffprobe.exe out of
    its bin/ folder (the zip's top-level folder is version-named, e.g.
    ffmpeg-7.1-essentials_build/, so this searches by suffix rather than a
    fixed path). The zip is always checked for *both* binaries (a build
    genuinely missing one of them is a sign its layout changed, worth
    failing loudly on), but only the one(s) requested via
    `need_ffmpeg`/`need_ffprobe` actually get written into `dest_dir` — a
    caller that only needs to replace a single missing binary shouldn't
    have the other, already-fine one silently overwritten too. Raises
    RuntimeError with a user-facing message on any failure (network, bad
    zip, missing binaries in the archive) — see
    `_download_and_extract_ffmpeg_zip` for the multi-source wrapper
    actually meant to be called."""
    tmp_zip = None
    try:
        fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        _download_to_file(url, tmp_zip, timeout=60)

        with zipfile.ZipFile(tmp_zip) as zf:
            wanted = {"ffmpeg.exe": None, "ffprobe.exe": None}
            for name in zf.namelist():
                base = os.path.basename(name)
                if base in wanted and name.replace("\\", "/").endswith(f"/bin/{base}"):
                    wanted[base] = name
            missing = [k for k, v in wanted.items() if v is None]
            if missing:
                raise RuntimeError(
                    f"Downloaded FFmpeg build from {url} didn't contain {', '.join(missing)} — "
                    "the build layout may have changed.")
            needed = {"ffmpeg.exe": need_ffmpeg, "ffprobe.exe": need_ffprobe}
            for base, member in wanted.items():
                if not needed[base]:
                    continue
                with zf.open(member) as src_f, open(os.path.join(dest_dir, base), "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as e:
        raise RuntimeError(f"Couldn't download FFmpeg from {url} ({e}).")
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            os.remove(tmp_zip)


def _download_and_extract_ffmpeg_zip(dest_dir: str, need_ffmpeg: bool = True, need_ffprobe: bool = True):
    """Tries each URL in `FFMPEG_DIRECT_DOWNLOAD_URLS` in turn, returning
    on the first success. Raises RuntimeError (the last attempt's message,
    plus a pointer to the manual-download page) only if every source
    fails."""
    last_error = None
    for url in FFMPEG_DIRECT_DOWNLOAD_URLS:
        try:
            _download_and_extract_ffmpeg_zip_from(url, dest_dir, need_ffmpeg, need_ffprobe)
            return
        except RuntimeError as e:
            last_error = e
    raise RuntimeError(f"{last_error} Install FFmpeg manually from "
                        "https://ffmpeg.org/download.html instead.")


def install_ffmpeg_suite_bundled(need_ffmpeg: bool = True, need_ffprobe: bool = True) -> str:
    """Gets ffmpeg.exe and/or ffprobe.exe sitting directly next to this app
    (see `_bundled_dir`), so audio processing gets the best available
    quality regardless of what (if anything) is installed system-wide.
    `need_ffmpeg`/`need_ffprobe` let a caller that already has one of the
    two (checked via `ffmpeg_available()`/`ffprobe_available()`) ask for
    only the specific binary that's actually missing — the winget/direct
    download source always carries both regardless (there's no way to fetch
    "just ffprobe" from either), but the final copy step only overwrites
    the bundled dir with whichever one(s) were actually requested, so an
    already-good bundled copy of the other isn't needlessly re-copied.
    Meant to run on a worker thread — this blocks for as long as the
    winget install / direct download takes. If everything needed is
    already resolvable via PATH (a prior separate install), just copies it
    in, no install needed. Prefers winget when it's available (it's what
    manages the install afterward, e.g. for updates); falls back to
    downloading the build directly (`_download_and_extract_ffmpeg_zip`)
    when winget isn't present at all, so this doesn't just give up and
    tell the user to sort it out by hand. Raises RuntimeError with a
    user-facing message on failure."""
    if os.name != "nt":
        raise RuntimeError("Automatic install is only wired up for Windows. On macOS: "
                            "`brew install ffmpeg`. On Linux: use your package manager "
                            "(e.g. `apt install ffmpeg`), or see https://ffmpeg.org/download.html.")

    dest_dir = _bundled_dir()
    src_ffmpeg = shutil.which("ffmpeg")
    src_ffprobe = shutil.which("ffprobe")

    def have_needed():
        return (not need_ffmpeg or src_ffmpeg) and (not need_ffprobe or src_ffprobe)

    if not have_needed():
        if shutil.which("winget") is not None:
            try:
                subprocess.run(
                    ["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                     "--accept-package-agreements", "--accept-source-agreements", "--silent"],
                    check=True, capture_output=True, timeout=600, creationflags=_SUBPROCESS_FLAGS,
                )
            except subprocess.CalledProcessError as e:
                detail = e.stderr.decode("utf-8", "ignore").strip()[:300] if e.stderr else str(e)
                raise RuntimeError(f"winget install failed: {detail}")
            except subprocess.SubprocessError as e:
                raise RuntimeError(f"winget install failed: {e}")
            src_ffmpeg = shutil.which("ffmpeg") or src_ffmpeg
            src_ffprobe = shutil.which("ffprobe") or src_ffprobe
            if not have_needed():
                found_ffmpeg, found_ffprobe = _find_winget_ffmpeg_suite()
                src_ffmpeg = src_ffmpeg or found_ffmpeg
                src_ffprobe = src_ffprobe or found_ffprobe

        if not have_needed():
            # No winget, or winget somehow didn't yield what's needed —
            # get a known-good build directly instead of giving up. The
            # zip always contains both binaries; only the ones actually
            # requested get written into dest_dir.
            _download_and_extract_ffmpeg_zip(dest_dir, need_ffmpeg, need_ffprobe)
            return dest_dir

    for need, src in ((need_ffmpeg, src_ffmpeg), (need_ffprobe, src_ffprobe)):
        if need and src:
            shutil.copy2(src, os.path.join(dest_dir, os.path.basename(src)))
    return dest_dir


# =============================================================================
# VLC (video offset live preview)
#
# python-vlc's own find_lib() (see the installed vlc.py) resolves libvlc
# purely at `import vlc` time, via — in order — the PYTHON_VLC_LIB_PATH /
# PYTHON_VLC_MODULE_PATH env vars, PATH, the `Software\VideoLAN\VLC`
# registry key (which is why a normal *system* VLC install just works
# with zero effort from this app — no copying needed, unlike ffmpeg),
# and a couple of hardcoded Program Files paths. Crucially, unlike
# ffmpeg's simple "resolve an absolute path per subprocess call", it
# never looks next to this app's own exe — so bundling a copy there only
# actually gets used if PYTHON_VLC_LIB_PATH/PYTHON_VLC_MODULE_PATH are
# pointed at it *before* `import vlc` ever runs anywhere in the process
# (`configure_bundled_vlc_env`, confirmed working end-to-end against a
# real downloaded+extracted portable build during development).
# =============================================================================
VLC_MANUAL_DOWNLOAD_URL = "https://www.videolan.org/vlc/"

# VideoLAN's own "always current" directory listing for the Windows
# 64-bit build — the actual filename inside is version-named (e.g.
# vlc-3.0.23-win64.zip) and changes on every release, so this is scraped
# for the current filename rather than hardcoded (see
# `_find_latest_vlc_zip_url`).
_VLC_ZIP_LISTING_URL = "https://get.videolan.org/vlc/last/win64/"


def _find_latest_vlc_zip_url() -> str:
    req = urllib.request.Request(_VLC_ZIP_LISTING_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError) as e:
        raise RuntimeError(f"Couldn't reach {_VLC_ZIP_LISTING_URL} ({e}).")
    m = re.search(r'href="(vlc-[0-9][^"]*-win64\.zip)"', html)
    if not m:
        raise RuntimeError("Couldn't find a current VLC build on VideoLAN's download page — "
                            "its page layout may have changed.")
    return _VLC_ZIP_LISTING_URL + m.group(1)


def _download_and_extract_vlc_zip(dest_dir: str):
    """Downloads the current official VLC portable win64 zip build and
    extracts libvlc.dll/libvlccore.dll plus the entire plugins/ folder
    into `dest_dir` — exactly what `configure_bundled_vlc_env` points
    python-vlc at. Verified end-to-end during development: downloaded a
    real build, extracted it, then loaded it via python-vlc purely
    through the env-var override with no system VLC install present at
    all. Raises RuntimeError with a user-facing message on any failure."""
    url = _find_latest_vlc_zip_url()
    tmp_zip = None
    try:
        fd, tmp_zip = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        _download_to_file(url, tmp_zip, timeout=60)

        with zipfile.ZipFile(tmp_zip) as zf:
            names = zf.namelist()
            dll_entries = [n for n in names if n.lower().endswith("/libvlc.dll")]
            if not dll_entries:
                raise RuntimeError(f"Downloaded VLC build from {url} didn't contain libvlc.dll — "
                                    "the build layout may have changed.")
            root = dll_entries[0].rsplit("/", 1)[0]  # e.g. "vlc-3.0.23"

            wanted = {"libvlc.dll": None, "libvlccore.dll": None}
            for n in names:
                parent, _, base = n.rpartition("/")
                if parent == root and base in wanted:
                    wanted[base] = n
            missing = [k for k, v in wanted.items() if v is None]
            if missing:
                raise RuntimeError(f"Downloaded VLC build from {url} didn't contain {', '.join(missing)}.")
            for base, member in wanted.items():
                with zf.open(member) as src_f, open(os.path.join(dest_dir, base), "wb") as out_f:
                    shutil.copyfileobj(src_f, out_f)

            plugins_prefix = root + "/plugins/"
            plugins_dest = os.path.join(dest_dir, "plugins")
            for n in names:
                if n.startswith(plugins_prefix) and not n.endswith("/"):
                    rel_parts = n[len(plugins_prefix):].split("/")
                    out_path = os.path.join(plugins_dest, *rel_parts)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with zf.open(n) as src_f, open(out_path, "wb") as out_f:
                        shutil.copyfileobj(src_f, out_f)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as e:
        raise RuntimeError(f"Couldn't download/extract VLC ({e}). Install it manually from "
                            f"{VLC_MANUAL_DOWNLOAD_URL} instead.")
    finally:
        if tmp_zip and os.path.exists(tmp_zip):
            os.remove(tmp_zip)


def configure_bundled_vlc_env():
    """If a bundled libvlc.dll is sitting next to this app (see
    `_bundled_dir` / `install_vlc_bundled`), points python-vlc's own
    env-var override (PYTHON_VLC_LIB_PATH / PYTHON_VLC_MODULE_PATH) at it
    so the *next* `import vlc` anywhere in the process picks it up
    instead of depending on a system-wide install. Must run before that
    first import — python-vlc's find_lib() only reads these vars at
    import time, and Python only ever executes a module's top-level code
    once. `setdefault` so an explicitly user-set env var (the whole point
    of python-vlc providing this override in the first place) always
    wins over this. No-op if nothing's bundled — safe to call
    unconditionally on every check."""
    if os.name != "nt":
        return
    d = _bundled_dir()
    dll = os.path.join(d, "libvlc.dll")
    plugins = os.path.join(d, "plugins")
    if os.path.isfile(dll):
        os.environ.setdefault("PYTHON_VLC_LIB_PATH", dll)
        if os.path.isdir(plugins):
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", plugins)


def vlc_available() -> bool:
    """Best-effort check for whether the video preview's VLC backend can
    actually be loaded: a bundled copy next to the app, or whatever
    python-vlc's own find_lib() can locate on its own (PATH / registry /
    standard install paths). This performs the actual `import vlc` as
    part of checking (there's no cheaper way to ask python-vlc "would
    this work" without it) — safe to call repeatedly; Python only
    re-executes the module's top-level code (and thus find_lib()) again
    if a prior attempt failed, since a failed import isn't cached."""
    configure_bundled_vlc_env()
    try:
        import vlc  # noqa: F401
    except Exception:
        return False
    return True


def install_vlc_bundled() -> str:
    """Gets a working VLC backend for the video preview by whatever means
    is available. Meant to run on a worker thread — blocks for as long as
    winget/the download takes. Unlike ffmpeg, a system-wide VLC install
    (via winget) needs nothing copied afterward: python-vlc's own
    find_lib() already checks the `Software\\VideoLAN\\VLC` registry key
    fresh on every `import vlc`, which VLC's real installer (what winget
    runs under the hood) always sets — so winget succeeding is already a
    complete fix by itself. Only when winget isn't available, or a winget
    install somehow still leaves VLC undetectable, does this fall back to
    `_download_and_extract_vlc_zip` (a portable copy next to the app,
    wired up via `configure_bundled_vlc_env`). Raises RuntimeError with a
    user-facing message on failure."""
    if os.name != "nt":
        raise RuntimeError(f"Automatic install is only wired up for Windows. Get VLC from "
                            f"{VLC_MANUAL_DOWNLOAD_URL} for your platform.")

    if vlc_available():
        return "system"

    if shutil.which("winget") is not None:
        try:
            subprocess.run(
                ["winget", "install", "-e", "--id", "VideoLAN.VLC",
                 "--accept-package-agreements", "--accept-source-agreements", "--silent"],
                check=True, capture_output=True, timeout=600, creationflags=_SUBPROCESS_FLAGS,
            )
        except subprocess.SubprocessError:
            pass  # winget missing/failed — fall through to direct download below
        else:
            if vlc_available():
                return "system"
            # winget reported success but VLC still isn't discoverable —
            # fall through to a direct download as a safety net rather
            # than leaving the user stuck.

    dest_dir = _bundled_dir()
    _download_and_extract_vlc_zip(dest_dir)
    if not vlc_available():
        raise RuntimeError("VLC was downloaded but still couldn't be loaded. Try restarting the app, "
                            f"or install it manually from {VLC_MANUAL_DOWNLOAD_URL}.")
    return dest_dir


# =============================================================================
# Metadata Manager
# =============================================================================
def apply_metadata_to_diffs(folder: str, diff_files: List[str], meta: dict):
    for fname in diff_files:
        bm = Beatmap(os.path.join(folder, fname))
        bm.apply_metadata(meta)
        bm.save()


def import_metadata_from(folder: str, diff_file: str) -> dict:
    bm = Beatmap(os.path.join(folder, diff_file))
    return bm.get_metadata()


# =============================================================================
# Volume / Kiai Copier
# =============================================================================
def _effective_beat_length_at(time: float, timing_points: List[TimingPoint]) -> float:
    """Returns the beatLength that's actually in effect at `time` on a
    given timeline (the most recent green line at or before `time`, else
    osu!'s default of -100 / 1.0x if none precede it)."""
    candidates = [tp for tp in timing_points if tp.uninherited == 0 and tp.time <= time + 1e-6]
    if not candidates:
        return -100.0
    return max(candidates, key=lambda t: t.time).beat_length


def _effective_state_at(time: float, timing_points: List[TimingPoint]):
    """Returns (sample_set, sample_index, volume, effects) in effect at
    `time` — from the most recent timing point (red or green) at or before
    it, else osu!'s defaults."""
    candidates = [tp for tp in timing_points if tp.time <= time + 1e-6]
    if not candidates:
        return (1, 0, 100, 0)
    latest = max(candidates, key=lambda t: t.time)
    return (latest.sample_set, latest.sample_index, latest.volume, latest.effects)


def _volume_changing_greens(points: List[TimingPoint]) -> List[TimingPoint]:
    """Returns only the green lines whose volume actually differs from
    whatever volume was already in effect immediately before them (red or
    green) — i.e. the lines that represent a genuine volume change, not
    every green line regardless of whether it changes anything."""
    ordered = sorted(points, key=lambda t: t.time)
    result = []
    last_volume = None
    for tp in ordered:
        if tp.uninherited == 0 and (last_volume is None or tp.volume != last_volume):
            result.append(tp)
        last_volume = tp.volume
    return result


def _kiai_changing_greens(points: List[TimingPoint]) -> List[TimingPoint]:
    """Returns only the green lines where the kiai bit (effects & 1)
    actually toggles compared to whatever was in effect immediately before
    them — the genuine kiai start/end points, not every green line."""
    ordered = sorted(points, key=lambda t: t.time)
    result = []
    last_kiai = None
    for tp in ordered:
        kiai = tp.effects & 1
        if tp.uninherited == 0 and (last_kiai is None or kiai != last_kiai):
            result.append(tp)
        last_kiai = kiai
    return result


def copy_volumes(folder: str, source_diff: str, target_diffs: List[str]):
    src = Beatmap(os.path.join(folder, source_diff))
    src_points = sorted(src.timing_points, key=lambda t: t.time)
    src_greens = _volume_changing_greens(src_points)

    for fname in target_diffs:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)

        # Update volume on whichever of the target's own timing points is
        # closest to each source timing point (existing behaviour).
        for tp in bm.timing_points:
            match = _closest_timing_point(tp.time, src_points)
            if match is not None:
                tp.volume = match.volume

        # Add a new green line for any source volume-changing green line
        # whose timestamp has no counterpart at all in the target —
        # otherwise a volume change that only exists as a standalone green
        # line in the source (with nothing at that time in the target) was
        # silently dropped instead of being reproduced. Only genuinely
        # volume-changing lines are considered — a green line in the source
        # that doesn't actually change the volume from what came before it
        # has nothing worth copying. The new line's beatLength preserves
        # whatever scroll velocity is already in effect in the target at
        # that point, so this only ever changes volume/sampleset, never SV.
        existing_times = {round(tp.time) for tp in bm.timing_points}
        for src_tp in src_greens:
            t = round(src_tp.time)
            if t in existing_times:
                continue
            new_tp = TimingPoint(
                time=float(t),
                beat_length=_effective_beat_length_at(t, bm.timing_points),
                meter=4,
                sample_set=src_tp.sample_set,
                sample_index=src_tp.sample_index,
                volume=src_tp.volume,
                uninherited=0,
                effects=0,
            )
            bm.timing_points.append(new_tp)
            existing_times.add(t)

        bm.save()


def copy_kiai(folder: str, source_diff: str, target_diffs: List[str]):
    """Copies kiai time (the effects bit 0 toggle) from a source diff onto
    the target diffs — separate from volume copying, and only touches the
    kiai bit: sample set, volume, and scroll velocity in the target are
    left exactly as they already were, both on matched existing timing
    points and on any newly-inserted ones."""
    src = Beatmap(os.path.join(folder, source_diff))
    src_points = sorted(src.timing_points, key=lambda t: t.time)
    src_kiai_greens = _kiai_changing_greens(src_points)

    for fname in target_diffs:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)

        # Set the kiai bit on every target timing point to whatever the
        # source's kiai state actually was AT that point's own time — the
        # most recent source point at-or-before it (the "governing" state),
        # not merely whichever source point happens to be nearest in raw
        # time distance. Matching by nearest-distance let a target point
        # "see into the future": a point sitting inside an active kiai
        # section but closer in time to the upcoming off-toggle than to the
        # on-toggle that started it would get switched off early, while a
        # point just past the off-toggle could stay matched to an earlier
        # on-toggle if it happened to be numerically closer to it than to
        # the off-toggle — silently dropping the off transition depending
        # on how densely the target's own timing points happened to be
        # spaced. Every other bit of `effects` (and every other field) is
        # left untouched.
        for tp in bm.timing_points:
            _, _, _, src_effects = _effective_state_at(tp.time, src_points)
            src_kiai = src_effects & 1
            tp.effects = (tp.effects & ~1) | src_kiai

        # Add a new green line for any source kiai toggle point that has
        # no counterpart at all in the target, so a kiai section that only
        # exists as a standalone green line in the source still gets
        # reproduced. The new line preserves whatever sample
        # set/volume/scroll velocity is already in effect in the target at
        # that point — only the kiai bit is actually new.
        existing_times = {round(tp.time) for tp in bm.timing_points}
        for src_tp in src_kiai_greens:
            t = round(src_tp.time)
            if t in existing_times:
                continue
            sample_set, sample_index, volume, effects = _effective_state_at(t, bm.timing_points)
            src_kiai = src_tp.effects & 1
            new_tp = TimingPoint(
                time=float(t),
                beat_length=_effective_beat_length_at(t, bm.timing_points),
                meter=4,
                sample_set=sample_set,
                sample_index=sample_index,
                volume=volume,
                uninherited=0,
                effects=(effects & ~1) | src_kiai,
            )
            bm.timing_points.append(new_tp)
            existing_times.add(t)

        bm.save()


def _closest_timing_point(time: float, points: List[TimingPoint]) -> Optional[TimingPoint]:
    if not points:
        return None
    best = points[0]
    best_dist = abs(points[0].time - time)
    for p in points:
        d = abs(p.time - time)
        if d < best_dist:
            best_dist = d
            best = p
    return best


# =============================================================================
# Map Cleaner
# =============================================================================
def resnap_all_notes(bm: Beatmap, divisor_key: str, section: Optional[tuple] = None):
    """Resnaps every note to `divisor_key`. `section`, if given, is an
    inclusive (from_ms, to_ms) range restricting which notes get resnapped
    — notes outside it are left untouched."""
    for ho in bm.hit_objects:
        if section is not None:
            lo, hi = section
            if not (lo <= ho.time <= hi):
                continue
        ho.time = snap_time(ho.time, bm.timing_points, divisor_key)


def resnap_important_green_lines(bm: Beatmap, divisor_key: str):
    """Resnaps only 'important' green lines: kiai toggles and those that share
    a timestamp with a red (uninherited) line."""
    red_times = {tp.time for tp in bm.timing_points if tp.uninherited == 1}

    kiai_changing_ids = set()
    last_kiai = None
    for tp in sorted(bm.timing_points, key=lambda t: t.time):
        kiai = tp.effects & 1
        if tp.uninherited == 0 and (last_kiai is None or kiai != last_kiai):
            kiai_changing_ids.add(id(tp))
        last_kiai = kiai

    for tp in bm.timing_points:
        if tp.uninherited == 0:
            is_red_supported = any(abs(rt - tp.time) < 1e-3 for rt in red_times)
            is_kiai_toggle = id(tp) in kiai_changing_ids
            if is_red_supported or is_kiai_toggle:
                tp.time = snap_time(tp.time, bm.timing_points, divisor_key)


def remove_unused_green_lines(bm: Beatmap):
    """A green (inherited) line is removed if either of these holds:

    1. It's a genuine no-op: its volume, scroll velocity, and kiai toggle
       all match whatever is already in effect — volume and kiai from the
       most recent timing point of either color, scroll velocity from the
       most recent green line (or osu!'s implicit 1.0x/-100 default if
       none has occurred yet).

    2. It DOES change something, but that change is inaudible/invisible:
       no note's governing timing point is this line, AND it doesn't
       govern any barline either (taiko always plays a note's
       hitsound/SV/volume, and flashes each barline's SV/kiai, from
       whichever timing point governs that moment — a line neither ever
       reads from can't affect gameplay). This is "governs", not merely
       "coincides with" — a green line at 1500ms with the next state
       change not until 2500ms is still the active line when a barline at
       2000ms fires, even though it doesn't sit exactly on that barline
       itself, so it must be kept. The barline grid comes from each red
       line's own meter: a 3/4 map has a barline every 3 beats, 4/4 every
       4, etc, restarting at each new red line.

    A green line that shares its timestamp with a red (uninherited) line
    is always kept, even if it would otherwise look redundant — matching
    a red line explicitly is treated as intentional.
    """
    points = sorted(bm.timing_points, key=lambda t: t.time)
    if not points:
        return

    red_times = {round(tp.time) for tp in points if tp.uninherited == 1}
    uninherited_sorted = [tp for tp in points if tp.uninherited == 1]

    # Which timing point (red or green, by id) governs at least one note,
    # or at least one barline — used for check #2 above. A barline is
    # "governed" by whichever timing point is active at its timestamp,
    # same rule as a note.
    end_time = max(
        [tp.time for tp in points] + [ho.time for ho in bm.hit_objects],
        default=0,
    )
    barline_times = osu_parser._barline_times(uninherited_sorted, end_time)
    governing_ids_for_notes = set()
    for ho in bm.hit_objects:
        gov = osu_parser._governing_timing_point(ho.time, points)
        if gov is not None:
            governing_ids_for_notes.add(id(gov))
    governing_ids_for_barlines = set()
    for bar_t in barline_times:
        gov = osu_parser._governing_timing_point(bar_t, points)
        if gov is not None:
            governing_ids_for_barlines.add(id(gov))

    keep = []
    current_volume = None   # last volume set by ANY timing point (red or green)
    current_kiai = None     # last kiai state set by ANY timing point (red or green)
    current_sv = -100.0     # last SV set by a GREEN line specifically; osu!'s default otherwise

    for tp in points:
        if tp.uninherited == 1:
            keep.append(tp)
            current_volume = tp.volume
            current_kiai = tp.effects & 1
            continue

        on_red_line = round(tp.time) in red_times

        # beatLength is a float and real maps often carry recurring-decimal
        # SV values (e.g. slider multiplier 1.4 at certain BPMs yields
        # something like -133.33333333333334), which can pick up tiny
        # floating-point noise across saves/reparses — compare with a
        # tolerance rather than exact equality.
        same_volume = current_volume is not None and tp.volume == current_volume
        same_sv = abs(tp.beat_length - current_sv) <= 0.01
        same_kiai = current_kiai is not None and (tp.effects & 1) == current_kiai
        is_noop = same_volume and same_sv and same_kiai

        affects_note = id(tp) in governing_ids_for_notes
        affects_barline = id(tp) in governing_ids_for_barlines
        is_silent_change = not affects_note and not affects_barline

        if on_red_line or not (is_noop or is_silent_change):
            keep.append(tp)

        current_volume = tp.volume
        current_kiai = tp.effects & 1
        current_sv = tp.beat_length

    bm.timing_points = keep


def turn_kat_whistle_to_clap(bm: Beatmap):
    for ho in bm.hit_objects:
        if ho.hit_sound & HS_WHISTLE and not (ho.hit_sound & HS_CLAP):
            ho.hit_sound = (ho.hit_sound & ~HS_WHISTLE) | HS_CLAP


def set_lines_to_normal_sampleset(bm: Beatmap):
    NORMAL = 1
    DEFAULT_CUSTOM = 0
    for tp in bm.timing_points:
        tp.sample_set = NORMAL
        tp.sample_index = DEFAULT_CUSTOM


def resolve_line_conflicts(bm: Beatmap):
    """Where a red (uninherited) line and green (inherited) line share the
    same timestamp, the red line's kiai (effects bit 0) and volume should
    follow the green line's values, since in-game the green line's settings
    take visual/audio precedence at that instant."""
    by_time: dict[float, List[TimingPoint]] = {}
    for tp in bm.timing_points:
        by_time.setdefault(round(tp.time), []).append(tp)

    for t, group in by_time.items():
        reds = [tp for tp in group if tp.uninherited == 1]
        greens = [tp for tp in group if tp.uninherited == 0]
        if reds and greens:
            green = greens[-1]
            for red in reds:
                red.volume = green.volume
                # effects bit 0 = kiai
                kiai = green.effects & 1
                red.effects = (red.effects & ~1) | kiai


DEFAULT_FINISHER_COORDS = {"finisher": (256, 128), "normal": (256, 192)}
DEFAULT_NOTE_TYPE_COORDS = {
    "don": (192, 128), "kat": (192, 256),
    "don_finisher": (320, 128), "kat_finisher": (320, 256),
}


def center_notes_to_playfield(bm: Beatmap, mode: str = "default",
                               finisher_coords: dict = None, note_type_coords: dict = None):
    """Positions every hit object according to `mode`:
      - "default": everything at the playfield center (256, 192) — the
        standard practice for taiko maps, since x/y position is purely
        cosmetic for taiko gameplay.
      - "separate_finishers": two positions (from `finisher_coords`, keys
        "finisher"/"normal") based on whether the Finish hitsound bit is set.
      - "separate_note_types": four positions (from `note_type_coords`,
        keys "don"/"kat"/"don_finisher"/"kat_finisher") based on both the
        Finish bit and whether the note is a "kat" (Whistle or Clap set) or
        a "don" (neither).
    If both separate-finishers and separate-note-types were somehow
    requested together, note-types wins — it's the more specific/granular
    of the two and already accounts for finishers on its own."""
    finisher_coords = finisher_coords or DEFAULT_FINISHER_COORDS
    note_type_coords = note_type_coords or DEFAULT_NOTE_TYPE_COORDS

    for ho in bm.hit_objects:
        is_finisher = bool(ho.hit_sound & HS_FINISH)
        is_kat = bool(ho.hit_sound & (HS_WHISTLE | HS_CLAP))

        if mode == "separate_note_types":
            if is_kat and is_finisher:
                ho.x, ho.y = note_type_coords["kat_finisher"]
            elif is_kat:
                ho.x, ho.y = note_type_coords["kat"]
            elif is_finisher:
                ho.x, ho.y = note_type_coords["don_finisher"]
            else:
                ho.x, ho.y = note_type_coords["don"]
        elif mode == "separate_finishers":
            ho.x, ho.y = finisher_coords["finisher"] if is_finisher else finisher_coords["normal"]
        else:
            ho.x, ho.y = 256, 192


def run_map_cleaner(bm: Beatmap, options: dict):
    """options keys: resnap_notes(bool), snap_divisor(str), resnap_notes_section
    ((from_ms, to_ms) or None), remove_unused_green(bool),
    resnap_important_green(bool), kat_whistle_to_clap(bool), normal_sampleset(bool),
    resolve_conflicts(bool), center_notes(bool), note_position_mode(str,
    one of "default"/"separate_finishers"/"separate_note_types"),
    finisher_coords(dict), note_type_coords(dict), set_base_sv_val(float or None),
    push_green_ms(int or None)"""
    if options.get("remove_unused_green"):
        remove_unused_green_lines(bm)
    if options.get("resnap_important_green"):
        resnap_important_green_lines(bm, options.get("snap_divisor", "1/4"))
    if options.get("resnap_notes"):
        resnap_all_notes(bm, options.get("snap_divisor", "1/4"), options.get("resnap_notes_section"))
    if options.get("kat_whistle_to_clap"):
        turn_kat_whistle_to_clap(bm)
    if options.get("normal_sampleset"):
        set_lines_to_normal_sampleset(bm)
    if options.get("resolve_conflicts"):
        resolve_line_conflicts(bm)
    if options.get("set_base_sv_val") is not None:
        sv_val = options["set_base_sv_val"]
        bm.set_kv("Difficulty", "SliderMultiplier", f"{sv_val:.1f}")
    if options.get("push_green_ms") is not None:
        push_green_lines(bm, options["push_green_ms"])
    if options.get("center_notes"):
        center_notes_to_playfield(
            bm,
            options.get("note_position_mode", "default"),
            options.get("finisher_coords"),
            options.get("note_type_coords"),
        )


def push_green_lines(bm: Beatmap, push_ms: int):
    """Pushes all green lines by `push_ms` milliseconds, excluding kiai toggles
    and red-line-supported green lines."""
    # Red line times (for red-line-supported green lines)
    red_times = {tp.time for tp in bm.timing_points if tp.uninherited == 1}
    
    # Kiai toggle green lines
    kiai_changing_greens = set()
    last_kiai = None
    for tp in sorted(bm.timing_points, key=lambda t: t.time):
        kiai = tp.effects & 1
        if tp.uninherited == 0 and (last_kiai is None or kiai != last_kiai):
            kiai_changing_greens.add(id(tp))
        last_kiai = kiai

    for tp in bm.timing_points:
        if tp.uninherited == 0:
            is_red_supported = any(abs(rt - tp.time) < 1e-3 for rt in red_times)
            is_kiai_toggle = id(tp) in kiai_changing_greens
            if not is_red_supported and not is_kiai_toggle:
                tp.time -= push_ms


# =============================================================================
# Offset Shifter
# =============================================================================
SILENCE_LEAD_IN_MS = 1000


def _ffprobe_probe(path: str) -> dict:
    """Reads codec_name/sample_rate/channels/bitrate_kbps for the first
    audio stream via ffprobe's structured JSON output — preferred over
    `_ffmpeg_probe` whenever ffprobe is resolvable (see
    `audio_tools_fully_available`), since it's a real structured read
    rather than regexing ffmpeg's human-readable log text. Returns {} if
    ffprobe isn't available or the probe fails."""
    if not ffprobe_available():
        return {}
    cmd = [
        _resolve_binary("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate:format=bit_rate",
        "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=30,
                                 creationflags=_SUBPROCESS_FLAGS)
        data = json.loads(result.stdout.decode("utf-8", "ignore"))
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}
    info: dict = {}
    streams = data.get("streams") or []
    if streams:
        s = streams[0]
        if s.get("codec_name"):
            info["codec_name"] = s["codec_name"]
        try:
            info["sample_rate"] = int(s["sample_rate"])
        except (KeyError, TypeError, ValueError):
            pass
        try:
            info["channels"] = int(s["channels"])
        except (KeyError, TypeError, ValueError):
            pass
        try:
            info["bitrate_kbps"] = round(int(s["bit_rate"]) / 1000)
        except (KeyError, TypeError, ValueError):
            pass
    if "bitrate_kbps" not in info:
        try:
            info["bitrate_kbps"] = round(int(data["format"]["bit_rate"]) / 1000)
        except (KeyError, TypeError, ValueError):
            pass
    return info


def _ffmpeg_probe(path: str) -> dict:
    """Reads codec_name/sample_rate/channels/bitrate_kbps for the first
    audio stream by parsing ffmpeg's own `-i` banner (its stderr output) —
    the fallback for when ffprobe isn't resolvable, since ffmpeg.exe alone
    is the one binary this app can always assume is present. Returns {}
    (not raising) if ffmpeg can't be run or nothing could be parsed —
    every caller treats a missing field as "unknown" and reacts
    accordingly rather than failing outright."""
    if not ffmpeg_available():
        return {}
    try:
        result = subprocess.run([_resolve_binary("ffmpeg"), "-hide_banner", "-i", path],
                                 capture_output=True, timeout=30,
                                 creationflags=_SUBPROCESS_FLAGS)
    except (subprocess.SubprocessError, OSError):
        return {}
    text = result.stderr.decode("utf-8", "ignore")
    info: dict = {}
    stream_match = re.search(r"Stream #\d+:\d+(?:\([^)]*\))?(?:\[[^\]]*\])?:\s*Audio:\s*(.*)", text)
    if stream_match:
        rest = stream_match.group(1)
        first_field = rest.split(",", 1)[0].split()
        if first_field:
            info["codec_name"] = first_field[0]
        hz = re.search(r"(\d+)\s*Hz", rest)
        if hz:
            info["sample_rate"] = int(hz.group(1))
        if re.search(r"\bmono\b", rest):
            info["channels"] = 1
        elif re.search(r"\bstereo\b", rest):
            info["channels"] = 2
        kbps = re.search(r"(\d+)\s*kb/s", rest)
        if kbps:
            info["bitrate_kbps"] = int(kbps.group(1))
    if "bitrate_kbps" not in info:
        fmt_match = re.search(r"\bbitrate:\s*(\d+)\s*kb/s", text)
        if fmt_match:
            info["bitrate_kbps"] = int(fmt_match.group(1))
    return info


def _probe_audio(path: str) -> dict:
    """Best available probe of the source's codec/sample_rate/channels/
    bitrate — ffprobe first (structured, more reliable — see
    `_ffprobe_probe`), falling back to parsing ffmpeg's own `-i` banner
    when ffprobe isn't resolvable."""
    info = _ffprobe_probe(path)
    return info if info else _ffmpeg_probe(path)


def _probe_audio_stream_info(path: str) -> Optional[dict]:
    """codec_name/sample_rate/channels for the first audio stream, or None
    if any of those three couldn't be determined."""
    info = _probe_audio(path)
    if "codec_name" in info and "sample_rate" in info and "channels" in info:
        return info
    return None


def get_audio_bitrate_kbps(path: str) -> Optional[int]:
    """The audio bitrate in kbps (see `_probe_audio`), or None if it
    couldn't be determined."""
    return _probe_audio(path).get("bitrate_kbps")


def _replace_locked_safe(tmp_path: str, dest_path: str):
    """`os.replace(tmp_path, dest_path)`, but turns a `PermissionError`
    (Windows: WinError 5 "Access is denied") into a clear, actionable
    message instead of a raw OS error. This specific failure means
    `dest_path` is currently open in another program — almost always osu!
    itself, since simply having a map open or selected in song select
    keeps its audio file locked for preview playback — rather than any
    real problem with the encode/silence-add that already succeeded into
    `tmp_path`. Cleans up `tmp_path` on failure so a failed replace
    doesn't leave a stray `.tmp_*` file behind in the song folder."""
    try:
        os.replace(tmp_path, dest_path)
    except PermissionError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(
            f"Couldn't replace {os.path.basename(dest_path)} — it's open in "
            "another program (most likely osu! itself, if this map is open "
            "or selected in song select, or another media player/editor). "
            "Close whatever has it open and try again."
        ) from e


def _remove_locked_safe(path: str):
    """`os.remove(path)`, with the same PermissionError -> clear-message
    treatment as `_replace_locked_safe` — used where a follow-up delete of
    the *old* file (after a rename to a new filename) can hit the same
    "still open in osu!" lock."""
    try:
        os.remove(path)
    except PermissionError as e:
        raise RuntimeError(
            f"Couldn't remove {os.path.basename(path)} — it's open in "
            "another program (most likely osu! itself, if this map is open "
            "or selected in song select, or another media player/editor). "
            "Close whatever has it open and try again."
        ) from e


def _concat_file_line(path: str) -> str:
    """One `file '...'` line for an ffmpeg concat-demuxer list. Forward
    slashes + an explicit `file:` protocol prefix sidestep ffmpeg's URL
    parser mistaking a Windows drive letter ("C:...") for a scheme."""
    p = path.replace("\\", "/").replace("'", "'\\''")
    return f"file 'file:{p}'\n"


# codec_name (as reported by ffmpeg's own probe) -> extension whose default
# ffmpeg encoder can regenerate that same codec for the silent lead-in clip.
# .mp3 ONLY — confirmed by direct testing (decode the result and diff the
# PCM samples against the original) that ffmpeg's concat demuxer with
# `-c copy` produces a byte-for-byte-identical mp3 elementary stream, since
# raw MP3 frame concatenation is inherently well-defined with no container
# to worry about. The same technique was also tried for .ogg and is NOT
# safe: an Ogg file has its own container framing (serial numbers, page
# sequencing, granule positions), and naively concatenating two
# independently-encoded Ogg files this way produces an invalid *chained*
# bitstream that decodes with continuous "Overread N bits" errors from the
# second segment onward — i.e. it actively corrupts the real song audio,
# not just a hypothetical risk. Ogg falls through to the re-encode
# fallback below instead.
_SILENCE_CODEC_EXT = {"mp3": ".mp3"}


def add_silence_to_audio(folder: str, silence_ms: int = SILENCE_LEAD_IN_MS) -> str:
    """Prepends `silence_ms` of silence to the map's audio file in place
    (same filename, so every diff's existing AudioFilename reference stays
    valid). Returns the audio filename.

    For .mp3 (see `_SILENCE_CODEC_EXT`), generates a separate silent clip
    matching the source's sample rate/channel count and concatenates it in
    front via ffmpeg's concat demuxer with `-c copy` — the actual song
    audio is stream-copied, never decoded or re-encoded, so it's
    bit-for-bit identical to the original. Everything else (.ogg included
    — see `_SILENCE_CODEC_EXT`'s note on why) falls back to the old
    `adelay`-filter re-encode, at least matched to the source's own
    bitrate (via `get_audio_bitrate_kbps`) so the quality hit from that
    unavoidable single re-encode pass is as small as possible."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found. Install it automatically from Settings, or place "
                            "ffmpeg.exe next to this app — it doesn't need to be on PATH.")
    audio_file = get_audio_filename(folder)
    if not audio_file:
        raise RuntimeError("No audio file found for this map.")
    src = os.path.join(folder, audio_file)
    if not os.path.exists(src):
        raise RuntimeError(f"Audio file not found: {audio_file}")

    base, ext = os.path.splitext(audio_file)
    pid = os.getpid()
    info = _probe_audio_stream_info(src)
    layout = None
    if info and info["channels"] in (1, 2):
        layout = "mono" if info["channels"] == 1 else "stereo"
    codec_ext = _SILENCE_CODEC_EXT.get((info or {}).get("codec_name"))

    if info and layout and codec_ext == ext.lower():
        silence_path = os.path.join(folder, f"{base}.tmp_silencegen_{pid}{ext}")
        list_path = os.path.join(folder, f"{base}.tmp_concatlist_{pid}.txt")
        out_path = os.path.join(folder, f"{base}.tmp_silence_{pid}{ext}")
        ffmpeg_bin = _resolve_binary("ffmpeg")
        try:
            gen_cmd = [
                ffmpeg_bin, "-y", "-f", "lavfi",
                "-i", f"anullsrc=channel_layout={layout}:sample_rate={info['sample_rate']}",
                "-t", str(silence_ms / 1000.0), silence_path,
            ]
            _run_ffmpeg(gen_cmd)
            with open(list_path, "w", encoding="utf-8") as f:
                f.write(_concat_file_line(silence_path))
                f.write(_concat_file_line(src))
            concat_cmd = [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                          "-i", list_path, "-c", "copy", out_path]
            _run_ffmpeg(concat_cmd)
            _replace_locked_safe(out_path, src)
        finally:
            for p in (silence_path, list_path, out_path):
                if os.path.exists(p):
                    os.remove(p)
        return audio_file

    # Fallback: re-encode via adelay, at least matching the source's own
    # bitrate so it's not a bigger quality hit than necessary.
    tmp_path = os.path.join(folder, f"{base}.tmp_silence_{pid}{ext}")
    cmd = [_resolve_binary("ffmpeg"), "-y", "-i", src, "-af", f"adelay={silence_ms}:all=1"]
    src_kbps = get_audio_bitrate_kbps(src)
    if src_kbps:
        cmd += ["-b:a", f"{src_kbps}k"]
    cmd.append(tmp_path)
    _run_ffmpeg(cmd)
    _replace_locked_safe(tmp_path, src)
    return audio_file


AUDIO_REENCODE_BITRATES = (208, 192, 160, 128)


def _reencode_target_ext(source_ext: str, bitrate_kbps: int) -> str:
    """The output extension for a re-encode: a `.mp3` source only switches
    to `.ogg` for 208kbps (not a real mp3 bitrate — mp3 tops out well
    below that in practice) and otherwise stays `.mp3`; every other source
    format (ogg included — already the right extension — plus wav/flac/
    m4a/whatever else `get_audio_bitrate_kbps` can read) always becomes
    `.ogg` regardless of bitrate, matching what a normal .osu map audio
    track is expected to be."""
    return ".ogg" if (source_ext.lower() != ".mp3" or bitrate_kbps == 208) else ".mp3"


def _reencode_audio_file(src: str, bitrate_kbps: int) -> str:
    """Runs the actual ffmpeg re-encode of `src` to `bitrate_kbps`, writing
    the result to a temp file in the same directory and returning that
    temp path — the caller decides where the final file actually lands
    (in place over the source, or alongside it under a different name;
    see `apply_audio_reencode_to_map`/`apply_audio_reencode_external`)."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found. Install it automatically from Settings, or place "
                            "ffmpeg.exe next to this app — it doesn't need to be on PATH.")
    if not os.path.exists(src):
        raise RuntimeError(f"Audio file not found: {src}")
    folder = os.path.dirname(src)
    base, ext = os.path.splitext(os.path.basename(src))
    target_ext = _reencode_target_ext(ext, bitrate_kbps)
    tmp_path = os.path.join(folder, f"{base}.tmp_reencode_{os.getpid()}{target_ext}")
    cmd = [_resolve_binary("ffmpeg"), "-y", "-i", src, "-b:a", f"{bitrate_kbps}k", tmp_path]
    _run_ffmpeg(cmd)
    return tmp_path


def apply_audio_reencode_to_map(folder: str, bitrate_kbps: int) -> str:
    """Re-encodes the current map's own audio file in place — same
    filename unless the extension changes (see `_reencode_target_ext`), in
    which case the old file is removed and AudioFilename is updated across
    every diff in the set (they all share the one audio file). Returns the
    resulting filename."""
    audio_file = get_audio_filename(folder)
    if not audio_file:
        raise RuntimeError("No audio file found for this map.")
    src = os.path.join(folder, audio_file)

    base, ext = os.path.splitext(audio_file)
    target_ext = _reencode_target_ext(ext, bitrate_kbps)
    tmp_path = _reencode_audio_file(src, bitrate_kbps)

    if target_ext != ext:
        new_filename = base + target_ext
        _replace_locked_safe(tmp_path, os.path.join(folder, new_filename))
        _remove_locked_safe(src)
        for fname in osu_parser.list_difficulty_files(folder):
            path = os.path.join(folder, fname)
            bm = Beatmap(path)
            bm.set_kv("General", "AudioFilename", new_filename)
            bm.save()
        return new_filename
    _replace_locked_safe(tmp_path, src)
    return audio_file


def apply_audio_reencode_external(src: str, bitrate_kbps: int) -> str:
    """Re-encodes an arbitrary external audio file `src` (not part of any
    map) and writes the result *alongside* it — same folder, named
    "<original base>_<bitrate>kbps<ext>" so the original file is never
    touched or overwritten. Returns the full path to the exported file."""
    base, ext = os.path.splitext(os.path.basename(src))
    target_ext = _reencode_target_ext(ext, bitrate_kbps)
    tmp_path = _reencode_audio_file(src, bitrate_kbps)
    out_path = os.path.join(os.path.dirname(src), f"{base}_{bitrate_kbps}kbps{target_ext}")
    _replace_locked_safe(tmp_path, out_path)
    return out_path


def apply_offset(folder: str, diff_files: List[str], delta_ms: float, add_silence: bool = False):
    """Shifts every selected diff's timing points, hit objects, break
    times, video event, and preview point by `delta_ms`. If `add_silence`
    is set, `SILENCE_LEAD_IN_MS` of silence is first added to the audio
    (once, regardless of how many diffs are selected — they all share the
    same audio file), and that same amount is folded into the shift so
    everything stays in sync with the now-longer audio."""
    if add_silence:
        add_silence_to_audio(folder)
        delta_ms += SILENCE_LEAD_IN_MS
    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        bm.shift_all_times(delta_ms)
        bm.save()


# =============================================================================
# BG Offset Shifter
#
# Approach validated against a real community tool (osutaiko-mapping-helper,
# BGHelper.cs / BGSetterForm.cs): the offset is stored as literal x,y
# numbers in the background event line — "0,0,\"bg.jpg\",x,y" — which is an
# official part of the .osu format the game itself reads to position the
# background. The image file is never modified for the offset itself (only
# optionally re-saved when converting .png -> .jpg). All positioning math
# happens in osu!'s own canonical 854x480 pixel space so the stored value
# means the same thing this tool, the game, and other tools all agree on.
# y=0 means the background sits centered within the visible band beneath
# the playfield bar — evenly offscreen on both sides when the image is
# taller than the band — not anchored to either edge of it.
# =============================================================================
def list_song_folder_images(folder: str) -> List[str]:
    if not folder or not os.path.isdir(folder):
        return []
    return sorted([f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))])


def _unique_dest_filename(folder: str, filename: str) -> str:
    """Appends " (2)", " (3)", ... before the extension until `filename`
    doesn't already exist in `folder` — used so importing an externally
    browsed file never silently clobbers an unrelated same-named file
    already sitting in the map folder."""
    base, ext = os.path.splitext(filename)
    candidate = filename
    n = 2
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{base} ({n}){ext}"
        n += 1
    return candidate


def import_external_bg_image(folder: str, src_path: str) -> str:
    """Copies a background image picked from outside the map folder (BG
    Settings' Browse button) into the map folder, so the rest of BG
    Settings (offset math, preview, apply) can treat it exactly like any
    other image already sitting there. Returns the final filename inside
    `folder` — same as `src_path`'s own basename unless a different file
    with that name was already present, in which case the copy is
    renamed rather than overwriting it."""
    filename = os.path.basename(src_path)
    dest = os.path.join(folder, filename)
    if os.path.exists(dest) and not os.path.samefile(src_path, dest):
        filename = _unique_dest_filename(folder, filename)
        dest = os.path.join(folder, filename)
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return filename


OSU_W, OSU_H = 854, 480          # osu!'s canonical widescreen pixel space
BAND_H = 255                      # visible background band height within it
PLAYFIELD_H = OSU_H - BAND_H      # taiko playfield/HUD bar height (the rest)
TAIKO_LANE_FRAC = PLAYFIELD_H / OSU_H

PREVIEW_CANVAS_W = 960
PREVIEW_CANVAS_H = 540

BG_PREVIEW_DIM = 0.7  # cosmetic darken factor for the preview only


def _process_bg_native(img):
    """Reproduces BGHelper.SetBgOnForm: if the image is wider than 16:9,
    crop it to 16:9 (centered) then scale to exactly OSU_H tall; otherwise
    (image is 16:9 or taller) don't crop — scale to OSU_W wide, which may
    leave it taller than OSU_H (that extra height is what you pan through)."""
    iw, ih = img.size
    ratio = ih / iw if iw else 1
    if ratio < (9 / 16) - 1e-6:
        crop_w = ih * (16 / 9)
        diff_w = (iw - crop_w) / 2
        cropped = img.crop((round(diff_w), 0, round(diff_w + crop_w), ih))
        scale = OSU_H / ih if ih else 1
        new_w = max(1, round(cropped.width * scale))
        return cropped.resize((new_w, OSU_H))
    else:
        scale = OSU_W / iw if iw else 1
        new_h = max(1, round(ih * scale))
        return img.resize((OSU_W, new_h))


def get_offset_bounds(image_path: str) -> "tuple[int, int]":
    """The valid range for the y offset — symmetric around 0, since 0 means
    perfectly centered within the visible band (equal amount hidden above,
    under the playfield bar, and below, off the bottom edge)."""
    from PIL import Image

    with Image.open(image_path) as img:
        iw, ih = img.size
    ratio = ih / iw if iw else 1
    if ratio < (9 / 16) - 1e-6:
        processed_h = OSU_H
    else:
        scale = OSU_W / iw if iw else 1
        processed_h = max(1, round(ih * scale))
    band_h = BAND_H
    extra = max(0, processed_h - band_h)
    return -int(round(extra / 2)), int(round(extra / 2))


def _crop_to_band(processed_img, offset_y: int, band_w: int, band_h: int):
    """Crops `processed_img` (already scaled to the right width) down to a
    `band_h`-tall slice — the part of the image that's actually visible
    beneath the playfield bar. At offset_y=0 this slice is exactly centered
    on the image, so if the image is taller than the band, equal amounts
    are hidden above (under the bar) and below (off the bottom edge) —
    "offscreen evenly", not anchored to either edge."""
    from PIL import Image

    ph = processed_img.height
    extra = max(0, ph - band_h)

    render_offset = max(-extra / 2, min(extra / 2, offset_y))

    center = ph / 2
    crop_top = round(center - band_h / 2 - render_offset)
    crop_top = max(0, min(crop_top, max(0, ph - band_h)))
    cropped = processed_img.crop((0, crop_top, band_w, crop_top + band_h))

    if cropped.height < band_h:
        padded = Image.new("RGB", (band_w, band_h), (0, 0, 0))
        padded.paste(cropped, (0, (band_h - cropped.height) // 2))
        cropped = padded
    return cropped, render_offset


def _compose_native_frame(image_path: str, offset_y: int):
    """Builds the OSU_W x OSU_H composite: the visible band (beneath the
    playfield bar) shows the background centered on `offset_y`, with the
    opaque playfield bar drawn over the top PLAYFIELD_H rows."""
    from PIL import Image, ImageDraw

    img = Image.open(image_path).convert("RGB")
    processed = _process_bg_native(img)
    dimmed = Image.eval(processed, lambda p: int(p * BG_PREVIEW_DIM))

    band_h = BAND_H
    band, offset_y = _crop_to_band(dimmed, offset_y, OSU_W, band_h)

    canvas = Image.new("RGB", (OSU_W, OSU_H), (0, 0, 0))
    canvas.paste(band, (0, PLAYFIELD_H))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, OSU_W - 1, PLAYFIELD_H - 1], fill=(0, 0, 0))
    return canvas, offset_y


def render_bg_preview(image_path: str, offset_y: int,
                       canvas_w=PREVIEW_CANVAS_W, canvas_h=PREVIEW_CANVAS_H):
    """Renders the live preview at `canvas_w`x`canvas_h` (just a crisper
    upscale of the OSU_W x OSU_H canonical composite — all the actual
    positioning math happens in osu!'s own pixel space, matching exactly
    what the game and other tools will show)."""
    canvas, _ = _compose_native_frame(image_path, offset_y)
    return canvas.resize((canvas_w, canvas_h))


def apply_bg_offset(folder: str, diff_files: List[str], bg_file: str, new_offset: int,
                     convert_to_jpg: bool):
    """Writes the y offset directly into each selected diff's background
    event line (x stays 0) — no image pixels are touched for the offset
    itself. Optionally converts .png -> .jpg (a plain re-save, no crop)."""
    src_path = os.path.join(folder, bg_file)

    final_name = bg_file
    if convert_to_jpg and bg_file.lower().endswith(".png"):
        from PIL import Image
        img = Image.open(src_path).convert("RGB")
        final_name = os.path.splitext(bg_file)[0] + ".jpg"
        img.save(os.path.join(folder, final_name), "JPEG", quality=95)
        if os.path.abspath(os.path.join(folder, final_name)) != os.path.abspath(src_path):
            try:
                os.remove(src_path)
            except OSError:
                pass

    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        bm.set_background_filename(final_name)
        bm.set_background_offset(0, int(round(new_offset)))
        bm.save()

    return final_name


# =============================================================================
# Video Offset Shifter
# =============================================================================
def list_song_folder_videos(folder: str) -> List[str]:
    if not folder or not os.path.isdir(folder):
        return []
    return sorted([f for f in os.listdir(folder) if f.lower().endswith((".mp4", ".m4v", ".avi", ".mov", ".flv", ".wmv", ".webm"))])


def import_external_video_file(folder: str, src_path: str) -> str:
    """Same as import_external_bg_image but for Video Settings' Browse
    button — copies an externally picked video into the map folder (with
    the same collision-safe rename via _unique_dest_filename) so it can be
    treated like any other video already sitting there."""
    filename = os.path.basename(src_path)
    dest = os.path.join(folder, filename)
    if os.path.exists(dest) and not os.path.samefile(src_path, dest):
        filename = _unique_dest_filename(folder, filename)
        dest = os.path.join(folder, filename)
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return filename


def resize_taiko_video(folder: str, video_file: str, blur: bool) -> str:
    """Encode to 1280x720 .avi, no audio. Content resized to height 339px,
    bottom-center, top 1280x381 always pitch black. If blur is enabled, only
    the left/right margins beside the video within that bottom 339px band
    show a blurred, stretched copy of the video — the top 381px stays plain
    black either way.

    The ideal split (339.5px video / 380.5px black bar, out of 720) isn't
    achievable here since ffmpeg's scale filter only accepts integer
    dimensions — rounded down to 339/381.

    Deletes the original full-size video once the resize succeeds — the
    resized file (a different filename, always ending `_taiko.avi`) is
    the one that actually ends up referenced by the beatmap afterward, so
    leaving the original behind just wastes disk space."""
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found. Install it automatically from Settings, or place "
                            "ffmpeg.exe next to this app — it doesn't need to be on PATH.")

    src = os.path.join(folder, video_file)
    out_name = os.path.splitext(video_file)[0] + "_taiko.avi"
    out_path = os.path.join(folder, out_name)

    if blur:
        filter_complex = (
            "color=c=black:s=1280x720[base];"
            "[0:v]scale=1280:339,boxblur=20:2[bgband];"
            "[0:v]scale=-1:339[fg];"
            "[bgband][fg]overlay=(W-w)/2:0[bottom];"
            "[base][bottom]overlay=0:381:shortest=1"
        )
    else:
        filter_complex = (
            "color=c=black:s=1280x720[bg];"
            "[0:v]scale=-1:339[fg];"
            "[bg][fg]overlay=(W-w)/2:H-h:shortest=1"
        )

    cmd = [
        _resolve_binary("ffmpeg"), "-y", "-i", src,
        "-filter_complex", filter_complex,
        "-shortest",
        "-an", "-c:v", "mjpeg", "-q:v", "3",
        out_path,
    ]
    _run_ffmpeg(cmd)

    if os.path.abspath(out_path) != os.path.abspath(src):
        try:
            os.remove(src)
        except OSError:
            pass

    return out_name


def apply_video_offset(folder: str, diff_files: List[str], video_file: Optional[str], new_offset_ms: float):
    """Sets the video's start time directly to `new_offset_ms`, replacing
    whatever offset was already there — entering a new value and applying
    it again should land exactly on that value, not add on top of the
    previous one. Also strips any 'Taiko Video SB Code' block a previous
    apply_video_sb_code call left behind, since this is the plain path
    (SB Code checkbox off) and a stale block shouldn't linger."""
    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        if video_file:
            bm.set_video_filename(video_file)
        bm.set_video_time(new_offset_ms)
        bm.clear_video_sb_commands()
        bm.save()


# =============================================================================
# Taiko Video SB Code — a storyboard-based alternative to the Taiko Video
# Resizer, commonly used in hybrid mapsets: instead of re-encoding the video,
# writes S(cale)/F(ade)/MY (move-Y) storyboard commands directly under the
# Video event so the video shrinks and shifts into the visible band beneath
# the taiko playfield bar live. No preview UI — Apply computes and writes the
# block directly; the vertical position is a fixed constant, and the scale is
# derived straight from the video's own real pixel height.
# =============================================================================
VIDEO_SB_SCALE_DEFAULT = 0.305     # fallback when the video's real height can't be probed at all
VIDEO_SB_Y_POSITION = 125          # fixed MY command value


def _ffprobe_probe_video_height(path: str) -> Optional[int]:
    """The first video stream's pixel height via ffprobe's structured JSON
    output — used to compute the Taiko Video SB Code's Scale value (see
    compute_video_sb_scale). None if ffprobe isn't available or the probe
    fails."""
    if not ffprobe_available():
        return None
    cmd = [
        _resolve_binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=height", "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=30,
                                 creationflags=_SUBPROCESS_FLAGS)
        data = json.loads(result.stdout.decode("utf-8", "ignore"))
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    streams = data.get("streams") or []
    if streams:
        try:
            return int(streams[0]["height"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _ffmpeg_probe_video_height(path: str) -> Optional[int]:
    """Fallback for when ffprobe isn't resolvable — parses ffmpeg's own
    `-i` banner (stderr) for the first Video stream's WxH, same spirit as
    _ffmpeg_probe's audio-banner parsing."""
    if not ffmpeg_available():
        return None
    try:
        result = subprocess.run([_resolve_binary("ffmpeg"), "-hide_banner", "-i", path],
                                 capture_output=True, timeout=30,
                                 creationflags=_SUBPROCESS_FLAGS)
    except (subprocess.SubprocessError, OSError):
        return None
    text = result.stderr.decode("utf-8", "ignore")
    stream_match = re.search(r"Stream #\d+:\d+(?:\([^)]*\))?(?:\[[^\]]*\])?:\s*Video:\s*(.*)", text)
    if not stream_match:
        return None
    dims = re.search(r"(\d+)x(\d+)", stream_match.group(1))
    if not dims:
        return None
    return int(dims.group(2))


def probe_video_height(path: str) -> Optional[int]:
    """Best available probe of a video file's native pixel height —
    ffprobe first, falling back to parsing ffmpeg's own `-i` banner when
    ffprobe isn't resolvable. None if neither is resolvable, the file
    doesn't exist, or the probe otherwise fails — callers fall back to
    VIDEO_SB_SCALE_DEFAULT in that case (see compute_video_sb_scale)."""
    if not path or not os.path.isfile(path):
        return None
    return _ffprobe_probe_video_height(path) or _ffmpeg_probe_video_height(path)


def compute_video_sb_scale(video_height: Optional[int]) -> float:
    """The Taiko Video SB Code's Scale value for a video of this native
    pixel height: (440 / height) / 2. Falls back to
    VIDEO_SB_SCALE_DEFAULT if the height is unknown (probe failed)."""
    if not video_height:
        return VIDEO_SB_SCALE_DEFAULT
    return round((440.0 / video_height) / 2, 3)


def _hit_object_end_time_ms(ho: HitObject, bm: Beatmap) -> float:
    """A hit object's own end point — its tail for a slider/spinner/hold,
    or just its start time for a plain note. Mirrors the slider-length
    math insert_pattern_into_map uses, solved forward here instead of
    backward: duration = slides * length / (SliderMultiplier * 100 * SV)
    * beatLength."""
    if _has_absolute_end_time(ho.obj_type):
        try:
            end_time, _ = _split_absolute_end_time(ho.remainder)
            return end_time
        except ValueError:
            return ho.time
    if ho.obj_type & 2:  # slider
        parts = ho.remainder.split(",")
        try:
            slides = int(float(parts[1])) if len(parts) > 1 else 1
            length = float(parts[2]) if len(parts) > 2 else 0.0
        except (ValueError, IndexError):
            return ho.time
        sv_beat_length = _effective_beat_length_at(ho.time, bm.timing_points)
        sv = -100.0 / sv_beat_length if sv_beat_length else 1.0
        try:
            slider_multiplier = float(bm.get_kv("Difficulty", "SliderMultiplier") or "1.4")
        except ValueError:
            slider_multiplier = 1.4
        pixels_per_beat = slider_multiplier * 100 * sv
        beat_length_here = _governing_beat_length(ho.time, bm.timing_points)
        duration_ms = (slides * length * beat_length_here / pixels_per_beat) if pixels_per_beat else 0.0
        return ho.time + duration_ms
    return ho.time


def get_map_end_time_ms(bm: Beatmap) -> Optional[float]:
    """The map's final note timestamp — a slider/spinner/hold's tail, not
    just its head. Used as the Taiko Video SB Code's fade-out time (see
    apply_video_sb_code). None if the map has no hit objects at all."""
    if not bm.hit_objects:
        return None
    return max(_hit_object_end_time_ms(ho, bm) for ho in bm.hit_objects)


def apply_video_sb_code(folder: str, diff_files: List[str], video_file: Optional[str],
                         start_time: float):
    """Writes the 'Taiko Video SB Code' block (see
    Beatmap.set_video_sb_commands) into each selected diff — filename and
    offset are set the same way apply_video_offset does, plus the S/F/F/MY
    block right under the Video line. endTime (the second F command's
    timestamp) is each diff's own last hit object's end time, computed
    independently per diff since different difficulties can end at
    different times. videoScale is computed once from the actual selected
    video file's real pixel height (compute_video_sb_scale); the vertical
    position is always VIDEO_SB_Y_POSITION."""
    scale = compute_video_sb_scale(probe_video_height(os.path.join(folder, video_file)) if video_file else None)
    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        if video_file:
            bm.set_video_filename(video_file)
        bm.set_video_time(start_time)
        end_time = get_map_end_time_ms(bm)
        if end_time is None:
            end_time = start_time
        bm.set_video_sb_commands(start_time, end_time, scale, VIDEO_SB_Y_POSITION)
        bm.save()


def get_audio_filename(folder: str) -> Optional[str]:
    """Reads AudioFilename from whichever .osu is in the folder (all diffs
    in a set normally share the same audio track)."""
    diffs = osu_parser.list_difficulty_files(folder)
    if not diffs:
        return None
    bm = Beatmap(os.path.join(folder, diffs[0]))
    return bm.get_kv("General", "AudioFilename")


# =============================================================================
# Song Search (searches the whole Songs folder for quick-select)
# =============================================================================
def build_song_index(songs_folder: str) -> List[dict]:
    """Scans every beatmapset folder under `songs_folder`, reading just
    enough of one .osu file per set to support searching (artist, title,
    mapper, tags, background). Can take a few seconds on a large library —
    callers should run this off the main thread."""
    index = []
    if not songs_folder or not os.path.isdir(songs_folder):
        return index

    try:
        entries = sorted(os.listdir(songs_folder))
    except OSError:
        return index

    for name in entries:
        folder_path = os.path.join(songs_folder, name)
        if not os.path.isdir(folder_path):
            continue
        diffs = osu_parser.list_difficulty_files(folder_path)
        if not diffs:
            continue
        meta = osu_parser.read_basic_metadata(os.path.join(folder_path, diffs[0]))
        if not meta:
            continue
        bg_path = None
        if meta.get("BackgroundFile"):
            candidate = os.path.join(folder_path, meta["BackgroundFile"])
            if os.path.exists(candidate):
                bg_path = candidate
        index.append({
            "folder": folder_path,
            "folder_name": name,
            "artist": meta["Artist"],
            "title": meta["Title"],
            "mapper": meta["Mapper"],
            "tags": meta["Tags"],
            "bg_path": bg_path,
        })
    return index


def search_song_index(index: List[dict], query: str, limit: int = 30) -> List[dict]:
    """Case-insensitive substring search across artist/title/mapper/tags/
    folder name. Matches where the query starts the artist or title are
    ranked first, then any other substring match, both alphabetically."""
    q = query.strip().lower()
    if not q:
        return []

    starts, contains = [], []
    for entry in index:
        haystack_start = f"{entry['artist']} {entry['title']}".lower()
        haystack_all = " ".join([
            entry["artist"], entry["title"], entry["mapper"],
            entry["tags"], entry["folder_name"],
        ]).lower()
        if haystack_start.startswith(q) or entry["artist"].lower().startswith(q) \
                or entry["title"].lower().startswith(q):
            starts.append(entry)
        elif q in haystack_all:
            contains.append(entry)

    results = starts + contains
    return results[:limit]


# =============================================================================
# File Name Checker
# =============================================================================
FILENAME_RE = re.compile(r"^(?P<artist>.+?) - (?P<title>.+?) \((?P<mapper>.+?)\) \[(?P<diff>.+?)\]\.osu$")

# Characters Windows will not allow in a file name at all. Since the file on
# disk can never contain these regardless of what the metadata says, we
# strip them before comparing so the checker only reports genuine
# capitalisation mismatches, not "missing" characters Windows itself removed.
WINDOWS_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize_for_filename(text: str) -> str:
    return WINDOWS_ILLEGAL_CHARS_RE.sub("", text)


def check_filenames(folder: str, diff_files: List[str]) -> List[dict]:
    """Compares each diff's on-disk filename capitalisation against the
    map's own metadata fields. Returns a list of dicts with any mismatches.
    Characters Windows doesn't allow in file names (e.g. '?', '*', ':') are
    excluded from the comparison — only capitalisation is checked."""
    results = []
    for fname in diff_files:
        bm = Beatmap(os.path.join(folder, fname))
        meta = bm.get_metadata()
        artist = _sanitize_for_filename(meta["RomanisedArtist"] or meta["Artist"])
        title = _sanitize_for_filename(meta["RomanisedTitle"] or meta["Title"])
        mapper = _sanitize_for_filename(meta["Mapper"])
        version = _sanitize_for_filename(meta["Version"])
        expected = f"{artist} - {title} ({mapper}) [{version}].osu"

        m = FILENAME_RE.match(fname)
        issues = []
        if fname != expected:
            if not m:
                issues.append("Filename does not match the expected pattern.")
            else:
                if m.group("artist") != artist:
                    issues.append(f'Artist case mismatch: file has "{m.group("artist")}", map has "{artist}"')
                if m.group("title") != title:
                    issues.append(f'Title case mismatch: file has "{m.group("title")}", map has "{title}"')
                if m.group("mapper") != mapper:
                    issues.append(f'Mapper case mismatch: file has "{m.group("mapper")}", map has "{mapper}"')
                if m.group("diff") != version:
                    issues.append(f'Difficulty name case mismatch: file has "{m.group("diff")}", map has "{version}"')

        results.append({
            "file": fname,
            "expected": expected,
            "ok": len(issues) == 0,
            "issues": issues,
        })
    return results


def safe_rename(src: str, dst: str):
    """os.rename, but safe for a rename that only changes capitalisation.
    On Windows (and any case-insensitive filesystem), renaming a file to a
    name that differs only by case fails directly — the OS considers src
    and dst the same file already existing — so this renames through a
    temporary intermediate name first when that's the situation."""
    if os.path.abspath(src) == os.path.abspath(dst):
        return  # nothing to do
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        # Case-only difference: two-step rename via a temp name.
        tmp = dst + f".tmp_rename_{os.getpid()}"
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        os.rename(src, dst)


def apply_filename_fixes(folder: str, diff_files: List[str]) -> int:
    """Renames every mismatched file in `diff_files` to match its own
    metadata's capitalisation. Returns how many files were actually
    renamed."""
    results = check_filenames(folder, diff_files)
    renamed = 0
    for r in results:
        if r["ok"]:
            continue
        src = os.path.join(folder, r["file"])
        dst = os.path.join(folder, r["expected"])
        if os.path.exists(dst) and os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
            # A different file already occupies the target name — skip to
            # avoid clobbering it.
            continue
        try:
            safe_rename(src, dst)
            renamed += 1
        except OSError:
            continue
    return renamed


# =============================================================================
# Early Volume Setting
# =============================================================================
VOLUME_THRESHOLD_CHOICES = ["10%", "15%", "20%", "25%", "30%", "35%", "40%"]
EARLY_THRESHOLD_CHOICES = ["10ms", "15ms", "20ms", "25ms", "30ms", "35ms", "40ms"]

_TIMESTAMP_RE = re.compile(r"(\d+):([0-5]\d)[.:](\d{1,3})")
_MS_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_time_input(text: str):
    """Parses a time field that accepts either an osu! timestamp
    (mm:ss:mmm — the format osu! stable's own editor puts on the clipboard
    when you press Ctrl+C there, with or without a note selected; a
    mm:ss.mmm with a period is also tolerated for pasting) or a plain
    millisecond number, tolerating surrounding junk characters (matched
    via search, not a full match — so pasting osu!'s full
    "01:25:302 (345,346) - " still extracts just the timestamp). Returns
    (ms, cleaned_text) — cleaned_text is always normalized to the
    mm:ss:mmm form regardless of which separator was matched — or None if
    no valid number/timestamp is found anywhere in the text."""
    m = _TIMESTAMP_RE.search(text)
    if m:
        minutes, seconds = int(m.group(1)), int(m.group(2))
        frac = m.group(3).ljust(3, "0")[:3]
        cleaned = f"{m.group(1)}:{m.group(2)}:{frac}"
        return minutes * 60000 + seconds * 1000 + int(frac), cleaned
    m2 = _MS_NUMBER_RE.search(text)
    if m2:
        return float(m2.group(1)), m2.group(0)
    return None


def _kiai_state_before(points: List[TimingPoint], time: float) -> int:
    """The kiai bit in effect strictly before `time` — naturally skips any
    timing point (including a red line) sitting exactly at `time`, since
    those are excluded by the strict `<`."""
    candidates = [tp for tp in points if tp.time < time - 1e-3]
    if not candidates:
        return 0
    return max(candidates, key=lambda t: t.time).effects & 1


def run_early_volume_setting(bm: Beatmap, volume_threshold_pct: float, early_threshold_ms: float,
                              section: Optional[tuple] = None):
    """For every green line whose volume changes by at least
    `volume_threshold_pct` (percentage points) from whatever volume was in
    effect immediately before it, checks whether any note sits in the
    window (line_time - early_threshold_ms, line_time + 5] — i.e. the line
    is within `early_threshold_ms` behind a note, up to 5ms ahead of it.
    If so:
      - normally, the line is moved back to note_time - early_threshold_ms.
      - if the line shares a timestamp with a red line, or is itself a
        kiai toggle, its own timestamp is load-bearing and can't move.
        If one or more other, freely-movable green lines already sit
        within early_threshold_ms just before it, one of those existing
        lines is reused instead of inserting a new one: it takes on the
        pinned line's volume and is pulled back to
        note_time - early_threshold_ms, and any other such lines in that
        same window are deleted outright (they'd otherwise sit
        redundantly between the pushed-early volume and the pinned line's
        own timestamp). Otherwise, a new green line is inserted at
        line_time - early_threshold_ms, inheriting SV/volume from the
        original (pinned) line and kiai from whatever was in effect just
        before it.
    Lines with no note in range, or a change under the threshold, are left
    untouched. `section`, if given, is an inclusive (from_ms, to_ms) range
    restricting which notes count as trigger points. A computed target
    time is never allowed before the map's very first red line — no green
    line may precede it — so it's clamped to that red line's own time
    instead in that case.

    Finally, every new/moved early-volume line acts as an anchor for a
    cleanup sweep: any other green line sitting anywhere from that anchor's
    own timestamp (inclusive) up to early_threshold_ms after it, whose
    volume/kiai/SV/sampleset are all identical to the anchor's, is deleted
    as unused — it no longer changes anything the anchor hasn't already
    established. A real kiai-toggle line is never deleted by this sweep.

    Before any of the above, a red line and green line that share the same
    timestamp but disagree on volume are unified (red follows green) —
    otherwise which one actually governs playback volume at that instant is
    ambiguous, which would skew the "volume change vs. previous" detection
    below."""
    ordered = sorted(bm.timing_points, key=lambda t: t.time)

    for green in ordered:
        if green.uninherited != 0:
            continue
        for red in ordered:
            if red.uninherited == 1 and abs(red.time - green.time) < 1e-3 and red.volume != green.volume:
                red.volume = green.volume

    red_times = {tp.time for tp in ordered if tp.uninherited == 1}
    first_red_time = min(red_times) if red_times else None

    kiai_changing_ids = set()
    last_kiai = None
    for tp in ordered:
        kiai = tp.effects & 1
        if tp.uninherited == 0 and (last_kiai is None or kiai != last_kiai):
            kiai_changing_ids.add(id(tp))
        last_kiai = kiai

    def _is_pinned(t: TimingPoint) -> bool:
        return any(abs(rt - t.time) < 1e-3 for rt in red_times) or id(t) in kiai_changing_ids

    # Volume "change vs. previous" is evaluated per same-timestamp group,
    # not per individual timing point -- a red line and green line sharing
    # a timestamp (see the unify step above) represent a single moment of
    # change together, so a green line there must be compared against
    # whatever governed strictly *before* that shared timestamp, not
    # against its own red-line sibling at the same instant (which would
    # mask a real jump, e.g. 78 -> 48 read as 48 -> 48).
    candidates = []
    prev_volume = 100
    i = 0
    while i < len(ordered):
        t = ordered[i].time
        j = i
        group = []
        while j < len(ordered) and abs(ordered[j].time - t) < 1e-3:
            group.append(ordered[j])
            j += 1
        for tp in group:
            if tp.uninherited == 0 and abs(tp.volume - prev_volume) >= volume_threshold_pct:
                candidates.append(tp)
        prev_volume = group[-1].volume
        i = j

    note_times = sorted(ho.time for ho in bm.hit_objects)
    if section is not None:
        lo, hi = section
        note_times = [t for t in note_times if lo <= t <= hi]
    if not note_times:
        return

    new_lines = []
    consumed_ids = set()
    delete_ids = set()
    anchors = []
    for tp in candidates:
        matches = [nt for nt in note_times if nt - early_threshold_ms < tp.time <= nt + 5]
        if not matches:
            continue
        note_time = min(matches, key=lambda nt: abs(nt - tp.time))
        target_time = note_time - early_threshold_ms
        if first_red_time is not None:
            # No green line may sit before the very first red line.
            target_time = max(target_time, first_red_time)

        if _is_pinned(tp):
            # Prefer folding into an existing, freely-movable green line
            # already sitting within the early threshold just before this
            # one, rather than stacking a brand new line right next to it.
            # If two or more such lines sit in that window, they're all
            # about to be superseded by the same pushed-early volume
            # anyway -- keep just one (moved back, carrying the volume)
            # and delete the rest instead of leaving redundant lines
            # behind.
            in_window = [
                other for other in ordered
                if other is not tp and other.uninherited == 0
                and id(other) not in consumed_ids and not _is_pinned(other)
                and tp.time - early_threshold_ms - 1e-3 <= other.time < tp.time - 1e-3
            ]

            if in_window:
                keep, extras = in_window[0], in_window[1:]
                keep.volume = tp.volume
                keep.time = target_time
                consumed_ids.add(id(keep))
                anchors.append(keep)
                for extra in extras:
                    consumed_ids.add(id(extra))
                    delete_ids.add(id(extra))
            else:
                kiai = _kiai_state_before(ordered, tp.time)
                new_tp = TimingPoint(
                    time=target_time,
                    beat_length=tp.beat_length,
                    meter=4,
                    sample_set=tp.sample_set,
                    sample_index=tp.sample_index,
                    volume=tp.volume,
                    uninherited=0,
                    effects=(tp.effects & ~1) | kiai,
                )
                new_lines.append(new_tp)
                anchors.append(new_tp)
        else:
            tp.time = target_time
            anchors.append(tp)

    bm.timing_points.extend(new_lines)

    # Sweep away now-unused green lines left sitting at or shortly after
    # each new/moved early-volume line: a duplicate that changes nothing
    # beyond what the anchor already established (same volume, kiai,
    # SV and sampleset) contributes nothing and would otherwise remain
    # concurrent with -- or redundantly trailing -- the anchor. The window
    # is inclusive of the anchor's own timestamp (offset 0), not just
    # strictly after it, since duplicates commonly land exactly there
    # (e.g. several identical pinned candidates all clamped to the same
    # timestamp).
    kept_anchor_ids = set()
    for anchor in anchors:
        if id(anchor) in delete_ids:
            continue
        kept_anchor_ids.add(id(anchor))
        for other in bm.timing_points:
            if other is anchor or other.uninherited != 0:
                continue
            oid = id(other)
            if oid in delete_ids or oid in kept_anchor_ids or oid in kiai_changing_ids:
                continue
            if not (anchor.time - 1e-3 <= other.time <= anchor.time + early_threshold_ms + 1e-3):
                continue
            if (other.volume == anchor.volume and other.effects == anchor.effects
                    and other.beat_length == anchor.beat_length
                    and other.sample_set == anchor.sample_set
                    and other.sample_index == anchor.sample_index):
                delete_ids.add(oid)

    if delete_ids:
        bm.timing_points = [t for t in bm.timing_points if id(t) not in delete_ids]


def apply_early_volume_setting(folder: str, diff_files: List[str], volume_threshold_pct: float,
                                early_threshold_ms: float, section: Optional[tuple] = None):
    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        run_early_volume_setting(bm, volume_threshold_pct, early_threshold_ms, section)
        bm.save()


# =============================================================================
# Pattern Gallery
#
# Captures a rhythm pattern from whatever hit objects are currently
# selected in a running osu! stable editor. Rather than reading the
# selection out of process memory (fragile — osu!'s internal object layout
# drifts between builds), this uses a much sturdier signal: osu!'s editor
# already puts a text summary of the current selection on the system
# clipboard when you press Ctrl+C there — "mm:ss:mmm (n1,n2,...) - ",
# where the leading timestamp is the first (earliest) selected object's
# time and the parenthesized numbers are per-object display indices. Those
# indices reset periodically (they're not a stable global id — the same
# number can recur later in the map) so the only things actually trusted
# from that string are the anchor timestamp and how many numbers are
# listed. Combined with the beatmap osu_memory.py already knows is open in
# the editor, that's enough to look up the exact selected objects directly
# from the parsed .osu file: find the object at the anchor time, then take
# the next `count` objects in chronological order. This assumes a
# contiguous selection, which is the normal case for capturing a reusable
# pattern (a scattered, non-contiguous selection isn't really "a pattern").
#
# Since taiko gameplay never depends on x/y — see center_notes_to_playfield
# above — a pattern only stores each object's time offset from the first
# note, its type (circle/slider/spinner — i.e. note/drumroll/balloon), and
# its hitsound bits (don vs kat, finisher). Position is intentionally
# dropped.
# =============================================================================
PATTERN_LIBRARY_PATH = os.path.join(os.path.expanduser("~"), ".osu_taiko_helper_patterns.json")

_EDITOR_CLIPBOARD_RE = re.compile(r"^(\d+):(\d{2}):(\d{3})\s*\(([\d,]+)\)\s*-\s*$")


def parse_editor_clipboard_selection(text: str) -> Optional[tuple]:
    """Parses osu! editor's Ctrl+C clipboard text for a hit object
    selection. Returns (anchor_ms, count), or None if `text` doesn't
    match that format at all (e.g. nothing was selected/copied)."""
    if not text:
        return None
    m = _EDITOR_CLIPBOARD_RE.match(text.strip())
    if not m:
        return None
    minutes, seconds, ms, nums = m.groups()
    anchor_ms = int(minutes) * 60000 + int(seconds) * 1000 + int(ms)
    count = len(nums.split(","))
    return anchor_ms, count


_OSU_CURSOR_CLIPBOARD_RE = re.compile(r"^(\d+):([0-5]\d):(\d{3})\s*(?:\([\d,]+\))?\s*-\s*$")


def parse_osu_cursor_timestamp(text: str) -> Optional[float]:
    """Strictly matches osu! stable's own Ctrl+C clipboard export —
    'mm:ss:mmm - ' (nothing selected, just the cursor) or
    'mm:ss:mmm (n1,n2,...) - ' (a selection). Deliberately narrower than
    parse_time_input: this is used to auto-detect "the user just copied a
    timestamp in osu!" while polling the clipboard, so it needs to reject
    ordinary numbers/text that merely happen to contain digits — not just
    tolerate junk around a valid match the way pasting into a field does.
    Returns ms, or None if `text` isn't exactly that format."""
    if not text:
        return None
    m = _OSU_CURSOR_CLIPBOARD_RE.match(text.strip())
    if not m:
        return None
    minutes, seconds, ms = m.groups()
    return int(minutes) * 60000 + int(seconds) * 1000 + int(ms)


def extract_selected_hit_objects(bm: Beatmap, clipboard_text: str) -> Optional[List[HitObject]]:
    """Returns the exact HitObjects referenced by an editor-selection
    clipboard string (see parse_editor_clipboard_selection), or None if
    the clipboard text doesn't match or no object exists at the anchor
    time."""
    parsed = parse_editor_clipboard_selection(clipboard_text)
    if parsed is None:
        return None
    anchor_ms, count = parsed
    ordered = sorted(bm.hit_objects, key=lambda h: h.time)
    start_idx = None
    for i, ho in enumerate(ordered):
        if abs(ho.time - anchor_ms) < 1e-3:
            start_idx = i
            break
    if start_idx is None:
        return None
    selection = ordered[start_idx:start_idx + count]
    return selection if len(selection) == count else None


def load_pattern_library() -> List[dict]:
    if not os.path.exists(PATTERN_LIBRARY_PATH):
        return []
    try:
        with open(PATTERN_LIBRARY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_pattern_library(patterns: List[dict]):
    with open(PATTERN_LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(patterns, f, indent=2)


def _split_absolute_end_time(remainder: str) -> tuple:
    """A spinner/hold-note's `remainder` starts with its EndTime as a raw
    absolute millisecond value ('endTime,hitSample...') — unlike a
    slider's length, which is spatial and doesn't need adjusting when the
    object moves in time, this does. Returns (end_time, rest_of_remainder)."""
    parts = remainder.split(",", 1)
    return float(parts[0]), (parts[1] if len(parts) > 1 else "")


def _has_absolute_end_time(obj_type: int) -> bool:
    return bool(obj_type & 8) or bool(obj_type & 0x80)  # spinner or hold note


def _governing_beat_length(time: float, timing_points: List[TimingPoint]) -> float:
    """The actual ms-per-beat (from the governing red line) in effect at
    `time` — unlike a green line's beatLength, which encodes SV, not BPM.
    Falls back to a plain 120 BPM (500ms/beat) if the map somehow has no
    red line at all, which shouldn't normally happen."""
    uninherited = sorted([tp for tp in timing_points if tp.uninherited == 1], key=lambda t: t.time)
    gov = osu_parser._governing_timing_point(time, uninherited)
    return gov.beat_length if gov else 500.0


def _detect_snap_divisor(offset_beats: List[float], tolerance: float = 0.03) -> str:
    """Given each note's position in beats relative to the pattern's first
    note, finds the coarsest osu! snap divisor (the same set Map Cleaner's
    resnap uses) that every one of them lands on cleanly — checked from
    1/1 down to the finest compound (1/48), same coarse-to-fine order the
    editor colours ticks in. Returns 'Other' if nothing fits even at the
    finest level (e.g. the notes weren't actually snapped to a grid)."""
    for divisor_key in ["1/1", "1/2", "1/4", "1/6", "1/12", "1/24", "1/36", "1/48"]:
        bases = osu_parser.DIVISOR_BASES[divisor_key]
        if all(
            any(abs(beats * base - round(beats * base)) < tolerance for base in bases)
            for beats in offset_beats
        ):
            return divisor_key
    return "Other"


def add_pattern_to_gallery(name: str, hit_objects: List[HitObject], timing_points: List[TimingPoint]) -> dict:
    """Stores `hit_objects` as a new named pattern — position-independent
    (see module note above): each object keeps only its time offset from
    the first note, type, hitsound, and raw `remainder` (which is where a
    slider's length data lives). A spinner/hold note's EndTime is pulled
    out of `remainder` and stored as its own offset so it can be
    re-anchored correctly on insert, same as the note's own start time.

    Each object's offset is also converted to beats (offset_ms divided by
    the beatLength actually governing that note's original timestamp) —
    a BPM-independent position that insert_pattern_into_map can use to
    rescale the pattern onto a different BPM later, and that lets the
    gallery report which snap divisor the pattern was originally on."""
    ordered = sorted(hit_objects, key=lambda h: h.time)
    base_time = ordered[0].time
    objects = []
    offset_beats_list = []
    for ho in ordered:
        beat_length = _governing_beat_length(ho.time, timing_points)
        offset_ms = ho.time - base_time
        offset_beats = offset_ms / beat_length if beat_length else 0.0
        offset_beats_list.append(offset_beats)
        entry_obj = {
            "offset_ms": stable_round(offset_ms),
            "offset_beats": offset_beats,
            "obj_type": ho.obj_type,
            "hit_sound": ho.hit_sound,
        }
        if _has_absolute_end_time(ho.obj_type):
            end_time, rest = _split_absolute_end_time(ho.remainder)
            entry_obj["end_offset_ms"] = stable_round(end_time - base_time)
            entry_obj["end_offset_beats"] = (end_time - base_time) / beat_length if beat_length else 0.0
            entry_obj["remainder"] = rest
        else:
            entry_obj["remainder"] = ho.remainder
        objects.append(entry_obj)

    entry = {
        "name": name,
        "note_count": len(ordered),
        "duration_ms": stable_round(ordered[-1].time - base_time),
        "snap_divisor": _detect_snap_divisor(offset_beats_list),
        "objects": objects,
    }
    library = load_pattern_library()
    library.append(entry)
    save_pattern_library(library)
    return entry


# A note placed directly onto the "Manually Add Pattern" beat grid has no
# real timestamp or timing point to derive a beatLength from — there's no
# beatmap involved at all — so building its entry uses this fixed reference
# tempo (120 BPM) purely to give offset_ms a plausible-looking fallback
# value for insert_pattern_into_map's match_bpm=False path. offset_beats
# (which the UI computes directly from grid position, not from this
# constant) is what actually matters wherever match_bpm=True.
MANUAL_PATTERN_REFERENCE_BEAT_LENGTH = 500.0


def _build_manual_pattern_entry(name: str, notes: List[dict]) -> dict:
    """Shared entry-construction logic behind add_manual_pattern_to_gallery
    and update_manual_pattern_in_gallery. Each entry in `notes` is
    {"offset_beats": float, "kind": "note"|"slider"|"spinner", "is_kat":
    bool, "is_finisher": bool, "end_offset_beats": float|None} —
    offset_beats/end_offset_beats relative to an arbitrary shared origin
    (the caller doesn't need to normalize to the first note; that happens
    here, same as add_pattern_to_gallery does for real captures). "kind"
    defaults to "note" if omitted, for callers/data predating slider and
    spinner support. A slider/spinner's duration is recorded the same
    BPM-independent way a captured spinner's already is (end_offset_beats/
    end_offset_ms) so insert_pattern_into_map can rebuild a correctly
    timed slider length or spinner endTime for whatever map it lands on —
    see insert_pattern_into_map's own docstring for why a slider needs
    special handling there that a spinner doesn't. Does not touch the
    library on disk — callers decide whether to append or replace."""
    ordered = sorted(notes, key=lambda n: n["offset_beats"])
    base_beats = ordered[0]["offset_beats"]
    objects = []
    offset_beats_list = []
    span_ends = []
    for note in ordered:
        kind = note.get("kind", "note")
        offset_beats = note["offset_beats"] - base_beats
        offset_beats_list.append(offset_beats)
        obj = {
            "offset_ms": stable_round(offset_beats * MANUAL_PATTERN_REFERENCE_BEAT_LENGTH),
            "offset_beats": offset_beats,
            "remainder": "",
        }
        if kind == "note":
            hit_sound = osu_parser.HS_NORMAL
            if note.get("is_kat"):
                hit_sound |= osu_parser.HS_WHISTLE
            if note.get("is_finisher"):
                hit_sound |= osu_parser.HS_FINISH
            obj["obj_type"] = 1
            obj["hit_sound"] = hit_sound
        else:
            # Slider (drumroll) or spinner (balloon) — spinners have no
            # finisher variant, so is_finisher is only honored for sliders.
            end_offset_beats = note["end_offset_beats"] - base_beats
            hit_sound = osu_parser.HS_FINISH if (kind == "slider" and note.get("is_finisher")) else osu_parser.HS_NORMAL
            obj["obj_type"] = 2 if kind == "slider" else 8
            obj["hit_sound"] = hit_sound
            obj["end_offset_beats"] = end_offset_beats
            obj["end_offset_ms"] = stable_round(end_offset_beats * MANUAL_PATTERN_REFERENCE_BEAT_LENGTH)
            span_ends.append(end_offset_beats)
        objects.append(obj)

    return {
        "name": name,
        "note_count": len(ordered),
        "duration_ms": stable_round(max(offset_beats_list + span_ends) * MANUAL_PATTERN_REFERENCE_BEAT_LENGTH),
        "snap_divisor": _detect_snap_divisor(offset_beats_list),
        "objects": objects,
    }


def add_manual_pattern_to_gallery(name: str, notes: List[dict]) -> dict:
    """Builds and appends a new named pattern from notes placed directly on
    the Pattern Gallery's manual beat-grid editor — see
    _build_manual_pattern_entry for the notes schema."""
    entry = _build_manual_pattern_entry(name, notes)
    library = load_pattern_library()
    library.append(entry)
    save_pattern_library(library)
    return entry


def update_manual_pattern_in_gallery(old_name: str, new_name: str, notes: List[dict]) -> dict:
    """Rebuilds the pattern named `old_name` from `notes` (same schema as
    add_manual_pattern_to_gallery) and replaces it in place — same list
    position, so editing a pattern doesn't reshuffle its spot in the
    gallery filmstrip. Used by the Pattern Gallery's Edit context-menu
    action (ManualPatternWindow opened with an existing pattern loaded).
    Raises ValueError if old_name isn't found."""
    entry = _build_manual_pattern_entry(new_name, notes)
    library = load_pattern_library()
    for i, p in enumerate(library):
        if p["name"] == old_name:
            library[i] = entry
            save_pattern_library(library)
            return entry
    raise ValueError(f'No pattern named "{old_name}" to update.')


def delete_pattern_from_gallery(name: str):
    library = [p for p in load_pattern_library() if p["name"] != name]
    save_pattern_library(library)


def rename_pattern_in_gallery(old_name: str, new_name: str):
    library = load_pattern_library()
    for p in library:
        if p["name"] == old_name:
            p["name"] = new_name
            break
    else:
        raise ValueError(f'No pattern named "{old_name}" to rename.')
    save_pattern_library(library)


def _next_available_pattern_name(base: str) -> str:
    """Appends an incrementing numeric suffix to `base` until it's not
    already taken in the library — e.g. duplicating "Foo" twice gives
    "Foo 2" then "Foo 3", and duplicating-inverted "Foo" twice gives
    "Foo inverted" then "Foo inverted 2"."""
    existing = {p["name"] for p in load_pattern_library()}
    if base not in existing:
        return base
    n = 2
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"


def default_pattern_name() -> str:
    """The auto-generated name used whenever the pattern name field is
    left blank — both Capture-from-osu! and the manual editor (add *and*
    edit) — "Untitled Pattern", or "Untitled Pattern 2", "Untitled
    Pattern 3", etc. if that's already taken."""
    return _next_available_pattern_name("Untitled Pattern")


def duplicate_pattern(name: str) -> dict:
    """Exact copy of `name`, named "<name> 2" (or the next free number)."""
    entry = get_pattern(name)
    if entry is None:
        raise ValueError(f'No pattern named "{name}".')
    new_entry = copy.deepcopy(entry)
    n = 2
    existing = {p["name"] for p in load_pattern_library()}
    while f"{name} {n}" in existing:
        n += 1
    new_entry["name"] = f"{name} {n}"
    library = load_pattern_library()
    library.append(new_entry)
    save_pattern_library(library)
    return new_entry


def duplicate_pattern_inverted(name: str) -> dict:
    """Copy of `name` named "<name> inverted" with every plain note's
    Don/Kat swapped. Sliders and spinners have no Don/Kat concept in
    taiko (any drum face hits them) — their hitsound/finisher status is
    left untouched."""
    entry = get_pattern(name)
    if entry is None:
        raise ValueError(f'No pattern named "{name}".')
    new_entry = copy.deepcopy(entry)
    for obj in new_entry["objects"]:
        if obj["obj_type"] == 1:
            is_kat = bool(obj["hit_sound"] & (osu_parser.HS_WHISTLE | osu_parser.HS_CLAP))
            is_finisher = bool(obj["hit_sound"] & osu_parser.HS_FINISH)
            hit_sound = osu_parser.HS_NORMAL
            if not is_kat:
                hit_sound |= osu_parser.HS_WHISTLE
            if is_finisher:
                hit_sound |= osu_parser.HS_FINISH
            obj["hit_sound"] = hit_sound
    new_entry["name"] = _next_available_pattern_name(f"{name} inverted")
    library = load_pattern_library()
    library.append(new_entry)
    save_pattern_library(library)
    return new_entry


def duplicate_pattern_reversed(name: str) -> dict:
    """Copy of `name` named "<name> reversed", played backward in time —
    e.g. a don-kat-Don-Kat stream becomes Kat-Don-kat-don. Every object's
    offset_ms (and offset_beats, if recorded) is mirrored around the
    pattern's own total span, independently in each unit — a *captured*
    pattern's offset_beats reflects whatever BPM the source map happened
    to have at capture time, not any fixed reference the way a manually-
    built one's does, so mirroring both fields separately (rather than
    re-deriving one from the other) keeps whatever relationship already
    existed between them intact either way. A slider/spinner's head and
    tail swap roles under reversal (both endpoints mirror, so the object
    keeps its own duration but moves to the mirrored position)."""
    entry = get_pattern(name)
    if entry is None:
        raise ValueError(f'No pattern named "{name}".')
    new_entry = copy.deepcopy(entry)
    objects = new_entry["objects"]
    max_ms = max(o["end_offset_ms"] if o.get("end_offset_ms") is not None else o["offset_ms"] for o in objects)
    has_beats = all("offset_beats" in o for o in objects)
    max_beats = None
    if has_beats:
        max_beats = max(
            o["end_offset_beats"] if o.get("end_offset_beats") is not None else o["offset_beats"]
            for o in objects
        )
    for o in objects:
        if o.get("end_offset_ms") is not None:
            head_ms, tail_ms = max_ms - o["end_offset_ms"], max_ms - o["offset_ms"]
            o["offset_ms"], o["end_offset_ms"] = head_ms, tail_ms
            if max_beats is not None:
                head_b, tail_b = max_beats - o["end_offset_beats"], max_beats - o["offset_beats"]
                o["offset_beats"], o["end_offset_beats"] = head_b, tail_b
        else:
            o["offset_ms"] = max_ms - o["offset_ms"]
            if max_beats is not None:
                o["offset_beats"] = max_beats - o["offset_beats"]
    objects.sort(key=lambda o: o["offset_ms"])
    new_entry["name"] = _next_available_pattern_name(f"{name} reversed")
    library = load_pattern_library()
    library.append(new_entry)
    save_pattern_library(library)
    return new_entry


def get_pattern(name: str) -> Optional[dict]:
    for p in load_pattern_library():
        if p["name"] == name:
            return p
    return None


def insert_pattern_into_map(folder: str, diff_files: List[str], pattern: dict, target_time_ms: float,
                             match_bpm: bool = True):
    """Inserts a saved pattern's hit objects into each selected diff,
    anchored so the pattern's first note lands at `target_time_ms`.
    Position is fixed to playfield center (256, 192) — taiko gameplay
    never depended on x/y, matching how patterns were captured without it
    in the first place.

    If `match_bpm` is set (the default) and the pattern has beat-relative
    offsets recorded (see add_pattern_to_gallery — older patterns captured
    before that existed won't), each note is placed using the *target*
    map's own BPM at the insertion point rather than the pattern's
    original millisecond gaps — so a rhythm captured at one BPM keeps its
    rhythmic feel (e.g. a 1/6 triplet stays a 1/6 triplet) instead of
    landing off-grid when reused somewhere with a different BPM. Falls
    back to the literal offset_ms for any object missing beat data.

    A slider (obj_type & 2) with end_offset_ms recorded is one built in the
    manual beat-grid editor, not captured from a real map — a captured
    slider's `remainder` already carries a real, meaningful pixel `length`
    from its source map and is used as-is; a manually-built one only has a
    *duration* (end_offset_beats/ms), so its `length` has to be computed
    fresh here from the *target* map's own SliderMultiplier and whatever
    SV is active at the insertion point (duration = length / (SliderMultiplier
    * 100 * SV) * beatLength, solved for length) — otherwise it would carry
    over some meaningless placeholder length instead of the intended
    duration. Spinners don't need this: their remainder is just an
    absolute endTime, independent of SliderMultiplier/SV."""
    for fname in diff_files:
        path = os.path.join(folder, fname)
        bm = Beatmap(path)
        target_beat_length = _governing_beat_length(target_time_ms, bm.timing_points) if match_bpm else None
        try:
            slider_multiplier = float(bm.get_kv("Difficulty", "SliderMultiplier") or "1.4")
        except ValueError:
            slider_multiplier = 1.4
        for obj in pattern["objects"]:
            if match_bpm and target_beat_length and "offset_beats" in obj:
                new_time = target_time_ms + obj["offset_beats"] * target_beat_length
            else:
                new_time = target_time_ms + obj["offset_ms"]
            remainder = obj["remainder"]
            if "end_offset_ms" in obj:
                if match_bpm and target_beat_length and "end_offset_beats" in obj:
                    end_time = target_time_ms + obj["end_offset_beats"] * target_beat_length
                else:
                    end_time = new_time + (obj["end_offset_ms"] - obj["offset_ms"])
                if obj["obj_type"] & 2:
                    duration_ms = end_time - new_time
                    sv_beat_length = _effective_beat_length_at(new_time, bm.timing_points)
                    sv = -100.0 / sv_beat_length if sv_beat_length else 1.0
                    pixels_per_beat = slider_multiplier * 100 * sv
                    beat_length_here = _governing_beat_length(new_time, bm.timing_points)
                    length = (duration_ms / beat_length_here * pixels_per_beat) if beat_length_here else 0.0
                    remainder = f"L|266:192,1,{length:.2f},0|0,0:0|0:0,0:0:0:0:"
                else:
                    end_time = stable_round(end_time)
                    remainder = f"{end_time},{remainder}" if remainder else str(end_time)
            bm.hit_objects.append(HitObject(
                x=256, y=192,
                time=new_time,
                obj_type=obj["obj_type"],
                hit_sound=obj["hit_sound"],
                remainder=remainder,
            ))
        bm.save()


# A captured pattern is meant to be a small, reusable rhythm snippet, not
# an entire section of a map — capped at this many beats (matching
# ManualPatternWindow's own default timeline length) so an over-eager
# selection in the osu! editor doesn't produce an unwieldy pattern.
CAPTURED_PATTERN_MAX_BEATS = 4.0


def _truncate_hit_objects_to_beats(hit_objects: List[HitObject], timing_points: List[TimingPoint],
                                    max_beats: float) -> tuple:
    """Drops any object whose offset from the first (earliest) selected
    object exceeds max_beats — measured the same beat-relative way
    add_pattern_to_gallery itself does (via _governing_beat_length, the
    actual red-line BPM at each object's own time). The first object is
    always kept (its own offset is 0). Only trims by an object's *start*;
    a slider/spinner that starts within the limit but whose own duration
    runs slightly past it is left as captured rather than also clipping
    its length — that would need the source map's SliderMultiplier/SV to
    do correctly (see insert_pattern_into_map's own note on this) and
    slightly overhanging is a minor imprecision next to that complexity.
    Returns (kept_objects, truncated: bool)."""
    ordered = sorted(hit_objects, key=lambda h: h.time)
    base_time = ordered[0].time
    kept = []
    truncated = False
    for ho in ordered:
        beat_length = _governing_beat_length(ho.time, timing_points)
        offset_beats = (ho.time - base_time) / beat_length if beat_length else 0.0
        if offset_beats > max_beats:
            truncated = True
            continue
        kept.append(ho)
    return kept, truncated


def _drop_concurrent_hit_objects(hit_objects: List[HitObject]) -> tuple:
    """Taiko is a single-lane rhythm — two or more objects sharing the same
    timestamp (within ~1ms, matching the tolerance used elsewhere for
    "same time" comparisons — see the red-line-supported green line check
    in CLAUDE.md) aren't a real playable pattern, just stacked/duplicate
    notes. Keeps only the first object encountered at each distinct time
    (chronological order) and drops the rest. Returns (kept_objects,
    had_concurrent: bool)."""
    ordered = sorted(hit_objects, key=lambda h: h.time)
    kept = []
    had_concurrent = False
    last_time = None
    for ho in ordered:
        if last_time is not None and abs(ho.time - last_time) < 1e-3:
            had_concurrent = True
            continue
        kept.append(ho)
        last_time = ho.time
    return kept, had_concurrent


def capture_pattern_from_osu_selection(name: str, clipboard_text: str, songs_folder: str) -> tuple:
    """Full pipeline for the Pattern Gallery's "Capture from osu!" button:
    resolves whichever beatmap is currently open in a running osu! stable
    editor, matches the clipboard's selection against it, and saves the
    result as a new named pattern — truncated to CAPTURED_PATTERN_MAX_BEATS
    if the selection was longer than that (see _truncate_hit_objects_to_beats),
    and with any concurrent (same-timestamp) notes reduced to just one (see
    _drop_concurrent_hit_objects). Raises ValueError with a message suitable
    for showing directly to the user if any step fails. Returns
    (entry, truncated: bool, had_concurrent: bool)."""
    import osu_memory

    reader = osu_memory._get_pymem_reader()
    if reader is None:
        raise ValueError(
            "Couldn't connect to a running osu! stable client. Make sure osu! "
            "is open (Windows only, and the pymem package must be installed)."
        )
    try:
        result = osu_memory.resolve_folder_and_filename(reader)
    except Exception:
        result = None
    if result is None:
        raise ValueError("Couldn't determine which beatmap is currently open in osu!.")
    folder_name, osu_filename = result
    path = os.path.join(songs_folder, folder_name, osu_filename)
    if not os.path.exists(path):
        raise ValueError("The beatmap open in osu! wasn't found in your configured Songs folder.")

    bm = Beatmap(path)
    hit_objects = extract_selected_hit_objects(bm, clipboard_text)
    if not hit_objects:
        raise ValueError(
            "Couldn't match the clipboard to a selection. In the osu! editor, "
            "select the note(s) you want and press Ctrl+C, then click Capture "
            "again without doing anything else in between."
        )
    hit_objects, had_concurrent = _drop_concurrent_hit_objects(hit_objects)
    hit_objects, truncated = _truncate_hit_objects_to_beats(hit_objects, bm.timing_points,
                                                              CAPTURED_PATTERN_MAX_BEATS)
    entry = add_pattern_to_gallery(name, hit_objects, bm.timing_points)
    return entry, truncated, had_concurrent

