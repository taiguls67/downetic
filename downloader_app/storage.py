from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

from downloader_app.models import DownloadRecord


def default_data_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "Downetic"


def filename_from_url(url: str) -> str:
    name = unquote(Path(urlsplit(url).path).name)
    return name or "download"


class Storage:
    def __init__(self, db_path: Path | None = None) -> None:
        data_dir = default_data_directory()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(db_path or data_dir / "downloads.sqlite3")
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS downloads (
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
                    output_format TEXT NOT NULL DEFAULT 'original',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(downloads)")
            }
            if "output_format" not in columns:
                self._connection.execute(
                    "ALTER TABLE downloads ADD COLUMN output_format "
                    "TEXT NOT NULL DEFAULT 'original'"
                )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )

    def add_download(
        self,
        url: str,
        destination: str,
        engine: str = "auto",
        output_format: str = "original",
    ) -> int:
        filename = filename_from_url(url)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """INSERT INTO downloads
                   (url, filename, status, destination, engine, output_format)
                   VALUES (?, ?, 'queued', ?, ?, ?)""",
                (url, filename, destination, engine, output_format),
            )
            return int(cursor.lastrowid)

    def update_download(self, download_id: int, **fields: object) -> None:
        allowed = {
            "filename", "status", "progress", "total", "speed", "eta",
            "destination", "engine", "output_format", "error",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._connection:
            self._connection.execute(
                f"UPDATE downloads SET {assignments} WHERE id = ?",
                (*updates.values(), download_id),
            )

    def delete_download(self, download_id: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM downloads WHERE id = ?",
                (download_id,),
            )

    def get_downloads(self, history: bool = False) -> list[DownloadRecord]:
        query = (
            "SELECT * FROM downloads WHERE status IN "
            "('completed', 'cancelled', 'error') ORDER BY id DESC"
            if history
            else "SELECT * FROM downloads WHERE status NOT IN "
            "('completed', 'cancelled', 'error') ORDER BY id DESC"
        )
        with self._lock:
            rows = self._connection.execute(query).fetchall()
        return [DownloadRecord(**dict(row)) for row in rows]

    def get_setting(self, key: str, default: str) -> str:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def recover_interrupted_downloads(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """UPDATE downloads
                   SET status = 'paused', speed = 0, eta = NULL
                   WHERE status IN ('queued', 'downloading')"""
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
