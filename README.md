# Downetic

<p><img src="downloader_app/assets/icons/text_ico.png" alt="Downetic app logo" width="600"></p>

**A straightforward download manager for Windows.**

Downetic brings regular downloads and media links together in one desktop app.
Paste links, scan pages for media, and keep an eye on active and finished
downloads from a single place.

> **Beta:** Downetic is still being tested and improved. Some sites or media
> formats may not work as expected. If you run into a problem, please report it
> from the Help tab.

## Download

Get the ready-to-use Windows build from
[Downetic Releases](https://github.com/taiguls67/downetic/releases).
Download the latest release and follow any instructions included with it.

## What it can do

- Add one or more HTTP or HTTPS links, including links copied to the clipboard.
- Scan a page for direct video, audio, and streaming links. Filter results by
  file type or host, search them, then queue, open, or copy selected links.
- Download files over HTTP, resuming partial downloads when the server supports
  it, or use yt-dlp for supported video and audio sites.
- Pause, resume, cancel, retry, and manage several downloads at once.
- Choose a download folder and save download history and preferences locally.
- See a Windows notification when a download finishes.
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
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Downetic `
  --icon "downloader_app\assets\icons\main_ico.ico" `
  --add-data "downloader_app\assets;downloader_app\assets" `
  --add-data "downloader_app\locales;downloader_app\locales" `
  --collect-all yt_dlp --collect-all yt_dlp_ejs main.py
```

The app is created at `dist\Downetic.exe`. Its first launch may take longer.

## Build the Windows installer

The installer interface is available in English, Italian, French, German,
Spanish, Brazilian Portuguese, and Japanese. It asks you to accept an
installation and use notice before continuing. It detects Node.js and FFmpeg
on `PATH` and lets you choose separately whether to reuse each existing
installation or download Downetic's bundled copy. The installer places
Downetic and the selected runtimes in the chosen app folder.
It downloads the Node.js LTS archive from nodejs.org and an LGPL shared FFmpeg
build from BtbN/FFmpeg-Builds during setup, so an Internet connection is
required. The archives are SHA-256 verified. The installed runtimes are local
to Downetic and do not change the system `PATH`. When you choose existing
components, Downetic uses them from the system `PATH` without changing it. The
portable executable and source builds use runtimes already installed on the
computer.

Build the executable first, install Inno Setup 6, then run the compiler from
the project folder:

```powershell
Set-Location .\installer
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\Downetic.iss
```

The installer is written to `dist\installer`. The installer supports Windows
10 version 22H2 and newer. The installed FFmpeg package contains its license;
Node.js includes its license and third-party notices. YouTube downloads still
depend on the supported site and may change over time.

## Files and privacy

Downloads are saved in the folder you choose. Settings and download history
are stored locally under `%LOCALAPPDATA%\Downetic`.

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
