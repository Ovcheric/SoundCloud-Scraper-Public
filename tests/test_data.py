"""
Tests for src/data/.

Every test runs against a fresh in-memory database built from the real
schema.sql, so these fail if the schema and the queries drift apart.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from data import db, dismissals, settings, starter, tracks  # noqa: E402

SEEN = "2026-09-19 10:00:00"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def add(conn, track_id, **row):
    row.setdefault("title", f"title {track_id}")
    row.setdefault("real_artists", "Subtronics")
    tracks.insert_many(conn, [{"track_id": track_id, **row}], seen_at=SEEN)


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------

def test_connect_applies_the_schema(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {"tracks", "track_verdicts", "track_artists", "artist_taste"} <= names


def test_foreign_keys_are_enforced(conn):
    """Off by default in SQLite, so this is worth pinning."""
    add(conn, "1")
    with pytest.raises(Exception):
        conn.execute("UPDATE tracks SET duplicate_of = 'nope' WHERE track_id = '1'")


# ---------------------------------------------------------------------------
# inserting
# ---------------------------------------------------------------------------

def test_insert_many_reports_what_it_added(conn):
    added = tracks.insert_many(conn, [
        {"track_id": "1", "title": "a", "real_artists": "Wooli"},
        {"track_id": "2", "title": "b", "real_artists": "Levity"},
    ], seen_at=SEEN)
    assert added == 2
    assert tracks.known_ids(conn) == {"1", "2"}


def test_reimporting_never_overwrites_existing_rows(conn):
    """A re-run must not be able to destroy a correction or a verdict."""
    add(conn, "1", title="original", real_artists="Subtronics")
    tracks.set_manual_artist(conn, "1", "Corrected Name")
    added = tracks.insert_many(
        conn, [{"track_id": "1", "title": "clobbered", "real_artists": "Wrong"}],
        seen_at=SEEN)
    assert added == 0
    row = conn.execute("SELECT title FROM tracks WHERE track_id='1'").fetchone()
    assert row["title"] == "original"
    assert tracks.all_refs(conn)[0].artist == "Corrected Name"


def test_all_refs_applies_manual_corrections(conn):
    add(conn, "1", real_artists="Tomorrowland")
    tracks.set_manual_artist(conn, "1", "Subtronics")
    assert tracks.all_refs(conn)[0].artist == "Subtronics"


# ---------------------------------------------------------------------------
# sync_likes - a mirror, not an append
# ---------------------------------------------------------------------------

def test_sync_likes_sets_and_reports(conn):
    add(conn, "1")
    add(conn, "2")
    assert tracks.sync_likes(conn, ["1"], at=SEEN) == (1, 0)
    assert tracks.verdict_counts(conn)["liked"] == 1


def test_sync_likes_clears_an_unhearted_track(conn):
    """The bug in the old mark_liked(): it could only ever add."""
    add(conn, "1")
    tracks.sync_likes(conn, ["1"], at=SEEN)
    assert tracks.sync_likes(conn, [], at=SEEN) == (0, 1)
    assert tracks.verdict_counts(conn).get("liked") is None


def test_sync_likes_ignores_likes_for_tracks_we_do_not_have(conn):
    """Your SoundCloud likes hold far more than this app has ever seen."""
    add(conn, "1")
    assert tracks.sync_likes(conn, ["1", "999", "998"], at=SEEN) == (1, 0)


def test_sync_likes_never_touches_a_manual_verdict(conn):
    add(conn, "1")
    conn.execute("UPDATE tracks SET manual_verdict='skipped' WHERE track_id='1'")
    tracks.sync_likes(conn, [], at=SEEN)
    row = conn.execute("SELECT manual_verdict FROM tracks WHERE track_id='1'").fetchone()
    assert row["manual_verdict"] == "skipped"


# ---------------------------------------------------------------------------
# Discovery snapshots - the only source of "listened"
# ---------------------------------------------------------------------------

def test_first_snapshot_infers_nothing(conn):
    add(conn, "1")
    tracks.record_discovery_snapshot(conn, ["1"], at="2026-09-19 10:00:00")
    assert tracks.infer_listened(conn) == 0
    assert tracks.verdict_counts(conn)["pending"] == 1


def test_track_gone_from_discovery_is_listened(conn):
    add(conn, "1")
    add(conn, "2")
    tracks.record_discovery_snapshot(conn, ["1", "2"], at="2026-09-19 10:00:00")
    tracks.record_discovery_snapshot(conn, ["2"], at="2026-09-20 10:00:00")
    assert tracks.infer_listened(conn) == 1
    assert tracks.verdict_counts(conn) == {"skipped": 1, "pending": 1}


def test_gone_and_liked_reads_as_liked_not_skipped(conn):
    add(conn, "1")
    tracks.record_discovery_snapshot(conn, ["1"], at="2026-09-19 10:00:00")
    tracks.record_discovery_snapshot(conn, [], at="2026-09-20 10:00:00")
    tracks.infer_listened(conn)
    tracks.sync_likes(conn, ["1"], at=SEEN)
    assert tracks.verdict_counts(conn) == {"liked": 1}


def test_an_empty_snapshot_is_still_a_run(conn):
    """
    An empty Discovery is meaningful - it is the state that says everything
    queued has been worked through, and it is the state the playlist is in
    today. Recording membership alone would write no rows here, making the run
    indistinguishable from never having run.
    """
    tracks.record_discovery_snapshot(conn, [], at="2026-09-19 10:00:00")
    assert tracks.latest_snapshot(conn) == "2026-09-19 10:00:00"


def test_a_skipped_run_does_not_lose_a_track(conn):
    """
    Compares against every earlier snapshot, not just the previous one, so a
    track that vanished two runs ago is still caught.
    """
    add(conn, "1")
    tracks.record_discovery_snapshot(conn, ["1"], at="2026-09-01 10:00:00")
    tracks.record_discovery_snapshot(conn, [], at="2026-09-02 10:00:00")
    tracks.record_discovery_snapshot(conn, [], at="2026-09-03 10:00:00")
    assert tracks.infer_listened(conn) == 1


def test_listened_is_not_overwritten_on_a_later_run(conn):
    add(conn, "1")
    tracks.record_discovery_snapshot(conn, ["1"], at="2026-09-01 10:00:00")
    tracks.record_discovery_snapshot(conn, [], at="2026-09-02 10:00:00")
    tracks.infer_listened(conn)
    first = conn.execute("SELECT listened_at FROM tracks WHERE track_id='1'").fetchone()[0]
    tracks.record_discovery_snapshot(conn, [], at="2026-09-03 10:00:00")
    assert tracks.infer_listened(conn) == 0
    after = conn.execute("SELECT listened_at FROM tracks WHERE track_id='1'").fetchone()[0]
    assert after == first


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def test_resolution_config_round_trips(conn):
    settings.add_alias(conn, "TapeB", "Tape B")
    settings.add_repost_account(conn, "REBiRTH Festival")
    conn.execute("INSERT INTO artist_settings VALUES ('known_event','Lost Lands')")
    cfg = settings.load_resolution_config(conn)
    assert cfg.aliases == {"TapeB": "Tape B"}
    assert cfg.is_repost_account("rebirth festival")
    assert "Lost Lands" in cfg.known_events


def test_resolution_config_learns_casing_from_manual_corrections(conn):
    add(conn, "1", real_artists="CRANKDAT")
    tracks.set_manual_artist(conn, "1", "Crankdat")
    cfg = settings.load_resolution_config(conn)
    assert cfg.canonical_casing["crankdat"] == "Crankdat"


def test_add_alias_updates_an_existing_mapping(conn):
    settings.add_alias(conn, "X", "Excision")
    settings.add_alias(conn, "X", "Excision Official")
    assert settings.load_resolution_config(conn).aliases["X"] == "Excision Official"


def test_keep_both_is_stored_once_regardless_of_order(conn):
    settings.add_keep_both(conn, "2", "1")
    settings.add_keep_both(conn, "1", "2")
    assert settings.keep_both_pairs(conn) == {frozenset({"1", "2"})}


def test_keep_both_ignores_a_track_paired_with_itself(conn):
    settings.add_keep_both(conn, "1", "1")
    assert settings.keep_both_pairs(conn) == set()


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def test_artists_to_skip_finds_repeated_misses(conn):
    for i in range(4):
        add(conn, str(i), real_artists="John Summit")
        tracks.record_discovery_snapshot(conn, [str(i)], at="2026-09-01 10:00:00")
    tracks.record_discovery_snapshot(conn, [], at="2026-09-02 10:00:00")
    tracks.infer_listened(conn)
    assert tracks.artists_to_skip(conn) == [{"artist": "John Summit", "heard": 4}]


def test_artist_taste_ranks_by_likes(conn):
    add(conn, "1", real_artists="Subtronics")
    add(conn, "2", real_artists="Wooli")
    tracks.sync_likes(conn, ["1"], at=SEEN)
    assert tracks.artist_taste(conn) == [
        {"artist": "Subtronics", "heard": 1, "liked": 1, "pending": 0}
    ]


# ---------------------------------------------------------------------------
# dismissals
# ---------------------------------------------------------------------------

def test_a_new_table_reaches_a_database_made_before_it(tmp_path):
    """
    discovery.db holds history that cannot be rebuilt, so new tables must
    arrive by being added on open - never by recreating the file.
    """
    path = tmp_path / "old.db"
    old = db.connect(path)
    add(old, "1")
    old.execute("DROP TABLE dismissed_tracks")   # as it was before Find
    old.commit()
    old.close()

    reopened = db.connect(path)
    names = {r[0] for r in reopened.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "dismissed_tracks" in names
    assert tracks.known_ids(reopened) == {"1"}     # nothing lost
    reopened.close()


def test_a_dismissal_is_recorded_with_its_reason(conn):
    dismissals.record(conn, [{"track_id": "9", "title": "t", "artist": "Wooli"}],
                      reason="style", note="too melodic", at=SEEN)
    assert dismissals.dismissed_ids(conn) == {"9"}
    row = conn.execute("SELECT reason, note FROM dismissed_tracks").fetchone()
    assert tuple(row) == ("style", "too melodic")


def test_the_latest_reason_wins(conn):
    for reason in ("style", "artist"):
        dismissals.record(conn, [{"track_id": "9", "title": "t", "artist": "A"}],
                          reason=reason, note=None, at=SEEN)
    assert dismissals.dismissed_artists(conn, "artist") == {"a"}


def test_dismissed_artists_splits_a_b2b_and_ignores_case(conn):
    dismissals.record(conn, [{"track_id": "9", "title": "t",
                              "artist": "Wooli, CRANKDAT"}],
                      reason="artist", note=None, at=SEEN)
    dismissals.record(conn, [{"track_id": "8", "title": "t", "artist": "Levity"}],
                      reason="style", note=None, at=SEEN)
    assert dismissals.dismissed_artists(conn, "artist") == {"wooli", "crankdat"}


def test_a_dismissed_set_is_never_a_verdict(conn):
    """It was never heard, so it must not count as pending or skipped."""
    dismissals.record(conn, [{"track_id": "9", "title": "t", "artist": "A"}],
                      reason="heard", note=None, at=SEEN)
    assert tracks.verdict_counts(conn) == {}
    assert tracks.artist_taste(conn) == []


def test_titles_only_returns_what_was_asked_for(conn):
    add(conn, "1", title="One")
    add(conn, "2", title="Two")
    assert tracks.titles(conn, ["2", "3"]) == {"2": "Two"}


# ---------------------------------------------------------------------------
# starter - the lists a brand-new library begins with
# ---------------------------------------------------------------------------

def test_a_new_library_gets_festival_names_and_places(conn):
    assert starter.seed_new_library(conn) > 0
    cfg = settings.load_resolution_config(conn)
    assert "Lost Lands" in cfg.known_events
    assert "miami" in settings.load_duplicate_config(conn).location_qualifiers


def test_seeding_happens_once(conn):
    starter.seed_new_library(conn)
    assert starter.seed_new_library(conn) == 0


def test_a_library_in_use_is_never_seeded(conn):
    add(conn, "1")
    assert starter.seed_new_library(conn) == 0
    assert settings.settings_of_kind(conn, "known_event") == []


def test_a_pruned_list_stays_pruned(conn):
    """Someone who kept one event of their own does not get the rest back."""
    settings.add_known_event(conn, "My Local Rave")
    assert starter.seed_new_library(conn) == 0
    assert settings.settings_of_kind(conn, "known_event") == ["My Local Rave"]


def test_places_are_stored_lowercase(conn):
    settings.add_location_qualifier(conn, "Miami")
    assert settings.settings_of_kind(conn, "location_qualifier") == ["miami"]
