# Downetic

<p align="center">
  <img src="downloader_app/assets/icons/text_ico.png" alt="Downetic app logo" width="600">
  <br>
  <a href="https://github.com/taiguls67/downetic/releases"><img src="https://img.shields.io/github/v/release/taiguls67/downetic?include_prereleases&label=release" alt="Latest release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2022H2%2B-blue" alt="Platform: Windows 10 version 22H2 and newer">
  <img src="https://img.shields.io/badge/torrents-libtorrent-brightgreen" alt="Torrent downloads powered by libtorrent">
  <img src="https://img.shields.io/badge/languages-20-brightgreen" alt="Available in 20 languages">
</p>

<p align="center"><strong>Downloads · Find Links · BitTorrent</strong></p>

One app for **HTTP/HTTPS files**, supported video sites, media discovered on
web pages, and **magnet or `.torrent` downloads**.

> **Beta:** Some sites and formats may change or stop working. Found a problem?
> Report it from the app's **Help** tab.

## Contents

- [Download](#download)
- [Features](#features)
- [Find Links](#find-links)
- [Torrent downloads](#torrent-downloads)
- [Video and audio sites](#video-and-audio-sites)
- [Screenshots](#screenshots)
- [Run from source](#run-from-source)
- [Build for Windows](#build-for-windows)
- [Files and privacy](#files-and-privacy)
- [Tests](#tests)

## Download

1. Download the latest **Windows installer** from
   [GitHub Releases](https://github.com/taiguls67/downetic/releases).
2. Run it and follow the prompts.
3. Open Downetic and paste a link, scan a page, or add a torrent.

Installing a newer version **keeps your settings and download history**.
Downetic can notify you when a release is available; it never installs updates
without your confirmation.

## Features

- **Downloads:** HTTP/HTTPS with resume support, plus video and audio from
  sites supported by yt-dlp.
- **Find Links:** scan a web page, filter discovered media, and queue, open, or
  copy results.
- **BitTorrent:** magnet links, `.torrent` URLs, and local torrent files, with
  per-file selection and optional VPN interface binding.
- **Manage:** pause, resume, stop, retry, inspect progress, and remove
  downloads—including partial torrent data.
- **Keep track:** download history, Windows completion notifications, and
  interface translations in **20 languages**.

**Examples:** `https://example.com/file.zip` · `magnet:?xt=urn:btih:...` ·
`https://example.com/file.torrent`

## Find Links

1. Open **Find Links** and paste a page URL.
2. Start the scan; filter results by **type** or **host**, or search by name.
3. Select a result to **download**, **open**, or **copy** its URL.

The scanner looks in media tags, lazy-loaded sources, page scripts, and embedded
players. It can also recognize yt-dlp-supported pages and, with Chrome or Edge
installed, inspect JavaScript-generated pages in a headless browser.

**Example:** Paste a page URL—not only a direct `.mp4` URL—to find media links
embedded in that page.

*Some sites load media only after interaction or restrict automated requests,
so not every link can be discovered.*

## Screenshots

**Link grabber**

<p><img src="https://i.imgur.com/30MMJt8.png" alt="Downetic link grabber" width="900"></p>

**Completed download**

<p><img src="https://i.imgur.com/goKqxpq.png" alt="Downetic showing a completed download" width="900"></p>

## Video and audio sites

Video and audio downloads use **yt-dlp**. Paste a link to one video; playlists,
profiles, and collection pages are not downloaded automatically.

- YouTube may require **Node.js LTS**, Deno, or Bun on `PATH`.
- If a site requires a signed-in session, choose **Settings → Browser cookies**
  and select Chrome or Edge. Cookies are used locally for yt-dlp downloads,
  **not** for ordinary file downloads.
- To save captions, turn on **Settings → Download subtitles**.

TikTok and other sites can change how links work; support depends on yt-dlp and
the site. If a download fails, Downetic shows troubleshooting suggestions and
the original error details.

## Torrent downloads

Powered by [libtorrent](https://www.libtorrent.org/). Paste a magnet link, a
`.torrent` URL, or choose a local `.torrent` file from the **Torrent** tab.
Each torrent saves into its own folder in your selected download directory.

**Choose files:** right-click a torrent → **Torrent information** → check the
files you want. Your selection is saved.

**See what's happening:** progress, speed, ETA, seeds, peers, and tracker host.
The **Host** column shows a tracker and how many more are available.

**Remove partial data:** stop or cancel a torrent, then choose **Delete
downloaded file** to remove its data, including incomplete files.

**Use with a VPN:** select a network interface and enable binding in the
**Torrent** tab *before* starting a torrent. This binds libtorrent traffic to
the selected interface; it does not connect to a VPN. Connect your VPN
separately and verify that the selected interface belongs to it.

## Run from source

Requires **64-bit Python 3.12+** with Tcl/Tk. In PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

## Build for Windows

### Standalone executable

With the virtual environment activated, run:

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Downetic `
  --icon "downloader_app\assets\icons\main_ico.ico" `
  --add-data "downloader_app\assets;downloader_app\assets" `
  --add-data "downloader_app\locales;downloader_app\locales" `
  --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all libtorrent main.py
```

Creates `dist\Downetic.exe`. The command bundles **libtorrent** for torrent
downloads; the first launch may take longer.

### Installer

First build the executable above and install **Inno Setup 6**. Then run:

```powershell
Set-Location .\installer
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\Downetic.iss
```

The installer is created in `dist\installer` and supports **Windows 10 22H2+**.
It includes Downetic and libtorrent. During setup, it downloads SHA-256-verified
Node.js and FFmpeg archives, unless you choose to reuse those tools from `PATH`.
Setup requires an Internet connection for any runtimes it needs to download.

Before publishing, synchronize the installer version and verify the release:

```powershell
node .\tests\js\sync-release-version.mjs
node .\tests\js\verify-release.mjs
```

## Files and privacy

- **Downloads:** saved in the folder you choose.
- **Settings and history:** stored locally in `%LOCALAPPDATA%\Downetic`.
- **Upgrades:** keep the existing settings and history.
- **Browser cookies:** only read locally when you enable them for yt-dlp;
  ordinary HTTP downloads do not use them.

## Tests

Run the tests from the project folder:

```powershell
python -m unittest discover -s tests -v
```

HTTP tests use a local temporary server; they don't contact an external
download service.

## Credits

Toolbar icons are from
[diinki Aero Icons](https://github.com/Aneko-san/diinki-aero-icons-1.5-remix).
The included MIT license and source attribution are in
`downloader_app\assets\aero`.

Thanks to everyone who tests and contributes to Downetic.
