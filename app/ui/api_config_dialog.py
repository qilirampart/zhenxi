from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.api_config_service import (
    APIConfigService,
    APIConfigTestError,
    APIConfigValidationError,
    MAX_API_PROVIDERS,
)
from app.ui.no_wheel_combo_box import NoWheelComboBox


class APIConnectionTestThread(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, service: APIConfigService, provider: dict[str, Any]) -> None:
        super().__init__()
        self._service = service
        self._provider = dict(provider)

    def run(self) -> None:
        try:
            result = self._service.test_connection(self._provider)
        except (APIConfigValidationError, APIConfigTestError) as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(result)


class APIConfigDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API 配置")
        self.setModal(True)
        self.setMinimumSize(640, 520)
        self.resize(760, 720)

        self._service = APIConfigService()
        self._providers: list[dict[str, Any]] = []
        self._active_provider_id = ""
        self._current_index = -1
        self._saved_config: dict[str, Any] | None = None
        self._test_thread: APIConnectionTestThread | None = None

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
        title = QLabel("云端 OCR API 配置")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel(
            "最多配置 5 个 API 通道。当前首选通道优先使用，失败后会自动降级到后续启用通道。"
        )
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        self.provider_selector = NoWheelComboBox()
        self.provider_selector.currentIndexChanged.connect(self._on_provider_changed)

        self.add_provider_button = QPushButton("新增通道")
        self.add_provider_button.setProperty("role", "secondary")
        self.add_provider_button.clicked.connect(self._add_provider)

        self.remove_provider_button = QPushButton("删除通道")
        self.remove_provider_button.setProperty("role", "danger")
        self.remove_provider_button.clicked.connect(self._remove_provider)

        self.set_active_button = QPushButton("设为首选")
        self.set_active_button.setProperty("role", "secondary")
        self.set_active_button.clicked.connect(self._set_current_as_active)

        selector_row = QHBoxLayout()
        selector_row.setSpacing(10)
        selector_row.addWidget(QLabel("当前通道"))
        selector_row.addWidget(self.provider_selector, 1)
        selector_row.addWidget(self.add_provider_button)
        selector_row.addWidget(self.remove_provider_button)
        selector_row.addWidget(self.set_active_button)

        self.active_hint_label = QLabel()
        self.active_hint_label.setProperty("role", "sectionNote")
        self.active_hint_label.setWordWrap(True)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：主通道 / 备用通道 1")
        self.enabled_checkbox = QCheckBox("启用该通道")

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("sk-...")

        self.show_key_checkbox = QCheckBox("显示 API Key")
        self.show_key_checkbox.toggled.connect(self._toggle_key_visibility)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o")

        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 600.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setSuffix(" 秒")

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 32768)
        self.max_tokens_spin.setSingleStep(100)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("用于 OCR 提取的提示词。")
        self.prompt_edit.setFixedHeight(140)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.addRow("通道名称", self.name_edit)
        form.addRow("", self.enabled_checkbox)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("", self.show_key_checkbox)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("模型", self.model_edit)
        form.addRow("超时", self.timeout_spin)
        form.addRow("最大 Token", self.max_tokens_spin)
        form.addRow("OCR 提示词", self.prompt_edit)

        self.status_label = QLabel("尚未测试")
        self.status_label.setProperty("role", "sectionNote")

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        self.detail_edit.setPlaceholderText("测试结果或错误详情会显示在这里。")
        self.detail_edit.setFixedHeight(160)

        self.test_button = QPushButton("测试当前通道")
        self.test_button.setProperty("role", "secondary")
        self.test_button.clicked.connect(self._start_test)

        self.save_button = QPushButton("保存配置")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self._save_config)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setProperty("role", "secondary")
        self.cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addWidget(self.test_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.addWidget(title)
        content_layout.addWidget(subtitle)
        content_layout.addLayout(selector_row)
        content_layout.addWidget(self.active_hint_label)
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
        layout.addLayout(buttons)

    def _load_current_config(self) -> None:
        config = self._service.load_config()
        self._providers = [dict(provider) for provider in config.get("providers", [])]
        self._active_provider_id = str(config.get("active_provider_id") or "")
        if not self._providers:
            self._providers = [self._service.build_provider(name="默认通道")]
            self._active_provider_id = self._providers[0]["id"]

        self._refresh_provider_selector()
        self._load_provider_into_form(self._index_for_provider_id(self._active_provider_id))

    def _refresh_provider_selector(self) -> None:
        with QSignalBlocker(self.provider_selector):
            self.provider_selector.clear()
            for provider in self._providers:
                label = provider["name"]
                if provider["id"] == self._active_provider_id:
                    label = f"{label}（首选）"
                if not provider.get("enabled", True):
                    label = f"{label} [已停用]"
                self.provider_selector.addItem(label, provider["id"])

        self.remove_provider_button.setEnabled(len(self._providers) > 1)
        self.add_provider_button.setEnabled(len(self._providers) < MAX_API_PROVIDERS)
        self._refresh_active_hint()

    def _refresh_active_hint(self) -> None:
        ordered = self._service.get_fallback_providers(
            {"active_provider_id": self._active_provider_id, "providers": self._providers}
        )
        if ordered:
            chain = " -> ".join(str(provider.get("name") or "未命名通道") for provider in ordered)
            self.active_hint_label.setText(f"自动降级顺序：{chain}")
        else:
            self.active_hint_label.setText("当前没有启用的通道，提取时将无法使用云端 OCR。")

    def _load_provider_into_form(self, index: int) -> None:
        if not self._providers:
            return

        safe_index = min(max(index, 0), len(self._providers) - 1)
        self._current_index = safe_index
        provider = self._providers[safe_index]

        with QSignalBlocker(self.provider_selector):
            self.provider_selector.setCurrentIndex(safe_index)

        self.name_edit.setText(str(provider.get("name", "")))
        self.enabled_checkbox.setChecked(bool(provider.get("enabled", True)))
        self.base_url_edit.setText(str(provider.get("base_url", "")))
        self.api_key_edit.setText(str(provider.get("api_key", "")))
        self.model_edit.setText(str(provider.get("model", "")))
        self.timeout_spin.setValue(float(provider.get("timeout_seconds", 30)))
        self.max_tokens_spin.setValue(int(provider.get("max_tokens", 1000)))
        self.prompt_edit.setPlainText(str(provider.get("prompt", "")))
        self.status_label.setText("尚未测试")

    def _sync_current_provider(self) -> None:
        if self._current_index < 0 or self._current_index >= len(self._providers):
            return
        current = dict(self._providers[self._current_index])
        current.update(
            {
                "name": self.name_edit.text().strip(),
                "enabled": self.enabled_checkbox.isChecked(),
                "base_url": self.base_url_edit.text().strip(),
                "api_key": self.api_key_edit.text().strip(),
                "model": self.model_edit.text().strip(),
                "timeout_seconds": float(self.timeout_spin.value()),
                "max_tokens": int(self.max_tokens_spin.value()),
                "prompt": self.prompt_edit.toPlainText().strip(),
            }
        )
        self._providers[self._current_index] = current

    def _build_config_payload(self) -> dict[str, Any]:
        self._sync_current_provider()
        return {
            "active_provider_id": self._active_provider_id,
            "providers": list(self._providers),
        }

    def _current_provider(self) -> dict[str, Any]:
        self._sync_current_provider()
        if self._current_index < 0 or self._current_index >= len(self._providers):
            raise APIConfigValidationError("当前没有可用的 API 通道。")
        return dict(self._providers[self._current_index])

    def _toggle_key_visibility(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _on_provider_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._providers):
            return
        if self._current_index != -1:
            self._sync_current_provider()
        self._load_provider_into_form(index)

    def _add_provider(self) -> None:
        if len(self._providers) >= MAX_API_PROVIDERS:
            QMessageBox.information(self, "达到上限", f"最多只能配置 {MAX_API_PROVIDERS} 个通道。")
            return

        self._sync_current_provider()
        provider = self._service.build_provider(name=f"备用通道 {len(self._providers) + 1}")
        self._providers.append(provider)
        self._refresh_provider_selector()
        self._load_provider_into_form(len(self._providers) - 1)

    def _remove_provider(self) -> None:
        if len(self._providers) <= 1 or self._current_index < 0:
            return

        provider = self._providers.pop(self._current_index)
        if provider["id"] == self._active_provider_id:
            self._active_provider_id = self._providers[0]["id"]

        self._refresh_provider_selector()
        self._load_provider_into_form(min(self._current_index, len(self._providers) - 1))

    def _set_current_as_active(self) -> None:
        provider = self._current_provider()
        self._active_provider_id = provider["id"]
        self._refresh_provider_selector()
        self.status_label.setText(f"已将 {provider['name']} 设为首选通道")

    def _start_test(self) -> None:
        if self._test_thread is not None:
            return

        provider = self._current_provider()
        self._set_busy(True)
        self.status_label.setText(f"正在测试 {provider['name']} ...")
        self.detail_edit.setPlainText("正在测试当前通道的连通性与鉴权，不会上传图片，请稍候。")

        self._test_thread = APIConnectionTestThread(self._service, provider)
        self._test_thread.succeeded.connect(self._on_test_success)
        self._test_thread.failed.connect(self._on_test_failed)
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.start()

    def _save_config(self) -> None:
        if self._test_thread is not None:
            return

        try:
            saved = self._service.save_config(self._build_config_payload())
        except APIConfigValidationError as exc:
            self.status_label.setText("配置校验失败")
            self.detail_edit.setPlainText(str(exc))
            QMessageBox.critical(self, "配置无效", str(exc))
            return

        self._saved_config = saved
        active_provider = self._service.get_active_provider(saved)
        self.status_label.setText("配置已保存")
        self.detail_edit.setPlainText(
            f"已保存 {len(saved['providers'])} 个 API 通道。\n"
            f"当前首选：{active_provider['name']}\n"
            f"Base URL：{active_provider['base_url']}\n"
            f"模型：{active_provider['model']}"
        )
        self.accept()

    def _on_test_success(self, payload: dict[str, Any]) -> None:
        message_lines = [
            "连接测试成功。",
            f"通道：{payload.get('provider_name', '')}",
            f"Base URL：{payload.get('base_url', '')}",
            f"模型：{payload.get('model', '')}",
            f"测试方式：{'模型列表' if payload.get('test_method') == 'models' else '文本请求'}",
            f"结果：{payload.get('message', '')}",
        ]
        if payload.get("model_found") is False:
            message_lines.append("提示：当前模型未出现在服务端模型列表中，请确认模型名是否正确。")
        note = str(payload.get("note", "")).strip()
        if note:
            message_lines.append(note)
        message = "\n".join(message_lines)
        self.status_label.setText("连接测试成功")
        self.detail_edit.setPlainText(message)
        QMessageBox.information(self, "测试成功", message)

    def _on_test_failed(self, message: str) -> None:
        self.status_label.setText("连接测试失败")
        self.detail_edit.setPlainText(message)
        QMessageBox.critical(self, "测试失败", message)

    def _on_test_finished(self) -> None:
        if self._test_thread is not None:
            self._test_thread.deleteLater()
        self._test_thread = None
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.provider_selector.setEnabled(not busy)
        self.add_provider_button.setEnabled(not busy and len(self._providers) < MAX_API_PROVIDERS)
        self.remove_provider_button.setEnabled(not busy and len(self._providers) > 1)
        self.set_active_button.setEnabled(not busy)
        self.test_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)

    def _index_for_provider_id(self, provider_id: str) -> int:
        for index, provider in enumerate(self._providers):
            if provider["id"] == provider_id:
                return index
        return 0
