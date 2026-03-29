from dataclasses import dataclass


@dataclass
class VideoMeta:
    path: str
    filename: str
    duration_ms: int
    fps: float
    width: int
    height: int
    aspect_ratio: str
    frame_count: int

    @property
    def resolution_text(self) -> str:
        return f"{self.width}×{self.height}"
