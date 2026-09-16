"""Login / sign-up page: a centered card, no header. Shown while unauthenticated."""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.ui.widgets import OverlayScrollArea, PasswordLineEdit

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginPage(QWidget):
    login_requested = Signal(str, str)  # email, password
    signup_requested = Signal(str, str, str)  # username, email, password

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loginRoot")
        self._mode = "login"
        self._busy = False

        self._title = QLabel("Welcome back", objectName="cardTitle")
        self._subtitle = QLabel(
            "Sign in to continue to Data Crawler", objectName="cardSubtitle"
        )

        # Segmented Sign in / Create account toggle.
        self._login_toggle = QPushButton("Sign in", objectName="modeToggle", checkable=True)
        self._signup_toggle = QPushButton(
            "Create account", objectName="modeToggle", checkable=True
        )
        self._login_toggle.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        mode_group.addButton(self._login_toggle)
        mode_group.addButton(self._signup_toggle)
        self._login_toggle.clicked.connect(lambda: self._set_mode("login"))
        self._signup_toggle.clicked.connect(lambda: self._set_mode("signup"))

        segmented = QFrame(objectName="segmentedHost")
        seg_layout = QHBoxLayout(segmented)
        seg_layout.setContentsMargins(3, 3, 3, 3)
        seg_layout.setSpacing(2)
        seg_layout.addWidget(self._login_toggle)
        seg_layout.addWidget(self._signup_toggle)

        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()
        self._warning_banner = QLabel(objectName="warningBanner", wordWrap=True)
        self._warning_banner.hide()

        self._build_login_form()
        self._build_signup_form()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._login_form)
        self._stack.addWidget(self._signup_form)

        card = QFrame(objectName="loginCard")
        card.setFixedWidth(400)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addSpacing(4)
        card_layout.addWidget(segmented)
        card_layout.addWidget(self._error_banner)
        card_layout.addWidget(self._warning_banner)
        card_layout.addWidget(self._stack)

        # Scrollable host: the card centers vertically while it fits and the
        # wheel scrolls the moment the content outgrows the viewport.
        content = QWidget(objectName="loginScrollContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(card)
        center.addStretch(1)
        content_layout.addLayout(center)
        content_layout.addStretch(1)

        self._scroll = OverlayScrollArea(objectName="loginScroll")
        self._scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._scroll)

    # -- construction helpers -------------------------------------------------

    def _build_login_form(self) -> None:
        self._login_email = QLineEdit(placeholderText="you@example.com")
        self._login_password = PasswordLineEdit("Your password")
        self._login_submit = QPushButton("Sign in", objectName="primaryButton")
        self._login_submit.clicked.connect(self._submit_login)
        for edit in (self._login_email, self._login_password):
            edit.returnPressed.connect(self._submit_login)
        self._login_form = self._build_form(
            ("Email", self._login_email),
            ("Password", self._login_password),
            submit=self._login_submit,
        )

    def _build_signup_form(self) -> None:
        self._signup_username = QLineEdit(placeholderText="jane_doe")
        self._signup_email = QLineEdit(placeholderText="you@example.com")
        self._signup_password = PasswordLineEdit("At least 8 characters")
        self._signup_confirm = PasswordLineEdit("Re-enter your password")
        self._signup_submit = QPushButton("Create account", objectName="primaryButton")
        self._signup_submit.clicked.connect(self._submit_signup)
        for edit in (
            self._signup_username,
            self._signup_email,
            self._signup_password,
            self._signup_confirm,
        ):
            edit.returnPressed.connect(self._submit_signup)
        self._signup_form = self._build_form(
            ("Username", self._signup_username),
            ("Email", self._signup_email),
            ("Password", self._signup_password),
            ("Confirm Password", self._signup_confirm),
            submit=self._signup_submit,
        )

    @staticmethod
    def _build_form(*fields: tuple[str, QLineEdit], submit: QPushButton) -> QWidget:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for caption, widget in fields:
            layout.addWidget(QLabel(caption, objectName="fieldCaption"))
            layout.addWidget(widget)
        layout.addSpacing(6)
        layout.addWidget(submit)
        return form

    # -- submission -----------------------------------------------------------

    def _submit_login(self) -> None:
        if self._busy:
            return
        email = self._login_email.text().strip()
        password = self._login_password.text()
        error = self._validate_email(email) or self._validate_login_password(password)
        if error:
            self.show_error(error)
            return
        self._clear_error()
        self.set_busy(True)
        self.login_requested.emit(email, password)

    def _submit_signup(self) -> None:
        if self._busy:
            return
        username = self._signup_username.text().strip()
        email = self._signup_email.text().strip()
        password = self._signup_password.text()
        confirm = self._signup_confirm.text()
        error = (
            self._validate_username(username)
            or self._validate_email(email)
            or self._validate_new_password(password)
            or self._validate_confirmation(password, confirm)
        )
        if error:
            self.show_error(error)
            return
        self._clear_error()
        self.set_busy(True)
        self.signup_requested.emit(username, email, password)

    # -- validation (mirrors the backend rules) ---------------------------------

    @staticmethod
    def _validate_username(username: str) -> str | None:
        if not 3 <= len(username) <= 32:
            return "Username must be 3-32 characters long."
        if not _USERNAME_RE.fullmatch(username):
            return (
                "Username may only contain letters, numbers, dots, "
                "dashes and underscores."
            )
        return None

    @staticmethod
    def _validate_email(email: str) -> str | None:
        if not _EMAIL_RE.fullmatch(email):
            return "Enter a valid email address."
        return None

    @staticmethod
    def _validate_login_password(password: str) -> str | None:
        if not password:
            return "Enter your password."
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
            return "Confirm your password."
        if confirm != password:
            return "Passwords do not match."
        return None

    # -- state -----------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._stack.setCurrentIndex(0 if mode == "login" else 1)
        if mode == "login":
            self._title.setText("Welcome back")
            self._subtitle.setText("Sign in to continue to Data Crawler")
            self._login_toggle.setChecked(True)
        else:
            self._title.setText("Create your account")
            self._subtitle.setText("A few details and you're in")
            self._signup_toggle.setChecked(True)
        self._clear_error()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self._login_email,
            self._login_password,
            self._login_submit,
            self._signup_username,
            self._signup_email,
            self._signup_password,
            self._signup_confirm,
            self._signup_submit,
            self._login_toggle,
            self._signup_toggle,
        ):
            widget.setEnabled(not busy)
        self._login_submit.setText(
            "Signing in…" if busy and self._mode == "login" else "Sign in"
        )
        self._signup_submit.setText(
            "Creating account…" if busy and self._mode == "signup" else "Create account"
        )

    def show_error(self, message: str) -> None:
        self._error_banner.setText(message)
        self._error_banner.show()

    def show_warning(self, message: str) -> None:
        self._warning_banner.setText(message)
        self._warning_banner.show()

    def _clear_error(self) -> None:
        self._error_banner.hide()

    def clear_warning(self) -> None:
        self._warning_banner.hide()

    def reset(self) -> None:
        """Clean Sign-in state. The warning banner is kept on purpose: it
        describes the environment (API/database), not this form's input."""
        self._set_mode("login")
        for edit in (
            self._login_email,
            self._login_password,
            self._signup_username,
            self._signup_email,
            self._signup_password,
            self._signup_confirm,
        ):
            edit.clear()
        self._clear_error()
        self.set_busy(False)
