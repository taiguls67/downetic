from __future__ import annotations

import html as html_module
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests

from downloader_app.downloader import HTTP_HEADERS


MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_BROWSER_NETLOG_BYTES = 32 * 1024 * 1024
MAX_EMBEDDED_PAGES = 8
MAX_EMBEDDED_DEPTH = 2
MAX_BROWSER_SCAN_SECONDS = 15
SCRIPT_URL_PATTERN = re.compile(r"https?://[^\s<>\"'\\]+", re.IGNORECASE)
MEDIA_EXTENSIONS = {
    ".3gp": "Video",
    ".aac": "Audio",
    ".avi": "Video",
    ".flac": "Audio",
    ".flv": "Video",
    ".m4a": "Audio",
    ".m4v": "Video",
    ".mkv": "Video",
    ".mov": "Video",
    ".mp3": "Audio",
    ".mp4": "Video",
    ".mpeg": "Video",
    ".mpg": "Video",
    ".mpd": "Stream",
    ".m3u8": "Stream",
    ".ogg": "Audio",
    ".ogv": "Video",
    ".opus": "Audio",
    ".wav": "Audio",
    ".webm": "Video",
    ".wma": "Audio",
}
MEDIA_CONTENT_TYPES = {
    "application/dash+xml": "Stream",
    "application/vnd.apple.mpegurl": "Stream",
    "application/x-mpegurl": "Stream",
    "audio/mpegurl": "Stream",
    "audio/x-mpegurl": "Stream",
}


@dataclass(frozen=True)
class MediaLink:
    name: str
    kind: str
    format: str
    url: str
    page_url: str


@dataclass(frozen=True)
class ScanProgress:
    page_url: str
    completed: int
    total: int
    found: int
    error: str = ""
    page_done: bool = False


def _media_kind(url: str) -> str | None:
    suffix = Path(unquote(urlsplit(url).path)).suffix.lower()
    return MEDIA_EXTENSIONS.get(suffix)


def _media_format(url: str, content_type: str = "") -> str:
    suffix = Path(unquote(urlsplit(url).path)).suffix
    if suffix:
        return suffix.lstrip(".").upper()
    normalized_type = content_type.split(";", 1)[0].strip().lower()
    known_formats = {
        "application/dash+xml": "MPD",
        "application/vnd.apple.mpegurl": "M3U8",
        "application/x-mpegurl": "M3U",
        "audio/mpeg": "MP3",
        "audio/mpegurl": "M3U",
        "audio/x-mpegurl": "M3U",
    }
    if normalized_type in known_formats:
        return known_formats[normalized_type]
    return normalized_type.rsplit("/", 1)[-1].upper() or "Unknown"


def _content_type_kind(content_type: str) -> str | None:
    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type.startswith("video/"):
        return "Video"
    if content_type.startswith("audio/"):
        return "Audio"
    return MEDIA_CONTENT_TYPES.get(content_type)


def _normalized_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _media_name(url: str) -> str:
    name = unquote(Path(urlsplit(url).path).name)
    return name or urlsplit(url).hostname or url


