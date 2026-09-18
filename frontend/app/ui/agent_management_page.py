"""Agent management page (all users): paginated agent table with add/edit/delete."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.api.client import Agent, AgentPage
from app.core.session import SessionController
from app.ui.agent_dialog import AgentDialog
from app.ui.confirm_dialog import ConfirmDialog
from app.ui.format import format_date, format_status
from app.ui.widgets import chosen_combo

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_LIMIT_CHOICES = (10, 25, 50, 100)
_DEFAULT_LIMIT = 25

# The script (up to 1 MB) is deliberately not a column; the edit dialog owns it.
_COLUMNS = ("Name", "Format", "Status", "Created", "Updated", "Updated by", "", "")

(
    _COL_NAME,
    _COL_FORMAT,
    _COL_STATUS,
    _COL_CREATED,
    _COL_UPDATED,
    _COL_UPDATED_BY,
    _COL_EDIT,
    _COL_DELETE,
) = range(8)


class AgentManagementPage(QWidget):
    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("agentManagementRoot")
        self._session = session
        self._limit = _DEFAULT_LIMIT
        self._page_index = 0  # 0-based
        self._total = 0
        self._loading = False
        # A delete request in flight; one delete at a time.
        self._pending_delete = False
        # Keep a delete error visible through the resync reload that follows it.
        self._preserve_banner = False

        self._subtitle = QLabel("—", objectName="pageSubtitle")
        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.hide()

        self._table = QTableWidget(objectName="agentsTable")
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setSortingEnabled(False)  # the server sorts newest-first
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setObjectName("agentsTableHeader")
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_NAME, QHeaderView.ResizeMode.Stretch
        )
        for col in (_COL_EDIT, _COL_DELETE):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
        self._table.setColumnWidth(_COL_FORMAT, 90)
        self._table.setColumnWidth(_COL_STATUS, 90)
        self._table.setColumnWidth(_COL_CREATED, 110)
        self._table.setColumnWidth(_COL_UPDATED, 110)
        self._table.setColumnWidth(_COL_UPDATED_BY, 180)
        self._table.setColumnWidth(_COL_EDIT, 52)
        self._table.setColumnWidth(_COL_DELETE, 52)

        self._add_button = QPushButton("Add agent", objectName="primaryButton")
        self._add_button.setIcon(QIcon(str(_ICONS_DIR / "plus.svg")))
        self._add_button.clicked.connect(self._open_create_dialog)

        self._limit_selector = chosen_combo("limitSelector")
        for limit in _LIMIT_CHOICES:
            self._limit_selector.addItem(str(limit), limit)
        self._limit_selector.setCurrentIndex(
            self._limit_selector.findData(_DEFAULT_LIMIT)
        )
        self._limit_selector.currentIndexChanged.connect(self._on_limit_changed)

        self._prev_button = QPushButton("Previous", objectName="pageButton")
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button = QPushButton("Next", objectName="pageButton")
        self._next_button.clicked.connect(self._go_next)
        self._page_indicator = QLabel("—", objectName="pageIndicator")

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self._add_button)
        toolbar.addStretch(1)

        pagination = QHBoxLayout()
        pagination.setSpacing(8)
        pagination.addWidget(QLabel("Rows per page", objectName="fieldCaption"))
        pagination.addWidget(self._limit_selector)
        pagination.addStretch(1)
        pagination.addWidget(self._prev_button)
        pagination.addWidget(self._page_indicator)
        pagination.addWidget(self._next_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(12)
        root.addWidget(QLabel("Agent management", objectName="pageTitle"))
        root.addWidget(self._subtitle)
        root.addWidget(self._error_banner)
        root.addWidget(self._progress)
        root.addLayout(toolbar)
        root.addWidget(self._table, 1)
        root.addLayout(pagination)

    # -- public ----------------------------------------------------------------

    def reload(self) -> None:
        """Fetch the current page; a no-op while a fetch is already running."""
        if self._loading or self._session.user is None:
            return
        self._set_loading(True)
        skip = self._page_index * self._limit
        self._session.list_agents(
            self._limit,
            skip,
            on_success=lambda page: self._on_page_loaded(page),
            on_error=lambda exc: self._on_load_failed(exc),
        )

    def clear(self) -> None:
        """Drop stale data (session ended; the next login may differ)."""
        self._page_index = 0
        self._total = 0
        self._pending_delete = False
        self._set_loading(False)
        self._preserve_banner = False
        self._error_banner.hide()
        self._table.setRowCount(0)
        self._subtitle.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)

    # -- fetching ---------------------------------------------------------------

    def _on_page_loaded(self, page: AgentPage) -> None:
        self._set_loading(False)
        if self._session.user is None:
            return  # Session ended mid-flight; clear() already reset the page.
        self._total = page.total
        page_count = max(1, math.ceil(self._total / self._limit))
        if self._page_index >= page_count:
            # The list shrank (e.g. another user): refetch the last page.
            self._page_index = page_count - 1
            self.reload()
            return
        if self._preserve_banner:
            self._preserve_banner = False
        else:
            self._error_banner.hide()
        self._subtitle.setText(f"{self._total} agents")
        self._populate(page.agents)
        self._sync_pagination(page_count)

    def _on_load_failed(self, exc: Exception) -> None:
        self._set_loading(False)
        self._preserve_banner = False
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._table.setRowCount(0)
        self._subtitle.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)

    # -- pagination ---------------------------------------------------------------

    def _on_limit_changed(self, _index: int) -> None:
        self._limit = self._limit_selector.currentData()
        self._page_index = 0
        self.reload()

    def _go_previous(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self.reload()

    def _go_next(self) -> None:
        page_count = max(1, math.ceil(self._total / self._limit))
        if self._page_index < page_count - 1:
            self._page_index += 1
            self.reload()

    def _sync_pagination(self, page_count: int) -> None:
        self._page_indicator.setText(f"Page {self._page_index + 1} of {page_count}")
        self._prev_button.setEnabled(self._page_index > 0)
        self._next_button.setEnabled(self._page_index < page_count - 1)

    def _set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._progress.setVisible(loading)
        self._table.setEnabled(not loading)
        self._limit_selector.setEnabled(not loading)
        self._prev_button.setEnabled(not loading)
        self._next_button.setEnabled(not loading)
        self._add_button.setEnabled(not loading)

    # -- table rows ----------------------------------------------------------------

    def _populate(self, agents: tuple[Agent, ...]) -> None:
        self._table.setRowCount(len(agents))
        for row, agent in enumerate(agents):
            # Rows are reused across pages and setItem() does NOT remove a
            # cell widget — drop whatever the previous page left here first,
            # or a stale edit/trash button survives the repopulate.
            for col in (_COL_EDIT, _COL_DELETE):
                self._table.removeCellWidget(row, col)
            self._table.setItem(row, _COL_NAME, QTableWidgetItem(agent.name))
            self._table.setItem(
                row, _COL_FORMAT, QTableWidgetItem(agent.format.upper())
            )
            self._table.setItem(
                row, _COL_STATUS, QTableWidgetItem(format_status(agent.status))
            )
            self._table.setItem(
                row, _COL_CREATED, QTableWidgetItem(format_date(agent.created_at))
            )
            self._table.setItem(
                row, _COL_UPDATED, QTableWidgetItem(format_date(agent.updated_at))
            )
            updated_by = QTableWidgetItem(agent.updated_by or "—")
            updated_by.setToolTip(agent.updated_by)
            self._table.setItem(row, _COL_UPDATED_BY, updated_by)
            self._table.setCellWidget(row, _COL_EDIT, self._edit_button(agent))
            self._table.setCellWidget(row, _COL_DELETE, self._delete_button(agent))
        self._table.resizeRowsToContents()

    def _edit_button(self, agent: Agent) -> QPushButton:
        button = QPushButton(objectName="rowEditButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "edit.svg")))
        button.setToolTip("Edit agent")
        button.setFixedSize(28, 28)
        button.clicked.connect(
            lambda _checked=False, a=agent: self._open_edit_dialog(a)
        )
        return button

    def _delete_button(self, agent: Agent) -> QPushButton:
        button = QPushButton(objectName="rowDeleteButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "trash.svg")))
        button.setToolTip("Delete agent")
        button.setFixedSize(28, 28)
        button.clicked.connect(
            lambda _checked=False, a=agent: self._confirm_delete_agent(a)
        )
        return button

    # -- actions -----------------------------------------------------------------

    def _open_create_dialog(self) -> None:
        if self._busy():
            return
        dialog = AgentDialog(self._session, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._page_index = 0  # newest-first: the new agent lands on page 1
            self.reload()

    def _open_edit_dialog(self, agent: Agent) -> None:
        if self._busy():
            return
        dialog = AgentDialog(self._session, agent=agent, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reload()

    def _confirm_delete_agent(self, agent: Agent) -> None:
        if self._busy():
            return
        message = (
            f"Delete {agent.name}? "
            "This permanently removes the agent and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete agent", message, "Delete", danger=True):
            return
        self._pending_delete = True
        self._set_loading(True)  # blocks reload/pagination/table for free
        self._session.delete_agent(
            agent.id,
            on_success=lambda _none: self._on_delete_success(),
            on_error=lambda exc: self._on_delete_failed(exc),
        )

    def _on_delete_success(self) -> None:
        self._pending_delete = False
        self._set_loading(False)  # reload() no-ops while loading
        if self._session.user is None:
            return  # Forced logout mid-call; clear() already reset the page.
        self._error_banner.hide()
        self.reload()

    def _on_delete_failed(self, exc: Exception) -> None:
        self._pending_delete = False
        self._set_loading(False)
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._preserve_banner = True
        self.reload()  # Resync (e.g. another user deleted the agent first).

    def _busy(self) -> bool:
        return self._loading or self._pending_delete
