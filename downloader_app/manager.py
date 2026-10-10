from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from downloader_app.downloader import (
    TransferCancelled,
    TransferPaused,
    download_http,
    download_video,
    select_engine,
)
from downloader_app.models import DownloadRecord, TransferControl


@dataclass
class Transfer:
    record: DownloadRecord
    control: TransferControl
    status: str
    remove_requested: bool = False


class DownloadManager:
    def __init__(self, events: queue.Queue[dict[str, object]], simultaneous: int = 3) -> None:
        self.events = events
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._transfers: dict[int, Transfer] = {}
        self._active = 0
        self._simultaneous = max(1, simultaneous)
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="download")
        self._closed = False
        self._dispatcher = threading.Thread(target=self._dispatch, daemon=True)
        self._dispatcher.start()

    def _emit(self, download_id: int, **values: object) -> None:
        self.events.put({"id": download_id, **values})

    def add(self, record: DownloadRecord) -> None:
        with self._condition:
            control = TransferControl(threading.Event(), threading.Event())
            status = (
                record.status
                if record.status in {"paused", "cancelled", "error"}
                else "queued"
            )
            if status == "paused":
                control.paused.set()
            elif status == "cancelled":
                control.cancelled.set()
            self._transfers[record.id] = Transfer(record, control, status)
            self._emit(
                record.id,
                status=status,
                error="" if status == "queued" else record.error,
            )
            self._condition.notify_all()

    def pause(self, download_id: int) -> None:
        with self._condition:
            transfer = self._transfers.get(download_id)
            if transfer is None or transfer.status not in {"queued", "downloading"}:
                return
            transfer.control.paused.set()
            if transfer.status == "queued":
                transfer.status = "paused"
                self._emit(download_id, status="paused", speed=0.0, eta=None)
            self._condition.notify_all()

    def resume(self, download_id: int) -> None:
        with self._condition:
            transfer = self._transfers.get(download_id)
            if transfer is None or transfer.status not in {
                "paused", "cancelled", "error",
            }:
                return
            transfer.control.paused.clear()
            transfer.control.cancelled.clear()
            transfer.status = "queued"
            self._emit(download_id, status="queued", error="", speed=0.0, eta=None)
            self._condition.notify_all()

    def cancel(self, download_id: int) -> None:
        with self._condition:
            transfer = self._transfers.get(download_id)
            if transfer is None or transfer.status not in {"queued", "downloading", "paused"}:
                return
            transfer.control.cancelled.set()
            transfer.control.paused.clear()
            if transfer.status in {"queued", "paused"}:
                transfer.status = "cancelled"
                self._emit(download_id, status="cancelled", speed=0.0, eta=None)
            self._condition.notify_all()

    def remove(self, download_id: int) -> None:
        with self._condition:
            transfer = self._transfers.get(download_id)
            if transfer is None:
                return
            if transfer.status == "downloading":
                transfer.remove_requested = True
                transfer.control.cancelled.set()
                transfer.control.paused.clear()
            else:
                self._transfers.pop(download_id)
            self._condition.notify_all()

    def set_simultaneous(self, count: int) -> None:
        with self._condition:
            self._simultaneous = min(max(count, 1), 8)
            self._condition.notify_all()

    def _dispatch(self) -> None:
        while True:
            with self._condition:
                while not self._closed and (
                    self._active >= self._simultaneous
                    or not any(t.status == "queued" for t in self._transfers.values())
                ):
                    self._condition.wait()
                if self._closed:
                    return
                transfer = next(
                    item for item in self._transfers.values() if item.status == "queued"
                )
                transfer.status = "downloading"
                self._active += 1
                self._emit(transfer.record.id, status="downloading", error="")
                future = self._executor.submit(self._run, transfer)
                future.add_done_callback(lambda completed, job=transfer: self._finished(job, completed))

    def _run(self, transfer: Transfer) -> str:
        record = transfer.record
        engine = select_engine(record.url, record.engine)

        def report(values: dict[str, object]) -> None:
            self._emit(record.id, **values)

        try:
            if engine == "video":
                filename = download_video(
                    record.url,
                    record.destination,
                    transfer.control,
                    report,
                    record.output_format,
                )
            else:
                filename = download_http(
                    record.url,
                    record.destination,
                    transfer.control,
                    report,
                    record.filename,
                )
        except TransferPaused:
            self._emit(record.id, status="paused", speed=0.0, eta=None)
            raise
        except TransferCancelled:
            self._emit(record.id, status="cancelled", speed=0.0, eta=None)
            raise
        self._emit(record.id, status="completed", filename=filename, speed=0.0, eta=0)
        return filename

    def _finished(self, transfer: Transfer, future: Future[str]) -> None:
        with self._condition:
            self._active -= 1
            try:
                future.result()
            except (TransferPaused, TransferCancelled):
                transfer.status = "paused" if transfer.control.paused.is_set() else "cancelled"
            except Exception as exc:
                transfer.status = "error"
                self._emit(
                    transfer.record.id,
                    status="error",
                    error=str(exc) or exc.__class__.__name__,
                    speed=0.0,
                    eta=None,
                )
            else:
                transfer.status = "completed"
            if transfer.remove_requested:
                self._transfers.pop(transfer.record.id, None)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            for transfer in self._transfers.values():
                if transfer.status == "downloading":
                    transfer.control.cancelled.set()
                    transfer.control.paused.clear()
            self._condition.notify_all()
        self._dispatcher.join(timeout=2)
        self._executor.shutdown(wait=False, cancel_futures=True)
