"""Main (post-login) screen: header with nav + user email, placeholder pages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class MainPage(QWidget):
    logout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mainRoot")

        # Header: Dashboard / Settings on the left, email + Log out on the right.
        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(8)

        self._dashboard_nav = QPushButton(
            "Dashboard", objectName="navButton", checkable=True
        )
        self._settings_nav = QPushButton(
            "Settings", objectName="navButton", checkable=True
        )
        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        nav_group.addButton(self._dashboard_nav)
        nav_group.addButton(self._settings_nav)

        self._user_email = QLabel("", objectName="userEmail")
        logout_button = QPushButton("Log out", objectName="logoutButton")
        logout_button.clicked.connect(self.logout_requested.emit)

        header_layout.addWidget(self._dashboard_nav)
        header_layout.addWidget(self._settings_nav)
        header_layout.addStretch(1)
        header_layout.addWidget(self._user_email)
        header_layout.addSpacing(12)
        header_layout.addWidget(logout_button)

        # Body: the two placeholder pages.
        self._dashboard_page = self._placeholder_page("This is the Dashboard page")
        self._settings_page = self._placeholder_page("This is the Settings page")
        self._body = QStackedWidget()
        self._body.addWidget(self._dashboard_page)
        self._body.addWidget(self._settings_page)

        self._dashboard_nav.clicked.connect(self._show_dashboard)
        self._settings_nav.clicked.connect(self._show_settings)
        self._dashboard_nav.setChecked(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(self._body, 1)

    def set_user_email(self, email: str) -> None:
        self._user_email.setText(email)

    def _show_dashboard(self) -> None:
        self._body.setCurrentWidget(self._dashboard_page)

    def _show_settings(self) -> None:
        self._body.setCurrentWidget(self._settings_page)

    @staticmethod
    def _placeholder_page(text: str) -> QWidget:
        label = QLabel(text, alignment=Qt.AlignmentFlag.AlignCenter, objectName="pageTitle")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label)
        return page