class _MediaLinkParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.links: list[MediaLink] = []
        self._seen: set[str] = set()
        self._media_context: list[tuple[str, str, str]] = []
        self.embedded_pages: list[str] = []
        self._seen_embedded_pages: set[str] = set()

    def _add(
        self,
        value: str | None,
        context: str | None = None,
        name: str = "",
        content_type: str = "",
    ) -> None:
        if not value:
            return
        url = urljoin(self.page_url, value.strip())
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return
        kind = _media_kind(url) or _content_type_kind(content_type) or context
        if kind is None:
            return
        normalized = _normalized_url(url)
        if normalized in self._seen:
            return
        self._seen.add(normalized)
        self.links.append(MediaLink(
            name=name.strip() or _media_name(url),
            kind=kind,
            format=_media_format(url, content_type),
            url=url,
            page_url=self.page_url,
        ))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value for name, value in attrs}
        lazy_source = next(
            (
                values.get(name)
                for name in (
                    "data-src",
                    "data-lazy-src",
                    "data-original",
                    "data-url",
                )
                if values.get(name)
            ),
            None,
        )
        if tag in {"video", "audio"}:
            kind = "Video" if tag == "video" else "Audio"
            name = values.get("title") or values.get("aria-label") or ""
            self._media_context.append((tag, kind, name))
            self._add(values.get("src") or lazy_source, kind, name)
        elif tag == "source":
            context = self._media_context[-1] if self._media_context else None
            name = values.get("title") or (context[2] if context else "")
            self._add(
                values.get("src") or lazy_source,
                context[1] if context else None,
                name,
                values.get("type") or "",
            )
        elif tag == "iframe":
            embedded_url = values.get("src") or lazy_source
            if embedded_url:
                url = urljoin(self.page_url, embedded_url.strip())
                parsed = urlsplit(url)
                if (
                    parsed.scheme.lower() in {"http", "https"}
                    and parsed.netloc
                    and url not in self._seen_embedded_pages
                ):
                    self._seen_embedded_pages.add(url)
                    self.embedded_pages.append(url)
        elif tag == "a":
            self._add(
                values.get("href"),
                name=values.get("title") or values.get("aria-label") or "",
                content_type=values.get("type") or "",
            )
        elif tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            context = "Audio" if "audio" in prop else "Video"
            if prop in {"og:video", "og:audio", "twitter:player:stream"}:
                self._add(values.get("content"), context)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._media_context) - 1, -1, -1):
            if self._media_context[index][0] == tag:
                del self._media_context[index:]
                break


def _parse_media_page(html: str, page_url: str) -> tuple[list[MediaLink], list[str]]:
    parser = _MediaLinkParser(page_url)
    parser.feed(html)
    parser.close()
    decoded_html = html_module.unescape(html)
    decoded_html = re.sub(r"\\u002f", "/", decoded_html, flags=re.IGNORECASE)
    decoded_html = re.sub(r"\\u0026", "&", decoded_html, flags=re.IGNORECASE)
    decoded_html = decoded_html.replace(r"\/", "/")
    for match in SCRIPT_URL_PATTERN.findall(decoded_html):
        parser._add(match.rstrip(".,;:!?)]}"))
    return parser.links, parser.embedded_pages


def extract_media_links(html: str, page_url: str) -> list[MediaLink]:
    links, _embedded_pages = _parse_media_page(html, page_url)
    return links


