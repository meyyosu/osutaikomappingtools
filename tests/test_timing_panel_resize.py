"""Resizing previews the height without repeatedly relaying out native widgets."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class TimingPanelResizeTests(unittest.TestCase):
    def setUp(self):
        self.frame = SimpleNamespace(
            table_canvas=Mock(), table_resize_handle=Mock(), advanced_controls=Mock(),
            _table_resize_guide=Mock(), _table_resize_start=None,
            winfo_screenheight=lambda: 1080)
        self.frame.table_canvas.winfo_height.return_value = 180
        self.frame.table_resize_handle.winfo_rooty.return_value = 400
        self.frame.advanced_controls.winfo_rooty.return_value = 100
        for name in ("_start_table_resize", "_resize_table", "_finish_table_resize"):
            setattr(self.frame, name, MethodType(getattr(TimingEditorFrame, name), self.frame))

    def test_drag_previews_and_release_commits_once(self):
        f = self.frame
        f._start_table_resize(SimpleNamespace(y_root=400))
        for y in range(410, 501, 10):
            f._resize_table(SimpleNamespace(y_root=y))
        f.table_canvas.configure.assert_not_called()
        f._table_resize_guide.place.assert_called_with(
            x=24, y=400, relwidth=1, width=-48, height=2)
        f._finish_table_resize(SimpleNamespace(y_root=520))
        f.table_canvas.configure.assert_called_once_with(height=300)
        f._table_resize_guide.place_forget.assert_called_once()
        self.assertIsNone(f._table_resize_start)
        f._resize_table(SimpleNamespace(y_root=600))
        self.assertEqual(f.table_canvas.configure.call_count, 1)

    def test_height_is_bounded(self):
        f = self.frame
        for y, expected in ((-1000, 72), (10000, 1080)):
            f._start_table_resize(SimpleNamespace(y_root=400))
            f._finish_table_resize(SimpleNamespace(y_root=y))
            f.table_canvas.configure.assert_called_with(height=expected)


if __name__ == "__main__":
    unittest.main()
