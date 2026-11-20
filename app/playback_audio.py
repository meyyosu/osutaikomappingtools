"""Prepare pitch-preserved slow audio; keep live BASS output free of stretching."""
from functools import lru_cache
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


@lru_cache(maxsize=8)
def tempo_filter(ffmpeg, rate):
    result = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if b"rubberband" not in result.stdout:
        raise RuntimeError("Pitch-preserving slow playback needs an FFmpeg build with the rubberband filter.")
    # FFmpeg invokes Rubber Band R2 in realtime mode. With a short 1024-sample
    # window at 48kHz and pitch=1, its documented start pad and output delay
    # are both 512 samples. Supply/trim those explicitly to retain beat timing.
    return (f"aresample=48000,adelay=512S:all=1,rubberband=tempo={rate}:"
            "pitchq=quality:channels=together:transients=crisp:detector=percussive:"
            "window=short,atrim=start_sample=512,asetpts=PTS-STARTPTS")


class PreparedAudioCache:
    def __init__(self):
        self._paths = {}
        self._lock = threading.Lock()
        self._generation = 0

    def _key(self, source, rate):
        stat = os.stat(source)
        return str(Path(source).resolve()), stat.st_mtime_ns, stat.st_size, rate

    def get(self, source, rate):
        if rate == 1:
            return source
        with self._lock:
            path = self._paths.get(self._key(source, rate))
        return path if path and os.path.isfile(path) else None

    def prepare(self, source, rate, ffmpeg, cancel):
        if rate not in (.25, .5, .75, 1):
            raise ValueError("Unsupported playback rate")
        existing = self.get(source, rate)
        if existing:
            return existing
        key = self._key(source, rate)
        with self._lock:
            generation = self._generation
        fd, path = tempfile.mkstemp(prefix="osu_taiko_quality_", suffix=".wav")
        os.close(fd)
        try:
            cmd = [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", source, "-vn",
                   "-af", tempo_filter(ffmpeg, rate), "-c:a", "pcm_s16le", "-rf64", "auto", path]
            if cancel.is_set():
                raise RuntimeError("Playback preparation cancelled")
            with tempfile.TemporaryFile() as errors:
                with subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errors,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as proc:
                    deadline = time.monotonic() + 600
                    while proc.poll() is None:
                        if cancel.wait(.05) or time.monotonic() >= deadline:
                            proc.kill()
                            proc.wait()
                            raise RuntimeError("Playback preparation cancelled or timed out")
                    if proc.returncode:
                        errors.seek(0)
                        raise RuntimeError(errors.read().decode("utf-8", "replace").strip() or "Audio preparation failed")
            with self._lock:
                if cancel.is_set() or generation != self._generation:
                    raise RuntimeError("Playback preparation cancelled")
                old = self._paths.get(key)
                self._paths[key] = path
            if old and old != path:
                self._remove(old)
            return path
        except BaseException:
            self._remove(path)
            raise

    @staticmethod
    def _remove(path):
        try:
            os.unlink(path)
        except OSError:
            pass

    def clear(self):
        with self._lock:
            self._generation += 1
            paths = list(self._paths.values())
            self._paths.clear()
        for path in paths:
            self._remove(path)
