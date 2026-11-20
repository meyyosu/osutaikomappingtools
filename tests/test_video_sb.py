"""Storyboard video endings follow the song audio timeline."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic


class VideoSBTests(unittest.TestCase):
    def write_map(self, folder, name, last_note, audio="song.mp3"):
        path = Path(folder, name)
        path.write_text(
            "osu file format v14\n\n[General]\n"
            f"AudioFilename: {audio}\n\n[Events]\nVideo,0,\"video.mp4\"\n"
            f"\n[HitObjects]\n256,192,{last_note},1,0,0:0:0:0:\n",
            encoding="utf-8",
        )
        return path

    def test_shared_song_ending_ignores_notes_and_video_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [self.write_map(folder, "a.osu", 10000),
                     self.write_map(folder, "b.osu", 30000)]
            with patch.object(logic, "probe_media_duration_seconds", return_value=45.1235) as probe, \
                    patch.object(logic, "probe_video_height", return_value=720):
                for _ in range(2):
                    logic.apply_video_sb_code(folder, [p.name for p in paths], "video.mp4", -500)
                self.assertEqual(probe.call_count, 2)  # once per shared audio per Apply
            for path in paths:
                text = path.read_text(encoding="utf-8-sig")
                self.assertIn("Video,-500,\"video.mp4\"", text)
                self.assertEqual(text.count("F,0,45124,45124,1,0"), 1)

    def test_unknown_duration_leaves_all_diffs_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [self.write_map(folder, "a.osu", 10000),
                     self.write_map(folder, "b.osu", 30000, "missing.mp3")]
            originals = [p.read_bytes() for p in paths]
            with patch.object(logic, "probe_media_duration_seconds", side_effect=[45.0, None]):
                with self.assertRaisesRegex(ValueError, "Cannot read song duration"):
                    logic.apply_video_sb_code(folder, [p.name for p in paths], "video.mp4", 1000)
            self.assertEqual([p.read_bytes() for p in paths], originals)


if __name__ == "__main__":
    unittest.main()
