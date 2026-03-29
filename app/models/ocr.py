from __future__ import annotations

from dataclasses import dataclass, field

from app.models.frame import FrameInfo
from app.models.video import VideoMeta


@dataclass
class OCRLine:
    text: str
    confidence: float
    box: list[list[float]] = field(default_factory=list)


@dataclass
class FrameOCRResult:
    frame: FrameInfo
    raw_text: str
    cleaned_text: str
    lines: list[OCRLine] = field(default_factory=list)


@dataclass
class ExtractionResult:
    mode: str
    video: VideoMeta
    merged_text: str
    segmented_texts: list[FrameOCRResult] = field(default_factory=list)
    screenshot_dir: str | None = None
