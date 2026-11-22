"""Small libmpv binding and song-clock-driven video preview (no tkinter)."""
import ctypes as C
import ctypes.util
import os
import sys
import time


def library_candidates():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    roots = [base, getattr(sys, "_MEIPASS", base)]
    names = ("libmpv-2.dll", "mpv-2.dll") if os.name == "nt" else ("libmpv.so.2", "libmpv.dylib")
    for root in dict.fromkeys(roots):
        for directory in (os.path.join(root, "mpv"), root):
            for name in names:
                yield os.path.join(directory, name)
    found = C.util.find_library("mpv")
    if found:
        yield found
    yield from names


class _Event(C.Structure):
    _fields_ = [("event_id", C.c_int), ("error", C.c_int),
                ("reply_userdata", C.c_uint64), ("data", C.c_void_p)]


class _EndFile(C.Structure):
    _fields_ = [("reason", C.c_int), ("error", C.c_int)]


def load_library():
    errors = []
    for path in library_candidates():
        try:
            lib = C.CDLL(path)
            signatures = {
                "mpv_create": (C.c_void_p, []),
                "mpv_initialize": (C.c_int, [C.c_void_p]),
                "mpv_set_option_string": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
                "mpv_set_property_string": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
                "mpv_get_property": (C.c_int, [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p]),
                "mpv_command": (C.c_int, [C.c_void_p, C.POINTER(C.c_char_p)]),
                "mpv_wait_event": (C.POINTER(_Event), [C.c_void_p, C.c_double]),
                "mpv_error_string": (C.c_char_p, [C.c_int]),
                "mpv_terminate_destroy": (None, [C.c_void_p]),
            }
            for name, (result, args) in signatures.items():
                fn = getattr(lib, name)
                fn.restype, fn.argtypes = result, args
            return lib
        except (OSError, AttributeError) as exc:
            errors.append(str(exc))
    raise RuntimeError("mpv could not be loaded. Install the matching libmpv build using Download Resources. " + errors[-1])


class MpvPlayer:
    def __init__(self, path, window_id=None, muted=False):
        self.lib = load_library()
        self.handle = self.lib.mpv_create()
        self.ready = self.ended = False
        if not self.handle:
            raise RuntimeError("mpv could not create a player.")
        try:
            options = {"config": "no", "terminal": "no", "input-default-bindings": "no",
                       "input-vo-keyboard": "no", "osc": "no", "osd-level": "0",
                       "keep-open": "yes", "pause": "yes", "audio-display": "no"}
            if window_id is None:
                options["vid"] = "no"
            else:
                options["wid"] = str(window_id & 0xffffffff if os.name == "nt" else window_id)
            if muted:
                options["aid"] = "no"
            for name, value in options.items():
                self.check(self.lib.mpv_set_option_string(self.handle, name.encode(), value.encode()))
            self.check(self.lib.mpv_initialize(self.handle))
            self.command("loadfile", os.path.abspath(path))
        except Exception:
            self.close()
            raise

    def check(self, result):
        if result < 0:
            raise RuntimeError("mpv: " + self.lib.mpv_error_string(result).decode("utf-8", "replace"))

    def command(self, *args):
        values = [str(arg).encode("utf-8") for arg in args] + [None]
        self.check(self.lib.mpv_command(self.handle, (C.c_char_p * len(values))(*values)))

    def set(self, name, value):
        if isinstance(value, bool):
            value = "yes" if value else "no"
        self.check(self.lib.mpv_set_property_string(self.handle, name.encode(), str(value).encode()))

    def number(self, name, default=0):
        value = C.c_double()
        return value.value if self.lib.mpv_get_property(self.handle, name.encode(), 5, C.byref(value)) >= 0 else default

    def flag(self, name):
        value = C.c_int()
        return bool(value.value) if self.lib.mpv_get_property(self.handle, name.encode(), 3, C.byref(value)) >= 0 else False

    def pump(self):
        for _ in range(100):
            event = self.lib.mpv_wait_event(self.handle, 0).contents
            if event.event_id == 0:
                break
            if event.event_id == 8:  # MPV_EVENT_FILE_LOADED
                self.ready = True
            elif event.event_id == 7:  # MPV_EVENT_END_FILE
                end = C.cast(event.data, C.POINTER(_EndFile)).contents
                if end.reason == 4:  # MPV_END_FILE_REASON_ERROR
                    self.check(end.error)
                self.ended = True
        return self.ready

    def seek(self, milliseconds):
        self.ended = False
        self.command("seek", max(0, milliseconds) / 1000, "absolute+exact")

    def close(self):
        if self.handle:
            self.lib.mpv_terminate_destroy(self.handle)
            self.handle = None


