"""Manual seeking and visual interpolation follow the one native audio clock."""
from pathlib import Path
import sys
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class PlaybackSeekTests(unittest.TestCase):
    def setUp(self):
        self.jobs = {}
        self.next_job = 0
        self.frame = SimpleNamespace(
            _player=Mock(), _playing=True, _play_after=None, _seek_job=None,
            _pending_seek_ms=None, cursor_ms=10000, _panned_cursor=10000,
            _play_anchor_ms=10000, _play_anchor_wall=100,
            _play_last_frame_wall=100, _play_floor_ms=0, _playback_rate=1.0,
            PLAY_TICK_MS=16, PLAYHEAD_SMOOTH_TAU=.75, play_btn=Mock(),
            _remove_metro_track=Mock(), metronome_var=SimpleNamespace(get=lambda: True),
        )
        for name in ("_audio_seek_to_cursor", "_flush_pending_seek", "_safe_set_time",
                     "_tick_play", "_reset_play_anchor", "_pause_audio", "_stop_audio",
                     "_on_metronome_toggle"):
            setattr(self.frame, name, MethodType(getattr(TimingEditorFrame, name), self.frame))
        self.frame.after = self.schedule
        self.frame.after_cancel = lambda job: self.jobs.pop(job)
        self.frame._pan_timeline = Mock(side_effect=self.pan)
        self.frame._player.get_time.return_value = 10000
        self.frame._player.ended.return_value = False
        self.frame._player.duration_ms = 20000
        self.frame._player.seek.side_effect = lambda ms: setattr(self.frame._player.get_time, "return_value", ms)

    def schedule(self, delay, callback):
        self.next_job += 1
        self.jobs[self.next_job] = callback
        return self.next_job

    def pan(self, ms):
        self.frame.cursor_ms = self.frame._panned_cursor = ms

    def run_job(self, job):
        self.jobs.pop(job)()

    def test_backward_steps_accumulate_without_old_clock_overwriting_input(self):
        f = self.frame
        for target in (9500, 9000, 8500):
            f.cursor_ms = target
            f._audio_seek_to_cursor()
            f._tick_play()
            self.assertEqual(f.cursor_ms, target)
        f._player.seek.assert_not_called()
        self.run_job(f._seek_job)
        f._player.seek.assert_called_once_with(8500)

    def test_explicit_backward_jump_resets_previous_visual_floor(self):
        f = self.frame
        with patch("screens.time.monotonic", return_value=100):
            f._safe_set_time(3000)
            f._tick_play()
        self.assertEqual(f.cursor_ms, 3000)
        self.assertEqual(f._play_floor_ms, 3000)

    def test_pause_cancels_pending_seek_and_frame(self):
        f = self.frame
        f._tick_play()
        f.cursor_ms = 3000
        f._audio_seek_to_cursor()
        f._pause_audio()
        self.assertEqual(self.jobs, {})
        self.assertIsNone(f._pending_seek_ms)
        f._player.pause.assert_called_once_with()

    def test_metronome_toggle_only_changes_gain_on_the_existing_graph(self):
        f = self.frame
        f._on_metronome_toggle()
        f._player.set_metronome.assert_called_once_with(True)
        f._player.seek.assert_not_called()

    def test_large_clock_discrepancy_does_not_jump_visual_position(self):
        f = self.frame
        f._player.get_time.return_value = 11000
        with patch("screens.time.monotonic", return_value=100.016):
            f._tick_play()
        self.assertLessEqual(f.cursor_ms - 10000, 16.65)

    def test_frame_schedule_accounts_for_rendering_work(self):
        f = self.frame
        delays = []
        f.after = lambda delay, callback: delays.append(delay)
        with patch("screens.time.monotonic", side_effect=[100.016, 100.022]):
            f._tick_play()
        self.assertEqual(delays, [10])

    def test_visual_playhead_advances_at_each_selected_rate(self):
        f = self.frame
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                f._playback_rate = rate
                with patch("screens.time.monotonic", return_value=100):
                    f._reset_play_anchor(10000)
                f._panned_cursor = 10000
                f._player.get_time.return_value = 10000 + 16 * rate
                with patch("screens.time.monotonic", return_value=100.016):
                    f._tick_play()
                self.assertAlmostEqual(f.cursor_ms, 10000 + 16 * rate)

    def test_end_of_song_releases_graph(self):
        f = self.frame
        player = f._player
        player.ended.return_value = True
        f._tick_play()
        self.assertEqual(f.cursor_ms, 20000)
        player.close.assert_called_once_with()
        self.assertFalse(f._playing)
        self.assertIsNone(f._player)


if __name__ == "__main__":
    unittest.main()
