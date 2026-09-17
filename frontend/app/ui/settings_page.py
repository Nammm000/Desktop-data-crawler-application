"""Settings page: current user profile and the change-password action."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.api.client import User
from app.core.session import SessionController
from app.ui.change_password_dialog import ChangePasswordDialog
from app.ui.format import format_date, format_role, format_status


class SettingsPage(QWidget):
    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsRoot")
        self._session = session
        self._user: User | None = None

        self._username_value = QLabel("—", objectName="profileValue")
        self._email_value = QLabel("—", objectName="profileValue")
        self._role_value = QLabel("—", objectName="profileValue")
        self._status_value = QLabel("—", objectName="profileValue")
        self._created_value = QLabel("—", objectName="profileValue")

        change_password_button = QPushButton(
            "Change password", objectName="primaryButton"
        )
        change_password_button.clicked.connect(self._open_change_password)

        card = QFrame(objectName="settingsCard")
        card.setMaximumWidth(520)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(14)
        card_layout.addWidget(QLabel("Profile", objectName="cardTitle"))

        profile = QFormLayout()
        profile.setSpacing(10)
        profile.addRow(QLabel("Username", objectName="fieldCaption"), self._username_value)
        profile.addRow(QLabel("Email", objectName="fieldCaption"), self._email_value)
        profile.addRow(QLabel("Role", objectName="fieldCaption"), self._role_value)
        profile.addRow(QLabel("Status", objectName="fieldCaption"), self._status_value)
        profile.addRow(QLabel("Created", objectName="fieldCaption"), self._created_value)
        card_layout.addLayout(profile)
        card_layout.addSpacing(6)
        card_layout.addWidget(change_password_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)
        root.addWidget(QLabel("Settings", objectName="pageTitle"))
        root.addWidget(card)
        root.addStretch(1)

    def set_user(self, user: User | None) -> None:
        self._user = user
        self._username_value.setText(user.username if user else "—")
        self._email_value.setText(user.email if user else "—")
        self._role_value.setText(format_role(user.role) if user else "—")
        self._status_value.setText(format_status(user.status) if user else "—")
        self._created_value.setText(format_date(user.created_at) if user else "—")

    def _open_change_password(self) -> None:
        ChangePasswordDialog(self._session, self).exec()
