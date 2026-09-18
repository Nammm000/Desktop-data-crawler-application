"""User management page (admin): paginated user table with role/status/delete actions."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from app.api.client import User, UserPage
from app.core.session import SessionController
from app.ui.confirm_dialog import ConfirmDialog
from app.ui.format import format_date, format_role, format_status
from app.ui.widgets import chosen_combo

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_LIMIT_CHOICES = (10, 25, 50, 100)
_DEFAULT_LIMIT = 25

_ROLE_ITEMS = (("User", "user"), ("Admin", "admin"))
_STATUS_ITEMS = (("Active", "active"), ("Inactive", "inactive"), ("Banned", "banned"))

_COLUMNS = ("", "Username", "Email", "Role", "Status", "Created", "")

(
    _COL_CHECK,
    _COL_USERNAME,
    _COL_EMAIL,
    _COL_ROLE,
    _COL_STATUS,
    _COL_CREATED,
    _COL_DELETE,
) = range(7)

_SELF_DELETE_TOOLTIP = "Admins cannot delete their own account"


class UserManagementPage(QWidget):
    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("userManagementRoot")
        self._session = session
        self._limit = _DEFAULT_LIMIT
        self._page_index = 0  # 0-based
        self._total = 0
        self._loading = False
        # The role/status change in flight, if any: (combo, original value).
        # One at a time keeps revert handling exact.
        self._pending_change: tuple[QComboBox, str] | None = None
        # A delete request in flight; deletes never overlap combo changes.
        self._pending_delete = False
        # Keep a delete error visible through the resync reload that follows it.
        self._preserve_banner = False
        # Row index -> user id for the currently rendered page.
        self._row_user_ids: list[str] = []

        self._subtitle = QLabel("—", objectName="pageSubtitle")
        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setTextVisible(False)
        self._progress.hide()

        self._table = QTableWidget(objectName="usersTable")
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setSortingEnabled(False)  # the server sorts newest-first
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setObjectName("usersTableHeader")
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_EMAIL, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_CHECK, QHeaderView.ResizeMode.Fixed
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_DELETE, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(_COL_CHECK, 40)
        self._table.setColumnWidth(_COL_USERNAME, 160)
        self._table.setColumnWidth(_COL_ROLE, 130)
        self._table.setColumnWidth(_COL_STATUS, 130)
        self._table.setColumnWidth(_COL_CREATED, 110)
        self._table.setColumnWidth(_COL_DELETE, 52)

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

        self._prev_button = QPushButton("Previous", objectName="pageButton")
        self._prev_button.clicked.connect(self._go_previous)
        self._next_button = QPushButton("Next", objectName="pageButton")
        self._next_button.clicked.connect(self._go_next)
        self._page_indicator = QLabel("—", objectName="pageIndicator")

        bulk_bar = QHBoxLayout()
        bulk_bar.setSpacing(8)
        bulk_bar.addWidget(self._select_all)
        bulk_bar.addStretch(1)
        bulk_bar.addWidget(self._delete_selected_button)

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
        root.addWidget(QLabel("User management", objectName="pageTitle"))
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
        self._session.list_users(
            self._limit,
            skip,
            on_success=lambda page: self._on_page_loaded(page),
            on_error=lambda exc: self._on_load_failed(exc),
        )

    def clear(self) -> None:
        """Drop stale data (session ended; the next login may differ)."""
        self._page_index = 0
        self._total = 0
        self._pending_change = None
        self._pending_delete = False
        self._row_user_ids = []
        self._set_loading(False)
        self._preserve_banner = False
        self._error_banner.hide()
        self._table.setRowCount(0)
        self._subtitle.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)
        self._select_all.blockSignals(True)
        self._select_all.setChecked(False)
        self._select_all.blockSignals(False)
        self._delete_selected_button.setEnabled(False)
        self._delete_selected_button.setText("Delete selected")

    # -- fetching ---------------------------------------------------------------

    def _on_page_loaded(self, page: UserPage) -> None:
        self._set_loading(False)
        if self._session.user is None:
            return  # Session ended mid-flight; clear() already reset the page.
        self._total = page.total
        page_count = max(1, math.ceil(self._total / self._limit))
        if self._page_index >= page_count:
            # The list shrank (e.g. a concurrent admin): refetch the last page.
            self._page_index = page_count - 1
            self.reload()
            return
        if self._preserve_banner:
            self._preserve_banner = False
        else:
            self._error_banner.hide()
        self._subtitle.setText(f"{self._total} users")
        self._populate(page.users)
        self._sync_pagination(page_count)

    def _on_load_failed(self, exc: Exception) -> None:
        self._set_loading(False)
        self._preserve_banner = False
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._table.setRowCount(0)
        self._row_user_ids = []
        self._sync_bulk_state()
        self._subtitle.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)

    # -- pagination -------------------------------------------------------------

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
            not loading and bool(self._checked_user_ids())
        )

    # -- table rows ---------------------------------------------------------------

    def _populate(self, users: tuple[User, ...]) -> None:
        current_user = self._session.user
        self._row_user_ids = [user.id for user in users]
        self._table.setRowCount(len(users))
        for row, user in enumerate(users):
            # Rows are reused across pages and setItem() does NOT remove a
            # cell widget — drop whatever the previous page left here first,
            # or a stale checkbox/combo/trash survives the repopulate.
            for col in (_COL_CHECK, _COL_ROLE, _COL_STATUS, _COL_DELETE):
                self._table.removeCellWidget(row, col)
            self._table.setItem(
                row, _COL_USERNAME, QTableWidgetItem(user.username)
            )
            self._table.setItem(row, _COL_EMAIL, QTableWidgetItem(user.email))
            self._table.setItem(
                row, _COL_CREATED, QTableWidgetItem(format_date(user.created_at))
            )

            is_self = current_user is not None and user.id == current_user.id
            if is_self:
                # The backend rejects self-changes; show plain text instead.
                role_item = QTableWidgetItem(format_role(user.role))
                role_item.setToolTip("Admins cannot change their own role")
                status_item = QTableWidgetItem(format_status(user.status))
                status_item.setToolTip("Admins cannot change their own status")
                self._table.setItem(row, _COL_ROLE, role_item)
                self._table.setItem(row, _COL_STATUS, status_item)
                for col in (_COL_CHECK, _COL_DELETE):
                    guard = QTableWidgetItem("")
                    guard.setToolTip(_SELF_DELETE_TOOLTIP)
                    self._table.setItem(row, col, guard)
            else:
                self._table.setCellWidget(
                    row, _COL_CHECK, self._check_cell()
                )
                self._table.setCellWidget(
                    row,
                    _COL_ROLE,
                    self._choice_combo("roleCombo", _ROLE_ITEMS, user, "role"),
                )
                self._table.setCellWidget(
                    row,
                    _COL_STATUS,
                    self._choice_combo("statusCombo", _STATUS_ITEMS, user, "status"),
                )
                self._table.setCellWidget(
                    row, _COL_DELETE, self._delete_button(user)
                )
        self._table.resizeRowsToContents()
        self._sync_bulk_state()

    def _choice_combo(self, object_name: str, items, user: User, field: str) -> QComboBox:
        original = user.role if field == "role" else user.status
        combo = chosen_combo(object_name)
        combo.setFixedHeight(28)
        combo.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        combo.blockSignals(True)
        for label, value in items:
            combo.addItem(label, value)
        combo.setCurrentIndex(combo.findData(original))
        combo.blockSignals(False)
        combo.currentIndexChanged.connect(
            lambda _index, c=combo, u=user: self._on_choice_changed(c, u, field)
        )
        return combo

    def _check_cell(self) -> QWidget:
        box = QCheckBox(objectName="rowCheckBox")
        box.toggled.connect(lambda _on: self._sync_bulk_state())
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box, 0, Qt.AlignmentFlag.AlignCenter)
        return cell

    def _delete_button(self, user: User) -> QPushButton:
        button = QPushButton(objectName="rowDeleteButton")
        button.setIcon(QIcon(str(_ICONS_DIR / "trash.svg")))
        button.setToolTip("Delete user")
        button.setFixedSize(28, 28)
        button.clicked.connect(lambda _checked=False, u=user: self._confirm_delete_user(u))
        return button

    # -- bulk selection ----------------------------------------------------------

    def _row_checkbox(self, row: int) -> QCheckBox | None:
        """The row's checkbox, or None on the admin's own row (no widget there)."""
        cell = self._table.cellWidget(row, _COL_CHECK)
        return cell.findChild(QCheckBox) if cell is not None else None

    def _checked_user_ids(self) -> list[str]:
        ids = []
        for row in range(self._table.rowCount()):
            if row >= len(self._row_user_ids):
                continue
            box = self._row_checkbox(row)
            if box is not None and box.isChecked():
                ids.append(self._row_user_ids[row])
        return ids

    def _sync_bulk_state(self) -> None:
        count = len(self._checked_user_ids())
        self._delete_selected_button.setEnabled(count > 0 and not self._loading)
        self._delete_selected_button.setText(
            f"Delete selected ({count})" if count else "Delete selected"
        )
        eligible = sum(
            1 for row in range(self._table.rowCount()) if self._row_checkbox(row)
        )
        self._select_all.blockSignals(True)
        self._select_all.setChecked(bool(count) and count == eligible)
        self._select_all.blockSignals(False)

    def _on_select_all_toggled(self, checked: bool) -> None:
        for row in range(self._table.rowCount()):
            box = self._row_checkbox(row)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
        self._sync_bulk_state()

    # -- deletes -------------------------------------------------------------------

    def _confirm_delete_user(self, user: User) -> None:
        if self._busy():
            return
        message = (
            f"Delete {user.username} ({user.email})? "
            "This permanently removes the account and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete user", message, "Delete", danger=True):
            return
        self._start_delete(
            lambda: self._session.delete_user(
                user.id,
                on_success=lambda _none: self._on_delete_success(),
                on_error=lambda exc: self._on_delete_failed(exc),
            )
        )

    def _delete_selected(self) -> None:
        ids = self._checked_user_ids()
        if not ids or self._busy():
            return
        message = (
            f"Delete {len(ids)} user{'s' if len(ids) != 1 else ''}? "
            "This permanently removes the accounts and cannot be undone."
        )
        if not ConfirmDialog.ask(self, "Delete users", message, "Delete", danger=True):
            return
        self._start_delete(
            lambda: self._session.delete_users(
                ids,
                on_success=lambda _count: self._on_delete_success(),
                on_error=lambda exc: self._on_delete_failed(exc),
            )
        )

    def _busy(self) -> bool:
        return self._loading or self._pending_change is not None or self._pending_delete

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
        self.reload()

    def _on_delete_failed(self, exc: Exception) -> None:
        self._pending_delete = False
        self._set_loading(False)
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._preserve_banner = True
        self.reload()  # Resync (e.g. another admin deleted the user first).

    # -- role / status changes ------------------------------------------------------

    def _on_choice_changed(self, combo: QComboBox, user: User, field: str) -> None:
        original = user.role if field == "role" else user.status
        new_value = combo.currentData()
        if new_value == original:
            return
        if self._pending_change is not None or self._pending_delete:
            # Another change is still in flight; undo this selection silently.
            self._reset_combo(combo, original)
            return
        combo.setEnabled(False)
        self._pending_change = (combo, original)
        if field == "role":
            self._session.update_user_role(
                user.id,
                new_value,
                on_success=lambda _updated: self._on_change_success(),
                on_error=lambda exc: self._on_change_failed(exc),
            )
        else:
            self._session.update_user_status(
                user.id,
                new_value,
                on_success=lambda _updated: self._on_change_success(),
                on_error=lambda exc: self._on_change_failed(exc),
            )

    def _on_change_success(self) -> None:
        self._pending_change = None
        if self._session.user is None:
            return
        self._error_banner.hide()
        self.reload()

    def _on_change_failed(self, exc: Exception) -> None:
        combo, original = self._pending_change or (None, "")
        self._pending_change = None
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        if combo is not None:
            self._reset_combo(combo, original)
            combo.setEnabled(True)

    @staticmethod
    def _reset_combo(combo: QComboBox, value: str) -> None:
        """Undo a selection without re-triggering the change handler."""
        combo.blockSignals(True)
        combo.setCurrentIndex(combo.findData(value))
        combo.blockSignals(False)
