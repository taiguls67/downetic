from __future__ import annotations

import tempfile
import threading
import queue
import base64
import json
import subprocess
import sqlite3
import sys
import time
import types
import unittest
import json
import re
from collections.abc import Callable
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from requests.exceptions import ConnectionError, HTTPError

from downloader_app import __version__
from downloader_app.downloader import (
    TransferCancelled,
    _browser_cookie_options,
    _run_with_cookie_snapshot_fallback,
    diagnose_download_error,
    _ffmpeg_options,
    _javascript_runtime_options,
    _video_output_options,
    _is_youtube_collection_url,
    _is_tiktok_collection_url,
    download_http,
    download_torrent,
    download_video,
    select_engine,
)
from downloader_app.gui import (
    DownloadManagerApp,
    _show_windows_notification,
    format_duration,
)
from downloader_app.links import parse_urls
from downloader_app.manager import DownloadManager
from downloader_app.media_links import (
    MediaLink,
    _scan_javascript_media,
    extract_media_links,
    scan_media_pages,
)
from downloader_app.models import DownloadRecord, TransferControl
from downloader_app.storage import Storage
from downloader_app.updates import is_newer_version, latest_release_version


PAYLOAD = b"download-manager-test-payload-" * 20000


class FileHandler(BaseHTTPRequestHandler):
    requested_ranges: list[str] = []
    received_user_agents: list[str] = []

    def do_GET(self) -> None:
        user_agent = self.headers.get("User-Agent", "")
        self.received_user_agents.append(user_agent)
        if "Mozilla/" not in user_agent:
            self.send_error(403)
            return
        range_header = self.headers.get("Range")
        if range_header:
            self.requested_ranges.append(range_header)
        # mimic a server that honors byte-range resumes.
        offset = int(range_header.removeprefix("bytes=").removesuffix("-")) if range_header else 0
        self.send_response(206 if offset else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="sample.bin"')
        self.send_header("Content-Length", str(len(PAYLOAD) - offset))
        if offset:
            self.send_header("Content-Range", f"bytes {offset}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        self.end_headers()
        self.wfile.write(PAYLOAD[offset:])

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class DownloadTests(unittest.TestCase):
    def test_release_version_comparison_respects_prerelease_order(self) -> None:
        self.assertTrue(is_newer_version("v0.1.1-beta.5", "0.1.1-beta.4"))
        self.assertTrue(is_newer_version("0.1.1", "0.1.1-rc.2"))
        self.assertFalse(is_newer_version("0.1.1-beta.3", "0.1.1-beta.4"))
        self.assertFalse(is_newer_version("0.1.1-beta.4", "0.1.1"))
        with self.assertRaises(ValueError):
            is_newer_version("nightly", "0.1.1")

    def test_latest_release_version_selects_highest_published_semver(self) -> None:
        response = Mock()
        response.json.return_value = [
            {"tag_name": "v0.1.2-beta.1", "draft": False},
            {"tag_name": "v0.1.1", "draft": False},
            {"tag_name": "v99.0.0", "draft": True},
            {"tag_name": "nightly", "draft": False},
        ]
        with patch(
            "downloader_app.updates.requests.get", return_value=response
        ) as get:
            self.assertEqual(latest_release_version(), "v0.1.2-beta.1")
        get.assert_called_once_with(
            "https://api.github.com/repos/taiguls67/downetic/releases",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Downetic/{__version__}",
            },
            params={"per_page": 100},
            timeout=(3, 5),
        )
        response.raise_for_status.assert_called_once_with()

    def test_update_prompt_opens_releases_only_when_confirmed(self) -> None:
        class PromptHarness:
            translations = {
                "update.title": "Update available",
                "update.available": "Version {version} is available.",
                "update.yes": "Yes",
                "update.no": "No",
            }

            def _t(self, key: str, **values: object) -> str:
                return self.translations[key].format(**values)

            def _ask_confirmation(
                self, *arguments: str
            ) -> bool:
                self.arguments = arguments
                return self.confirmed

        prompt = PromptHarness()
        from downloader_app.gui import DownloadManagerApp
        from downloader_app.updates import RELEASES_PAGE_URL

        prompt.confirmed = True
        with patch("downloader_app.gui.webbrowser.open", return_value=True) as open_url:
            DownloadManagerApp._show_update_prompt(prompt, "0.1.2")
        self.assertEqual(
            prompt.arguments,
            (
                "Update available",
                "Version 0.1.2 is available.",
                "Yes",
                "No",
            ),
        )
        open_url.assert_called_once_with(RELEASES_PAGE_URL)

        prompt.confirmed = False
        with patch("downloader_app.gui.webbrowser.open") as open_url:
            DownloadManagerApp._show_update_prompt(prompt, "0.1.2")
        open_url.assert_not_called()

    def test_update_check_silently_skips_when_offline(self) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app._closing = False
        app.update_events = queue.Queue()

        with (
            patch(
                "downloader_app.gui.latest_release_version",
                side_effect=ConnectionError("offline"),
            ),
            patch("downloader_app.gui.threading.Thread") as thread,
            patch("downloader_app.gui.logging.getLogger") as get_logger,
        ):
            DownloadManagerApp._start_update_check(app)
            thread.call_args.kwargs["target"]()

        get_logger.return_value.warning.assert_not_called()
        self.assertTrue(app.update_events.empty())

    def test_format_duration_supports_long_and_unbounded_etas(self) -> None:
        durations = (
            (None, "--"),
            (0, "0s"),
            (59, "59s"),
            (3600, "1h"),
            (3600 + 15 * 60 + 30, "1h 15min"),
            (86400, "1g"),
            (86400 + 3600 + 125, "1g 1h"),
            (2 * 86400 + 30, "2g"),
            (30 * 86400 + 2 * 86400, "1m 2g"),
            (365 * 86400 + 2 * 30 * 86400, "1y 2m"),
            (float("inf"), "∞"),
            (float("nan"), "--"),
        )
        for seconds, expected in durations:
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FileHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/sample.bin"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_locale_json_files_share_keys_and_placeholders(self) -> None:
        locale_directory = (
            Path(__file__).parents[1] / "downloader_app" / "locales"
        )
        locale_files = sorted(locale_directory.glob("*.json"))
        self.assertEqual(len(locale_files), 20)
        locales = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in locale_files
        }
        english = locales["en.json"]
        expected_keys = set(english)
        placeholders = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")
        expected_tokens = {
            key: set(placeholders.findall(value))
            for key, value in english.items()
            if isinstance(value, str)
        }
        for filename, translations in locales.items():
            with self.subTest(locale=filename):
                self.assertEqual(set(translations), expected_keys)
                for key, value in translations.items():
                    self.assertIsInstance(value, str)
                    if key in expected_tokens:
                        self.assertEqual(
                            set(placeholders.findall(value)),
                            expected_tokens[key],
                            key,
                        )
        from downloader_app.localization import available_locales

        self.assertEqual(len(available_locales()), 20)
        for filename, translations in locales.items():
            with self.subTest(locale=filename):
                self.assertEqual(set(translations), expected_keys)
        from downloader_app.localization import available_locales, load_locale

        self.assertEqual(len(available_locales()), 20)
        for locale in available_locales():
            with self.subTest(locale=locale):
                self.assertEqual(set(load_locale(locale)), expected_keys)

    def test_package_version_matches_windows_installer_display_name(self) -> None:
        installer = (
            Path(__file__).parents[1] / "installer" / "Downetic.iss"
        ).read_text(encoding="utf-8")
        self.assertIn(f'#define AppVersion "{__version__}"', installer)
        self.assertIn("AppVerName=Downetic v{#AppVersion}", installer)
        self.assertIn(
            "UninstallDisplayName=Downetic v{#AppVersion}",
            installer,
        )

    def test_installer_upgrades_existing_install_without_touching_user_data(self) -> None:
        project_root = Path(__file__).parents[1]
        installer = (project_root / "installer" / "Downetic.iss").read_text(
            encoding="utf-8"
        )
        storage = (project_root / "downloader_app" / "storage.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "AppId={{B89BD8C2-E973-4C8A-B577-76A2884FC25B}",
            installer,
        )
        self.assertIn("UsePreviousAppDir=yes", installer)
        self.assertIn("CloseApplications=force", installer)
        self.assertIn("RestartApplications=no", installer)
        self.assertIn('Path(os.environ.get("LOCALAPPDATA"', storage)
        self.assertNotIn("[UninstallDelete]", installer)

    def test_parse_urls_filters_and_deduplicates(self) -> None:
        magnet = "magnet:?xt=urn:btih:0123456789abcdef&dn=Sample"
        links = parse_urls(
            f"See {self.url} and {self.url}. Ignore ftp://example.com/file "
            f"and malformed https:// plus {magnet}."
        )
        self.assertEqual(links, [self.url, magnet])

    def test_windows_download_notification_uses_encoded_script(self) -> None:
        with (
            patch("downloader_app.gui.sys.platform", "win32"),
            patch("downloader_app.gui.subprocess.Popen") as start_process,
        ):
            _show_windows_notification("Downetic - Completato", "O'Brien.mp4")

        command = start_process.call_args.args[0]
        encoded_script = command[command.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded_script).decode("utf-16le")
        self.assertIn("$notification.ShowBalloonTip(5000)", script)
        self.assertIn("$notification.BalloonTipTitle = 'Downetic - Completato'", script)
        self.assertIn("$notification.BalloonTipText = 'O''Brien.mp4'", script)
        self.assertEqual(
            start_process.call_args.kwargs["creationflags"],
            subprocess.CREATE_NO_WINDOW,
        )

    def test_completion_event_notifies_only_when_download_becomes_completed(self) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app.events = queue.Queue()
        app.update_events = queue.Queue()
        record = types.SimpleNamespace(id=1, status="downloading", filename="sample.mp4")
        app.records = {record.id: record}
        app.storage = types.SimpleNamespace(update_download=Mock())
        app._upsert_row = Mock()
        app._refresh_status = Mock()
        app._show_selected_progress = Mock()
        app.root = types.SimpleNamespace(after=Mock())
        app._t = lambda key: {
            "app.title": "Downetic",
            "download.status.completed": "Completed",
        }[key]

        with patch("downloader_app.gui._show_windows_notification") as notify:
            app.events.put({"id": record.id, "status": "completed"})
            app._poll_events()
            notify.assert_called_once_with("Downetic - Completed", "sample.mp4")

            app.events.put({"id": record.id, "status": "completed"})
            app._poll_events()
            notify.assert_called_once()

    def test_focus_grabber_opens_add_links_panel_explicitly(self) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app.grabber_tab = object()
        app.tabs = types.SimpleNamespace(select=Mock())
        app.input_frame = Mock()
        app.link_text = Mock()

        app._focus_grabber()

        app.tabs.select.assert_called_once_with(app.grabber_tab)
        app.input_frame.pack.assert_called_once_with(
            fill="x", padx=5, pady=5
        )
        app.link_text.focus_set.assert_called_once()
        app.link_text.event_generate.assert_called_once_with("<<Paste>>")

    def test_add_links_closes_panel_after_queueing_links(self) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app.link_text = Mock()
        app.link_text.get.return_value = "https://example.com/file.mp4"
        app.input_frame = Mock()
        app.tabs = types.SimpleNamespace(select=Mock())
        app.downloads_tab = object()
        app._queue_links = Mock()

        app._add_links()

        app._queue_links.assert_called_once_with(
            ["https://example.com/file.mp4"]
        )
        app.link_text.delete.assert_called_once_with("1.0", "end")
        app.input_frame.pack_forget.assert_called_once()
        app.tabs.select.assert_called_once_with(app.downloads_tab)

    def test_switching_to_downloads_refreshes_download_information(self) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app.downloads_tab = object()
        app.tabs = types.SimpleNamespace(
            select=Mock(return_value=str(app.downloads_tab))
        )
        app._show_selected_progress = Mock()

        app._on_tab_changed()

        app._show_selected_progress.assert_called_once_with()

    def test_switching_away_from_downloads_does_not_refresh_download_information(
        self,
    ) -> None:
        app = DownloadManagerApp.__new__(DownloadManagerApp)
        app.downloads_tab = object()
        app.tabs = types.SimpleNamespace(select=Mock(return_value="grabber-tab"))
        app._show_selected_progress = Mock()

        app._on_tab_changed()

        app._show_selected_progress.assert_not_called()

    def test_extract_media_links_from_mixed_page(self) -> None:
        links = extract_media_links(
            """
            <nav><a href="/about">About</a><a href="/docs/manual.pdf">Manual</a></nav>
            <article>
              <video title="Trailer">
                <source src="/media/trailer.mp4?token=abc">
                <source src="/media/trailer.m3u8?token=abc">
              </video>
              <audio src="https://cdn.example/music.mp3"></audio>
              <a href="/media/clip.webm">Watch clip</a>
              <a href="/serve?id=bonus" type="video/mp4">Bonus clip</a>
              <meta property="og:video" content="/media/trailer.mp4?token=abc">
            </article>
            """,
            "https://example.com/story/",
        )
        self.assertEqual(
            [(link.kind, link.url) for link in links],
            [
                ("Video", "https://example.com/media/trailer.mp4?token=abc"),
                ("Stream", "https://example.com/media/trailer.m3u8?token=abc"),
                ("Audio", "https://cdn.example/music.mp3"),
                ("Video", "https://example.com/media/clip.webm"),
                ("Video", "https://example.com/serve?id=bonus"),
            ],
        )
        self.assertEqual(
            [link.format for link in links],
            ["MP4", "M3U8", "MP3", "WEBM", "MP4"],
        )
        self.assertEqual(links[0].name, "Trailer")
        self.assertEqual(links[0].page_url, "https://example.com/story/")

    def test_extract_media_links_finds_lazy_sources_and_script_player_urls(self) -> None:
        links = extract_media_links(
            r"""
            <video data-lazy-src="/media/lazy.mp4"></video>
            <script>
              player.setup({file: "https:\/\/cdn.example\/hidden.m3u8?key=abc\u0026v=1"});
            </script>
            <iframe data-src="/embed/123"></iframe>
            """,
            "https://example.com/watch",
        )

        self.assertEqual(
            [(link.kind, link.url) for link in links],
            [
                ("Video", "https://example.com/media/lazy.mp4"),
                ("Stream", "https://cdn.example/hidden.m3u8?key=abc&v=1"),
            ],
        )

    def test_scan_media_pages_finds_unstarted_video_with_extractor(self) -> None:
        page_url = "https://example.com/watch/123"
        extracted = MediaLink(
            "Unstarted video",
            "Video",
            "MP4",
            page_url,
            page_url,
        )

        with (
            patch(
                "downloader_app.media_links.requests.get",
                return_value=self._html_response(
                    page_url,
                    b"<html><button class='play'>Play</button></html>",
                ),
            ),
            patch(
                "downloader_app.media_links._extract_video_page",
                return_value=extracted,
            ) as extract_video,
        ):
            links = scan_media_pages([page_url])

        self.assertEqual(links, [extracted])
        extract_video.assert_called_once_with(page_url, page_url)

    def test_scan_media_pages_descends_into_player_iframes(self) -> None:
        page_url = "https://example.com/article"
        player_url = "https://player.example/embed/123"
        responses = [
            self._html_response(
                page_url,
                b'<iframe src="https://player.example/embed/123"></iframe>',
            ),
            self._html_response(player_url, b"<html><button>Play</button></html>"),
        ]
        extracted = MediaLink(
            "Embedded video",
            "Video",
            "MP4",
            player_url,
            page_url,
        )

        with (
            patch(
                "downloader_app.media_links.requests.get",
                side_effect=responses,
            ) as get,
            patch(
                "downloader_app.media_links._extract_video_page",
                side_effect=[None, extracted],
            ),
        ):
            links = scan_media_pages([page_url])

        self.assertEqual(links, [extracted])
        self.assertEqual(get.call_count, 2)

    def test_javascript_scan_detects_requested_hls_manifest(self) -> None:
        page_url = "https://example.com/watch"
        stream_url = "https://cdn.example/playback/session/opaque-token-123"
        extracted_video_url = "https://cdn.example/live/preview.mp4"

        def run_browser(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            netlog_argument = next(
                argument
                for argument in command
                if argument.startswith("--log-net-log=")
            )
            netlog_path = Path(netlog_argument.split("=", 1)[1])
            netlog_path.write_text(
                json.dumps({
                    "events": [
                        {
                            "source": {"id": 42},
                            "params": {"url": stream_url},
                        },
                        {
                            "source": {"id": 42},
                            "params": {
                                "headers": [
                                    "HTTP/1.1 200 OK",
                                    "Content-Type: application/vnd.apple.mpegurl",
                                ],
                            },
                        },
                    ],
                }),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f'<video src="{extracted_video_url}"></video>',
                stderr="",
            )

        with (
            patch(
                "downloader_app.media_links._browser_executable",
                return_value=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            ),
            patch(
                "downloader_app.media_links.subprocess.run",
                side_effect=run_browser,
            ) as run,
        ):
            links, error = _scan_javascript_media(page_url)

        self.assertEqual(error, "")
        self.assertEqual(
            [(link.kind, link.format, link.url) for link in links],
            [
                ("Video", "MP4", extracted_video_url),
                ("Stream", "M3U8", stream_url),
            ],
        )
        self.assertIn("--headless=new", run.call_args.args[0])
        self.assertNotIn("--no-sandbox", run.call_args.args[0])

    def test_scan_media_pages_uses_browser_for_javascript_streams(self) -> None:
        page_url = "https://example.com/watch"
        stream = MediaLink(
            "index.m3u8",
            "Stream",
            "M3U8",
            "https://cdn.example/live/index.m3u8",
            page_url,
        )

        with (
            patch(
                "downloader_app.media_links.requests.get",
                return_value=self._html_response(
                    page_url,
                    b"<html><button>Play</button></html>",
                ),
            ),
            patch(
                "downloader_app.media_links._extract_video_page",
                return_value=None,
            ),
            patch(
                "downloader_app.media_links._scan_javascript_media",
                return_value=([stream], ""),
            ) as scan_browser,
        ):
            links = scan_media_pages([page_url])

        self.assertEqual(links, [stream])
        scan_browser.assert_called_once_with(page_url)

    def test_scan_stops_fallbacks_after_access_denied(self) -> None:
        page_url = "https://example.com/watch"
        progress = []

        class DeniedResponse:
            url = page_url
            headers = {"Content-Type": "text/html", "Retry-After": "60"}

            def __enter__(self) -> DeniedResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                error = HTTPError("403 Client Error: Forbidden")
                error.response = types.SimpleNamespace(
                    status_code=403,
                    headers=self.headers,
                )
                raise error

        with (
            patch(
                "downloader_app.media_links.requests.get",
                return_value=DeniedResponse(),
            ),
            patch(
                "downloader_app.media_links._extract_video_page"
            ) as extract_video,
            patch(
                "downloader_app.media_links._scan_javascript_media"
            ) as scan_browser,
        ):
            links = scan_media_pages([page_url], progress.append)

        self.assertEqual(links, [])
        self.assertIn("403", progress[-1].error)
        extract_video.assert_not_called()
        scan_browser.assert_not_called()

    def test_scan_reports_retry_after_without_retrying_rate_limited_page(self) -> None:
        page_url = "https://example.com/watch"
        progress = []

        class RateLimitedResponse:
            url = page_url
            headers = {
                "Content-Type": "text/html",
                "Retry-After": "120",
            }

            def __enter__(self) -> RateLimitedResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                error = HTTPError("429 Client Error: Too Many Requests")
                error.response = types.SimpleNamespace(
                    status_code=429,
                    headers=self.headers,
                )
                raise error

        with (
            patch(
                "downloader_app.media_links.requests.get",
                return_value=RateLimitedResponse(),
            ),
            patch(
                "downloader_app.media_links._extract_video_page"
            ) as extract_video,
            patch(
                "downloader_app.media_links._scan_javascript_media"
            ) as scan_browser,
        ):
            links = scan_media_pages([page_url], progress.append)

        self.assertEqual(links, [])
        self.assertIn("429", progress[-1].error)
        self.assertIn("Retry-After: 120", progress[-1].error)
        extract_video.assert_not_called()
        scan_browser.assert_not_called()

    @staticmethod
    def _html_response(url: str, body: bytes) -> object:
        class HtmlResponse:
            headers = {"Content-Type": "text/html; charset=utf-8"}
            encoding = "utf-8"

            def __init__(self) -> None:
                self.url = url

            def __enter__(self) -> HtmlResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int) -> list[bytes]:
                self.chunk_size = chunk_size
                return [body]

        return HtmlResponse()

    def test_scan_media_pages_finds_direct_media_responses(self) -> None:
        class MediaResponse:
            url = "https://cdn.example/video"
            headers = {"Content-Type": "video/mp4"}

            def __enter__(self) -> MediaResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

        with patch(
            "downloader_app.media_links.requests.get",
            return_value=MediaResponse(),
        ) as get:
            links = scan_media_pages(["https://example.com/video-page"])
        get.assert_called_once()
        self.assertEqual(
            [(link.kind, link.url) for link in links],
            [("Video", "https://cdn.example/video")],
        )

    def test_scan_media_pages_extracts_media_from_html(self) -> None:
        class HtmlResponse:
            url = "https://example.com/story"
            headers = {"Content-Type": "text/html; charset=utf-8"}
            encoding = "utf-8"

            def __enter__(self) -> HtmlResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int) -> list[bytes]:
                self.chunk_size = chunk_size
                return [
                    b'<main><a href="/guide">Guide</a><video>'
                    b'<source src="/media/clip.mp4"></video></main>'
                ]

        with patch(
            "downloader_app.media_links.requests.get",
            return_value=HtmlResponse(),
        ):
            links = scan_media_pages(["https://example.com/story"])
        self.assertEqual(
            [(link.kind, link.url) for link in links],
            [("Video", "https://example.com/media/clip.mp4")],
        )

    def test_scan_continues_after_page_error_and_reports_progress(self) -> None:
        class MediaResponse:
            url = "https://cdn.example/final.mp4"
            headers = {"Content-Type": "video/mp4"}

            def __enter__(self) -> MediaResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

        progress = []
        with (
            patch(
                "downloader_app.media_links.requests.get",
                side_effect=[ConnectionError("offline"), MediaResponse()],
            ),
            patch(
                "downloader_app.media_links._scan_javascript_media",
                return_value=([], ""),
            ),
        ):
            links = scan_media_pages(
                ["https://example.com/unavailable", "https://example.com/video"],
                progress.append,
            )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].format, "MP4")
        self.assertEqual(len(progress), 4)
        self.assertEqual(progress[1].error, "offline")
        self.assertEqual(progress[-1].completed, 2)
        self.assertEqual(progress[-1].found, 1)

    def test_video_runtime_options_enable_installed_node(self) -> None:
        with patch("downloader_app.downloader.shutil.which") as which:
            which.side_effect = lambda name: (
                r"C:\Program Files\nodejs\node.exe" if name == "node" else None
            )
            self.assertEqual(
                _javascript_runtime_options(),
                {"js_runtimes": {"node": {"path": r"C:\Program Files\nodejs\node.exe"}}},
            )

    def test_video_runtime_options_use_installer_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime_directory = Path(directory) / "runtime"
            node_path = (
                runtime_directory
                / "node"
                / "node-v24.21.0-win-x64"
                / "node.exe"
            )
            ffmpeg_path = (
                runtime_directory
                / "ffmpeg"
                / "ffmpeg-N-127271-gba987fe24d-win64-lgpl-shared"
                / "bin"
                / "ffmpeg.exe"
            )
            node_path.parent.mkdir(parents=True)
            ffmpeg_path.parent.mkdir(parents=True)
            node_path.touch()
            ffmpeg_path.touch()

            with (
                patch(
                    "downloader_app.downloader._runtime_directory",
                    return_value=runtime_directory,
                ),
                patch("downloader_app.downloader.shutil.which") as which,
            ):
                self.assertEqual(
                    _javascript_runtime_options(),
                    {"js_runtimes": {"node": {"path": str(node_path)}}},
                )
                self.assertEqual(
                    _ffmpeg_options(),
                    {"ffmpeg_location": str(ffmpeg_path.parent)},
                )
                which.assert_not_called()

    def test_youtube_without_javascript_runtime_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("downloader_app.downloader.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "YouTube downloads require Node.js"):
                    download_video(
                        "https://www.youtube.com/watch?v=example",
                        directory,
                        TransferControl(threading.Event(), threading.Event()),
                        lambda _event: None,
                    )

    def test_video_output_format_options(self) -> None:
        self.assertEqual(_video_output_options("original"), {})
        self.assertEqual(
            _video_output_options("mp4")["merge_output_format"], "mp4"
        )
        self.assertEqual(
            _video_output_options("webm")["merge_output_format"], "webm"
        )
        self.assertEqual(
            _video_output_options("mp3")["postprocessors"][0]["preferredcodec"],
            "mp3",
        )
        with self.assertRaises(ValueError):
            _video_output_options("flv")

    def test_hls_manifest_always_uses_video_downloader(self) -> None:
        url = "https://cdn.example/video/index-v1-a1.m3u8?token=example"
        self.assertEqual(select_engine(url, "auto"), "video")
        self.assertEqual(select_engine(url, "http"), "video")
        dash_url = "https://cdn.example/video/manifest.mpd?token=example"
        self.assertEqual(select_engine(dash_url, "auto"), "video")
        self.assertEqual(select_engine(dash_url, "http"), "video")

    def test_torrent_links_always_use_torrent_downloader(self) -> None:
        magnet = "magnet:?xt=urn:btih:0123456789abcdef&dn=Sample"
        torrent_url = "https://example.com/sample.torrent?token=abc"
        self.assertEqual(select_engine(magnet, "auto"), "torrent")
        self.assertEqual(select_engine(magnet, "http"), "torrent")
        self.assertEqual(select_engine(torrent_url, "auto"), "torrent")
        self.assertEqual(select_engine(torrent_url, "video"), "torrent")

    def test_tiktok_video_and_short_links_use_video_downloader(self) -> None:
        video_urls = (
            "https://www.tiktok.com/@creator/video/1234567890123456789",
            "https://vm.tiktok.com/ZM123456/",
            "https://vt.tiktok.com/ZM123456/",
            "https://www.tiktok.com/t/ZM123456/",
            "https://www.tiktokv.com/v/1234567890123456789.html",
        )
        for url in video_urls:
            with self.subTest(url=url):
                self.assertEqual(select_engine(url, "auto"), "video")

    def test_tiktok_collection_urls_are_rejected_before_download(self) -> None:
        collection_urls = (
            "https://www.tiktok.com/@creator",
            "https://www.tiktok.com/@creator/collection/favorites-123",
            "https://www.tiktok.com/tag/example",
            "https://www.tiktok.com/music/example-123",
        )
        for url in collection_urls:
            with self.subTest(url=url):
                self.assertTrue(_is_tiktok_collection_url(url))
        self.assertFalse(
            _is_tiktok_collection_url(
                "https://www.tiktok.com/@creator/video/1234567890123456789"
            )
        )
        self.assertFalse(
            _is_tiktok_collection_url("https://not-tiktok.com/@creator")
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "one specific video"):
                download_video(
                    collection_urls[0],
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                )

    def test_tiktok_video_url_is_passed_to_ytdlp(self) -> None:
        captured: dict[str, object] = {}

        class FakeYoutubeDL:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def download(self, urls: list[str]) -> int:
                captured["urls"] = urls
                return 0

        url = "https://www.tiktok.com/@creator/video/1234567890123456789"
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                sys.modules,
                {"yt_dlp": types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)},
            ):
                download_video(
                    url,
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                    browser_cookie_browser="edge",
                )
        self.assertEqual(captured["urls"], [url])
        self.assertTrue(captured["noplaylist"])
        self.assertEqual(captured["cookiesfrombrowser"], ("edge",))
        self.assertEqual(_browser_cookie_options("chrome"), {
            "cookiesfrombrowser": ("chrome",),
        })
        with self.assertRaises(ValueError):
            _browser_cookie_options("firefox")

    def test_browser_cookies_apply_to_all_video_sites_when_enabled(self) -> None:
        captured: dict[str, object] = {}

        class FakeYoutubeDL:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def download(self, _urls: list[str]) -> int:
                return 0

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                sys.modules,
                {"yt_dlp": types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)},
            ):
                download_video(
                    "https://example.com/video",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                    browser_cookie_browser="edge",
                )
        self.assertEqual(captured["cookiesfrombrowser"], ("edge",))

    def test_diagnose_download_error_preserves_unrecognized_error(self) -> None:
        original = "Connection reset by peer"
        self.assertEqual(
            diagnose_download_error(original, "https://example.com/video"),
            original,
        )
        self.assertIn(
            "rate-limiting requests",
            diagnose_download_error(
                "HTTP Error 429: Too Many Requests",
                "https://example.com/video",
            ),
        )

    def test_diagnose_chrome_cookie_database_copy_error(self) -> None:
        original = (
            "ERROR: Could not copy Chrome cookie database. See "
            "https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
        )
        diagnosed = diagnose_download_error(original, "https://example.com/video")
        self.assertIn("Fully exit Chrome, including background processes", diagnosed)
        self.assertIn("select Edge or turn off Browser cookies", diagnosed)
        self.assertIn(f"Details: {original}", diagnosed)
        self.assertEqual(
            diagnose_download_error(diagnosed, "https://example.com/video"),
            diagnosed,
        )

    def test_chrome_cookie_copy_error_retries_from_sqlite_snapshot(self) -> None:
        class FakeDownloadError(Exception):
            pass

        with tempfile.TemporaryDirectory() as directory:
            user_data = Path(directory) / "User Data"
            profile = user_data / "Default"
            cookie_database = profile / "Network" / "Cookies"
            cookie_database.parent.mkdir(parents=True)
            connection = sqlite3.connect(cookie_database)
            try:
                connection.execute("CREATE TABLE cookies (name TEXT)")
                connection.execute("INSERT INTO cookies VALUES ('session')")
                connection.commit()
            finally:
                connection.close()
            user_data.mkdir(exist_ok=True)
            (user_data / "Local State").write_text("browser state")
            attempts: list[dict[str, object]] = []
            snapshot_cookie_rows: list[tuple[str]] = []

            def operation(options: dict[str, object]) -> str:
                attempts.append(options)
                if len(attempts) == 1:
                    raise FakeDownloadError(
                        "ERROR: Could not copy Chrome cookie database"
                    )
                browser_cookie_option = options["cookiesfrombrowser"]
                self.assertIsInstance(browser_cookie_option, tuple)
                profile_path = browser_cookie_option[1]
                self.assertIsInstance(profile_path, str)
                snapshot_profile = Path(profile_path)
                self.assertEqual(
                    (snapshot_profile.parent / "Local State").read_text(),
                    "browser state",
                )
                connection = sqlite3.connect(
                    snapshot_profile / "Network" / "Cookies"
                )
                try:
                    snapshot_cookie_rows.extend(
                        connection.execute("SELECT name FROM cookies").fetchall()
                    )
                finally:
                    connection.close()
                return "retried"

            with patch(
                "downloader_app.downloader._browser_profile_root",
                return_value=user_data,
            ):
                result = _run_with_cookie_snapshot_fallback(
                    "chrome",
                    {"cookiesfrombrowser": ("chrome",)},
                    operation,
                    FakeDownloadError,
                )

        self.assertEqual(result, "retried")
        self.assertEqual(len(attempts), 2)
        browser_cookie_option = attempts[1]["cookiesfrombrowser"]
        self.assertIsInstance(browser_cookie_option, tuple)
        browser, profile_path = browser_cookie_option
        self.assertIsInstance(profile_path, str)
        self.assertEqual(browser, "chrome")
        self.assertEqual(snapshot_cookie_rows, [("session",)])
        self.assertFalse(Path(profile_path).exists())

    def test_video_download_includes_subtitles_only_when_enabled(self) -> None:
        captured: dict[str, object] = {}

        class FakeYoutubeDL:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def download(self, _urls: list[str]) -> int:
                return 0

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                sys.modules,
                {"yt_dlp": types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)},
            ):
                download_video(
                    "https://example.com/video",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                    download_subtitles=True,
                )
        self.assertTrue(captured["writesubtitles"])
        self.assertTrue(captured["writeautomaticsub"])
        self.assertEqual(captured["subtitleslangs"], ["all"])
        self.assertEqual(captured["subtitlesformat"], "best")

    def test_youtube_channel_and_playlist_urls_are_rejected(self) -> None:
        collection_urls = (
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/channel/UC123",
            "https://www.youtube.com/@example",
            "https://www.youtube.com/",
        )
        for url in collection_urls:
            with self.subTest(url=url):
                self.assertTrue(_is_youtube_collection_url(url))
        self.assertFalse(
            _is_youtube_collection_url(
                "https://www.youtube.com/watch?v=QxWKRrsDank&list=PL123"
            )
        )

    def test_download_video_rejects_youtube_collection_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Paste the URL of one specific video"):
                download_video(
                    "https://www.youtube.com/playlist?list=PL123",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                )

    def test_video_downloader_passes_format_and_hls_options_to_ytdlp(self) -> None:
        captured: dict[str, object] = {}

        class FakeYoutubeDL:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def download(self, urls: list[str]) -> int:
                captured["urls"] = urls
                progress_hook = captured["progress_hooks"][0]
                captured["hook"] = progress_hook
                return 0

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "video.mp4").write_bytes(b"existing")
            (Path(directory) / "video (1).mkv").write_bytes(b"existing retry")
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                result = download_video(
                    "https://cdn.example/video/index-v1-a1.m3u8",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _event: None,
                    "mp4",
                    "video.mp4",
                )
        self.assertEqual(result, "Video download")
        self.assertEqual(
            Path(str(captured["outtmpl"])).name,
            "video (2).%(ext)s",
        )
        self.assertEqual(captured["merge_output_format"], "mp4")
        self.assertIn("bestvideo", captured["format"])
        self.assertTrue(captured["noplaylist"])
        self.assertEqual(captured["playlist_items"], "1")
        self.assertEqual(
            captured["urls"],
            ["https://cdn.example/video/index-v1-a1.m3u8"],
        )

    def test_video_progress_updates_are_throttled_and_final_is_reported(self) -> None:
        captured: dict[str, object] = {}
        reports: list[dict[str, object]] = []

        class FakeYoutubeDL:
            def __init__(self, options: dict[str, object]) -> None:
                captured.update(options)

            def __enter__(self) -> FakeYoutubeDL:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def download(self, _urls: list[str]) -> int:
                hook = captured["progress_hooks"][0]
                for index in range(100):
                    hook({
                        "status": "downloading",
                        "filename": "video.mp4",
                        "downloaded_bytes": index * 1024,
                        "total_bytes": 200 * 1024,
                        "speed": 1024.0,
                        "eta": 100.0,
                    })
                hook({
                    "status": "finished",
                    "filename": "video.mp4",
                    "downloaded_bytes": 200 * 1024,
                    "total_bytes": 200 * 1024,
                })
                return 0

        fake_module = types.SimpleNamespace(YoutubeDL=FakeYoutubeDL)
        now = [0.0]

        def advance_time() -> float:
            value = now[0]
            now[0] += 0.01
            return value

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                with patch("downloader_app.downloader.time.monotonic", side_effect=advance_time):
                    download_video(
                        "https://cdn.example/video/index-v1-a1.m3u8",
                        directory,
                        TransferControl(threading.Event(), threading.Event()),
                        reports.append,
                    )
        self.assertLessEqual(len(reports), 3)
        self.assertEqual(reports[-1]["progress"], 200 * 1024)

    def test_torrent_download_reports_progress_with_libtorrent(self) -> None:
        reports: list[dict[str, object]] = []
        saved_paths: list[str] = []
        configured_interfaces: list[tuple[str, str]] = []
        file_priorities: list[list[int]] = []
        trackers: list[dict[str, str]] = []
        selected_files = iter(
            (frozenset({"Subs/en.srt"}), frozenset({"video.mkv"}))
        )

        class FakeSettingsPack:
            enable_dht = "enable_dht"
            enable_lsd = "enable_lsd"
            enable_upnp = "enable_upnp"
            enable_natpmp = "enable_natpmp"
            outgoing_interfaces = "outgoing_interfaces"

            def set_bool(self, _key: str, _value: bool) -> None:
                return None

            def set_str(self, key: str, value: str) -> None:
                configured_interfaces.append((key, value))

        class FakeParams:
            save_path = ""

        class FakeTorrentFile:
            def name(self) -> str:
                return "Example Torrent"

            def files(self) -> object:
                return FakeFileStorage()

        class FakeFileStorage:
            def num_files(self) -> int:
                return 2

            def file_path(self, index: int) -> str:
                return ("video.mkv", "Subs/en.srt")[index]

            def file_size(self, index: int) -> int:
                return (2048, 128)[index]

        class FakeStatus:
            def __init__(self, done: int, seeding: bool) -> None:
                self.has_metadata = True
                self.total_wanted = 100
                self.total_wanted_done = done
                self.download_rate = 50.0
                self.num_seeds = 4
                self.num_peers = 12
                self.is_seeding = seeding

        class FakeHandle:
            def __init__(self) -> None:
                self.statuses = [
                    FakeStatus(40, False),
                    FakeStatus(100, True),
                ]

            def add_tracker(self, tracker: dict[str, str]) -> None:
                trackers.append(tracker)

            def trackers(self) -> list[dict[str, str]]:
                return trackers

            def prioritize_files(self, priorities: list[int]) -> None:
                file_priorities.append(priorities)

            def status(self) -> FakeStatus:
                return self.statuses.pop(0)

            def torrent_file(self) -> FakeTorrentFile:
                return FakeTorrentFile()

            def pause(self) -> None:
                return None

            def resume(self) -> None:
                return None

        class FakeSession:
            def apply_settings(self, _settings: FakeSettingsPack) -> None:
                return None

            def start_dht(self) -> None:
                return None

            def start_lsd(self) -> None:
                return None

            def start_upnp(self) -> None:
                return None

            def start_natpmp(self) -> None:
                return None

            def add_torrent(self, params: FakeParams) -> FakeHandle:
                saved_paths.append(params.save_path)
                return FakeHandle()

            def remove_torrent(self, _handle: FakeHandle) -> None:
                return None

        def parse_magnet_uri(_url: str) -> FakeParams:
            return FakeParams()

        fake_libtorrent = types.SimpleNamespace(
            session=FakeSession,
            settings_pack=FakeSettingsPack,
            parse_magnet_uri=parse_magnet_uri,
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"libtorrent": fake_libtorrent}):
                result = download_torrent(
                    "magnet:?xt=urn:btih:0123456789abcdef&dn=Example%20Torrent",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    reports.append,
                    "10.0.0.2",
                    lambda: next(selected_files),
                )
            self.assertTrue(Path(saved_paths[0]).is_dir())
        self.assertEqual(result, "Example Torrent")
        self.assertEqual(reports[-1]["progress"], 100)
        self.assertEqual(reports[-1]["seeds"], 4)
        self.assertEqual(reports[-1]["peers"], 12)
        self.assertEqual(file_priorities, [[0, 4], [4, 0]])
        self.assertEqual(
            next(
                event["torrent_tracker"]
                for event in reports
                if "torrent_tracker" in event
            ),
            "tracker.openbittorrent.com +3",
        )
        self.assertEqual(
            configured_interfaces,
            [("outgoing_interfaces", "10.0.0.2")],
        )
        self.assertEqual(
            json.loads(str(reports[-1]["torrent_files"])),
            [
                {"path": "video.mkv", "size": 2048},
                {"path": "Subs/en.srt", "size": 128},
            ],
        )

    def test_torrent_download_binds_vpn_with_dict_settings_api(self) -> None:
        applied_settings: list[dict[str, object]] = []

        class FakeParams:
            save_path = ""

        class FakeStatus:
            has_metadata = False
            total_wanted = 100
            total_wanted_done = 100
            download_rate = 0
            num_seeds = 1
            num_peers = 1
            is_seeding = True

        class FakeHandle:
            def add_tracker(self, _tracker: dict[str, str]) -> None:
                return None

            def status(self) -> FakeStatus:
                return FakeStatus()

        class FakeSession:
            def __init__(self) -> None:
                self.settings: dict[str, object] = {
                    "enable_dht": False,
                    "enable_lsd": False,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                    "outgoing_interfaces": "",
                }

            def get_settings(self) -> dict[str, object]:
                return self.settings

            def apply_settings(self, settings: dict[str, object]) -> None:
                applied_settings.append(settings.copy())

            def start_dht(self) -> None:
                return None

            def start_lsd(self) -> None:
                return None

            def start_upnp(self) -> None:
                return None

            def start_natpmp(self) -> None:
                return None

            def add_torrent(self, _params: FakeParams) -> FakeHandle:
                return FakeHandle()

            def remove_torrent(self, _handle: FakeHandle) -> None:
                return None

        fake_libtorrent = types.SimpleNamespace(
            session=FakeSession,
            parse_magnet_uri=lambda _url: FakeParams(),
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"libtorrent": fake_libtorrent}):
                result = download_torrent(
                    "magnet:?xt=urn:btih:0123456789abcdef&dn=Example%20Torrent",
                    directory,
                    TransferControl(threading.Event(), threading.Event()),
                    lambda _values: None,
                    "10.0.0.2",
                )

        self.assertEqual(result, "Example Torrent")
        self.assertEqual(len(applied_settings), 1)
        self.assertEqual(applied_settings[0]["outgoing_interfaces"], "10.0.0.2")
        self.assertTrue(applied_settings[0]["enable_dht"])
        self.assertTrue(applied_settings[0]["enable_lsd"])
        self.assertTrue(applied_settings[0]["enable_upnp"])
        self.assertTrue(applied_settings[0]["enable_natpmp"])

    def test_storage_migrates_legacy_downloads_and_saves_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "downloads.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """CREATE TABLE downloads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress INTEGER NOT NULL DEFAULT 0,
                        total INTEGER,
                        speed REAL NOT NULL DEFAULT 0,
                        eta REAL,
                        destination TEXT NOT NULL,
                        engine TEXT NOT NULL DEFAULT 'auto',
                        error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                connection.execute(
                    """INSERT INTO downloads (url, filename, status, destination)
                       VALUES ('https://example.com/old', 'old', 'queued', ?)""",
                    (directory,),
                )
                connection.commit()
            finally:
                connection.close()

            storage = Storage(database)
            try:
                new_id = storage.add_download(
                    "https://example.com/new", directory, "video", "mp4"
                )
                records = {record.id: record for record in storage.get_downloads()}
                self.assertEqual(records[1].output_format, "original")
                self.assertEqual(records[new_id].output_format, "mp4")
                self.assertEqual(records[1].torrent_files, "[]")
                self.assertEqual(records[1].torrent_directory, "")
                self.assertEqual(records[1].torrent_selected_files, "")
                self.assertEqual(records[1].torrent_tracker, "")
                storage.update_download(
                    new_id,
                    torrent_files='[{"path":"video.mkv","size":2048}]',
                    torrent_directory=str(Path(directory) / "Example Torrent"),
                    torrent_selected_files='["video.mkv"]',
                    torrent_tracker="tracker.example +7",
                )
                records = {record.id: record for record in storage.get_downloads()}
                self.assertEqual(
                    records[new_id].torrent_files,
                    '[{"path":"video.mkv","size":2048}]',
                )
                self.assertEqual(
                    records[new_id].torrent_directory,
                    str(Path(directory) / "Example Torrent"),
                )
                self.assertEqual(
                    records[new_id].torrent_selected_files, '["video.mkv"]'
                )
                self.assertEqual(
                    records[new_id].torrent_tracker, "tracker.example +7"
                )
                storage.delete_download(new_id)
                self.assertNotIn(new_id, {
                    record.id for record in storage.get_downloads()
                })
            finally:
                storage.close()

    def test_data_directory_always_uses_downetic_folder(self) -> None:
        import os

        from downloader_app.storage import default_data_directory

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                self.assertEqual(default_data_directory(), Path(directory) / "Downetic")
                legacy_directory = Path(directory) / "ClassicDownloadManager"
                legacy_directory.mkdir()
                self.assertEqual(default_data_directory(), Path(directory) / "Downetic")

    def test_download_and_history_views_keep_completed_records(self) -> None:
        import os
        import tkinter as tk
        from tkinter import ttk

        from downloader_app.gui import DownloadManagerApp

        with tempfile.TemporaryDirectory() as directory:
            previous_local_app_data = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = directory
            root = tk.Tk()
            app = DownloadManagerApp(root)
            try:
                self.assertEqual(root.title(), "Downetic")
                self.assertEqual(
                    (app._app_icon.width(), app._app_icon.height()),
                    (1254, 1254),
                )
                self.assertEqual(
                    set(app.dialog_icons),
                    {"info", "warning", "error"},
                )
                self.assertEqual(
                    {
                        (icon.width(), icon.height())
                        for icon in app.dialog_icons.values()
                    },
                    {(43, 43)},
                )
                root.after(
                    0,
                    lambda: [
                        child.destroy()
                        for child in root.winfo_children()
                        if isinstance(child, tk.Toplevel)
                    ],
                )
                app._show_info("Info", "Popup test")
                self.assertEqual(
                    set(app.toolbar_icons),
                    {
                        "add",
                        "start",
                        "pause",
                        "stop",
                        "move_up",
                        "move_down",
                        "retry",
                        "clipboard",
                    },
                )
                self.assertEqual(app.toolbar_icons["start"].width(), 26)
                app.tabs.select(app.grabber_tab)
                root.update()
                self.assertAlmostEqual(float(app.grabber_tree.xview()[1]), 1.0)
                root.geometry("800x500")
                root.update()
                self.assertTrue(app.grabber_x_scrollbar.winfo_ismapped())
                self.assertAlmostEqual(float(app.grabber_tree.xview()[1]), 1.0)
                app.tabs.select(app.downloads_tab)
                self.assertEqual(
                    ttk.Style(app.root).lookup(
                        "TNotebook.Tab", "background", ("selected",)
                    ),
                    "#fff4c7",
                )
                self.assertEqual(
                    ttk.Style(app.root).lookup(
                        "Horizontal.TProgressbar", "background"
                    ),
                    "#28a745",
                )
                self.assertEqual(
                    [
                        app.tabs.tab(tab, "text")
                        for tab in app.tabs.tabs()
                        if app.tabs.tab(tab, "state") != "hidden"
                    ],
                    ["Downloads", "Link Grabber", "Torrent", "Help"],
                )
                app._set_language("it")
                self.assertEqual(
                    app.tabs.tab(app.grabber_tab, "text"), "Raccolta link"
                )
                self.assertEqual(
                    app.grabber_tree.heading("url", "text"),
                    "URL diretto del media",
                )
                self.assertEqual(
                    app.settings_menu.entrycget(
                        app.settings_menu.index("end"), "label"
                    ),
                    "Lingua",
                )
                self.assertEqual(
                    app.settings_menu.entrycget(3, "label"),
                    "Cookie del browser",
                )
                self.assertEqual(
                    app.browser_cookie_menu.entrycget(0, "label"),
                    "Disattivati",
                )
                app._set_browser_cookie_browser("edge")
                self.assertEqual(
                    app.storage.get_setting("browser_cookie_browser", ""),
                    "edge",
                )
                self.assertEqual(app.manager._browser_cookie_browser, "edge")
                app._set_browser_cookie_browser("")
                self.assertEqual(app.file_menu.entrycget(0, "label"), "Aggiungi link...")
                self.assertEqual(
                    app.settings_menu.entrycget(0, "label"), "Cartella di download"
                )
                self.assertEqual(
                    app.download_tree.heading("status", "text"), "Stato"
                )
                self.assertEqual(
                    app.download_tree.heading("connection", "text"),
                    "Seed / Peer",
                )
                self.assertEqual(
                    app.context_menu.entrycget(10, "label"), "Rinomina..."
                )
                self.assertEqual(
                    app.context_menu.entrycget(3, "label"), "Riprova"
                )
                self.assertEqual(
                    app.media_context_menu.entrycget(0, "label"),
                    "Scarica selezionati",
                )
                self.assertEqual(
                    app.format_menu.entrycget(0, "label"), "Originale"
                )
                self.assertEqual(
                    app.stats_vars["links"].get(), "Pacchetti: 0"
                )
                self.assertEqual(app.detail_text.get(), "Nessun download selezionato")
                self.assertEqual(
                    app.empty_labels["history"].cget("text"),
                    "Cronologia vuota",
                )
                self.assertEqual(
                    app.empty_details["history"].cget("text"),
                    "I download completati o interrotti compariranno qui.",
                )
                self.assertEqual(
                    app.contribute_heading.cget("text"),
                    "Una mano?",
                )
                self.assertTrue(app.empty_states_visible["downloads"])
                self.assertTrue(app.empty_states_visible["grabber"])
                self.assertEqual(app.empty_icons["downloads"].cget("width"), "72")
                self.assertEqual(app.engine_var.get(), "Automatico")
                self.assertEqual(
                    app.engine_combo.cget("values"),
                    ("Automatico", "HTTP", "Video", "Torrent"),
                )
                self.assertEqual(app.output_format_var.get(), "Originale")
                self.assertEqual(
                    app.output_format_combo.cget("values"),
                    ("Originale", "MP3", "MP4", "WebM"),
                )
                self.assertEqual(app._engine_value(app.engine_var.get()), "auto")
                self.assertEqual(
                    app._output_format_value(app.output_format_var.get()), "original"
                )
                self.assertEqual(app.storage.get_setting("language", "en"), "it")
                for locale in app.locales:
                    app._set_language(locale)
                    self.assertEqual(root.title(), "Downetic")
                    self.assertEqual(
                        app.download_tree.heading("status", "text"),
                        app._t("download.column.status"),
                    )
                    self.assertEqual(
                        app.download_tree.heading("connection", "text"),
                        f"{app._t('torrent.seeds')} / {app._t('torrent.peers')}",
                    )
                    self.assertEqual(
                        app.format_menu.entrycget(0, "label"),
                        app._t("format.original"),
                    )
                app._set_language("en")
                self.assertEqual(
                    app.tabs.tab(app.grabber_tab, "text"), "Link Grabber"
                )
                self.assertEqual(app.download_tree.get_children(), ())
                self.assertTrue(app.empty_labels["downloads"].winfo_manager())
                app.tabs.select(app.contribute_tab)
                root.update()
                self.assertTrue(app.contribute_heading.winfo_ismapped())
                self.assertTrue(app.contribute_intro.winfo_ismapped())
                app._set_language("it")
                with (
                    patch.object(app.root, "clipboard_clear") as clipboard_clear,
                    patch.object(app.root, "clipboard_append") as clipboard_append,
                ):
                    app._copy_contribution_template()
                clipboard_clear.assert_called_once_with()
                self.assertIn(
                    "Problema in Downetic",
                    clipboard_append.call_args.args[0],
                )
                app._set_language("en")
                app.tabs.select(app.grabber_tab)
                app._media_scan_total = 3
                app._show_media_candidates(
                    [
                        MediaLink(
                            "Movie trailer", "Video", "MP4",
                            "https://cdn.example/trailer.mp4",
                            "https://example.com/story",
                        ),
                        MediaLink(
                            "Song", "Audio", "MP3",
                            "https://audio.example/song.mp3",
                            "https://example.com/story",
                        ),
                        MediaLink(
                            "Second trailer", "Video", "WEBM",
                            "https://cdn.example/second.webm",
                            "https://example.com/story",
                        ),
                    ],
                    [],
                )
                self.assertEqual(len(app.grabber_tree.get_children()), 3)
                self.assertFalse(app.empty_states_visible["grabber"])
                self.assertEqual(app.media_selection_var.get(), "3 selected of 3")
                self.assertEqual(str(app.queue_media_button["state"]), "normal")
                self.assertEqual(
                    app.grabber_tree.item("0", "values")[2], "MP4"
                )
                app._set_language("it")
                self.assertEqual(app.media_type_var.get(), "Tutti i tipi")
                self.assertEqual(
                    app.media_type_combo.cget("values"),
                    ("Tutti i tipi", "Video", "Audio", "Flusso"),
                )
                app.media_type_var.set("Video")
                app._apply_media_filters()
                self.assertEqual(
                    set(app.grabber_tree.get_children()), {"0", "2"}
                )
                app._set_language("en")
                self.assertEqual(app.media_type_var.get(), "Video")
                app.media_type_var.set("All types")
                app._apply_media_filters()
                app.grabber_tree.selection_set("0")
                root.update()
                self.assertEqual(app.media_selection_var.get(), "1 selected of 3")
                with patch("downloader_app.gui.webbrowser.open", return_value=True) as open_url:
                    app._open_selected_media()
                open_url.assert_called_once_with("https://cdn.example/trailer.mp4")
                with (
                    patch.object(app.root, "clipboard_clear") as clipboard_clear,
                    patch.object(app.root, "clipboard_append") as clipboard_append,
                ):
                    app._copy_selected_media()
                clipboard_clear.assert_called_once_with()
                clipboard_append.assert_called_once_with(
                    "https://cdn.example/trailer.mp4"
                )
                app.media_type_var.set("Video")
                app._apply_media_filters()
                self.assertEqual(
                    set(app.grabber_tree.get_children()), {"0", "2"}
                )
                app.media_host_var.set("audio.example")
                app._apply_media_filters()
                self.assertEqual(app.grabber_tree.get_children(), ())
                self.assertTrue(app.empty_states_visible["grabber"])
                app.media_search_var.set("song")
                app.media_type_var.set("All types")
                app.media_host_var.set("All hosts")
                app._apply_media_filters()
                self.assertEqual(app.grabber_tree.get_children(), ("1",))
                self.assertFalse(app.empty_states_visible["grabber"])
                app.media_search_var.set("")
                app._apply_media_filters()
                app.selected_only_var.set(True)
                app._clear_media_selection()
                self.assertEqual(app.grabber_tree.get_children(), ())
                app.selected_only_var.set(False)
                app._apply_media_filters()

                app.tabs.select(app.downloads_tab)
                record_id = app.storage.add_download(
                    "https://www.youtube.com/watch?v=QxWKRrsDank",
                    directory,
                    "video",
                )
                record = DownloadRecord(
                    id=record_id,
                    url="https://www.youtube.com/watch?v=QxWKRrsDank",
                    filename="video.mp4",
                    status="downloading",
                    progress=100,
                    total=200,
                    speed=1.0,
                    eta=100.0,
                    destination=directory,
                    engine="video",
                    error="",
                    created_at="",
                )
                app.records[record.id] = record
                app._upsert_row(record)
                self.assertTrue(app.download_tree.exists(str(record.id)))
                self.assertFalse(app.history_tree.exists(str(record.id)))
                video_values = app.download_tree.item(str(record.id), "values")
                self.assertEqual(video_values[5], "1 B/s")
                self.assertEqual(video_values[3], "-")
                self.assertEqual(video_values[10], "-")
                torrent_id = app.storage.add_download(
                    "magnet:?xt=urn:btih:0123456789abcdef&dn=sample",
                    directory,
                    "auto",
                )
                torrent_record = DownloadRecord(
                    id=torrent_id,
                    url="magnet:?xt=urn:btih:0123456789abcdef&dn=sample",
                    filename="sample",
                    status="downloading",
                    progress=100,
                    total=200,
                    speed=1024.0,
                    eta=100.0,
                    destination=directory,
                    engine="auto",
                    error="",
                    created_at="",
                    seeds=4,
                    peers=12,
                )
                app.records[torrent_record.id] = torrent_record
                app._upsert_row(torrent_record)
                torrent_values = app.download_tree.item(
                    str(torrent_record.id), "values"
                )
                self.assertEqual(torrent_values[5], "1.0 KB/s")
                self.assertEqual(torrent_values[3], "4 / 12")
                self.assertEqual(torrent_values[2], "-")
                self.assertEqual(torrent_values[10], "-")
                torrent_record.torrent_tracker = "tracker.example +7"
                app._upsert_row(torrent_record)
                self.assertEqual(
                    app.download_tree.item(
                        str(torrent_record.id), "values"
                    )[2],
                    "tracker.example +7",
                )
                root.update()
                torrent_bounds = app.download_tree.bbox(str(torrent_record.id))
                with patch.object(app.context_menu, "tk_popup"):
                    app._show_download_context_menu(
                        types.SimpleNamespace(
                            widget=app.download_tree,
                            y=torrent_bounds[1] + 1,
                            x_root=10,
                            y_root=10,
                        )
                    )
                self.assertEqual(
                    app.context_menu.entrycget(6, "label"), "Open magnet"
                )
                self.assertEqual(
                    app.context_menu.entrycget(7, "label"), "Copy magnet link"
                )
                self.assertEqual(app.context_menu.entrycget(8, "state"), "normal")
                torrent_record.torrent_files = json.dumps(
                    [{"path": "video.mkv", "size": 2048}],
                    ensure_ascii=False,
                )
                with patch.object(app, "_show_torrent_info_dialog") as show_info:
                    app._show_selected_torrent_info()
                show_info.assert_called_once()
                (
                    info_title,
                    details,
                    magnet_url,
                    files_heading,
                    file_count,
                    name_heading,
                    size_heading,
                    files,
                ) = show_info.call_args.args
                self.assertEqual(info_title, "Torrent information")
                show_info.call_args.kwargs["on_selection_changed"](
                    {"video.mkv"}
                )
                self.assertEqual(
                    torrent_record.torrent_selected_files, '["video.mkv"]'
                )
                self.assertEqual(
                    app.storage.get_downloads()[0].torrent_selected_files,
                    '["video.mkv"]',
                )
                detail_map = dict(details)
                self.assertEqual(detail_map["Name"], "sample")
                self.assertEqual(detail_map["Seeds"], "4")
                self.assertEqual(detail_map["Peers"], "12")
                self.assertEqual(magnet_url, torrent_record.url)
                self.assertEqual(files_heading, "Torrent files")
                self.assertEqual(file_count, "Files: 1")
                self.assertEqual(name_heading, "Name")
                self.assertEqual(size_heading, "Size")
                self.assertEqual(files, [("video.mkv", "2.0 KB")])
                app.torrent_vpn_interfaces = {"Cloudflare WARP (10.0.0.2)": "10.0.0.2"}
                app.torrent_vpn_combo.set("Cloudflare WARP (10.0.0.2)")
                app._select_torrent_vpn_interface()
                self.assertEqual(
                    app.storage.get_setting("torrent_vpn_address", ""),
                    "10.0.0.2",
                )
                app.torrent_vpn_var.set(True)
                app._set_torrent_vpn_enabled()
                self.assertEqual(
                    app.storage.get_setting("torrent_vpn_enabled", ""),
                    "1",
                )
                self.assertEqual(app.manager._torrent_vpn_address, "10.0.0.2")
                app.torrent_vpn_var.set(False)
                app._set_torrent_vpn_enabled()
                self.assertEqual(app.manager._torrent_vpn_address, "")
                root.after(
                    50,
                    lambda: [
                        widget.destroy()
                        for widget in root.winfo_children()
                        if isinstance(widget, tk.Toplevel)
                    ],
                )
                app._show_torrent_info_dialog(
                    info_title,
                    details,
                    magnet_url,
                    files_heading,
                    file_count,
                    name_heading,
                    size_heading,
                    files,
                )
                app.download_tree.selection_set(str(record.id))
                app._show_selected_progress()
                self.assertEqual(float(app.detail_progress["value"]), 50.0)
                self.assertIn("50%", app.detail_text.get())
                app.information_panel.pack_forget()
                app.quick_add_panel.pack_forget()
                app._select_tab("downloads")
                root.update()
                row_bounds = app.download_tree.bbox(str(record.id))
                app._show_download_information(
                    types.SimpleNamespace(
                        widget=app.download_tree,
                        y=row_bounds[1] + 1,
                    )
                )
                root.update()
                self.assertEqual(app.information_panel.winfo_manager(), "pack")
                self.assertEqual(app.quick_add_panel.winfo_manager(), "pack")
                self.assertIn("Downloading", app.detail_text.get())
                self.assertIs(app.input_frame.master, app.grabber_tab)
                app.tabs.select(app.grabber_tab)
                root.update()
                self.assertTrue(app.detail_progress.winfo_ismapped())
                self.assertIn("50%", app.detail_text.get())
                app.tabs.select(app.downloads_tab)
                root.update()
                self.assertTrue(app.detail_progress.winfo_ismapped())
                self.assertIn("50%", app.detail_text.get())
                app._set_language("it")
                self.assertEqual(
                    app.download_tree.item(str(record.id), "values")[4],
                    "In download",
                )
                self.assertIn("In download", app.detail_text.get())
                app._set_language("en")
                root.update()
                row_bounds = app.download_tree.bbox(str(record.id))
                with patch.object(app.context_menu, "tk_popup") as popup:
                    app._show_download_context_menu(
                        types.SimpleNamespace(
                            widget=app.download_tree,
                            y=row_bounds[1] + 1,
                            x_root=10,
                            y_root=10,
                        )
                    )
                popup.assert_called_once()
                self.assertEqual(app.context_menu.entrycget(0, "state"), "disabled")
                self.assertEqual(app.context_menu.entrycget(1, "state"), "normal")
                self.assertEqual(app.context_menu.entrycget(3, "state"), "disabled")
                self.assertEqual(app.context_menu.entrycget(6, "label"), "Open URL")
                self.assertEqual(app.context_menu.entrycget(7, "label"), "Copy URL")
                self.assertNotIn(
                    "Torrent information",
                    [
                        app.context_menu.entrycget(index, "label")
                        for index in range(app.context_menu.index("end") + 1)
                        if app.context_menu.type(index) == "command"
                    ],
                )
                self.assertEqual(app.context_menu.entrycget(9, "state"), "disabled")

                record.status = "completed"
                app.storage.update_download(record.id, status="completed")
                app._upsert_row(record)
                app._refresh_status()
                self.assertTrue(app.download_tree.exists(str(record.id)))
                self.assertTrue(app.history_tree.exists(str(record.id)))
                app.information_panel.pack_forget()
                app.quick_add_panel.pack_forget()
                app._select_tab("history")
                root.update()
                row_bounds = app.history_tree.bbox(str(record.id))
                app._show_download_information(
                    types.SimpleNamespace(
                        widget=app.history_tree,
                        y=row_bounds[1] + 1,
                    )
                )
                root.update()
                self.assertEqual(app.information_panel.winfo_manager(), "pack")
                self.assertEqual(app.quick_add_panel.winfo_manager(), "pack")
                self.assertIn("Completed", app.detail_text.get())
                self.assertEqual(
                    app.tabs.tab(app.downloads_tab, "text"),
                    "Downloads",
                )
                self.assertEqual(
                    app.tabs.tab(app.history_tab, "text"),
                    "History (1)",
                )
                app._select_tab("history")
                self.assertEqual(app.tabs.select(), str(app.history_tab))
                app._select_tab("downloads")
                self.assertFalse(app.empty_labels["downloads"].winfo_manager())
                self.assertFalse(app.empty_labels["history"].winfo_manager())
                root.update()
                self.assertEqual(app.download_tree.winfo_parent(), app.download_tree.master._w)
                self.assertTrue(app.download_tree.winfo_ismapped())
                self.assertGreater(app.download_tree.winfo_width(), 100)
                self.assertGreater(app.download_tree.bbox(str(record.id))[3], 0)
                (Path(directory) / "video.mp4").write_bytes(b"test")
                with patch.object(
                    app.storage,
                    "update_download",
                    side_effect=sqlite3.OperationalError("database unavailable"),
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        app._rename_download(record.id, "temporary.mp4")
                self.assertEqual(record.filename, "video.mp4")
                self.assertTrue((Path(directory) / "video.mp4").is_file())
                self.assertFalse((Path(directory) / "temporary.mp4").exists())
                app._rename_download(record.id, "renamed-video.mp4")
                self.assertEqual(record.filename, "renamed-video.mp4")
                self.assertTrue((Path(directory) / "renamed-video.mp4").is_file())
                persisted = {
                    item.id: item
                    for item in app.storage.get_downloads(history=True)
                }
                self.assertEqual(persisted[record.id].filename, "renamed-video.mp4")
                row_bounds = app.download_tree.bbox(str(record.id))
                with patch.object(app.context_menu, "tk_popup"):
                    app._show_download_context_menu(
                        types.SimpleNamespace(
                            widget=app.download_tree,
                            y=row_bounds[1] + 1,
                            x_root=10,
                            y_root=10,
                        )
                    )
                self.assertEqual(app.context_menu.entrycget(3, "state"), "normal")
                self.assertEqual(app.context_menu.entrycget(10, "state"), "normal")
                self.assertEqual(app.context_menu.entrycget(12, "state"), "normal")
                with patch.object(app.manager, "add") as add_retry:
                    app.history_tree.selection_set(str(record.id))
                    app._retry_selected()
                retry_record = next(
                    retry
                    for retry in app.records.values()
                    if retry.id != record.id and retry.url == record.url
                )
                self.assertEqual(retry_record.status, "queued")
                self.assertEqual(retry_record.filename, "renamed-video.mp4")
                self.assertEqual(retry_record.destination, record.destination)
                add_retry.assert_called_once_with(
                    retry_record,
                    video_retry_filename="renamed-video.mp4",
                )
                self.assertTrue(app.download_tree.exists(str(retry_record.id)))
                app.tabs.select(app.history_tab)
                app._context_tree = None
                app.history_tree.selection_set(str(record.id))
                with patch.object(
                    app, "_ask_confirmation", return_value=True
                ) as confirm:
                    app._delete_selected_file()
                confirm.assert_called_once_with(
                    "Delete downloaded file",
                    "Permanently delete 1 downloaded file(s)?",
                    "Delete downloaded file...",
                )
                self.assertFalse((Path(directory) / "renamed-video.mp4").exists())
                self.assertNotIn(record.id, app.records)

                app.tabs.select(app.downloads_tab)
                second_id = app.storage.add_download(
                    "https://example.com/second.bin", directory
                )
                app.storage.update_download(second_id, status="paused")
                second_record = DownloadRecord(
                    id=second_id,
                    url="https://example.com/second.bin",
                    filename="second.bin",
                    status="paused",
                    progress=25,
                    total=100,
                    speed=0.0,
                    eta=None,
                    destination=directory,
                    engine="http",
                    error="",
                    created_at="",
                )
                app.records[second_id] = second_record
                app._upsert_row(second_record)
                third_id = app.storage.add_download(
                    "https://example.com/third.bin", directory
                )
                app.storage.update_download(third_id, status="paused")
                third_record = DownloadRecord(
                    id=third_id,
                    url="https://example.com/third.bin",
                    filename="third.bin",
                    status="paused",
                    progress=10,
                    total=100,
                    speed=0.0,
                    eta=None,
                    destination=directory,
                    engine="http",
                    error="",
                    created_at="",
                )
                app.records[third_id] = third_record
                app._upsert_row(third_record)
                app.download_tree.selection_set(
                    str(second_record.id), str(third_record.id)
                )
                app._show_selected_progress()
                self.assertIn("2 downloads selected", app.detail_text.get())
                self.assertAlmostEqual(
                    float(app.detail_progress["value"]),
                    35 / 200 * 100,
                )
                with patch.object(app, "_ask_confirmation", return_value=False) as confirm:
                    app._remove_selected()
                confirm.assert_called_once_with(
                    "Remove downloads",
                    "Remove 2 selected download(s) from the list?",
                    "Remove from list",
                )
                self.assertIn(second_record.id, app.records)
                app._remove_downloads([second_record.id, third_record.id])
                self.assertNotIn(record.id, app.records)
                self.assertNotIn(second_record.id, app.records)
                self.assertNotIn(third_record.id, app.records)
                self.assertFalse(app.download_tree.exists(str(second_record.id)))
                self.assertFalse(app.download_tree.exists(str(third_record.id)))
                remaining = {
                    item.id
                    for item in (
                        app.storage.get_downloads()
                        + app.storage.get_downloads(history=True)
                    )
                }
                self.assertNotIn(record.id, remaining)
                self.assertNotIn(second_record.id, remaining)
                self.assertNotIn(third_record.id, remaining)

                partial_torrent_directory = Path(directory) / "partial-torrent"
                partial_torrent_directory.mkdir()
                (partial_torrent_directory / "partial.bin").write_bytes(b"partial")
                partial_torrent_id = app.storage.add_download(
                    "magnet:?xt=urn:btih:0123456789abcdef&dn=Partial",
                    directory,
                    "torrent",
                    "original",
                )
                app.storage.update_download(
                    partial_torrent_id,
                    status="cancelled",
                    torrent_directory=str(partial_torrent_directory),
                )
                partial_torrent_record = DownloadRecord(
                    id=partial_torrent_id,
                    url="magnet:?xt=urn:btih:0123456789abcdef&dn=Partial",
                    filename="Partial",
                    status="cancelled",
                    progress=100,
                    total=500,
                    speed=0.0,
                    eta=None,
                    destination=directory,
                    engine="torrent",
                    error="",
                    created_at="",
                    torrent_directory=str(partial_torrent_directory),
                )
                app.records[partial_torrent_id] = partial_torrent_record
                app._upsert_row(partial_torrent_record)
                root.update()
                row_bounds = app.download_tree.bbox(str(partial_torrent_id))
                with patch.object(app.context_menu, "tk_popup"):
                    app._show_download_context_menu(
                        types.SimpleNamespace(
                            widget=app.download_tree,
                            y=row_bounds[1] + 1,
                            x_root=10,
                            y_root=10,
                        )
                    )
                self.assertEqual(app.context_menu.entrycget(12, "state"), "normal")
                with patch.object(app, "_ask_confirmation", return_value=True):
                    app._delete_selected_file()
                self.assertFalse(partial_torrent_directory.exists())
                self.assertNotIn(partial_torrent_id, app.records)
            finally:
                app._close()
                if previous_local_app_data is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous_local_app_data

    def test_http_download_writes_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control = TransferControl(threading.Event(), threading.Event())
            filename = download_http(self.url, directory, control, lambda _event: None)
            self.assertEqual(filename, "sample.bin")
            self.assertEqual((Path(directory) / filename).read_bytes(), PAYLOAD)
            self.assertIn("Mozilla/", FileHandler.received_user_agents[-1])

    def test_http_download_resumes_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "sample.bin.part"
            partial.write_bytes(PAYLOAD[:12000])
            control = TransferControl(threading.Event(), threading.Event())
            filename = download_http(self.url, directory, control, lambda _event: None)
            self.assertEqual((Path(directory) / filename).read_bytes(), PAYLOAD)
            self.assertEqual(FileHandler.requested_ranges[-1], "bytes=12000-")

    def test_existing_destination_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "sample.bin"
            existing.write_bytes(b"keep this existing file")
            control = TransferControl(threading.Event(), threading.Event())
            filename = download_http(self.url, directory, control, lambda _event: None)
            self.assertEqual(filename, "sample (1).bin")
            self.assertEqual(existing.read_bytes(), b"keep this existing file")
            self.assertEqual((Path(directory) / filename).read_bytes(), PAYLOAD)

    def test_manager_runs_transfer_in_background_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: queue.Queue[dict[str, object]] = queue.Queue()
            manager = DownloadManager(events, simultaneous=1)
            record = DownloadRecord(
                id=1,
                url=self.url,
                filename="sample.bin",
                status="queued",
                progress=0,
                total=None,
                speed=0,
                eta=None,
                destination=directory,
                engine="http",
                error="",
                created_at="",
            )
            try:
                manager.add(record)
                received: list[dict[str, object]] = []
                while not any(event.get("status") == "completed" for event in received):
                    received.append(events.get(timeout=5))
            finally:
                manager.close()
            self.assertTrue((Path(directory) / "sample.bin").exists())

    def test_manager_routes_torrent_links_to_torrent_downloader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: queue.Queue[dict[str, object]] = queue.Queue()
            manager = DownloadManager(events, simultaneous=1)
            record = DownloadRecord(
                id=91,
                url="magnet:?xt=urn:btih:0123456789abcdef&dn=Sample",
                filename="Sample",
                status="queued",
                progress=0,
                total=None,
                speed=0,
                eta=None,
                destination=directory,
                engine="auto",
                error="",
                created_at="",
            )
            try:
                with patch(
                    "downloader_app.manager.download_torrent",
                    return_value="Sample",
                ) as torrent_download:
                    manager.add(record)
                    received: list[dict[str, object]] = []
                    while not any(
                        event.get("status") == "completed"
                        for event in received
                    ):
                        received.append(events.get(timeout=5))
                torrent_download.assert_called_once()
            finally:
                manager.close()

    def test_manager_removes_active_transfer_after_cancelling_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: queue.Queue[dict[str, object]] = queue.Queue()
            manager = DownloadManager(events, simultaneous=1)
            started = threading.Event()

            def wait_for_cancel(
                _url: str,
                _destination: str,
                control: TransferControl,
                _report: object,
                _filename: str,
            ) -> str:
                started.set()
                if not control.cancelled.wait(timeout=2):
                    raise TimeoutError("The test transfer was not cancelled.")
                raise TransferCancelled()

            record = DownloadRecord(
                id=73,
                url="https://example.com/active.bin",
                filename="active.bin",
                status="queued",
                progress=0,
                total=None,
                speed=0.0,
                eta=None,
                destination=directory,
                engine="http",
                error="",
                created_at="",
            )
            try:
                with patch("downloader_app.manager.download_http", wait_for_cancel):
                    manager.add(record)
                    self.assertTrue(started.wait(timeout=2))
                    manager.remove(record.id)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        with manager._condition:
                            if record.id not in manager._transfers:
                                break
                        threading.Event().wait(0.01)
                    with manager._condition:
                        self.assertNotIn(record.id, manager._transfers)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
