"""Run blocking work on the global thread pool and deliver results to the GUI thread."""

from __future__ import annotations

from typing import Callable, TypeVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

T = TypeVar("T")


class _TaskSignals(QObject):
    finished = Signal(object)  # result value
    failed = Signal(object)  # exception


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], object], signals: _TaskSignals):
        super().__init__()
        self._fn = fn
        self._signals = signals

    def run(self) -> None:  # pool thread
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - forwarded to the GUI thread
            self._signals.failed.emit(exc)
        else:
            self._signals.finished.emit(result)


def run_async(
    fn: Callable[[], T],
    on_success: Callable[[T], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Run blocking fn() off the GUI thread; call on_success/on_error on the GUI thread.

    The signals QObject is created on the calling (GUI) thread, so the
    auto-connections resolve to queued connections and the slots execute there.
    """
    signals = _TaskSignals()
    signals.finished.connect(on_success)
    signals.failed.connect(on_error)
    # The runnable owns the signals object for as long as it runs, so the
    # local reference going out of scope here is safe.
    QThreadPool.globalInstance().start(_Task(fn, signals))
