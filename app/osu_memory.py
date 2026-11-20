"""
osu_memory.py
Best-effort detection of the beatmap currently selected/open in a running
osu! stable client, by reading its process memory the same way community
tools like OsuMemoryDataProvider / gosumemory do.

This is inherently Windows-only, requires osu! stable (not lazer) to be
running, and depends on memory offsets that can change between osu!
updates. Every public entry point here is designed to fail closed: if
anything at all goes wrong (wrong OS, osu! not running, a
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
import time
from typing import Iterator, List, NamedTuple, Optional, Tuple

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

    def close(self):
        """Release any OS resources owned by this reader."""


# ----------------------------------------------------------------------------
# Core, OS-independent algorithm (unit-testable against a fake MemoryReader)
# ----------------------------------------------------------------------------
def find_pattern(reader: MemoryReader, pattern: bytes) -> Optional[int]:
    """Scans every non-execute-only region for `pattern`, returning the
    absolute address of the first match, or None."""
    return next(find_patterns(reader, pattern), None)


def find_patterns(reader: MemoryReader, pattern: bytes) -> Iterator[int]:
    """Yield all candidates: the byte signature alone is not unique."""
    for region in reader.list_regions():
        if region.executable_only:
            continue
        data = reader.read_bytes(region.base, region.size)
        if not data:
            continue
        idx = data.find(pattern)
        while idx != -1:
            yield region.base + idx
            idx = data.find(pattern, idx + 1)


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


# Cache a validated anchor per process. Re-check its full pointer chain on
# every poll and periodically scan again on failure (including downstream
# failures), without scanning the whole address space every second at menus.
_signature_cache = {"pid": None, "base_addr": None, "last_scan": float("-inf")}
_RESCAN_INTERVAL = 5.0


def resolve_folder_and_filename(reader: MemoryReader) -> Optional[Tuple[str, str]]:
    """Returns (folder_name, osu_filename) for the currently loaded beatmap,
    or None if anything along the chain can't be resolved."""
    pid = getattr(reader, "pid", None)
    same_process = pid is not None and _signature_cache["pid"] == pid
    base_addr = _signature_cache["base_addr"] if same_process else None
    if base_addr is not None:
        # Validate the entire chain, not just the first nonzero pointer.
        # A stale anchor can still point at readable, unrelated memory.
        if reader.read_bytes(base_addr, len(OSU_BASE_PATTERN)) == OSU_BASE_PATTERN:
            result = _read_beatmap_at(reader, base_addr)
            if result:
                return result
    now = time.monotonic()
    if same_process and now - _signature_cache["last_scan"] < _RESCAN_INTERVAL:
        return None
    _signature_cache.update(pid=pid, base_addr=None, last_scan=now)
    for candidate in find_patterns(reader, OSU_BASE_PATTERN):
        result = _read_beatmap_at(reader, candidate)
        if result:
            _signature_cache["base_addr"] = candidate
            return result
    return None


def _read_beatmap_at(reader: MemoryReader, base_addr: int) -> Optional[Tuple[str, str]]:
    beatmap_data_ptr = reader.read_uint32(base_addr + OSU_BASE_TO_BEATMAP_DATA_OFFSET)
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
    if not folder or not osu_filename or not osu_filename.lower().endswith(".osu"):
        return None
    return folder, osu_filename


