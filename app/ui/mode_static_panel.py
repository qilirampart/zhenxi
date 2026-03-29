from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import DEFAULT_STATIC_CANDIDATE_FRAME_COUNT
from app.ui.no_wheel_combo_box import NoWheelComboBox


class ModeStaticPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeStaticPanel")

        self.selection_mode_combo = NoWheelComboBox()
        self.selection_mode_combo.addItem("自动 + 手动补充", "hybrid")
        self.selection_mode_combo.addItem("自动选帧", "auto")
        self.selection_mode_combo.addItem("手动选帧", "manual")

        self.ocr_engine_combo = NoWheelComboBox()
        self.ocr_engine_combo.addItem("视觉 API（云端）", "api")
        self.ocr_engine_combo.addItem("本地 PaddleOCR", "paddle")
        self.ocr_engine_combo.addItem("自动（本地优先）", "auto")

        self.candidate_count_label = QLabel(str(DEFAULT_STATIC_CANDIDATE_FRAME_COUNT))
        self.candidate_count_label.setProperty("role", "inlineValue")

        self.enable_roi_checkbox = QCheckBox("启用手动框选 OCR 区域")
        self.keep_screenshots_checkbox = QCheckBox("保留截图")
        self.keep_screenshots_checkbox.setChecked(True)

        self.generate_candidates_button = QPushButton("自动生成候选帧")
        self.generate_candidates_button.setProperty("role", "secondary")
        self.generate_candidates_button.setEnabled(False)

        self.add_current_frame_button = QPushButton("添加当前帧")
        self.add_current_frame_button.setProperty("role", "secondary")
        self.add_current_frame_button.setEnabled(False)

        self.remove_selected_button = QPushButton("删除选中帧")
        self.remove_selected_button.setProperty("role", "danger")
        self.remove_selected_button.setEnabled(False)

        self.selected_frames_list = QListWidget()
        self.selected_frames_list.setObjectName("candidateFrameList")
        self.selected_frames_list.setSelectionMode(QListWidget.SingleSelection)
        self.selected_frames_list.setEnabled(False)
        self.selected_frames_list.setMinimumHeight(140)
        self.selected_frames_list.setMaximumHeight(220)

        options_group = QGroupBox("识别参数")
        options_group.setObjectName("settingsGroup")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(10)
        options_layout.addWidget(self._build_field_block("选帧方式", self.selection_mode_combo))
        options_layout.addWidget(self._build_value_block("候选帧数量", self.candidate_count_label))
        options_layout.addWidget(self._build_field_block("OCR 引擎", self.ocr_engine_combo))
        options_layout.addWidget(self._build_field_block("OCR 区域", self.enable_roi_checkbox))
        options_layout.addWidget(self._build_field_block("截图保存", self.keep_screenshots_checkbox))

        candidate_group = QGroupBox("候选帧管理")
        candidate_group.setObjectName("settingsGroup")
        candidate_layout = QVBoxLayout(candidate_group)
        candidate_layout.setSpacing(10)

        candidate_hint = QLabel("自动候选帧按清晰度和稳定度评分；图片素材会默认生成一张静态帧。")
        candidate_hint.setProperty("role", "sectionNote")
        candidate_hint.setWordWrap(True)

        candidate_layout.addWidget(candidate_hint)
        candidate_layout.addWidget(self.generate_candidates_button)
        candidate_layout.addWidget(self.add_current_frame_button)
        candidate_layout.addWidget(self.remove_selected_button)
        candidate_layout.addWidget(self.selected_frames_list)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(options_group)
        layout.addWidget(candidate_group)

    def _build_field_block(self, label_text: str, widget: QWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("stackField")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setProperty("role", "fieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return card

    def _build_value_block(self, label_text: str, value_widget: QWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("stackField")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setProperty("role", "fieldLabel")
        layout.addWidget(label)
        layout.addWidget(value_widget)
        return card

    def selected_mode(self) -> str:
        return str(self.selection_mode_combo.currentData())

    def selected_ocr_mode(self) -> str:
        return str(self.ocr_engine_combo.currentData())

    def set_source_capabilities(
        self,
        loaded: bool,
        *,
        can_generate_candidates: bool,
        can_add_current_frame: bool,
    ) -> None:
        self.generate_candidates_button.setEnabled(loaded and can_generate_candidates)
        self.add_current_frame_button.setEnabled(loaded and can_add_current_frame)
        self.selected_frames_list.setEnabled(loaded)
        self._update_remove_button_state()

    def set_video_loaded(self, loaded: bool) -> None:
        self.set_source_capabilities(
            loaded,
            can_generate_candidates=loaded,
            can_add_current_frame=loaded,
        )

    def clear_frame_list(self) -> None:
        self.selected_frames_list.clear()
        self._update_remove_button_state()

    def add_frame_item(self, label: str, frame_key: int, selected: bool = True) -> None:
        for index in range(self.selected_frames_list.count()):
            item = self.selected_frames_list.item(index)
            if item.data(Qt.UserRole) == frame_key:
                item.setSelected(True)
                item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
                return

        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, frame_key)
        item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
        self.selected_frames_list.addItem(item)
        self._update_remove_button_state()

    def checked_frame_keys(self) -> list[int]:
        keys: list[int] = []
        for index in range(self.selected_frames_list.count()):
            item = self.selected_frames_list.item(index)
            if item.checkState() == Qt.Checked:
                keys.append(int(item.data(Qt.UserRole)))
        return keys

    def remove_selected_item(self) -> int | None:
        row = self.selected_frames_list.currentRow()
        if row < 0:
            return None
        item = self.selected_frames_list.takeItem(row)
        self._update_remove_button_state()
        return int(item.data(Qt.UserRole))

    def _update_remove_button_state(self) -> None:
        self.remove_selected_button.setEnabled(
            self.selected_frames_list.count() > 0 and self.selected_frames_list.isEnabled()
        )
