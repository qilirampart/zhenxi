from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.settings import (
    APP_ICON_PATH,
    APP_NAME,
    OUTPUT_DIR,
    PREVIEW_RATIO_PRESETS,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
)
from app.core.video.loader import VideoLoader, VideoLoaderError
from app.core.video.ratio import detect_aspect_ratio
from app.models.extraction import ROI
from app.models.frame import FrameInfo
from app.models.ocr import ExtractionResult
from app.models.video import VideoMeta
from app.services.extraction_service import ExtractionService
from app.services.media_download_service import (
    MediaDownloadError,
    MediaDownloadResult,
    MultiPlatformDownloadService,
)
from app.ui.api_config_dialog import APIConfigDialog
from app.ui.audio_transcribe_page import AudioTranscribePage
from app.ui.help_dialog import HelpDialog
from app.ui.mode_static_panel import ModeStaticPanel
from app.ui.no_wheel_combo_box import NoWheelComboBox
from app.ui.result_panel import ResultPanel
from app.ui.video_preview import VideoPreviewWidget


class LinkDownloadThread(QThread):
    progress_changed = Signal(int, int)
    result_ready = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(self, service: MultiPlatformDownloadService, text: str) -> None:
        super().__init__()
        self._service = service
        self._text = text

    def run(self) -> None:
        try:
            result = self._service.download_from_text(
                self._text,
                progress_callback=lambda downloaded, total: self.progress_changed.emit(downloaded, total),
                should_cancel=self.isInterruptionRequested,
            )
        except MediaDownloadError as exc:
            if self.isInterruptionRequested():
                self.cancelled.emit("下载已取消")
                return
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            if self.isInterruptionRequested():
                self.cancelled.emit("下载已取消")
                return
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(result)


