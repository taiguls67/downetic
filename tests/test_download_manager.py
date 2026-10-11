from __future__ import annotations

import tempfile
import threading
import queue
import base64
import subprocess
import sqlite3
import sys
import time
import types
import unittest
import json
import re
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from requests.exceptions import ConnectionError

from downloader_app import __version__
from downloader_app.downloader import (
    TransferCancelled,
    _ffmpeg_options,
    _javascript_runtime_options,
    _video_output_options,
    _is_youtube_collection_url,
    download_http,
    download_video,
    select_engine,
)
from downloader_app.gui import DownloadManagerApp, _show_windows_notification
from downloader_app.links import parse_urls
from downloader_app.manager import DownloadManager
from downloader_app.media_links import MediaLink, extract_media_links, scan_media_pages
from downloader_app.models import DownloadRecord, TransferControl
from downloader_app.storage import Storage


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

    def test_parse_urls_filters_and_deduplicates(self) -> None:
        links = parse_urls(
            f"See {self.url} and {self.url}. Ignore ftp://example.com/file "
            "and malformed https://"
        )
        self.assertEqual(links, [self.url])

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
        with patch(
            "downloader_app.media_links.requests.get",
            side_effect=[ConnectionError("offline"), MediaResponse()],
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
                    ["Downloads", "Link Grabber", "Help"],
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
                self.assertEqual(app.file_menu.entrycget(0, "label"), "Aggiungi link...")
                self.assertEqual(
                    app.settings_menu.entrycget(0, "label"), "Cartella di download"
                )
                self.assertEqual(
                    app.download_tree.heading("status", "text"), "Stato"
                )
                self.assertEqual(
                    app.context_menu.entrycget(9, "label"), "Rinomina..."
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
                    ("Automatico", "HTTP", "Video"),
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
                app.download_tree.selection_set(str(record.id))
                app._show_selected_progress()
                self.assertEqual(float(app.detail_progress["value"]), 50.0)
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
                self.assertEqual(app.context_menu.entrycget(9, "state"), "disabled")

                record.status = "completed"
                app.storage.update_download(record.id, status="completed")
                app._upsert_row(record)
                app._refresh_status()
                self.assertTrue(app.download_tree.exists(str(record.id)))
                self.assertTrue(app.history_tree.exists(str(record.id)))
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
                self.assertEqual(app.context_menu.entrycget(9, "state"), "normal")
                self.assertEqual(app.context_menu.entrycget(11, "state"), "normal")
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
                with patch(
                    "downloader_app.gui.messagebox.askyesno",
                    return_value=True,
                ):
                    app._delete_selected_file()
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
