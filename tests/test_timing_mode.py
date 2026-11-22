"""Basic offset mode and advanced timing use separate controls and apply paths."""
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame, OffsetShifterFrame, _checkbox_icon_cache
from osu_parser import Beatmap


class TimingModeTests(unittest.TestCase):
    def setUp(self):
        _checkbox_icon_cache.clear()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("1100x850")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.osu"
        self.path.write_text(
            "osu file format v14\n[General]\nPreviewTime:2000\n"
            "[Metadata]\nVersion:Oni\n[TimingPoints]\n"
            "752,500,4,1,0,100,1,0\n1500,-80,4,1,0,100,0,0\n"
            "3000,400,4,1,0,100,1,0\n"
            "[HitObjects]\n256,192,2000,1,0,0:0:0:0:\n", encoding="utf-8")
        self.app = SimpleNamespace(
            get_diff_files=lambda: (self.temp.name, [self.path.name]), frames={})
        self.frame = TimingEditorFrame(self.root, self.app)
        self.frame.pack(fill="both", expand=True)
        with patch("screens.logic.ffmpeg_available", return_value=False):
            self.frame._load_map()
        self.root.update()

    def tearDown(self):
        for job in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", job)
        self.root.destroy()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_default_and_toggle_preserve_both_sets_of_edits(self):
        f = self.frame
        self.assertFalse(f.advanced_timing_var.get())
        self.assertTrue(f.offset_controls.winfo_ismapped())
        self.assertFalse(f.advanced_controls.winfo_ismapped())
        self.assertEqual(f.offset_controls.base_offset, 752)
        f.offset_controls.new_var.set("800")
        self.assertEqual(f.offset_controls.change_var.get(), "48")
        f.advanced_timing_var.set(True)
        f._sync_timing_mode()
        self.root.update()
        self.assertTrue(f.advanced_controls.winfo_ismapped())
        self.assertFalse(f.offset_controls.winfo_ismapped())
        f.rows[0]["nbpm_var"].set("125")
        f.advanced_timing_var.set(False)
        f._sync_timing_mode()
        self.root.update()
        self.assertEqual(f.offset_controls.new_var.get(), "800")
        self.assertEqual(f.rows[0]["nbpm_var"].get(), "125")
        self.assertEqual(f._new_redlines()[0]["bl"], 500)

    def test_basic_apply_shifts_everything_and_ignores_advanced_edits(self):
        f = self.frame
        f.rows[0]["nbpm_var"].set("140")
        f.offset_controls.change_var.set("25")
        self.assertEqual(f.offset_controls.new_var.get(), "777")
        f.notify_done = Mock()
        # Execute work and completion synchronously for disk assertions.
        f.after = lambda delay, callback: callback() if delay == 0 else None
        with patch("screens.threading.Thread", side_effect=lambda target, **kw:
                   SimpleNamespace(start=target)), patch("screens.logic.ffmpeg_available", return_value=False), \
                patch("screens.logic.apply_advanced_timing") as advanced:
            f._apply()
        advanced.assert_not_called()
        bm = Beatmap(str(self.path))
        self.assertEqual([tp.time for tp in bm.timing_points], [777, 1525, 3025])
        self.assertEqual(bm.timing_points[0].beat_length, 500)
        self.assertEqual(bm.hit_objects[0].time, 2025)
        self.assertEqual(bm.get_kv("General", "PreviewTime"), "2025")
        self.assertEqual(f.offset_controls.new_var.get(), "777")
        self.assertEqual(f.offset_controls.change_var.get(), "0")
        f.notify_done.assert_called_once()

    def test_audio_screen_keeps_audio_operations_without_offset_fields(self):
        audio = OffsetShifterFrame(self.root, self.app)
        audio.refresh()
        self.assertFalse(hasattr(audio, "new_var"))
        self.assertFalse(hasattr(audio, "change_var"))
        self.assertTrue(hasattr(audio, "add_silence_var"))
        self.assertTrue(hasattr(audio, "reencode_bitrate_var"))

    def test_basic_shortcuts_cannot_edit_hidden_timing(self):
        f = self.frame
        f._is_current = lambda: True
        f._undo, f._redo, f._delete_selected = Mock(), Mock(), Mock()
        f._on_undo_key()
        f._on_redo_key()
        f._on_delete_key()
        f._undo.assert_not_called()
        f._redo.assert_not_called()
        f._delete_selected.assert_not_called()

    def test_offset_fields_redraw_shifted_preview_without_saving(self):
        f = self.frame
        original = self.path.read_bytes()
        original_x = f._t_to_x(752)
        with patch.object(f, "_redraw_timeline", wraps=f._redraw_timeline) as redraw:
            f.offset_controls.new_var.set("852")
            redraw.assert_called_once()
            self.assertEqual([r["t"] for r in f._new_redlines()], [852, 3100])
            self.assertAlmostEqual(f._t_to_x(852) - original_x, 100 * f.px_per_ms)
            # The rendered timing label changes too, without an Apply click.
            labels = [f.timeline.itemcget(item, "text")
                      for item in f.timeline.find_all() if f.timeline.type(item) == "text"]
            self.assertTrue(any("00:00:852" in text for text in labels))
            redraw.reset_mock()
            f.offset_controls.change_var.set("-50")
            redraw.assert_called_once()
            self.assertEqual(f.offset_controls.new_var.get(), "702")
            self.assertEqual([r["t"] for r in f._new_redlines()], [702, 2950])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(f.rows[0]["old_time"], 752)

    def test_switch_carries_basic_offset_into_advanced_mode(self):
        f = self.frame
        f.offset_controls.change_var.set("100")
        f.advanced_timing_switch.event_generate("<Button-1>", x=10, y=10)
        self.root.update()
        self.assertTrue(f.advanced_timing_var.get())
        self.assertTrue(f.advanced_controls.winfo_ismapped())
        self.assertEqual([r["t"] for r in f._new_redlines()], [852, 3100])
        f.advanced_timing_switch.event_generate("<Button-1>", x=10, y=10)
        self.root.update()
        self.assertFalse(f.advanced_timing_var.get())
        self.assertTrue(f.offset_controls.winfo_ismapped())
        self.assertEqual([r["t"] for r in f._new_redlines()], [852, 3100])

    def test_advanced_offset_carries_back_without_accumulating_on_toggle(self):
        f = self.frame
        f.advanced_timing_switch._toggle()
        f.rows[0]["noff_var"].set("00:00:802")
        f.rows[1]["noff_var"].set("00:03:100")
        f.rows[1]["nbpm_var"].set("160")
        f.advanced_timing_switch._toggle()
        self.assertEqual(f.offset_controls.new_var.get(), "802")
        self.assertEqual(f.offset_controls.change_var.get(), "50")
        # Increasing the overall shift by 25ms preserves the advanced
        # section's extra 50ms and its independent BPM edit.
        f.offset_controls.change_var.set("75")
        for _ in range(3):
            f.advanced_timing_switch._toggle()
            self.assertEqual([r["t"] for r in f._new_redlines()], [827, 3125])
            self.assertEqual(f.rows[1]["nbpm_var"].get(), "160")
            f.advanced_timing_switch._toggle()
            self.assertEqual(f.offset_controls.change_var.get(), "75")
        self.assertEqual(Beatmap(str(self.path)).timing_points[0].time, 752)


if __name__ == "__main__":
    unittest.main()