class _QuietYtDlpLogger:
    def debug(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def _extract_video_page(page_url: str, source_page_url: str) -> MediaLink | None:
    import yt_dlp

    options: dict[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 10,
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "logger": _QuietYtDlpLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except yt_dlp.utils.DownloadError:
        return None
    if not isinstance(info, dict) or info.get("_type") in {
        "playlist",
        "multi_video",
    }:
        return None
    formats = info.get("formats")
    if not info.get("url") and not (isinstance(formats, list) and formats):
        return None
    extension = info.get("ext") or ""
    if not extension and isinstance(formats, list):
        extension = next(
            (
                item.get("ext")
                for item in formats
                if isinstance(item, dict) and item.get("ext")
            ),
            "",
        )
    protocol = info.get("protocol") or ""
    if not protocol and isinstance(formats, list):
        protocol = next(
            (
                item.get("protocol")
                for item in formats
                if isinstance(item, dict) and item.get("protocol")
            ),
            "",
        )
    media_format = str(extension).upper() or "VIDEO"
    if "m3u8" in str(protocol).lower():
        media_format = "M3U8"
    elif "dash" in str(protocol).lower() or "mpd" in str(protocol).lower():
        media_format = "MPD"
    return MediaLink(
        name=str(info.get("title") or _media_name(page_url)),
        kind="Stream" if media_format in {"M3U8", "MPD"} else "Video",
        format=media_format,
        url=page_url,
        page_url=source_page_url,
    )


def _browser_executable() -> str | None:
    for browser in ("msedge.exe", "chrome.exe"):
        executable = shutil.which(browser)
        if executable:
            return executable

    program_files = (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    )
    relative_paths = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Chromium/Application/chrome.exe"),
    )
    for directory in program_files:
        if not directory:
            continue
        for relative_path in relative_paths:
            candidate = Path(directory) / relative_path
            if candidate.is_file():
                return str(candidate)
    return None


def _network_media_links(netlog_path: Path, page_url: str) -> list[MediaLink]:
    if netlog_path.stat().st_size > MAX_BROWSER_NETLOG_BYTES:
        raise ValueError("Browser network log exceeded the scan size limit")
    with netlog_path.open(encoding="utf-8") as netlog_file:
        netlog = json.load(netlog_file)

    links: list[MediaLink] = []
    seen: set[str] = set()
    urls_by_source: dict[int, set[str]] = {}
    stream_types_by_source: dict[int, str] = {}
    events = netlog.get("events", []) if isinstance(netlog, dict) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        params = event.get("params")
        if not isinstance(params, dict):
            continue
        source = event.get("source")
        source_id = source.get("id") if isinstance(source, dict) else None
        if isinstance(source_id, int):
            for key in ("url", "original_url", "new_url", "location"):
                value = params.get(key)
                if isinstance(value, str):
                    urls = urls_by_source.setdefault(source_id, set())
                    urls.update(
                        html_module.unescape(match.rstrip(".,;:!?)]}"))
                        for match in SCRIPT_URL_PATTERN.findall(value)
                    )

            headers = params.get("headers")
            if isinstance(headers, list):
                response_headers = [
                    header
                    for header in headers
                    if isinstance(header, str)
                ]
            elif isinstance(headers, str):
                response_headers = headers.splitlines()
            else:
                response_headers = []
            is_response = any(
                re.match(r"^\s*HTTP/\d(?:\.\d)?\s+\d{3}\b", header, re.I)
                or re.match(r"^\s*:status:\s*\d{3}\b", header, re.I)
                for header in response_headers
            )
            if is_response:
                for header in response_headers:
                    name, separator, value = header.partition(":")
                    if separator and name.strip().lower() == "content-type":
                        kind = _content_type_kind(value)
                        if kind == "Stream":
                            stream_types_by_source[source_id] = value.strip()
                        break

        for value in params.values():
            if not isinstance(value, str):
                continue
            for match in SCRIPT_URL_PATTERN.findall(value):
                url = html_module.unescape(match.rstrip(".,;:!?)]}"))
                if _media_kind(url) != "Stream":
                    continue
                _add_network_media_link(links, seen, url, page_url)

    for source_id, content_type in stream_types_by_source.items():
        for url in urls_by_source.get(source_id, set()):
            if _media_kind(url) == "Stream":
                continue
            _add_network_media_link(
                links, seen, url, page_url, content_type
            )
    return links


def _add_network_media_link(
    links: list[MediaLink],
    seen: set[str],
    url: str,
    page_url: str,
    content_type: str = "",
) -> None:
    key = _normalized_url(url)
    if key in seen:
        return
    seen.add(key)
    links.append(MediaLink(
        name=_media_name(url),
        kind=_media_kind(url) or _content_type_kind(content_type) or "Stream",
        format=_media_format(url, content_type),
        url=url,
        page_url=page_url,
    ))


def _scan_javascript_media(page_url: str) -> tuple[list[MediaLink], str]:
    browser = _browser_executable()
    if browser is None:
        return [], ""

    try:
        with tempfile.TemporaryDirectory(prefix="downetic-media-scan-") as folder:
            profile_path = Path(folder) / "profile"
            netlog_path = Path(folder) / "network.json"
            command = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-sync",
                "--no-first-run",
                "--autoplay-policy=user-gesture-required",
                f"--user-data-dir={profile_path}",
                "--virtual-time-budget=5000",
                "--timeout=8000",
                f"--log-net-log={netlog_path}",
                "--net-log-capture-mode=Default",
                "--dump-dom",
                page_url,
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MAX_BROWSER_SCAN_SECONDS,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                details = completed.stderr.strip().splitlines()
                error = details[-1] if details else "Browser scan failed"
                return [], error
            if not netlog_path.is_file():
                return [], "Browser did not produce a network log"
            links, _embedded_pages = _parse_media_page(
                completed.stdout, page_url
            )
            links.extend(_network_media_links(netlog_path, page_url))
            return links, ""
    except subprocess.TimeoutExpired:
        return [], "Browser scan timed out"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], str(exc) or exc.__class__.__name__


