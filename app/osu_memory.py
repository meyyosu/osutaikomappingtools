"""
osu_memory.py
Best-effort detection of the beatmap currently selected/open in a running
osu! stable client, by reading its process memory the same way community
tools like OsuMemoryDataProvider / gosumemory do.

This is inherently Windows-only, requires osu! stable (not lazer) to be
running, and depends on memory offsets that can change between osu!
updates. Every public entry point here is designed to fail closed: if
anything at all goes wrong (wrong OS, pymem missing, osu! not running, a
signature not found, garbage pointers), functions return None rather than
raising, so callers can always fall back to asking the user to pick the
folder manually.

Algorithm ported from Piotrekol's ProcessMemoryDataFinder / OsuMemoryDataProvider
(GPL-3.0, https://github.com/Piotrekol/ProcessMemoryDataFinder) — specifically
OsuMemoryReader.cs's "OsuBase" signature and the CurrentBeatmapData pointer
chain (folder name at +0x78, .osu file name at +0x90 from the resolved
beatmap-data pointer). osu! stable is a 32-bit process, so pointers here are
always 4 bytes.
"""

from __future__ import annotations
import os
import struct
from typing import Callable, List, NamedTuple, Optional, Tuple

# The exact byte pattern OsuMemoryDataProvider scans for to locate a stable
# anchor point in osu!'s memory (see SignatureNames.OsuBase in the upstream
# source). Everything else is computed relative to wherever this is found.
OSU_BASE_PATTERN = bytes.fromhex("F80174048365")
OSU_BASE_TO_BEATMAP_DATA_OFFSET = -12
FOLDER_NAME_OFFSET = 0x78
OSU_FILENAME_OFFSET = 0x90


class MemoryRegion(NamedTuple):
    base: int
    size: int
    executable_only: bool  # regions that are PAGE_EXECUTE_READ are skipped,
                             # matching upstream's scan (it only searches
                             # writable/data pages, not pure code pages)


class MemoryReader:
    """Minimal interface the resolution logic needs. A real implementation
    wraps pymem+ctypes on Windows; tests can supply a fake in-memory one."""

    def list_regions(self) -> List[MemoryRegion]:
        raise NotImplementedError

    def read_bytes(self, address: int, size: int) -> Optional[bytes]:
        raise NotImplementedError

    def read_uint32(self, address: int) -> Optional[int]:
        data = self.read_bytes(address, 4)
        if data is None or len(data) != 4:
            return None
        return struct.unpack("<I", data)[0]


# ----------------------------------------------------------------------------
# Core, OS-independent algorithm (unit-testable against a fake MemoryReader)
# ----------------------------------------------------------------------------
def find_pattern(reader: MemoryReader, pattern: bytes) -> Optional[int]:
    """Scans every non-execute-only region for `pattern`, returning the
    absolute address of the first match, or None."""
    for region in reader.list_regions():
        if region.executable_only:
            continue
        data = reader.read_bytes(region.base, region.size)
        if not data:
            continue
        idx = data.find(pattern)
        if idx != -1:
            return region.base + idx
    return None


def read_clr_string(reader: MemoryReader, field_address: int) -> Optional[str]:
    """Reads a .NET (CLR) string given the address of a field that holds a
    pointer to it. Layout (32-bit CLR): [vtable ptr][int32 length][UTF-16
    chars...], so: dereference the field, read the length at +4, then read
    length*2 bytes at +8 and decode as UTF-16-LE."""
    str_obj_ptr = reader.read_uint32(field_address)
    if not str_obj_ptr:
        return None
    length = reader.read_uint32(str_obj_ptr + 4)
    if length is None or length <= 0 or length > 32768:
        return None
    raw = reader.read_bytes(str_obj_ptr + 8, length * 2)
    if raw is None or len(raw) != length * 2:
        return None
    try:
        return raw.decode("utf-16-le")
    except UnicodeDecodeError:
        return None


# OsuBase's address is a stable anchor for the lifetime of a given osu!
# process (it doesn't move once the module is loaded), so once found for a
# given pid it's cached here rather than re-scanning the entire address
# space on every call — needed because resolve_folder_and_filename() is now
# also called from a continuous once-a-second live-sync poll (main.py's
# _poll_live_osu_map), where re-running find_pattern's full-region scan that
# often would be wasteful. Falls back to a fresh scan if the cached address
# stops resolving (osu! restarted and reused the pid, stale cache, etc.).
_signature_cache = {"pid": None, "base_addr": None}


