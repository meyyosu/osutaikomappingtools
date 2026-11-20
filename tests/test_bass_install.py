"""Install architecture-matched libraries and notices without ZIP path writes."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic


class BassInstallTests(unittest.TestCase):
    def test_official_packages_extract_only_matching_dlls_and_notices(self):
        def download(url, target, timeout):
            self.assertTrue(url.startswith("https://www.un4seen.com/files/"))
            name = url.rsplit("/", 1)[-1].removesuffix("24.zip")
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr(name + ".dll", b"x86")
                archive.writestr("x64/" + name + ".dll", b"x64")
                archive.writestr(name + ".txt", name + " original notice")
                archive.writestr("../../escaped.txt", b"must not extract")
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(logic, "_bundled_dir", return_value=folder), \
                patch.object(logic, "bass_available", side_effect=[False, True]), \
                patch.object(logic, "_download_to_file", side_effect=download):
            installed = Path(logic.install_bass_bundled())
            for name in ("bass", "bassmix", "bass_fx"):
                self.assertEqual((installed / (name + ".dll")).read_bytes(),
                                 b"x64" if struct.calcsize("P") == 8 else b"x86")
                self.assertEqual((installed / (name + ".txt")).read_text(), name + " original notice")
            self.assertEqual(len(list(installed.iterdir())), 6)
            self.assertFalse((Path(folder).parent / "escaped.txt").exists())


if __name__ == "__main__":
    unittest.main()
