from __future__ import annotations

from app.core.extractors.static_extractor import StaticExtractor
from app.core.extractors.static_selector import StaticFrameSelector
from app.core.text.formatter import format_segmented_results
from app.models.extraction import ROI
from app.models.ocr import ExtractionResult
from app.utils.paths import build_screenshot_session_dir


class ExtractionService:
    def __init__(self) -> None:
        self.static_selector = StaticFrameSelector()
        self.static_extractor = StaticExtractor()

    def generate_static_candidates(self, video_loader, max_candidates: int = 5):
        return self.static_selector.select_candidates(video_loader, max_candidates=max_candidates)

    def extract_static(
        self,
        source_meta,
        frames,
        keep_screenshots: bool = True,
        roi: ROI | None = None,
        progress_callback=None,
        should_cancel=None,
    ) -> ExtractionResult:
        if source_meta is None:
            raise RuntimeError("当前没有可提取的素材。")

        screenshot_dir = None
        if keep_screenshots:
            screenshot_dir = build_screenshot_session_dir(source_meta.filename).as_posix()

        return self.static_extractor.extract(
            video=source_meta,
            frames=frames,
            keep_screenshots=keep_screenshots,
            screenshot_dir=screenshot_dir,
            roi=roi,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
        )

    @staticmethod
    def format_segmented_result(result: ExtractionResult) -> str:
        items = [
            (f"{entry.frame.timestamp_ms // 1000}s", entry.cleaned_text or entry.raw_text)
            for entry in result.segmented_texts
        ]
        return format_segmented_results(items)
