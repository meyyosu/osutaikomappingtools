"""Timing editor snaps, cursor and clicks reconstruct integer grid times."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class TimingGridTests(unittest.TestCase):
    def frame(self, anchor, unit, divisor=1):
        red = {"t": anchor, "bl": unit, "row": {}}
        return SimpleNamespace(
            _new_redlines=lambda **kwargs: [red], _divisor_denom=lambda: divisor,
            _row_meter=lambda row: 4, _snap_unit_at=lambda t: (anchor, unit),
            _clamp_cursor=Mock(), _redraw_timeline=Mock(), _audio_seek_to_cursor=Mock())

    def test_snap_truncates_anchor_then_sum_in_both_time_directions(self):
        for anchor, target, expected in ((1000.9, 1334, 1333),
                                         (-1000.9, -667, -666),
                                         (1000.9, 667, 666)):
            with self.subTest(anchor=anchor, target=target):
                frame = self.frame(anchor, 1000 / 3)
                self.assertEqual(TimingEditorFrame._snap_t(frame, target), expected)

    def test_half_tick_uses_half_away_from_zero(self):
        frame = self.frame(0, 500)
        self.assertEqual(TimingEditorFrame._snap_t(frame, 250), 500)
        self.assertEqual(TimingEditorFrame._snap_t(frame, -250), -500)

    def test_subdivision_and_bar_only_snap(self):
        self.assertEqual(TimingEditorFrame._snap_t(self.frame(1000.9, 1000 / 3, 4), 1084), 1083)
        self.assertEqual(TimingEditorFrame._snap_t(self.frame(1000.9, 1000 / 3, None), 2334), 2333)

    def test_cursor_steps_reconstruct_from_anchor_without_accumulating_error(self):
        frame = self.frame(1000.9, 1000 / 3)
        frame.cursor_ms = 1000
        for expected in (1333, 1666, 2000, 2333):
            TimingEditorFrame._step_cursor(frame, 1)
            self.assertEqual(frame.cursor_ms, expected)
        TimingEditorFrame._step_cursor(frame, -1)
        self.assertEqual(frame.cursor_ms, 2000)

    def test_metronome_uses_same_integer_ticks(self):
        frame = self.frame(1000.9, 1000 / 3)
        beats = list(TimingEditorFrame._iter_grid_beats(frame, 2500))
        self.assertIn((1000, True), beats)
        self.assertIn((1333, False), beats)
        self.assertIn((2333, True), beats)
        self.assertTrue(all(isinstance(time, int) and time >= 0 for time, _ in beats))


if __name__ == "__main__":
    unittest.main()
