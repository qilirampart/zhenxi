from __future__ import annotations

import re
import unicodedata

BLACKLIST_TERMS = {
    "抖音",
    "点赞",
    "评论",
    "分享",
    "原声",
    "进入直播间",
    "搜索",
}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def clean_ocr_text(text: str) -> str:
    normalized = normalize_text(text)
    lines: list[str] = []
    for line in normalized.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate in BLACKLIST_TERMS:
            continue
        if len(candidate) <= 2 and candidate in {"赞", "评", "转"}:
            continue
        lines.append(candidate)
    return "\n".join(lines).strip()
