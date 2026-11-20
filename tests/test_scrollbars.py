"""Shared scrollbar orientation, nested hover, modal drag and popup coverage."""
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from main import App
from scrollbars import AutoHideScrollbar
from screens import (BaseToolFrame, MetadataManagerFrame, FRONT_BG, _checkbox_icon_cache,
                     make_scrollable_toplevel_body)


class ScrollbarTests(unittest.TestCase):
    def setUp(self):
        _checkbox_icon_cache.clear()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)
        self.root.geometry("500x300")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.004)

    def test_horizontal_drag_and_fade_preserve_modal_grab(self):
        canvas = tk.Canvas(self.root, bg=FRONT_BG, scrollregion=(0, 0, 2000, 100))
        bar = AutoHideScrollbar(self.root, orient="horizontal", command=canvas.xview,
                                target=canvas)
        canvas.configure(xscrollcommand=bar.set)
        bar.pack(side="bottom", fill="x")
        canvas.pack(fill="both", expand=True)
        self.root.update()
        self.assertFalse(bar.visible)
        self.root.grab_set()
        top, length, _, _ = bar._thumb()
        bar.event_generate("<ButtonPress-1>", x=int(top + length / 2), y=6)
        bar.event_generate("<B1-Motion>", x=bar.winfo_width() - 4, y=6)
        bar.event_generate("<ButtonRelease-1>", x=bar.winfo_width() - 4, y=6)
        self.pump(0.2)
        self.assertIs(self.root.grab_current(), self.root)
        self.assertGreater(canvas.xview()[0], 0.5)
        thumb = bar.find_withtag("thumb")[0]
        x1, y1, x2, y2 = bar.coords(thumb)
        self.assertEqual(y1, y2)
        self.assertGreater(x2, x1)
        self.assertEqual(bar._opacity, 1)
        bar._set_visible(False)
        self.pump(0.24)
        self.assertEqual(bar._opacity, 0)
        self.assertFalse(bar.find_withtag("thumb"))

    def test_nested_child_edge_hover_and_tool_scroll(self):
        shell = SimpleNamespace(frames={}, _current_frame_name="tool")
        frame = BaseToolFrame(self.root, shell)
        shell.frames["tool"] = frame
        frame.pack(fill="both", expand=True)
        content = tk.Frame(frame.body, height=1500, width=400)
        content.pack(fill="x")
        self.root.update()
        bar = frame._scrollbar
        self.assertTrue(bar.can_scroll)
        self.assertFalse(bar.visible)
        # Created after attach(): hover must still reach the viewport observer.
        child = tk.Label(content, text="edge")
        child.place(relx=1, x=-10, y=20, width=10, height=20)
        self.root.update()
        child.event_generate("<Motion>", x=5, y=5)
        self.root.update()
        self.assertTrue(bar.visible)
        frame._scroll_canvas.yview_scroll(3, "units")
        self.root.update()
        self.assertGreater(frame._scroll_canvas.yview()[0], 0)
        bindings = list(bar._scope_bindings)
        frame.destroy()
        self.root.update()
        for owner, sequence, token in bindings:
            self.assertNotIn(token, owner.bind(sequence))

    def test_text_wheel_stays_inside_field_in_tools_and_popups(self):
        for popup in (False, True):
            with self.subTest(popup=popup):
                if popup:
                    owner = tk.Toplevel(self.root)
                    owner.attributes("-alpha", 0)
                    owner.geometry("500x300")
                    body = make_scrollable_toplevel_body(owner)
                    canvas = body.master
                else:
                    shell = SimpleNamespace(frames={}, _current_frame_name="tool")
                    owner = BaseToolFrame(self.root, shell)
                    shell.frames["tool"] = owner
                    owner.pack(fill="both", expand=True)
                    body = owner.body
                    canvas = owner._scroll_canvas
                field = tk.Text(body, height=3)
                field.pack(fill="x")
                field.insert("1.0", "\n".join(f"tag {i}" for i in range(100)))
                label = tk.Label(body, text="Page content")
                label.pack()
                tk.Frame(body, height=1500).pack(fill="x")
                self.root.update()
                owner.event_generate("<Enter>")
                canvas.yview_moveto(0)
                field.yview_moveto(0)
                page_view = canvas.yview()
                field.event_generate("<MouseWheel>", delta=-120)
                self.root.update()
                self.assertGreater(field.yview()[0], 0)
                self.assertEqual(canvas.yview(), page_view)
                # Reaching a field's boundary must not start moving the page.
                field.yview_moveto(1)
                field.event_generate("<MouseWheel>", delta=-120)
                self.root.update()
                self.assertEqual(canvas.yview(), page_view)
                label.event_generate("<MouseWheel>", delta=-120)
                self.root.update()
                self.assertGreater(canvas.yview()[0], page_view[0])
                owner.event_generate("<Leave>")
                owner.destroy()

    def test_metadata_tags_height_follows_five_line_field(self):
        shell = SimpleNamespace(metadata_autofill=False)
        frame = MetadataManagerFrame(self.root, shell)
        frame.pack(fill="both", expand=True)
        self.root.update()
        field = frame.tags_text
        self.assertEqual(int(field.cget("height")), 5)
        self.assertLessEqual(field.winfo_height(), field.winfo_reqheight() + 2)
        field.insert("1.0", "\n".join(f"tag {i}" for i in range(30)))
        self.root.update()
        self.assertLessEqual(field.winfo_height(), field.winfo_reqheight() + 2)
        self.assertLess(field.yview()[1], 1)

    def test_settings_and_first_time_setup_use_shared_scrollbars(self):
        self.root.osu_songs_folder = ""
        self.root.metadata_display_mode = "unicode"
        self.root.song_index_mode = "manual"
        self.root.confirm_pattern_delete = True
        self.root.live_sync_enabled = True

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        for opener, attribute in ((App.open_settings, "_settings_win"),
                                  (App.open_first_time_setup, "_first_time_win")):
            opener(self.root)
            win = getattr(self.root, attribute)
            win.geometry("580x250")
            self.root.update()
            bars = [w for w in descendants(win) if isinstance(w, AutoHideScrollbar)]
            self.assertEqual(len(bars), 1)
            bar = bars[0]
            for owner, sequence, token in bar._scope_bindings:
                self.assertIn(token, owner.bind(sequence))
            self.assertTrue(bar.can_scroll)
            self.assertEqual(bar.cget("bg"), FRONT_BG)
            bar.command("moveto", 0.5)
            self.root.update()
            self.assertTrue(bar.visible)
            self.assertGreater(bar.first, 0)
            win.destroy()
            self.root.update()


if __name__ == "__main__":
    unittest.main()
