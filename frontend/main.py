"""Data Crawler desktop frontend — entry point.

Run from the frontend/ directory:
    .venv/bin/python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.api.client import ApiClient
from app.core.session import SessionController
from app.ui.main_window import MainWindow

_RESOURCES_DIR = Path(__file__).resolve().parent / "app" / "resources"


def _load_stylesheet(app: QApplication) -> None:
    app.setStyleSheet((_RESOURCES_DIR / "style.qss").read_text(encoding="utf-8"))


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("DataCrawler")
    app.setApplicationName("Data Crawler")
    _load_stylesheet(app)

    client = ApiClient()  # base URL overridable via DATA_CRAWLER_API_URL
    session = SessionController(client)
    window = MainWindow(session)
    window.show()
    window.raise_()
    window.activateWindow()

    session.bootstrap()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
