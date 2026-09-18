"""Agent management page (all users): paginated agent table with
add/edit/delete/run plus a per-agent crawled-data table."""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.api.client import Agent, AgentData, AgentDataPage, AgentPage, ApiError
from app.core.session import SessionController
from app.ui.agent_data_dialog import AgentDataDialog
from app.ui.agent_dialog import AgentDialog
from app.ui.confirm_dialog import ConfirmDialog
from app.ui.format import format_date, format_status
from app.ui.widgets import chosen_combo

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_LIMIT_CHOICES = (10, 25, 50, 100)
_DEFAULT_LIMIT = 25

_DATA_EMPTY_CAPTION = "Click an agent name above to see the data it created"

# The script (up to 1 MB) is deliberately not a column; the edit dialog owns it.
_COLUMNS = ("Name", "Format", "Status", "Created", "Updated", "Updated by", "", "", "")

(
    _COL_NAME,
    _COL_FORMAT,
    _COL_STATUS,
    _COL_CREATED,
    _COL_UPDATED,
    _COL_UPDATED_BY,
    _COL_RUN,
    _COL_EDIT,
    _COL_DELETE,
) = range(9)

_DATA_COLUMNS = ("", "URL", "Fields", "Crawled", "")

(
    _DCOL_CHECK,
    _DCOL_URL,
    _DCOL_FIELDS,
    _DCOL_CRAWLED,
    _DCOL_DELETE,
) = range(5)


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
        # A run request in flight; one at a time.
        self._pending_run = False
        # Keep a delete error visible through the resync reload that follows it.
        self._preserve_banner = False
        # Row index -> Agent for the currently rendered page (name clicks).
        self._row_agents: list[Agent] = []

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
        for col in (_COL_RUN, _COL_EDIT, _COL_DELETE):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
        self._table.setColumnWidth(_COL_FORMAT, 90)
        self._table.setColumnWidth(_COL_STATUS, 90)
        self._table.setColumnWidth(_COL_CREATED, 110)
        self._table.setColumnWidth(_COL_UPDATED, 110)
        self._table.setColumnWidth(_COL_UPDATED_BY, 180)
        self._table.setColumnWidth(_COL_RUN, 52)
        self._table.setColumnWidth(_COL_EDIT, 52)
        self._table.setColumnWidth(_COL_DELETE, 52)
        self._table.cellClicked.connect(self._on_cell_clicked)

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

        # Row count lives here next to the selector, not in a subtitle label.
        self._count_label = QLabel("—", objectName="pageIndicator")

        self._prev_button = QPushButton("Previous", objectName="pageButton")
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button = QPushButton("Next", objectName="pageButton")
        self._next_button.clicked.connect(self._go_next)
        self._page_indicator = QLabel("—", objectName="pageIndicator")

        # Add agent sits opposite the page title, not in its own toolbar row.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(QLabel("Agent management", objectName="pageTitle"))
        title_row.addStretch(1)
        title_row.addWidget(self._add_button)

        pagination = QHBoxLayout()
        pagination.setSpacing(8)
        pagination.addWidget(QLabel("Rows per page", objectName="fieldCaption"))
        pagination.addWidget(self._limit_selector)
        pagination.addWidget(self._count_label)
        pagination.addStretch(1)
        pagination.addWidget(self._prev_button)
        pagination.addWidget(self._page_indicator)
        pagination.addWidget(self._next_button)

        # -- data section (the selected agent's crawled records) --------------

        self._selected_agent: Agent | None = None
        self._data_limit = _DEFAULT_LIMIT
        self._data_page_index = 0  # 0-based
        self._data_total = 0
        self._data_loading = False
        # A data delete in flight; one at a time.
        self._pending_data_delete = False
        self._preserve_data_banner = False
        # Row index -> AgentData for the currently rendered data page
        # (bulk deletes and fields clicks).
        self._data_rows: list[AgentData] = []
        # User collapsed the data body; a name click always re-expands.
        self._data_collapsed = False

        self._data_toggle_button = QPushButton("Hide", objectName="pageButton")
        self._data_toggle_button.setToolTip("Hide or show the data table")
        self._data_toggle_button.clicked.connect(self._on_data_toggle_clicked)
        self._data_subtitle = QLabel(_DATA_EMPTY_CAPTION, objectName="pageSubtitle")
        self._data_error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._data_error_banner.hide()
        self._data_progress = QProgressBar()
        self._data_progress.setRange(0, 0)  # indeterminate
        self._data_progress.setTextVisible(False)
        self._data_progress.hide()

        self._data_table = QTableWidget(objectName="dataTable")
        self._data_table.setColumnCount(len(_DATA_COLUMNS))
        self._data_table.setHorizontalHeaderLabels(_DATA_COLUMNS)
        self._data_table.verticalHeader().setVisible(False)
        self._data_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._data_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._data_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._data_table.setSortingEnabled(False)  # the server sorts newest-first
        self._data_table.setAlternatingRowColors(True)
        self._data_table.horizontalHeader().setObjectName("dataTableHeader")
        self._data_table.horizontalHeader().setSectionResizeMode(
            _DCOL_URL, QHeaderView.ResizeMode.Stretch
        )
        for col in (_DCOL_CHECK, _DCOL_DELETE):
            self._data_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.Fixed
            )
        self._data_table.setColumnWidth(_DCOL_CHECK, 40)
        self._data_table.setColumnWidth(_DCOL_FIELDS, 260)
        self._data_table.setColumnWidth(_DCOL_CRAWLED, 110)
        self._data_table.setColumnWidth(_DCOL_DELETE, 52)
        self._data_table.cellClicked.connect(self._on_data_cell_clicked)

        self._data_select_all = QCheckBox("Select all", objectName="selectAllCheckBox")
        self._data_select_all.toggled.connect(self._on_data_select_all_toggled)
        self._data_delete_selected_button = QPushButton(
            "Delete selected", objectName="dangerButton"
        )
        self._data_delete_selected_button.setEnabled(False)
        self._data_delete_selected_button.clicked.connect(self._delete_selected_data)

        self._data_limit_selector = chosen_combo("limitSelector")
        for limit in _LIMIT_CHOICES:
            self._data_limit_selector.addItem(str(limit), limit)
        self._data_limit_selector.setCurrentIndex(
            self._data_limit_selector.findData(_DEFAULT_LIMIT)
        )
        self._data_limit_selector.currentIndexChanged.connect(
            self._on_data_limit_changed
        )

        self._data_count_label = QLabel("—", objectName="pageIndicator")

        self._data_prev_button = QPushButton("Previous", objectName="pageButton")
        self._data_prev_button.clicked.connect(self._go_data_previous)
        self._data_next_button = QPushButton("Next", objectName="pageButton")
        self._data_next_button.clicked.connect(self._go_data_next)
        self._data_page_indicator = QLabel("—", objectName="pageIndicator")

        data_bulk_bar = QHBoxLayout()
        data_bulk_bar.setSpacing(8)
        data_bulk_bar.addWidget(self._data_select_all)
        data_bulk_bar.addStretch(1)
        data_bulk_bar.addWidget(self._data_toggle_button)
        data_bulk_bar.addWidget(self._data_delete_selected_button)

        data_pagination = QHBoxLayout()
        data_pagination.setSpacing(8)
        data_pagination.addWidget(QLabel("Rows per page", objectName="fieldCaption"))
        data_pagination.addWidget(self._data_limit_selector)
        data_pagination.addWidget(self._data_count_label)
        data_pagination.addStretch(1)
        data_pagination.addWidget(self._data_prev_button)
        data_pagination.addWidget(self._data_page_indicator)
        data_pagination.addWidget(self._data_next_button)

        # The bulk bar (with the Hide/Show toggle) stays visible while
        # collapsed, so only the table + pagination hide below it.
        self._data_collapsible = QWidget()
        data_collapsible_layout = QVBoxLayout(self._data_collapsible)
        data_collapsible_layout.setContentsMargins(0, 0, 0, 0)
        data_collapsible_layout.setSpacing(12)
        data_collapsible_layout.addWidget(self._data_table, 1)
        data_collapsible_layout.addLayout(data_pagination)

        # One wrapper so a single hide()/show() toggles the whole active area;
        # the subtitle/banners above stay visible in the empty state.
        self._data_body = QWidget()
        data_body_layout = QVBoxLayout(self._data_body)
        data_body_layout.setContentsMargins(0, 0, 0, 0)
        data_body_layout.setSpacing(12)
        data_body_layout.addLayout(data_bulk_bar)
        data_body_layout.addWidget(self._data_collapsible, 1)
        self._data_body.hide()

        agents_section = QWidget()
        agents_layout = QVBoxLayout(agents_section)
        agents_layout.setContentsMargins(0, 0, 0, 0)
        agents_layout.setSpacing(12)
        agents_layout.addLayout(title_row)
        agents_layout.addWidget(self._error_banner)
        agents_layout.addWidget(self._progress)
        agents_layout.addWidget(self._table, 1)
        agents_layout.addLayout(pagination)

        data_section = QWidget()
        data_layout = QVBoxLayout(data_section)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(12)
        data_layout.addWidget(self._data_subtitle)
        data_layout.addWidget(self._data_error_banner)
        data_layout.addWidget(self._data_progress)
        data_layout.addWidget(self._data_body, 1)

        # The divider is draggable: users reallocate height between the two
        # sections. Order is fixed and neither pane ever collapses fully.
        self._splitter = QSplitter(Qt.Orientation.Vertical, objectName="agentsSplitter")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(agents_section)
        self._splitter.addWidget(data_section)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter_sized = False

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.addWidget(self._splitter)

    # -- public ----------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._splitter_sized:
            # First real geometry: seed the old 3:2 fixed stretch as the
            # initial divider position (later drags/resizes win).
            self._splitter_sized = True
            height = max(self._splitter.height(), 200)
            top = height * 3 // 5
            self._splitter.setSizes([top, height - top])

    def reload(self) -> None:
        """Fetch the current page; a no-op while a fetch is already running."""
        if not self._loading and self._session.user is not None:
            self._set_loading(True)
            skip = self._page_index * self._limit
            self._session.list_agents(
                self._limit,
                skip,
                on_success=lambda page: self._on_page_loaded(page),
                on_error=lambda exc: self._on_load_failed(exc),
            )
        # Refresh the selected agent's data too (page re-entry / resyncs);
        # _reload_data no-ops without a selection or while already loading.
        self._reload_data()

    def clear(self) -> None:
        """Drop stale data (session ended; the next login may differ)."""
        self._page_index = 0
        self._total = 0
        self._pending_delete = False
        self._pending_run = False
        self._row_agents = []
        self._set_loading(False)
        self._preserve_banner = False
        self._error_banner.hide()
        self._table.setRowCount(0)
        self._count_label.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._clear_data_section()

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
        self._count_label.setText(f" {self._total} rows")
        self._populate(page.agents)
        if self._selected_agent is not None:
            # Refresh the selection snapshot (renames, status changes) when
            # the agent is on this page; otherwise the old snapshot stands.
            for agent in page.agents:
                if agent.id == self._selected_agent.id:
                    self._selected_agent = agent
                    break
            self._sync_data_subtitle()
        self._sync_pagination(page_count)

    def _on_load_failed(self, exc: Exception) -> None:
        self._set_loading(False)
        self._preserve_banner = False
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._table.setRowCount(0)
        self._row_agents = []
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
        self._add_button.setEnabled(not loading)

    # -- table rows ----------------------------------------------------------------

    def _populate(self, agents: tuple[Agent, ...]) -> None:
        self._row_agents = list(agents)
        self._table.setRowCount(len(agents))
        for row, agent in enumerate(agents):
            # Rows are reused across pages and setItem() does NOT remove a
            # cell widget — drop whatever the previous page left here first,
            # or a stale run/edit/trash button survives the repopulate.
            for col in (_COL_RUN, _COL_EDIT, _COL_DELETE):
                self._table.removeCellWidget(row, col)
            # The name is the link into the data section. The stylesheet
            # cannot reach items, so the indigo + underline affordance lives
            # here (this is not setStyleSheet — the golden rule holds).
            name_item = QTableWidgetItem(agent.name)
            name_font = self._table.font()
            name_font.setUnderline(True)
            name_item.setFont(name_font)
            name_item.setForeground(QColor("#4F46E5"))
            name_item.setToolTip("View data created by this agent")
            self._table.setItem(row, _COL_NAME, name_item)
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
            self._table.setCellWidget(row, _COL_RUN, self._run_button(agent))
            self._table.setCellWidget(row, _COL_EDIT, self._edit_button(agent))
            self._table.setCellWidget(row, _COL_DELETE, self._delete_button(agent))
        self._table.resizeRowsToContents()

    def _run_button(self, agent: Agent) -> QPushButton:
        button = QPushButton(objectName="rowRunButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "play.svg")))
        button.setFixedSize(28, 28)
        if agent.status == "Running":
            button.setEnabled(False)
            button.setToolTip("Agent is already running")
        else:
            button.setToolTip("Run agent")
        button.clicked.connect(lambda _checked=False, a=agent: self._run_agent(a))
        return button

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

    def _run_agent(self, agent: Agent) -> None:
        if self._busy():
            return
        self._pending_run = True
        self._set_loading(True)  # blocks reload/pagination/table for free
        self._session.run_agent(
            agent.id,
            on_success=lambda _updated: self._on_run_success(),
            on_error=lambda exc: self._on_run_failed(exc),
        )

    def _on_run_success(self) -> None:
        self._pending_run = False
        self._set_loading(False)  # reload() no-ops while loading
        if self._session.user is None:
            return  # Forced logout mid-call; clear() already reset the page.
        self._error_banner.hide()
        self.reload()  # the row flips to Running; the crawl finishes in the
        # background and its records appear on the next data refresh.

    def _on_run_failed(self, exc: Exception) -> None:
        self._pending_run = False
        self._set_loading(False)
        if self._session.user is None:
            return
        # 409 already running / 400 script not runnable / 404 gone — verbatim.
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._preserve_banner = True
        self.reload()  # Resync (e.g. a stale row said "New" but it is running).

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
            on_success=lambda _none, a=agent: self._on_delete_success(a),
            on_error=lambda exc: self._on_delete_failed(exc),
        )

    def _on_delete_success(self, agent: Agent) -> None:
        self._pending_delete = False
        self._set_loading(False)  # reload() no-ops while loading
        if self._session.user is None:
            return  # Forced logout mid-call; clear() already reset the page.
        self._error_banner.hide()
        if self._selected_agent is not None and self._selected_agent.id == agent.id:
            self._clear_data_section()  # the data list endpoint 404s once the
            # agent is gone — drop the section before the resync below.
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
        return self._loading or self._pending_delete or self._pending_run

    # -- agent selection -----------------------------------------------------------

    def _on_cell_clicked(self, row: int, column: int) -> None:
        # Only the name cell selects. Clicks on the run/edit/trash buttons
        # never get here (their widgets consume them), but clicks on the
        # padding of those cells do — hence the hard column filter.
        if column != _COL_NAME or self._busy():
            return
        if row >= len(self._row_agents):
            return
        self._select_agent(self._row_agents[row])

    def _select_agent(self, agent: Agent) -> None:
        if self._data_busy():
            return  # a data fetch/delete is in flight; nothing to cancel it
        # Re-clicking the same name refetches page 1 — the manual data refresh.
        self._selected_agent = agent
        self._data_page_index = 0
        # A name click is an explicit "show me" — it always re-expands.
        self._data_collapsed = False
        self._apply_data_collapse()
        self._data_body.show()
        self._reload_data()

    def _on_data_cell_clicked(self, row: int, column: int) -> None:
        # Only the Fields cell opens the viewer. No busy guard: the table is
        # disabled while a data fetch/delete is in flight, so clicks cannot
        # arrive mid-flight. Empty ("—") cells have nothing to show.
        if column != _DCOL_FIELDS or row >= len(self._data_rows):
            return
        item = self._data_rows[row]
        if not item.fields:
            return
        AgentDataDialog(item, parent=self).exec()

    def _on_data_toggle_clicked(self) -> None:
        self._data_collapsed = not self._data_collapsed
        self._apply_data_collapse()

    def _apply_data_collapse(self) -> None:
        # Only the table + pagination hide — the bulk bar (holding this
        # button's Show state) must stay visible to undo the collapse.
        self._data_collapsible.setVisible(not self._data_collapsed)
        self._data_toggle_button.setText("Show" if self._data_collapsed else "Hide")

    # -- data fetching -----------------------------------------------------------

    def _reload_data(self) -> None:
        """Fetch the selected agent's current data page; a no-op while a
        fetch is already running or when nothing is selected."""
        if (
            self._data_loading
            or self._selected_agent is None
            or self._session.user is None
        ):
            return
        self._set_data_loading(True)
        skip = self._data_page_index * self._data_limit
        self._session.list_agent_data(
            self._selected_agent.id,
            self._data_limit,
            skip,
            on_success=lambda page: self._on_data_loaded(page),
            on_error=lambda exc: self._on_data_load_failed(exc),
        )

    def _on_data_loaded(self, page: AgentDataPage) -> None:
        self._set_data_loading(False)
        if self._session.user is None or self._selected_agent is None:
            return  # Session ended or selection cleared mid-flight.
        self._data_total = page.total
        page_count = max(1, math.ceil(self._data_total / self._data_limit))
        if self._data_page_index >= page_count:
            # The list shrank (deletes elsewhere): refetch the last page.
            self._data_page_index = page_count - 1
            self._reload_data()
            return
        if self._preserve_data_banner:
            self._preserve_data_banner = False
        else:
            self._data_error_banner.hide()
        self._data_count_label.setText(f" {self._data_total} rows")
        self._sync_data_subtitle()
        self._populate_data(page.data)
        self._sync_data_pagination(page_count)

    def _on_data_load_failed(self, exc: Exception) -> None:
        self._set_data_loading(False)
        self._preserve_data_banner = False
        if self._session.user is None:
            return
        if isinstance(exc, ApiError) and exc.status_code == 404:
            # The agent is gone (deleted elsewhere): its data docs survive,
            # but the per-agent list endpoint 404s — drop the section.
            self._clear_data_section()
            self._data_error_banner.setText("The selected agent no longer exists.")
            self._data_error_banner.show()
            self.reload()  # resync the agents table too
            return
        self._data_error_banner.setText(str(exc))
        self._data_error_banner.show()
        self._data_table.setRowCount(0)
        self._data_rows = []
        self._sync_data_bulk_state()
        self._data_count_label.setText("—")
        self._data_page_indicator.setText("—")
        self._data_prev_button.setEnabled(False)
        self._data_next_button.setEnabled(False)

    def _sync_data_subtitle(self) -> None:
        if self._selected_agent is None:
            self._data_subtitle.setText(_DATA_EMPTY_CAPTION)
        else:
            self._data_subtitle.setText("")

    # -- data pagination -----------------------------------------------------------

    def _on_data_limit_changed(self, _index: int) -> None:
        self._data_limit = self._data_limit_selector.currentData()
        self._data_page_index = 0
        self._reload_data()

    def _go_data_previous(self) -> None:
        if self._data_page_index > 0:
            self._data_page_index -= 1
            self._reload_data()

    def _go_data_next(self) -> None:
        page_count = max(1, math.ceil(self._data_total / self._data_limit))
        if self._data_page_index < page_count - 1:
            self._data_page_index += 1
            self._reload_data()

    def _sync_data_pagination(self, page_count: int) -> None:
        self._data_page_indicator.setText(
            f"Page {self._data_page_index + 1} of {page_count}"
        )
        self._data_prev_button.setEnabled(self._data_page_index > 0)
        self._data_next_button.setEnabled(self._data_page_index < page_count - 1)

    def _set_data_loading(self, loading: bool) -> None:
        # Deliberately separate from _set_loading: an agents fetch must not
        # freeze the data section, and vice versa.
        self._data_loading = loading
        self._data_progress.setVisible(loading)
        self._data_table.setEnabled(not loading)
        self._data_limit_selector.setEnabled(not loading)
        self._data_prev_button.setEnabled(not loading)
        self._data_next_button.setEnabled(not loading)
        self._data_select_all.setEnabled(not loading)
        self._data_delete_selected_button.setEnabled(
            not loading and bool(self._checked_data_ids())
        )

    # -- data table rows ------------------------------------------------------------

    def _populate_data(self, items: tuple[AgentData, ...]) -> None:
        self._data_rows = list(items)
        self._data_table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Same stale-widget rule as the agents table.
            for col in (_DCOL_CHECK, _DCOL_DELETE):
                self._data_table.removeCellWidget(row, col)
            url_item = QTableWidgetItem(item.url)
            url_item.setToolTip(item.url)
            self._data_table.setItem(row, _DCOL_URL, url_item)
            compact = (
                json.dumps(item.fields, ensure_ascii=False) if item.fields else ""
            )
            if item.fields:
                # Same link affordance as the agents-table Name cell — the
                # stylesheet cannot reach items, so indigo + underline lives
                # here. Clicking opens the read-only fields viewer.
                fields_item = QTableWidgetItem(compact)
                fields_font = self._data_table.font()
                fields_font.setUnderline(True)
                fields_item.setFont(fields_font)
                fields_item.setForeground(QColor("#4F46E5"))
                fields_item.setToolTip("View the fields extracted from this page")
            else:
                fields_item = QTableWidgetItem("—")
            self._data_table.setItem(row, _DCOL_FIELDS, fields_item)
            self._data_table.setItem(
                row, _DCOL_CRAWLED, QTableWidgetItem(format_date(item.crawled_at))
            )
            self._data_table.setCellWidget(row, _DCOL_CHECK, self._data_check_cell())
            self._data_table.setCellWidget(
                row, _DCOL_DELETE, self._data_delete_button(item)
            )
        self._data_table.resizeRowsToContents()
        self._sync_data_bulk_state()

    def _data_check_cell(self) -> QWidget:
        box = QCheckBox(objectName="rowCheckBox")
        box.toggled.connect(lambda _on: self._sync_data_bulk_state())
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        return cell

    def _data_delete_button(self, item: AgentData) -> QPushButton:
        button = QPushButton(objectName="rowDeleteButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "trash.svg")))
        button.setToolTip("Delete record")
        button.setFixedSize(28, 28)
        button.clicked.connect(
            lambda _checked=False, d=item: self._confirm_delete_data(d)
        )
        return button

    # -- data bulk selection ----------------------------------------------------------

    def _data_row_checkbox(self, row: int) -> QCheckBox | None:
        cell = self._data_table.cellWidget(row, _DCOL_CHECK)
        if cell is None:
            return None
        return cell.findChild(QCheckBox)

    def _checked_data_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self._data_table.rowCount()):
            box = self._data_row_checkbox(row)
            if box is not None and box.isChecked() and row < len(self._data_rows):
                ids.append(self._data_rows[row].id)
        return ids

    def _sync_data_bulk_state(self) -> None:
        count = len(self._checked_data_ids())
        self._data_delete_selected_button.setEnabled(count > 0 and not self._data_loading)
        self._data_delete_selected_button.setText(
            f"Delete selected ({count})" if count else "Delete selected"
        )
        eligible = sum(
            1
            for row in range(self._data_table.rowCount())
            if self._data_row_checkbox(row)
        )
        self._data_select_all.blockSignals(True)
        self._data_select_all.setChecked(bool(count) and count == eligible)
        self._data_select_all.blockSignals(False)

    def _on_data_select_all_toggled(self, checked: bool) -> None:
        for row in range(self._data_table.rowCount()):
            box = self._data_row_checkbox(row)
            if box is None:
                continue
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._sync_data_bulk_state()

    # -- data deletes ------------------------------------------------------------

    def _confirm_delete_data(self, item: AgentData) -> None:
        if self._data_busy():
            return
        message = (
            "Delete the record crawled from this URL? "
            "This permanently removes the data and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete data", message, "Delete", danger=True):
            return
        self._start_data_delete(
            lambda: self._session.delete_data(
                item.id,
                on_success=lambda _none: self._on_data_delete_success(),
                on_error=lambda exc: self._on_data_delete_failed(exc),
            )
        )

    def _delete_selected_data(self) -> None:
        ids = self._checked_data_ids()
        if not ids or self._data_busy():
            return
        message = (
            f"Delete {len(ids)} record{'s' if len(ids) != 1 else ''}? "
            "This permanently removes the data and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete data", message, "Delete", danger=True):
            return
        self._start_data_delete(
            lambda: self._session.delete_data_items(
                ids,
                on_success=lambda _count: self._on_data_delete_success(),
                on_error=lambda exc: self._on_data_delete_failed(exc),
            )
        )

    def _start_data_delete(self, launch) -> None:
        self._pending_data_delete = True
        self._set_data_loading(True)  # blocks data reload/pagination for free
        launch()

    def _on_data_delete_success(self) -> None:
        self._pending_data_delete = False
        self._set_data_loading(False)  # _reload_data() no-ops while loading
        if self._session.user is None or self._selected_agent is None:
            return
        self._data_error_banner.hide()
        self._reload_data()  # the last-page clamp absorbs emptied pages

    def _on_data_delete_failed(self, exc: Exception) -> None:
        self._pending_data_delete = False
        self._set_data_loading(False)
        if self._session.user is None:
            return
        self._data_error_banner.setText(str(exc))
        self._data_error_banner.show()
        self._preserve_data_banner = True
        self._reload_data()  # Resync (unknown ids simply don't count).

    def _data_busy(self) -> bool:
        return self._data_loading or self._pending_data_delete

    # -- data section reset --------------------------------------------------------

    def _clear_data_section(self) -> None:
        """Reset the data section to its pre-selection empty state."""
        self._selected_agent = None
        self._data_page_index = 0
        self._data_total = 0
        self._pending_data_delete = False
        self._data_rows = []
        self._data_collapsed = False
        self._set_data_loading(False)
        self._preserve_data_banner = False
        self._data_error_banner.hide()
        self._data_table.setRowCount(0)
        self._data_count_label.setText("—")
        self._data_page_indicator.setText("—")
        self._data_prev_button.setEnabled(False)
        self._data_next_button.setEnabled(False)
        self._data_select_all.blockSignals(True)
        self._data_select_all.setChecked(False)
        self._data_select_all.blockSignals(False)
        self._data_delete_selected_button.setEnabled(False)
        self._data_delete_selected_button.setText("Delete selected")
        self._sync_data_subtitle()
        # Hiding _data_body covers the toggle button too (bulk bar inside).
        self._data_body.hide()
