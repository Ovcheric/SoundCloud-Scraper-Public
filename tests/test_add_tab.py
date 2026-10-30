"""
Tests for the Add screen's confidence colouring, and the parts of its dark
theme that carry meaning: readable row colours, the empty-table hint, and
buttons that look disabled when they are.

These build a real Treeview, because the thing being checked is what the
widget ends up holding - a tag that no style defines, or a sort that quietly
falls back to alphabetical, both look fine in the code and wrong on screen.

No network and no database: _on_checked() is handed a plan directly, which is
all the rendering depends on.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from services import discovery            # noqa: E402
from ui import confidence, theme          # noqa: E402
from ui.tabs import add as add_tab        # noqa: E402


def track(track_id, title, artist="Someone", minutes=60):
    return {
        "track_id": track_id,
        "title": title,
        "real_artists": artist,
        "duration_ms": minutes * 60_000,
    }


def plan_of(*rows):
    """An AddPlan from (track, confidence) pairs."""
    return discovery.AddPlan(
        playlist_title="A playlist",
        to_add=[r for r, _ in rows],
        confidence={r["track_id"]: level for r, level in rows},
    )


@pytest.fixture(scope="module")
def tab(tk_root):
    """
    One tab, built on the session-wide root from conftest.

    Making a root here instead broke tests/test_app.py, which makes one of its
    own - Tk does not tolerate several roots in a process. See conftest.py.
    """
    return add_tab.AddTab(tk_root, app=object())


# ---------------------------------------------------------------------------
# Colouring
# ---------------------------------------------------------------------------

def test_every_confidence_level_has_a_colour():
    """
    Adding a level in core/ and forgetting the UI would render an untagged,
    invisible row. Pinned here so that cannot ship silently.
    """
    for level in discovery.CONFIDENCE_ORDER:
        assert level in confidence.CONFIDENCE
        label, colour, _tint, explanation = confidence.CONFIDENCE[level]
        assert label and explanation
        assert colour.startswith("#")


def test_each_row_is_tagged_with_its_confidence(tab):
    tab._on_checked(plan_of(
        (track("1", "Clean"), discovery.CERTAIN),
        (track("2", "Parsed"), discovery.LIKELY),
        (track("3", "Garbled"), discovery.GUESS),
    ))
    assert tab._tree.item("1", "tags") == ("certain",)
    assert tab._tree.item("2", "tags") == ("likely",)
    assert tab._tree.item("3", "tags") == ("guess",)


def test_the_tags_actually_carry_a_colour(tab):
    """tag_configure is silent about a tag nobody defined."""
    tab._on_checked(plan_of((track("1", "Clean"), discovery.CERTAIN)))
    for level in discovery.CONFIDENCE_ORDER:
        assert str(tab._tree.tag_configure(level, "foreground")) == \
            confidence.CONFIDENCE[level][1]


def _contrast(foreground, background):
    """WCAG contrast ratio between two #rrggbb colours."""
    def luminance(colour):
        rgb = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                   for c in rgb]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_row_colour_is_readable_on_the_dark_table():
    """
    The colours are the whole point of the preview, so a palette edit that
    leaves one level dim on its tint would hide exactly the rows to fix.
    4.5 is the WCAG bar for body text.
    """
    for level in discovery.CONFIDENCE_ORDER:
        _, colour, tint, _ = confidence.CONFIDENCE[level]
        assert _contrast(colour, tint or theme.SURFACE) >= 4.5, level
    assert _contrast(*confidence.FIXED) >= 4.5


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

def test_the_least_trustworthy_rows_come_first(tab):
    """
    The colours only save time if the suspect rows are at the top. Titles are
    deliberately reverse-alphabetical to the confidence order, so an
    alphabetical fallback would fail this.
    """
    tab._on_checked(plan_of(
        (track("1", "AAA certain"), discovery.CERTAIN),
        (track("2", "BBB likely"), discovery.LIKELY),
        (track("3", "CCC unsure"), discovery.UNSURE),
        (track("4", "DDD guess"), discovery.GUESS),
    ))
    assert list(tab._tree.get_children()) == ["4", "3", "2", "1"]


def test_rows_of_equal_confidence_stay_alphabetical(tab):
    tab._on_checked(plan_of(
        (track("1", "Zebra"), discovery.CERTAIN),
        (track("2", "apple"), discovery.CERTAIN),
    ))
    assert list(tab._tree.get_children()) == ["2", "1"]


