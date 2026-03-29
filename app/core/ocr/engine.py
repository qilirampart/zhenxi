from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np

from app.config.settings import OCR_CLS_MODEL_DIR, OCR_DET_MODEL_DIR, OCR_REC_MODEL_DIR
from app.core.ocr.preprocessor import preprocess_for_ocr
from app.models.ocr import OCRLine


class OCREngine:
    """统一 OCR 入口：优先本地 PaddleOCR，本地不可用时回退到云端 API。"""

    def __init__(self) -> None:
        self._paddle_engine = None
        self._api_engine = None
        self._mode: str | None = None
        self._preferred_mode: str = "auto"
        self._paddle_bootstrap_failed = False
        self._paddle_bootstrap_error = ""

    def recognize(self, frame_bgr: np.ndarray) -> tuple[str, list[OCRLine]]:
        mode = self._resolve_mode()
        if mode == "paddle":
            try:
                processed = preprocess_for_ocr(frame_bgr)
                return self._recognize_paddle(processed)
            except Exception as exc:  # noqa: BLE001
                self._paddle_bootstrap_failed = True
                self._paddle_bootstrap_error = str(exc).strip()
                if self._api_available():
                    return self._get_api_engine().recognize(frame_bgr)
                raise RuntimeError(f"本地 PaddleOCR 不可用：{exc}") from exc
        if mode == "api":
            return self._get_api_engine().recognize(frame_bgr)

        raise RuntimeError(
            "OCR 引擎不可用：\n"
            "  - 本地模型未就绪（请把模型放到 runtime/models/ 目录）\n"
            "  - 云端 API 未启用（请在 API 配置里至少启用一个可用通道）"
        )

    def set_preferred_mode(self, mode: str) -> None:
        self._preferred_mode = mode
        self._mode = None
        self._paddle_bootstrap_failed = False
        self._paddle_bootstrap_error = ""

    def current_mode(self) -> str | None:
        return self._resolve_mode()

    def _resolve_mode(self) -> str | None:
        pref = self._preferred_mode
        if pref == "api":
            return "api" if self._api_available() else None
        if pref == "paddle":
            if self._local_models_ready() or not self._paddle_bootstrap_failed:
                return "paddle"
            return "api" if self._api_available() else None

        if self._mode is not None:
            return self._mode
        if self._local_models_ready():
            self._mode = "paddle"
        elif self._api_available():
            self._mode = "api"
        else:
            self._mode = None
        return self._mode

    @staticmethod
    def _local_models_ready() -> bool:
        for directory in (OCR_DET_MODEL_DIR, OCR_REC_MODEL_DIR):
            if not OCREngine._model_dir_ready(directory):
                return False
        return True

    @staticmethod
    def _api_available() -> bool:
        from app.core.ocr.api_engine import APIEngine

        return APIEngine().is_enabled()

    def _get_paddle_engine(self):
        if self._paddle_engine is None:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
            from paddleocr import PaddleOCR

            det_dir = self._resolve_model_dir(OCR_DET_MODEL_DIR)
            rec_dir = self._resolve_model_dir(OCR_REC_MODEL_DIR)
            cls_dir = self._resolve_model_dir(OCR_CLS_MODEL_DIR)
            self._paddle_engine = PaddleOCR(
                # If a local model directory is missing, PaddleOCR will try downloading
                # the official model automatically.
                text_detection_model_dir=det_dir,
                text_recognition_model_dir=rec_dir,
                textline_orientation_model_dir=cls_dir,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_hpi=False,
                device="cpu",
                lang="ch",
            )
        return self._paddle_engine

    def _recognize_paddle(self, processed: np.ndarray) -> tuple[str, list[OCRLine]]:
        result = self._get_paddle_engine().ocr(processed)
        lines: list[OCRLine] = []
        text_parts: list[str] = []

        for page in result or []:
            payload = self._extract_payload(page)
            texts = payload.get("rec_texts") or []
            scores = payload.get("rec_scores") or []
            polys = payload.get("rec_polys") or payload.get("dt_polys") or []

            for index, text in enumerate(texts):
                normalized = str(text).strip()
                if not normalized:
                    continue
                confidence = float(scores[index]) if index < len(scores) else 0.0
                box = self._normalize_box(polys[index]) if index < len(polys) else []
                lines.append(OCRLine(text=normalized, confidence=confidence, box=box))
                text_parts.append(normalized)

        return "\n".join(text_parts).strip(), lines

    def _get_api_engine(self):
        if self._api_engine is None:
            from app.core.ocr.api_engine import APIEngine

            self._api_engine = APIEngine()
        return self._api_engine

    @staticmethod
    def _resolve_model_dir(path: Path) -> str | None:
        if not OCREngine._model_dir_ready(path):
            return None
        resolved = OCREngine._mirror_model_dir_if_needed(path)
        return str(resolved)

    @staticmethod
    def _model_dir_ready(path: Path) -> bool:
        required_files = (
            "config.json",
            "inference.json",
            "inference.pdiparams",
            "inference.yml",
        )
        return path.exists() and all((path / filename).exists() for filename in required_files)

    @staticmethod
    def _mirror_model_dir_if_needed(path: Path) -> Path:
        if OCREngine._is_ascii_path(path):
            return path

        cache_root = OCREngine._ascii_cache_root()
        if cache_root is None:
            return path

        target = cache_root / path.name
        target.mkdir(parents=True, exist_ok=True)

        for source_file in path.iterdir():
            if not source_file.is_file():
                continue
            target_file = target / source_file.name
            if OCREngine._needs_copy(source_file, target_file):
                shutil.copy2(source_file, target_file)

        return target

    @staticmethod
    def _needs_copy(source: Path, target: Path) -> bool:
        if not target.exists():
            return True
        source_stat = source.stat()
        target_stat = target.stat()
        return (
            source_stat.st_size != target_stat.st_size
            or int(source_stat.st_mtime) != int(target_stat.st_mtime)
        )

    @staticmethod
    def _is_ascii_path(path: Path) -> bool:
        try:
            str(path).encode("ascii")
        except UnicodeEncodeError:
            return False
        return True

    @staticmethod
    def _ascii_cache_root() -> Path | None:
        candidates = [Path.home() / ".codex" / "memories"]
        for env_name in ("LOCALAPPDATA", "USERPROFILE", "TEMP"):
            value = os.environ.get(env_name)
            if value:
                candidates.append(Path(value))

        for base in candidates:
            root = base / "framix" / "ocr-models"
            if OCREngine._is_ascii_path(root):
                return root
        return None

    @staticmethod
    def _extract_payload(page) -> dict:
        if isinstance(page, dict):
            if isinstance(page.get("res"), dict):
                return page["res"]
            return page
        return {}

    @staticmethod
    def _normalize_box(raw_box) -> list[list[float]]:
        if raw_box is None:
            return []
        if hasattr(raw_box, "tolist"):
            raw_box = raw_box.tolist()
        if not isinstance(raw_box, list):
            return []

        normalized: list[list[float]] = []
        for point in raw_box:
            if hasattr(point, "tolist"):
                point = point.tolist()
            if not isinstance(point, list) or len(point) < 2:
                continue
            normalized.append([float(point[0]), float(point[1])])
        return normalized
