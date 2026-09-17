"""Change password dialog: card-style modal form over the settings page."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.session import SessionController
from app.ui.widgets import PasswordLineEdit

_CLOSE_DELAY_MS = 1200


class ChangePasswordDialog(QDialog):
    def __init__(self, session: SessionController, parent=None):
        super().__init__(parent)
        self.setObjectName("changePasswordDialog")
        self.setWindowTitle("Change password")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(400)
        self._session = session
        self._busy = False

        self._title = QLabel("Change password", objectName="cardTitle")
        self._subtitle = QLabel(
            "Choose a new password for your account", objectName="cardSubtitle"
        )
        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()
        self._success_banner = QLabel(objectName="successBanner", wordWrap=True)
        self._success_banner.hide()

        self._current_edit = PasswordLineEdit("Your current password")
        self._new_edit = PasswordLineEdit("At least 8 characters")
        self._confirm_edit = PasswordLineEdit("Re-enter the new password")

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.reject)
        self._submit_button = QPushButton("Change password", objectName="primaryButton")
        self._submit_button.clicked.connect(self._submit)
        for edit in (self._current_edit, self._new_edit, self._confirm_edit):
            edit.returnPressed.connect(self._submit)

        card = QFrame(objectName="dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addSpacing(4)
        card_layout.addWidget(self._error_banner)
        card_layout.addWidget(self._success_banner)
        card_layout.addWidget(self._form())
        card_layout.addLayout(self._buttons_row())

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.addWidget(card)

    # -- construction helpers -------------------------------------------------

    def _form(self) -> QWidget:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for caption, edit in (
            ("Current password", self._current_edit),
            ("New password", self._new_edit),
            ("Confirm password", self._confirm_edit),
        ):
            layout.addWidget(QLabel(caption, objectName="fieldCaption"))
            layout.addWidget(edit)
        return form

    def _buttons_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self._cancel_button)
        row.addStretch(1)
        row.addWidget(self._submit_button)
        return row

    # -- submission -----------------------------------------------------------

    def _submit(self) -> None:
        if self._busy:
            return
        current = self._current_edit.text()
        new = self._new_edit.text()
        confirm = self._confirm_edit.text()
        error = (
            self._validate_current(current)
            or self._validate_new_password(new)
            or self._validate_confirmation(new, confirm)
            or self._validate_different(current, new)
        )
        if error:
            self._show_error(error)
            return
        self._clear_error()
        self._set_busy(True)
        self._session.change_password(
            current,
            new,
            on_success=lambda _pair: self._on_success(),
            on_error=lambda exc: self._on_error(exc),
        )

    def _on_success(self) -> None:
        # The session has already adopted the fresh token pair; the backend
        # revoked every other session, so this device stays signed in.
        self._success_banner.setText("Password changed.")
        self._success_banner.show()
        QTimer.singleShot(_CLOSE_DELAY_MS, self.accept)

    def _on_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if self._session.user is None:
            # Forced logout happened mid-call; MainWindow moved to Login.
            self.reject()
            return
        self._show_error(str(exc))

    # -- validation (mirrors the backend rules) ---------------------------------

    @staticmethod
    def _validate_current(password: str) -> str | None:
        if not password:
            return "Enter your current password."
        return None

    @staticmethod
    def _validate_new_password(password: str) -> str | None:
        if len(password) < 8:
            return "Password must be at least 8 characters."
        if len(password) > 64:
            return "Password must be at most 64 characters."
        if len(password.encode("utf-8")) > 72:
            return "Password is too long (over 72 bytes)."
        return None

    @staticmethod
    def _validate_confirmation(password: str, confirm: str) -> str | None:
        if not confirm:
            return "Confirm your new password."
        if confirm != password:
            return "Passwords do not match."
        return None

    @staticmethod
    def _validate_different(current: str, new: str) -> str | None:
        if new == current:
            return "New password must be different from the current password."
        return None

    # -- state -----------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self._current_edit,
            self._new_edit,
            self._confirm_edit,
            self._cancel_button,
            self._submit_button,
        ):
            widget.setEnabled(not busy)
        self._submit_button.setText("Changing…" if busy else "Change password")

    def reject(self) -> None:
        if self._busy:
            return  # A submission is in flight; the timeout bounds the wait.
        super().reject()

    def _show_error(self, message: str) -> None:
        self._error_banner.setText(message)
        self._error_banner.show()

    def _clear_error(self) -> None:
        self._error_banner.hide()
