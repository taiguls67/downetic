# Downetic

A small Windows download manager built with Python 3.12+, Tkinter, SQLite,
Requests, and yt-dlp.

The app has Downloads, Link Grabber, Help, and History views, plus a toolbar
for common actions. Choose the interface language from Settings > Language.
History is available from the Help menu.

Toolbar icons use the diinki Aero icon pack, with its included MIT license and
source attribution in `downloader_app\assets\aero`.

## Features

- Choose one of 20 interface languages from Settings > Language. The selection
  is saved between runs; locale JSON resources are in `downloader_app\locales`.
- The Help tab has ways to report problems, share ideas, and work on
  translations. Empty Downloads, Link Grabber, and History views show a small
  monochrome illustration and a short hint.
- Paste multiple links in Link Grabber; duplicate HTTP/HTTPS links are removed.
- Scan a web page for embedded audio/video/stream sources and direct media-file
  links, then filter by type or host, search by name/host, and select results to
  queue. Selected links can also be opened or copied directly from the results.
  Scanning reports page progress and continues when an individual page fails.
  Scanning reads the page's HTML; media added dynamically by page scripts may
  not be detected.
- Queue, pause, resume, cancel, and retry transfers.
- HTTP downloads resume from `.part` files when the server supports byte ranges.
- HTTP requests use a browser-compatible User-Agent for hosts that reject the
  default Python Requests identity.
- yt-dlp handles compatible video/audio websites. `Auto` uses HTTP for common
  direct-file extensions and yt-dlp for other links; select HTTP or Video to
  override this choice.
- HLS `.m3u8` and DASH `.mpd` manifests are downloaded as media by yt-dlp, even
  if HTTP is selected, rather than saving the manifest text as a file.
- YouTube downloads use an installed JavaScript runtime (Node.js, Deno, or Bun)
  to solve player challenges. Node.js LTS is recommended; the app detects it
  automatically from `PATH`.
- Choose Original, MP3, MP4, or WebM for yt-dlp downloads in Link Grabber.
  The selection is saved with each queued download.
- Configure the target directory and up to eight simultaneous downloads.
- File, Settings, Tools, and Help menus provide clipboard import,
  selected-link actions, history access, and quick access to format and concurrency.
- SQLite saves settings and download history between runs.
- Download work runs off the Tkinter UI thread.

## Install and run

1. Install 64-bit Python 3.12 or newer from [python.org](https://www.python.org/downloads/windows/).
   Enable the Python launcher and Tcl/Tk components in the installer.
2. Open PowerShell in this project directory.
3. Create and activate a virtual environment, then install dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install --upgrade -r requirements.txt
   ```

4. Launch the application:

   ```powershell
   python main.py
   ```

Install [Node.js LTS](https://nodejs.org/) and restart the application if
YouTube downloads report that no JavaScript runtime is available.

Downloaded files go to the chosen directory. The SQLite database and settings
are stored under `%LOCALAPPDATA%\Downetic`. Existing installations continue
using `%LOCALAPPDATA%\ClassicDownloadManager` when that legacy data folder is
present and no Downetic folder exists.

## Build a standalone Windows executable

From the activated environment, run:

```powershell
python -m PyInstaller --noconfirm --clean --windowed --name Downetic `
  --icon "downloader_app\assets\icons\main_ico.ico" `
  --add-data "downloader_app\assets;downloader_app\assets" `
  --add-data "downloader_app\locales;downloader_app\locales" `
  --collect-all yt_dlp --collect-all yt_dlp_ejs main.py
```

The executable is created at `dist\Downetic\Downetic.exe`.
For a single-file executable, add `--onefile` to the command. The first launch
can take longer while the one-file bundle extracts.
Node.js, Deno, or Bun must still be installed separately for YouTube downloads.
The Windows executable uses the multi-size icon converted from `main_ico.png`;
the app uses the PNG for its window icon when run from source.

## Tests

Run the focused local HTTP downloader tests (they use an ephemeral local server):

```powershell
python -m unittest discover -s tests -v
```

No external download service is used by the tests.
