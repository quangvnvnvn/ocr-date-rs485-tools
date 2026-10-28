"""Industrial camera acquisition adapters."""

from .frame_source import FramePacket, FrameSource, FrameSourceError
from .simulated_source import TriggeredImageSource

__all__ = ["FramePacket", "FrameSource", "FrameSourceError", "TriggeredImageSource"]

