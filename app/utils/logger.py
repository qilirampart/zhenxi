from __future__ import annotations

import logging
from pathlib import Path

from app.config.settings import LOG_DIR

_LOGGING_READY = False


def configure_logging() -> None:
    global _LOGGING_READY
    if _LOGGING_READY:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = Path(LOG_DIR) / "app.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    _LOGGING_READY = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
