"""
Tests for the Find screen: what the queue shows, and removing and fixing rows.

A real FindTab on the session-wide Tk root (see conftest.py), with plans
handed in directly - no network. Removal writes to a real in-memory database,
because "the reason is remembered" is the promise being checked.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data import db, dismissals  # noqa: E402
from services import discovery, finder  # noqa: E402
from ui.tabs import find as find_tab  # noqa: E402


class FakeApp:
    """Just enough App for the screen: one in-memory database."""

    def __init__(self):
        self.conn = db.connect(":memory:")

    @contextmanager
    def db(self):
        yield self.conn
        self.conn.commit()


def row(track_id, title, artist="Wooli"):
    return {"track_id": track_id, "title": title, "real_artists": artist,
            "uploader": artist, "duration_ms": 60 * 60 * 1000}


def plan_of(*rows, levels=None, scores=None):
    plan = finder.FindPlan(requests=3, seen=len(rows))
    plan.queue.to_add = list(rows)
    plan.queue.confidence = {r["track_id"]: (levels or {}).get(
        r["track_id"], discovery.CERTAIN) for r in rows}
    plan.scores = dict(scores or {})
    plan.found_via = {r["track_id"]: f"found {r['track_id']}" for r in rows}
    return plan


@pytest.fixture(scope="module")
def tab(tk_root):
    return find_tab.FindTab(tk_root, FakeApp())


def show(tab, plan):
    tab._plan = plan
    tab._refresh()


def test_each_row_says_why_it_was_found(tab):
    show(tab, plan_of(row("1", "Wooli @ Lost Lands 2026")))
    assert tab._tree.item("1", "values")[3] == "found 1"


def test_unsure_artists_first_then_the_strongest_finds(tab):
    show(tab, plan_of(row("1", "a"), row("2", "b"), row("3", "c"),
                      levels={"3": discovery.GUESS}, scores={"1": 1.0, "2": 5.0}))
    assert list(tab._tree.get_children()) == ["3", "2", "1"]


def test_the_queue_can_only_be_added_when_there_is_one(tab):
    show(tab, finder.FindPlan())
    assert str(tab._add_button.cget("state")) == "disabled"
    show(tab, plan_of(row("1", "a")))
    assert str(tab._add_button.cget("state")) == "normal"
    tab._set_busy(True)
    assert str(tab._add_button.cget("state")) == "disabled"
    tab._set_busy(False)


def test_removing_selected_rows_records_the_reason(tab, monkeypatch):
    monkeypatch.setattr(find_tab, "ask_dismissal",
                        lambda *a, **kw: ("style", "too melodic"))
    show(tab, plan_of(row("1", "a"), row("2", "b"), row("3", "c")))
    tab._tree.selection_set(("1", "3"))
    tab._on_remove()
    assert list(tab._tree.get_children()) == ["2"]
    assert {"1", "3"} <= dismissals.dismissed_ids(tab._app.conn)


def test_cancelling_a_removal_changes_nothing(tab, monkeypatch):
    monkeypatch.setattr(find_tab, "ask_dismissal", lambda *a, **kw: None)
    show(tab, plan_of(row("5", "a")))
    tab._tree.selection_set(("5",))
    tab._on_remove()
    assert list(tab._tree.get_children()) == ["5"]


def test_the_dialog_names_every_performer_once(tab, monkeypatch):
    """So 'not into this artist' says exactly who it will leave out."""
    seen = {}

    def capture(_parent, _count, _reasons, _learns, performers, _not_now):
        seen["performers"] = performers
        return None

    monkeypatch.setattr(find_tab, "ask_dismissal", capture)
    show(tab, plan_of(row("6", "a", "Wooli, Crankdat"), row("7", "b", "Wooli")))
    tab._tree.selection_set(("6", "7"))
    tab._on_remove()
    assert seen["performers"] == ["Wooli", "Crankdat"]


def test_a_corrected_artist_shows_and_turns_green(tab, monkeypatch):
    monkeypatch.setattr(find_tab, "ask_artist", lambda *a, **kw: "Wooli, LAYZ")
    show(tab, plan_of(row("8", "Garbled title", "Garbled"),
                      levels={"8": discovery.GUESS}))
    tab._tree.focus("8")
    tab._on_edit_artist()
    assert tab._plan.queue.corrections["8"] == "Wooli, LAYZ"
    assert tab._tree.item("8", "tags") == ("fixed",)
    assert tab._tree.item("8", "values")[0] == "Wooli, LAYZ"


def test_the_panel_starts_from_sensible_defaults(tab):
    options = tab._panel.options()
    assert options.sources == frozenset(finder.SOURCES)
    assert options.filters.min_minutes == 20
    assert options.filters.max_results == 30
    assert "podcast" in options.filters.exclude_words


def test_new_controls_reach_the_search(tab):
    options = tab._panel.options()
    assert options.filters.max_known_sets is None       # default: any artist
    assert options.depth == "normal"
    assert "genres" in options.sources
    tab._panel._new_to_you.set("Never")
    tab._panel._depth.set("Deep")
    options = tab._panel.options()
    assert options.filters.max_known_sets == 0
    assert options.depth == "deep"
    tab._panel._new_to_you.set("Any")
    tab._panel._depth.set("Normal")


def test_a_short_search_says_how_short_and_what_to_widen():
    found = plan_of(*[row(str(i), f"t{i}") for i in range(19)])
    found.asked = 50
    message = find_tab.FindTab._found_message(found)
    assert "19 of the 50" in message and "Deep" in message


def test_the_notes_say_which_sources_filled_the_queue(tab):
    plan = plan_of(row("1", "a"))
    plan.queued_by_source.update({"similar": 12, "genres": 7})
    show(tab, plan)
    assert "Queued from: Similar sets 12  ·  Your genres 7" in tab._notes.cget("text")
