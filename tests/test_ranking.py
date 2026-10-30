"""
Tests for src/core/ranking.py - the Find search's filters, scores and picks.

Pure functions, so no database and no network: raw track dicts in the shape
SoundCloud returns them, and a fixed "now".
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.ranking import (  # noqa: E402
    Candidate, Filters, Hit, pick, rejection, score, uploaded_at)

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
HOUR = 60 * 60 * 1000


def raw(**over):
    track = {"id": 1, "title": "Wooli @ Lost Lands 2026", "duration": HOUR,
             "access": "playable", "created_at": "2026/09/30 12:00:00 +0000",
             "favoritings_count": 100, "genre": "Dubstep", "tag_list": ""}
    return {**track, **over}


# ---------------------------------------------------------------------------
# rejection
# ---------------------------------------------------------------------------

def test_a_plain_set_passes_the_defaults():
    assert rejection(raw(), Filters(), NOW) is None


def test_each_filter_says_why_it_rejected():
    cases = [
        (raw(access="preview"), Filters(), "preview only"),
        (raw(duration=10 * 60 * 1000), Filters(), "too short"),
        (raw(duration=4 * HOUR), Filters(max_minutes=180), "too long"),
        (raw(created_at="2024/01/01 00:00:00 +0000"), Filters(within_days=365),
         "too old"),
        (raw(favoritings_count=3), Filters(min_likes=10), "too few likes"),
        (raw(title="Wooli Podcast 12"), Filters(exclude_words=("podcast",)),
         "excluded word"),
        (raw(), Filters(include_words=("b2b",)), "missing keyword"),
    ]
    for track, filters, why in cases:
        assert rejection(track, filters, NOW) == why


def test_previews_pass_when_the_user_allows_them():
    assert rejection(raw(access="preview"), Filters(playable_only=False), NOW) is None


def test_zero_means_no_limit():
    long_old = raw(duration=6 * HOUR, created_at="2015/01/01 00:00:00 +0000")
    assert rejection(long_old, Filters(max_minutes=0, within_days=0), NOW) is None


def test_keywords_match_whole_words_not_fragments():
    """Excluding "radio" must not drop a set by Radiohead's tribute act."""
    assert rejection(raw(title="Radiohead Tribute Mix"),
                     Filters(exclude_words=("radio",)), NOW) is None


def test_keywords_also_match_the_genre_and_tags():
    assert rejection(raw(genre="Drum & Bass"),
                     Filters(include_words=("drum",)), NOW) is None


def test_a_set_with_no_date_is_not_rejected_as_old():
    assert rejection(raw(created_at=None), Filters(within_days=7), NOW) is None


def test_soundcloud_dates_are_parsed():
    assert uploaded_at(raw()) == datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    assert uploaded_at(raw(created_at="2026-09-30T12:00:00Z")) is not None


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

def test_two_reasons_outscore_one():
    once = Candidate(raw(), [Hit("liked_by", 2.0, "liked by Wooli")])
    twice = Candidate(raw(), [Hit("liked_by", 2.0, "liked by Wooli"),
                              Hit("related", 1.5, "like X")])
    assert score(twice, NOW) > score(once, NOW)


def test_a_newer_upload_breaks_a_tie():
    old = Candidate(raw(created_at="2025/01/01 00:00:00 +0000"),
                    [Hit("artist", 3.0, "")])
    new = Candidate(raw(), [Hit("artist", 3.0, "")])
    assert score(new, NOW) > score(old, NOW)


# ---------------------------------------------------------------------------
# pick
# ---------------------------------------------------------------------------

def test_best_first_and_no_more_than_asked_for():
    ranked = [("a", "A", 1.0), ("b", "B", 5.0), ("c", "C", 3.0)]
    chosen, left_out = pick(ranked, Filters(max_results=2, per_artist=0), set())
    assert chosen == ["b", "c"]
    assert left_out["over the limit"] == 1


def test_one_artist_cannot_fill_the_queue():
    ranked = [(str(i), "Wooli", 10.0 - i) for i in range(5)] + [("x", "Levity", 1.0)]
    chosen, left_out = pick(ranked, Filters(per_artist=2), set())
    assert chosen == ["0", "1", "x"]
    assert left_out["per-artist cap"] == 3


def test_a_blocked_artist_is_left_out():
    chosen, left_out = pick([("a", "Skrillex", 9.0)], Filters(), {"skrillex"})
    assert chosen == []
    assert left_out["artist left out"] == 1


def test_a_b2b_survives_unless_every_performer_is_blocked():
    """One name you dropped next to one you love is still worth hearing."""
    ranked = [("a", "Skrillex, Wooli", 9.0), ("b", "Skrillex, John Summit", 8.0)]
    chosen, _ = pick(ranked, Filters(), {"skrillex", "john summit"})
    assert chosen == ["a"]


# ---------------------------------------------------------------------------
# New to you
# ---------------------------------------------------------------------------

KNOWN = {"wooli": 14, "levity": 1}       # sets of theirs in the library


def test_no_familiarity_limit_unless_asked():
    ranked = [("a", "Wooli", 3.0), ("b", "Nobody Yet", 2.0)]
    chosen, _ = pick(ranked, Filters(), set(), KNOWN)
    assert chosen == ["a", "b"]


def test_never_heard_keeps_only_artists_the_library_lacks():
    ranked = [("a", "Wooli", 3.0), ("b", "Levity", 2.5), ("c", "Nobody Yet", 2.0)]
    chosen, left_out = pick(ranked, Filters(max_known_sets=0), set(), KNOWN)
    assert chosen == ["c"]
    assert left_out["artist already familiar"] == 2


def test_once_at_most_lets_a_one_set_artist_through():
    ranked = [("a", "Wooli", 3.0), ("b", "Levity", 2.5)]
    chosen, _ = pick(ranked, Filters(max_known_sets=1), set(), KNOWN)
    assert chosen == ["b"]


def test_a_b2b_is_new_when_any_performer_is():
    """A new name usually arrives next to one you already know."""
    ranked = [("a", "Wooli, Nobody Yet", 3.0), ("b", "Wooli, Levity", 2.0)]
    chosen, _ = pick(ranked, Filters(max_known_sets=0), set(), KNOWN)
    assert chosen == ["a"]


def test_familiarity_ignores_case():
    chosen, _ = pick([("a", "WOOLI", 3.0)], Filters(max_known_sets=0), set(), KNOWN)
    assert chosen == []
