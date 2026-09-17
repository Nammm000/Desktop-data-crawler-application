"""Top-level window: Loading / Login / Main pages driven by the session state."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.session import SessionController
from app.ui.login_page import LoginPage
from app.ui.main_page import MainPage


class _LoadingPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadingRoot")
        title = QLabel("Data Crawler", objectName="appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spinner = QProgressBar()
        spinner.setRange(0, 0)  # indeterminate
        spinner.setTextVisible(False)
        spinner.setFixedWidth(220)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(spinner)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(16)
        layout.addLayout(row)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data Crawler")
        self.resize(1000, 640)
        self.setMinimumSize(820, 560)

        self._session = session
        self._login_page = LoginPage()
        self._main_page = MainPage(session)

        self._stack = QStackedWidget()
        self._stack.addWidget(_LoadingPage())  # index 0
        self._stack.addWidget(self._login_page)  # index 1
        self._stack.addWidget(self._main_page)  # index 2
        self.setCentralWidget(self._stack)

        self._login_page.login_requested.connect(
            lambda email, password: self._session.login_and_start(email, password)
        )
        self._login_page.signup_requested.connect(
            lambda username, email, password: self._session.signup_and_start(
                username, email, password
            )
        )
        self._main_page.logout_requested.connect(self._session.logout)

        self._session.session_started.connect(self._on_session_started)
        self._session.session_ended.connect(self._on_session_ended)
        self._session.auth_failed.connect(self._on_auth_failed)
        self._session.backend_warning.connect(self._login_page.show_warning)

    def _on_session_started(self, user) -> None:
        self._main_page.set_user(user)
        self._login_page.reset()
        self._login_page.clear_warning()
        self._stack.setCurrentWidget(self._main_page)

    def _on_session_ended(self, reason: str) -> None:
        self._main_page.reset()
        self._login_page.set_busy(False)
        self._login_page.reset()
        if reason:
            self._login_page.show_error(reason)
        self._stack.setCurrentWidget(self._login_page)

    def _on_auth_failed(self, message: str) -> None:
        self._login_page.set_busy(False)
        self._login_page.show_error(message)
