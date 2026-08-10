# osu!taiko Mapping Tools

A Windows desktop tool for osu!taiko mappers, built from the wireframes:
metadata manager, volume/kiai copier, map cleaner, offset shifter, BG
setting, video setting, and file name checker.

This was built and tested in a Linux sandbox (Python + tkinter, headless
Xvfb + unit tests for every tool's logic). I could not compile an actual
Windows `.exe` from here — you'll build that yourself on Windows with the
included script (takes about a minute, one-time).

## Quick start (run from source, fastest way to try it)

1. Install **Python 3.10+** from python.org (tick "Add to PATH" during install).
2. Install **ffmpeg** and make sure `ffmpeg` is on PATH (needed for the video
   resizer and audio spectrogram). Easiest on Windows: `winget install ffmpeg`,
   or download a build from https://www.gyan.dev/ffmpeg/builds/ and add its
   `bin` folder to PATH.
3. **Optional, for the live video-offset preview** (plays the map's audio and
   video together with a seeker + quick offset buttons): install
   [VLC Media Player](https://www.videolan.org/vlc/download-windows.html)
   matching your Python's bitness (64-bit VLC for 64-bit Python, which is the
   default for almost everyone). `python-vlc` (in requirements.txt) then
   auto-detects your VLC install through the registry — no extra PATH setup
   needed. Without VLC installed, every other tool still works fully; the
   Video Setting's Preview button will just tell you it's unavailable
   and you can type the Offset value directly instead.
4. Open a terminal in the `app` folder and run:
   ```
   pip install -r requirements.txt
   python main.py
   ```

