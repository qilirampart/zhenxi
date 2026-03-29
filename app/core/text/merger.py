from __future__ import annotations

from app.core.text.cleaner import normalize_text
from app.core.text.deduplicator import deduplicate_texts


def merge_static_texts(texts: list[str]) -> str:
    unique = deduplicate_texts(texts)
    if not unique:
        return ""

    normalized_pairs = [(text, normalize_text(text)) for text in unique]
    normalized_pairs.sort(key=lambda item: len(item[1]), reverse=True)

    kept: list[str] = []
    seen_normalized: list[str] = []
    for original, normalized in normalized_pairs:
        if any(normalized in existing or existing in normalized for existing in seen_normalized):
            continue
        kept.append(original.strip())
        seen_normalized.append(normalized)

    return "\n\n".join(kept)
