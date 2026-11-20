"""Timeline and waveform share one surface and one playback translation."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class CanvasRecorder:
    def __init__(self):
        self.items = {}
        self.next_id = 0
        self.moves = 0

    def winfo_width(self):
        return 800

    def create(self, kind, *coords, **options):
        if len(coords) == 1:
            coords = coords[0]
        tags = options.get("tags", ())
        if isinstance(tags, str):
            tags = (tags,)
        self.next_id += 1
        self.items[self.next_id] = [kind, list(coords), tags, options]
        return self.next_id

    def create_line(self, *args, **kwargs):
        return self.create("line", *args, **kwargs)

    def create_text(self, *args, **kwargs):
        return self.create("text", *args, **kwargs)

    def create_polygon(self, *args, **kwargs):
        return self.create("polygon", *args, **kwargs)

    def create_rectangle(self, *args, **kwargs):
        return self.create("rectangle", *args, **kwargs)

    def delete(self, tag):
        self.items = {i: item for i, item in self.items.items()
                      if tag != "all" and tag not in item[2]}

    def move(self, tag, dx, dy):
        self.moves += 1
        for _, coords, tags, _ in self.items.values():
            if tag in tags:
                for i in range(0, len(coords), 2):
                    coords[i] += dx
                    coords[i + 1] += dy

    def itemconfigure(self, *args, **kwargs):
        pass


class TimelineAlignmentTests(unittest.TestCase):
    def setUp(self):
        canvas = CanvasRecorder()
        var = SimpleNamespace(get=lambda: False)
        reds = [{"t": 0, "bl": 500, "row": {"bl_var": var}}]
        self.frame = SimpleNamespace(
            timeline=canvas, wave=canvas, cursor_ms=1000, px_per_ms=.15,
            LEFT_PAD=8, CURSOR_FRAC=.30, TIMELINE_H=120, WAVE_H=66,
            WAVEFORM_VISUAL_OFFSET_MS=20, _peaks=[.4] * 4000, _bucket_ms=5,
            _wave_mode="amplitude", _new_redlines=lambda: reds,
            _row_meter=lambda row: 4, _divisor_denom=lambda: None,
            timeline_locked_var=var, wave_barlines_var=SimpleNamespace(get=lambda: True),
            _sync_seek_scrollbar=Mock(), _play_aux_ui_wall=0,
        )
        for name in ("_redraw_timeline", "_redraw_waveform", "_iter_barline_x",
                     "_pan_timeline", "_timeline_w", "_cursor_x", "_view_start",
                     "_visible_ms", "_total_ms", "_t_to_x", "_x_to_t", "_tl_press"):
            setattr(self.frame, name, MethodType(getattr(TimingEditorFrame, name), self.frame))
        self.frame._redraw_timeline()

    def assert_aligned(self):
        grid, wave = [], []
        for kind, coords, tags, options in self.frame.timeline.items.values():
            if kind == "line" and options.get("fill") == "#000000":
                (wave if "wave" in tags else grid).append(coords[0])
        self.assertTrue(grid)
        self.assertEqual(grid, wave)

    def test_barline_alignment_survives_playback_and_full_redraws(self):
        self.assert_aligned()
        for i in range(1, 201):
            before = self.frame.timeline.moves
            self.frame._pan_timeline(1000 + i * 17.3)
            self.assertEqual(self.frame.timeline.moves - before, 1)
            self.assert_aligned()
            if i % 30 == 0:
                self.frame._redraw_timeline()
                self.assert_aligned()

    def test_waveform_redraw_preserves_timeline_items_and_height(self):
        c = self.frame.timeline
        timeline_items = {i for i, item in c.items.items() if "wave" not in item[2]}
        self.frame._redraw_waveform()
        self.assertTrue(timeline_items.issubset(c.items))
        self.assert_aligned()
        for kind, coords, tags, _ in c.items.values():
            if kind == "polygon" and "wave" in tags:
                self.assertGreaterEqual(min(coords[1::2]), 120)
                self.assertLessEqual(max(coords[1::2]), 186)

    def test_fractional_grid_uses_truncated_times_in_both_bands(self):
        red = self.frame._new_redlines()[0]
        red.update(t=1000.9, bl=1000 / 3)
        self.frame._redraw_timeline()
        self.assert_aligned()
        xs = list(self.frame._iter_barline_x([red], 900, 2400))
        self.assertEqual(xs, [self.frame._t_to_x(1000), self.frame._t_to_x(2333)])

    def test_waveform_drag_does_not_grab_redline(self):
        self.frame.timeline.focus_set = Mock()
        self.frame._tl_press(SimpleNamespace(x=self.frame._t_to_x(0), y=130))
        self.assertTrue(self.frame._drag_in_wave)
        self.assertIsNone(self.frame._drag_red)


if __name__ == "__main__":
    unittest.main()
