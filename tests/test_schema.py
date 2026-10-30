"""
Tests for the derived views in src/data/schema.sql.

The views ARE the business logic: nothing else may decide a track's verdict or
its artist. Each test builds a fresh in-memory database from the real schema
file, so these fail if the schema drifts.
"""

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "src" / "data" / "schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


def add(db, track_id, **cols):
    cols.setdefault("title", f"title {track_id}")
    cols.setdefault("first_seen_at", "2026-01-01")
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    db.execute(
        f"INSERT INTO tracks (track_id, {names}) VALUES (?, {marks})",
        (track_id, *cols.values()),
    )


def verdict(db, track_id):
    return db.execute(
        "SELECT verdict, verdict_source FROM track_verdicts WHERE track_id = ?",
        (track_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# track_verdicts
# ---------------------------------------------------------------------------

def test_pending_when_nothing_is_known(db):
    add(db, "1")
    assert verdict(db, "1") == ("pending", "none")


def test_listened_without_a_like_is_skipped(db):
    add(db, "1", listened_at="2026-01-02")
    assert verdict(db, "1") == ("skipped", "inferred")


def test_in_likes_is_liked(db):
    add(db, "1", in_likes_at="2026-01-02", listened_at="2026-01-02")
    assert verdict(db, "1") == ("liked", "soundcloud")


def test_like_is_inherited_from_a_liked_duplicate(db):
    """
    The old code physically copied the like onto the surviving row. Here it is
    a join, which is why a set reads as liked when only its duplicate
    child was ever hearted.
    """
    add(db, "target", listened_at="2026-01-02")
    add(db, "child", duplicate_of="target", in_likes_at="2026-01-02")
    assert verdict(db, "target") == ("liked", "duplicate")


def test_manual_verdict_used_when_soundcloud_is_silent(db):
    add(db, "1", manual_verdict="liked", listened_at="2026-01-02")
    assert verdict(db, "1") == ("liked", "manual")


def test_live_like_outranks_a_stale_manual_skip(db):
    """
    The Mersiv case: hand-typed 'no' long ago, hearted on SoundCloud since.
    Hearting is the user's live mechanism, so it wins. Flip the order in the
    view if that judgement ever changes.
    """
    add(db, "1", manual_verdict="skipped", in_likes_at="2026-01-02")
    assert verdict(db, "1") == ("liked", "soundcloud")


# ---------------------------------------------------------------------------
# track_artists — a typed correction always wins
# ---------------------------------------------------------------------------

def test_resolved_artist_used_when_no_correction(db):
    add(db, "1", real_artists="Tomorrowland")
    assert db.execute("SELECT artist, artist_source FROM track_artists").fetchone() == (
        "Tomorrowland",
        "resolved",
    )


def test_manual_artist_always_wins(db):
    add(db, "1", real_artists="Tomorrowland", real_artists_manual="Subtronics")
    assert db.execute("SELECT artist, artist_source FROM track_artists").fetchone() == (
        "Subtronics",
        "manual",
    )


def test_empty_manual_artist_does_not_shadow_the_resolved_one(db):
    add(db, "1", real_artists="Subtronics", real_artists_manual="")
    assert db.execute("SELECT artist, artist_source FROM track_artists").fetchone() == (
        "Subtronics",
        "resolved",
    )


# ---------------------------------------------------------------------------
# artist_taste
# ---------------------------------------------------------------------------

def test_duplicates_do_not_count_twice_for_an_artist(db):
    add(db, "target", real_artists="Kai Wachi", listened_at="2026-01-02")
    add(db, "child", real_artists="Kai Wachi", duplicate_of="target",
        in_likes_at="2026-01-02")
    rows = db.execute("SELECT artist, heard, liked FROM artist_taste").fetchall()
    assert rows == [("Kai Wachi", 1, 1)]


def test_artist_taste_groups_by_the_manual_name(db):
    add(db, "1", real_artists="Tomorrowland", real_artists_manual="Subtronics",
        in_likes_at="2026-01-02")
    add(db, "2", real_artists="Subtronics", listened_at="2026-01-02")
    assert db.execute("SELECT artist, heard, liked FROM artist_taste").fetchall() == [
        ("Subtronics", 2, 1)
    ]


def test_tracks_with_no_artist_are_excluded(db):
    add(db, "1", real_artists="", listened_at="2026-01-02")
    assert db.execute("SELECT COUNT(*) FROM artist_taste").fetchone()[0] == 0


def test_skip_filter_finds_artists_with_no_likes(db):
    for i in range(4):
        add(db, str(i), real_artists="John Summit", listened_at="2026-01-02")
    rows = db.execute(
        "SELECT artist, heard FROM artist_taste WHERE liked = 0 AND heard >= 4"
    ).fetchall()
    assert rows == [("John Summit", 4)]


# ---------------------------------------------------------------------------
# artist_taste credits every performer in a b2b
# ---------------------------------------------------------------------------

def test_a_b2b_credits_both_artists(db):
    """
    Grouping on the joined string credited neither performer, so an artist
    with a liked b2b set could sit in the stop-queueing list on zero likes.
    """
    add(db, "1", real_artists="Wooli, Crankdat", in_likes_at="2026-01-02")
    rows = dict(db.execute("SELECT artist, liked FROM artist_taste").fetchall())
    assert rows == {"Wooli": 1, "Crankdat": 1}


def test_a_three_way_set_credits_all_three(db):
    add(db, "1", real_artists="Doctor P, Flux Pavilion, FuntCase",
        listened_at="2026-01-02")
    assert set(r[0] for r in db.execute("SELECT artist FROM artist_taste")) == {
        "Doctor P", "Flux Pavilion", "FuntCase"
    }


def test_solo_and_b2b_sets_add_up_for_one_artist(db):
    add(db, "1", real_artists="Wooli", in_likes_at="2026-01-02")
    add(db, "2", real_artists="Wooli, Crankdat", listened_at="2026-01-02")
    assert db.execute(
        "SELECT heard, liked FROM artist_taste WHERE artist='Wooli'").fetchone() == (2, 1)


def test_a_liked_b2b_keeps_an_artist_off_the_skip_list(db):
    """Excision's real case: four solo skips but a liked b2b."""
    for i in range(4):
        add(db, f"s{i}", real_artists="Excision", listened_at="2026-01-02")
    add(db, "b", real_artists="Excision, Wooli", in_likes_at="2026-01-02")
    skipped = [r[0] for r in db.execute(
        "SELECT artist FROM artist_taste WHERE liked = 0 AND heard >= 4")]
    assert "Excision" not in skipped


def test_splitting_tolerates_spacing_and_blanks(db):
    add(db, "1", real_artists="Wooli,Crankdat , ,Levity", listened_at="2026-01-02")
    assert set(r[0] for r in db.execute("SELECT artist FROM artist_taste")) == {
        "Wooli", "Crankdat", "Levity"
    }
