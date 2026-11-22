"""Uniform offset edits default to moving notes and survive saving to disk."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame, _fmt_ms_timestamp
from osu_parser import Beatmap


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TimingRetimeTests(unittest.TestCase):
    def frame(self):
        frame = TimingEditorFrame.__new__(TimingEditorFrame)
        frame.advanced_timing_var = Value(True)
        frame.rows = []
        for time, bl in ((1000, 60000 / 137.123456), (5000, 400)):
            frame.rows.append({
                "old_time": time, "old_bl": bl, "is_new": False,
                "meter": 4, "sample_set": 1, "sample_index": 0,
                "volume": 100, "effects": 0, "_deleted": False,
                "noff_var": Value(_fmt_ms_timestamp(time)),
                "nbpm_var": Value(frame._fmt_bpm(60000 / bl)),
                "ts_var": Value("4"), "bl_var": Value(False),
            })
        frame.resnap_equalise_var = Value(False)
        frame._restoring = False
        frame._redraw_timeline = Mock()
        frame._schedule_commit = Mock()
        return frame

    def test_default_tracks_offsets_and_bpm_and_recovers(self):
        frame = self.frame()
        frame._sync_resnap_default()
        self.assertTrue(frame.resnap_equalise_var.get())
        frame.rows[0]["noff_var"].set("1025")
        frame._after_row_edit(frame.rows[0])
        self.assertFalse(frame.resnap_equalise_var.get())
        frame.rows[1]["noff_var"].set("5025")
        frame._after_row_edit(frame.rows[1])
        self.assertTrue(frame.resnap_equalise_var.get())
        frame.rows[1]["nbpm_var"].set("151")
        frame._after_row_edit(frame.rows[1])
        self.assertFalse(frame.resnap_equalise_var.get())
        frame.rows[1]["nbpm_var"].set("150")
        frame._after_row_edit(frame.rows[1])
        self.assertTrue(frame.resnap_equalise_var.get())
        frame.rows[0]["noff_var"].set("-")
        frame._after_row_edit(frame.rows[0])
        self.assertFalse(frame.resnap_equalise_var.get())

    def test_deleted_rows_do_not_affect_default(self):
        frame = self.frame()
        frame.rows[1]["nbpm_var"].set("151")
        frame.rows[1]["_deleted"] = True
        frame._sync_resnap_default()
        self.assertTrue(frame.resnap_equalise_var.get())
        frame.rows[1]["_deleted"] = False
        frame._sync_resnap_default()
        self.assertFalse(frame.resnap_equalise_var.get())
        frame.rows = []
        frame._sync_resnap_default()
        self.assertFalse(frame.resnap_equalise_var.get())

    def test_apply_uniform_offsets_moves_saved_notes_without_bpm_or_sv_drift(self):
        for delta in (-25, 25):
            with self.subTest(delta=delta), tempfile.TemporaryDirectory() as folder:
                frame = self.frame()
                path = Path(folder) / "test.osu"
                bl = frame.rows[0]["old_bl"]
                path.write_text(
                    "osu file format v14\n[General]\nPreviewTime:2000\n"
                    "[Events]\n2,2500,3000\n[TimingPoints]\n"
                    f"1000,{bl!r},4,1,0,100,1,0\n"
                    "1500,-80,4,1,0,100,0,0\n5000,400,4,1,0,100,1,0\n"
                    "[HitObjects]\n256,192,900,1,0,0:0:0:0:\n"
                    "256,192,2000,1,0,0:0:0:0:\n"
                    "256,192,4800,8,0,5200,0:0:0:0:\n"
                    "256,192,6000,1,0,0:0:0:0:\n", encoding="utf-8")
                for row in frame.rows:
                    row["noff_var"].set(str(row["old_time"] + delta))
                frame._after_row_edit(frame.rows[0])
                self.assertTrue(frame.resnap_equalise_var.get())
                frame.require_map = lambda: True
                frame.app = SimpleNamespace(get_diff_files=lambda: (folder, [path.name]))
                frame.diff_list = SimpleNamespace(selected=lambda: [path.name])
                frame._capture_state = lambda: ((False,),)
                frame._initial_state = ()
                frame.apply_btn = Mock()
                frame.status_var = Value("")
                frame._apply_done = Mock()
                frame._apply_failed = Mock()
                frame.after = lambda delay, callback: callback()
                with patch("screens.threading.Thread", side_effect=lambda target, **kw:
                           SimpleNamespace(start=target)):
                    frame._apply()
                frame._apply_failed.assert_not_called()
                frame._apply_done.assert_called_once()
                bm = Beatmap(str(path))
                self.assertEqual([h.time for h in bm.hit_objects],
                                 [t + delta for t in (900, 2000, 4800, 6000)])
                self.assertTrue(bm.hit_objects[2].remainder.startswith(str(5200 + delta)))
                reds = [tp for tp in bm.timing_points if tp.uninherited]
                self.assertEqual([tp.time for tp in reds], [1000 + delta, 5000 + delta])
                self.assertAlmostEqual(reds[0].beat_length, bl, places=10)
                green = next(tp for tp in bm.timing_points if not tp.uninherited)
                self.assertEqual((green.time, green.beat_length), (1500 + delta, -80))
                self.assertEqual(bm.get_kv("General", "PreviewTime"), str(2000 + delta))


if __name__ == "__main__":
    unittest.main()
