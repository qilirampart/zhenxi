from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import OUTPUT_DIR, SUPPORTED_VIDEO_EXTENSIONS, TRANSCRIPT_DIR
from app.models.audio_transcription import AudioTranscriptionResult, PreparedAudio
from app.services.audio_transcription_service import AudioTranscriptionError, AudioTranscriptionService
from app.services.tencent_asr_config_service import TencentASRConfigService
from app.ui.audio_asr_config_dialog import AudioASRConfigDialog


class AudioExtractThread(QThread):
    progress_changed = Signal(int, int, str)
    result_ready = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, service: AudioTranscriptionService, source_path: str) -> None:
        super().__init__()
        self._service = service
        self._source_path = source_path

    def run(self) -> None:
        try:
            result = self._service.extract_audio(
                self._source_path,
                progress_callback=lambda current, total, message: self.progress_changed.emit(current, total, message),
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001
            if self.isInterruptionRequested():
                self.cancelled.emit("已取消音频分离。")
                return
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(result)


class AudioTranscribeThread(QThread):
    progress_changed = Signal(int, int, str)
    result_ready = Signal(object, object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, service: AudioTranscriptionService, source_path: str, prepared: PreparedAudio | None) -> None:
        super().__init__()
        self._service = service
        self._source_path = source_path
        self._prepared = prepared

    def run(self) -> None:
        try:
            if self._prepared is None or self._prepared.source_path != self._source_path:
                prepared, result = self._service.transcribe_source(
                    self._source_path,
                    progress_callback=lambda current, total, message: self.progress_changed.emit(current, total, message),
                    should_cancel=self.isInterruptionRequested,
                )
            else:
                prepared = self._prepared
                result = self._service.transcribe_prepared_audio(
                    prepared,
                    progress_callback=lambda current, total, message: self.progress_changed.emit(current, total, message),
                    should_cancel=self.isInterruptionRequested,
                )
        except Exception as exc:  # noqa: BLE001
            if self.isInterruptionRequested():
                self.cancelled.emit("已取消语音转写。")
                return
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(prepared, result)


class AudioTranscribePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = AudioTranscriptionService()
        self._config_service = TencentASRConfigService()
        self._linked_media_path = ""
        self._linked_media_label = ""
        self._active_source_path = ""
        self._prepared_audio: PreparedAudio | None = None
        self._result: AudioTranscriptionResult | None = None
        self._extract_thread: AudioExtractThread | None = None
        self._transcribe_thread: AudioTranscribeThread | None = None

        self._build_ui()
        self._refresh_config_summary()
        self._update_actions()

    def set_linked_media(self, media_path: str | None, source_label: str = "") -> None:
        self._linked_media_path = media_path or ""
        self._linked_media_label = source_label.strip()
        if not self._active_source_path and self._linked_media_path:
            self._apply_source(self._linked_media_path, f"当前视频 / {self._linked_media_label or '已导入素材'}")
        self._update_actions()

    def clear_linked_media(self, *, clear_active: bool = False) -> None:
        previous_linked = self._linked_media_path
        self._linked_media_path = ""
        self._linked_media_label = ""
        if clear_active and self._active_source_path == previous_linked:
            self._active_source_path = ""
            self._prepared_audio = None
            self._result = None
            self.full_text_edit.clear()
            self.srt_edit.clear()
            self.audio_value.setText("尚未分离音频")
        if not self._active_source_path:
            self.source_value.setText("尚未选择语音素材")
        self._update_actions()

    def _build_ui(self) -> None:
        header_card = QFrame()
        header_card.setObjectName("overviewCard")

        title = QLabel("语音转写")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("把视频中的人声先分离为音频，再用腾讯云转写成文本和字幕。长素材会自动分段处理，不再受 5MB 直传限制卡死。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        self.source_value = QLabel("尚未选择语音素材")
        self.source_value.setProperty("role", "inlineValue")
        self.audio_value = QLabel("尚未分离音频")
        self.audio_value.setProperty("role", "secondaryText")
        self.config_value = QLabel()
        self.config_value.setProperty("role", "secondaryText")

        self.use_current_button = QPushButton("使用当前视频")
        self.use_current_button.setProperty("role", "secondary")
        self.use_current_button.clicked.connect(self._use_current_media)

        self.choose_video_button = QPushButton("选择视频")
        self.choose_video_button.setProperty("role", "secondary")
        self.choose_video_button.clicked.connect(self._choose_video)

        self.choose_audio_button = QPushButton("选择音频")
        self.choose_audio_button.setProperty("role", "secondary")
        self.choose_audio_button.clicked.connect(self._choose_audio)

        self.config_button = QPushButton("音频 API 配置")
        self.config_button.setProperty("role", "secondary")
        self.config_button.clicked.connect(self._open_config_dialog)

        self.extract_button = QPushButton("分离音频")
        self.extract_button.setProperty("role", "secondary")
        self.extract_button.clicked.connect(self._start_extract)

        self.transcribe_button = QPushButton("开始转写")
        self.transcribe_button.setProperty("role", "primary")
        self.transcribe_button.clicked.connect(self._start_transcribe)

        self.cancel_button = QPushButton("中断转写")
        self.cancel_button.setProperty("role", "danger")
        self.cancel_button.clicked.connect(self._cancel_running_task)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.progress_label = QLabel("等待开始。")
        self.progress_label.setProperty("role", "sectionNote")
        self.progress_label.setWordWrap(True)

        source_buttons = QHBoxLayout()
        source_buttons.setSpacing(10)
        source_buttons.addWidget(self.use_current_button)
        source_buttons.addWidget(self.choose_video_button)
        source_buttons.addWidget(self.choose_audio_button)
        source_buttons.addWidget(self.config_button)
        source_buttons.addStretch(1)

        action_buttons = QHBoxLayout()
        action_buttons.setSpacing(10)
        action_buttons.addWidget(self.extract_button)
        action_buttons.addWidget(self.transcribe_button)
        action_buttons.addWidget(self.cancel_button)
        action_buttons.addStretch(1)

        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(20, 20, 20, 20)
        header_layout.setSpacing(12)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addWidget(self.source_value)
        header_layout.addWidget(self.audio_value)
        header_layout.addWidget(self.config_value)
        header_layout.addLayout(source_buttons)
        header_layout.addLayout(action_buttons)
        header_layout.addWidget(self.progress_bar)
        header_layout.addWidget(self.progress_label)

        result_card = QFrame()
        result_card.setObjectName("resultsCard")
        result_title = QLabel("转写结果")
        result_title.setProperty("role", "sectionTitle")

        self.full_text_edit = QPlainTextEdit()
        self.full_text_edit.setObjectName("resultPrimaryEditor")
        self.full_text_edit.setReadOnly(True)
        self.full_text_edit.setPlaceholderText("转写完成后会在这里显示完整文本。")

        self.srt_edit = QPlainTextEdit()
        self.srt_edit.setObjectName("resultSecondaryEditor")
        self.srt_edit.setReadOnly(True)
        self.srt_edit.setPlaceholderText("这里会显示可直接导出的 SRT 字幕。")

        self.status_edit = QPlainTextEdit()
        self.status_edit.setObjectName("statusLogEditor")
        self.status_edit.setReadOnly(True)
        self.status_edit.setPlaceholderText("这里会记录音频分离和转写过程。")

        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(self.full_text_edit, "完整文本")
        self.result_tabs.addTab(self.srt_edit, "字幕 SRT")
        self.result_tabs.addTab(self.status_edit, "处理日志")

        self.export_txt_button = QPushButton("导出 TXT")
        self.export_txt_button.setProperty("role", "secondary")
        self.export_txt_button.clicked.connect(self._export_txt)

        self.export_srt_button = QPushButton("导出 SRT")
        self.export_srt_button.setProperty("role", "secondary")
        self.export_srt_button.clicked.connect(self._export_srt)

        self.export_json_button = QPushButton("导出 JSON")
        self.export_json_button.setProperty("role", "secondary")
        self.export_json_button.clicked.connect(self._export_json)

        export_row = QHBoxLayout()
        export_row.setSpacing(10)
        export_row.addWidget(self.export_txt_button)
        export_row.addWidget(self.export_srt_button)
        export_row.addWidget(self.export_json_button)
        export_row.addStretch(1)

        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(20, 20, 20, 20)
        result_layout.setSpacing(12)
        result_layout.addWidget(result_title)
        result_layout.addWidget(self.result_tabs, 1)
        result_layout.addLayout(export_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(header_card)
        layout.addWidget(result_card, 1)

    def _choose_video(self) -> None:
        filters = "视频文件 (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_VIDEO_EXTENSIONS)) + ")"
        file_path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", "", filters)
        if file_path:
            self._apply_source(file_path, "手动选择视频")

    def _choose_audio(self) -> None:
        filters = "音频文件 (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.wma *.amr)"
        file_path, _ = QFileDialog.getOpenFileName(self, "选择音频文件", "", filters)
        if file_path:
            self._apply_source(file_path, "手动选择音频")

    def _use_current_media(self) -> None:
        if not self._linked_media_path:
            return
        label = self._linked_media_label or "当前视频"
        self._apply_source(self._linked_media_path, f"当前视频 / {label}")

    def _apply_source(self, source_path: str, label: str) -> None:
        self._active_source_path = source_path
        self._prepared_audio = None
        self._result = None
        self.full_text_edit.clear()
        self.srt_edit.clear()
        self.source_value.setText(f"当前语音素材：{source_path}")
        self.audio_value.setText(f"来源：{label}")
        self._append_status(f"已选择语音素材：{source_path}")
        self._update_actions()

    def _open_config_dialog(self) -> None:
        dialog = AudioASRConfigDialog(self)
        if dialog.exec() != dialog.Accepted:
            return
        self._refresh_config_summary()
        self._append_status("音频 API 配置已更新。")

    def _refresh_config_summary(self) -> None:
        config = self._config_service.load_config()
        self.config_value.setText(
            f"当前配置：Region={config.get('region')} / 引擎={config.get('engine_model_type')} / 返回格式={config.get('res_text_format')}"
        )

    def _start_extract(self) -> None:
        if not self._active_source_path:
            QMessageBox.warning(self, "缺少素材", "请先选择视频或音频素材。")
            return
        self._extract_thread = AudioExtractThread(self._service, self._active_source_path)
        self._extract_thread.progress_changed.connect(self._handle_progress)
        self._extract_thread.result_ready.connect(self._handle_extract_success)
        self._extract_thread.failed.connect(self._handle_worker_failure)
        self._extract_thread.cancelled.connect(self._handle_worker_cancelled)
        self._extract_thread.finished.connect(self._update_actions)
        self._extract_thread.start()
        self._append_status("开始分离音频。")
        self._update_actions()

    def _start_transcribe(self) -> None:
        if not self._active_source_path:
            QMessageBox.warning(self, "缺少素材", "请先选择视频或音频素材。")
            return
        self._transcribe_thread = AudioTranscribeThread(self._service, self._active_source_path, self._prepared_audio)
        self._transcribe_thread.progress_changed.connect(self._handle_progress)
        self._transcribe_thread.result_ready.connect(self._handle_transcribe_success)
        self._transcribe_thread.failed.connect(self._handle_worker_failure)
        self._transcribe_thread.cancelled.connect(self._handle_worker_cancelled)
        self._transcribe_thread.finished.connect(self._update_actions)
        self._transcribe_thread.start()
        self._append_status("开始语音转写。")
        self._update_actions()

    def _cancel_running_task(self) -> None:
        if self._extract_thread is not None and self._extract_thread.isRunning():
            self._extract_thread.requestInterruption()
        if self._transcribe_thread is not None and self._transcribe_thread.isRunning():
            self._transcribe_thread.requestInterruption()
        self.progress_label.setText("正在中断当前任务...")

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        total_value = max(total, 1)
        percent = int((max(current, 0) / total_value) * 100)
        self.progress_bar.setValue(percent)
        self.progress_label.setText(message)

    def _handle_extract_success(self, prepared: PreparedAudio) -> None:
        self._prepared_audio = prepared
        self.audio_value.setText(
            f"音频已分离：{prepared.audio_path} / 时长 {prepared.duration_ms / 1000:.1f}s / 大小 {prepared.size_bytes / 1024:.1f} KB / 分段 {len(prepared.chunk_paths)}"
        )
        self.progress_bar.setValue(100)
        self.progress_label.setText("音频分离完成。")
        self._append_status(f"音频输出：{prepared.audio_path}")
        self._append_status(f"分段数量：{len(prepared.chunk_paths)}")
        self._update_actions()

    def _handle_transcribe_success(self, prepared: PreparedAudio, result: AudioTranscriptionResult) -> None:
        self._prepared_audio = prepared
        self._result = result
        self.full_text_edit.setPlainText(result.text)
        self.srt_edit.setPlainText(result.srt_text)
        self.progress_bar.setValue(100)
        self.progress_label.setText("语音转写完成。")
        self._append_status(f"语音转写完成，共 {len(result.segments)} 段。")
        self._append_status(f"音频文件：{result.audio_path}")
        self._update_actions()

    def _handle_worker_failure(self, message: str) -> None:
        self.progress_label.setText("处理失败。")
        self.progress_bar.setValue(0)
        self._append_status(f"失败：{message}")
        QMessageBox.critical(self, "处理失败", message)
        self._update_actions()

    def _handle_worker_cancelled(self, message: str) -> None:
        self.progress_label.setText(message)
        self.progress_bar.setValue(0)
        self._append_status(message)
        self._update_actions()

    def _export_txt(self) -> None:
        if self._result is None:
            return
        default_name = f"{Path(self._result.source_path).stem}_transcript.txt"
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 TXT", str(TRANSCRIPT_DIR / default_name), "Text Files (*.txt)")
        if not file_path:
            return
        Path(file_path).write_text(self._result.text, encoding="utf-8")
        self._append_status(f"已导出 TXT：{file_path}")

    def _export_srt(self) -> None:
        if self._result is None:
            return
        default_name = f"{Path(self._result.source_path).stem}.srt"
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 SRT", str(TRANSCRIPT_DIR / default_name), "SubRip Files (*.srt)")
        if not file_path:
            return
        Path(file_path).write_text(self._result.srt_text, encoding="utf-8")
        self._append_status(f"已导出 SRT：{file_path}")

    def _export_json(self) -> None:
        if self._result is None:
            return
        default_name = f"{Path(self._result.source_path).stem}_transcript.json"
        file_path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", str(TRANSCRIPT_DIR / default_name), "JSON Files (*.json)")
        if not file_path:
            return
        payload = {
            "source_path": self._result.source_path,
            "audio_path": self._result.audio_path,
            "text": self._result.text,
            "srt_text": self._result.srt_text,
            "segments": [
                {
                    "text": segment.text,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "speaker_id": segment.speaker_id,
                    "words": [
                        {"text": word.text, "start_ms": word.start_ms, "end_ms": word.end_ms}
                        for word in segment.words
                    ],
                }
                for segment in self._result.segments
            ],
            "raw_tasks": self._result.raw_tasks,
        }
        Path(file_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_status(f"已导出 JSON：{file_path}")

    def _append_status(self, message: str) -> None:
        self.status_edit.appendPlainText(message)

    def _update_actions(self) -> None:
        extract_busy = self._extract_thread is not None and self._extract_thread.isRunning()
        transcribe_busy = self._transcribe_thread is not None and self._transcribe_thread.isRunning()
        busy = extract_busy or transcribe_busy
        has_source = bool(self._active_source_path)
        has_linked = bool(self._linked_media_path)
        has_result = self._result is not None and bool(self._result.text.strip())

        self.use_current_button.setEnabled(has_linked and not busy)
        self.choose_video_button.setEnabled(not busy)
        self.choose_audio_button.setEnabled(not busy)
        self.config_button.setEnabled(not busy)
        self.extract_button.setEnabled(has_source and not busy)
        self.transcribe_button.setEnabled(has_source and not busy)
        self.cancel_button.setEnabled(busy)
        self.export_txt_button.setEnabled(has_result)
        self.export_srt_button.setEnabled(has_result)
        self.export_json_button.setEnabled(has_result)
