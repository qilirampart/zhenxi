from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config.settings import DOWNLOAD_DIR, LOG_DIR, OUTPUT_DIR, PROJECT_ROOT, SCREENSHOT_DIR


def ensure_output_directories() -> None:
    for directory in (OUTPUT_DIR, SCREENSHOT_DIR, DOWNLOAD_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def build_screenshot_session_dir(video_name: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in video_name)
    session_dir = SCREENSHOT_DIR / f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def build_download_output_path(seed_name: str, suffix: str = ".mp4") -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in seed_name).strip("_")
    safe_name = safe_name or "douyin_video"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return DOWNLOAD_DIR / f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"


def build_article_session_dir(seed_name: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in seed_name).strip("_")
    safe_name = safe_name or "wechat_article"
    session_dir = DOWNLOAD_DIR / f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


__all__ = [
    "PROJECT_ROOT",
    "OUTPUT_DIR",
    "SCREENSHOT_DIR",
    "DOWNLOAD_DIR",
    "LOG_DIR",
    "ensure_output_directories",
    "build_screenshot_session_dir",
    "build_download_output_path",
    "build_article_session_dir",
]
