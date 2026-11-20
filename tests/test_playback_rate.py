"""Rate controls preserve playback state and space clicks in output time."""
from array import array
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from screens import TimingEditorFrame


class PlaybackRateTests(unittest.TestCase):
    def test_arrows_step_and_clamp_at_each_end(self):
        variable = SimpleNamespace(value="100%")
        variable.get = lambda: variable.value
        variable.set = lambda value: setattr(variable, "value", value)
        frame = SimpleNamespace(PLAYBACK_RATE_OPTIONS=TimingEditorFrame.PLAYBACK_RATE_OPTIONS,
                                playback_rate_var=variable)
        for direction, expected in ((1, "100%"), (-1, "75%"), (-1, "50%"),
                                    (-1, "25%"), (-1, "25%"), (1, "50%")):
            TimingEditorFrame._step_playback_rate(frame, direction)
            self.assertEqual(variable.value, expected)

    def test_rate_change_pauses_then_resumes_only_when_playing(self):
        for playing in (True, False):
            with self.subTest(playing=playing):
                frame = SimpleNamespace(
                    PLAYBACK_RATE_OPTIONS=TimingEditorFrame.PLAYBACK_RATE_OPTIONS,
                    playback_rate_var=SimpleNamespace(get=lambda: "50%"),
                    _playback_rate=1.0, _playing=playing, cursor_ms=12345,
                )
                events = []
                frame._pause_audio = lambda: events.append(("pause", frame._playback_rate))
                frame._resume_audio = lambda: events.append(("resume", frame._playback_rate))
                TimingEditorFrame._on_playback_rate_changed(frame)
                self.assertEqual(frame._playback_rate, .5)
                self.assertEqual(frame.cursor_ms, 12345)
                self.assertEqual(events, [("pause", 1), ("resume", .5)] if playing else [])

    def test_same_rate_is_noop(self):
        frame = SimpleNamespace(
            PLAYBACK_RATE_OPTIONS=TimingEditorFrame.PLAYBACK_RATE_OPTIONS,
            playback_rate_var=SimpleNamespace(get=lambda: "100%"),
            _playback_rate=1.0, _playing=True,
            _pause_audio=Mock(), _resume_audio=Mock(),
        )
        TimingEditorFrame._on_playback_rate_changed(frame)
        frame._pause_audio.assert_not_called()
        frame._resume_audio.assert_not_called()

    def test_rate_changes_click_spacing_without_stretching_each_click(self):
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                frame = SimpleNamespace(
                    _playback_rate=rate, _tick1_path="bar", _tick2_path="beat",
                    _read_metro_wav=lambda path: (array("h", [1000, 500, 250]), 1, 1200),
                    _iter_grid_beats=lambda duration: iter([(0, True), (500, False), (1000, True)]),
                )
                path = TimingEditorFrame._build_metro_track(frame, 1000)
                try:
                    with wave.open(path, "rb") as src:
                        self.assertEqual(src.getnframes(), round(1800 / rate))
                        samples = array("h")
                        samples.frombytes(src.readframes(src.getnframes()))
                    expected = set()
                    for beat_ms in (0, 500, 1000):
                        offset = round(beat_ms * 1200 / (1000 * rate))
                        self.assertEqual(list(samples[offset:offset + 3]), [1000, 500, 250])
                        expected.update(range(offset, offset + 3))
                    self.assertEqual({i for i, sample in enumerate(samples) if sample}, expected)
                finally:
                    if path:
                        os.unlink(path)

    def test_rate_change_rebuilds_click_spacing(self):
        frame = SimpleNamespace(_playback_rate=1.0, _new_redlines=lambda: [])
        first = TimingEditorFrame._metro_signature_for(frame, 1000)
        frame._playback_rate = .5
        self.assertNotEqual(first, TimingEditorFrame._metro_signature_for(frame, 1000))


if __name__ == "__main__":
    unittest.main()
