from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests

from downloader_app.downloader import HTTP_HEADERS


MAX_PAGE_BYTES = 4 * 1024 * 1024
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
        if tag in {"video", "audio"}:
            kind = "Video" if tag == "video" else "Audio"
            name = values.get("title") or values.get("aria-label") or ""
            self._media_context.append((tag, kind, name))
            self._add(values.get("src") or values.get("data-src"), kind, name)
        elif tag == "source":
            context = self._media_context[-1] if self._media_context else None
            name = values.get("title") or (context[2] if context else "")
            self._add(
                values.get("src") or values.get("data-src"),
                context[1] if context else None,
                name,
                values.get("type") or "",
            )
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


def extract_media_links(html: str, page_url: str) -> list[MediaLink]:
    parser = _MediaLinkParser(page_url)
    parser.feed(html)
    parser.close()
    return parser.links


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
                with requests.get(
                    page_url,
                    headers=HTTP_HEADERS,
                    stream=True,
                    timeout=(15, 30),
                ) as response:
                    response.raise_for_status()
                    final_url = response.url or page_url
                    content_type = (
                        response.headers.get("Content-Type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    kind = _media_kind(final_url) or _content_type_kind(content_type)
                    if kind is not None:
                        candidates = [
                            MediaLink(
                                _media_name(final_url),
                                kind,
                                _media_format(final_url, content_type),
                                final_url,
                                page_url,
                            )
                        ]
                    elif content_type in {"text/html", "application/xhtml+xml"}:
                        body = bytearray()
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            body.extend(chunk)
                            if len(body) >= MAX_PAGE_BYTES:
                                del body[MAX_PAGE_BYTES:]
                                break
                        try:
                            html = body.decode(
                                response.encoding or "utf-8", errors="replace"
                            )
                        except LookupError:
                            html = body.decode("utf-8", errors="replace")
                        candidates = extract_media_links(html, final_url)
                    else:
                        candidates = []
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
