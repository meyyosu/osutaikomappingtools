"""Stretch the song, then mix untouched clicks on one BASS output clock."""
import ctypes as c
import os
from pathlib import Path
import sys
import threading

DECODE = 0x200000
FLOAT = 0x100
PRESCAN = 0x20000
UNICODE = 0x80000000
MIXER_END = 0x10000
POS_FLUSH = 0x1000000
ATTRIB_VOL = 2
ATTRIB_TEMPO = 0x10000
ATTRIB_SRC = 8
TEMPO_SHANNON = 0x800
QWORD_ERROR = (1 << 64) - 1
DLL_NAMES = ("bass.dll", "bassmix.dll", "bass_fx.dll")


def library_directory():
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).parent / "bass")
    if hasattr(sys, "_MEIPASS"):
        roots.append(Path(sys._MEIPASS) / "bass")
    roots.append(Path(__file__).resolve().parent / "bass")
    for root in roots:
        for folder in (root, root / "x64") if c.sizeof(c.c_void_p) == 8 else (root,):
            if all((folder / name).is_file() for name in DLL_NAMES):
                return folder
    raise OSError("BASS audio libraries are missing. Install BASS through Settings.")


class BassError(RuntimeError):
    pass


class BassApi:
    def __init__(self):
        if os.name != "nt":
            raise OSError("BASS playback is currently supported on Windows")
        folder = library_directory()
        self._dll_path = os.add_dll_directory(str(folder))
        self.bass = c.WinDLL(str(folder / "bass.dll"))
        self.mix = c.WinDLL(str(folder / "bassmix.dll"))
        self.fx = c.WinDLL(str(folder / "bass_fx.dll"))
        u, q, b, p, f, d = c.c_uint32, c.c_uint64, c.c_int32, c.c_void_p, c.c_float, c.c_double
        definitions = (
            (self.bass, "BASS_GetVersion", u, []),
            (self.mix, "BASS_Mixer_GetVersion", u, []),
            (self.fx, "BASS_FX_GetVersion", u, []),
            (self.bass, "BASS_Init", b, [c.c_int32, u, u, p, p]),
            (self.bass, "BASS_GetDevice", u, []),
            (self.bass, "BASS_SetDevice", b, [u]),
            (self.bass, "BASS_ErrorGetCode", c.c_int32, []),
            (self.bass, "BASS_StreamCreateFile", u, [u, p, q, q, u]),
            (self.bass, "BASS_StreamFree", b, [u]),
            (self.bass, "BASS_ChannelGetLength", q, [u, u]),
            (self.bass, "BASS_ChannelGetPosition", q, [u, u]),
            (self.bass, "BASS_ChannelSetPosition", b, [u, q, u]),
            (self.bass, "BASS_ChannelSeconds2Bytes", q, [u, d]),
            (self.bass, "BASS_ChannelBytes2Seconds", d, [u, q]),
            (self.bass, "BASS_ChannelSetAttribute", b, [u, u, f]),
            (self.bass, "BASS_ChannelPlay", b, [u, b]),
            (self.bass, "BASS_ChannelPause", b, [u]),
            (self.bass, "BASS_ChannelStop", b, [u]),
            (self.bass, "BASS_ChannelIsActive", u, [u]),
            (self.bass, "BASS_ChannelGetData", u, [u, p, u]),
            (self.mix, "BASS_Mixer_StreamCreate", u, [u, u, u]),
            (self.mix, "BASS_Mixer_StreamAddChannel", b, [u, u, u]),
            (self.mix, "BASS_Mixer_ChannelSetPosition", b, [u, q, u]),
            (self.fx, "BASS_FX_TempoCreate", u, [u, u]),
        )
        for dll, name, result, args in definitions:
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)
        for name in ("BASS_GetVersion", "BASS_Mixer_GetVersion", "BASS_FX_GetVersion"):
            if getattr(self, name)() >> 16 != 0x204:
                raise BassError(f"{name}: BASS 2.4 libraries are required")
        self._devices = {}

    def check(self, result, operation):
        if not result:
            raise BassError(f"{operation} failed (BASS error {self.BASS_ErrorGetCode()})")
        return result

    def device(self, decode):
        if decode not in self._devices:
            if not self.BASS_Init(0 if decode else -1, 48000, 0, None, None):
                if self.BASS_ErrorGetCode() != 14:  # BASS_ERROR_ALREADY
                    self.check(False, "Opening audio output")
            self._devices[decode] = self.BASS_GetDevice()
        self.check(self.BASS_SetDevice(self._devices[decode]), "Selecting audio output")


