"""Reusable styled confirmation dialog (card over dimmed background)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class ConfirmDialog(QDialog):
    """Ask a yes/no question in the house card style; purely local (no network)."""

    def __init__(
        self,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        *,
        danger: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("confirmDialog")
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(400)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton(
            confirm_text, objectName="dangerButton" if danger else "primaryButton"
        )
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)

        card = QFrame(objectName="dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(QLabel(title, objectName="cardTitle"))
        card_layout.addWidget(QLabel(message, objectName="cardSubtitle", wordWrap=True))
        buttons = QHBoxLayout()
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(confirm)
        card_layout.addLayout(buttons)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(card)

    @classmethod
    def ask(
        cls,
        parent,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        *,
        danger: bool = False,
    ) -> bool:
        dialog = cls(title, message, confirm_text, danger=danger, parent=parent)
        return dialog.exec() == QDialog.DialogCode.Accepted
