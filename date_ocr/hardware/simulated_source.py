"""Manual hardware-trigger simulator used before the camera is installed."""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .frame_source import FramePacket, FrameSource, FrameSourceError


class TriggeredImageSource(FrameSource):
    """Accept explicit trigger calls; never silently drops an occupied trigger."""

    def __init__(self, capacity: int = 1) -> None:
        if capacity != 1:
            raise ValueError("Mô phỏng dùng đúng một frame chờ để phát hiện BUSY")
        self._queue: queue.Queue[FramePacket] = queue.Queue(maxsize=capacity)
        self._opened = False
        self._running = False
        self._lock = threading.Lock()
        self._next_id = 1
        self.rejected_busy = 0

    def open(self) -> None:
        self._opened = True

    def start(self) -> None:
        if not self._opened:
            raise FrameSourceError("Nguồn mô phỏng chưa được mở")
        self._running = True

    def trigger(self, image: np.ndarray, name: str = "simulated") -> FramePacket:
        if not self._running:
            raise FrameSourceError("Nguồn mô phỏng chưa ARM")
        if image is None or image.size == 0:
            raise FrameSourceError("Ảnh trigger rỗng")
        with self._lock:
            packet = FramePacket(
                frame_id=self._next_id,
                image=np.ascontiguousarray(image).copy(),
                received_monotonic=time.perf_counter(),
                source=f"simulation://{name}",
                metadata={"provider": "simulation", "trigger": "manual"},
            )
            try:
                self._queue.put_nowait(packet)
            except queue.Full as exc:
                self.rejected_busy += 1
                raise FrameSourceError(
                    "FAIL_BUSY: frame trước chưa được lấy; trigger mới bị từ chối"
                ) from exc
            self._next_id += 1
            return packet

    def get_frame(self, timeout_ms: int) -> FramePacket | None:
        if not self._running:
            raise FrameSourceError("Nguồn mô phỏng chưa ARM")
        try:
            return self._queue.get(timeout=max(0, timeout_ms) / 1000)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self._opened = False
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

