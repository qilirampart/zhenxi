from __future__ import annotations


def format_segmented_results(items: list[tuple[str, str]]) -> str:
    blocks: list[str] = []
    for title, content in items:
        blocks.append(f"[{title}]\n{content.strip() or '（无文本）'}")
    return "\n\n".join(blocks)
