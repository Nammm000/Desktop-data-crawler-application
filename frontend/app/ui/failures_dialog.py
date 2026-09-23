"""Failures dialog: read-only list of why each link in an agent's last run
produced no data (broken links, bot checks, timeouts, ...)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.api.client import Agent
from app.ui.format import format_date, format_failure_reason, format_status

# Fixed slot (the AgentDataDialog philosophy): the table scrolls instead of
# resizing the dialog with the failure count.
_TABLE_HEIGHT = 300


class FailuresDialog(QDialog):
    """Show the selected agent's lastRun failure summary; purely local."""

    def __init__(self, agent: Agent, parent=None):
        super().__init__(parent)
        self.setObjectName("failuresDialog")
        self.setWindowTitle("Last run failures")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(560)

        last_run = agent.last_run
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.reject)

        card = QFrame(objectName="dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(QLabel("Last run failures", objectName="cardTitle"))
        subtitle = "—"
        if last_run is not None:
            subtitle = (
                f"{agent.name} · {format_status(last_run.outcome).lower()} "
                f"on {format_date(last_run.finished_at)} · "
                f"{last_run.success_count} of {last_run.total_links} links succeeded"
            )
        card_layout.addWidget(QLabel(subtitle, objectName="cardSubtitle", wordWrap=True))
        card_layout.addSpacing(4)

        self._table = QTableWidget(objectName="failuresTable")
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(("Reason", "URL", "Detail"))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setFixedHeight(_TABLE_HEIGHT)

        failures = last_run.failures if last_run is not None else ()
        self._table.setRowCount(len(failures))
        for row, failure in enumerate(failures):
            reason = QTableWidgetItem(format_failure_reason(failure.reason))
            reason.setToolTip(failure.reason)
            self._table.setItem(row, 0, reason)
            url = QTableWidgetItem(failure.url or "(run-level)")
            url.setToolTip(failure.url or "")
            self._table.setItem(row, 1, url)
            detail = QTableWidgetItem(failure.detail or "—")
            detail.setToolTip(failure.detail or "")
            self._table.setItem(row, 2, detail)
        card_layout.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        card_layout.addLayout(buttons)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(card)
