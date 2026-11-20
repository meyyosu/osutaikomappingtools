"""Native, speaker-free tests of the SAME mixer/tempo graph used by the UI."""
from array import array
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import statistics
import unittest
from unittest.mock import Mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from bass_audio import BassPlayer, BassError, available, ATTRIB_VOL
from playback_audio import PreparedAudioCache


@unittest.skipUnless(os.name == "nt" and available(), "Bundled BASS libraries unavailable")
class NativeBassTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.song = Path(self.folder.name, "曲.wav")
        self.metro = Path(self.folder.name, "clicks.wav")
        self.players = []
        self.metro_paths = {}
        for path, right in ((self.song, False), (self.metro, True)):
            pcm = array("h")
            for i in range(48000 * 3):
                t = i / 48000
                pulse = int(6000 * math.sin(2 * math.pi * 1000 * t)) if any(b <= t < b + .04 for b in (.25, .75, 1.25)) else 0
                pcm.extend((0, pulse) if right else (pulse, 0))
            with wave.open(str(path), "wb") as dst:
                dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                dst.writeframes(pcm.tobytes())
        for rate in (.25, .5, .75, 1):
            path = Path(self.folder.name, f"clicks-{rate}.wav")
            pcm = array("h", [0]) * round(48000 * 3 / rate * 2)
            for beat in (.25, .75, 1.25):
                offset = round(beat * 48000 / rate)
                for i in range(1920):
                    pcm[2 * (offset + i) + 1] = int(6000 * math.sin(2 * math.pi * 1000 * i / 48000))
            with wave.open(str(path), "wb") as dst:
                dst.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                dst.writeframes(pcm.tobytes())
            self.metro_paths[rate] = path

    def tearDown(self):
        for player in self.players:
            player.close()
        self.folder.cleanup()

    def player(self, rate):
        player = BassPlayer(self.song, self.metro_paths[rate], rate, decode=True)
        self.players.append(player)
        return player

    def test_click_waveform_and_spacing_are_preserved_at_all_rates(self):
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                player = self.player(rate)
                output = []
                for _ in range(40):
                    output.extend(player.read(2048))
                self.assertGreater(max(map(abs, output)), .05)
                with wave.open(str(self.metro_paths[rate]), "rb") as wav:
                    expected = array("h")
                    expected.frombytes(wav.readframes(len(output) // 2))
                self.assertLess(max(abs(r - value / 32768) for r, value in zip(output[1::2], expected[1::2])), 1e-6)
                expected_ms = len(output) / 2 / 48000 * rate * 1000
                self.assertAlmostEqual(player.get_time(), expected_ms, delta=1)

    def test_independent_volume_and_metronome_mute_do_not_shift_either_source(self):
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                player = self.player(rate)
                player.set_volume(50)
                reference = self.player(rate)
                output = []
                original = []
                for _ in range(40):
                    output.extend(player.read(2048))
                    original.extend(reference.read(2048))
                self.assertLess(max(abs(l - r * .5) for l, r in zip(output[::2], original[::2])), 1e-5)
                player.set_metronome(False)
                player.seek(250)
                muted = player.read(8192)
                self.assertEqual(max(map(abs, muted[1::2])), 0)
                player.set_metronome(True)
                player.seek(250)
                unmuted = player.read(8192)
                self.assertGreater(max(map(abs, unmuted[1::2])), .01)

    def test_backward_seeks_and_rate_changes_flush_old_tempo_audio(self):
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                player = self.player(rate)
                player.seek(1250)
                player.read(8192)
                player.seek(2000)  # a completely silent region
                self.assertEqual(player.get_time(), 2000)
                self.assertEqual(max(map(abs, player.read(8192))), 0)
                player.seek(250)
                self.assertEqual(player.get_time(), 250)
                output = player.read(8192)
                self.assertGreater(max(map(abs, output)), .01)

    def test_clicks_do_not_affect_song_time_stretching(self):
        for rate in (.25, .5, .75, 1):
            with self.subTest(rate=rate):
                with_clicks = self.player(rate)
                without_clicks = self.player(rate)
                without_clicks.set_metronome(False)
                for _ in range(40):
                    first = with_clicks.read(2048)[::2]
                    second = without_clicks.read(2048)[::2]
                    self.assertEqual(first, second)

    def test_mix_headroom_prevents_output_clipping(self):
        player = self.player(.25)
        player.set_volume(100)
        self.assertLessEqual(2 * player._output_gain, .9)
        player.set_volume(40)
        self.assertLessEqual(1.4 * player._output_gain, .9)

    def test_prepared_song_keeps_pitch_and_transient_alignment_at_slow_rates(self):
        ffmpeg = Path(__file__).resolve().parents[1] / "app" / "ffmpeg.exe"
        if not ffmpeg.is_file():
            self.skipTest("Bundled FFmpeg unavailable")
        cache = PreparedAudioCache()
        try:
            for rate in (.25, .5, .75):
                with self.subTest(rate=rate):
                    prepared = cache.prepare(str(self.song), rate, str(ffmpeg), threading.Event())
                    player = BassPlayer(prepared, self.metro_paths[rate], rate, decode=True, prepared_song=True)
                    self.players.append(player)
                    output = []
                    for _ in range(40):
                        output.extend(player.read(2048))
                    left, right = output[::2], output[1::2]
                    song_onset = next(i for i, value in enumerate(left) if abs(value) > .01)
                    click_onset = next(i for i, value in enumerate(right) if abs(value) > .01)
                    self.assertLess(abs(song_onset - click_onset) / 48, 45)
                    crossings = [i for i in range(song_onset + 100, song_onset + 1500)
                                 if left[i-1] <= 0 < left[i]]
                    period = statistics.median(b - a for a, b in zip(crossings, crossings[1:]))
                    self.assertAlmostEqual(48000 / period, 1000, delta=25)
                    player.seek(250)
                    self.assertEqual(player.get_time(), 250)
                    chunk = player.read(4096)
                    self.assertGreater(max(map(abs, chunk[1::2])), .01)
                    player.close()
        finally:
            cache.clear()

    def test_close_is_idempotent_and_releases_file_handles(self):
        player = self.player(.5)
        player.close()
        player.close()
        self.song.unlink()
        self.metro.unlink()


class BassOwnershipTests(unittest.TestCase):
    def test_partial_graph_failure_frees_every_created_handle(self):
        api = Mock()
        api.BASS_StreamCreateFile.side_effect = [101, 102]
        api.BASS_Mixer_StreamCreate.return_value = 201
        api.BASS_FX_TempoCreate.return_value = 303
        api.BASS_Mixer_StreamAddChannel.side_effect = [True, False]
        api.BASS_ChannelGetLength.return_value = 1000
        api.BASS_ChannelBytes2Seconds.return_value = 3
        def check(result, operation):
            if not result:
                raise BassError(operation)
            return result
        api.check.side_effect = check
        with self.assertRaisesRegex(BassError, "metronome"):
            BassPlayer("song.wav", "click.wav", api=api)
        self.assertEqual([call.args[0] for call in api.BASS_StreamFree.call_args_list], [201, 102, 303, 101])

    def test_metronome_toggle_changes_gain_without_pausing_or_seeking(self):
        api = Mock()
        player = BassPlayer.__new__(BassPlayer)
        player.api, player.metronome = api, 123
        player.output, player._song_gain = 201, 1.0
        player.set_metronome(False)
        api.BASS_ChannelSetAttribute.assert_any_call(123, ATTRIB_VOL, 0)
        api.BASS_ChannelPause.assert_not_called()
        api.BASS_ChannelSetPosition.assert_not_called()


if __name__ == "__main__":
    unittest.main()