class StaticExtractionThread(QThread):
    progress_changed = Signal(int, int, str)
    result_ready = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        service: ExtractionService,
        source_meta: VideoMeta,
        frames: list[tuple[FrameInfo, np.ndarray]],
        keep_screenshots: bool,
        roi: ROI | None,
        ocr_mode: str,
    ) -> None:
        super().__init__()
        self._service = service
        self._source_meta = source_meta
        self._frames = frames
        self._keep_screenshots = keep_screenshots
        self._roi = roi
        self._ocr_mode = ocr_mode

    def run(self) -> None:
        try:
            total = max(len(self._frames), 1)
            self.progress_changed.emit(0, total, "正在准备 OCR")
            self._service.static_extractor.ocr_engine.set_preferred_mode(self._ocr_mode)
            result = self._service.extract_static(
                self._source_meta,
                self._frames,
                keep_screenshots=self._keep_screenshots,
                roi=self._roi,
                progress_callback=lambda current, total, message: self.progress_changed.emit(current, total, message),
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001
            if self.isInterruptionRequested() or str(exc).strip() == "提取已取消":
                self.cancelled.emit("提取已取消")
                return
            self.failed.emit(str(exc))
            return
        self.result_ready.emit(result)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(1560, 980)

        self.video_loader = VideoLoader()
        self.extraction_service = ExtractionService()
        self.download_service = MultiPlatformDownloadService()

        self._source_kind = "none"
        self._source_label = "未导入"
        self._active_source_meta: VideoMeta | None = None
        self._current_frame_timestamp_ms = 0
        self._current_frame_image: np.ndarray | None = None
        self._candidate_frames: dict[int, tuple[FrameInfo, np.ndarray]] = {}
        self._manual_roi: ROI | None = None
        self._last_result: ExtractionResult | None = None
        self._last_article_result: dict[str, object] | None = None
        self._download_thread: LinkDownloadThread | None = None
        self._extraction_thread: StaticExtractionThread | None = None
        self._last_extraction_progress_message = ""

        self._playback_timer = QTimer(self)
        self._playback_timer.timeout.connect(self._advance_playback)

        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(1.0)
        self._audio_player = QMediaPlayer(self)
        self._audio_player.setAudioOutput(self._audio_output)

        self._build_ui()
        self._load_theme()
        self._reset_source_state()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._stop_playback(reset_audio=True)
        for thread in (self._download_thread, self._extraction_thread):
            if thread is not None and thread.isRunning():
                thread.wait(300)
        self.video_loader.close()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("appShell")
        self.setCentralWidget(shell)

        header = self._build_header_bar()
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("workspaceTabs")
        self.workspace_tabs.setDocumentMode(True)

        self.material_page = self._build_material_page()
        self.extract_page = self._build_extract_page()
        self.audio_transcribe_page = AudioTranscribePage()
        self.result_panel = ResultPanel()

        self.workspace_tabs.addTab(self.material_page, "素材")
        self.workspace_tabs.addTab(self.extract_page, "提取")
        self.workspace_tabs.addTab(self.audio_transcribe_page, "语音转写")
        self.workspace_tabs.addTab(self.result_panel, "结果")

        layout = QVBoxLayout(shell)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(header)
        layout.addWidget(self.workspace_tabs, 1)

        self.result_panel.export_txt_button.clicked.connect(self._export_result_txt)
        self.result_panel.export_json_button.clicked.connect(self._export_result_json)

    def _build_header_bar(self) -> QFrame:
        card = QFrame()
        card.setObjectName("workspaceHeader")

        title = QLabel(APP_NAME)
        title.setObjectName("workspaceTitle")

        subtitle = QLabel("把流程拆成素材、提取、结果三个工作区，避免同一页里互相挤占空间。")
        subtitle.setProperty("role", "sectionNote")
        subtitle.setWordWrap(True)

        self.status_badge = QLabel()
        self.status_badge.setObjectName("statusBadge")
        self.status_badge.setAlignment(Qt.AlignCenter)

        self.api_config_button = QPushButton("API 配置")
        self.api_config_button.setProperty("role", "secondary")
        self.api_config_button.clicked.connect(self._open_api_config_dialog)

        self.help_button = QPushButton("使用说明")
        self.help_button.setProperty("role", "secondary")
        self.help_button.clicked.connect(self._open_help_dialog)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.help_button)
        layout.addWidget(self.api_config_button)
        layout.addWidget(self.status_badge)
        return card

    def _build_material_page(self) -> QWidget:
        page = QWidget()
        action_card = QFrame()
        action_card.setObjectName("heroCard")

        eyebrow = QLabel("Workspace 1")
        eyebrow.setProperty("role", "heroEyebrow")

        title = QLabel("准备素材")
        title.setObjectName("heroTitle")

        note = QLabel("先导入本地视频或图片，或者粘贴分享文本直接下载。素材准备好后，再切到“提取”工作区处理。")
        note.setObjectName("heroSubtitle")
        note.setWordWrap(True)

        self.import_button = QPushButton("导入视频")
        self.import_button.setProperty("role", "primary")
        self.import_image_button = QPushButton("导入图片")
        self.import_image_button.setProperty("role", "ghost")
        self.download_link_button = QPushButton("粘贴下载")
        self.download_link_button.setProperty("role", "ghost")
        self.cancel_download_button = QPushButton("取消下载")
        self.cancel_download_button.setProperty("role", "danger")
        self.clear_source_button = QPushButton("清空")
        self.clear_source_button.setProperty("role", "ghost")

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.import_image_button)
        button_row.addWidget(self.download_link_button)
        button_row.addWidget(self.cancel_download_button)
        button_row.addWidget(self.clear_source_button)
        button_row.addStretch(1)

        share_label = QLabel("分享文本 / 链接")
        share_label.setProperty("role", "fieldLabel")

        self.share_text_edit = QPlainTextEdit()
        self.share_text_edit.setMaximumHeight(120)
        self.share_text_edit.setPlaceholderText(
            "粘贴完整分享文本，例如带 https:// 的抖音、快手、小红书、B 站分享内容。"
        )

        share_hint = QLabel("下载时优先读取剪贴板；剪贴板为空时读取这里的文本。")
        share_hint.setProperty("role", "sectionNote")
        share_hint.setWordWrap(True)

        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(22, 20, 22, 20)
        action_layout.setSpacing(12)
        action_layout.addWidget(eyebrow)
        action_layout.addWidget(title)
        action_layout.addWidget(note)
        action_layout.addLayout(button_row)
        action_layout.addWidget(share_label)
        action_layout.addWidget(self.share_text_edit)
        action_layout.addWidget(share_hint)

        summary_card = self._build_material_summary_card()

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(action_card)
        splitter.addWidget(summary_card)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([760, 560])

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(splitter)
        layout.addStretch(1)

        self.import_button.clicked.connect(self._choose_and_import_video)
        self.import_image_button.clicked.connect(self._choose_and_import_image)
        self.download_link_button.clicked.connect(self._start_link_download)
        self.cancel_download_button.clicked.connect(self._cancel_link_download)
        self.clear_source_button.clicked.connect(self._clear_source)
        self.share_text_edit.textChanged.connect(self._update_action_states)
        return page

    def _build_material_summary_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("overviewCard")

        title = QLabel("当前素材")
        title.setProperty("role", "sectionTitle")

        self.video_name_value = QLabel("尚未导入素材")
        self.video_name_value.setObjectName("videoNameValue")

        self.source_metric = self._build_metric_card("来源", "未导入", compact=True)
        self.duration_metric = self._build_metric_card("时长", "--", compact=True)
        self.resolution_metric = self._build_metric_card("分辨率", "--", compact=True)
        self.aspect_metric = self._build_metric_card("比例", "--", compact=True)
        self.fps_metric = self._build_metric_card("帧率", "--", compact=True)
        self.frames_metric = self._build_metric_card("总帧数", "--", compact=True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        metrics = (
            self.source_metric,
            self.duration_metric,
            self.resolution_metric,
            self.aspect_metric,
            self.fps_metric,
            self.frames_metric,
        )
        for index, metric in enumerate(metrics):
            grid.addWidget(metric["card"], index // 3, index % 3)

        go_extract_button = QPushButton("进入提取")
        go_extract_button.setProperty("role", "secondary")
        go_extract_button.clicked.connect(lambda: self.workspace_tabs.setCurrentWidget(self.extract_page))

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.video_name_value)
        layout.addLayout(grid)
        layout.addWidget(go_extract_button)
        return card

    def _build_extract_page(self) -> QWidget:
        page = QWidget()
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_preview_workspace_v3())
        splitter.addWidget(self._build_controls_card_v2())
        splitter.setStretchFactor(0, 9)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([1160, 360])

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter, 1)
        return page

    def _build_preview_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("previewCard")
        card.setMinimumHeight(680)

        title = QLabel("视频预览")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("这里是主要工作区。选帧时只保留预览、播放和逐帧控制，不再和长结果文本抢空间。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        ratio_label = QLabel("预览比例")
        ratio_label.setProperty("role", "fieldLabel")
        self.preview_ratio_combo = NoWheelComboBox()
        for ratio in PREVIEW_RATIO_PRESETS:
            self.preview_ratio_combo.addItem(ratio)

        zoom_label = QLabel("预览放大")
        zoom_label.setProperty("role", "fieldLabel")
        self.preview_zoom_combo = NoWheelComboBox()
        for label, zoom in (("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)):
            self.preview_zoom_combo.addItem(label, zoom)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addWidget(ratio_label)
        toolbar.addWidget(self.preview_ratio_combo)
        toolbar.addSpacing(8)
        toolbar.addWidget(zoom_label)
        toolbar.addWidget(self.preview_zoom_combo)
        toolbar.addStretch(1)

        self.video_preview = VideoPreviewWidget()
        self.video_preview.setMinimumHeight(620)

        self.preview_play_button = QPushButton("播放")
        self.preview_play_button.setProperty("role", "secondary")
        self.preview_prev_frame_button = QPushButton("上一帧")
        self.preview_prev_frame_button.setProperty("role", "secondary")
        self.preview_next_frame_button = QPushButton("下一帧")
        self.preview_next_frame_button.setProperty("role", "secondary")

        self.preview_frame_status = QLabel("尚未加载素材")
        self.preview_frame_status.setProperty("role", "sectionNote")
        self.preview_frame_status.setWordWrap(True)

        transport_buttons = QHBoxLayout()
        transport_buttons.setSpacing(10)
        transport_buttons.addWidget(self.preview_play_button)
        transport_buttons.addWidget(self.preview_prev_frame_button)
        transport_buttons.addWidget(self.preview_next_frame_button)
        transport_buttons.addStretch(1)

        transport = QVBoxLayout()
        transport.setSpacing(6)
        transport.addLayout(transport_buttons)
        transport.addWidget(self.preview_frame_status)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(toolbar)
        layout.addWidget(self.video_preview, 1)
        layout.addLayout(transport)

        self.preview_ratio_combo.currentTextChanged.connect(self.video_preview.set_preview_ratio)
        self.preview_zoom_combo.currentIndexChanged.connect(self._on_preview_zoom_changed)
        self.video_preview.seek_requested.connect(self._on_seek_requested)
        self.preview_play_button.clicked.connect(self._toggle_playback)
        self.preview_prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.preview_next_frame_button.clicked.connect(lambda: self._step_frame(1))
        return card

    def _build_preview_workspace(self) -> QFrame:
        card = QFrame()
        card.setObjectName("previewCard")
        card.setMinimumHeight(680)

        title = QLabel("视频预览")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("这里是主要工作区。选帧时只保留预览、播放和逐帧控制，不再和长结果文本抢空间。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)
        title_block.addWidget(title)
        title_block.addWidget(subtitle)

        ratio_label = QLabel("预览比例")
        ratio_label.setProperty("role", "fieldLabel")
        self.preview_ratio_combo = NoWheelComboBox()
        self.preview_ratio_combo.setMinimumWidth(120)
        for ratio in PREVIEW_RATIO_PRESETS:
            self.preview_ratio_combo.addItem(ratio)

        zoom_label = QLabel("预览放大")
        zoom_label.setProperty("role", "fieldLabel")
        self.preview_zoom_combo = NoWheelComboBox()
        self.preview_zoom_combo.setMinimumWidth(120)
        for label, zoom in (("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)):
            self.preview_zoom_combo.addItem(label, zoom)

        self.preview_play_button = QPushButton("播放")
        self.preview_play_button.setProperty("role", "secondary")
        self.preview_play_button.setProperty("size", "compact")
        self.preview_prev_frame_button = QPushButton("上一帧")
        self.preview_prev_frame_button.setProperty("role", "secondary")
        self.preview_prev_frame_button.setProperty("size", "compact")
        self.preview_next_frame_button = QPushButton("下一帧")
        self.preview_next_frame_button.setProperty("role", "secondary")
        self.preview_next_frame_button.setProperty("size", "compact")

        self.preview_frame_status = QLabel("尚未加载素材")
        self.preview_frame_status.setProperty("role", "sectionNote")
        self.preview_frame_status.setWordWrap(True)
        self.preview_frame_status.setMinimumWidth(360)

        control_grid = QGridLayout()
        control_grid.setHorizontalSpacing(10)
        control_grid.setVerticalSpacing(8)
        control_grid.addWidget(ratio_label, 0, 0)
        control_grid.addWidget(self.preview_ratio_combo, 0, 1)
        control_grid.addWidget(zoom_label, 0, 2)
        control_grid.addWidget(self.preview_zoom_combo, 0, 3)

        playback_row = QHBoxLayout()
        playback_row.setSpacing(8)
        playback_row.addWidget(self.preview_play_button)
        playback_row.addWidget(self.preview_prev_frame_button)
        playback_row.addWidget(self.preview_next_frame_button)
        playback_row.addStretch(1)

        controls_block = QVBoxLayout()
        controls_block.setSpacing(8)
        controls_block.addLayout(control_grid)
        controls_block.addLayout(playback_row)
        controls_block.addWidget(self.preview_frame_status)

        top_row = QHBoxLayout()
        top_row.setSpacing(24)
        top_row.addLayout(title_block, 1)
        top_row.addLayout(controls_block, 0)

        self.video_preview = VideoPreviewWidget()
        self.video_preview.setMinimumHeight(620)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)
        layout.addLayout(top_row)
        layout.addWidget(self.video_preview, 1)

        self.preview_ratio_combo.currentTextChanged.connect(self.video_preview.set_preview_ratio)
        self.preview_zoom_combo.currentIndexChanged.connect(self._on_preview_zoom_changed)
        self.video_preview.seek_requested.connect(self._on_seek_requested)
        self.preview_play_button.clicked.connect(self._toggle_playback)
        self.preview_prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.preview_next_frame_button.clicked.connect(lambda: self._step_frame(1))
        return card

    def _build_preview_workspace_v2(self) -> QFrame:
        card = QFrame()
        card.setObjectName("previewCard")
        card.setMinimumHeight(680)

        title = QLabel("视频预览")
        title.setProperty("role", "sectionTitle")

        controls_card = QFrame()
        controls_card.setObjectName("previewControlCard")
        controls_card.setMinimumWidth(400)
        controls_card.setMaximumWidth(400)

        ratio_label = QLabel("预览比例")
        ratio_label.setProperty("role", "fieldLabel")
        self.preview_ratio_combo = NoWheelComboBox()
        self.preview_ratio_combo.setMinimumWidth(128)
        for ratio in PREVIEW_RATIO_PRESETS:
            self.preview_ratio_combo.addItem(ratio)

        zoom_label = QLabel("预览放大")
        zoom_label.setProperty("role", "fieldLabel")
        self.preview_zoom_combo = NoWheelComboBox()
        self.preview_zoom_combo.setMinimumWidth(128)
        for label, zoom in (("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)):
            self.preview_zoom_combo.addItem(label, zoom)

        ratio_row = QHBoxLayout()
        ratio_row.setSpacing(10)
        ratio_row.addWidget(ratio_label)
        ratio_row.addWidget(self.preview_ratio_combo, 1)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(10)
        zoom_row.addWidget(zoom_label)
        zoom_row.addWidget(self.preview_zoom_combo, 1)

        self.preview_play_button = QPushButton("播放")
        self.preview_play_button.setProperty("role", "secondary")
        self.preview_play_button.setProperty("size", "compact")
        self.preview_prev_frame_button = QPushButton("上一帧")
        self.preview_prev_frame_button.setProperty("role", "secondary")
        self.preview_prev_frame_button.setProperty("size", "compact")
        self.preview_next_frame_button = QPushButton("下一帧")
        self.preview_next_frame_button.setProperty("role", "secondary")
        self.preview_next_frame_button.setProperty("size", "compact")

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(self.preview_play_button)
        button_row.addWidget(self.preview_prev_frame_button)
        button_row.addWidget(self.preview_next_frame_button)
        button_row.addStretch(1)

        self.preview_frame_status = QLabel("尚未加载素材")
        self.preview_frame_status.setProperty("role", "sectionNote")
        self.preview_frame_status.setWordWrap(True)

        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(18, 16, 18, 16)
        controls_layout.setSpacing(10)
        controls_layout.addLayout(ratio_row)
        controls_layout.addLayout(zoom_row)
        controls_layout.addLayout(button_row)
        controls_layout.addWidget(self.preview_frame_status)

        header_row = QHBoxLayout()
        header_row.setSpacing(20)
        header_row.addWidget(title, 1, Qt.AlignVCenter | Qt.AlignLeft)
        header_row.addWidget(controls_card, 0, Qt.AlignTop)

        self.video_preview = VideoPreviewWidget()
        self.video_preview.setMinimumHeight(620)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(header_row)
        layout.addWidget(self.video_preview, 1)

        self.preview_ratio_combo.currentTextChanged.connect(self.video_preview.set_preview_ratio)
        self.preview_zoom_combo.currentIndexChanged.connect(self._on_preview_zoom_changed)
        self.video_preview.seek_requested.connect(self._on_seek_requested)
        self.preview_play_button.clicked.connect(self._toggle_playback)
        self.preview_prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.preview_next_frame_button.clicked.connect(lambda: self._step_frame(1))
        return card

    def _build_controls_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("controlsCard")
        card.setMinimumWidth(420)

        title = QLabel("提取控制")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("参数和候选帧都放在这里，视频始终保留在左侧大预览区。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        self.mode_static_panel = ModeStaticPanel()

        self.extract_button = QPushButton("开始静态提取")
        self.extract_button.setProperty("role", "primary")
        self.cancel_extract_button = QPushButton("中断提取")
        self.cancel_extract_button.setProperty("role", "danger")
        self.open_result_button = QPushButton("查看结果")
        self.open_result_button.setProperty("role", "secondary")
        self.open_result_button.clicked.connect(lambda: self.workspace_tabs.setCurrentWidget(self.result_panel))

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.extract_button)
        actions.addWidget(self.cancel_extract_button)
        actions.addWidget(self.open_result_button)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.mode_static_panel, 1)
        layout.addLayout(actions)

        self.mode_static_panel.generate_candidates_button.clicked.connect(self._generate_candidates_clicked)
        self.mode_static_panel.add_current_frame_button.clicked.connect(self._add_current_frame_clicked)
        self.mode_static_panel.remove_selected_button.clicked.connect(self._remove_selected_frame)
        self.mode_static_panel.selected_frames_list.itemChanged.connect(self._update_action_states)
        self.mode_static_panel.selection_mode_combo.currentIndexChanged.connect(self._update_action_states)
        self.extract_button.clicked.connect(self._start_static_extraction)
        return card

    def _build_preview_workspace_v3(self) -> QFrame:
        card = QFrame()
        card.setObjectName("previewCard")
        card.setMinimumHeight(720)

        title = QLabel("视频预览")
        title.setProperty("role", "sectionTitle")

        self.video_preview = VideoPreviewWidget()
        self.video_preview.setMinimumHeight(760)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.video_preview, 1)
        return card

    def _build_controls_card_v2(self) -> QFrame:
        card = QFrame()
        card.setObjectName("controlsCard")
        card.setMinimumWidth(340)
        card.setMaximumWidth(420)

        title = QLabel("提取控制")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("预览控制与提取控制统一放在右侧，左侧只保留大预览区。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        preview_group = QGroupBox("预览控制")
        preview_group.setObjectName("settingsGroup")

        ratio_label = QLabel("预览比例")
        ratio_label.setProperty("role", "fieldLabel")
        self.preview_ratio_combo = NoWheelComboBox()
        for ratio in PREVIEW_RATIO_PRESETS:
            self.preview_ratio_combo.addItem(ratio)

        zoom_label = QLabel("预览放大")
        zoom_label.setProperty("role", "fieldLabel")
        self.preview_zoom_combo = NoWheelComboBox()
        for label, zoom in (("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0)):
            self.preview_zoom_combo.addItem(label, zoom)

        self.preview_play_button = QPushButton("播放")
        self.preview_play_button.setProperty("role", "secondary")
        self.preview_play_button.setProperty("size", "compact")
        self.preview_prev_frame_button = QPushButton("上一帧")
        self.preview_prev_frame_button.setProperty("role", "secondary")
        self.preview_prev_frame_button.setProperty("size", "compact")
        self.preview_next_frame_button = QPushButton("下一帧")
        self.preview_next_frame_button.setProperty("role", "secondary")
        self.preview_next_frame_button.setProperty("size", "compact")

        self.preview_frame_status = QLabel("尚未加载素材")
        self.preview_frame_status.setProperty("role", "sectionNote")
        self.preview_frame_status.setWordWrap(True)

        preview_layout = QGridLayout(preview_group)
        preview_layout.setHorizontalSpacing(10)
        preview_layout.setVerticalSpacing(10)
        preview_layout.addWidget(ratio_label, 0, 0)
        preview_layout.addWidget(self.preview_ratio_combo, 0, 1)
        preview_layout.addWidget(zoom_label, 1, 0)
        preview_layout.addWidget(self.preview_zoom_combo, 1, 1)

        preview_buttons = QHBoxLayout()
        preview_buttons.setSpacing(8)
        preview_buttons.addWidget(self.preview_play_button)
        preview_buttons.addWidget(self.preview_prev_frame_button)
        preview_buttons.addWidget(self.preview_next_frame_button)
        preview_layout.addLayout(preview_buttons, 2, 0, 1, 2)
        preview_layout.addWidget(self.preview_frame_status, 3, 0, 1, 2)

        self.mode_static_panel = ModeStaticPanel()

        self.extract_button = QPushButton("开始静态提取")
        self.extract_button.setProperty("role", "primary")
        self.cancel_extract_button = QPushButton("中断提取")
        self.cancel_extract_button.setProperty("role", "danger")
        self.open_result_button = QPushButton("查看结果")
        self.open_result_button.setProperty("role", "secondary")
        self.open_result_button.clicked.connect(lambda: self.workspace_tabs.setCurrentWidget(self.result_panel))

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)
        scroll_layout.addWidget(preview_group)
        scroll_layout.addWidget(self.mode_static_panel)
        scroll_layout.addStretch(1)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("controlsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setWidget(scroll_content)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.extract_button)
        actions.addWidget(self.cancel_extract_button)
        actions.addWidget(self.open_result_button)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(scroll_area, 1)
        layout.addLayout(actions)

        self.preview_ratio_combo.currentTextChanged.connect(self.video_preview.set_preview_ratio)
        self.preview_zoom_combo.currentIndexChanged.connect(self._on_preview_zoom_changed)
        self.video_preview.seek_requested.connect(self._on_seek_requested)
        self.video_preview.roi_changed.connect(self._on_manual_roi_changed)
        self.preview_play_button.clicked.connect(self._toggle_playback)
        self.preview_prev_frame_button.clicked.connect(lambda: self._step_frame(-1))
        self.preview_next_frame_button.clicked.connect(lambda: self._step_frame(1))
        self.mode_static_panel.generate_candidates_button.clicked.connect(self._generate_candidates_clicked)
        self.mode_static_panel.add_current_frame_button.clicked.connect(self._add_current_frame_clicked)
        self.mode_static_panel.remove_selected_button.clicked.connect(self._remove_selected_frame)
        self.mode_static_panel.enable_roi_checkbox.toggled.connect(self._on_roi_toggle_changed)
        self.mode_static_panel.selected_frames_list.itemChanged.connect(self._update_action_states)
        self.mode_static_panel.selection_mode_combo.currentIndexChanged.connect(self._update_action_states)
        self.extract_button.clicked.connect(self._start_static_extraction)
        self.cancel_extract_button.clicked.connect(self._cancel_static_extraction)
        return card

    def _build_metric_card(self, caption: str, value: str, *, compact: bool) -> dict[str, QWidget]:
        card = QFrame()
        card.setObjectName("metricCard")
        if compact:
            card.setProperty("density", "compact")

        caption_label = QLabel(caption)
        caption_label.setProperty("role", "metricCaption")
        value_label = QLabel(value)
        value_label.setProperty("role", "metricValue")

        layout = QVBoxLayout(card)
        if compact:
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(4)
        else:
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(6)
        layout.addWidget(caption_label)
        layout.addWidget(value_label)
        return {"card": card, "value": value_label}

    def _load_theme(self) -> None:
        theme_path = Path(__file__).with_name("theme.qss")
        if theme_path.exists():
            self.setStyleSheet(theme_path.read_text(encoding="utf-8"))

    def _set_status(self, text: str, tone: str = "idle") -> None:
        self.status_badge.setText(text)
        self.status_badge.setProperty("tone", tone)
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        self.status_badge.update()

    def _open_api_config_dialog(self) -> None:
        dialog = APIConfigDialog(self)
        if dialog.exec() != dialog.Accepted:
            return

        saved_config = dialog.saved_config or {}
        providers = saved_config.get("providers") or []
        active_provider_id = str(saved_config.get("active_provider_id") or "")
        active_provider = next(
            (provider for provider in providers if str(provider.get("id")) == active_provider_id),
            providers[0] if providers else {},
        )
        model = str(active_provider.get("model", "")).strip()
        base_url = str(active_provider.get("base_url", "")).strip()
        provider_name = str(active_provider.get("name", "")).strip()
        self.result_panel.append_status("API 配置已更新。")
        if provider_name:
            self.result_panel.append_status(f"首选通道：{provider_name}")
        if providers:
            self.result_panel.append_status(f"已配置通道数：{len(providers)}")
        if base_url:
            self.result_panel.append_status(f"Base URL：{base_url}")
        if model:
            self.result_panel.append_status(f"模型：{model}")
        self._set_status("API 配置已更新", "accent")

    def _open_help_dialog(self) -> None:
        HelpDialog(self).exec()

    def _reset_source_state(self) -> None:
        self._stop_playback(reset_audio=True)
        self._source_kind = "none"
        self._source_label = "未导入"
        self._active_source_meta = None
        self._current_frame_timestamp_ms = 0
        self._current_frame_image = None
        self._candidate_frames.clear()
        self._manual_roi = None
        self._last_result = None
        self._last_article_result = None
        self._last_extraction_progress_message = ""
        self.audio_transcribe_page.clear_linked_media(clear_active=True)

        self.video_name_value.setText("尚未导入素材")
        for metric in (
            self.source_metric,
            self.duration_metric,
            self.resolution_metric,
            self.aspect_metric,
            self.fps_metric,
            self.frames_metric,
        ):
            metric["value"].setText("--")
        self.source_metric["value"].setText("未导入")

        self.video_preview.clear_preview()
        self.video_preview.set_roi_enabled(False)
        self.video_preview.set_zoom_factor(float(self.preview_zoom_combo.currentData() or 1.0))
        self.mode_static_panel.clear_frame_list()
        self.mode_static_panel.set_source_capabilities(
            False,
            can_generate_candidates=False,
            can_add_current_frame=False,
        )
        self.preview_frame_status.setText("尚未加载素材")
        self.result_panel.clear_all()
        self.workspace_tabs.setCurrentWidget(self.material_page)
        self._set_status("等待素材", "muted")
        self._update_action_states()

    def _clear_source(self) -> None:
        if self._download_thread is not None or self._extraction_thread is not None:
            return
        self.video_loader.close()
        self._audio_player.setSource(QUrl())
        self._reset_source_state()

    def _choose_and_import_video(self) -> None:
        filters = "视频文件 (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_VIDEO_EXTENSIONS)) + ")"
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            filters,
        )
        if file_path:
            self._import_video_file(file_path, "本地导入")

    def _choose_and_import_image(self) -> None:
        filters = "图片文件 (" + " ".join(f"*{ext}" for ext in sorted(SUPPORTED_IMAGE_EXTENSIONS)) + ")"
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择图片文件",
            "",
            filters,
        )
        if file_path:
            self._import_image_file(file_path, "图片导入")

    def _import_video_file(self, file_path: str, source_hint: str) -> None:
        try:
            meta = self.video_loader.open(file_path)
            first_frame = self.video_loader.read_frame_at_ms(0)
        except (VideoLoaderError, Exception) as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        self._stop_playback(reset_audio=True)
        self._source_kind = "video"
        self._source_label = source_hint
        self._active_source_meta = meta
        self._load_audio_source(file_path)
        self._candidate_frames.clear()
        self._manual_roi = None
        self.video_preview.clear_roi()
        self.video_preview.set_roi_enabled(self.mode_static_panel.enable_roi_checkbox.isChecked())
        self.mode_static_panel.clear_frame_list()
        self.result_panel.clear_all()
        self._last_result = None
        self._last_article_result = None

        self.video_name_value.setText(meta.filename)
        self.source_metric["value"].setText(source_hint)
        self.duration_metric["value"].setText(self._format_duration(meta.duration_ms))
        self.resolution_metric["value"].setText(meta.resolution_text)
        self.aspect_metric["value"].setText(meta.aspect_ratio)
        self.fps_metric["value"].setText(f"{meta.fps:.2f}")
        self.frames_metric["value"].setText(str(meta.frame_count))

        self.video_preview.set_duration(meta.duration_ms)
        self._display_frame(0, first_frame)
        self.mode_static_panel.set_source_capabilities(
            True,
            can_generate_candidates=True,
            can_add_current_frame=True,
        )
        self.result_panel.append_status(f"已导入视频：{meta.filename}")
        self.result_panel.append_status(f"素材来源：{source_hint}")
        self.result_panel.append_status(f"文件位置：{Path(file_path).resolve()}")
        self.audio_transcribe_page.set_linked_media(file_path, source_hint)
        self.workspace_tabs.setCurrentWidget(self.extract_page)
        self._set_status("素材已就绪", "success")
        self._update_action_states()

    def _import_image_file(self, file_path: str, source_hint: str) -> None:
        try:
            image = self._read_image_file(file_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))
            return

        height, width = image.shape[:2]
        meta = VideoMeta(
            path=file_path,
            filename=Path(file_path).name,
            duration_ms=0,
            fps=0.0,
            width=width,
            height=height,
            aspect_ratio=detect_aspect_ratio(width, height),
            frame_count=1,
        )

        self._stop_playback(reset_audio=True)
        self.video_loader.close()
        self._audio_player.setSource(QUrl())
        self._source_kind = "image"
        self._source_label = source_hint
        self._active_source_meta = meta
        self._candidate_frames.clear()
        self._manual_roi = None
        self.video_preview.clear_roi()
        self.video_preview.set_roi_enabled(self.mode_static_panel.enable_roi_checkbox.isChecked())
        self.mode_static_panel.clear_frame_list()
        self.result_panel.clear_all()
        self._last_result = None
        self._last_article_result = None

        self.video_name_value.setText(meta.filename)
        self.source_metric["value"].setText(source_hint)
        self.duration_metric["value"].setText("单帧")
        self.resolution_metric["value"].setText(meta.resolution_text)
        self.aspect_metric["value"].setText(meta.aspect_ratio)
        self.fps_metric["value"].setText("--")
        self.frames_metric["value"].setText("1")

        self.video_preview.set_duration(0)
        self._display_frame(0, image)
        self.mode_static_panel.set_source_capabilities(
            True,
            can_generate_candidates=False,
            can_add_current_frame=False,
        )

        frame_info = FrameInfo(index=0, timestamp_ms=0, selected=True)
        self._candidate_frames[0] = (frame_info, image.copy())
        self.mode_static_panel.add_frame_item("图片帧 · 00:00", 0, selected=True)
        self.result_panel.append_status(f"已导入图片：{meta.filename}")
        self.result_panel.append_status("图片素材已自动生成一张默认候选帧。")
        self.audio_transcribe_page.clear_linked_media(clear_active=True)
        self.result_panel.append_status(f"文件位置：{Path(file_path).resolve()}")
        self.workspace_tabs.setCurrentWidget(self.extract_page)
        self._set_status("图片已就绪", "success")
        self._update_action_states()

    def _display_frame(self, timestamp_ms: int, frame: np.ndarray) -> None:
        self._current_frame_timestamp_ms = max(0, int(timestamp_ms))
        self._current_frame_image = frame.copy()
        self.video_preview.display_frame(frame)
        self.video_preview.set_position(self._current_frame_timestamp_ms)
        self._refresh_preview_status()

    def _on_roi_toggle_changed(self, checked: bool) -> None:
        self.video_preview.set_roi_enabled(checked)
        if not checked:
            self._manual_roi = None
            self.video_preview.clear_roi()
            self.result_panel.append_status("已关闭手动框选 OCR 区域。")
            self._refresh_preview_status()
            return

        self.result_panel.append_status("已启用手动框选 OCR 区域，请在左侧预览画面上按住鼠标左键拖拽选择。")
        self._set_status("请拖拽选择 OCR 区域", "accent")
        self._refresh_preview_status()

    def _on_manual_roi_changed(self, roi_payload) -> None:
        if roi_payload is None:
            self._manual_roi = None
            self._refresh_preview_status()
            return

        x, y, width, height = roi_payload
        self._manual_roi = ROI(x=int(x), y=int(y), width=int(width), height=int(height), source="manual")
        self.result_panel.append_status(
            f"手动 OCR 区域已更新：x={self._manual_roi.x}, y={self._manual_roi.y}, "
            f"w={self._manual_roi.width}, h={self._manual_roi.height}"
        )
        self._set_status("手动 OCR 区域已设置", "success")
        self._refresh_preview_status()

    def _refresh_preview_status(self) -> None:
        if self._active_source_meta is None:
            self.preview_frame_status.setText("尚未加载素材")
            return

        label = self._frame_label(self._current_frame_timestamp_ms)
        details = self._build_frame_info()
        state_text = "播放中" if self._playback_timer.isActive() else "已暂停"
        roi_text = ""
        if self.mode_static_panel.enable_roi_checkbox.isChecked():
            if self._manual_roi is not None:
                roi_text = (
                    f"  ·  OCR 区域 {self._manual_roi.width}x{self._manual_roi.height}"
                    f" @ ({self._manual_roi.x}, {self._manual_roi.y})"
                )
            else:
                roi_text = "  ·  请拖拽设置 OCR 区域"
        self.preview_frame_status.setText(f"{label}  ·  {details}  ·  {state_text}{roi_text}")

    def _on_preview_zoom_changed(self) -> None:
        zoom_factor = float(self.preview_zoom_combo.currentData() or 1.0)
        self.video_preview.set_zoom_factor(zoom_factor)
        self._refresh_preview_status()

    def _on_seek_requested(self, timestamp_ms: int) -> None:
        if self._source_kind != "video":
            return
        self._seek_to_timestamp(timestamp_ms, sync_audio=True)

    def _toggle_playback(self) -> None:
        if self._source_kind != "video" or self._active_source_meta is None:
            return

        if self._playback_timer.isActive():
            self._stop_playback(reset_audio=False)
            self._set_status("预览已暂停", "muted")
            self._refresh_preview_status()
            self._update_action_states()
            return

        self._playback_timer.start(self._frame_step_ms())
        if self._audio_player.source().isValid():
            self._audio_player.setPosition(self._current_frame_timestamp_ms)
            self._audio_player.play()
        self.preview_play_button.setText("暂停")
        self._set_status("正在播放预览", "accent")
        self._refresh_preview_status()
        self._update_action_states()

    def _stop_playback(self, reset_audio: bool = False) -> None:
        self._playback_timer.stop()
        self.preview_play_button.setText("播放")
        if reset_audio:
            self._audio_player.stop()
            self._audio_player.setPosition(0)
        else:
            self._audio_player.pause()

    def _advance_playback(self) -> None:
        if self._source_kind != "video" or self._active_source_meta is None:
            self._stop_playback(reset_audio=False)
            return

        if self._audio_player.playbackState() == QMediaPlayer.PlayingState:
            next_timestamp = self._audio_player.position()
        else:
            next_timestamp = self._current_frame_timestamp_ms + self._frame_step_ms()

        if next_timestamp >= self._active_source_meta.duration_ms:
            last_timestamp = max(0, self._active_source_meta.duration_ms - 1)
            self._seek_to_timestamp(last_timestamp, sync_audio=False)
            self._stop_playback(reset_audio=False)
            self._set_status("预览播放结束", "muted")
            self._refresh_preview_status()
            self._update_action_states()
            return

        self._seek_to_timestamp(next_timestamp, sync_audio=False)

    def _step_frame(self, direction: int) -> None:
        if self._source_kind != "video" or self._active_source_meta is None:
            return
        self._stop_playback(reset_audio=False)
        next_timestamp = self._current_frame_timestamp_ms + (self._frame_step_ms() * direction)
        self._seek_to_timestamp(next_timestamp, sync_audio=True)
        self._set_status("已切换帧", "accent")
        self._update_action_states()

    def _seek_to_timestamp(self, timestamp_ms: int, *, sync_audio: bool) -> None:
        if self._source_kind != "video" or self._active_source_meta is None:
            return

        safe_timestamp = min(max(0, int(timestamp_ms)), max(self._active_source_meta.duration_ms - 1, 0))
        try:
            frame = self.video_loader.read_frame_at_ms(safe_timestamp)
        except VideoLoaderError as exc:
            self._set_status("读取视频帧失败", "danger")
            self.result_panel.append_status(f"读取视频帧失败：{exc}")
            return

        self._display_frame(safe_timestamp, frame)
        if sync_audio and self._audio_player.source().isValid():
            self._audio_player.setPosition(safe_timestamp)

    def _frame_step_ms(self) -> int:
        if self._active_source_meta is None or self._active_source_meta.fps <= 0:
            return 40
        return max(1, int(round(1000.0 / self._active_source_meta.fps)))

    def _frame_index_from_timestamp(self, timestamp_ms: int) -> int:
        if self._active_source_meta is None:
            return 0
        if self._active_source_meta.fps <= 0 or self._active_source_meta.frame_count <= 1:
            return 0
        frame_index = int(round((max(0, timestamp_ms) / 1000.0) * self._active_source_meta.fps))
        return min(max(frame_index, 0), self._active_source_meta.frame_count - 1)

    def _generate_candidates_clicked(self) -> None:
        if self._source_kind != "video":
            return
        self._generate_candidates()

    def _generate_candidates(self) -> bool:
        if self._source_kind != "video" or self._active_source_meta is None:
            return False

        try:
            candidate_count = max(1, int(self.mode_static_panel.candidate_count_label.text().strip()))
        except ValueError:
            candidate_count = 5

        self._stop_playback(reset_audio=False)
        self._candidate_frames.clear()
        self.mode_static_panel.clear_frame_list()
        self.result_panel.append_status(f"开始生成候选帧，目标数量：{candidate_count}")

        candidates = self.extraction_service.generate_static_candidates(
            self.video_loader,
            max_candidates=candidate_count,
        )
        if not candidates:
            self._set_status("未生成候选帧", "warning")
            self.result_panel.append_status("没有生成可用候选帧。")
            self._update_action_states()
            return False

        for candidate in candidates:
            frame_key = int(candidate.frame.timestamp_ms)
            frame_info = FrameInfo(
                index=self._frame_index_from_timestamp(candidate.frame.timestamp_ms),
                timestamp_ms=candidate.frame.timestamp_ms,
                selected=True,
                score=candidate.frame.score,
            )
            self._candidate_frames[frame_key] = (frame_info, candidate.image.copy())
            self.mode_static_panel.add_frame_item(
                self._frame_label(candidate.frame.timestamp_ms),
                frame_key,
                selected=True,
            )

        self.mode_static_panel.selected_frames_list.setCurrentRow(0)
        self._set_status(f"已生成 {len(candidates)} 张候选帧", "success")
        self.result_panel.append_status(f"候选帧生成完成，共 {len(candidates)} 张。")
        self._update_action_states()
        return True

    def _add_current_frame_clicked(self) -> None:
        if self._active_source_meta is None or self._current_frame_image is None:
            return

        frame_key = 0 if self._source_kind == "image" else int(self._current_frame_timestamp_ms)
        frame_info = FrameInfo(
            index=self._frame_index_from_timestamp(self._current_frame_timestamp_ms),
            timestamp_ms=self._current_frame_timestamp_ms,
            selected=True,
        )
        self._candidate_frames[frame_key] = (frame_info, self._current_frame_image.copy())
        self.mode_static_panel.add_frame_item(
            self._frame_label(self._current_frame_timestamp_ms),
            frame_key,
            selected=True,
        )
        self.result_panel.append_status(f"已添加候选帧：{self._frame_timestamp_text(self._current_frame_timestamp_ms)}")
        self._set_status("当前帧已加入候选区", "success")
        self._update_action_states()

    def _remove_selected_frame(self) -> None:
        frame_key = self.mode_static_panel.remove_selected_item()
        if frame_key is None:
            return
        self._candidate_frames.pop(frame_key, None)
        self.result_panel.append_status(f"已移除候选帧：{self._frame_timestamp_text(frame_key)}")
        self._set_status("已移除候选帧", "muted")
        self._update_action_states()

    def _cancel_link_download(self) -> None:
        if self._download_thread is None:
            return
        self._download_thread.requestInterruption()
        self._set_status("正在取消下载", "warning")
        self.result_panel.append_status("已请求取消下载，正在等待任务停止。")
        self._update_action_states()

    def _start_link_download(self) -> None:
        if self._download_thread is not None:
            return

        clipboard_text = QApplication.clipboard().text().strip()
        share_text = clipboard_text or self.share_text_edit.toPlainText().strip()
        if not share_text:
            QMessageBox.information(self, "缺少链接", "请先粘贴分享文本，或先把分享内容复制到剪贴板。")
            return

        self._download_thread = LinkDownloadThread(self.download_service, share_text)
        self._download_thread.progress_changed.connect(self._on_download_progress)
        self._download_thread.result_ready.connect(self._on_download_success)
        self._download_thread.cancelled.connect(self._on_download_cancelled)
        self._download_thread.failed.connect(self._on_download_failed)
        self._download_thread.finished.connect(self._on_download_thread_finished)
        self._download_thread.start()

        self._set_status("正在解析并下载素材", "working")
        self.result_panel.append_status("开始解析分享链接并下载视频。")
        self._update_action_states()

    def _on_download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            percent = int(downloaded * 100 / total)
            self._set_status(f"下载中 {percent}%", "working")
            return
        self._set_status(f"下载中 {downloaded / (1024 * 1024):.1f} MB", "working")

    def _on_download_success(self, result: MediaDownloadResult) -> None:
        title_text = result.title or Path(result.local_path).name
        self.result_panel.append_status(f"下载完成：{result.platform} / {title_text}")
        if result.author:
            self.result_panel.append_status(f"作者：{result.author}")
        self.result_panel.append_status(f"文件位置：{Path(result.local_path).resolve()}")
        if result.kind == "article":
            self._present_article_result(result)
            return
        self._import_video_file(result.local_path, f"{result.platform} 链接下载")

    def _on_download_cancelled(self, message: str) -> None:
        self._set_status("下载已取消", "warning")
        self.result_panel.append_status(message)

    def _on_download_failed(self, message: str) -> None:
        self._set_status("下载失败", "danger")
        self.result_panel.append_status(f"下载失败：{message}")
        QMessageBox.critical(self, "下载失败", message)

    def _on_download_thread_finished(self) -> None:
        if self._download_thread is not None:
            self._download_thread.deleteLater()
        self._download_thread = None
        self._update_action_states()

    def _start_static_extraction(self) -> None:
        if self._extraction_thread is not None or self._active_source_meta is None:
            return

        selected_mode = self.mode_static_panel.selected_mode()
        if self._source_kind == "video" and selected_mode in {"auto", "hybrid"}:
            if not self.mode_static_panel.checked_frame_keys() and not self._generate_candidates():
                QMessageBox.warning(self, "无法提取", "没有生成可用候选帧，请先检查视频素材。")
                return

        frames = self._collect_frames_for_extraction()
        if not frames:
            QMessageBox.warning(self, "缺少候选帧", "请至少保留一张候选帧后再开始提取。")
            return

        active_roi = None
        if self.mode_static_panel.enable_roi_checkbox.isChecked():
            active_roi = self._manual_roi
            if active_roi is None:
                QMessageBox.warning(self, "缺少 OCR 区域", "已启用手动框选 OCR 区域，请先在左侧预览中拖拽选区。")
                return

        self._stop_playback(reset_audio=False)
        self.result_panel.append_status(f"开始静态提取，候选帧数量：{len(frames)}")
        if active_roi is not None:
            self.result_panel.append_status(
                f"使用手动 OCR 区域：x={active_roi.x}, y={active_roi.y}, w={active_roi.width}, h={active_roi.height}"
            )
        self._last_extraction_progress_message = ""

        self._extraction_thread = StaticExtractionThread(
            self.extraction_service,
            self._active_source_meta,
            frames,
            keep_screenshots=self.mode_static_panel.keep_screenshots_checkbox.isChecked(),
            roi=active_roi,
            ocr_mode=self.mode_static_panel.selected_ocr_mode(),
        )
        self._extraction_thread.progress_changed.connect(self._on_extraction_progress)
        self._extraction_thread.result_ready.connect(self._on_extraction_success)
        self._extraction_thread.cancelled.connect(self._on_extraction_cancelled)
        self._extraction_thread.failed.connect(self._on_extraction_failed)
        self._extraction_thread.finished.connect(self._on_extraction_thread_finished)
        self._extraction_thread.start()

        self._set_status("正在执行 OCR 提取", "working")
        self._update_action_states()

    def _cancel_static_extraction(self) -> None:
        if self._extraction_thread is None:
            return
        self._extraction_thread.requestInterruption()
        self._set_status("正在中断提取", "warning")
        self.result_panel.append_status("已请求中断提取，正在等待当前识别步骤结束。")
        self._update_action_states()

    def _collect_frames_for_extraction(self) -> list[tuple[FrameInfo, np.ndarray]]:
        checked_keys = self.mode_static_panel.checked_frame_keys()
        if not checked_keys and len(self._candidate_frames) == 1:
            checked_keys = list(self._candidate_frames.keys())

        frames: list[tuple[FrameInfo, np.ndarray]] = []
        for frame_key in sorted(checked_keys):
            frame_payload = self._candidate_frames.get(frame_key)
            if frame_payload is None:
                continue
            frame_info, image = frame_payload
            frames.append((frame_info, image.copy()))
        return frames

    def _on_extraction_progress(self, current: int, total: int, message: str) -> None:
        safe_total = max(total, 1)
        percent = int(min(max(current, 0), safe_total) * 100 / safe_total)
        self._set_status(f"{message} {percent}%", "working")
        if message != self._last_extraction_progress_message:
            self.result_panel.append_status(message)
            self._last_extraction_progress_message = message

    def _on_extraction_success(self, result: ExtractionResult) -> None:
        self._last_result = result
        self._last_article_result = None
        self.result_panel.set_full_text(result.merged_text)
        self.result_panel.set_segmented_text(self.extraction_service.format_segmented_result(result))
        self.result_panel.append_status(f"提取完成，共识别 {len(result.segmented_texts)} 张候选帧。")
        if result.screenshot_dir:
            self.result_panel.append_status(f"截图目录：{result.screenshot_dir}")
        self.workspace_tabs.setCurrentWidget(self.result_panel)
        self._set_status("提取完成", "success")
        self._update_action_states()

    def _on_extraction_failed(self, message: str) -> None:
        self._set_status("提取失败", "danger")
        self.result_panel.append_status(f"提取失败：{message}")
        QMessageBox.critical(self, "提取失败", message)

    def _on_extraction_cancelled(self, message: str) -> None:
        self._set_status("提取已取消", "warning")
        self.result_panel.append_status(message)

    def _on_extraction_thread_finished(self) -> None:
        if self._extraction_thread is not None:
            self._extraction_thread.deleteLater()
        self._extraction_thread = None
        self._last_extraction_progress_message = ""
        self._update_action_states()

    def _export_result_txt(self) -> None:
        if self._last_result is None and self._last_article_result is None:
            return

        if self._last_result is not None:
            default_name = f"{Path(self._last_result.video.filename).stem}_result.txt"
            export_text = self._last_result.merged_text
        else:
            default_name = f"{self._article_export_stem()}_article.txt"
            export_text = self.result_panel.full_text_edit.toPlainText().strip()
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出 TXT",
            str(OUTPUT_DIR / default_name),
            "Text Files (*.txt)",
        )
        if not file_path:
            return

        Path(file_path).write_text(export_text, encoding="utf-8")
        self.result_panel.append_status(f"已导出 TXT：{file_path}")

    def _export_result_json(self) -> None:
        if self._last_result is None and self._last_article_result is None:
            return

        if self._last_result is not None:
            default_name = f"{Path(self._last_result.video.filename).stem}_result.json"
        else:
            default_name = f"{self._article_export_stem()}_article.json"
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出 JSON",
            str(OUTPUT_DIR / default_name),
            "JSON Files (*.json)",
        )
        if not file_path:
            return

        if self._last_result is not None:
            payload = {
                "mode": self._last_result.mode,
                "video": {
                    "path": self._last_result.video.path,
                    "filename": self._last_result.video.filename,
                    "duration_ms": self._last_result.video.duration_ms,
                    "fps": self._last_result.video.fps,
                    "width": self._last_result.video.width,
                    "height": self._last_result.video.height,
                    "aspect_ratio": self._last_result.video.aspect_ratio,
                    "frame_count": self._last_result.video.frame_count,
                },
                "merged_text": self._last_result.merged_text,
                "screenshot_dir": self._last_result.screenshot_dir,
                "segmented_texts": [
                    {
                        "frame": {
                            "index": entry.frame.index,
                            "timestamp_ms": entry.frame.timestamp_ms,
                            "image_path": entry.frame.image_path,
                            "selected": entry.frame.selected,
                            "score": entry.frame.score,
                        },
                        "raw_text": entry.raw_text,
                        "cleaned_text": entry.cleaned_text,
                        "lines": [
                            {
                                "text": line.text,
                                "confidence": line.confidence,
                                "box": line.box,
                            }
                            for line in entry.lines
                        ],
                    }
                    for entry in self._last_result.segmented_texts
                ],
            }
        else:
            payload = dict(self._last_article_result or {})
        Path(file_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.result_panel.append_status(f"已导出 JSON：{file_path}")

    def _present_article_result(self, result: MediaDownloadResult) -> None:
        self._reset_source_state()

        payload = {
            "kind": result.kind,
            "platform": result.platform,
            "title": result.title,
            "author": result.author,
            "share_url": result.share_url,
            "resolved_url": result.resolved_url,
            "local_path": result.local_path,
            "article_text": result.article_text or "",
            "image_paths": list(result.image_paths),
        }
        self._last_article_result = payload

        title_text = (result.title or "未命名文章").strip()
        author_text = (result.author or "未知作者").strip()
        article_text = (result.article_text or "").strip()
        image_paths = list(result.image_paths)

        full_sections = [
            f"标题：{title_text}",
            f"作者：{author_text}",
            f"来源平台：{result.platform}",
            f"原始链接：{result.share_url}",
            f"解析链接：{result.resolved_url}",
            "",
            "正文：",
            article_text or "未提取到正文文本。",
        ]
        segmented_lines = [
            f"文章目录：{result.local_path}",
            f"图片数量：{len(image_paths)}",
            "",
            "图片列表：",
        ]
        if image_paths:
            segmented_lines.extend(f"{index}. {path}" for index, path in enumerate(image_paths, start=1))
        else:
            segmented_lines.append("未提取到图片。")

        self.result_panel.set_full_text("\n".join(full_sections).strip())
        self.result_panel.set_segmented_text("\n".join(segmented_lines).strip())
        self.result_panel.append_status(f"文章解析完成：{title_text}")
        self.result_panel.append_status(f"保存目录：{result.local_path}")
        self.result_panel.append_status(f"图片下载数量：{len(image_paths)}")
        self.result_panel.tabs.setCurrentWidget(self.result_panel.full_text_edit)
        self.workspace_tabs.setCurrentWidget(self.result_panel)
        self._set_status("文章解析完成", "success")
        self._update_action_states()

    def _article_export_stem(self) -> str:
        if self._last_article_result is None:
            return "article_result"

        title = str(self._last_article_result.get("title") or "").strip()
        if title:
            return Path(title).stem
        local_path = str(self._last_article_result.get("local_path") or "").strip()
        if local_path:
            return Path(local_path).stem
        return "article_result"

    def _build_frame_info(self) -> str:
        if self._active_source_meta is None:
            return "--"
        if self._source_kind == "image":
            return f"单帧图片  ·  {self._active_source_meta.resolution_text}"
        frame_index = self._frame_index_from_timestamp(self._current_frame_timestamp_ms)
        return (
            f"第 {frame_index + 1}/{max(self._active_source_meta.frame_count, 1)} 帧"
            f"  ·  {self._active_source_meta.resolution_text}"
        )

    def _load_audio_source(self, file_path: str) -> None:
        self._audio_player.stop()
        self._audio_player.setSource(QUrl.fromLocalFile(file_path))
        self._audio_player.setPosition(0)

    def _read_image_file(self, file_path: str) -> np.ndarray:
        image_bytes = np.fromfile(file_path, dtype=np.uint8)
        if image_bytes.size == 0:
            raise RuntimeError("图片文件为空，无法读取。")
        image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("图片解码失败，请检查文件格式是否受支持。")
        return image

    def _frame_label(self, timestamp_ms: int) -> str:
        if self._source_kind == "image":
            return "图片帧 · 00:00"
        frame_index = self._frame_index_from_timestamp(timestamp_ms) + 1
        return f"帧 {frame_index} · {self._frame_timestamp_text(timestamp_ms)}"

    def _frame_timestamp_text(self, timestamp_ms: int) -> str:
        safe_ms = max(0, int(timestamp_ms))
        total_seconds, milliseconds = divmod(safe_ms, 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

    def _format_duration(self, duration_ms: int) -> str:
        total_seconds = max(0, int(duration_ms) // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _update_action_states(self) -> None:
        has_preview_source = self._active_source_meta is not None
        has_article_result = self._last_article_result is not None
        has_source = has_preview_source or has_article_result
        has_video = self._source_kind == "video" and has_preview_source
        has_result = self._last_result is not None and bool(self._last_result.merged_text.strip())
        has_checked_frames = bool(self.mode_static_panel.checked_frame_keys())
        download_busy = self._download_thread is not None
        extraction_busy = self._extraction_thread is not None
        busy = download_busy or extraction_busy

        self.import_button.setEnabled(not busy)
        self.import_image_button.setEnabled(not busy)
        self.download_link_button.setEnabled(not busy)
        self.cancel_download_button.setEnabled(download_busy)
        self.cancel_extract_button.setEnabled(extraction_busy)
        self.clear_source_button.setEnabled(has_source and not busy)
        self.share_text_edit.setEnabled(not download_busy)

        self.preview_ratio_combo.setEnabled(has_preview_source)
        self.preview_zoom_combo.setEnabled(has_preview_source)
        self.preview_play_button.setEnabled(has_video and not busy)
        self.preview_prev_frame_button.setEnabled(has_video and not busy)
        self.preview_next_frame_button.setEnabled(has_video and not busy)

        can_extract = False
        if has_preview_source and not busy:
            if self._source_kind == "image":
                can_extract = bool(self._candidate_frames)
            elif self.mode_static_panel.selected_mode() == "manual":
                can_extract = has_checked_frames
            else:
                can_extract = has_checked_frames or has_video

        self.extract_button.setEnabled(can_extract)
        self.open_result_button.setEnabled(True)
