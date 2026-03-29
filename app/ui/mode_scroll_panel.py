from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from app.config.settings import AVAILABLE_SCROLL_INTERVALS, DEFAULT_SCROLL_INTERVAL_SECONDS


class ModeScrollPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeScrollPanel")

        self.interval_combo = QComboBox()
        for interval in AVAILABLE_SCROLL_INTERVALS:
            self.interval_combo.addItem(f"每 {interval} 秒 1 帧", interval)
        default_index = max(0, AVAILABLE_SCROLL_INTERVALS.index(DEFAULT_SCROLL_INTERVAL_SECONDS))
        self.interval_combo.setCurrentIndex(default_index)

        self.enable_roi_checkbox = QCheckBox("启用手动框选 OCR 区域")
        self.keep_screenshots_checkbox = QCheckBox("保留截图")
        self.keep_screenshots_checkbox.setChecked(True)
        self.estimated_frames_label = QLabel("导入视频后自动计算")
        self.estimated_frames_label.setProperty("role", "inlineValue")

        planning_note = QLabel("滚动模式的参数面板先行就位，但识别链路仍会在 Build-3 接入。")
        planning_note.setProperty("role", "sectionNote")
        planning_note.setWordWrap(True)

        options_group = QGroupBox("滚动提词参数")
        options_group.setObjectName("settingsGroup")
        options_form = QFormLayout(options_group)
        options_form.setSpacing(12)
        options_form.addRow("截图频率", self.interval_combo)
        options_form.addRow("OCR 区域", self.enable_roi_checkbox)
        options_form.addRow("截图保存", self.keep_screenshots_checkbox)
        options_form.addRow("预计截图数", self.estimated_frames_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(planning_note)
        layout.addWidget(options_group)

    def current_interval_seconds(self) -> int:
        return int(self.interval_combo.currentData())

    def update_estimated_frames(self, duration_ms: int) -> None:
        if duration_ms <= 0:
            self.estimated_frames_label.setText("导入视频后自动计算")
            return
        interval = self.current_interval_seconds()
        count = (duration_ms + interval * 1000 - 1) // (interval * 1000)
        self.estimated_frames_label.setText(f"约 {count} 张")
