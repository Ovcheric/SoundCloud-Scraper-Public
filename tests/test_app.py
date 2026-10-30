"""
Tests for src/ui/app.py's startup safety net.

run.bat launches the app with pythonw.exe, which has no console. An unhandled
exception there would vanish and the app would just appear not to start, so
the crash path has to work even when everything else does not.

No window is created: the dialog is stubbed out.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ui import app as app_module  # noqa: E402


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Redirect the crash log and capture the dialog instead of showing it."""
    shown = []
    monkeypatch.setattr(app_module, "CRASH_LOG", tmp_path / "crash.log")
    monkeypatch.setattr(
        app_module.messagebox, "showerror",
        lambda title, message, **kw: shown.append((title, message)))
    return shown, tmp_path / "crash.log"


def test_a_crash_is_written_down_and_shown(captured):
    shown, log = captured
    app_module._report_crash(None, ValueError("database is locked"))

    assert log.exists()
    written = log.read_text(encoding="utf-8")
    assert "ValueError" in written
    assert "database is locked" in written

    title, message = shown[0]
    assert "could not start" in title
    assert "database is locked" in message
    assert "crash.log" in message


def test_the_traceback_is_kept_not_just_the_message(captured):
    """Without the traceback the log cannot say where it broke."""
    _, log = captured
    try:
        raise KeyError("missing")
    except KeyError as exc:
        app_module._report_crash(None, exc)
    assert "Traceback" in log.read_text(encoding="utf-8")


def test_the_dialog_still_appears_when_the_log_cannot_be_written(
    monkeypatch, captured
):
    """A read-only folder must not turn a crash report into a second crash."""
    shown, log = captured

    def refuse(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "write_text", refuse)
    app_module._report_crash(None, ValueError("boom"))

    assert not log.exists()
    title, message = shown[0]
    assert "boom" in message
    assert "crash.log" not in message  # nothing to point at


# ---------------------------------------------------------------------------
# Connections and threads
# ---------------------------------------------------------------------------
# Every other test calls the services directly, on one thread, so all 1951
# passed while the real app failed on the first click: the pipelines run on
# worker threads and SQLite objects belong to the thread that made them.

import sqlite3      # noqa: E402
import threading    # noqa: E402

from data import db  # noqa: E402


def run_in_thread(fn):
    """Call fn on a worker thread; return (result, error)."""
    box = {}

    def target():
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001 - the point is to capture it
            box["error"] = exc

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(timeout=10)
    return box.get("result"), box.get("error")


def test_a_sqlite_connection_cannot_cross_threads(tmp_path):
    """The constraint this whole design works around. Pinned so it stays true."""
    conn = db.connect(tmp_path / "x.db")
    _, error = run_in_thread(lambda: conn.execute("SELECT 1").fetchone())
    assert isinstance(error, sqlite3.ProgrammingError)
    assert "same thread" in str(error)
    conn.close()


@pytest.fixture(scope="module")
def running_app(tmp_path_factory, tk_root):
    """
    One real App on the session-wide Tk root from conftest.

    Tk does not reliably tolerate several roots being created and destroyed in
    a single process, which makes per-test roots flaky. These tests only
    exercise App.db(), so one instance is enough.
    """
    path = tmp_path_factory.mktemp("appdb") / "app.db"
    return app_module.App(tk_root, lambda: db.connect(path))


def test_app_db_can_be_used_from_a_worker_thread(running_app):
    """
    What the Add screen actually does. A shared connection raised
    "SQLite objects created in a thread can only be used in that same thread"
    the moment a playlist URL was pasted.
    """
    def work():
        with running_app.db() as conn:
            conn.execute(
                "INSERT INTO tracks (track_id, title, first_seen_at) "
                "VALUES ('1', 't', '2026-01-01')")
            return conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    result, error = run_in_thread(work)
    assert error is None, error
    assert result == 1

    # committed on the way out, so a later connection sees it
    with running_app.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1


def test_the_sidebar_shows_one_screen_at_a_time(running_app):
    """Hidden screens are unpacked, not destroyed, so they keep their state."""
    screens = (running_app.add_tab, running_app.find_tab,
               running_app.cleanup_tab, running_app.taste_tab)
    for index in range(len(screens)):
        running_app.show(index)
        shown = [s.winfo_manager() == "pack" for s in screens]
        assert shown == [i == index for i in range(len(screens))]
    running_app.show(0)


def test_app_db_closes_its_connection_even_when_the_work_raises(running_app):
    """A failed Add must not leak a connection per attempt."""
    captured = {}
    with pytest.raises(ValueError):
        with running_app.db() as conn:
            captured["conn"] = conn
            raise ValueError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")