def test_an_unknown_track_id_sorts_last_rather_than_crashing(tab):
    """A plan missing a confidence entry must still render."""
    plan = discovery.AddPlan(to_add=[track("9", "Orphan")], confidence={})
    tab._on_checked(plan)
    assert list(tab._tree.get_children()) == ["9"]
    assert tab._tree.item("9", "tags") == ("certain",)


# ---------------------------------------------------------------------------
# Legend
# ---------------------------------------------------------------------------

def test_the_legend_counts_each_level(tab):
    tab._on_checked(plan_of(
        (track("1", "a"), discovery.CERTAIN),
        (track("2", "b"), discovery.CERTAIN),
        (track("3", "c"), discovery.GUESS),
    ))
    assert "2 uploader's own name" in tab._legend_labels[discovery.CERTAIN].cget("text")
    assert "1 couldn't parse" in tab._legend_labels[discovery.GUESS].cget("text")


def test_a_level_with_nothing_in_it_is_hidden(tab):
    """A row of zeroes is noise on a run where everything parsed cleanly."""
    tab._on_checked(plan_of((track("1", "a"), discovery.CERTAIN)))
    assert tab._legend_labels[discovery.CERTAIN].winfo_manager() == "pack"
    assert tab._legend_labels[discovery.GUESS].winfo_manager() == ""


def test_clearing_empties_the_legend(tab):
    tab._on_checked(plan_of((track("1", "a"), discovery.GUESS)))
    tab._reset()
    for label in tab._legend_labels.values():
        assert label.winfo_manager() == ""


# ---------------------------------------------------------------------------
# Empty table and buttons
# ---------------------------------------------------------------------------

def test_the_empty_table_explains_itself_until_a_plan_arrives(tab):
    tab._reset()
    assert tab._tree._hint.winfo_manager() == "place"
    tab._on_checked(plan_of((track("1", "a"), discovery.CERTAIN)))
    assert tab._tree._hint.winfo_manager() == ""
    tab._reset()
    assert tab._tree._hint.winfo_manager() == "place"


def test_a_disabled_button_drops_its_accent(tab):
    """
    CTk only greys the label of a disabled button and keeps its fill, so an
    orange "Add to Discovery" with nothing to add would read as pressable.
    """
    tab._reset()
    assert tab._add_button.cget("fg_color") != theme.ORANGE_TINT
    assert tab._add_button.cget("text_color") == theme.TEXT_FAINT
    tab._on_checked(plan_of((track("1", "a"), discovery.CERTAIN)))
    assert tab._add_button.cget("fg_color") == theme.ORANGE_TINT
    assert tab._add_button.cget("text_color") == theme.ORANGE_SOFT


# ---------------------------------------------------------------------------
# Correcting
# ---------------------------------------------------------------------------

def test_a_corrected_row_turns_green_and_keeps_the_typed_name(tab, monkeypatch):
    monkeypatch.setattr(add_tab, "ask_artist",
                        lambda *a, **kw: "Jessica Audiffred, LAYZ")
    tab._on_checked(plan_of((track("3", "Garbled", "Some Festival 2026"),
                             discovery.GUESS)))
    tab._tree.focus("3")
    tab._on_edit_artist()

    assert tab._plan.corrections["3"] == "Jessica Audiffred, LAYZ"
    assert tab._tree.item("3", "tags") == ("fixed",)
    assert tab._tree.set("3", "artist") == "Jessica Audiffred, LAYZ"


def test_the_dialog_is_told_where_the_name_came_from(tab, monkeypatch):
    """
    Every row explains itself, not just a flagged one - on a green row that is
    the reassurance there is nothing to second-guess.
    """
    seen = {}

    def capture(_parent, _title, current, reason="", **_options):
        seen["reason"] = reason
        return None

    monkeypatch.setattr(add_tab, "ask_artist", capture)
    tab._on_checked(plan_of((track("1", "Clean"), discovery.CERTAIN)))
    tab._tree.focus("1")
    tab._on_edit_artist()
    assert "nothing was parsed" in seen["reason"]


