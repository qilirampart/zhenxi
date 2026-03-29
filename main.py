from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.config.settings import APP_ICON_PATH, APP_NAME, APP_ORGANIZATION, ensure_app_directories
from app.ui.main_window import MainWindow
from app.utils.logger import configure_logging


def main() -> int:
    configure_logging()
    ensure_app_directories()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
