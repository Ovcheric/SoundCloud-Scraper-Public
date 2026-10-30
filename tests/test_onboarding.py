"""
Tests for a first launch: the connect window, the guide, and importing likes.

Real widgets on the session-wide Tk root (see conftest.py). Nothing here
reaches SoundCloud: sign-in and the API are handed in as fakes, and run() -
which blocks until a person closes the window - is never called.
"""

import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data import db, tracks as track_store  # noqa: E402
from ui import onboarding  # noqa: E402
from ui.sidebar import Sidebar  # noqa: E402
from ui.task import TaskRunner  # noqa: E402
from ui.tabs import cleanup as cleanup_tab  # noqa: E402

HOUR = 60 * 60 * 1000


def pump(root, until, seconds=5.0):
    """Run the event loop until `until()` holds - workers report via after()."""
    end = time.time() + seconds
    while not until() and time.time() < end:
        root.update()
        time.sleep(0.01)
    assert until(), "timed out"


def sign_in(**overrides):
    calls = {"saved": [], "connected": 0}

    def connect():
        calls["connected"] += 1
        if overrides.get("fail"):
            raise RuntimeError(overrides["fail"])

    details = onboarding.SignIn(
        save=lambda client_id, secret: calls["saved"].append((client_id, secret)),
        connect=connect, redirect_uri="http://localhost:8080/callback",
        apps_page="https://soundcloud.com/you/apps",
        needs_app=overrides.get("needs_app", True))
    return details, calls


def test_connect_asks_for_both_halves_of_the_app(tk_root):
    details, calls = sign_in()
    dialog = onboarding._ConnectDialog(tk_root, details)
    dialog._client_id.insert(0, "abc")
    dialog._connect()
    assert calls["saved"] == [] and calls["connected"] == 0
    assert "client secret" in dialog._status.cget("text")
    dialog.destroy()


def test_connect_saves_the_app_then_signs_in(tk_root):
    details, calls = sign_in()
    dialog = onboarding._ConnectDialog(tk_root, details)
    dialog._client_id.insert(0, "  abc ")
    dialog._secret.insert(0, "shh")
    dialog._connect()
    pump(tk_root, lambda: dialog.result is not None)
    assert calls["saved"] == [("abc", "shh")]
    assert calls["connected"] == 1
    assert dialog.result is True


def test_a_failed_sign_in_says_why_and_can_be_retried(tk_root):
    details, calls = sign_in(needs_app=False, fail="Port 8080 is busy")
    dialog = onboarding._ConnectDialog(tk_root, details)
    dialog._connect()
    pump(tk_root, lambda: "8080" in dialog._status.cget("text"))
    assert calls["saved"] == []                  # nothing to save on re-auth
    assert str(dialog._connect_button.cget("state")) == "normal"
    dialog.destroy()


def test_the_guide_can_hand_off_to_importing(tk_root):
    guide = onboarding._GuideDialog(tk_root)
    guide._finish("import")
    assert guide.result == "import"


def test_the_sidebar_offers_the_guide(tk_root):
    asked = []
    bar = Sidebar(tk_root, [("", "Add Sets")], on_select=lambda i: None,
                  on_help=lambda: asked.append(True))
    help_item = next(w for w in bar.winfo_children()
                     if getattr(w, "_command", None) and w._label.cget(
                         "text") == "How it works")
    help_item._command()
    assert asked == [True]
    bar.destroy()


# ---------------------------------------------------------------------------
# Import liked sets, from the Clean Up screen
# ---------------------------------------------------------------------------

class FakeApi:
    def liked_tracks(self):
        return [
            {"id": 1, "title": "Nova Ember @ Lost Lands 2026", "duration": HOUR,
             "user": {"username": "Nova Ember"},
             "created_at": "2026/09/01 00:00:00 +0000"},
            {"id": 2, "title": "A three-minute song", "duration": 3 * 60000,
             "user": {"username": "Nova Ember"},
             "created_at": "2026/09/01 00:00:00 +0000"},
        ]


class FakeApp:
    """Enough App for the screen: a runner, a library, a fake SoundCloud."""

    def __init__(self, root, conn):
        self.conn = conn
        self.runner = TaskRunner(root)
        self.runner.start()
        self.refreshed = 0
        self.taste_tab = self

    @contextmanager
    def db(self):
        yield self.conn
        self.conn.commit()

    def api(self):
        return FakeApi()

    def refresh_title(self):
        self.refreshed += 1

    def refresh(self):
        """Stands in for taste_tab.refresh()."""


def shared_library() -> sqlite3.Connection:
    """
    One in-memory library the worker thread may use too. The app never shares
    a connection (see App.db); a test does, so it can read what was written.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db.initialize(conn)
    return conn


@pytest.fixture
def screen(tk_root):
    app = FakeApp(tk_root, shared_library())
    tab = cleanup_tab.CleanupTab(tk_root, app)
    yield tab, app
    app.runner.stop()
    tab.destroy()
    app.conn.close()


def status(tab) -> str:
    return tab._status._text.cget("text")


def test_import_brings_in_liked_sets_as_kept(screen, tk_root, monkeypatch):
    tab, app = screen
    asked = []
    monkeypatch.setattr(cleanup_tab, "ask_yes_no",
                        lambda *a, **k: asked.append(a[2]) or True)
    tab.import_likes()
    pump(tk_root, lambda: "Imported" in status(tab))
    assert "1 set(s)" in asked[0]                # the song is not a set
    assert track_store.verdict_counts(app.conn) == {"liked": 1}
    assert app.refreshed == 1


def test_declining_the_import_writes_nothing(screen, tk_root, monkeypatch):
    tab, app = screen
    monkeypatch.setattr(cleanup_tab, "ask_yes_no", lambda *a, **k: False)
    tab.import_likes()
    pump(tk_root, lambda: "Nothing imported" in status(tab))
    assert track_store.verdict_counts(app.conn) == {}
