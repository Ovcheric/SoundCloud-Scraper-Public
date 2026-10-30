"""
Tests for src/ui/task.py.

No window is created. A fake widget captures the after() callbacks so the
polling loop can be stepped by hand, which makes the threading deterministic.
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ui.task import TaskRunner  # noqa: E402


class FakeWidget:
    """Stands in for a Tk widget: records after() callbacks instead of timing."""

    def __init__(self):
        self.pending = []

    def after(self, _ms, callback):
        self.pending.append(callback)

    def tick(self, times=1):
        """Run the currently-scheduled callback, `times` times."""
        for _ in range(times):
            if not self.pending:
                return
            self.pending.pop(0)()


@pytest.fixture
def runner():
    widget = FakeWidget()
    r = TaskRunner(widget, poll_interval_ms=0)
    r.start()
    return r, widget


def wait_for(predicate, timeout=2.0):
    done = threading.Event()
    deadline = threading.Timer(timeout, done.set)
    deadline.start()
    try:
        while not predicate() and not done.is_set():
            pass
        return predicate()
    finally:
        deadline.cancel()


def test_result_is_delivered(runner):
    r, widget = runner
    results = []
    r.run(lambda report: 6 * 7, on_done=results.append, on_error=results.append)
    assert wait_for(lambda: not r._queue.empty())
    widget.tick()
    assert results == [42]


def test_an_exception_goes_to_on_error_not_on_done(runner):
    """A worker must never be able to kill the window."""
    r, widget = runner
    done, errors = [], []

    def explode(report):
        raise ValueError("nope")

    r.run(explode, on_done=done.append, on_error=errors.append)
    assert wait_for(lambda: not r._queue.empty())
    widget.tick()
    assert done == []
    assert isinstance(errors[0], ValueError)


def test_progress_is_reported_before_the_result(runner):
    r, widget = runner
    seen, results = [], []

    def work(report):
        report("step one")
        report("step two")
        return "finished"

    r.run(work, on_done=results.append, on_error=results.append,
          on_progress=seen.append)
    assert wait_for(lambda: r._queue.qsize() >= 3)
    widget.tick()
    assert seen == ["step one", "step two"]
    assert results == ["finished"]


def test_a_cancelled_task_delivers_nothing(runner):
    r, widget = runner
    results = []
    task = r.run(lambda report: "late", on_done=results.append,
                 on_error=results.append)
    task.cancel()
    assert wait_for(lambda: not r._queue.empty())
    widget.tick()
    assert results == []


def test_work_without_a_progress_handler_still_completes(runner):
    """Progress is optional; reporting into nowhere must not raise."""
    r, widget = runner
    results = []

    def work(report):
        report("ignored")
        return "ok"

    r.run(work, on_done=results.append, on_error=results.append)
    assert wait_for(lambda: r._queue.qsize() >= 2)
    widget.tick()
    assert results == ["ok"]


def test_handlers_are_released_once_a_task_finishes(runner):
    """Otherwise every run leaks its callbacks for the life of the window."""
    r, widget = runner
    r.run(lambda report: None, on_done=lambda _: None, on_error=lambda _: None)
    assert wait_for(lambda: not r._queue.empty())
    widget.tick()
    assert r._handlers == {}


def test_two_runners_do_not_share_handlers():
    """A class-level dict would let one window's callbacks fire for another."""
    a = TaskRunner(FakeWidget())
    b = TaskRunner(FakeWidget())
    a.run(lambda report: None, on_done=lambda _: None, on_error=lambda _: None)
    assert len(a._handlers) == 1
    assert b._handlers == {}


def test_polling_stops_when_asked(runner):
    r, widget = runner
    widget.pending.clear()
    r.stop()
    r._drain()
    assert widget.pending == []
