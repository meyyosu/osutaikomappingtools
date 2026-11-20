"""Resize only the active tool and let nested cards settle without reentry."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from main import App, EARLY_VOLUME_DEFAULTS
from screens import BaseToolFrame, RoundedCard, EarlyVolumeSettingFrame, FRONT_BG, _checkbox_icon_cache


class WindowResizeTests(unittest.TestCase):
    def setUp(self):
        # PhotoImages belong to the Tcl interpreter used by each test.
        _checkbox_icon_cache.clear()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.root.geometry("700x500")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_hidden_tools_do_not_reflow_and_keep_their_state(self):
        shell = SimpleNamespace(frames={}, sidebar_buttons={}, _current_frame_name=None)
        shell._toggle_sidebar_category = Mock()
        for name in ("one", "two"):
            frame = BaseToolFrame(self.root, shell)
            frame.on_shown = Mock()
            frame.on_hidden = Mock()
            card = RoundedCard(frame.body)
            card.pack(fill="x")
            entry = ttk.Entry(card.body)
            entry.insert(0, name)
            entry.pack()
            frame.saved_entry = entry
            shell.frames[name] = frame
        App.show_frame(shell, "one")
        self.root.update()
        App.show_frame(shell, "two")
        self.root.update()
        hidden = shell.frames["one"]
        changes = []
        hidden.bind("<Configure>", changes.append)
        for width in range(710, 910, 20):
            self.root.geometry(f"{width}x550")
            self.root.update()
        self.assertEqual(changes, [])
        self.assertFalse(hidden.winfo_ismapped())
        hidden.on_hidden.assert_called_once()
        App.show_frame(shell, "one")
        self.root.update()
        self.assertTrue(hidden.winfo_ismapped())
        self.assertEqual(hidden.saved_entry.get(), "one")
        self.assertEqual(hidden.winfo_width(), self.root.winfo_width())
        self.assertEqual(hidden.on_shown.call_count, 2)

    def test_nested_cards_follow_content_and_reuse_their_background(self):
        outer = RoundedCard(self.root)
        outer.pack(fill="x")
        inner = RoundedCard(outer.body)
        inner.pack(fill="x")
        label = tk.Label(inner.body, text="first row")
        label.pack()
        self.root.update()
        before_h = outer.winfo_height()
        background = outer._background_item
        extra = tk.Label(inner.body, text="extra rows\n" * 6)
        extra.pack()
        # Existing callers can still request a redraw explicitly. Bursts
        # must leave only one pending job and must never flush layout inline.
        with patch.object(outer._canvas, "update_idletasks", side_effect=AssertionError):
            outer.redraw()
            pending = outer._redraw_job
            for _ in range(100):
                outer.redraw()
            self.assertEqual(outer._redraw_job, pending)
            self.root.update()
        self.assertGreater(outer.winfo_height(), before_h)
        extra.destroy()
        self.root.geometry("900x500")
        self.root.update()
        self.assertEqual(outer.winfo_height(), before_h)
        self.assertEqual(outer._background_item, background)
        self.assertEqual(len(outer._canvas.find_withtag("card_bg")), 1)
        self.assertEqual(inner.winfo_width(), outer.winfo_width() - outer.PAD_X * 2)
        self.assertIsNone(outer._redraw_job)
        outer.redraw()
        outer.destroy()
        self.root.update()

    def test_settings_sections_are_flat_and_content_sized(self):
        self.root.osu_songs_folder = ""
        self.root.song_index_mode = "manual"
        self.root.confirm_pattern_delete = True
        self.root.live_sync_enabled = True
        self.root.metadata_display_mode = "unicode"
        App.open_settings(self.root)
        win = self.root._settings_win
        self.root.update()

        def sections(widget):
            for child in widget.winfo_children():
                if isinstance(child, RoundedCard):
                    yield child
                yield from sections(child)

        cards = list(sections(win))
        self.assertEqual(len(cards), 6)
        for card in cards:
            self.assertTrue(card._flat)
            self.assertEqual(card.body.cget("bg"), FRONT_BG)
            self.assertEqual(card.winfo_height(), card.body.winfo_reqheight()
                             + card.PAD_Y_TOP + card.PAD_Y_BOTTOM)
        for first, second in zip(cards, cards[1:]):
            self.assertEqual(second.winfo_y() - first.winfo_y() - first.winfo_height(), 16)
        win.destroy()

    def test_tool_section_does_not_stretch_with_window_height(self):
        shell = SimpleNamespace(
            early_volume_settings=dict(EARLY_VOLUME_DEFAULTS),
            frames={}, _current_frame_name="volume",
            save_early_volume_settings=Mock(),
        )
        frame = EarlyVolumeSettingFrame(self.root, shell)
        shell.frames["volume"] = frame
        frame.pack(fill="both", expand=True)
        self.root.update()
        card = next(child for child in frame.body.winfo_children()
                    if isinstance(child, RoundedCard))
        original = (card.winfo_y(), card.winfo_height())
        for height in (700, 1000, 600):
            self.root.geometry(f"1000x{height}")
            self.root.update()
            self.assertEqual((card.winfo_y(), card.winfo_height()), original)
        self.assertEqual(card.winfo_height(), card.body.winfo_reqheight()
                         + card.PAD_Y_TOP + card.PAD_Y_BOTTOM)

    def test_title_layout_requests_are_coalesced(self):
        shell = SimpleNamespace(
            after_idle=self.root.after_idle,
            winfo_exists=self.root.winfo_exists,
            _titlebar=tk.Frame(self.root),
            _now_selecting_label=Mock(),
            _now_selecting_font=Mock(),
            now_selecting_var=tk.StringVar(self.root, value="map title"),
        )
        with patch("main._truncate_to_width", return_value="map title") as truncate:
            for _ in range(100):
                App._update_now_selecting_wraplength(shell)
            self.root.update()
            truncate.assert_called_once()
            self.assertIsNone(shell._now_selecting_layout_job)


if __name__ == "__main__":
    unittest.main()