_api = None
_api_lock = threading.Lock()


def get_api():
    global _api
    with _api_lock:
        if _api is None:
            _api = BassApi()
        return _api


def available():
    try:
        get_api()
        return True
    except (OSError, AttributeError, BassError):
        return False


class BassPlayer:
    """Only the song enters the tempo processor; clicks retain their timbre.

    Sources retain independent gains. No timers schedule individual clicks, no
    second output device is involved, and all public times use source-song ms.
    decode=True is a silent native integration-test path using the same graph.
    """

    def __init__(self, song_path, metronome_path=None, rate=1.0, *, decode=False, api=None, prepared_song=False):
        self.api = api or get_api()
        self.api.device(decode)
        self._handles = []
        self.output = self.mixer = self.tempo = self.song = self.metronome = 0
        self.rate = None
        self._song_gain = 1.0
        self._metro_enabled = bool(metronome_path)
        self._base_ms = 0.0
        self._decode = decode
        self._closed = False
        self._prepared_song = prepared_song
        self._prepared_rate = rate
        try:
            self.song = self._open_file(song_path)
            length = self.api.BASS_ChannelGetLength(self.song, 0)
            if length == QWORD_ERROR:
                raise BassError("Could not determine song duration")
            self.duration_ms = self.api.BASS_ChannelBytes2Seconds(self.song, length) * 1000
            if prepared_song:
                self.duration_ms *= rate
            if callable(metronome_path):
                metronome_path = metronome_path(self.duration_ms, rate)
            self.tempo = self.song if prepared_song else self._own(self.api.BASS_FX_TempoCreate(self.song, FLOAT | DECODE | TEMPO_SHANNON), "Creating song tempo stream")
            # Full quality processing, automatic music windows, and click
            # prevention. The metronome never passes through this algorithm.
            for attribute, value in (() if prepared_song else ((0x10010, 1), (0x10011, 64), (0x10012, 0),
                                     (0x10013, 0), (0x10014, 0), (0x10016, 1))):
                self.api.check(self.api.BASS_ChannelSetAttribute(self.tempo, attribute, value), "Configuring tempo quality")
            self.mixer = self._own(self.api.BASS_Mixer_StreamCreate(48000, 2, FLOAT | MIXER_END | (DECODE if decode else 0)), "Creating output mixer")
            self.output = self.mixer
            self.api.check(self.api.BASS_ChannelSetAttribute(self.tempo, ATTRIB_SRC, 4), "Configuring song resampling")
            self.api.check(self.api.BASS_Mixer_StreamAddChannel(self.mixer, self.tempo, 0), "Adding stretched song to mixer")
            if metronome_path:
                self.metronome = self._open_file(metronome_path)
                self.api.check(self.api.BASS_ChannelSetAttribute(self.metronome, ATTRIB_SRC, 4), "Configuring click resampling")
                self.api.check(self.api.BASS_Mixer_StreamAddChannel(self.mixer, self.metronome, 0), "Adding metronome to mixer")
            self.set_rate(rate)
            self._update_headroom()
        except BaseException:
            self.close()
            raise

    def _own(self, handle, operation):
        self.api.check(handle, operation)
        self._handles.append(handle)
        return handle

    def _open_file(self, path):
        name = c.c_wchar_p(str(Path(path).resolve()))
        return self._own(self.api.BASS_StreamCreateFile(0, c.cast(name, c.c_void_p), 0, 0,
                                                       DECODE | FLOAT | PRESCAN | UNICODE), "Opening audio file")

    def set_rate(self, rate):
        if rate not in (.25, .5, .75, 1.0):
            raise ValueError("Unsupported playback rate")
        if self.metronome and self.rate is not None and rate != self.rate:
            raise ValueError("A new rate needs a matching, unstretched metronome track")
        if self._prepared_song:
            if rate != self._prepared_rate:
                raise ValueError("A new rate requires matching prepared song audio")
        else:
            self.api.check(self.api.BASS_ChannelSetAttribute(self.tempo, ATTRIB_TEMPO, (rate - 1) * 100), "Changing playback rate")
        self.rate = rate

    def set_volume(self, percent):
        self._song_gain = max(0, min(100, percent)) / 100
        self.api.check(self.api.BASS_ChannelSetAttribute(self.tempo, ATTRIB_VOL, self._song_gain), "Changing song volume")
        self._update_headroom()

    def _update_headroom(self):
        # Reserve space for two full-level sources plus a small margin. Fixed
        # gain avoids pumping and keeps the song slider from changing clicks.
        gain = .45
        self._output_gain = gain
        self.api.check(self.api.BASS_ChannelSetAttribute(self.output, ATTRIB_VOL, gain), "Reserving mix headroom")

    def set_metronome(self, enabled):
        self._metro_enabled = bool(enabled)
        if self.metronome:
            # Gain, rather than pausing the source, keeps its sample position
            # locked to the song while the checkbox is switched off.
            self.api.check(self.api.BASS_ChannelSetAttribute(self.metronome, ATTRIB_VOL, 1.0 if enabled else 0.0), "Changing metronome volume")
        self._update_headroom()

    def play(self):
        self.api.check(self.api.BASS_ChannelPlay(self.output, False), "Starting playback")

    def pause(self):
        if self.api.BASS_ChannelIsActive(self.output) in (1, 2):
            self.api.check(self.api.BASS_ChannelPause(self.output), "Pausing playback")

    def seek(self, ms):
        ms = max(0.0, min(float(ms), self.duration_ms))
        active = not self._decode and self.api.BASS_ChannelIsActive(self.output) in (1, 2)
        if active:
            self.pause()
        for handle in (self.tempo, self.metronome):
            if handle:
                length = self.api.BASS_ChannelGetLength(handle, 0)
                seconds = ms / 1000 if handle == self.tempo and not self._prepared_song else ms / (1000 * self.rate)
                position = self.api.BASS_ChannelSeconds2Bytes(handle, seconds)
                self.api.check(self.api.BASS_Mixer_ChannelSetPosition(handle, min(position, length), POS_FLUSH), "Seeking mixer source")
        # Flush the one output buffer after moving both sources and clearing
        # the song's tempo history. Mixer time starts at zero after each seek.
        self.api.check(self.api.BASS_ChannelSetPosition(self.output, 0, POS_FLUSH), "Flushing audio seek")
        self._base_ms = ms
        if active:
            self.play()

    def get_time(self):
        position = self.api.BASS_ChannelGetPosition(self.output, 0)
        if position == QWORD_ERROR:
            raise BassError("Could not read audio playback position")
        elapsed = self.api.BASS_ChannelBytes2Seconds(self.output, position) * 1000 * self.rate
        return min(self.duration_ms, self._base_ms + elapsed)

    def ended(self):
        return self.get_time() >= self.duration_ms or self.api.BASS_ChannelIsActive(self.output) == 0

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.output:
            self.api.BASS_ChannelStop(self.output)
        # No AUTOFREE/FREESOURCE flags: each native handle has one owner.
        # Detach downstream users before releasing their sources.
        for handle in (self.mixer, self.metronome, self.tempo, self.song):
            if handle and handle in self._handles:
                self.api.BASS_StreamFree(handle)
                self._handles.remove(handle)

    def read(self, frames=4096):
        """Read float stereo output without speakers (decode-only test graph)."""
        if not self._decode:
            raise RuntimeError("read requires decode=True")
        buffer = (c.c_float * (frames * 2))()
        size = self.api.BASS_ChannelGetData(self.output, buffer, c.sizeof(buffer))
        if size == 0xFFFFFFFF:
            if self.api.BASS_ErrorGetCode() == 45:  # BASS_ERROR_ENDED
                return []
            self.api.check(False, "Decoding audio")
        return list(buffer[:size // c.sizeof(c.c_float)])
