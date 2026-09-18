"""Agent data dialog: read-only card showing one crawled record's fields."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.api.client import AgentData
from app.ui.format import format_date

# Fixed slot (AgentDialog's rows-slot philosophy): a read-only editor's
# sizeHint grows with the document, so big field sets scroll instead of
# resizing the dialog.
_VIEW_HEIGHT = 300


class AgentDataDialog(QDialog):
    """Show one crawled record read-only; purely local (no network)."""

    def __init__(self, item: AgentData, parent=None):
        super().__init__(parent)
        self.setObjectName("agentDataDialog")
        self.setWindowTitle("Agent data")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(560)

        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.reject)

        card = QFrame(objectName="dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(QLabel("Agent data", objectName="cardTitle"))
        card_layout.addWidget(QLabel(item.url, objectName="cardSubtitle", wordWrap=True))
        card_layout.addSpacing(4)
        card_layout.addWidget(QLabel("Crawled", objectName="fieldCaption"))
        card_layout.addWidget(QLabel(format_date(item.crawled_at)))
        card_layout.addWidget(QLabel("Fields", objectName="fieldCaption"))
        self._view = QPlainTextEdit(objectName="fieldsView")
        self._view.setReadOnly(True)
        self._view.setFixedHeight(_VIEW_HEIGHT)
        self._view.setPlaceholderText("No fields were extracted from this page")
        if item.fields:
            self._view.setPlainText(
                json.dumps(item.fields, ensure_ascii=False, indent=2)
            )
        card_layout.addWidget(self._view)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        card_layout.addLayout(buttons)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(card)