def test_green_does_not_claim_the_name_was_checked(tab, monkeypatch):
    """
    The one failure green cannot see is an unregistered repost channel, where
    the uploader is a festival or label rather than the performer. Measured on
    the real library, no single-track signal separates those: the blunt one
    fires on 66 tracks to catch about 8, and a tighter "the title names
    someone else" test fires on 167, almost all of them mix-series titles like
    "CarTunes Vol: 4". So the dialog says what green means instead.
    """
    seen = {}

    def capture(_parent, _title, current, reason="", **_options):
        seen["reason"] = reason
        return None

    monkeypatch.setattr(add_tab, "ask_artist", capture)
    tab._on_checked(plan_of((track("1", "Clean"), discovery.CERTAIN)))
    tab._tree.focus("1")
    tab._on_edit_artist()
    assert "did not guess" in seen["reason"]
    assert "repost" in seen["reason"]


def test_a_flagged_row_adds_the_parser_s_own_reason(tab, monkeypatch):
    seen = {}

    def capture(_parent, _title, current, reason="", **_options):
        seen["reason"] = reason
        return None

    monkeypatch.setattr(add_tab, "ask_artist", capture)
    plan = plan_of((track("3", "Garbled"), discovery.GUESS))
    plan.needs_review = [
        discovery.Unsure("3", "Garbled", "Garbled", "no separator found")]
    tab._on_checked(plan)
    tab._tree.focus("3")
    tab._on_edit_artist()
    assert "placeholder" in seen["reason"]          # the level's explanation
    assert "no separator found" in seen["reason"]   # and the specific cause


# ---------------------------------------------------------------------------
# The playlist as uploaded
# ---------------------------------------------------------------------------

from ui.tabs import add_review  # noqa: E402


def raw_row(track_id, status, *, reposter=False, official=False):
    return discovery.RawRow(track_id, f"title {track_id}", f"uploader {track_id}",
                            60 * 60_000, status, reposter=reposter,
                            official=official)


def test_every_left_pane_highlight_is_readable():
    for fate, (_words, colour, tint) in add_review.FATES.items():
        assert _contrast(colour, tint or theme.SURFACE) >= 4.5, fate


def test_the_left_pane_colours_follow_the_asked_precedence():
    """Red over blue over yellow: a reposter's stored copy reads red."""
    assert add_review.fate_tag(raw_row("1", discovery.LIBRARY, reposter=True)) == "library"
    assert add_review.fate_tag(raw_row("2", discovery.DUPLICATE, reposter=True)) == "duplicate"
    assert add_review.fate_tag(raw_row("3", discovery.ADD, reposter=True)) == "reposter"
    assert add_review.fate_tag(raw_row("4", discovery.ADD)) == "add"
    assert add_review.fate_tag(raw_row("5", discovery.SHORT)) == "faint"


def test_both_panes_show_the_star_only_on_official_uploads(tab):
    plan = plan_of((track("1", "Own upload"), discovery.CERTAIN),
                   (track("2", "Reposted"), discovery.LIKELY))
    plan.rows = [raw_row("1", discovery.ADD, official=True),
                 raw_row("2", discovery.ADD, reposter=True)]
    plan.official = {"1"}
    tab._show(plan)
    assert tab._tree.set("1", "star") == add_review.STAR
    assert tab._tree.set("2", "star") == ""
    assert tab._raw.set("1", "star") == add_review.STAR
    assert tab._raw.item("2", "tags") == ("reposter",)
    assert "· reposter" in tab._tree.set("2", "uploader")


def test_the_toolbar_offers_only_what_fits_the_selection(tab):
    plan = plan_of((track("1", "a"), discovery.CERTAIN))
    plan.rows = [raw_row("1", discovery.ADD), raw_row("9", discovery.LIBRARY)]
    tab._show(plan)
    tab._raw.selection_set(("1",))
    tab._sync_toolbar()
    buttons = tab._panes.buttons
    assert str(buttons["skip"].cget("state")) == "normal"
    assert str(buttons["same"].cget("state")) == "disabled"     # needs two
    tab._raw.selection_set(("1", "9"))
    tab._sync_toolbar()
    assert str(buttons["same"].cget("state")) == "normal"
    assert str(buttons["different"].cget("state")) == "disabled"  # no pair


def test_the_reposter_button_offers_to_undo_when_every_uploader_is_one(tab):
    plan = plan_of((track("1", "a"), discovery.CERTAIN))
    plan.rows = [raw_row("1", discovery.ADD, reposter=True)]
    tab._show(plan)
    tab._raw.selection_set(("1",))
    tab._sync_toolbar()
    assert tab._panes.buttons["reposter"].cget("text") == "Not reposter"
