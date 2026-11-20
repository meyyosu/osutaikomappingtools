"""Table scrolling stays bounded and yields to direct scrollbar input."""
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class TimingScrollTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("600x180")
        self.frame = TimingEditorFrame.__new__(TimingEditorFrame)
        tk.Frame.__init__(self.frame, self.root)
        self.frame.pack(fill="both", expand=True)
        self.frame.rows = []
        self.frame.table_canvas = tk.Canvas(self.frame, height=180, yscrollincrement=1,
                                            highlightthickness=0, borderwidth=0)
        self.frame.table_canvas.pack(fill="both", expand=True)
        self.frame.table_inner = tk.Frame(self.frame.table_canvas, height=2000, width=600)
        self.frame.table_canvas.create_window(0, 0, window=self.frame.table_inner, anchor="nw")
        self.frame.table_canvas.configure(scrollregion=(0, 0, 600, 2000))
        self.root.update_idletasks()
        self.frame.table_canvas.yview_moveto(0)

    def tearDown(self):
        self.frame._cancel_table_animation()
        job = getattr(self.frame, "_table_scroll_settle_job", None)
        if job:
            self.frame.after_cancel(job)
        self.root.destroy()

    def run_animation(self):
        deadline = time.monotonic() + 2
        while self.frame._table_anim_job is not None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertIsNone(self.frame._table_anim_job)

    def test_rapid_and_fractional_wheel_events_accumulate(self):
        for delta in (-120, -120, -60):
            self.assertEqual(self.frame._table_wheel(SimpleNamespace(delta=delta)), "break")
        self.assertEqual(self.frame._table_scroll_target, 120)
        self.run_animation()
        self.assertAlmostEqual(self.frame.table_canvas.yview()[0] * 2000, 120, delta=1)

    def test_edges_and_scrollbar_takeover(self):
        self.frame._table_wheel(SimpleNamespace(delta=12000))
        self.assertEqual(self.frame._table_scroll_target, 0)
        self.frame._table_wheel(SimpleNamespace(delta=-12000))
        self.assertLessEqual(self.frame._table_scroll_target, 2000)
        self.frame._table_yview("moveto", "0.25")
        self.assertIsNone(self.frame._table_anim_job)
        self.assertIsNone(self.frame._table_scroll_target)
        self.assertAlmostEqual(self.frame.table_canvas.yview()[0], 0.25, places=3)



if __name__ == "__main__":
    unittest.main()
