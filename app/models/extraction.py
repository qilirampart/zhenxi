from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ROI:
    x: int
    y: int
    width: int
    height: int
    source: str = "auto"
