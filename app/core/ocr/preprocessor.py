from __future__ import annotations

import cv2
import numpy as np


def preprocess_for_ocr(frame_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    enlarged = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.medianBlur(enlarged, 3)
    enhanced = cv2.equalizeHist(denoised)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
