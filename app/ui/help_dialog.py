from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from app.config.settings import PROJECT_ROOT


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        self.setModal(True)
        self.resize(860, 760)
        self._build_ui()

    def _build_ui(self) -> None:
        title = QLabel("帧析使用说明")
        title.setProperty("role", "sectionTitle")

        subtitle = QLabel("内置文档会随软件功能一起更新，本地 OCR 仅作为兼容能力保留，不建议作为默认方案。")
        subtitle.setProperty("role", "sectionSubtitle")
        subtitle.setWordWrap(True)

        browser = QTextBrowser()
        browser.setObjectName("helpBrowser")
        browser.setOpenExternalLinks(True)
        browser.setReadOnly(True)
        browser.setMarkdown(self._load_markdown())

        close_button = QPushButton("关闭")
        close_button.setProperty("role", "secondary")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(browser, 1)
        layout.addWidget(close_button)

    def _load_markdown(self) -> str:
        doc_path = PROJECT_ROOT / "docs" / "user-guide.md"
        if not doc_path.exists():
            return "说明文档不存在，请检查 docs/user-guide.md。"
        return doc_path.read_text(encoding="utf-8")
