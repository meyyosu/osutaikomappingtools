@echo off
REM Run this on Windows, inside the "app" folder, with Python 3.10+ installed.
REM It installs dependencies and builds a standalone osu_taiko_helper.exe.

python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --onefile --windowed ^
    --name "osu_taiko_helper" ^
    --icon "icon.ico" ^
    --add-data "icon.png;." ^
    main.py

echo.
echo Build complete. Find osu_taiko_helper.exe in the "dist" folder.
echo Remember:
echo  - Copy ffmpeg.exe AND ffprobe.exe next to osu_taiko_helper.exe if you
echo    can — the app looks there first (before PATH) for both, and having
echo    ffprobe alongside ffmpeg keeps audio quality-preserving in more
echo    cases (e.g. the Add Silence feature). Needed for the Taiko Video
echo    Resizer too. If you skip this, none of it is fatal — the app will
echo    offer to install both automatically the moment they're needed
echo    (Add Silence, the Video Resizer's Apply, or the "The tool is not
echo    working?" link), trying winget first and falling back to a direct
echo    download if winget isn't available.
echo  - If VLC is installed system-wide (not portable), the live video
echo    preview should keep working out of the box — no copying needed.
echo    If you used a portable VLC instead, copy libvlc.dll, libvlccore.dll,
echo    and the "plugins" folder from your VLC install next to
echo    osu_taiko_helper.exe. Same as ffmpeg, this isn't required up front:
echo    opening the preview without VLC offers to install it automatically
echo    too.
pause
