from __future__ import annotations

from rapidfuzz import fuzz

from app.core.text.cleaner import normalize_text


def deduplicate_texts(texts: list[str], threshold: int = 92) -> list[str]:
    unique_texts: list[str] = []
    normalized_history: list[str] = []

    for text in texts:
        normalized = normalize_text(text)
        if not normalized:
            continue
        if normalized in normalized_history:
            continue
        if any(fuzz.ratio(normalized, existing) >= threshold for existing in normalized_history):
            continue
        normalized_history.append(normalized)
        unique_texts.append(text.strip())

    return unique_texts
