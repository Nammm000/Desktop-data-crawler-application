"""Run blocking work on the global thread pool and deliver results to the GUI thread."""

from __future__ import annotations

from typing import Callable, TypeVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

T = TypeVar("T")

# Signals objects awaiting delivery of their queued result on the GUI thread.
_PENDING: set[_TaskSignals] = set()


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
    # Hold the signals object alive until its queued emission has been
    # delivered: once the pool deletes the finished runnable, its Python
    # reference would be the last one — and dropping it on the pool thread
    # would destroy the QObject while the GUI thread is delivering to it.
    _PENDING.add(signals)

    def _release() -> None:
        _PENDING.discard(signals)

    signals.finished.connect(on_success)
    signals.finished.connect(_release)  # runs after on_success (connect order)
    signals.failed.connect(on_error)
    signals.failed.connect(_release)
    QThreadPool.globalInstance().start(_Task(fn, signals))
