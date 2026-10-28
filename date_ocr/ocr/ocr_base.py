from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldReading:
    text: str
    confidence: float


class OCRBase(ABC):
    @abstractmethod
    def read_field(self, image, field: str, decoder: str = 'greedy') -> FieldReading:
        """Read only the already-cropped field, never the full package."""
