from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from typing import Callable, Generator, TypeVar
from urllib.parse import parse_qs, unquote, urlsplit

import requests

from downloader_app.models import TransferControl


ProgressCallback = Callable[[dict[str, object]], None]
CHUNK_SIZE = 128 * 1024
_Result = TypeVar("_Result")


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
DIRECT_FILE_SUFFIXES = {
    ".7z", ".avi", ".bin", ".bz2", ".csv", ".doc", ".docx", ".exe",
    ".flac", ".gif", ".gz", ".iso", ".jpeg", ".jpg", ".m4a", ".mkv",
    ".mov", ".mp3", ".mp4", ".mpeg", ".mpg", ".msi", ".ogg", ".pdf",
    ".png", ".ppt", ".pptx", ".rar", ".tar", ".tgz", ".txt", ".wav",
    ".webm", ".webp", ".wma", ".wmv", ".xls", ".xlsx", ".zip",
}
TORRENT_TRACKERS = (
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)


class TransferPaused(Exception):
    pass


class TransferCancelled(Exception):
    pass


def select_engine(url: str, requested: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.lower() == "magnet":
        return "torrent"
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    if suffix == ".torrent":
        return "torrent"
    if suffix in {".m3u8", ".mpd"}:
        return "video"
    if requested in {"http", "video", "torrent"}:
        return requested
    if _is_tiktok_url(url):
        return "video"
    return "http" if suffix in DIRECT_FILE_SUFFIXES else "video"


def _wait_if_paused(control: TransferControl) -> None:
    while control.paused.is_set():
        if control.cancelled.is_set():
            raise TransferCancelled
        time.sleep(0.2)
    if control.cancelled.is_set():
        raise TransferCancelled


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "download"


def _response_filename(response: requests.Response, url: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    message = Message()
    message["content-disposition"] = disposition
    name = message.get_filename()
    if not name:
        name = unquote(Path(urlsplit(response.url or url).path).name) or "download"
    return _safe_filename(name)


def _javascript_runtime_options() -> dict[str, object]:
    runtime_directory = _runtime_directory()
    bundled_node = runtime_directory / "node" / "node.exe"
    node_candidates = [bundled_node]
    node_candidates.extend(
        sorted(
            (runtime_directory / "node").glob("node-v*-win-x64/node.exe"),
            reverse=True,
        )
    )
    for candidate in node_candidates:
        if candidate.is_file():
            return {"js_runtimes": {"node": {"path": str(candidate)}}}

    for runtime in ("node", "deno", "bun"):
        runtime_path = shutil.which(runtime)
        if runtime_path:
            return {"js_runtimes": {runtime: {"path": runtime_path}}}
    return {}


def _runtime_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    return Path(__file__).resolve().parents[1] / "runtime"


def _ffmpeg_options() -> dict[str, object]:
    ffmpeg_directory = _runtime_directory() / "ffmpeg"
    candidates = [ffmpeg_directory / "bin" / "ffmpeg.exe"]
    candidates.extend(
        sorted(
            ffmpeg_directory.glob("ffmpeg-*/bin/ffmpeg.exe"),
            reverse=True,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return {"ffmpeg_location": str(candidate.parent)}
    return {}


def _browser_cookie_options(browser: str) -> dict[str, object]:
    if browser not in {"chrome", "edge"}:
        if browser:
            raise ValueError(f"Unsupported browser for cookies: {browser}")
        return {}
    return {"cookiesfrombrowser": (browser,)}


class _CookieFallbackLogger:
    def debug(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        return None

    def error(self, message: str) -> None:
        if "could not copy chrome cookie database" not in message.casefold():
            print(message, file=sys.stderr)


def _browser_profile_root(browser: str) -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not local_app_data:
        return None
    browser_directory = {
        "chrome": Path(local_app_data) / "Google" / "Chrome" / "User Data",
        "edge": Path(local_app_data) / "Microsoft" / "Edge" / "User Data",
    }.get(browser)
    return browser_directory


def _find_browser_cookie_database(user_data: Path) -> tuple[Path, Path] | None:
    candidates: list[tuple[Path, Path]] = []
    profiles = [user_data]
    profiles.extend(path for path in user_data.iterdir() if path.is_dir())
    for profile in profiles:
        for relative_path in (Path("Network") / "Cookies", Path("Cookies")):
            database = profile / relative_path
            if database.is_file():
                candidates.append((database, profile))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0].stat().st_mtime)


@contextmanager
def _snapshot_browser_cookie_options(
    browser: str,
) -> Generator[dict[str, object], None, None]:
    user_data = _browser_profile_root(browser)
    if user_data is None or not user_data.is_dir():
        raise RuntimeError(
            f"Could not locate the {browser.title()} profile to snapshot its "
            "cookies. Verify the browser is installed and try again."
        )
    source = _find_browser_cookie_database(user_data)
    if source is None:
        raise RuntimeError(
            f"Could not find the {browser.title()} cookie database. Sign in to "
            "the browser, then retry."
        )
    source_database, source_profile = source
    local_state = user_data / "Local State"
    if not local_state.is_file():
        raise RuntimeError(
            f"Could not find the {browser.title()} Local State file required "
            "to decrypt its cookies."
        )

    with tempfile.TemporaryDirectory(prefix="downetic-cookies-") as temporary:
        snapshot_root = Path(temporary) / "User Data"
        snapshot_profile = snapshot_root / source_profile.name
        snapshot_database = snapshot_profile / source_database.relative_to(
            source_profile
        )
        snapshot_database.parent.mkdir(parents=True)
        shutil.copy2(local_state, snapshot_root / "Local State")
        source_connection: sqlite3.Connection | None = None
        snapshot_connection: sqlite3.Connection | None = None
        try:
            source_connection = sqlite3.connect(
                source_database.as_uri() + "?mode=ro",
                uri=True,
                timeout=5,
            )
            snapshot_connection = sqlite3.connect(snapshot_database)
            source_connection.backup(snapshot_connection)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(
                f"Could not create a consistent {browser.title()} cookie "
                "snapshot. Close the browser completely and retry."
            ) from exc
        finally:
            if snapshot_connection is not None:
                snapshot_connection.close()
            if source_connection is not None:
                source_connection.close()
        yield {
            "cookiesfrombrowser": (browser, str(snapshot_profile)),
        }


def _run_with_cookie_snapshot_fallback(
    browser: str,
    options: dict[str, object],
    operation: Callable[[dict[str, object]], _Result],
    download_error_type: type[Exception],
) -> _Result:
    try:
        return operation(options)
    except download_error_type as exc:
        if (
            browser not in {"chrome", "edge"}
            or "could not copy chrome cookie database" not in str(exc).casefold()
        ):
            raise
    with _snapshot_browser_cookie_options(browser) as snapshot_options:
        retry_options = {**options, **snapshot_options}
        return operation(retry_options)


def diagnose_download_error(message: str, url: str) -> str:
    lowered = message.casefold()
    if "could not copy chrome cookie database" in lowered:
        explanation = (
            "Windows could not copy Chrome's cookie database. Fully exit Chrome, "
            "including background processes, then retry. If it still fails, "
            "select Edge or turn off Browser cookies in Settings."
        )
    elif "unexpected response from webpage request" in lowered:
        if _is_tiktok_url(url):
            explanation = (
                "TikTok did not return the page data yt-dlp needs. Verify that "
                "the video opens in your browser, update Downetic, and if you "
                "are signed in, enable Browser cookies and select that browser."
            )
        else:
            explanation = (
                "The site returned an unexpected page. Verify the link in your "
                "browser and update Downetic."
            )
    elif "http error 403" in lowered or "forbidden" in lowered:
        explanation = (
            "The site refused the request (HTTP 403). Check that the link is "
            "accessible in your browser; if you are signed in, try enabling "
            "Browser cookies for that site."
        )
    elif "http error 429" in lowered or "too many requests" in lowered:
        explanation = (
            "The site is temporarily rate-limiting requests. Wait before "
            "trying again; repeated attempts may extend the limit."
        )
    elif "failed to decrypt" in lowered or "could not copy" in lowered:
        explanation = (
            "Downetic could not read the browser cookies. Close the selected "
            "browser and retry, or select a different browser in Settings."
        )
    elif "timed out" in lowered or "name resolution" in lowered:
        explanation = (
            "The site could not be reached. Check your Internet connection, "
            "VPN, DNS, and proxy settings, then try again."
        )
    elif "requested format is not available" in lowered:
        explanation = (
            "The requested media format is not available. Change the output "
            "format or try the Original option."
        )
    elif "private" in lowered or "login required" in lowered or "sign in" in lowered:
        explanation = (
            "This media requires an authorized browser session. Enable Browser "
            "cookies and select the browser where you are signed in."
        )
    elif "unsupported url" in lowered or "no suitable extractor" in lowered:
        explanation = (
            "Downetic cannot recognize this video-site URL. Check the link and "
            "update Downetic."
        )
    else:
        return message
    if message.startswith(f"{explanation}\n\nDetails: "):
        return message
    return f"{explanation}\n\nDetails: {message}"


def _is_youtube_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname.endswith((".youtube.com", ".youtube-nocookie.com"))
    )


def _is_tiktok_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return (
        hostname == "tiktok.com"
        or hostname.endswith((".tiktok.com", ".tiktokv.com"))
        or hostname == "tiktokv.com"
    )


def _is_youtube_collection_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if not _is_youtube_url(url):
        return False
    path = parsed.path.lower()
    return (
        path == "/playlist"
        or path.startswith(("/channel/", "/user/", "/c/", "/@", "/feed/"))
        or path in {"/", "/feed/trending", "/feed/subscriptions"}
    )


def _is_tiktok_collection_url(url: str) -> bool:
    parsed = urlsplit(url)
    if not _is_tiktok_url(url):
        return False
    path = parsed.path.rstrip("/").lower()
    return (
        bool(re.fullmatch(r"/@[\w.-]+(?:/collection/[^/]+)?", path))
        or path.startswith(("/tag/", "/music/"))
    )


def _video_output_options(output_format: str) -> dict[str, object]:
    if output_format == "original":
        return {}
    if output_format == "mp4":
        return {
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"
            ),
            "merge_output_format": "mp4",
        }
    if output_format == "webm":
        return {
            "format": "bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]",
            "merge_output_format": "webm",
        }
    if output_format == "mp3":
        return {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    raise ValueError(f"Unsupported video output format: {output_format}")


def _unique_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    stem, suffix = target.stem, target.suffix
    index = 1
    while target.exists() or target.with_name(target.name + ".part").exists():
        target = directory / f"{stem} ({index}){suffix}"
        index += 1
    return target


def _is_magnet_link(url: str) -> bool:
    return urlsplit(url).scheme.lower() == "magnet"


def _torrent_display_name(url: str) -> str:
    if _is_magnet_link(url):
        query = parse_qs(urlsplit(url).query)
        names = query.get("dn") or []
        if names:
            return _safe_filename(names[0])
        hashes = query.get("xt") or []
        for value in hashes:
            if value.lower().startswith("urn:btih:"):
                return _safe_filename(f"torrent-{value.rsplit(':', 1)[-1][:12]}")
        return "torrent-download"
    return _safe_filename(unquote(Path(urlsplit(url).path).stem) or "torrent-download")


def _torrent_tracker_summary(handle: object) -> str:
    get_trackers = getattr(handle, "trackers", None)
    trackers = get_trackers() if callable(get_trackers) else (
        {"url": url} for url in TORRENT_TRACKERS
    )
    hosts: list[str] = []
    for tracker in trackers:
        if not isinstance(tracker, dict) or not isinstance(tracker.get("url"), str):
            continue
        host = urlsplit(tracker["url"]).hostname
        if host and host not in hosts:
            hosts.append(host)
    if not hosts:
        return ""
    additional = len(hosts) - 1
    return f"{hosts[0]} +{additional}" if additional else hosts[0]


def _torrent_info_from_url(lt: object, url: str) -> object:
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as torrent_file:
        temporary_path = Path(torrent_file.name)
    try:
        with requests.get(url, headers=HTTP_HEADERS, timeout=(15, 30)) as response:
            response.raise_for_status()
            temporary_path.write_bytes(response.content)
        return lt.torrent_info(str(temporary_path))
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass


def _torrent_add_params(lt: object, url: str, save_path: Path) -> object:
    if _is_magnet_link(url):
        params = lt.parse_magnet_uri(url)
        params.save_path = str(save_path)
        return params
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"http", "https"}:
        torrent_info = _torrent_info_from_url(lt, url)
    else:
        torrent_info = lt.torrent_info(url)
    params = lt.add_torrent_params()
    params.ti = torrent_info
    params.save_path = str(save_path)
    return params


def download_torrent(
    url: str,
    directory: str,
    control: TransferControl,
    report: ProgressCallback,
    vpn_address: str = "",
    selected_files: Callable[[], frozenset[str] | None] | None = None,
) -> str:
    try:
        import libtorrent as lt
    except ImportError as exc:
        raise RuntimeError(
            "Torrent downloads require the libtorrent package. "
            "Install requirements.txt and restart Downetic."
        ) from exc

    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    torrent_name = _torrent_display_name(url)
    target_dir = _unique_target(output_dir, torrent_name)
    target_dir.mkdir(parents=True, exist_ok=True)

    session = lt.session()
    if hasattr(lt, "settings_pack"):
        settings = lt.settings_pack()
        for setting_name in (
            "enable_dht",
            "enable_lsd",
            "enable_upnp",
            "enable_natpmp",
        ):
            setting_key = getattr(lt.settings_pack, setting_name, None)
            if setting_key is not None:
                settings.set_bool(setting_key, True)
        if vpn_address:
            setting_key = getattr(lt.settings_pack, "outgoing_interfaces", None)
            if setting_key is None:
                raise RuntimeError(
                    "This libtorrent version cannot bind torrent traffic "
                    "to a selected VPN interface."
                )
            settings.set_str(setting_key, vpn_address)
        session.apply_settings(settings)
    else:
        try:
            settings = session.get_settings()
        except AttributeError:
            if vpn_address:
                raise RuntimeError(
                    "This libtorrent version cannot bind torrent traffic "
                    "to a selected VPN interface."
                ) from None
        else:
            for setting_name in (
                "enable_dht",
                "enable_lsd",
                "enable_upnp",
                "enable_natpmp",
            ):
                if setting_name in settings:
                    settings[setting_name] = True
            if vpn_address:
                if "outgoing_interfaces" not in settings:
                    raise RuntimeError(
                        "This libtorrent version cannot bind torrent traffic "
                        "to a selected VPN interface."
                    )
                settings["outgoing_interfaces"] = vpn_address
            session.apply_settings(settings)
    session.start_dht()
    try:
        session.start_lsd()
        session.start_upnp()
        session.start_natpmp()
    except AttributeError:
        pass

    params = _torrent_add_params(lt, url, target_dir)
    handle = session.add_torrent(params)
    report({"torrent_directory": str(target_dir)})
    for tracker in TORRENT_TRACKERS:
        try:
            handle.add_tracker({"url": tracker})
        except Exception:
            pass
    report({"torrent_tracker": _torrent_tracker_summary(handle)})

    last_report = 0.0
    reported_name = torrent_name
    reported_files: str | None = None
    applied_selection: frozenset[str] | None = None
    was_paused = False
    try:
        while True:
            if control.cancelled.is_set():
                raise TransferCancelled
            if control.paused.is_set():
                if not was_paused:
                    handle.pause()
                    was_paused = True
                _wait_if_paused(control)
                handle.resume()
                was_paused = False

            status = handle.status()
            if status.has_metadata:
                try:
                    torrent_info = handle.torrent_file()
                    reported_name = _safe_filename(torrent_info.name())
                except RuntimeError:
                    pass
                else:
                    requested_files = selected_files() if selected_files else None
                    if (
                        requested_files is not None
                        and requested_files != applied_selection
                    ):
                        files = torrent_info.files()
                        handle.prioritize_files(
                            [
                                4 if files.file_path(index) in requested_files else 0
                                for index in range(files.num_files())
                            ]
                        )
                        applied_selection = requested_files
                    if reported_files is None:
                        files = torrent_info.files()
                        reported_files = json.dumps(
                            [
                                {
                                    "path": files.file_path(index),
                                    "size": int(files.file_size(index)),
                                }
                                for index in range(files.num_files())
                            ],
                            ensure_ascii=False,
                        )
            total = int(status.total_wanted) if status.total_wanted else None
            progress = int(status.total_wanted_done)
            speed = float(status.download_rate)
            eta = (
                max((total or 0) - progress, 0) / speed
                if total and speed > 0
                else None
            )
            now = time.monotonic()
            if now - last_report >= 0.5:
                values: dict[str, object] = {
                    "filename": reported_name,
                    "progress": progress,
                    "total": total,
                    "speed": speed,
                    "eta": eta,
                    "seeds": int(status.num_seeds),
                    "peers": int(status.num_peers),
                }
                if reported_files is not None:
                    values["torrent_files"] = reported_files
                report(values)
                last_report = now
            if status.is_seeding or (total is not None and progress >= total and total > 0):
                values = {
                    "filename": reported_name,
                    "progress": progress,
                    "total": total,
                    "speed": 0.0,
                    "eta": 0,
                    "seeds": int(status.num_seeds),
                    "peers": int(status.num_peers),
                    "torrent_files": reported_files or "[]",
                }
                report(values)
                return reported_name
            time.sleep(0.5)
    finally:
        try:
            session.remove_torrent(handle)
        except Exception:
            pass


def download_http(
    url: str,
    directory: str,
    control: TransferControl,
    report: ProgressCallback,
    previous_filename: str = "",
) -> str:
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    url_filename = _safe_filename(unquote(Path(urlsplit(url).path).name) or "download")
    resume_filename = _safe_filename(previous_filename or url_filename)
    resume_target = _unique_target(output_dir, resume_filename)
    existing_partial = output_dir / (resume_filename + ".part")
    if existing_partial.exists() and not (output_dir / resume_filename).exists():
        resume_target = output_dir / resume_filename
    resume_partial = resume_target.with_name(resume_target.name + ".part")
    resume_at = resume_partial.stat().st_size if resume_partial.exists() else 0
    headers = {**HTTP_HEADERS}
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
    started = time.monotonic()
    last_report = started

    with requests.get(url, headers=headers, stream=True, timeout=(15, 30)) as response:
        if response.status_code == 416 and resume_at:
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes \*/(\d+)", content_range)
            response.close()
            if match and int(match.group(1)) == resume_at:
                os.replace(resume_partial, resume_target)
                report({
                    "filename": resume_target.name,
                    "progress": resume_at,
                    "total": resume_at,
                    "speed": 0.0,
                    "eta": 0,
                })
                return resume_target.name
            raise RuntimeError(f"Server rejected the resume request for {url}.")
        if response.status_code not in (200, 206):
            response.raise_for_status()
        response_filename = _response_filename(response, url)
        filename = (
            resume_filename
            if resume_at
            else response_filename
        )
        if not previous_filename or previous_filename == url_filename:
            if not resume_at:
                filename = response_filename
        target = resume_target if resume_at else _unique_target(output_dir, filename)
        partial = target.with_name(target.name + ".part")

        # append only when the server confirms the exact byte offset.
        if resume_at and response.status_code == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {resume_at}-"):
                response.close()
                raise RuntimeError("Server returned an invalid Content-Range for this download.")
        elif resume_at:
            resume_at = 0

        total_header = response.headers.get("Content-Length")
        total = int(total_header) + resume_at if total_header and total_header.isdigit() else None
        mode = "ab" if resume_at else "wb"
        downloaded = resume_at
        with partial.open(mode) as output:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                _wait_if_paused(control)
                if not chunk:
                    continue
                output.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_report >= 0.5:
                    elapsed = max(now - started, 0.001)
                    speed = (downloaded - resume_at) / elapsed
                    remaining = max(total - downloaded, 0) / speed if total and speed else None
                    report({
                        "filename": filename,
                        "progress": downloaded,
                        "total": total,
                        "speed": speed,
                        "eta": remaining,
                    })
                    last_report = now

    if control.cancelled.is_set():
        raise TransferCancelled
    os.replace(partial, target)
    size = target.stat().st_size
    report({
        "filename": target.name,
        "progress": size,
        "total": size,
        "speed": 0.0,
        "eta": 0,
    })
    return target.name


def download_video(
    url: str,
    directory: str,
    control: TransferControl,
    report: ProgressCallback,
    output_format: str = "original",
    retry_filename: str = "",
    browser_cookie_browser: str = "",
    download_subtitles: bool = False,
) -> str:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required for this link. Install requirements.txt.") from exc

    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = ""
    last_progress_report: float | None = None
    output_template = str(output_dir / "%(title)s.%(ext)s")
    if retry_filename:
        stem = Path(retry_filename).stem
        index = 1
        while any(
            path.name.casefold().startswith(f"{stem} ({index}).".casefold())
            for path in output_dir.iterdir()
        ):
            index += 1
        output_template = str(output_dir / f"{stem} ({index}).%(ext)s")
    runtime_options = _javascript_runtime_options()
    if _is_youtube_collection_url(url):
        raise RuntimeError(
            "YouTube playlist/channel links are not downloaded automatically. "
            "Paste the URL of one specific video instead."
        )
    if _is_tiktok_collection_url(url):
        raise RuntimeError(
            "TikTok profile and collection links are not downloaded automatically. "
            "Paste the URL of one specific video instead."
        )
    if _is_youtube_url(url) and not runtime_options:
        raise RuntimeError(
            "YouTube downloads require Node.js, Deno, or Bun for yt-dlp's "
            "JavaScript challenges. Install a JavaScript runtime and restart the app."
        )

    def progress_hook(data: dict[str, object]) -> None:
        nonlocal filename, last_progress_report
        _wait_if_paused(control)
        filename = Path(str(data.get("filename", filename))).name
        if data.get("status") == "downloading":
            now = time.monotonic()
            if last_progress_report is not None and now - last_progress_report < 0.5:
                return
            last_progress_report = now
            total_value = data.get("total_bytes") or data.get("total_bytes_estimate")
            speed_value = data.get("speed")
            eta_value = data.get("eta")
            report({
                "filename": filename,
                "progress": int(data.get("downloaded_bytes") or 0),
                "total": int(total_value) if total_value else None,
                "speed": float(speed_value) if speed_value else 0.0,
                "eta": float(eta_value) if eta_value is not None else None,
            })
        elif data.get("status") == "finished":
            filename = Path(str(data.get("filename", filename))).name
            downloaded = data.get("downloaded_bytes")
            total_value = data.get("total_bytes") or data.get("total_bytes_estimate")
            report({
                "filename": filename,
                "progress": int(downloaded) if downloaded else 0,
                "total": int(total_value) if total_value else None,
                "speed": 0.0,
                "eta": 0,
            })

    def postprocessor_hook(data: dict[str, object]) -> None:
        nonlocal filename
        info = data.get("info_dict")
        if data.get("status") == "finished" and isinstance(info, dict):
            final_path = info.get("filepath")
            if final_path:
                filename = Path(str(final_path)).name

    options: dict[str, object] = {
        "outtmpl": output_template,
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "continuedl": True,
        "overwrites": False,
        **_video_output_options(output_format),
        **runtime_options,
        **_ffmpeg_options(),
        **_browser_cookie_options(browser_cookie_browser),
    }
    if browser_cookie_browser:
        options["logger"] = _CookieFallbackLogger()
    if download_subtitles:
        options.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["all"],
            "subtitlesformat": "best",
        })

    def run_download(current_options: dict[str, object]) -> object:
        with yt_dlp.YoutubeDL(current_options) as ydl:
            code = ydl.download([url])
        return code

    try:
        code = _run_with_cookie_snapshot_fallback(
            browser_cookie_browser,
            options,
            run_download,
            getattr(
                getattr(yt_dlp, "utils", None),
                "DownloadError",
                RuntimeError,
            ),
        )
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(diagnose_download_error(str(exc), url)) from exc
    if code:
        raise RuntimeError(f"yt-dlp exited with status {code}.")
    if control.cancelled.is_set():
        raise TransferCancelled
    return filename or "Video download"