# ----------------------------------------------------------------------------
# Windows-backed implementation (only usable on real Windows with osu! open)
# ----------------------------------------------------------------------------
class WindowsMemoryReader(MemoryReader):
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

        query = ctypes.WinDLL("kernel32", use_last_error=True).VirtualQueryEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                          ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
        query.restype = ctypes.c_size_t
        handle = self.process_handle
        address = 0
        mbi = MEMORY_BASIC_INFORMATION()
        max_address = 0x7FFFFFFF  # osu! stable is 32-bit
        while address < max_address:
            result = query(
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


class PymemReader(WindowsMemoryReader):
    def __init__(self, pm):
        self._pm = pm
        self.pid = getattr(pm, "process_id", None)

    @property
    def process_handle(self):
        return self._pm.process_handle

    def close(self):
        if self._pm.process_handle:
            self._pm.close_process()

    def read_bytes(self, address: int, size: int) -> Optional[bytes]:
        try:
            return self._pm.read_bytes(address, size)
        except Exception:
            return None


class NativeWindowsReader(WindowsMemoryReader):
    """Read-only Win32 backend using Python's standard library."""

    def __init__(self, kernel, handle, pid):
        self._kernel = kernel
        self.process_handle = handle
        self.pid = pid

    def close(self):
        if self.process_handle:
            self._kernel.CloseHandle(self.process_handle)
            self.process_handle = None

    def read_bytes(self, address, size):
        import ctypes
        if not self.process_handle or size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        if not self._kernel.ReadProcessMemory(
                self.process_handle, ctypes.c_void_p(address), buffer,
                size, ctypes.byref(count)) or count.value != size:
            return None
        return buffer.raw


def _get_native_reader():
    """Find the 32-bit stable client without requiring a pip package."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Process32FirstW": ([wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
        "Process32NextW": ([wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "IsWow64Process2": ([wintypes.HANDLE, ctypes.POINTER(wintypes.WORD),
                             ctypes.POINTER(wintypes.WORD)], wintypes.BOOL),
        "ReadProcessMemory": ([wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
    }
    for name, (argtypes, restype) in declarations.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = argtypes, restype

    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == "osu!.exe":
                handle = kernel.OpenProcess(0x0410, False, entry.th32ProcessID)
                if handle:
                    keep = False
                    try:
                        process_machine, native_machine = wintypes.WORD(), wintypes.WORD()
                        if kernel.IsWow64Process2(handle, ctypes.byref(process_machine),
                                                 ctypes.byref(native_machine)):
                            # I386, either under WOW64 or on native 32-bit Windows.
                            if (process_machine.value or native_machine.value) == 0x014c:
                                reader = NativeWindowsReader(kernel, handle, entry.th32ProcessID)
                                keep = True
                                return reader
                    finally:
                        if not keep:
                            kernel.CloseHandle(handle)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return None


def _get_pymem_reader():
    """Return a reader attached to stable (legacy name kept for callers).

    Prefer the built-in Windows backend; pymem supports older Windows
    versions without IsWow64Process2. Neither backend writes process memory.
    """
    if os.name != "nt":
        return None
    try:
        reader = _get_native_reader()
        if reader is not None:
            return reader
    except Exception:
        pass
    try:
        import pymem
    except ImportError:
        return None
    try:
        # Materialize the generator so its process-snapshot handle is closed
        # even when we return early. Lazer uses the same executable name;
        # stable's pointer layout is exclusively 32-bit.
        # pymem yields the SAME mutable PROCESSENTRY32 for each iteration.
        processes = [(p.th32ProcessID, p.szExeFile.lower())
                     for p in pymem.process.list_processes()]
        seen = set()
        for pid, name in processes:
            if name != b"osu!.exe" or pid in seen:
                continue
            seen.add(pid)
            pm = None
            try:
                pm = pymem.Pymem()
                # Read/query access is sufficient. Pymem(pid) requests full
                # access and enables debug privileges; some pymem versions
                # leak a token handle on every such privilege adjustment.
                pm.process_handle = pymem.process.open(
                    pid, debug=False, process_access=0x0400 | 0x0010)
                if not pm.process_handle:
                    continue
                pm.process_id = pid
                if not pymem.process.is_64_bit(pm.process_handle):
                    return PymemReader(pm)
            except Exception:
                pass  # A process may exit while we're enumerating it.
            if pm is not None and pm.process_handle:
                pm.close_process()
    except Exception:
        pass
    return None


def get_current_beatmap_folder(songs_folder: str) -> Optional[str]:
    """Best-effort: returns the full path to the beatmap folder currently
    selected/open in a running osu! stable client, or None if it can't be
    determined (wrong OS, osu! not running, memory
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
    finally:
        reader.close()
    if not result:
        return None
    folder_name, osu_filename = result
    full_path = os.path.join(songs_folder, folder_name)
    if os.path.isdir(full_path):
        return full_path, osu_filename
    return None
