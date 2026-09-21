"""No-agent data page (all users): paginated table of crawled-data records
whose agent has been deleted — view fields, delete single/bulk."""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
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

from app.api.client import AgentData, AgentDataPage
from app.core.session import SessionController
from app.ui.agent_data_dialog import AgentDataDialog
from app.ui.confirm_dialog import ConfirmDialog
from app.ui.format import format_date
from app.ui.widgets import chosen_combo

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_LIMIT_CHOICES = (10, 25, 50, 100)
_DEFAULT_LIMIT = 25

_SUBTITLE = "Crawled records whose agent has been deleted"

_COLUMNS = ("", "Agent", "URL", "Fields", "Crawled", "")

(
    _COL_CHECK,
    _COL_AGENT,
    _COL_URL,
    _COL_FIELDS,
    _COL_CRAWLED,
    _COL_DELETE,
) = range(6)

_AGENT_CELL_TOOLTIP = "The agent this record was crawled by (now deleted)"


class OrphanedDataPage(QWidget):
    # Emitted by the "Back to agents" button; MainPage switches pages.
    agent_management_requested = Signal()

    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("orphanedDataRoot")
        self._session = session
        self._limit = _DEFAULT_LIMIT
        self._page_index = 0  # 0-based
        self._total = 0
        self._loading = False
        # A delete request in flight; one at a time.
        self._pending_delete = False
        # Keep a delete error visible through the resync reload that follows it.
        self._preserve_banner = False
        # Row index -> AgentData for the currently rendered page
        # (bulk deletes and fields clicks).
        self._rows: list[AgentData] = []

        self._back_button = QPushButton("Back to agents", objectName="pageButton")
        self._back_button.setToolTip("Return to agent management")
        self._back_button.clicked.connect(self.agent_management_requested.emit)

        self._subtitle = QLabel(_SUBTITLE, objectName="pageSubtitle")
        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.hide()

        self._table = QTableWidget(objectName="dataTable")
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setSortingEnabled(False)  # the server sorts newest-first
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setObjectName("dataTableHeader")
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_URL, QHeaderView.ResizeMode.Stretch
        )
        for col in (_COL_CHECK, _COL_DELETE):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
        self._table.setColumnWidth(_COL_CHECK, 40)
        self._table.setColumnWidth(_COL_AGENT, 150)
        self._table.setColumnWidth(_COL_FIELDS, 260)
        self._table.setColumnWidth(_COL_CRAWLED, 110)
        self._table.setColumnWidth(_COL_DELETE, 52)
        self._table.cellClicked.connect(self._on_cell_clicked)

        self._select_all = QCheckBox("Select all", objectName="selectAllCheckBox")
        self._select_all.toggled.connect(self._on_select_all_toggled)
        self._delete_selected_button = QPushButton(
            "Delete selected", objectName="dangerButton"
        )
        self._delete_selected_button.setEnabled(False)
        self._delete_selected_button.clicked.connect(self._delete_selected)

        self._limit_selector = chosen_combo("limitSelector")
        for limit in _LIMIT_CHOICES:
            self._limit_selector.addItem(str(limit), limit)
        self._limit_selector.setCurrentIndex(
            self._limit_selector.findData(_DEFAULT_LIMIT)
        )
        self._limit_selector.currentIndexChanged.connect(self._on_limit_changed)

        # Row count lives here next to the selector, not in a subtitle label.
        self._count_label = QLabel("—", objectName="pageIndicator")

        self._prev_button = QPushButton("Previous", objectName="pageButton")
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button = QPushButton("Next", objectName="pageButton")
        self._next_button.clicked.connect(self._go_next)
        self._page_indicator = QLabel("—", objectName="pageIndicator")

        # Back to agents sits opposite the page title, like the agents page's
        # "Add agent" — the page has no other toolbar row.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(QLabel("No-agent data", objectName="pageTitle"))
        title_row.addStretch(1)
        title_row.addWidget(self._back_button)

        bulk_bar = QHBoxLayout()
        bulk_bar.setSpacing(8)
        bulk_bar.addWidget(self._select_all)
        bulk_bar.addStretch(1)
        bulk_bar.addWidget(self._delete_selected_button)

        pagination = QHBoxLayout()
        pagination.setSpacing(8)
        pagination.addWidget(QLabel("Rows per page", objectName="fieldCaption"))
        pagination.addWidget(self._limit_selector)
        pagination.addWidget(self._count_label)
        pagination.addStretch(1)
        pagination.addWidget(self._prev_button)
        pagination.addWidget(self._page_indicator)
        pagination.addWidget(self._next_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(12)
        root.addLayout(title_row)
        root.addWidget(self._subtitle)
        root.addWidget(self._error_banner)
        root.addWidget(self._progress)
        root.addLayout(bulk_bar)
        root.addWidget(self._table, 1)
        root.addLayout(pagination)

    # -- public ----------------------------------------------------------------

    def reload(self) -> None:
        """Fetch the current page; a no-op while a fetch is already running."""
        if self._loading or self._session.user is None:
            return
        self._set_loading(True)
        skip = self._page_index * self._limit
        self._session.list_orphaned_data(
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
        self._rows = []
        self._set_loading(False)
        self._preserve_banner = False
        self._error_banner.hide()
        self._table.setRowCount(0)
        self._count_label.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._select_all.blockSignals(True)
        self._select_all.setChecked(False)
        self._select_all.blockSignals(False)
        self._delete_selected_button.setEnabled(False)
        self._delete_selected_button.setText("Delete selected")

    # -- fetching ---------------------------------------------------------------

    def _on_page_loaded(self, page: AgentDataPage) -> None:
        self._set_loading(False)
        if self._session.user is None:
            return  # Session ended mid-flight; clear() already reset the page.
        self._total = page.total
        page_count = max(1, math.ceil(self._total / self._limit))
        if self._page_index >= page_count:
            # The list shrank (deletes elsewhere): refetch the last page.
            self._page_index = page_count - 1
            self.reload()
            return
        if self._preserve_banner:
            self._preserve_banner = False
        else:
            self._error_banner.hide()

        self._count_label.setText(f" {self._total} rows")
        self._populate(page.data)
        self._sync_pagination(page_count)

    def _on_load_failed(self, exc: Exception) -> None:
        self._set_loading(False)
        self._preserve_banner = False
        if self._session.user is None:
            return
        # No 404 branch: the orphaned listing never 404s — an empty page just
        # means nothing is orphaned.
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._table.setRowCount(0)
        self._rows = []
        self._sync_bulk_state()
        self._count_label.setText("—")
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
        self._select_all.setEnabled(not loading)
        self._delete_selected_button.setEnabled(
            not loading and bool(self._checked_ids())
        )

    # -- table rows ----------------------------------------------------------------

    def _populate(self, items: tuple[AgentData, ...]) -> None:
        self._rows = list(items)
        self._table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Rows are reused across pages and setItem() does NOT remove a
            # cell widget — drop whatever the previous page left here first,
            # or a stale checkbox/trash survives the repopulate.
            for col in (_COL_CHECK, _COL_DELETE):
                self._table.removeCellWidget(row, col)
            # Plain text, never a link: a same-name recreated agent never
            # relinks (the doc's agentId points at the deleted one).
            agent_item = QTableWidgetItem(item.agent_name or "—")
            agent_item.setToolTip(_AGENT_CELL_TOOLTIP)
            self._table.setItem(row, _COL_AGENT, agent_item)
            url_item = QTableWidgetItem(item.url)
            url_item.setToolTip(item.url)
            self._table.setItem(row, _COL_URL, url_item)
            compact = (
                json.dumps(item.fields, ensure_ascii=False) if item.fields else ""
            )
            if item.fields:
                # Same link affordance as the agents-table Name cell — the
                # stylesheet cannot reach items, so indigo + underline lives
                # here. Clicking opens the read-only fields viewer.
                fields_item = QTableWidgetItem(compact)
                fields_font = self._table.font()
                fields_font.setUnderline(True)
                fields_item.setFont(fields_font)
                fields_item.setForeground(QColor("#4F46E5"))
                fields_item.setToolTip("View the fields extracted from this page")
            else:
                fields_item = QTableWidgetItem("—")
            self._table.setItem(row, _COL_FIELDS, fields_item)
            self._table.setItem(
                row, _COL_CRAWLED, QTableWidgetItem(format_date(item.crawled_at))
            )
            self._table.setCellWidget(row, _COL_CHECK, self._check_cell())
            self._table.setCellWidget(row, _COL_DELETE, self._delete_button(item))
        self._table.resizeRowsToContents()
        self._sync_bulk_state()

    def _on_cell_clicked(self, row: int, column: int) -> None:
        # Only the Fields cell opens the viewer. No busy guard: the table is
        # disabled while a fetch/delete is in flight, so clicks cannot
        # arrive mid-flight. Empty ("—") cells have nothing to show.
        if column != _COL_FIELDS or row >= len(self._rows):
            return
        item = self._rows[row]
        if not item.fields:
            return
        AgentDataDialog(item, parent=self).exec()

    def _check_cell(self) -> QWidget:
        box = QCheckBox(objectName="rowCheckBox")
        box.toggled.connect(lambda _on: self._sync_bulk_state())
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        return cell

    def _delete_button(self, item: AgentData) -> QPushButton:
        button = QPushButton(objectName="rowDeleteButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "trash.svg")))
        button.setToolTip("Delete record")
        button.setFixedSize(28, 28)
        button.clicked.connect(
            lambda _checked=False, d=item: self._confirm_delete_item(d)
        )
        return button

    # -- bulk selection ----------------------------------------------------------

    def _row_checkbox(self, row: int) -> QCheckBox | None:
        cell = self._table.cellWidget(row, _COL_CHECK)
        if cell is None:
            return None
        return cell.findChild(QCheckBox)

    def _checked_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self._table.rowCount()):
            box = self._row_checkbox(row)
            if box is not None and box.isChecked() and row < len(self._rows):
                ids.append(self._rows[row].id)
        return ids

    def _sync_bulk_state(self) -> None:
        count = len(self._checked_ids())
        self._delete_selected_button.setEnabled(count > 0 and not self._loading)
        self._delete_selected_button.setText(
            f"Delete selected ({count})" if count else "Delete selected"
        )
        eligible = sum(
            1
            for row in range(self._table.rowCount())
            if self._row_checkbox(row)
        )
        self._select_all.blockSignals(True)
        self._select_all.setChecked(bool(count) and count == eligible)
        self._select_all.blockSignals(False)

    def _on_select_all_toggled(self, checked: bool) -> None:
        for row in range(self._table.rowCount()):
            box = self._row_checkbox(row)
            if box is None:
                continue
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._sync_bulk_state()

    # -- deletes --------------------------------------------------------------------

    def _confirm_delete_item(self, item: AgentData) -> None:
        if self._busy():
            return
        message = (
            "Delete the record crawled from this URL? "
            "This permanently removes the data and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete data", message, "Delete", danger=True):
            return
        self._start_delete(
            lambda: self._session.delete_data(
                item.id,
                on_success=lambda _none: self._on_delete_success(),
                on_error=lambda exc: self._on_delete_failed(exc),
            )
        )

    def _delete_selected(self) -> None:
        ids = self._checked_ids()
        if not ids or self._busy():
            return
        message = (
            f"Delete {len(ids)} record{'s' if len(ids) != 1 else ''}? "
            "This permanently removes the data and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete data", message, "Delete", danger=True):
            return
        self._start_delete(
            lambda: self._session.delete_data_items(
                ids,
                on_success=lambda _count: self._on_delete_success(),
                on_error=lambda exc: self._on_delete_failed(exc),
            )
        )

    def _busy(self) -> bool:
        return self._loading or self._pending_delete

    def _start_delete(self, launch) -> None:
        self._pending_delete = True
        self._set_loading(True)  # blocks reload/pagination/table for free
        launch()

    def _on_delete_success(self) -> None:
        self._pending_delete = False
        self._set_loading(False)  # reload() no-ops while loading
        if self._session.user is None:
            return  # Forced logout mid-call; clear() already reset the page.
        self._error_banner.hide()
        self.reload()  # the last-page clamp absorbs emptied pages

    def _on_delete_failed(self, exc: Exception) -> None:
        self._pending_delete = False
        self._set_loading(False)
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._preserve_banner = True
        self.reload()  # Resync (unknown ids simply don't count).
