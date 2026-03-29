from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.ocr.engine import OCREngine
from app.core.ocr.region import default_roi_for_video
from app.core.text.cleaner import clean_ocr_text
from app.core.text.merger import merge_static_texts
from app.models.extraction import ROI
from app.models.frame import FrameInfo
from app.models.ocr import ExtractionResult, FrameOCRResult
from app.models.video import VideoMeta


class StaticExtractor:
    def __init__(self, ocr_engine: OCREngine | None = None) -> None:
        self.ocr_engine = ocr_engine or OCREngine()

    def extract(
        self,
        video: VideoMeta,
        frames: list[tuple[FrameInfo, np.ndarray]],
        keep_screenshots: bool = True,
        screenshot_dir: str | None = None,
        roi: ROI | None = None,
        progress_callback=None,
        should_cancel=None,
    ) -> ExtractionResult:
        target_roi = roi or default_roi_for_video(video)
        results: list[FrameOCRResult] = []

        screenshot_path = Path(screenshot_dir) if screenshot_dir else None
        if screenshot_path is not None:
            screenshot_path.mkdir(parents=True, exist_ok=True)

        total_frames = max(len(frames), 1)
        if progress_callback is not None:
            progress_callback(0, total_frames, "正在准备 OCR")

        for order, (frame_info, frame_bgr) in enumerate(frames, start=1):
            if should_cancel is not None and should_cancel():
                raise RuntimeError("提取已取消")
            if progress_callback is not None:
                progress_callback(order - 1, total_frames, f"正在识别第 {order}/{total_frames} 张")
            cropped = self._crop_to_roi(frame_bgr, target_roi)
            raw_text, lines = self.ocr_engine.recognize(cropped)
            cleaned_text = clean_ocr_text(raw_text)

            image_path: str | None = None
            if keep_screenshots and screenshot_path is not None:
                filename = f"frame_{order:04d}_{self._format_timestamp(frame_info.timestamp_ms)}.jpg"
                output_path = screenshot_path / filename
                ret, buf = cv2.imencode(".jpg", cropped, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ret:
                    output_path.write_bytes(buf.tobytes())
                image_path = str(output_path)

            results.append(
                FrameOCRResult(
                    frame=FrameInfo(
                        index=frame_info.index,
                        timestamp_ms=frame_info.timestamp_ms,
                        image_path=image_path,
                        selected=True,
                        score=frame_info.score,
                    ),
                    raw_text=raw_text,
                    cleaned_text=cleaned_text,
                    lines=lines,
                )
            )
            if should_cancel is not None and should_cancel():
                raise RuntimeError("提取已取消")
            if progress_callback is not None:
                progress_callback(order, total_frames, f"已完成第 {order}/{total_frames} 张")

        merged = merge_static_texts([item.cleaned_text for item in results])
        return ExtractionResult(
            mode="static",
            video=video,
            merged_text=merged,
            segmented_texts=results,
            screenshot_dir=str(screenshot_path) if screenshot_path else None,
        )

    @staticmethod
    def _crop_to_roi(frame_bgr: np.ndarray, roi: ROI) -> np.ndarray:
        height, width = frame_bgr.shape[:2]
        x1 = max(0, min(roi.x, width - 1))
        y1 = max(0, min(roi.y, height - 1))
        x2 = max(x1 + 1, min(roi.x + roi.width, width))
        y2 = max(y1 + 1, min(roi.y + roi.height, height))
        return frame_bgr[y1:y2, x1:x2].copy()

    @staticmethod
    def _format_timestamp(timestamp_ms: int) -> str:
        total_seconds = max(0, timestamp_ms // 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}-{minutes:02d}-{seconds:02d}"
