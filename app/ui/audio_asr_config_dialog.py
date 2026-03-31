from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.tencent_asr_config_service import (
    TencentASRConfigService,
    TencentASRConfigTestError,
    TencentASRConfigValidationError,
)
from app.ui.no_wheel_combo_box import NoWheelComboBox


class TencentASRConnectionTestThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, service: TencentASRConfigService, config: dict[str, Any]) -> None:
        super().__init__()
        self._service = service
        self._config = dict(config)

    def run(self) -> None:
        try:
            result = self._service.test_connection(self._config)
        except (TencentASRConfigValidationError, TencentASRConfigTestError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class AudioASRConfigDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("音频 API 配置")
        self.setModal(True)
        self.setMinimumSize(600, 460)
        self.resize(680, 560)

        self._service = TencentASRConfigService()
        self._saved_config: dict[str, Any] | None = None
        self._test_thread: TencentASRConnectionTestThread | None = None

        self._build_ui()
        self._load_current_config()

    @property
    def saved_config(self) -> dict[str, Any] | None:
        return self._saved_config

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._test_thread is not None and self._test_thread.isRunning():
            self._test_thread.wait(200)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        title = QLabel("腾讯云语音转写配置")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("用于视频分离音频后的语音转写。默认推荐使用标准 16k 中文引擎，后续可再切换大模型。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        self.enabled_checkbox = QCheckBox("启用腾讯云语音转写")

        self.secret_id_edit = QLineEdit()
        self.secret_id_edit.setPlaceholderText("AKID...")

        self.secret_key_edit = QLineEdit()
        self.secret_key_edit.setEchoMode(QLineEdit.Password)
        self.secret_key_edit.setPlaceholderText("SecretKey")

        self.show_secret_checkbox = QCheckBox("显示 SecretKey")
        self.show_secret_checkbox.toggled.connect(self._toggle_secret_visibility)

        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("ap-shanghai")

        self.engine_combo = NoWheelComboBox()
        self.engine_combo.addItem("16k_zh", "16k_zh")
        self.engine_combo.addItem("16k_zh_en", "16k_zh_en")

        self.result_format_combo = NoWheelComboBox()
        self.result_format_combo.addItem("0 - 基础结果", 0)
        self.result_format_combo.addItem("1 - 词级结果", 1)
        self.result_format_combo.addItem("2 - 词级+标点", 2)
        self.result_format_combo.addItem("3 - 字幕分段", 3)

        self.channel_combo = NoWheelComboBox()
        self.channel_combo.addItem("单声道", 1)
        self.channel_combo.addItem("双声道", 2)

        form = QFormLayout()
        form.setSpacing(12)
        form.addRow("", self.enabled_checkbox)
        form.addRow("SecretId", self.secret_id_edit)
        form.addRow("", self.show_secret_checkbox)
        form.addRow("SecretKey", self.secret_key_edit)
        form.addRow("Region", self.region_edit)
        form.addRow("引擎模型", self.engine_combo)
        form.addRow("返回格式", self.result_format_combo)
        form.addRow("声道数", self.channel_combo)

        self.status_label = QLabel("尚未测试连接。")
        self.status_label.setProperty("role", "sectionNote")

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setPlaceholderText("这里会显示连接测试结果和接口返回详情。")
        self.detail_edit.setFixedHeight(170)

        self.test_button = QPushButton("测试连接")
        self.test_button.setProperty("role", "secondary")
        self.test_button.clicked.connect(self._start_test)

        self.save_button = QPushButton("保存配置")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self._save_config)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setProperty("role", "secondary")
        self.cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self.test_button)
        button_row.addStretch(1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.save_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.addWidget(title)
        content_layout.addWidget(subtitle)
        content_layout.addLayout(form)
        content_layout.addWidget(self.status_label)
        content_layout.addWidget(self.detail_edit)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(scroll, 1)
        layout.addLayout(button_row)

    def _load_current_config(self) -> None:
        config = self._service.load_config()
        self.enabled_checkbox.setChecked(bool(config.get("enabled", True)))
        self.secret_id_edit.setText(str(config.get("secret_id", "")))
        self.secret_key_edit.setText(str(config.get("secret_key", "")))
        self.region_edit.setText(str(config.get("region", "")))
        self._set_combo_data(self.engine_combo, str(config.get("engine_model_type", "16k_zh")))
        self._set_combo_data(self.result_format_combo, int(config.get("res_text_format", 3)))
        self._set_combo_data(self.channel_combo, int(config.get("channel_num", 1)))

    def _collect_config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled_checkbox.isChecked(),
            "secret_id": self.secret_id_edit.text().strip(),
            "secret_key": self.secret_key_edit.text().strip(),
            "region": self.region_edit.text().strip() or "ap-shanghai",
            "engine_model_type": self.engine_combo.currentData(),
            "res_text_format": int(self.result_format_combo.currentData()),
            "channel_num": int(self.channel_combo.currentData()),
        }

    def _save_config(self) -> None:
        try:
            saved = self._service.save_config(self._collect_config())
        except TencentASRConfigValidationError as exc:
            self.status_label.setText("保存失败。")
            self.detail_edit.setPlainText(str(exc))
            return
        self._saved_config = saved
        self.accept()

    def _start_test(self) -> None:
        config = self._collect_config()
        self.test_button.setEnabled(False)
        self.status_label.setText("正在测试连接...")
        self.detail_edit.setPlainText("")
        self._test_thread = TencentASRConnectionTestThread(self._service, config)
        self._test_thread.succeeded.connect(self._handle_test_success)
        self._test_thread.failed.connect(self._handle_test_failure)
        self._test_thread.finished.connect(lambda: self.test_button.setEnabled(True))
        self._test_thread.start()

    def _handle_test_success(self, result: dict[str, Any]) -> None:
        self.status_label.setText(str(result.get("message") or "测试完成。"))
        self.detail_edit.setPlainText(str(result.get("detail") or ""))

    def _handle_test_failure(self, message: str) -> None:
        self.status_label.setText("测试失败。")
        self.detail_edit.setPlainText(message)

    def _toggle_secret_visibility(self, checked: bool) -> None:
        self.secret_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    @staticmethod
    def _set_combo_data(combo: NoWheelComboBox, target: Any) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == target:
                combo.setCurrentIndex(index)
                return
