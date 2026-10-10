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


@dataclass
class TransferControl:
    paused: Event
    cancelled: Event