def scan_media_pages(
    page_urls: list[str],
    on_progress: Callable[[ScanProgress], None] | None = None,
) -> list[MediaLink]:
    results: list[MediaLink] = []
    seen: set[str] = set()
    total = len(page_urls)
    for index, page_url in enumerate(page_urls, start=1):
        if on_progress:
            on_progress(ScanProgress(
                page_url=page_url,
                completed=index - 1,
                total=total,
                found=len(results),
            ))
        error = ""
        try:
            direct_kind = _media_kind(page_url)
            if direct_kind:
                candidates = [
                    MediaLink(
                        _media_name(page_url),
                        direct_kind,
                        _media_format(page_url),
                        page_url,
                        page_url,
                    )
                ]
            else:
                candidates = []
                embedded_queue: list[tuple[str, int]] = [(page_url, 0)]
                visited_pages: set[str] = set()
                first_error = ""
                access_denied = False
                while (
                    embedded_queue
                    and not access_denied
                    and len(visited_pages) < MAX_EMBEDDED_PAGES + 1
                ):
                    current_url, depth = embedded_queue.pop(0)
                    normalized_page_url = _normalized_url(current_url)
                    if normalized_page_url in visited_pages:
                        continue
                    visited_pages.add(normalized_page_url)
                    try:
                        with requests.get(
                            current_url,
                            headers=HTTP_HEADERS,
                            stream=True,
                            timeout=(15, 30),
                        ) as response:
                            response.raise_for_status()
                            final_url = response.url or current_url
                            content_type = (
                                response.headers.get("Content-Type", "")
                                .split(";", 1)[0]
                                .strip()
                                .lower()
                            )
                            kind = _media_kind(final_url) or _content_type_kind(
                                content_type
                            )
                            if kind is not None:
                                candidates.append(MediaLink(
                                    _media_name(final_url),
                                    kind,
                                    _media_format(final_url, content_type),
                                    final_url,
                                    page_url,
                                ))
                                continue
                            if content_type not in {
                                "text/html",
                                "application/xhtml+xml",
                            }:
                                continue
                            body = bytearray()
                            for chunk in response.iter_content(
                                chunk_size=64 * 1024
                            ):
                                body.extend(chunk)
                                if len(body) >= MAX_PAGE_BYTES:
                                    del body[MAX_PAGE_BYTES:]
                                    break
                            try:
                                html = body.decode(
                                    response.encoding or "utf-8",
                                    errors="replace",
                                )
                            except LookupError:
                                html = body.decode("utf-8", errors="replace")
                            page_candidates, embedded_pages = _parse_media_page(
                                html, final_url
                            )
                            candidates.extend(page_candidates)
                            if depth < MAX_EMBEDDED_DEPTH:
                                for embedded_url in embedded_pages:
                                    if (
                                        embedded_url not in visited_pages
                                        and len(embedded_queue)
                                        < MAX_EMBEDDED_PAGES
                                    ):
                                        embedded_queue.append(
                                            (embedded_url, depth + 1)
                                        )
                            if not any(
                                candidate.kind == "Video"
                                for candidate in page_candidates
                            ):
                                extracted_video = _extract_video_page(
                                    current_url, page_url
                                )
                                if extracted_video:
                                    candidates.append(extracted_video)
                    except requests.HTTPError as exc:
                        if not first_error:
                            first_error = str(exc) or exc.__class__.__name__
                        response = exc.response
                        if response is not None and response.status_code in {
                            401,
                            403,
                            429,
                        }:
                            access_denied = True
                            retry_after = response.headers.get("Retry-After")
                            if response.status_code == 429 and retry_after:
                                first_error = (
                                    f"{first_error} (Retry-After: {retry_after})"
                                )
                    except (requests.RequestException, ValueError) as exc:
                        if not first_error:
                            first_error = str(exc) or exc.__class__.__name__
                if not any(
                    candidate.kind in {"Video", "Stream"}
                    for candidate in candidates
                ) and not access_denied:
                    browser_candidates, browser_error = _scan_javascript_media(
                        page_url
                    )
                    candidates.extend(browser_candidates)
                    if browser_error and not first_error:
                        first_error = browser_error
                if not candidates and first_error:
                    error = first_error
        except (requests.RequestException, ValueError) as exc:
            candidates = []
            error = str(exc) or exc.__class__.__name__
        for candidate in candidates:
            key = _normalized_url(candidate.url)
            if key not in seen:
                seen.add(key)
                results.append(candidate)
        if on_progress:
            on_progress(ScanProgress(
                page_url=page_url,
                completed=index,
                total=total,
                found=len(results),
                error=error,
                page_done=True,
            ))
    return results
