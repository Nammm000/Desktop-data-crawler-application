"""Dashboard page: placeholder for regular users, admin user-management entry."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.api.client import User


class DashboardPage(QWidget):
    user_management_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardRoot")

        self._title = QLabel(
            "This is the Dashboard page",
            alignment=Qt.AlignmentFlag.AlignCenter,
            objectName="pageTitle",
        )

        self._management_card = QFrame(objectName="dashboardCard")
        self._management_card.setFixedWidth(380)
        card_layout = QVBoxLayout(self._management_card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(8)
        card_layout.addWidget(QLabel("User management", objectName="cardTitle"))
        card_layout.addWidget(
            QLabel("Review accounts, roles and statuses", objectName="cardSubtitle")
        )
        open_button = QPushButton("Open user management", objectName="primaryButton")
        open_button.clicked.connect(self.user_management_requested.emit)
        card_layout.addSpacing(6)
        card_layout.addWidget(open_button)
        self._management_card.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(self._title)
        center.addStretch(1)
        root.addLayout(center)
        root.addSpacing(16)
        center_card = QHBoxLayout()
        center_card.addStretch(1)
        center_card.addWidget(self._management_card)
        center_card.addStretch(1)
        root.addLayout(center_card)
        root.addStretch(1)

    def set_user(self, user: User | None) -> None:
        self._management_card.setVisible(
            user is not None and user.role == "admin"
        )
