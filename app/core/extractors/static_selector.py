from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.config.settings import DEFAULT_STATIC_CANDIDATE_FRAME_COUNT
from app.models.frame import FrameInfo
from app.models.video import VideoMeta


@dataclass
class CandidateFrame:
    frame: FrameInfo
    image: np.ndarray


class StaticFrameSelector:
    def __init__(self, sample_interval_ms: int = 1000) -> None:
        self.sample_interval_ms = sample_interval_ms

    def select_candidates(self, video_loader, max_candidates: int = DEFAULT_STATIC_CANDIDATE_FRAME_COUNT) -> list[CandidateFrame]:
        meta: VideoMeta | None = video_loader.meta
        if meta is None or meta.duration_ms <= 0:
            return []

        samples: list[tuple[FrameInfo, np.ndarray, float]] = []
        previous_gray: np.ndarray | None = None
        frame_index = 0

        for timestamp_ms in range(0, meta.duration_ms + 1, self.sample_interval_ms):
            try:
                frame = video_loader.read_frame_at_ms(timestamp_ms)
            except Exception:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
            text_density = self._estimate_text_density(gray)
            stability = self._estimate_stability(gray, previous_gray)
            score = (sharpness * 0.45) + (text_density * 1000 * 0.35) + (stability * 100 * 0.20)

            frame_info = FrameInfo(
                index=frame_index,
                timestamp_ms=timestamp_ms,
                score=float(score),
            )
            samples.append((frame_info, frame, score))
            previous_gray = gray
            frame_index += 1

        samples.sort(key=lambda item: item[2], reverse=True)

        selected: list[CandidateFrame] = []
        selected_times: list[int] = []
        minimum_gap_ms = max(self.sample_interval_ms, 1500)

        for frame_info, image, _score in samples:
            if any(abs(frame_info.timestamp_ms - existing) < minimum_gap_ms for existing in selected_times):
                continue
            selected.append(CandidateFrame(frame=frame_info, image=image))
            selected_times.append(frame_info.timestamp_ms)
            if len(selected) >= max_candidates:
                break

        selected.sort(key=lambda item: item.frame.timestamp_ms)
        return selected

    @staticmethod
    def _estimate_text_density(gray: np.ndarray) -> float:
        edges = cv2.Canny(gray, 80, 160)
        return float(np.count_nonzero(edges)) / float(edges.size)

    @staticmethod
    def _estimate_stability(gray: np.ndarray, previous_gray: np.ndarray | None) -> float:
        if previous_gray is None or previous_gray.shape != gray.shape:
            return 0.5
        diff = cv2.absdiff(gray, previous_gray)
        mean_diff = float(np.mean(diff))
        return max(0.0, 1.0 - (mean_diff / 255.0))