def resolve_current_beatmap_data_pointer(reader: MemoryReader) -> Optional[int]:
    """Finds OsuBase, then follows the -12/dereference chain to get the
    CurrentBeatmapData pointer (see OsuMemoryReader.cs's CreateBeatmapDataSignatures)."""
    pid = getattr(reader, "pid", None)
    base_addr = _signature_cache["base_addr"] if pid is not None and _signature_cache["pid"] == pid else None
    if base_addr is not None:
        value = reader.read_uint32(base_addr + OSU_BASE_TO_BEATMAP_DATA_OFFSET)
        if value:
            return value
        # Cached address stopped resolving — fall through and rescan.

    base_addr = find_pattern(reader, OSU_BASE_PATTERN)
    if base_addr is None:
        return None
    _signature_cache["pid"] = pid
    _signature_cache["base_addr"] = base_addr
    ptr_location = base_addr + OSU_BASE_TO_BEATMAP_DATA_OFFSET
    return reader.read_uint32(ptr_location)


def resolve_folder_and_filename(reader: MemoryReader) -> Optional[Tuple[str, str]]:
    """Returns (folder_name, osu_filename) for the currently loaded beatmap,
    or None if anything along the chain can't be resolved."""
    beatmap_data_ptr = resolve_current_beatmap_data_pointer(reader)
    if not beatmap_data_ptr:
        return None
    # beatmap_data_ptr itself is a field holding a pointer to the actual
    # beatmap object (ResolveChainOfPointers always dereferences once before
    # applying the field offset).
    beatmap_obj_ptr = reader.read_uint32(beatmap_data_ptr)
    if not beatmap_obj_ptr:
        return None
    folder = read_clr_string(reader, beatmap_obj_ptr + FOLDER_NAME_OFFSET)
    osu_filename = read_clr_string(reader, beatmap_obj_ptr + OSU_FILENAME_OFFSET)
    if not folder or not osu_filename:
        return None
    return folder, osu_filename


# ----------------------------------------------------------------------------
# Windows-backed implementation (only usable on real Windows with osu! open)
# ----------------------------------------------------------------------------
class PymemReader(MemoryReader):
    def __init__(self, pm):
        self._pm = pm
        self.pid = getattr(pm, "process_id", None)

    def list_regions(self) -> List[MemoryRegion]:
        import ctypes
        from ctypes import wintypes

        regions = []
        PAGE_EXECUTE_READ = 0x20
        MEM_COMMIT = 0x1000

        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
            ]

        handle = self._pm.process_handle
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        max_address = 0x7FFFFFFF  # osu! stable is 32-bit
        while address < max_address:
            result = ctypes.windll.kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if result == 0:
                break
            if mbi.State == MEM_COMMIT and mbi.RegionSize > 0:
                regions.append(MemoryRegion(
                    base=address,
                    size=mbi.RegionSize,
                    executable_only=(mbi.Protect == PAGE_EXECUTE_READ),
                ))
            address += mbi.RegionSize or 0x1000
        return regions

    def read_bytes(self, address: int, size: int) -> Optional[bytes]:
        try:
            return self._pm.read_bytes(address, size)
        except Exception:
            return None


def _get_pymem_reader():
    """Returns a ready PymemReader attached to a running osu!.exe, or None
    if that's not possible for any reason (not Windows, pymem missing,
    osu! not running, access denied, ...)."""
    if os.name != "nt":
        return None
    try:
        import pymem
    except ImportError:
        return None
    try:
        pm = pymem.Pymem("osu!.exe")
    except Exception:
        return None
    return PymemReader(pm)


def get_current_beatmap_folder(songs_folder: str) -> Optional[str]:
    """Best-effort: returns the full path to the beatmap folder currently
    selected/open in a running osu! stable client, or None if it can't be
    determined (wrong OS, osu! not running, pymem not installed, memory
    layout not matching what we expect, etc.) — callers should fall back to
    asking the user to pick the folder manually whenever this returns None."""
    result = get_current_beatmap_folder_and_diff(songs_folder)
    return result[0] if result else None


def get_current_beatmap_folder_and_diff(songs_folder: str) -> Optional[Tuple[str, str]]:
    """Same best-effort detection as get_current_beatmap_folder(), but also
    returns the specific .osu filename currently open (e.g. for showing
    exactly which difficulty is selected, not just which map). Returns
    (full_folder_path, osu_filename), or None on any failure."""
    reader = _get_pymem_reader()
    if reader is None:
        return None
    try:
        result = resolve_folder_and_filename(reader)
    except Exception:
        return None
    if not result:
        return None
    folder_name, osu_filename = result
    full_path = os.path.join(songs_folder, folder_name)
    if os.path.isdir(full_path):
        return full_path, osu_filename
    return None
