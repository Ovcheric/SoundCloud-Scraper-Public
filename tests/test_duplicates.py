"""
Golden tests for src/core/duplicates.py.

False positives are the expensive failure: a wrong match hides a set you would
have kept. So the fixtures record every same-artist pair in
tests/golden/corpus.json the detector judged, not only the ones it matched.
Regenerate with scripts/gen_golden_duplicates.py, and review the diff.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core import duplicates  # noqa: E402
from core.models import TrackRef  # noqa: E402

GOLDEN = json.loads(
    (ROOT / "tests" / "golden" / "duplicates.json").read_text(encoding="utf-8")
)
QUALIFIERS = frozenset(q.lower() for q in GOLDEN["config"]["location_qualifiers"])
CFG = duplicates.DuplicateConfig(
    location_qualifiers=QUALIFIERS,
    repost_accounts=frozenset(a.lower() for a in GOLDEN["config"]["repost_accounts"]),
)


def ref(row: dict, track_id: str = "x") -> TrackRef:
    return TrackRef(
        track_id=row.get("track_id", track_id),
        title=row["title"],
        artist=row["real_artists"],
        uploader=row.get("uploader", row.get("artist", "")),
        duration_ms=row["duration_ms"],
        duplicate_of=row.get("duplicate_of", ""),
    )


ALL_TRACKS = [ref(r, r["track_id"]) for r in GOLDEN["rows"]]


@pytest.mark.parametrize(
    "case",
    GOLDEN["pairs"],
    ids=[f"{p['a']['title'][:24]}~{p['b']['title'][:24]}" for p in GOLDEN["pairs"]],
)
def test_predicate_is_unchanged(case):
    got = duplicates.is_probable_duplicate(
        ref(case["a"], "a"), ref(case["b"], "b"), QUALIFIERS
    )
    assert got == case["expected"]


@pytest.mark.parametrize(
    "case",
    [m for m in GOLDEN["matches"] if m["matches"]],
    ids=[m["track_id"] for m in GOLDEN["matches"] if m["matches"]],
)
def test_pipeline_is_unchanged(case):
    new = ref(case, case["track_id"])
    others = [t for t in ALL_TRACKS if t.track_id != case["track_id"]]
    got = sorted(
        [m.existing.track_id, m.new_is_official, m.existing_is_official]
        for m in duplicates.find_candidates(new, others, CFG)
    )
    assert got == sorted(case["matches"])


def test_pipeline_still_finds_nothing_where_it_found_nothing():
    """The false-positive guard: tracks that matched nothing still must not."""
    empty = [m for m in GOLDEN["matches"] if not m["matches"]]
    for case in empty:
        new = ref(case, case["track_id"])
        others = [t for t in ALL_TRACKS if t.track_id != case["track_id"]]
        assert duplicates.find_candidates(new, others, CFG) == [], case["title"]


# ---------------------------------------------------------------------------
# The guards, individually
# ---------------------------------------------------------------------------

def make(title, artist="Subtronics", dur=60 * 60000, track_id="t", uploader=""):
    return TrackRef(track_id=track_id, title=title, artist=artist,
                    uploader=uploader, duration_ms=dur)


def test_identical_event_is_a_duplicate():
    assert duplicates.is_probable_duplicate(
        make("Subtronics - EDC Las Vegas 2026 (Full Set)", dur=69 * 60000),
        make("Subtronics - Live @ EDC Las Vegas 2026", dur=70 * 60000),
    )


def test_different_year_is_not_a_duplicate():
    assert not duplicates.is_probable_duplicate(
        make("Subtronics @ Lost Lands 2024"), make("Subtronics @ Lost Lands 2025")
    )


def test_different_series_number_is_not_a_duplicate():
    assert not duplicates.is_probable_duplicate(
        make("NOW THATS WHAT I CALL RIDDIM VOL. 4"),
        make("NOW THATS WHAT I CALL RIDDIM VOL. 5"),
    )


def test_different_location_qualifier_is_not_a_duplicate():
    """Both sides name a qualifier, and a DIFFERENT one - different events."""
    assert not duplicates.is_probable_duplicate(
        make("Subtronics @ Ultra Miami 2026"),
        make("Subtronics @ Ultra Europe 2026"),
        frozenset({"miami", "europe"}),
    )


def test_one_sided_qualifier_does_not_veto():
    """
    Only one side names a qualifier: that is an under-specified title, not
    evidence of a conflict, so "CAMP EDC" and "EDC" may still be one recording.
    """
    assert duplicates.is_probable_duplicate(
        make("Subtronics @ CAMP EDC 2026"),
        make("Subtronics @ EDC 2026"),
        frozenset({"camp"}),
    )


def test_a_partial_rip_of_the_same_set_is_a_duplicate():
    """
    Festival livestreams are ripped by several accounts at different lengths:
    at Lost Lands 2026 one Excision b2b Space Laces set came as 63, 64 and 78
    minutes, and Tynan's own 54-minute upload sat beside a 29-minute rip.
    When the titles agree word for word, length is not evidence.
    """
    assert duplicates.is_probable_duplicate(
        make("Tynan - Live @ Lost Lands 2026", artist="Tynan", dur=29 * 60000),
        make("TYNAN LOST LANDS 2026", artist="Tynan", dur=54 * 60000),
    )


def test_lengths_still_count_when_the_titles_differ():
    """Excision's main set and his Detox set: same festival, different sets."""
    assert not duplicates.is_probable_duplicate(
        make("Excision - Live @ Lost Lands 2026", artist="Excision", dur=108 * 60000),
        make("Excision (Detox Set) - Live @ Lost Lands 2026", artist="Excision",
             dur=38 * 60000),
    )


