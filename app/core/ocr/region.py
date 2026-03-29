from __future__ import annotations

from app.models.extraction import ROI
from app.models.video import VideoMeta


def default_roi_for_video(video: VideoMeta) -> ROI:
    width = video.width
    height = video.height

    if video.aspect_ratio == "9:16":
        x = int(width * 0.05)
        y = int(height * 0.10)
        roi_width = int(width * 0.80)
        roi_height = int(height * 0.72)
    elif video.aspect_ratio == "16:9":
        x = int(width * 0.05)
        y = int(height * 0.08)
        roi_width = int(width * 0.90)
        roi_height = int(height * 0.80)
    else:
        x = int(width * 0.08)
        y = int(height * 0.08)
        roi_width = int(width * 0.84)
        roi_height = int(height * 0.84)

    return ROI(x=x, y=y, width=roi_width, height=roi_height, source="auto")
