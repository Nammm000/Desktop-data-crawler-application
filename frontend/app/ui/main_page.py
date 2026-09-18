"""Main (post-login) screen: header with nav + account menu, content pages."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.api.client import User
from app.core.session import SessionController
from app.ui.agent_management_page import AgentManagementPage
from app.ui.dashboard_page import DashboardPage
from app.ui.settings_page import SettingsPage
from app.ui.user_management_page import UserManagementPage

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"


class MainPage(QWidget):
    logout_requested = Signal()

    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("mainRoot")

        # Header: Dashboard + Agents nav on the left, account menu on the right.
        header = QFrame(objectName="header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        header_layout.setSpacing(8)

        self._dashboard_nav = QPushButton(
            "Dashboard", objectName="navButton", checkable=True
        )
        self._agents_nav = QPushButton(
            "Agents", objectName="navButton", checkable=True
        )
        self._account_button = QPushButton(objectName="accountButton")
        self._account_button.setIcon(QIcon(str(_ICONS_DIR / "chevron-down.svg")))
        self._account_button.clicked.connect(self._open_account_menu)

        header_layout.addWidget(self._dashboard_nav)
        header_layout.addWidget(self._agents_nav)
        header_layout.addStretch(1)
        header_layout.addWidget(self._account_button)

        # Body pages.
        self._dashboard_page = DashboardPage()
        self._agent_management_page = AgentManagementPage(session)
        self._settings_page = SettingsPage(session)
        self._user_management_page = UserManagementPage(session)
        self._body = QStackedWidget()
        self._body.addWidget(self._dashboard_page)
        self._body.addWidget(self._agent_management_page)
        self._body.addWidget(self._settings_page)
        self._body.addWidget(self._user_management_page)

        self._dashboard_nav.clicked.connect(self._show_dashboard)
        self._agents_nav.clicked.connect(self._show_agents)
        self._dashboard_page.user_management_requested.connect(
            self._show_user_management
        )
        self._body.currentChanged.connect(self._on_body_page_changed)
        self._dashboard_nav.setChecked(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        root.addWidget(self._body, 1)

    # -- session state ----------------------------------------------------------

    def set_user(self, user: User) -> None:
        self._account_button.setText(user.email)
        self._dashboard_page.set_user(user)
        self._settings_page.set_user(user)

    def reset(self) -> None:
        """Clear session data; the next login may be a different user."""
        self._account_button.setText("")
        self._dashboard_page.set_user(None)
        self._settings_page.set_user(None)
        self._agent_management_page.clear()
        self._user_management_page.clear()
        self._body.setCurrentWidget(self._dashboard_page)

    # -- navigation ---------------------------------------------------------------

    def _show_dashboard(self) -> None:
        self._body.setCurrentWidget(self._dashboard_page)

    def _show_agents(self) -> None:
        self._body.setCurrentWidget(self._agent_management_page)

    def _show_user_management(self) -> None:
        self._body.setCurrentWidget(self._user_management_page)

    def _on_body_page_changed(self) -> None:
        current = self._body.currentWidget()
        self._dashboard_nav.setChecked(current is self._dashboard_page)
        self._agents_nav.setChecked(current is self._agent_management_page)
        if current is self._user_management_page:
            self._user_management_page.reload()
        elif current is self._agent_management_page:
            self._agent_management_page.reload()

    def _open_account_menu(self) -> None:
        menu = QMenu(objectName="accountMenu", parent=self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        menu.addAction("Settings", self._show_settings)
        menu.addAction("Log out", self.logout_requested.emit)
        position = self._account_button.mapToGlobal(
            QPoint(0, self._account_button.height())
        )
        menu.exec(position)

    def _show_settings(self) -> None:
        self._body.setCurrentWidget(self._settings_page)