def test_lengths_still_count_when_the_numbers_differ():
    """Event words drop numbers, so the numbers are checked on their own."""
    assert not duplicates.is_probable_duplicate(
        make("Jantsen - Gutter Music 7 (Part 1)", artist="Jantsen", dur=25 * 60000),
        make("Jantsen - Gutter Music 3 (Part 1)", artist="Jantsen", dur=34 * 60000),
    )


def test_partial_is_a_description_not_part_of_the_event():
    assert duplicates.event_tokens(
        "Slander - Lost Lands 2026 (Partial Set)", "Slander") == \
        duplicates.event_tokens("Slander - Live @ Lost Lands 2026", "Slander")


def test_too_few_event_tokens_never_matches():
    """Titles that are only the artist name carry no event evidence."""
    assert not duplicates.is_probable_duplicate(make("Subtronics"), make("Subtronics"))


def test_event_tokens_drop_the_artist_name():
    assert "subtronics" not in duplicates.event_tokens(
        "Subtronics @ Lost Lands 2026", "Subtronics"
    )


def test_series_number_reads_several_spellings():
    assert duplicates.series_number("RIDDIM VOL. 4") == "4"
    assert duplicates.series_number("Radio Episode 52") == "52"
    assert duplicates.series_number("no number here") is None


def test_already_flagged_duplicates_are_not_raised_again():
    new = make("Subtronics - EDC Las Vegas 2026", track_id="new")
    settled = TrackRef(track_id="old", title="Subtronics - Live @ EDC Las Vegas 2026",
                       artist="Subtronics", duration_ms=60 * 60000,
                       duplicate_of="something")
    assert duplicates.find_candidates(new, [settled], CFG) == []


def test_different_artists_are_never_compared():
    a = make("Live @ EDC Las Vegas 2026", artist="Subtronics", track_id="a")
    b = make("Live @ EDC Las Vegas 2026", artist="Wooli", track_id="b")
    assert duplicates.find_candidates(a, [b], CFG) == []


# ---------------------------------------------------------------------------
# The same-batch bug
# ---------------------------------------------------------------------------

def test_find_pairs_within_catches_the_edc_triplet():
    """
    The exact tracks that slipped through: all three arrived in one playlist on
    2026-05-29 and all three were added, because incoming tracks were only ever
    compared against the stored database.
    """
    batch = [
        make("Subtronics - EDC Las Vegas 2026 (Full Set)", dur=69 * 60000, track_id="1"),
        make("Subtronics - Live @ EDC Las Vegas 2026", dur=70 * 60000, track_id="2"),
        make("SUBTRONICS - Live @ EDC Las Vegas 2026", dur=69 * 60000, track_id="3"),
    ]
    pairs = duplicates.find_pairs_within(batch, CFG)
    assert len(pairs) == 3  # every combination of the three
    assert {(m.existing.track_id, m.new.track_id) for m in pairs} == {
        ("1", "2"), ("1", "3"), ("2", "3")
    }


def test_find_pairs_within_leaves_distinct_sets_alone():
    batch = [
        make("Subtronics - Forbidden Kingdom 2026", track_id="1"),
        make("Subtronics - Ultra Europe 2026", track_id="2"),
        make("Subtronics - Zombie Apocalypse Festival 2026", track_id="3"),
    ]
    assert duplicates.find_pairs_within(batch, CFG) == []


def test_preferred_copy_favours_the_official_upload():
    official = make("Wooli @ EDC 2026", artist="Wooli", uploader="Wooli", track_id="off")
    repost = make("Wooli @ EDC 2026", artist="Wooli", uploader="Johnnykham", track_id="rp")
    [match] = duplicates.find_candidates(official, [repost], CFG)
    assert match.preferred().track_id == "off"
