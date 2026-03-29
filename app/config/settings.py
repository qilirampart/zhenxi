from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_NAME = "帧析"
APP_ORGANIZATION = "Claude Code"
APP_ICON_PATH = PROJECT_ROOT / "assets" / "app-icon.svg"

RUNTIME_DIR = PROJECT_ROOT / "runtime"
OCR_MODEL_DIR = RUNTIME_DIR / "models"
OCR_DET_MODEL_DIR = OCR_MODEL_DIR / "text_detection"
OCR_REC_MODEL_DIR = OCR_MODEL_DIR / "text_recognition"
OCR_CLS_MODEL_DIR = OCR_MODEL_DIR / "textline_orientation"

OUTPUT_DIR = PROJECT_ROOT / "output"
SCREENSHOT_DIR = OUTPUT_DIR / "screenshots"
DOWNLOAD_DIR = OUTPUT_DIR / "downloads"
EXTRACTED_AUDIO_DIR = OUTPUT_DIR / "audio"
TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"
LOG_DIR = OUTPUT_DIR / "logs"

TENCENT_ASR_CONFIG_PATH = RUNTIME_DIR / "tencent_asr_config.json"
ASR_DIRECT_UPLOAD_LIMIT_BYTES = 5 * 1024 * 1024
ASR_AUDIO_CHUNK_SECONDS = 10 * 60

SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".flv",
    ".wmv",
    ".m4v",
}

SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
}

DEFAULT_STATIC_CANDIDATE_FRAME_COUNT = 5
AVAILABLE_SCROLL_INTERVALS = (2, 3)
DEFAULT_SCROLL_INTERVAL_SECONDS = 2

PREVIEW_RATIO_PRESETS = ("原始", "9:16", "16:9", "1:1", "3:4", "4:3")
DEFAULT_PREVIEW_RATIO = "原始"
DEFAULT_PREVIEW_CANVAS_RATIO = 9 / 16
PREVIEW_MIN_WIDTH = 320
PREVIEW_MIN_HEIGHT = 320
PREVIEW_PLAYER_MIN_HEIGHT = 460
PREVIEW_PLAYER_PREFERRED_HEIGHT = 560
PREVIEW_PLAYER_PADDING = 20
WINDOW_MIN_WIDTH = 1400
WINDOW_MIN_HEIGHT = 860


def ensure_app_directories() -> None:
    for directory in (OUTPUT_DIR, SCREENSHOT_DIR, DOWNLOAD_DIR, EXTRACTED_AUDIO_DIR, TRANSCRIPT_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
