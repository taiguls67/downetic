from __future__ import annotations

from dataclasses import dataclass
from threading import Event


@dataclass
class DownloadRecord:
    id: int
    url: str
    filename: str
    status: str
    progress: int
    total: int | None
    speed: float
    eta: float | None
    destination: str
    engine: str
    error: str
    created_at: str
    output_format: str = "original"
    seeds: int = 0
    peers: int = 0
    torrent_files: str = "[]"
    torrent_directory: str = ""
    torrent_selected_files: str = ""
    torrent_tracker: str = ""


@dataclass
class TransferControl:
    paused: Event
    cancelled: Event
