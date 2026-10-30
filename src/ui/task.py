"""
Run slow work off the UI thread, and deliver the result back onto it.

Write this once. The old app repeated the same chain - phase1_worker,
on_tracks_ready, on_add, phase2_worker, on_done, on_cancel, on_error - six
times, once per tab, which was roughly 800 lines and most of its bug surface.

Tkinter may only be touched from the thread that made the window, so a worker
never calls a callback directly: it posts to a queue that the UI thread drains
on a timer.

Public: TaskRunner, Task

Imports nothing from src/. Knows about threads and tkinter, nothing else.
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable

POLL_INTERVAL_MS = 80


@dataclass(eq=False)  # identity, not value: tasks are used as dict keys
class Task:
    """A handle on running work. Cancelling only ignores the result."""

    name: str
    _cancelled: bool = False

    def cancel(self) -> None:
        """
        Stop delivering callbacks for this task.

        The worker keeps running - Python cannot safely kill a thread mid-HTTP
        call - so this means "I am no longer interested", not "it stopped".
        Anything already written stays written.
        """
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class TaskRunner:
    """
    Owns one queue and one polling loop for a window.

    Create it once, next to the root window, and share it across tabs.
    """

    def __init__(self, widget, *, poll_interval_ms: int = POLL_INTERVAL_MS):
        self._widget = widget
        self._queue: queue.Queue = queue.Queue()
        self._poll_interval_ms = poll_interval_ms
        self._running = False
        # Per instance, not per class: a shared dict would let one window's
        # callbacks fire for another window's task.
        self._handlers: dict = {}

    # -- public --------------------------------------------------------------

    def start(self) -> None:
        """Begin draining the queue. Call once, after the window exists."""
        if not self._running:
            self._running = True
            self._widget.after(self._poll_interval_ms, self._drain)

    def stop(self) -> None:
        self._running = False

    def run(
        self,
        work: Callable[..., Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        on_progress: Callable[[str], None] | None = None,
        name: str = "task",
    ) -> Task:
        """
        Run `work` in a thread and report back on the UI thread.

        `work` is called with a single `report` argument - a function taking a
        status string - so it can describe what it is doing without knowing
        anything about widgets. Exceptions are caught and routed to on_error;
        a worker must never be able to kill the window.
        """
        task = Task(name=name)

        def report(message: str) -> None:
            self._queue.put((task, "progress", message))

        def runner() -> None:
            try:
                self._queue.put((task, "done", work(report)))
            except Exception as exc:  # noqa: BLE001 - boundary: nothing may escape
                self._queue.put((task, "error", exc))

        self._handlers[task] = (on_done, on_error, on_progress)
        threading.Thread(target=runner, name=name, daemon=True).start()
        return task

    # -- internals -----------------------------------------------------------

    def _drain(self) -> None:
        """Deliver everything queued since the last tick, on the UI thread."""
        while True:
            try:
                task, kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            handlers = self._handlers.get(task)
            if handlers is None or task.cancelled:
                continue
            on_done, on_error, on_progress = handlers

            if kind == "progress":
                if on_progress is not None:
                    on_progress(payload)
                continue

            self._handlers.pop(task, None)
            if kind == "done":
                on_done(payload)
            else:
                traceback.print_exception(
                    type(payload), payload, payload.__traceback__)
                on_error(payload)

        if self._running:
            self._widget.after(self._poll_interval_ms, self._drain)
