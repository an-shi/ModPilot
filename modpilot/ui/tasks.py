from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from ..i18n import tr

log = logging.getLogger(__name__)


class TaskHandle(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(self, task_id: str, title: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.task_id = task_id
        self.title = title


class _Worker(QRunnable):
    def __init__(self, handle: TaskHandle, fn: Callable[..., Any], with_progress: bool) -> None:
        super().__init__()
        self.handle = handle
        self.fn = fn
        self.with_progress = with_progress
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if self.with_progress:
                result = self.fn(self.handle.progress.emit)
            else:
                result = self.fn()
        except Exception as exc:
            log.exception(tr('ui.tasks.background_task_failed_s'), self.handle.title)
            message = str(exc) or exc.__class__.__name__
            self.handle.failed.emit(message, traceback.format_exc())
        else:
            self.handle.finished.emit(result)


class TaskManager(QObject):
    """Run blocking service calls off the UI thread.

    Callbacks are invoked on the UI thread because they are connected to Qt
    signals emitted by a QObject that lives in the main thread.
    """

    count_changed = Signal(int)
    unhandled_error = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self._handles: dict[str, TaskHandle] = {}
        self._counter = 0

    def run(
        self,
        title: str,
        fn: Callable[..., Any],
        *,
        with_progress: bool = False,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> TaskHandle:
        self._counter += 1
        task_id = f"task-{self._counter}"
        handle = TaskHandle(task_id, title, self)
        self._handles[task_id] = handle
        if on_progress:
            handle.progress.connect(on_progress)
        if on_done:
            handle.finished.connect(on_done)
        handle.failed.connect(
            lambda message, details: self._failed(handle, message, details, on_error)
        )
        handle.finished.connect(lambda _result: self._done(handle))
        handle.failed.connect(lambda _message, _details: self._done(handle))
        self.count_changed.emit(len(self._handles))
        self.pool.start(_Worker(handle, fn, with_progress))
        return handle

    def _done(self, handle: TaskHandle) -> None:
        self._handles.pop(handle.task_id, None)
        self.count_changed.emit(len(self._handles))
        handle.deleteLater()

    def _failed(
        self,
        handle: TaskHandle,
        message: str,
        details: str,
        on_error: Callable[[str], None] | None,
    ) -> None:
        if on_error:
            on_error(message)
        else:
            self.unhandled_error.emit(handle.title, message)

    @property
    def active_count(self) -> int:
        return len(self._handles)

    def shutdown(self, timeout_ms: int = 2000) -> None:
        self.pool.waitForDone(timeout_ms)
