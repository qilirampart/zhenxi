from __future__ import annotations

COMMON_RATIOS: dict[str, float] = {
    "9:16": 9 / 16,
    "16:9": 16 / 9,
    "1:1": 1.0,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
}


def detect_aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "unknown"

    current_ratio = width / height
    return min(
        COMMON_RATIOS,
        key=lambda label: abs(COMMON_RATIOS[label] - current_ratio),
    )


def fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0 or max_width <= 0 or max_height <= 0:
        return 0, 0

    scale = min(max_width / width, max_height / height)
    return max(1, int(width * scale)), max(1, int(height * scale))