class PreviewPlayback:
    """Positive osu! offsets delay video; negative offsets skip its beginning.

    Audio is the master clock. No delayed Tk play callbacks can outlive a seek,
    pause or close. Media is loaded paused before either player starts.
    """
    def __init__(self, video, audio=None, offset=0, clock=time.monotonic):
        self.video, self.audio, self.offset = video, audio, offset
        self.clock = clock
        self.paused = False
        self.ready = False
        self.position = 0
        self.duration = 0
        self.video_visible = False
        self._target = 0
        self._anchor = self._loaded_at = clock()
        self._last_seek = -float("inf")

    def set_volume(self, volume):
        (self.audio or self.video).set("volume", max(0, min(100, volume)))

    def seek(self, milliseconds):
        target = max(0, milliseconds)
        if self.duration:
            target = min(target, self.duration)
        self.position = self._target = target
        self._anchor = self.clock()
        if self.ready:
            if self.audio:
                self.audio.seek(target)
            if target < self.offset:
                self.video.seek(0)
            self._last_seek = self.clock()
            self._sync(force=True)

    def toggle_pause(self):
        self.tick()
        self.paused = not self.paused
        self._target, self._anchor = self.position, self.clock()
        if self.ready:
            if self.audio:
                self.audio.set("pause", self.paused)
            self._sync()

    def restart(self, offset):
        self.offset = offset
        if self.ready and not self.audio:
            self.duration = max(0, self.video.number("duration") * 1000 + offset)
        self.paused = False
        self.seek(0)
        if self.ready and self.audio:
            self.audio.set("pause", False)

    def _sync(self, force=False):
        target = self.position - self.offset
        self.video_visible = target >= 0
        video_end = self.video.number("duration") * 1000
        at_end = video_end > 0 and target >= video_end
        if at_end:
            target = max(0, video_end - 1)
        self.video.set("pause", self.paused or not self.video_visible or at_end)
        if self.video_visible and (force or self.clock() - self._last_seek > .5):
            actual = self.video.number("time-pos") * 1000
            if force or abs(actual - target) > 150:
                self.video.seek(target)
                self._last_seek = self.clock()

    def tick(self):
        video_ready = self.video.pump()
        audio_ready = self.audio.pump() if self.audio else True
        if not self.ready:
            if not (video_ready and audio_ready):
                if self.clock() - self._loaded_at > 20:
                    raise RuntimeError("mpv timed out opening the selected media.")
                return
            self.ready = True
            self.duration = (self.audio.number("duration") * 1000 if self.audio else
                             max(0, self.video.number("duration") * 1000 + self.offset))
            self.seek(self._target)
            if self.audio:
                self.audio.set("pause", self.paused)
        elif not self.paused:
            if self.audio:
                self.position = self.audio.number("time-pos", self.position / 1000) * 1000
            else:
                self.position = self._target + (self.clock() - self._anchor) * 1000
            ended = (self.audio and self.clock() - self._last_seek > .5
                     and self.audio.flag("eof-reached"))
            if self.duration and (self.position >= self.duration - 1 or ended):
                self.position = self.duration
                self.paused = True
                if self.audio:
                    self.audio.set("pause", True)
        self._sync()

    def close(self):
        try:
            self.video.close()
        finally:
            if self.audio:
                self.audio.close()
