# Downetic

<p align="center">
  <img src="downloader_app/assets/icons/text_ico.png" alt="Downetic app logo" width="600">
</p>

<p align="center">
  <a href="https://github.com/taiguls67/downetic/releases"><img src="https://img.shields.io/github/v/release/taiguls67/downetic?include_prereleases&label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2B-blue" alt="Platform: Windows 10 and newer">
  <img src="https://img.shields.io/badge/torrents-libtorrent-brightgreen" alt="Torrent downloads powered by libtorrent">
  <img src="https://img.shields.io/badge/languages-20-brightgreen" alt="Available in 20 languages">
</p>

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
At startup, Downetic checks GitHub for a newer release and can open the Releases
page when you choose to view it; updates are not installed automatically.

## What it can do

- Add one or more HTTP, HTTPS, magnet, or `.torrent` links, including links
  copied to the clipboard.
- Scan a page for direct video, audio, and streaming links. Filter results by
  file type or host, search them, then queue, open, or copy selected links.
- Download files over HTTP, resuming partial downloads when the server supports
  it, use yt-dlp for supported video and audio sites, or download torrents
  through libtorrent.
- Pause, resume, cancel, retry, and manage several downloads at once.
- Choose a download folder and save download history and preferences locally.
- See a Windows notification when a download finishes.
- Use the interface in any of 20 languages.

Page scanning checks media tags, lazy-loaded sources, player URLs in page
scripts, and embedded player frames (up to two levels deep). It also uses
yt-dlp to recognize supported video pages even when playback has not started.
If Chrome or Edge is installed, it also opens pages headlessly and checks
JavaScript-generated markup and media-stream requests, without clicking Play.
The browser scan uses a temporary profile and does not reuse browser sessions.
Media loaded only after user interaction may still not be found. Site support
depends on the site and the media format.

## Screenshots

**Link grabber**

<p><img src="https://i.imgur.com/30MMJt8.png" alt="Downetic link grabber" width="900"></p>

**Completed download**

<p><img src="https://i.imgur.com/goKqxpq.png" alt="Downetic showing a completed download" width="900"></p>

## YouTube and other video sites

Video downloads use yt-dlp, which includes extractors for TikTok video pages and
TikTok share links (`vm.tiktok.com`, `vt.tiktok.com`, and `tiktok.com/t/...`).
Paste a link to one video; profile, tag, music, and collection pages are not
downloaded automatically. TikTok may change its service, so availability can
vary over time. If TikTok rejects an anonymous request, choose Chrome or Edge
under **Settings > Browser cookies** and retry. This opt-in setting applies to
all yt-dlp video-site downloads, not only TikTok. yt-dlp reads cookies from
the selected browser locally; turn the setting off from the same menu whenever
you like. On Windows, if Chrome locks its cookie database, Downetic retries
using a temporary SQLite snapshot and removes that snapshot when the operation
ends. Cookies are not used for ordinary HTTP file downloads.

YouTube may also need a JavaScript runtime:
[Node.js](https://nodejs.org/) LTS is recommended; Deno and Bun are also
supported when available on `PATH`.

Enable **Settings > Download subtitles** to save available manually supplied
and automatic subtitles alongside video downloads. The default is off.

If a video download fails, Downetic gives a suggested next step for common
problems such as an unsupported URL, unavailable format, HTTP 403/429, or a
site response yt-dlp cannot parse. The original extractor details remain
included to help diagnose site-side changes.

## Torrent downloads

Downetic recognizes magnet links and `.torrent` URLs automatically. You can also
open the **Torrent** tab to paste magnet links, paste `.torrent` URLs, or choose
local `.torrent` files. Torrent downloads use libtorrent, save each torrent into
its own folder inside the selected download directory, and support pause, resume,
cancel, progress, speed, and ETA updates. Open **Torrent information** from a
torrent's context menu to select which files to download; the selection is saved
and applied while the torrent is active. The **Host** column shows one tracker
and the count of additional trackers.

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
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all libtorrent main.py
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

Before building or publishing a release, verify its version and installer
languages and licenses with the JavaScript release checker:

```powershell
node .\tests\js\sync-release-version.mjs
node .\tests\js\verify-release.mjs
```

## Files and privacy

Downloads are saved in the folder you choose. Settings and download history
are stored locally under `%LOCALAPPDATA%\Downetic`. Installing a newer release
over an existing installation reuses its install folder and preserves these
settings and download history.

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

Thanks to everyone who tests and contributes to Downetic.
