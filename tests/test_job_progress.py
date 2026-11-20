"""Progress reporting follows real work and stays isolated per worker."""
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic


class JobProgressTests(unittest.TestCase):
    def test_reporter_is_thread_local_and_restored_after_error(self):
        values, other = [], []
        with logic.progress_reporting(values.append):
            logic.report_job_progress(0.25)
            def worker():
                with logic.progress_reporting(other.append):
                    logic.report_job_progress(0.75)
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            with self.assertRaises(ValueError):
                with logic.progress_reporting(other.append):
                    raise ValueError("failed stage")
            logic.report_job_progress(0.5)
        logic.report_job_progress(0.9)
        self.assertEqual(values, [0.25, 0.5])
        self.assertEqual(other, [0.75])

    def test_download_uses_received_bytes_and_preserves_file(self):
        payload = b"x" * 150_000
        response = BytesIO(payload)
        response.headers = {"Content-Length": str(len(payload))}
        values = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "asset.zip"
            with patch.object(logic.urllib.request, "urlopen", return_value=response), \
                    logic.progress_reporting(values.append):
                logic._download_to_file("https://example.com/asset.zip", str(path))
            self.assertEqual(path.read_bytes(), payload)
        self.assertAlmostEqual(values[0], 4096 / len(payload))
        self.assertTrue(any(0.1 < value < 0.9 for value in values if value is not None))
        self.assertEqual(values[-2:], [0.99, None])

    def test_unknown_download_length_stays_indeterminate(self):
        response = BytesIO(b"content")
        response.headers = {}
        values = []
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(logic.urllib.request, "urlopen", return_value=response), \
                    logic.progress_reporting(values.append):
                logic._download_to_file("https://example.com/file", str(Path(folder) / "file"))
        self.assertEqual(values, [None, None])


if __name__ == "__main__":
    unittest.main()
