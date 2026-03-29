from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class ResultPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultsCard")

        title_label = QLabel("识别结果")
        title_label.setProperty("role", "sectionTitle")

        subtitle_label = QLabel("完整结果用于复制和导出，分帧结果与状态日志用于回溯和人工校对。")
        subtitle_label.setProperty("role", "sectionSubtitle")
        subtitle_label.setWordWrap(True)

        self.full_text_edit = QPlainTextEdit()
        self.full_text_edit.setObjectName("resultPrimaryEditor")
        self.full_text_edit.setPlaceholderText("提取完成后，这里会显示整理后的完整提示词。")

        self.segmented_text_edit = QPlainTextEdit()
        self.segmented_text_edit.setObjectName("resultSecondaryEditor")
        self.segmented_text_edit.setReadOnly(True)
        self.segmented_text_edit.setPlaceholderText("这里会显示按帧拆分的 OCR 结果，便于复核。")

        self.status_log_edit = QPlainTextEdit()
        self.status_log_edit.setObjectName("statusLogEditor")
        self.status_log_edit.setReadOnly(True)
        self.status_log_edit.setPlaceholderText("处理日志会显示在这里。")

        self.copy_button = QPushButton("复制结果")
        self.copy_button.setProperty("role", "secondary")
        self.export_txt_button = QPushButton("导出 TXT")
        self.export_txt_button.setProperty("role", "secondary")
        self.export_json_button = QPushButton("导出 JSON")
        self.export_json_button.setProperty("role", "secondary")
        self.copy_button.setEnabled(False)
        self.export_txt_button.setEnabled(False)
        self.export_json_button.setEnabled(False)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("resultTabs")
        self.tabs.addTab(self.full_text_edit, "完整结果")
        self.tabs.addTab(self.segmented_text_edit, "按帧结果")
        self.tabs.addTab(self.status_log_edit, "状态日志")

        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        buttons_layout.addWidget(self.copy_button)
        buttons_layout.addWidget(self.export_txt_button)
        buttons_layout.addWidget(self.export_json_button)
        buttons_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons_layout)

        self.copy_button.clicked.connect(self.copy_full_text)

    def append_status(self, message: str) -> None:
        self.status_log_edit.appendPlainText(message)

    def clear_all(self) -> None:
        self.full_text_edit.clear()
        self.segmented_text_edit.clear()
        self.status_log_edit.clear()
        self.copy_button.setEnabled(False)
        self.export_txt_button.setEnabled(False)
        self.export_json_button.setEnabled(False)

    def set_full_text(self, text: str) -> None:
        self.full_text_edit.setPlainText(text)
        enabled = bool(text.strip())
        self.copy_button.setEnabled(enabled)
        self.export_txt_button.setEnabled(enabled)
        self.export_json_button.setEnabled(enabled)

    def set_segmented_text(self, text: str) -> None:
        self.segmented_text_edit.setPlainText(text)

    def copy_full_text(self) -> None:
        text = self.full_text_edit.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
