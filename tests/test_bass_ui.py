"""The real timing UI starts, changes rates and seeks with no VLC dependency."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from bass_audio import BassPlayer, available
from screens import TimingEditorFrame, _checkbox_icon_cache


@unittest.skipUnless(os.name == "nt" and available(), "BASS libraries unavailable")
class BassUITests(unittest.TestCase):
    def setUp(self):
        _checkbox_icon_cache.clear()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)
        self.root.geometry("1000x700")
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name, "silence.wav")
        with wave.open(str(self.path), "wb") as dst:
            dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            dst.writeframes(bytes(48000 * 4 * 3))
        app = SimpleNamespace(get_diff_files=lambda: (None, []), frames={}, _current_frame_name="timing")
        def run_job(message, work, on_success, **kwargs):
            on_success(work(threading.Event()))
        app.run_cancellable_job = run_job
        self.frame = TimingEditorFrame(self.root, app)
        app.frames["timing"] = self.frame
        self.frame.pack(fill="both", expand=True)
        self.frame._audio_path = lambda: str(self.path)
        self.frame.rows = [{"_deleted": False, "noff_var": tk.StringVar(value="0"),
                            "nbpm_var": tk.StringVar(value="120"), "ts_var": tk.StringVar(value="4"),
                            "bl_var": tk.BooleanVar(value=False)}]
        self.frame.metronome_var.set(False)  # all native output is silent
        self.root.update()

    def tearDown(self):
        self.frame.destroy()
        self.root.destroy()
        self.folder.cleanup()
        self.assertEqual(self.errors, [])

    def test_start_rate_changes_backward_seek_and_cleanup_without_vlc(self):
        def unexpected_alert(*args):
            raise AssertionError(args)
        with patch("screens._show_alert", side_effect=unexpected_alert), \
                patch("screens.logic.mpv_available", side_effect=AssertionError("Video backend was consulted")):
            self.frame._toggle_play()
            self.assertIsInstance(self.frame._player, BassPlayer)
            self.assertTrue(self.frame._playing)
            for label, rate in (("25%", .25), ("50%", .5), ("75%", .75), ("100%", 1)):
                self.frame.playback_rate_var.set(label)
                self.assertTrue(self.frame._playing)
                self.assertEqual(self.frame._player.rate, rate)
                self.assertEqual(self.frame._player._prepared_song, rate != 1)
                self.frame._safe_set_time(1000)
                self.frame._safe_set_time(250)
                self.assertAlmostEqual(self.frame._player.get_time(), 250, delta=20)
            track = self.frame._metro_track_path
            self.frame._stop_audio()
            self.assertIsNone(self.frame._player)
            self.assertFalse(os.path.exists(track))


if __name__ == "__main__":
    unittest.main()
