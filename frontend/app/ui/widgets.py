"""Reusable input widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QFrame, QLineEdit, QScrollArea, QWidget

_ICONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"


class PasswordLineEdit(QLineEdit):
    """Password field with a trailing eye / eye-slash action to show or hide it."""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._visible = False
        self._action = self.addAction(
            self._icon("eye.svg"), QLineEdit.ActionPosition.TrailingPosition
        )
        self._action.setToolTip("Show password")
        self._action.triggered.connect(self._toggle_visibility)

    @staticmethod
    def _icon(name: str) -> QIcon:
        return QIcon(str(_ICONS_DIR / name))

    def _toggle_visibility(self) -> None:
        self._visible = not self._visible
        self.setEchoMode(
            QLineEdit.EchoMode.Normal if self._visible else QLineEdit.EchoMode.Password
        )
        self._action.setIcon(self._icon("eye-off.svg" if self._visible else "eye.svg"))
        self._action.setToolTip("Hide password" if self._visible else "Show password")


class _ScrollIndicator(QWidget):
    """The overlay scrollbar's thumb: a rounded translucent pill, click-through."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scrollIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#1F2430")
        color.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 3.5, 3.5
        )


class OverlayScrollArea(QScrollArea):
    """Scroll area with hidden native scrollbars and a macOS-style overlay
    indicator that appears only while scrolling."""

    _WIDTH = 8  # indicator width, px
    _MARGIN = 4  # inset from the top/bottom/right edges, px
    _MIN_THUMB = 24  # shortest thumb, px
    _HIDE_DELAY_MS = 800

    def __init__(self, parent=None, objectName=None):
        super().__init__(parent)
        if objectName is not None:
            self.setObjectName(objectName)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._indicator = _ScrollIndicator(self)
        self._indicator.raise_()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self._HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._indicator.hide)

        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll)
        bar.rangeChanged.connect(self._on_range)

    def wheelEvent(self, event) -> None:
        super().wheelEvent(event)
        if self.verticalScrollBar().maximum() > 0:
            # Reveal after super(): valueChanged already revealed with fresh
            # geometry; this covers wheeling at min/max where it can't change.
            self._reveal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_indicator()

    def _on_scroll(self, value: int) -> None:
        if self.verticalScrollBar().maximum() > 0:
            self._reveal()

    def _on_range(self, minimum: int, maximum: int) -> None:
        if maximum == 0:
            # Content fits again (window grew / banners cleared): never leave
            # a stale indicator on screen.
            self._hide_timer.stop()
            self._indicator.hide()
        self._update_indicator()

    def _reveal(self) -> None:
        self._update_indicator()
        self._indicator.show()
        self._indicator.raise_()
        self._hide_timer.start()

    def _update_indicator(self) -> None:
        bar = self.verticalScrollBar()
        track = self.viewport().height() - 2 * self._MARGIN
        content = bar.maximum() + bar.pageStep()
        if track <= 0 or content <= 0 or bar.maximum() <= 0:
            return
        thumb = max(
            self._MIN_THUMB, min(round(track * bar.pageStep() / content), track)
        )
        y = self._MARGIN + round((track - thumb) * bar.value() / bar.maximum())
        self._indicator.setGeometry(
            self.width() - self._MARGIN - self._WIDTH, y, self._WIDTH, thumb
        )
