from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QRubberBand, QSizePolicy, QSlider, QVBoxLayout, QWidget

from app.config.settings import (
    DEFAULT_PREVIEW_CANVAS_RATIO,
    DEFAULT_PREVIEW_RATIO,
    PREVIEW_MIN_WIDTH,
    PREVIEW_PLAYER_MIN_HEIGHT,
    PREVIEW_PLAYER_PADDING,
    PREVIEW_PLAYER_PREFERRED_HEIGHT,
)
from app.core.video.ratio import COMMON_RATIOS, fit_size


class VideoPreviewWidget(QWidget):
    seek_requested = Signal(int)
    roi_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None
        self._suspend_slider_signal = False
        self._source_aspect_ratio = DEFAULT_PREVIEW_CANVAS_RATIO
        self._preview_ratio_key = DEFAULT_PREVIEW_RATIO
        self._zoom_factor = 1.0
        self._roi_enabled = False
        self._drag_origin: QPoint | None = None
        self._current_roi_rect: QRect | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.player_container = QWidget()
        self.player_container.setObjectName("previewStage")
        self.player_container.setMinimumHeight(PREVIEW_PLAYER_MIN_HEIGHT)
        self.player_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.player_container.installEventFilter(self)
        self.player_container.setMouseTracking(True)

        self.canvas_label = QLabel("尚未加载素材", self.player_container)
        self.canvas_label.setObjectName("previewCanvas")
        self.canvas_label.setAlignment(Qt.AlignCenter)
        self.canvas_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._roi_band = QRubberBand(QRubberBand.Rectangle, self.player_container)
        self._roi_band.hide()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("previewSlider")
        self.slider.setEnabled(False)
        self.slider.setRange(0, 0)

        self.current_time_label = QLabel("00:00")
        self.current_time_label.setObjectName("timelineValue")
        self.total_time_label = QLabel("00:00")
        self.total_time_label.setObjectName("timelineValue")

        timeline_layout = QHBoxLayout()
        timeline_layout.setSpacing(10)
        timeline_layout.addWidget(self.current_time_label)
        timeline_layout.addStretch(1)
        timeline_layout.addWidget(self.total_time_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.player_container, 1)
        layout.addWidget(self.slider)
        layout.addLayout(timeline_layout)

        self.slider.valueChanged.connect(self._handle_slider_changed)
        self._update_canvas_geometry()

    def sizeHint(self) -> QSize:  # noqa: N802
        controls_height = self.slider.sizeHint().height() + max(
            self.current_time_label.sizeHint().height(),
            self.total_time_label.sizeHint().height(),
        ) + 28
        player_height = int(round(PREVIEW_PLAYER_PREFERRED_HEIGHT * self._zoom_factor))
        return QSize(PREVIEW_MIN_WIDTH, player_height + controls_height)

    def set_duration(self, duration_ms: int) -> None:
        safe_duration = max(0, duration_ms)
        self._suspend_slider_signal = True
        self.slider.setEnabled(safe_duration > 0)
        self.slider.setRange(0, safe_duration)
        self.slider.setValue(0)
        self._suspend_slider_signal = False
        self.current_time_label.setText(self._format_time(0))
        self.total_time_label.setText(self._format_time(safe_duration))

    def set_position(self, position_ms: int) -> None:
        self._suspend_slider_signal = True
        self.slider.setValue(min(max(0, position_ms), self.slider.maximum()))
        self._suspend_slider_signal = False
        self.current_time_label.setText(self._format_time(position_ms))

    def set_preview_ratio(self, ratio_key: str) -> None:
        if ratio_key != DEFAULT_PREVIEW_RATIO and ratio_key not in COMMON_RATIOS:
            ratio_key = DEFAULT_PREVIEW_RATIO
        self._preview_ratio_key = ratio_key
        self._update_canvas_geometry()
        self._refresh_pixmap()

    def set_zoom_factor(self, zoom_factor: float) -> None:
        safe_zoom = min(max(zoom_factor, 1.0), 3.0)
        self._zoom_factor = safe_zoom
        self.player_container.setMinimumHeight(int(round(PREVIEW_PLAYER_MIN_HEIGHT * safe_zoom)))
        self.player_container.updateGeometry()
        self.updateGeometry()
        self._update_canvas_geometry()
        self._refresh_pixmap()

    def set_roi_enabled(self, enabled: bool) -> None:
        self._roi_enabled = enabled
        if not enabled:
            self._drag_origin = None

    def clear_roi(self) -> None:
        self._drag_origin = None
        self._current_roi_rect = None
        self._roi_band.hide()
        self.roi_changed.emit(None)

    def current_roi(self) -> tuple[int, int, int, int] | None:
        if self._current_roi_rect is None:
            return None
        return (
            int(self._current_roi_rect.x()),
            int(self._current_roi_rect.y()),
            int(self._current_roi_rect.width()),
            int(self._current_roi_rect.height()),
        )

    def clear_preview(self) -> None:
        self._source_pixmap = None
        self._source_aspect_ratio = DEFAULT_PREVIEW_CANVAS_RATIO
        self.canvas_label.setPixmap(QPixmap())
        self.canvas_label.setText("尚未加载素材")
        self.clear_roi()
        self._update_canvas_geometry()
        self.set_duration(0)

    def display_frame(self, frame_bgr) -> None:
        height, width, channels = frame_bgr.shape
        self._source_aspect_ratio = width / max(height, 1)
        rgb_frame = frame_bgr[:, :, ::-1].copy()
        image = QImage(
            rgb_frame.data,
            width,
            height,
            channels * width,
            QImage.Format_RGB888,
        ).copy()
        self._source_pixmap = QPixmap.fromImage(image)
        self.canvas_label.setText("")
        self._update_canvas_geometry()
        self._refresh_pixmap()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.player_container:
            if event.type() == QEvent.Resize:
                self._update_canvas_geometry()
                self._refresh_pixmap()
                return False
            if event.type() == QEvent.MouseButtonPress:
                self._handle_mouse_press(event)
                return False
            if event.type() == QEvent.MouseMove:
                self._handle_mouse_move(event)
                return False
            if event.type() == QEvent.MouseButtonRelease:
                self._handle_mouse_release(event)
                return False
        return super().eventFilter(watched, event)

    def _update_canvas_geometry(self) -> None:
        available_width = max(0, self.player_container.width() - PREVIEW_PLAYER_PADDING * 2)
        available_height = max(0, self.player_container.height() - PREVIEW_PLAYER_PADDING * 2)
        if available_width <= 0 or available_height <= 0:
            self.canvas_label.setGeometry(0, 0, 0, 0)
            return

        ratio = self._resolved_preview_ratio()
        ratio_width = max(1, int(round(ratio * 1000)))
        canvas_width, canvas_height = fit_size(
            ratio_width,
            1000,
            available_width,
            available_height,
        )
        offset_x = (self.player_container.width() - canvas_width) // 2
        offset_y = (self.player_container.height() - canvas_height) // 2
        self.canvas_label.setGeometry(offset_x, offset_y, canvas_width, canvas_height)
        self._sync_roi_band()

    def _resolved_preview_ratio(self) -> float:
        if self._preview_ratio_key == DEFAULT_PREVIEW_RATIO:
            return self._source_aspect_ratio
        return COMMON_RATIOS.get(self._preview_ratio_key, DEFAULT_PREVIEW_CANVAS_RATIO)

    def _refresh_pixmap(self) -> None:
        if not self._source_pixmap:
            self.canvas_label.setPixmap(QPixmap())
            self._sync_roi_band()
            return
        if self.canvas_label.width() <= 0 or self.canvas_label.height() <= 0:
            return
        scaled = self._source_pixmap.scaled(
            self.canvas_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.canvas_label.setPixmap(scaled)
        self._sync_roi_band()

    def _handle_mouse_press(self, event) -> None:
        if not self._roi_enabled or self._source_pixmap is None or event.button() != Qt.LeftButton:
            return
        image_rect = self._displayed_image_rect()
        if not image_rect.contains(event.pos()):
            return
        self._drag_origin = self._clamp_point_to_rect(event.pos(), image_rect)
        self._roi_band.setGeometry(QRect(self._drag_origin, QSize()))
        self._roi_band.show()

    def _handle_mouse_move(self, event) -> None:
        if self._drag_origin is None or self._source_pixmap is None:
            return
        image_rect = self._displayed_image_rect()
        current = self._clamp_point_to_rect(event.pos(), image_rect)
        selection = QRect(self._drag_origin, current).normalized().intersected(image_rect)
        self._roi_band.setGeometry(selection)

    def _handle_mouse_release(self, event) -> None:
        if self._drag_origin is None or self._source_pixmap is None or event.button() != Qt.LeftButton:
            return
        image_rect = self._displayed_image_rect()
        current = self._clamp_point_to_rect(event.pos(), image_rect)
        selection = QRect(self._drag_origin, current).normalized().intersected(image_rect)
        self._drag_origin = None

        if selection.width() < 8 or selection.height() < 8:
            self.clear_roi()
            return

        self._current_roi_rect = self._map_display_rect_to_image(selection, image_rect)
        self._sync_roi_band()
        self.roi_changed.emit(self.current_roi())

    def _sync_roi_band(self) -> None:
        if self._current_roi_rect is None or self._source_pixmap is None:
            self._roi_band.hide()
            return

        image_rect = self._displayed_image_rect()
        display_rect = self._map_image_rect_to_display(self._current_roi_rect, image_rect)
        if display_rect.width() <= 0 or display_rect.height() <= 0:
            self._roi_band.hide()
            return

        self._roi_band.setGeometry(display_rect)
        self._roi_band.show()

    def _displayed_image_rect(self) -> QRect:
        label_rect = self.canvas_label.geometry()
        if self._source_pixmap is None or label_rect.width() <= 0 or label_rect.height() <= 0:
            return label_rect

        scaled_width, scaled_height = fit_size(
            max(1, self._source_pixmap.width()),
            max(1, self._source_pixmap.height()),
            label_rect.width(),
            label_rect.height(),
        )
        offset_x = label_rect.x() + (label_rect.width() - scaled_width) // 2
        offset_y = label_rect.y() + (label_rect.height() - scaled_height) // 2
        return QRect(offset_x, offset_y, scaled_width, scaled_height)

    @staticmethod
    def _clamp_point_to_rect(point: QPoint, rect: QRect) -> QPoint:
        x = min(max(point.x(), rect.left()), rect.right())
        y = min(max(point.y(), rect.top()), rect.bottom())
        return QPoint(x, y)

    def _map_display_rect_to_image(self, selection: QRect, image_rect: QRect) -> QRect:
        source_width = max(1, self._source_pixmap.width())
        source_height = max(1, self._source_pixmap.height())
        scale_x = source_width / max(1, image_rect.width())
        scale_y = source_height / max(1, image_rect.height())

        x = int(round((selection.x() - image_rect.x()) * scale_x))
        y = int(round((selection.y() - image_rect.y()) * scale_y))
        width = int(round(selection.width() * scale_x))
        height = int(round(selection.height() * scale_y))
        return QRect(
            max(0, min(x, source_width - 1)),
            max(0, min(y, source_height - 1)),
            max(1, min(width, source_width)),
            max(1, min(height, source_height)),
        )

    def _map_image_rect_to_display(self, image_roi: QRect, image_rect: QRect) -> QRect:
        source_width = max(1, self._source_pixmap.width())
        source_height = max(1, self._source_pixmap.height())
        scale_x = image_rect.width() / source_width
        scale_y = image_rect.height() / source_height

        x = image_rect.x() + int(round(image_roi.x() * scale_x))
        y = image_rect.y() + int(round(image_roi.y() * scale_y))
        width = int(round(image_roi.width() * scale_x))
        height = int(round(image_roi.height() * scale_y))
        return QRect(x, y, max(1, width), max(1, height))

    def _handle_slider_changed(self, value: int) -> None:
        self.current_time_label.setText(self._format_time(value))
        if not self._suspend_slider_signal:
            self.seek_requested.emit(value)

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        total_seconds = max(0, milliseconds // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
