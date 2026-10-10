# Downetic

<p><img src="downloader_app/assets/icons/main_ico.png" alt="Downetic app icon" width="150"></p>

**A straightforward download manager for Windows.**

Downetic brings regular downloads and media links together in one desktop app.
Paste links, scan pages for media, and keep an eye on active and finished
downloads from a single place.

> **Beta:** Downetic is still being tested and improved. Some sites or media
> formats may not work as expected. If you run into a problem, please report it
> from the Help tab.

## Download

Get the ready-to-use Windows build from
[Downetic Releases](https://github.com/taiguls67/downetic/releases/latest).
Download the latest release and follow any instructions included with it.

## What it can do

- Add one or more HTTP or HTTPS links, including links copied to the clipboard.
- Scan a page for direct video, audio, and streaming links. Filter results by
  file type or host, search them, then queue, open, or copy selected links.
- Download files over HTTP, resuming partial downloads when the server supports
  it, or use yt-dlp for supported video and audio sites.
- Pause, resume, cancel, retry, and manage several downloads at once.
- Choose a download folder and save download history and preferences locally.
- Use the interface in any of 20 languages.

Page scanning reads the page's HTML. Media added later by page scripts may not
be found. Site support depends on the site and the media format.

## YouTube and other video sites

Video downloads use yt-dlp. YouTube may also need a JavaScript runtime:
[Node.js](https://nodejs.org/) LTS is recommended; Deno and Bun are also
supported when available on `PATH`.

## Run from source

1. Install 64-bit Python 3.12 or newer from
   [python.org](https://www.python.org/downloads/windows/). Make sure Tcl/Tk is
   included.
2. Open PowerShell in the project folder and install the dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

3. Start Downetic:

   ```powershell
   python main.py
   ```

## Build a Windows executable

With the virtual environment activated, run:

```powershell
python -m PyInstaller --noconfirm --clean --windowed --name Downetic `
  --icon "downloader_app\assets\icons\main_ico.ico" `
  --add-data "downloader_app\assets;downloader_app\assets" `
  --add-data "downloader_app\locales;downloader_app\locales" `
  --collect-all yt_dlp --collect-all yt_dlp_ejs main.py
```

The app is created in `dist\Downetic\Downetic.exe`. Add `--onefile` if you
prefer a single executable; its first launch may take longer. YouTube downloads
still require a separately installed JavaScript runtime.

## Files and privacy

Downloads are saved in the folder you choose. Settings and download history
are stored locally under `%LOCALAPPDATA%\Downetic`. Downetic continues to use
the old `%LOCALAPPDATA%\ClassicDownloadManager` data folder if it exists and
the Downetic data folder has not yet been created.

## Tests

Run the test suite from the project folder:

```powershell
python -m unittest discover -s tests -v
```

The HTTP download tests use a local temporary server; they do not contact an
external download service.

## Credits

Toolbar icons are from
[diinki Aero Icons](https://github.com/Aneko-san/diinki-aero-icons-1.5-remix).
The included MIT license and source attribution are in
`downloader_app\assets\aero`.
