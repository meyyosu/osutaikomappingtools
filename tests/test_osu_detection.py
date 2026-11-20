"""Recovery regressions; no live client, Tk window, or real beatmaps needed."""
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import main
import osu_memory as memory


class FakeReader(memory.MemoryReader):
    pid = 123

    def __init__(self):
        self.data = bytearray(8192)
        self.scans = 0

    def list_regions(self):
        self.scans += 1
        return [memory.MemoryRegion(0, len(self.data), False)]

    def read_bytes(self, address, size):
        if address < 0 or address + size > len(self.data):
            return None
        return bytes(self.data[address:address + size])

    def uint(self, address, value):
        self.data[address:address + 4] = struct.pack("<I", value)

    def beatmap(self, anchor, slot, obj, folder="123 song", filename="song [Oni].osu"):
        self.data[anchor:anchor + len(memory.OSU_BASE_PATTERN)] = memory.OSU_BASE_PATTERN
        self.uint(anchor - 12, slot)
        self.uint(slot, obj)
        for offset, text, string_obj in ((memory.FOLDER_NAME_OFFSET, folder, obj + 256),
                                         (memory.OSU_FILENAME_OFFSET, filename, obj + 512)):
            raw = text.encode("utf-16-le")
            self.uint(obj + offset, string_obj)
            self.uint(string_obj + 4, len(raw) // 2)
            self.data[string_obj + 8:string_obj + 8 + len(raw)] = raw


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        memory._signature_cache.update(pid=None, base_addr=None, last_scan=float("-inf"))
        self.reader = FakeReader()

    def test_skips_false_signature_and_caches_valid_candidate(self):
        self.reader.data[100:106] = memory.OSU_BASE_PATTERN
        self.reader.beatmap(200, 500, 1000)
        for _ in range(3):
            self.assertEqual(memory.resolve_folder_and_filename(self.reader),
                             ("123 song", "song [Oni].osu"))
        self.assertEqual(self.reader.scans, 1)

    def test_recovers_when_cached_chain_is_nonzero_but_stale(self):
        self.reader.beatmap(200, 500, 1000)
        with patch.object(memory.time, "monotonic", return_value=0):
            self.assertIsNotNone(memory.resolve_folder_and_filename(self.reader))
        self.reader.uint(500, 7000)  # Initial pointer remains nonzero/readable.
        self.reader.beatmap(300, 600, 2000, filename="new [Oni].osu")
        with patch.object(memory.time, "monotonic", return_value=6):
            self.assertEqual(memory.resolve_folder_and_filename(self.reader)[1], "new [Oni].osu")

    def test_temporary_null_selection_recovers_without_scanning_every_poll(self):
        self.reader.beatmap(200, 500, 1000)
        with patch.object(memory.time, "monotonic", return_value=0):
            memory.resolve_folder_and_filename(self.reader)
            self.reader.uint(500, 0)
            for _ in range(3):
                self.assertIsNone(memory.resolve_folder_and_filename(self.reader))
            self.reader.uint(500, 1000)
            self.assertIsNotNone(memory.resolve_folder_and_filename(self.reader))
        self.assertEqual(self.reader.scans, 1)

    def test_reused_pid_revalidates_signature_and_rescans(self):
        self.reader.beatmap(200, 500, 1000)
        with patch.object(memory.time, "monotonic", return_value=0):
            memory.resolve_folder_and_filename(self.reader)
        self.reader.data[200:206] = b"xxxxxx"
        self.reader.beatmap(300, 600, 2000, filename="restarted.osu")
        with patch.object(memory.time, "monotonic", return_value=6):
            self.assertEqual(memory.resolve_folder_and_filename(self.reader)[1], "restarted.osu")

    def test_public_detection_closes_reader_even_on_error(self):
        for error in (False, True):
            reader = Mock()
            with patch.object(memory, "_get_pymem_reader", return_value=reader), \
                 patch.object(memory, "resolve_folder_and_filename",
                              side_effect=RuntimeError("read failed") if error else None,
                              return_value=("folder", "map.osu")), \
                 patch.object(memory.os.path, "isdir", return_value=True):
                result = memory.get_current_beatmap_folder_and_diff("Songs")
            self.assertEqual(result, None if error else (os.path.join("Songs", "folder"), "map.osu"))
            reader.close.assert_called_once()

    def test_lazer_first_does_not_hide_stable_and_handles_are_closed(self):
        try:
            import pymem
        except ImportError:
            self.skipTest("Optional legacy pymem backend is not installed")
        # Match pymem's generator that reuses a single mutable entry.
        entry = SimpleNamespace()

        def processes():
            for pid, name in ((1, b"osu!.exe"), (2, b"osu!.exe"), (3, b"other.exe")):
                entry.th32ProcessID, entry.szExeFile = pid, name
                yield entry

        clients = {pid: Mock(process_id=pid, process_handle=pid) for pid in (1, 2)}
        with patch.object(memory.os, "name", "nt"), \
             patch.object(memory, "_get_native_reader", return_value=None), \
             patch.object(pymem.process, "list_processes", side_effect=processes), \
             patch.object(pymem, "Pymem", side_effect=[clients[1], clients[2]]), \
             patch.object(pymem.process, "open", side_effect=lambda pid, **kwargs: pid) as open_process, \
             patch.object(pymem.process, "is_64_bit", side_effect=lambda handle: handle == 1):
            reader = memory._get_pymem_reader()
            self.assertEqual(reader.pid, 2)
            clients[1].close_process.assert_called_once()
            reader.close()
            clients[2].close_process.assert_called_once()
            open_process.assert_any_call(2, debug=False, process_access=0x0410)

    def test_detection_works_without_pymem(self):
        reader = Mock()
        with patch.object(memory.os, "name", "nt"), \
             patch.dict(sys.modules, {"pymem": None}), \
             patch.object(memory, "_get_native_reader", return_value=reader), \
             patch.object(memory, "resolve_folder_and_filename", return_value=("folder", "map.osu")), \
             patch.object(memory.os.path, "isdir", return_value=True):
            self.assertEqual(memory.get_current_beatmap_folder_and_diff("Songs"),
                             (os.path.join("Songs", "folder"), "map.osu"))
        reader.close.assert_called_once()

    def test_native_reader_closes_handle_once_and_failed_reads_return_none(self):
        kernel = Mock()
        kernel.ReadProcessMemory.return_value = False
        reader = memory.NativeWindowsReader(kernel, 1234, 5678)
        self.assertIsNone(reader.read_bytes(100, 4))
        reader.close()
        reader.close()
        kernel.CloseHandle.assert_called_once_with(1234)
        self.assertIsNone(reader.read_bytes(100, 4))
        self.assertEqual(kernel.ReadProcessMemory.call_count, 1)


class PollTests(unittest.TestCase):
    def test_load_error_does_not_stop_polling_or_mark_map_as_loaded(self):
        app = SimpleNamespace(winfo_exists=lambda: True, live_sync_enabled=True,
                              osu_songs_folder="Songs", _last_live_sync_key=None,
                              _select_map_folder=Mock(side_effect=OSError("file being saved")),
                              after=Mock(), _poll_live_osu_map=Mock())
        result = ("Songs/map", "map.osu")
        with patch.object(main.os.path, "isdir", return_value=True), \
             patch.object(memory, "get_current_beatmap_folder_and_diff", return_value=result):
            with self.assertRaises(OSError):
                main.App._poll_live_osu_map(app)
            self.assertIsNone(app._last_live_sync_key)
            app.after.assert_called_once_with(1000, app._poll_live_osu_map)
            app._select_map_folder.side_effect = None
            main.App._poll_live_osu_map(app)
            self.assertEqual(app._last_live_sync_key, result)
            self.assertEqual(app._select_map_folder.call_count, 2)


if __name__ == "__main__":
    unittest.main()
