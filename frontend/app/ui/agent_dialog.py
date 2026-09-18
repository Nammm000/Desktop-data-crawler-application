"""Agent dialog: card-style modal form for creating or editing an agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.api.client import Agent
from app.core.session import SessionController
from app.ui.widgets import OverlayScrollArea, chosen_combo

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"

_FORMAT_ITEMS = (("JSON", "json"), ("XML", "xml"), ("Markdown", "md"))
_MAX_NAME = 100
_MAX_SCRIPT = 1_000_000
# The rows area is a fixed-height slot, so adding or removing rows never
# resizes the dialog. Json mode stacks rows + Generate button + the 240px
# editor, so the slot is smaller than the editor it sits above.
_ROWS_HEIGHT = 160

# Grid columns: key | remove-key | value | add-value | remove-value.
_COLUMN_STRETCHES = (1, 0, 1, 0, 0)
_BUTTON_SIZE = 24


@dataclass
class _KeyEntry:
    """One key group: the key edit, its buttons, and the value edits."""

    key_edit: QLineEdit
    key_remove: QPushButton
    add_value: QPushButton
    values: list[QLineEdit] = field(default_factory=list)
    value_removes: list[QPushButton] = field(default_factory=list)


class AgentDialog(QDialog):
    def __init__(
        self,
        session: SessionController,
        agent: Agent | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("agentDialog")
        self._session = session
        self._agent = agent
        self._is_edit = agent is not None
        self._busy = False

        self.setWindowTitle("Edit agent" if self._is_edit else "Add agent")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(560)

        self._title = QLabel(
            "Edit agent" if self._is_edit else "Add agent", objectName="cardTitle"
        )
        self._subtitle = QLabel(
            "Update the agent's details" if self._is_edit else "Describe what this agent collects",
            objectName="cardSubtitle",
        )
        self._error_banner = QLabel(objectName="errorBanner", wordWrap=True)
        self._error_banner.hide()

        self._name_edit = QLineEdit(placeholderText="e.g. Product scraper")
        self._name_edit.setMaxLength(_MAX_NAME)
        self._format_combo = chosen_combo("formatCombo")
        for label, value in _FORMAT_ITEMS:
            self._format_combo.addItem(label, value)
        self._script_edit = QPlainTextEdit(placeholderText="Agent script")
        self._script_edit.setFixedHeight(240)

        self._entries: list[_KeyEntry] = []
        self._mode: str | None = None

        self._add_key_button = QPushButton(objectName="addRowButton")
        self._add_key_button.setIcon(QIcon(str(_ICONS_DIR / "plus-neutral.svg")))
        self._add_key_button.setToolTip("Add key")
        self._add_key_button.clicked.connect(self._add_key)

        self._generate_button = QPushButton("Generate JSON")
        self._generate_button.setToolTip("Generate JSON from the rows above")
        self._generate_button.clicked.connect(self._generate_json)

        self._kv_container = self._build_kv_container()

        if self._is_edit and agent is not None:
            self._name_edit.setText(agent.name)
            self._format_combo.setCurrentIndex(self._format_combo.findData(agent.format))
            self._script_edit.setPlainText(agent.script)

        if self._format_combo.currentData() == "json":
            self._rebuild_entries(
                self._script_to_entries(self._script_edit.toPlainText())
            )
        self._apply_mode(self._format_combo.currentData(), initial=True)
        # Connected after populate + prefill: addItem()/setCurrentIndex() fire
        # this signal, and the initial mode is applied explicitly above.
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)

        submit_text = "Save changes" if self._is_edit else "Add agent"
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self.reject)
        self._submit_button = QPushButton(submit_text, objectName="primaryButton")
        self._submit_button.clicked.connect(self._submit)
        self._name_edit.returnPressed.connect(self._submit)

        card = QFrame(objectName="dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(12)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addSpacing(4)
        card_layout.addWidget(self._error_banner)
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
            ("Name", self._name_edit),
            ("Format", self._format_combo),
        ):
            layout.addWidget(QLabel(caption, objectName="fieldCaption"))
            layout.addWidget(edit)
        script_header = QHBoxLayout()
        script_header.addWidget(QLabel("Script", objectName="fieldCaption"))
        script_header.addWidget(self._add_key_button)
        script_header.addStretch(1)
        layout.addLayout(script_header)
        # Json-only builder widgets sit above the editor as siblings toggled
        # by visibility (NOT a QStackedWidget: its minimumSizeHint is the max
        # over pages, which would pin the tallest page onto every mode). The
        # editor itself stays visible in every format — it is the script.
        layout.addWidget(self._kv_container)
        layout.addWidget(self._generate_button)
        layout.addWidget(self._script_edit)
        return form

    def _buttons_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self._cancel_button)
        row.addStretch(1)
        row.addWidget(self._submit_button)
        return row

    def _build_kv_container(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # The caption grid mirrors the rows grid (same stretches, spacing and
        # fixed button columns) so "key"/"value" sit exactly over their columns
        # and never scroll away.
        captions = QGridLayout()
        captions.setContentsMargins(0, 0, 8, 0)
        captions.setHorizontalSpacing(6)
        for col, stretch in enumerate(_COLUMN_STRETCHES):
            captions.setColumnStretch(col, stretch)
        for col in (1, 3, 4):
            captions.setColumnMinimumWidth(col, _BUTTON_SIZE)
        captions.addWidget(QLabel("key", objectName="columnCaption"), 0, 0)
        captions.addWidget(QLabel("value", objectName="columnCaption"), 0, 2)
        layout.addLayout(captions)

        self._rows_host = QWidget(objectName="kvRowsHost")
        self._rows_grid = QGridLayout(self._rows_host)
        self._rows_grid.setContentsMargins(0, 0, 8, 0)
        self._rows_grid.setHorizontalSpacing(6)
        self._rows_grid.setVerticalSpacing(6)
        for col, stretch in enumerate(_COLUMN_STRETCHES):
            self._rows_grid.setColumnStretch(col, stretch)

        self._rows_scroll = OverlayScrollArea(objectName="kvRowsScroll")
        self._rows_scroll.setWidget(self._rows_host)
        self._rows_scroll.setFixedHeight(_ROWS_HEIGHT)
        layout.addWidget(self._rows_scroll)
        return container

    # -- json rows UI ----------------------------------------------------------

    def _add_key(self) -> None:
        entry = self._make_entry("", [""])
        self._entries.append(entry)
        self._relayout_rows()
        entry.key_edit.setFocus()

    def _add_value(self, entry: _KeyEntry) -> None:
        self._append_value(entry, "")
        self._relayout_rows()
        entry.values[-1].setFocus()

    def _remove_value(self, entry: _KeyEntry, value: QLineEdit) -> None:
        if len(entry.values) == 1:
            # A key with zero values is meaningless; drop the whole group.
            self._remove_entry(entry)
            return
        index = entry.values.index(value)
        remove = entry.value_removes.pop(index)
        entry.values.pop(index)
        self._discard(value)
        self._discard(remove)
        self._relayout_rows()

    def _remove_entry(self, entry: _KeyEntry) -> None:
        self._entries.remove(entry)
        self._discard(entry.key_edit)
        self._discard(entry.key_remove)
        self._discard(entry.add_value)
        for widget in entry.values + entry.value_removes:
            self._discard(widget)
        self._relayout_rows()

    def _make_entry(self, key: str, values: list[str]) -> _KeyEntry:
        entry = _KeyEntry(
            key_edit=self._make_line_edit(key, "Key"),
            key_remove=self._make_row_button(remove=True, tooltip="Remove key"),
            add_value=self._make_row_button(remove=False, tooltip="Add value"),
        )
        # Handlers capture the widgets, never indices — positions are resolved
        # at click time, so relayouts cannot strand a stale reference.
        entry.key_remove.clicked.connect(
            lambda _checked=False, e=entry: self._remove_entry(e)
        )
        entry.add_value.clicked.connect(
            lambda _checked=False, e=entry: self._add_value(e)
        )
        for value in values:
            self._append_value(entry, value)
        return entry

    def _append_value(self, entry: _KeyEntry, text: str) -> None:
        value_edit = self._make_line_edit(text, "Value")
        remove = self._make_row_button(remove=True, tooltip="Remove value")
        remove.clicked.connect(
            lambda _checked=False, e=entry, v=value_edit: self._remove_value(e, v)
        )
        entry.values.append(value_edit)
        entry.value_removes.append(remove)

    @staticmethod
    def _make_line_edit(text: str, placeholder: str) -> QLineEdit:
        return QLineEdit(text, placeholderText=placeholder)

    @staticmethod
    def _make_row_button(*, remove: bool, tooltip: str) -> QPushButton:
        button = QPushButton(
            objectName="removeRowButton" if remove else "addRowButton"
        )
        button.setIcon(
            QIcon(
                str(
                    _ICONS_DIR
                    / ("minus-neutral.svg" if remove else "plus-neutral.svg")
                )
            )
        )
        button.setToolTip(tooltip)
        return button

    def _rebuild_entries(self, rows: list[tuple[str, list[str]]]) -> None:
        for entry in self._entries:
            self._discard(entry.key_edit)
            self._discard(entry.key_remove)
            self._discard(entry.add_value)
            for widget in entry.values + entry.value_removes:
                self._discard(widget)
        self._entries = [self._make_entry(key, values) for key, values in rows]
        self._relayout_rows()

    def _relayout_rows(self) -> None:
        # QGridLayout cannot remove a row; detach everything and re-add in
        # model order. Detaching keeps the widgets alive (text and focus too).
        while self._rows_grid.count():
            self._rows_grid.takeAt(0)
        row = 0
        for entry in self._entries:
            self._place(entry.key_edit, row, 0)
            self._place(entry.key_remove, row, 1)
            self._place(entry.values[0], row, 2)
            self._place(entry.add_value, row, 3)
            self._place(entry.value_removes[0], row, 4)
            row += 1
            for value, remove in zip(entry.values[1:], entry.value_removes[1:]):
                # Extra values stack under the value column; no key there.
                self._place(value, row, 2)
                self._place(remove, row, 4)
                row += 1
        # The scroll area stretches the host to the viewport; without this
        # spacer the grid would spread the content rows over the surplus.
        self._rows_grid.setRowStretch(row, 1)

    def _place(self, widget: QWidget, row: int, col: int) -> None:
        # Widgets added after the dialog is shown stay hidden unless shown
        # here — and hidden children are skipped by the grid's size math.
        widget.show()
        self._rows_grid.addWidget(widget, row, col)

    def _refit(self) -> None:
        # Deferred: the visibility flip invalidates the enclosing layout
        # through posted LayoutRequest events, so a synchronous adjustSize()
        # right after the toggle would compute the height from stale hints.
        QTimer.singleShot(0, self.adjustSize)

    @staticmethod
    def _discard(widget: QWidget) -> None:
        # hide() first: a deleted-but-visible widget paints one orphan frame.
        widget.hide()
        widget.deleteLater()

    # -- mode switching ---------------------------------------------------------

    def _on_format_changed(self) -> None:
        fmt = self._format_combo.currentData()
        if fmt == self._mode:
            return
        if fmt == "json":
            # Rows are a builder for the editor: entering json mode repopulates
            # them from the editor so they reflect the current script.
            self._rebuild_entries(
                self._script_to_entries(self._script_edit.toPlainText())
            )
        # Leaving json mode carries nothing over — the editor already holds
        # the script.
        self._apply_mode(fmt)

    def _apply_mode(self, fmt: str, *, initial: bool = False) -> None:
        json_mode = fmt == "json"
        self._add_key_button.setVisible(json_mode)
        self._kv_container.setVisible(json_mode)
        self._generate_button.setVisible(json_mode)
        self._mode = fmt
        if not initial:
            self._refit()

    @staticmethod
    def _script_to_entries(text: str) -> list[tuple[str, list[str]]]:
        """Parse a stored script into key-value rows (best effort)."""
        text = text.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        rows: list[tuple[str, list[str]]] = []
        for key, value in data.items():  # json.loads preserves insertion order
            if isinstance(value, str):
                values = [value]
            elif isinstance(value, list) and all(
                isinstance(item, str) for item in value
            ):
                values = list(value) or [""]
            elif isinstance(value, (dict, list)):
                values = [json.dumps(value, ensure_ascii=False)]
            else:
                values = [str(value)]
            rows.append((str(key), values))
        return rows

    def _generate_json(self) -> None:
        """Write the validated rows into the editor as the script to submit.

        The only path that writes generated JSON; hand edits in the editor are
        never auto-overwritten.
        """
        rows = self._collect_rows()
        error = self._validate_rows(rows)
        if error:
            self._show_error(error)
            return
        self._script_edit.setPlainText(self._rows_to_script(rows))
        self._clear_error()

    # -- collection / validation --------------------------------------------------

    def _collect_rows(self) -> list[tuple[str, list[str]]]:
        rows = []
        for entry in self._entries:
            key = entry.key_edit.text().strip()
            values = [value.text().strip() for value in entry.values]
            if not key and not any(values):
                continue  # fully blank rows are ignored
            rows.append((key, values))
        return rows

    @staticmethod
    def _rows_to_script(rows: list[tuple[str, list[str]]]) -> str:
        data = {key: values[0] if len(values) == 1 else values for key, values in rows}
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def _validate_rows(rows: list[tuple[str, list[str]]]) -> str | None:
        if not rows:
            return "Enter the agent script."
        seen: set[str] = set()
        for key, values in rows:
            if not key:
                value = next((v for v in values if v), "")
                return f'Enter a key for the value "{value}".'
            if not any(values):
                return f'Enter a value for the key "{key}".'
            if key in seen:
                return f'Duplicate key "{key}".'
            seen.add(key)
        return None

    @staticmethod
    def _validate_script(script: str, fmt: str) -> str | None:
        if not script:
            return "Enter the agent script."
        if len(script) > _MAX_SCRIPT:
            return f"Script is too long (at most {_MAX_SCRIPT:,} characters)."
        if fmt == "json":
            # The editor is hand-editable; a broken edit must not reach the
            # backend (which would 400 on the same check).
            try:
                json.loads(script)
            except ValueError as exc:
                return f"Script is not valid JSON: {exc}"
        return None

    # -- submission -----------------------------------------------------------

    def _submit(self) -> None:
        if self._busy:
            return
        name = self._name_edit.text().strip()
        name_error = self._validate_name(name)
        if name_error:
            self._show_error(name_error)
            return
        fmt = self._format_combo.currentData()
        # The editor is the source of truth in every format: json rows are a
        # builder whose output reaches the script only via Generate JSON.
        script = self._script_edit.toPlainText()
        error = self._validate_script(script, fmt)
        if error:
            self._show_error(error)
            return
        self._clear_error()
        self._set_busy(True)
        if self._is_edit and self._agent is not None:
            self._session.update_agent(
                self._agent.id,
                name,
                fmt,
                script,
                on_success=lambda _agent: self._on_success(),
                on_error=lambda exc: self._on_error(exc),
            )
        else:
            self._session.create_agent(
                name,
                fmt,
                script,
                on_success=lambda _agent: self._on_success(),
                on_error=lambda exc: self._on_error(exc),
            )

    def _on_success(self) -> None:
        # Close immediately: the reloaded table row behind the dialog is the
        # feedback (unlike ChangePasswordDialog, whose effect is invisible).
        self.accept()

    def _on_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if self._session.user is None:
            # Forced logout happened mid-call; MainWindow moved to Login.
            self.reject()
            return
        self._show_error(str(exc))

    # -- validation (mirrors the backend rules) ---------------------------------

    @staticmethod
    def _validate_name(name: str) -> str | None:
        if not name:
            return "Enter a name."
        if len(name) > _MAX_NAME:
            return f"Name must be at most {_MAX_NAME} characters."
        return None

    # -- state -----------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        widgets = [
            self._name_edit,
            self._format_combo,
            self._script_edit,
            self._add_key_button,
            self._generate_button,
            self._cancel_button,
            self._submit_button,
        ]
        # findChildren covers every dynamic row edit and plus/minus button.
        widgets += self._kv_container.findChildren(QLineEdit)
        widgets += self._kv_container.findChildren(QPushButton)
        for widget in widgets:
            widget.setEnabled(not busy)
        if busy:
            self._submit_button.setText("Saving…" if self._is_edit else "Adding…")
        else:
            self._submit_button.setText("Save changes" if self._is_edit else "Add agent")

    def reject(self) -> None:
        if self._busy:
            return  # A submission is in flight; the timeout bounds the wait.
        super().reject()

    def _show_error(self, message: str) -> None:
        self._error_banner.setText(message)
        self._error_banner.show()

    def _clear_error(self) -> None:
        self._error_banner.hide()
