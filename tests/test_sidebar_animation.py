"""The sidebar must animate without moving any embedded native windows."""
from pathlib import Path
import sys
import time
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from main import App


class SidebarShell(tk.Tk):
    _build_body = App._build_body
    _draw_sidebar = App._draw_sidebar
    _sidebar_target = App._sidebar_target
    _sidebar_motion = App._sidebar_motion
    _sidebar_press = App._sidebar_press
    _sidebar_release = App._sidebar_release
    _toggle_sidebar_category = App._toggle_sidebar_category
    _update_sidebar_scroll = App._update_sidebar_scroll
    _on_sidebar_wheel = App._on_sidebar_wheel

    def show_frame(self, name):
        self._current_frame_name = name
        self._draw_sidebar()


class SidebarAnimationTests(unittest.TestCase):
    def setUp(self):
        self.root = SidebarShell()
        self.root.attributes("-alpha", 0)
        self.root.geometry("1000x600")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.root._build_body()
        self.root.update()
        self.canvas = self.root._sidebar_canvas
        self.width = self.canvas.winfo_width()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def assert_clear_labels(self):
        self.assertEqual(self.canvas.winfo_children(), [])
        self.assertFalse(any(self.canvas.type(i) == "window" for i in self.canvas.find_all()))
        self.assertEqual(self.canvas.winfo_width(), self.width)
        headings = [self.canvas.bbox(i) for i in self.canvas.find_withtag("category-label")]
        for index, category in enumerate(self.root.sidebar_categories):
            for item in self.canvas.find_withtag(category):
                bounds = self.canvas.bbox(item)
                self.assertGreaterEqual(bounds[0], 0)
                self.assertLessEqual(bounds[2], self.width)
                if "tool-label" in self.canvas.gettags(item):
                    self.assertGreater(bounds[1], headings[index][3])
                    if index + 1 < len(headings):
                        self.assertLess(bounds[3], headings[index + 1][1])

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            self.assert_clear_labels()
            time.sleep(0.004)

    def click(self, key):
        left, top, right, bottom, _ = next(
            region for region in self.root._sidebar_hit_regions if region[-1] == key)
        x = int((left + right) / 2 - self.canvas.canvasx(0))
        y = int((top + bottom) / 2 - self.canvas.canvasy(0))
        self.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        self.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.root.update()

    def test_mapset_clicks_and_rapid_reversal(self):
        state = self.root.sidebar_categories["Mapset"]
        self.click(("category", "Mapset"))
        self.pump(0.06)
        before = state["openness"]
        self.click(("category", "Mapset"))
        self.assertAlmostEqual(before, state["openness"], places=3)
        self.pump(0.32)
        self.assertEqual(state["openness"], 1)
        for target in (0, 1):
            self.click(("category", "Mapset"))
            self.pump(0.32)
            self.assertEqual(state["openness"], target)
            self.assertIsNone(state["animation_job"])

    def test_labels_never_overlap_neighbors_at_any_animation_fraction(self):
        for category, state in self.root.sidebar_categories.items():
            for step in range(101):
                state["openness"] = step / 100
                self.root._draw_sidebar()
                self.root.update()
                self.assert_clear_labels()
            state["openness"] = 1

    def test_tool_selection_and_scrolling(self):
        self.click(("tool", "Map Cleaner"))
        self.assertEqual(self.root._current_frame_name, "Map Cleaner")
        label = next(i for i in self.canvas.find_withtag("Map Cleaner")
                     if self.canvas.type(i) == "text")
        from main import UI_ACCENT
        self.assertEqual(self.canvas.itemcget(label, "fill"), UI_ACCENT)
        self.canvas.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertGreater(self.canvas.canvasy(0), 0)
        self.assert_clear_labels()

    def test_scrollbar_reveals_on_wheel_and_edge_then_hides(self):
        bar = self.root._sidebar_scrollbar
        self.assertTrue(bar.can_scroll)
        self.assertFalse(bar.visible)
        self.canvas.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertTrue(bar.visible)
        self.assertEqual(bar.itemcget(bar.find_withtag("thumb")[0], "capstyle"), "round")
        self.pump(1.2)
        self.assertFalse(bar.visible)
        self.canvas.event_generate("<Motion>", x=self.width - 3, y=100)
        self.root.update()
        self.assertTrue(bar.visible)
        self.pump(1.2)
        self.assertTrue(bar.visible)
        self.canvas.event_generate("<Motion>", x=80, y=100)
        self.pump(1.2)
        self.assertFalse(bar.visible)

    def test_scrollbar_drag_and_no_overflow(self):
        bar = self.root._sidebar_scrollbar
        top, length, _, _ = bar._thumb()
        bar.event_generate("<ButtonPress-1>", x=6, y=int(top + length / 2))
        bar.event_generate("<B1-Motion>", x=6, y=bar.winfo_height() - 4)
        bar.event_generate("<ButtonRelease-1>", x=6, y=bar.winfo_height() - 4)
        self.root.update()
        self.assertGreater(self.canvas.yview()[0], 0)
        self.assertIsNone(bar._drag_offset)
        for state in self.root.sidebar_categories.values():
            state["expanded"] = False
            state["openness"] = 0
        self.root._draw_sidebar()
        self.root.update()
        bar.reveal()
        self.assertFalse(bar.can_scroll)
        self.assertFalse(bar.visible)
        self.assertEqual(self.canvas.winfo_width(), self.width)


if __name__ == "__main__":
    unittest.main()
