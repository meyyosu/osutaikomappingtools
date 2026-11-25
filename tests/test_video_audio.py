"""Removing video audio preserves the source until processing succeeds."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic


class VideoAudioTests(unittest.TestCase):
    def test_success_replaces_selected_file_without_reencoding(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "selected.mp4")
            source.write_bytes(b"original")

            def remux(cmd):
                self.assertEqual(cmd[cmd.index("-i") + 1], str(source))
                self.assertEqual(cmd[cmd.index("-map") + 1], "0")
                self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
                self.assertIn("-an", cmd)
                self.assertEqual(Path(cmd[-1]).suffix, ".mp4")
                self.assertEqual(source.read_bytes(), b"original")
                Path(cmd[-1]).write_bytes(b"video without audio")

            with patch.object(logic, "ffmpeg_available", return_value=True), \
                    patch.object(logic, "_run_ffmpeg", side_effect=remux):
                self.assertEqual(logic.remove_video_audio(folder, source.name), source.name)
            self.assertEqual(source.read_bytes(), b"video without audio")
            self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_failed_remux_keeps_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "selected.avi")
            source.write_bytes(b"original")

            def fail(cmd):
                Path(cmd[-1]).write_bytes(b"partial output")
                raise subprocess.CalledProcessError(1, cmd)

            with patch.object(logic, "ffmpeg_available", return_value=True), \
                    patch.object(logic, "_run_ffmpeg", side_effect=fail):
                with self.assertRaises(subprocess.CalledProcessError):
                    logic.remove_video_audio(folder, source.name)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(list(Path(folder).iterdir()), [source])


if __name__ == "__main__":
    unittest.main()
