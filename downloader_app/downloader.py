from __future__ import annotations

import os
import re
import shutil
import time
from email.message import Message
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import requests

from downloader_app.models import TransferControl


ProgressCallback = Callable[[dict[str, object]], None]
CHUNK_SIZE = 128 * 1024
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


class TransferPaused(Exception):
    pass


class TransferCancelled(Exception):
    pass


def select_engine(url: str, requested: str) -> str:
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    if suffix in {".m3u8", ".mpd"}:
        return "video"
    if requested in {"http", "video"}:
        return requested
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
    for runtime in ("node", "deno", "bun"):
        runtime_path = shutil.which(runtime)
        if runtime_path:
            return {"js_runtimes": {runtime: {"path": runtime_path}}}
    return {}


def _is_youtube_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname.endswith((".youtube.com", ".youtube-nocookie.com"))
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
) -> str:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required for this link. Install requirements.txt.") from exc

    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = ""
    last_progress_report: float | None = None
    runtime_options = _javascript_runtime_options()
    if _is_youtube_collection_url(url):
        raise RuntimeError(
            "YouTube playlist/channel links are not downloaded automatically. "
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
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
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
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        code = ydl.download([url])
    if code:
        raise RuntimeError(f"yt-dlp exited with status {code}.")
    if control.cancelled.is_set():
        raise TransferCancelled
    return filename or "Video download"
