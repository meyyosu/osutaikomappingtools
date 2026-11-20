"""VLC discovery regressions without requiring a system VLC installation."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import tools_logic as logic


class VlcDetectionTests(unittest.TestCase):
    def test_skips_unloadable_bundle_and_keeps_system_dll_directory_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = [str(Path(tmp) / name) for name in ("bundle", "system")]
            for directory in dirs:
                Path(directory, "plugins").mkdir(parents=True)
                Path(directory, "libvlc.dll").touch()
            failed_cookie, good_cookie = Mock(), Mock()

            def load(path):
                if path.startswith(dirs[0]):
                    raise OSError("wrong architecture")
                return Mock()

            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(logic.os, "name", "nt"), \
                    patch.object(logic, "_vlc_install_dirs", return_value=iter(dirs)), \
                    patch.object(logic, "_vlc_dll_handles", {}), \
                    patch("os.add_dll_directory", side_effect=[failed_cookie, good_cookie], create=True), \
                    patch("ctypes.CDLL", side_effect=load), patch("os.chdir") as chdir:
                logic.configure_bundled_vlc_env()
                self.assertEqual(os.environ["PYTHON_VLC_LIB_PATH"],
                                 os.path.join(dirs[1], "libvlc.dll"))
                self.assertEqual(os.environ["PYTHON_VLC_MODULE_PATH"],
                                 os.path.join(dirs[1], "plugins"))
                failed_cookie.close.assert_called_once()
                good_cookie.close.assert_not_called()
                chdir.assert_not_called()
                self.assertIn(dirs[1], logic._vlc_dll_handles)

    def test_explicit_overrides_are_preserved(self):
        env = {"PYTHON_VLC_LIB_PATH": os.path.abspath("custom/libvlc.dll"),
               "PYTHON_VLC_MODULE_PATH": "custom-plugins"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(logic.os, "name", "nt"), \
                patch.object(logic, "_vlc_install_dirs") as discover, \
                patch("os.path.isfile", return_value=True), \
                patch("os.path.isdir", return_value=True), \
                patch("os.add_dll_directory", create=True), patch("ctypes.CDLL"), \
                patch.object(logic, "_vlc_dll_handles", {}):
            logic.configure_bundled_vlc_env()
            discover.assert_not_called()
            self.assertEqual(dict(os.environ), env)

    def test_checks_instance_and_releases_it(self):
        instance = Mock()
        with patch.object(logic, "configure_bundled_vlc_env"), \
                patch.dict(sys.modules, vlc=SimpleNamespace(Instance=Mock(return_value=instance))):
            self.assertTrue(logic.vlc_available())
            instance.release.assert_called_once()

    def test_missing_plugins_or_binding_exit_returns_false(self):
        for outcome in (None, SystemExit(1), OSError("missing DLL")):
            constructor = Mock(side_effect=outcome) if isinstance(outcome, BaseException) else Mock(return_value=outcome)
            with self.subTest(outcome=outcome), \
                    patch.object(logic, "configure_bundled_vlc_env"), \
                    patch.dict(sys.modules, vlc=SimpleNamespace(Instance=constructor)):
                self.assertFalse(logic.vlc_available())


if __name__ == "__main__":
    unittest.main()
