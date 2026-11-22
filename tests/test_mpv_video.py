"""Preview timing regressions independent of media devices and tkinter."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from mpv_video import PreviewPlayback, library_candidates
import tools_logic as logic


class Player:
    def __init__(self, duration=30):
        self.ready = True
        self.values = {"duration": duration, "time-pos": 0, "pause": True}
        self.seeks = []
        self.closed = False

    def pump(self):
        return self.ready

    def number(self, name, default=0):
        return self.values.get(name, default)

    def set(self, name, value):
        self.values[name] = value

    def flag(self, name):
        return bool(self.values.get(name))

    def seek(self, ms):
        self.seeks.append(ms)
        self.values["time-pos"] = ms / 1000

    def close(self):
        self.closed = True


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.audio, self.video = Player(), Player(20)

    def preview(self, offset=0, audio=True):
        return PreviewPlayback(self.video, self.audio if audio else None,
                               offset, clock=lambda: self.now)

    def test_positive_delay_and_pause_during_lead_in(self):
        p = self.preview(2000)
        p.tick()
        self.assertFalse(p.video_visible)
        self.assertTrue(self.video.values["pause"])
        self.audio.values["time-pos"] = 1
        p.toggle_pause()
        self.now += 5
        p.tick()
        self.assertEqual(p.position, 1000)
        self.assertFalse(p.video_visible)
        p.toggle_pause()
        self.audio.values["time-pos"] = 2.1
        p.tick()
        self.assertTrue(p.video_visible)
        self.assertFalse(self.video.values["pause"])

    def test_negative_offset_and_seek_preserve_pause(self):
        p = self.preview(-1500)
        p.tick()
        self.assertEqual(self.video.seeks[-1], 1500)
        p.toggle_pause()
        p.seek(5000)
        self.assertEqual(self.audio.seeks[-1], 5000)
        self.assertEqual(self.video.seeks[-1], 6500)
        self.assertTrue(self.video.values["pause"])
        self.assertTrue(self.audio.values["pause"])

    def test_seek_back_into_delay_hides_video_without_late_callbacks(self):
        p = self.preview(3000)
        p.tick()
        p.seek(10000)
        self.assertTrue(p.video_visible)
        p.seek(1000)
        self.assertFalse(p.video_visible)
        self.assertTrue(self.video.values["pause"])
        p.seek(-500)
        self.assertEqual(p.position, 0)

    def test_nudge_restart_plays_from_song_start(self):
        p = self.preview(500)
        p.tick()
        p.toggle_pause()
        p.restart(-1000)
        self.assertEqual(p.position, 0)
        self.assertFalse(p.paused)
        self.assertEqual(self.video.seeks[-1], 1000)
        self.assertFalse(self.audio.values["pause"])

    def test_waits_for_both_files_and_retains_early_seek_and_pause(self):
        self.audio.ready = False
        p = self.preview()
        p.seek(4000)
        p.toggle_pause()
        self.assertFalse(p.ready)
        self.audio.ready = True
        p.tick()
        self.assertEqual(p.position, 4000)
        self.assertTrue(self.audio.values["pause"])
        self.assertEqual(self.video.seeks[-1], 4000)

    def test_no_song_audio_uses_paused_timeline_and_video_volume(self):
        p = self.preview(1000, audio=False)
        p.tick()
        self.now += .5
        p.toggle_pause()
        self.now += 3
        p.tick()
        self.assertEqual(p.position, 500)
        p.toggle_pause()
        self.now += 1
        p.tick()
        self.assertEqual(p.position, 1500)
        self.assertTrue(p.video_visible)
        p.set_volume(35)
        self.assertEqual(self.video.values["volume"], 35)

    def test_drift_corrected_against_song_and_seek_clamped_to_end(self):
        p = self.preview(1000)
        p.tick()
        self.now += 1
        self.audio.values["time-pos"] = 10
        p.tick()
        self.assertEqual(self.video.seeks[-1], 9000)
        p.seek(999999)
        self.assertEqual(p.position, 30000)
        p.tick()
        self.assertTrue(p.paused)

    def test_volume_and_close_both_native_players(self):
        p = self.preview()
        p.set_volume(500)
        self.assertEqual(self.audio.values["volume"], 100)
        p.close()
        self.assertTrue(self.video.closed and self.audio.closed)

    def test_offset_change_updates_duration_without_song(self):
        p = self.preview(1000, audio=False)
        p.tick()
        self.assertEqual(p.duration, 21000)
        p.restart(-1000)
        self.assertEqual(p.duration, 19000)

    def test_video_freezes_at_end_while_song_continues(self):
        p = self.preview()
        p.tick()
        self.now += 1
        self.audio.values["time-pos"] = 25
        p.tick()
        self.assertFalse(p.paused)
        self.assertTrue(self.video.values["pause"])
        self.assertLess(self.video.seeks[-1], 20000)

    def test_timeout_is_visible_error(self):
        self.video.ready = False
        p = self.preview()
        self.now += 21
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            p.tick()


class DiscoveryTests(unittest.TestCase):
    def test_bundle_precedes_system_and_frozen_extraction_is_searched(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "_MEIPASS", "frozen-data", create=True):
            paths = list(library_candidates())
            self.assertIn("mpv", paths[0])
            self.assertTrue(any("frozen-data" in path for path in paths))

    def test_unloadable_library_fails_closed(self):
        with patch("mpv_video.load_library", side_effect=RuntimeError("missing")):
            self.assertFalse(logic.mpv_available())

    def test_availability_releases_native_handle(self):
        lib = Mock()
        with patch("mpv_video.load_library", return_value=lib):
            self.assertTrue(logic.mpv_available())
            lib.mpv_terminate_destroy.assert_called_once_with(lib.mpv_create.return_value)


if __name__ == "__main__":
    unittest.main()
