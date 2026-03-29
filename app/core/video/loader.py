from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.video.ratio import detect_aspect_ratio
from app.models.video import VideoMeta


class VideoLoaderError(RuntimeError):
    pass


class VideoLoader:
    def __init__(self) -> None:
        self._capture: cv2.VideoCapture | None = None
        self._meta: VideoMeta | None = None
        self._video_path: Path | None = None

    @property
    def meta(self) -> VideoMeta | None:
        return self._meta

    @property
    def is_open(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def open(self, video_path: str | Path) -> VideoMeta:
        path = Path(video_path)
        if not path.exists():
            raise VideoLoaderError(f"视频不存在：{path}")

        self.close()
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise VideoLoaderError(f"无法打开视频：{path}")

        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration_ms = int((frame_count / fps) * 1000) if fps > 0 and frame_count > 0 else 0

        self._capture = capture
        self._video_path = path
        self._meta = VideoMeta(
            path=str(path),
            filename=path.name,
            duration_ms=duration_ms,
            fps=fps,
            width=width,
            height=height,
            aspect_ratio=detect_aspect_ratio(width, height),
            frame_count=frame_count,
        )
        return self._meta

    def read_frame_at_ms(self, timestamp_ms: int) -> np.ndarray:
        if not self.is_open or self._capture is None or self._meta is None:
            raise VideoLoaderError("当前没有已打开的视频")

        safe_timestamp = max(0, min(timestamp_ms, max(self._meta.duration_ms - 1, 0)))
        self._capture.set(cv2.CAP_PROP_POS_MSEC, safe_timestamp)
        success, frame = self._capture.read()

        if success and frame is not None:
            return frame

        if self._meta.fps > 0 and self._meta.frame_count > 0:
            frame_index = min(
                int(round((safe_timestamp / 1000) * self._meta.fps)),
                self._meta.frame_count - 1,
            )
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            success, frame = self._capture.read()
            if success and frame is not None:
                return frame

        raise VideoLoaderError(f"无法读取时间点 {safe_timestamp} ms 的视频帧")

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        self._meta = None
        self._video_path = None

    def __del__(self) -> None:
        self.close()
