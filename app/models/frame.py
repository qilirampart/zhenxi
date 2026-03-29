from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FrameInfo:
    index: int
    timestamp_ms: int
    image_path: str | None = None
    selected: bool = False
    score: float | None = None
