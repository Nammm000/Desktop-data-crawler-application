"""User management page (admin): paginated user table with role/status actions."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from app.ui.format import format_date, format_role, format_status

_LIMIT_CHOICES = (10, 25, 50, 100)
_DEFAULT_LIMIT = 25

_ROLE_ITEMS = (("User", "user"), ("Admin", "admin"))
_STATUS_ITEMS = (("Active", "active"), ("Inactive", "inactive"), ("Banned", "banned"))

_COLUMNS = ("Username", "Email", "Role", "Status", "Created")


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
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.setColumnWidth(0, 160)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(3, 130)
        self._table.setColumnWidth(4, 110)

        self._limit_selector = QComboBox(objectName="limitSelector")
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
        self._set_loading(False)
        self._error_banner.hide()
        self._table.setRowCount(0)
        self._subtitle.setText("—")
        self._page_indicator.setText("—")
        self._prev_button.setEnabled(False)
        self._next_button.setEnabled(False)

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
        self._error_banner.hide()
        self._subtitle.setText(f"{self._total} users")
        self._populate(page.users)
        self._sync_pagination(page_count)

    def _on_load_failed(self, exc: Exception) -> None:
        self._set_loading(False)
        if self._session.user is None:
            return
        self._error_banner.setText(str(exc))
        self._error_banner.show()
        self._table.setRowCount(0)
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

    # -- table rows ---------------------------------------------------------------

    def _populate(self, users: tuple[User, ...]) -> None:
        current_user = self._session.user
        self._table.setRowCount(len(users))
        for row, user in enumerate(users):
            self._table.setItem(row, 0, QTableWidgetItem(user.username))
            self._table.setItem(row, 1, QTableWidgetItem(user.email))
            self._table.setItem(row, 4, QTableWidgetItem(format_date(user.created_at)))

            is_self = current_user is not None and user.id == current_user.id
            if is_self:
                # The backend rejects self-changes; show plain text instead.
                role_item = QTableWidgetItem(format_role(user.role))
                role_item.setToolTip("Admins cannot change their own role")
                status_item = QTableWidgetItem(format_status(user.status))
                status_item.setToolTip("Admins cannot change their own status")
                self._table.setItem(row, 2, role_item)
                self._table.setItem(row, 3, status_item)
            else:
                self._table.setCellWidget(
                    row, 2, self._choice_combo("roleCombo", _ROLE_ITEMS, user, "role")
                )
                self._table.setCellWidget(
                    row,
                    3,
                    self._choice_combo("statusCombo", _STATUS_ITEMS, user, "status"),
                )
        self._table.resizeRowsToContents()

    def _choice_combo(self, object_name: str, items, user: User, field: str) -> QComboBox:
        original = user.role if field == "role" else user.status
        combo = QComboBox(objectName=object_name)
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

    # -- role / status changes ------------------------------------------------------

    def _on_choice_changed(self, combo: QComboBox, user: User, field: str) -> None:
        original = user.role if field == "role" else user.status
        new_value = combo.currentData()
        if new_value == original:
            return
        if self._pending_change is not None:
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
