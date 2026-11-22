"""Audio edits and preview use the real encoder and native playback graph."""
from array import array
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic
from osu_parser import Beatmap
from bass_audio import BassPlayer
from screens import OffsetShifterFrame, _checkbox_icon_cache


class AudioFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.audio = self.folder / "song.wav"
        samples = array("h")
        for i in range(48000):
            value = int(3000 * math.sin(2 * math.pi * 440 * i / 48000))
            samples.extend((value, value // 2))
        with wave.open(str(self.audio), "wb") as dst:
            dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            dst.writeframes(samples.tobytes())
        for name in ("Oni", "Muzukashii"):
            (self.folder / f"{name}.osu").write_text(
                f"osu file format v14\n\n[General]\nAudioFilename: song.wav\nPreviewTime: 500\n"
                f"\n[Metadata]\nVersion:{name}\n\n[Difficulty]\nSliderMultiplier:1.4\n"
                "\n[TimingPoints]\n0,500,4,1,0,100,1,0\n\n[HitObjects]\n256,192,500,1,0,0:0:0:0:\n",
                encoding="utf-8")


@unittest.skipUnless(logic.ffmpeg_available(), "ffmpeg unavailable")
class AudioProcessingTests(AudioFixture):
    def peak(self):
        with wave.open(str(self.audio), "rb") as src:
            samples = array("h")
            samples.frombytes(src.readframes(src.getnframes()))
        return max(map(abs, samples))

    def test_gain_changes_audio_without_moving_objects(self):
        before = self.peak()
        logic.adjust_audio_volume(str(self.folder), 6)
        self.assertAlmostEqual(self.peak() / before, 10 ** (6 / 20), delta=.01)
        self.assertEqual(Beatmap(str(self.folder / "Oni.osu")).hit_objects[0].time, 500)
        self.assertFalse(list(self.folder.glob("*.tmp_*")))

    def test_volume_handles_audio_named_only_dot_mp3(self):
        # osu! mapsets can contain a song literally named ".mp3". splitext
        # treats that as a basename, leaving FFmpeg without an output format.
        dot_audio = self.folder / ".mp3"
        logic._run_ffmpeg([logic._resolve_binary("ffmpeg"), "-y", "-i", str(self.audio),
                           "-b:a", "192k", "-f", "mp3", str(dot_audio)])
        for path in self.folder.glob("*.osu"):
            path.write_text(path.read_text(encoding="utf-8").replace("song.wav", ".mp3"),
                            encoding="utf-8")
        before = dot_audio.read_bytes()
        self.assertEqual(logic.adjust_audio_volume(str(self.folder), -6), ".mp3")
        self.assertNotEqual(dot_audio.read_bytes(), before)
        self.assertEqual(logic._probe_audio(str(dot_audio))["codec_name"], "mp3")
        self.assertFalse([p for p in self.folder.iterdir() if ".tmp_volume_" in p.name])

    def test_custom_silence_shifts_only_selected_diff(self):
        logic.apply_offset(str(self.folder), ["Oni.osu"], 0, True, silence_ms=350)
        self.assertEqual(Beatmap(str(self.folder / "Oni.osu")).hit_objects[0].time, 850)
        self.assertEqual(Beatmap(str(self.folder / "Muzukashii.osu")).hit_objects[0].time, 500)
        with wave.open(str(self.audio), "rb") as src:
            self.assertAlmostEqual(src.getnframes() / src.getframerate(), 1.35, places=2)

    def test_invalid_settings_leave_audio_untouched(self):
        before = self.audio.read_bytes()
        for db in (-16, 16, float("nan")):
            with self.assertRaises(ValueError):
                logic.adjust_audio_volume(str(self.folder), db)
        with self.assertRaises(ValueError):
            logic.apply_offset(str(self.folder), ["Oni.osu"], 0, True, silence_ms=99)
        self.assertEqual(self.audio.read_bytes(), before)


@unittest.skipUnless(os.name == "nt" and logic.bass_available(), "BASS unavailable")
class AudioSettingsUITests(AudioFixture):
    def setUp(self):
        super().setUp()
        _checkbox_icon_cache.clear()
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)
        self.root.geometry("1000x780")
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        alerts = patch("screens._show_alert", side_effect=lambda *args: self.fail(str(args)))
        alerts.start()
        self.addCleanup(alerts.stop)
        self.app = SimpleNamespace(get_diff_files=lambda: (str(self.folder), ["Oni.osu", "Muzukashii.osu"]))
        self.app.run_cancellable_job = lambda msg, work, on_success, **kw: on_success(work(threading.Event()))
        self.frame = OffsetShifterFrame(self.root, self.app)
        self.frame.pack(fill="both", expand=True)
        self.frame.refresh()
        self.root.update()

    def test_option_states_and_ranges(self):
        self.assertFalse(self.frame.custom_silence_check.enabled)
        self.assertEqual(str(self.frame.gain_spinner.entry["state"]), "disabled")
        self.frame.add_silence_var.set(True)
        self.frame.custom_silence_var.set(True)
        self.frame.volume_enabled_var.set(True)
        self.frame._sync_audio_state()
        self.assertEqual(str(self.frame.silence_spinner.entry["state"]), "normal")
        self.assertEqual(str(self.frame.volume_preview_btn["state"]), "normal")
        self.frame.silence_duration_var.set("9000")
        self.frame.gain_db_var.set("-18")
        self.frame._clamp_audio_values()
        self.assertEqual(self.frame.silence_duration_var.get(), "2000")
        self.assertEqual(self.frame.gain_db_var.get(), "-15")
        self.assertFalse(self.errors)

    def test_preview_seek_apply_and_close_release_resources(self):
        self.frame.open_volume_preview()
        win = self.frame._volume_preview_win
        self.addCleanup(lambda: win.close() if win.winfo_exists() else None)
        self.root.update()
        self.frame.open_volume_preview()
        self.assertIs(win, self.frame._volume_preview_win)
        win.monitor_var.set(0)  # Native playback stays silent during tests.
        win.gain_var.set(6)
        win._gain_changed()
        win._seek(SimpleNamespace(x=win.wave.winfo_width() * .25))
        self.assertAlmostEqual(win._position, 500, delta=3)
        win._toggle()
        deadline = time.monotonic() + .15
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        win._toggle()
        self.assertGreater(win._player.get_time(), 500)
        player = win._player
        win._apply()
        self.assertEqual(float(self.frame.gain_db_var.get()), 6)
        self.assertTrue(player._closed)
        self.assertIsNone(win._job)
        self.assertFalse(self.errors)

    def test_preview_gain_amplifies_native_pcm(self):
        def read_peak(db):
            player = BassPlayer(self.audio, decode=True)
            try:
                player.set_preview_gain(db)
                return max(map(abs, player.read(4096)))
            finally:
                player.close()
        self.assertAlmostEqual(read_peak(6) / read_peak(0), 10 ** (6 / 20), delta=.01)

    def test_waveform_drag_moves_timeline_and_alt_wheel_zooms(self):
        self.frame.open_volume_preview()
        win = self.frame._volume_preview_win
        self.addCleanup(lambda: win.close() if win.winfo_exists() else None)
        self.root.update()
        win._duration = 60000
        win._position = 20000
        win._view_start = 18000
        width = win.wave.winfo_width()
        positions = [win._position]
        with patch.object(win._player, "seek", side_effect=positions.append), \
             patch.object(win._player, "get_time", side_effect=lambda: positions[-1]):
            win.wave.event_generate("<Button-1>", x=width // 2, y=50)
            self.assertEqual(positions, [20000])  # Pressing never jumps.
            win.wave.event_generate("<B1-Motion>", x=width // 2 - 76, y=50)
            self.assertGreater(win._view_start, 18000)
            self.assertAlmostEqual(win._position - win._view_start, 2000)
            win.wave.event_generate("<ButtonRelease-1>", x=width // 2 - 76, y=50)
        anchor_before = win._position
        win.wave.event_generate("<Alt-MouseWheel>", x=width // 2, delta=120)
        self.assertAlmostEqual(win._span, 8000)
        self.assertAlmostEqual(win._view_start + win._span * win.PLAYHEAD_FRACTION, anchor_before)
        win.wave.event_generate("<Alt-MouseWheel>", x=width // 2, delta=-120)
        self.assertAlmostEqual(win._span, 10000)
        for _ in range(40):
            win._zoom_wave(SimpleNamespace(x=width // 2, delta=120))
        self.assertEqual(win._span, 250)
        self.assertFalse(self.errors)

    def test_preview_sliders_jump_on_click_and_meters_have_requested_labels(self):
        self.frame.open_volume_preview()
        win = self.frame._volume_preview_win
        self.addCleanup(lambda: win.close() if win.winfo_exists() else None)
        self.root.update()
        for scale, fraction, expected, var in (
                (win.gain_scale, .8, 9, win.gain_var),
                (win.monitor_scale, .2, 20, win.monitor_var)):
            lo, hi = float(scale.cget("from")), float(scale.cget("to"))
            self.assertEqual(scale.winfo_class(), "TScale")
            self.assertEqual(scale.cget("style"), "Light.Horizontal.TScale")
            right, y = scale.winfo_width(), scale.winfo_height() // 2
            x = round(fraction * right)
            scale.event_generate("<Button-1>", x=x, y=y)
            self.assertAlmostEqual(var.get(), expected, delta=1)
            scale.event_generate("<B1-Motion>", x=right, y=y)
            self.assertAlmostEqual(var.get(), hi, delta=.1)
            scale.event_generate("<ButtonRelease-1>", x=right, y=y)
        win._draw_meters((.5, .25))
        labels = [win.meter.itemcget(item, "text") for item in win.meter.find_all()
                  if win.meter.type(item) == "text"]
        self.assertEqual(labels, ["L", "R", "-60", "-48", "-36", "-24", "-12", "0"])
        self.assertFalse(self.errors)

    def test_playhead_stays_fixed_at_song_start_middle_end_and_after_zoom(self):
        self.frame.open_volume_preview()
        win = self.frame._volume_preview_win
        self.addCleanup(lambda: win.close() if win.winfo_exists() else None)
        self.root.update()
        def assert_fixed():
            cursor = win.wave.coords("playhead")
            self.assertAlmostEqual(cursor[0], win.wave.winfo_width() * win.PLAYHEAD_FRACTION)
            self.assertAlmostEqual(win._view_start + win._span * win.PLAYHEAD_FRACTION, win._position)
        for position in (0, 500, win._duration):
            win._seek_time(position)
            assert_fixed()
            win._zoom_wave(SimpleNamespace(x=0, delta=120))
            assert_fixed()
        win._scroll("moveto", "0")
        self.assertAlmostEqual(win._position, 0)
        assert_fixed()
        win._scroll("moveto", str(win._duration / (win._duration + win._span)))
        self.assertAlmostEqual(win._position, win._duration, delta=1)
        assert_fixed()
        self.assertFalse(self.errors)

    def test_apply_processes_shared_audio_once_and_passes_custom_duration(self):
        self.frame.add_silence_var.set(True)
        self.frame.custom_silence_var.set(True)
        self.frame.silence_duration_var.set("750")
        self.frame.volume_enabled_var.set(True)
        self.frame.gain_db_var.set("-3")
        with patch.object(logic, "adjust_audio_volume") as gain, \
             patch.object(logic, "apply_offset") as shift, \
             patch.object(self.frame, "notify_done"):
            self.frame.apply()
        gain.assert_called_once_with(str(self.folder), -3)
        shift.assert_called_once()
        self.assertEqual(set(shift.call_args.args[1]), {"Oni.osu", "Muzukashii.osu"})
        self.assertEqual(shift.call_args.kwargs["silence_ms"], 750)

    def test_closing_preview_discards_gain_and_map_change_releases_player(self):
        self.frame.open_volume_preview()
        win = self.frame._volume_preview_win
        win.gain_var.set(-8)
        player = win._player
        self.frame.on_map_changed()
        self.assertTrue(player._closed)
        self.assertFalse(win.winfo_exists())
        self.assertEqual(self.frame.gain_db_var.get(), "0")


if __name__ == "__main__":
    unittest.main()
