"""Small acquisition seam shared by real and simulated triggered cameras."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import Any

import numpy as np


class FrameSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class FramePacket:
    frame_id: int
    image: np.ndarray
    received_monotonic: float = field(default_factory=time.perf_counter)
    source: str = "camera"
    device_timestamp: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class FrameSource(ABC):
    """A source that is armed once and yields one image per external trigger."""

    @abstractmethod
    def open(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_frame(self, timeout_ms: int) -> FramePacket | None:
        """Return None only when the timeout expires without a triggered frame."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


def missing_frame_count(previous_frame_id: int | None, current_frame_id: int) -> int:
    """Count SDK frame-number gaps; zero/unknown IDs cannot prove a loss."""
    if previous_frame_id is None or previous_frame_id <= 0 or current_frame_id <= 0:
        return 0
    return max(0, current_frame_id - previous_frame_id - 1)
