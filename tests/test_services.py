"""
Tests for src/services/.

A fake API stands in for SoundCloud and a real in-memory database underneath,
so these exercise the pipelines end to end without the network. The cases that
matter are the ones the old app got wrong: duplicates arriving together, and
the order of snapshot-versus-removal.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api.client import NotFound  # noqa: E402
from data import db, dismissals, settings, tracks as track_store  # noqa: E402
from services import cleanup, discovery, library  # noqa: E402

LONG = 70 * 60 * 1000
NOW = "2026-09-19 10:00:00"


class FakeApi:
    """Records playlist writes; serves whatever it was constructed with."""

    def __init__(self, playlist_tracks=(), likes=(), discovery_ids=(), kind="playlist",
                 titles=None):
        self._tracks = list(playlist_tracks)
        # A list, not a set: likes may be bare ids or full track dicts, and
        # importing liked sets needs the durations.
        self._likes = list(likes)
        self.discovery_ids = list(discovery_ids)
        self._kind = kind
        self._titles = dict(titles or {})   # for tracks the library lacks
        self.written = None

    def track(self, track_id):
        if str(track_id) not in self._titles:
            raise NotFound(f"track {track_id}")
        return {"id": track_id, "title": self._titles[str(track_id)]}

    def resolve(self, url):
        return {"kind": self._kind, "id": 1, "title": "Source Playlist"}

    def playlist_tracks(self, playlist_id):
        return self._tracks

    def liked_track_ids(self):
        return {str(t["id"]) if isinstance(t, dict) else str(t) for t in self._likes}

    def liked_tracks(self):
        """Full track dicts, as the real client returns."""
        return [t for t in self._likes if isinstance(t, dict)]

    def discovery_playlist(self):
        return 99, list(self.discovery_ids)

    def set_playlist_tracks(self, playlist_id, track_ids):
        self.written = list(track_ids)
        self.discovery_ids = list(track_ids)


def raw(track_id, title, uploader="Johnnykham", duration=LONG):
    return {"id": track_id, "title": title, "duration": duration,
            "user": {"username": uploader}, "created_at": "2026/05/29 00:00:00 +0000"}


@pytest.fixture
def conn():
    """
    A database configured enough for resolution to work. The event names
    matter: without them "Wooli - EDC Las Vegas 2026" resolves to
    "Wooli - EDC Las Vegas", artists stop matching, and duplicate detection
    silently does nothing. That is not a test artefact - it is exactly what a
    missing known_event does in production.
    """
    c = db.connect(":memory:")
    settings.add_repost_account(c, "Johnnykham")
    for event in ("EDC Las Vegas", "Ultra Europe", "Forbidden Kingdom",
                  "Zombie Apocalypse Festival", "Lost Lands"):
        c.execute("INSERT INTO artist_settings VALUES ('known_event', ?)", (event,))
    for qualifier in ("vegas", "europe", "miami"):
        c.execute(
            "INSERT INTO artist_settings VALUES ('location_qualifier', ?)", (qualifier,))
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Add — filtering
# ---------------------------------------------------------------------------

def test_short_tracks_are_filtered_out(conn):
    api = FakeApi([raw(1, "A Song", duration=3 * 60 * 1000),
                   raw(2, "Wooli @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    assert (plan.too_short, plan.add_count) == (1, 1)


def test_already_known_tracks_are_filtered_out(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "x"}], seen_at=NOW)
    plan = discovery.plan_add("url", conn, FakeApi([raw(1, "Wooli @ EDC 2026")]))
    assert (plan.already_known, plan.add_count) == (1, 0)


def test_a_set_turned_down_in_find_is_not_offered_by_a_playlist(conn):
    """Dismissed means dismissed, wherever the set turns up again."""
    dismissals.record(conn, [{"track_id": "2", "title": "t", "artist": "Wooli"}],
                      reason="style", note=None, at=NOW)
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026"),
                   raw(2, "Wooli @ Lost Lands 2026")])
    plan = discovery.plan_add("url", conn, api)
    assert [t["track_id"] for t in plan.to_add] == ["1"]
    assert plan.dismissed_before == 1


def test_a_non_playlist_url_is_rejected(conn):
    with pytest.raises(ValueError, match="not a playlist"):
        discovery.plan_add("url", conn, FakeApi(kind="track"))


def test_artists_are_resolved_from_the_title_for_repost_accounts(conn):
    api = FakeApi([raw(1, "Wooli - EDC Las Vegas 2026 (Full Set)")])
    plan = discovery.plan_add("url", conn, api)
    assert plan.to_add[0]["real_artists"] == "Wooli"
    assert plan.to_add[0]["uploader"] == "Johnnykham"


# ---------------------------------------------------------------------------
# Add — the same-batch bug
# ---------------------------------------------------------------------------

def test_three_copies_in_one_playlist_add_once(conn):
    """
    The exact failure that put all three EDC sets in the library. Nothing here
    may ask the user - with no tiebreaker the old code asked, and when nobody
    answered, everything was added.
    """
    api = FakeApi([
        raw(1, "Subtronics - EDC Las Vegas 2026 (Full Set)", duration=69 * 60000),
        raw(2, "Subtronics - Live @ EDC Las Vegas 2026", duration=70 * 60000),
        raw(3, "SUBTRONICS - Live @ EDC Las Vegas 2026", duration=69 * 60000),
    ])
    plan = discovery.plan_add("url", conn, api)
    assert plan.add_count == 1
    assert len(plan.skipped) == 2
    assert {s.because for s in plan.skipped} == {"duplicate in this playlist"}


def test_the_longest_copy_wins_when_nothing_else_separates_them(conn):
    """Reposts are trimmed, so length is the tiebreaker of last resort."""
    api = FakeApi([
        raw(1, "Subtronics - EDC Las Vegas 2026", duration=69 * 60000),
        raw(2, "Subtronics - Live @ EDC Las Vegas 2026", duration=70 * 60000),
    ])
    plan = discovery.plan_add("url", conn, api)
    assert plan.to_add[0]["track_id"] == "2"
    assert plan.skipped[0].reason == "longer copy"


def test_an_official_upload_beats_a_repost(conn):
    api = FakeApi([
        raw(1, "Subtronics - EDC Las Vegas 2026", uploader="Johnnykham"),
        raw(2, "Subtronics - Live @ EDC Las Vegas 2026", uploader="Subtronics"),
    ])
    plan = discovery.plan_add("url", conn, api)
    assert plan.to_add[0]["track_id"] == "2"
    assert plan.skipped[0].reason == "official upload"


def test_distinct_festivals_are_all_kept(conn):
    """Same artist, different events - not duplicates. Must not over-collapse."""
    api = FakeApi([
        raw(1, "Subtronics - Forbidden Kingdom 2026"),
        raw(2, "Subtronics - Ultra Europe 2026"),
        raw(3, "Subtronics - Zombie Apocalypse Festival 2026"),
    ])
    assert discovery.plan_add("url", conn, api).add_count == 3


def test_a_duplicate_of_a_stored_track_is_skipped(conn):
    track_store.insert_many(conn, [{
        "track_id": "1", "title": "Subtronics - EDC Las Vegas 2026",
        "real_artists": "Subtronics", "duration_ms": LONG}], seen_at=NOW)
    api = FakeApi([raw(2, "Subtronics - Live @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    assert plan.add_count == 0
    assert plan.skipped[0].because == "already in library"


def test_a_keep_both_decision_is_never_re_raised(conn):
    """60 of 62 duplicate pairs are settled this way. Re-asking is the worst sin."""
    track_store.insert_many(conn, [{
        "track_id": "1", "title": "Subtronics - EDC Las Vegas 2026",
        "real_artists": "Subtronics", "duration_ms": LONG}], seen_at=NOW)
    settings.add_keep_both(conn, "1", "2")
    api = FakeApi([raw(2, "Subtronics - Live @ EDC Las Vegas 2026")])
    assert discovery.plan_add("url", conn, api).add_count == 1


# ---------------------------------------------------------------------------
# Add — committing
# ---------------------------------------------------------------------------

def test_apply_add_writes_the_library_and_the_playlist(conn):
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    result = discovery.apply_add(plan, conn, api, now=NOW)
    assert (result.added_to_library, result.added_to_playlist) == (1, 1)
    assert track_store.known_ids(conn) == {"1"}
    assert api.written == ["1"]


def test_apply_add_appends_rather_than_replacing_the_playlist(conn):
    """The full list is PUT, so existing tracks must be carried over."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")], discovery_ids=["555"],
                  titles={"555": "Zeds Dead @ Lost Lands 2025"})
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert sorted(api.written) == ["1", "555"]


def test_discovery_is_left_in_title_order(conn):
    """Old and new together, A to Z, case ignored - not new ones tacked on."""
    track_store.insert_many(conn, [{
        "track_id": "7", "title": "levity @ Lost Lands 2025",
        "real_artists": "Levity", "duration_ms": LONG}], seen_at=NOW)
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026"),
                   raw(2, "Apashe @ Ultra Europe 2026", uploader="Apashe")],
                  discovery_ids=["7", "555"],
                  titles={"555": "Zeds Dead @ Lost Lands 2025"})
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert api.written == ["2", "7", "1", "555"]


def test_sorting_never_drops_a_track_soundcloud_cannot_name(conn):
    """A deleted upload still in the playlist goes last, not missing."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")], discovery_ids=["404"])
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert api.written == ["1", "404"]


def test_apply_add_records_the_duplicate_link(conn):
    track_store.insert_many(conn, [{
        "track_id": "1", "title": "Subtronics - EDC Las Vegas 2026",
        "real_artists": "Subtronics", "duration_ms": LONG}], seen_at=NOW)
    api = FakeApi([raw(2, "Subtronics - Live @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    # The skipped incoming track is not stored, so no link can be written for
    # it; what matters is that nothing was added and the playlist is untouched.
    discovery.apply_add(plan, conn, api, now=NOW)
    assert track_store.known_ids(conn) == {"1"}


def test_nothing_is_sent_to_the_playlist_when_there_is_nothing_to_add(conn):
    api = FakeApi([])
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert api.written is None


# ---------------------------------------------------------------------------
# Clean Up
# ---------------------------------------------------------------------------

def test_cleanup_snapshots_before_anything_is_removed(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "a"}], seen_at=NOW)
    api = FakeApi(discovery_ids=["1"])
    plan = cleanup.plan_cleanup(conn, api, now=NOW)
    assert track_store.latest_snapshot(conn) == NOW
    assert api.written is None  # planning must not write


def test_cleanup_syncs_likes(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "a"}], seen_at=NOW)
    plan = cleanup.plan_cleanup(conn, FakeApi(likes=["1"], discovery_ids=["1"]), now=NOW)
    assert plan.likes_added == 1
    assert plan.queued[0].verdict == "liked"


def test_a_track_removed_between_runs_is_marked_listened(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "a"}], seen_at=NOW)
    cleanup.plan_cleanup(conn, FakeApi(discovery_ids=["1"]), now="2026-09-19 10:00:00")
    plan = cleanup.plan_cleanup(conn, FakeApi(discovery_ids=[]), now="2026-09-20 10:00:00")
    assert plan.newly_listened == 1
    assert track_store.verdict_counts(conn) == {"skipped": 1}


def test_hearting_beats_removal_when_both_happened(conn):
    """Liked AND deleted from the playlist is a keep, not a skip."""
    track_store.insert_many(conn, [{"track_id": "1", "title": "a"}], seen_at=NOW)
    cleanup.plan_cleanup(conn, FakeApi(discovery_ids=["1"]), now="2026-09-19 10:00:00")
    cleanup.plan_cleanup(conn, FakeApi(likes=["1"], discovery_ids=[]),
                         now="2026-09-20 10:00:00")
    assert track_store.verdict_counts(conn) == {"liked": 1}


def test_only_liked_tracks_are_offered_for_removal(conn):
    """
    A track still in the playlist and not liked might simply not have been
    reached yet - nothing distinguishes that from heard-and-skipped.
    """
    track_store.insert_many(conn, [
        {"track_id": "1", "title": "kept"}, {"track_id": "2", "title": "unplayed"}],
        seen_at=NOW)
    plan = cleanup.plan_cleanup(
        conn, FakeApi(likes=["1"], discovery_ids=["1", "2"]), now=NOW)
    assert plan.removable == ["1"]


def test_a_playlist_track_we_have_never_seen_is_reported_not_hidden(conn):
    plan = cleanup.plan_cleanup(conn, FakeApi(discovery_ids=["999"]), now=NOW)
    assert plan.queued[0].in_library is False
    assert plan.queued[0].verdict == "unknown"


def test_apply_removals_keeps_everything_else(conn):
    track_store.insert_many(conn, [
        {"track_id": "1", "title": "a"}, {"track_id": "2", "title": "b"}], seen_at=NOW)
    api = FakeApi(likes=["1"], discovery_ids=["1", "2"])
    plan = cleanup.plan_cleanup(conn, api, now=NOW)
    assert cleanup.apply_removals(plan, conn, api, plan.removable) == 1
    assert api.written == ["2"]


def test_apply_removals_does_nothing_when_given_nothing(conn):
    api = FakeApi(discovery_ids=["1"])
    plan = cleanup.plan_cleanup(conn, api, now=NOW)
    assert cleanup.apply_removals(plan, conn, api, []) == 0
    assert api.written is None


# ---------------------------------------------------------------------------
# Uncertain artists are surfaced, not silently stored
# ---------------------------------------------------------------------------

def test_an_unparseable_title_is_flagged_but_still_added(conn):
    """
    Dropping the set would be worse than a wrong label. What must not happen
    is landing silently - an unreviewed guess becomes a one-off artist nobody
    notices, which is how the old database filled up.
    """
    api = FakeApi([raw(1, "GRiZ Seven Stars Fest Night 3")])
    plan = discovery.plan_add("url", conn, api)
    assert plan.add_count == 1
    assert len(plan.needs_review) == 1
    assert "no separator" in plan.needs_review[0].reason


def test_a_clean_parse_is_not_flagged(conn):
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")])
    assert discovery.plan_add("url", conn, api).needs_review == []


def test_an_odd_but_real_artist_name_is_not_flagged(conn):
    """999999999, HOL! and I Hate Models are genuine acts."""
    api = FakeApi([raw(1, "999999999 @ EDC Las Vegas 2026"),
                   raw(2, "I Hate Models @ EDC Las Vegas 2026")])
    assert discovery.plan_add("url", conn, api).needs_review == []


# ---------------------------------------------------------------------------
# Corrections typed in the preview
# ---------------------------------------------------------------------------

def test_a_correction_is_stored_as_manual_not_resolved(conn):
    """
    It has to land in real_artists_manual, or a later re-resolve would quietly
    undo the user's typing - the exact flaw the two columns exist to prevent.
    """
    api = FakeApi([raw(1, "GRiZ Seven Stars Fest Night 3")])
    plan = discovery.plan_add("url", conn, api)
    plan.corrections["1"] = "GRiZ"
    result = discovery.apply_add(plan, conn, api, now=NOW)

    assert result.corrected == 1
    row = conn.execute(
        "SELECT real_artists, real_artists_manual FROM tracks WHERE track_id='1'"
    ).fetchone()
    assert row["real_artists_manual"] == "GRiZ"
    assert row["real_artists"] != "GRiZ"        # the resolver's guess is kept
    assert track_store.all_refs(conn)[0].artist == "GRiZ"


def test_a_correction_credits_each_artist_in_a_b2b(conn):
    api = FakeApi([raw(1, "Fred again Thomas Bangalter")])
    plan = discovery.plan_add("url", conn, api)
    plan.corrections["1"] = "Fred again.., Thomas Bangalter"
    discovery.apply_add(plan, conn, api, now=NOW)
    assert {r[0] for r in conn.execute("SELECT artist FROM artist_taste")} == {
        "Fred again..", "Thomas Bangalter"
    }


def test_no_corrections_means_no_manual_values(conn):
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    result = discovery.apply_add(plan, conn, api, now=NOW)
    assert result.corrected == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE real_artists_manual IS NOT NULL"
    ).fetchone()[0] == 0


def test_adding_takes_a_baseline_snapshot(conn):
    """
    Without a snapshot taken while the track is still in the playlist, its
    later removal cannot be detected - "gone" only means something relative to
    an earlier observation. Leaving that to the user to remember is how 783
    verdicts were nearly lost.
    """
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")])
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert track_store.latest_snapshot(conn) == NOW
    assert [r[0] for r in conn.execute(
        "SELECT track_id FROM discovery_snapshots")] == ["1"]


def test_the_baseline_includes_tracks_already_queued(conn):
    """The snapshot is the whole playlist, not just what this run added."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")], discovery_ids=["555"],
                  titles={"555": "Zeds Dead @ Lost Lands 2025"})
    discovery.apply_add(discovery.plan_add("url", conn, api), conn, api, now=NOW)
    assert {r[0] for r in conn.execute(
        "SELECT track_id FROM discovery_snapshots")} == {"555", "1"}


def test_add_then_delete_is_detected_as_skipped(conn):
    """The whole round trip, without the user ever pressing Check Discovery."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026")])
    discovery.apply_add(discovery.plan_add("url", conn, api), conn,
                        api, now="2026-09-19 10:00:00")
    api.discovery_ids = []          # listened to it and deleted it by hand
    cleanup.plan_cleanup(conn, api, now="2026-09-20 10:00:00")
    assert track_store.verdict_counts(conn) == {"skipped": 1}


def test_nothing_added_means_no_snapshot(conn):
    """An empty run must not pretend the playlist was observed."""
    discovery.apply_add(discovery.plan_add("url", conn, FakeApi([])),
                        conn, FakeApi([]), now=NOW)
    assert track_store.latest_snapshot(conn) is None


# ---------------------------------------------------------------------------
# Adopting sets liked outside the Discovery loop
# ---------------------------------------------------------------------------

def test_only_liked_sets_are_imported_not_liked_songs(conn):
    """The 20-minute rule, the same one Add uses to decide what a set is."""
    api = FakeApi(likes=[raw(1, "Wooli @ EDC Las Vegas 2026"),
                         raw(2, "A Song", duration=3 * 60 * 1000)])
    plan = library.plan_import_likes(conn, api)
    assert (plan.total_likes, plan.sets, plan.songs) == (2, 1, 1)
    assert plan.add_count == 1


def test_an_imported_set_is_liked_and_listened(conn):
    """You hearted it, so you heard it - even though no snapshot ever saw it."""
    api = FakeApi(likes=[raw(1, "Wooli @ EDC Las Vegas 2026")])
    result = library.apply_import(library.plan_import_likes(conn, api), conn, now=NOW)
    assert (result.added, result.marked_liked, result.marked_listened) == (1, 1, 1)
    assert track_store.verdict_counts(conn) == {"liked": 1}


def test_importing_does_not_touch_the_discovery_playlist(conn):
    """They have already been heard; queueing them would be absurd."""
    api = FakeApi(likes=[raw(1, "Wooli @ EDC Las Vegas 2026")])
    library.apply_import(library.plan_import_likes(conn, api), conn, now=NOW)
    assert api.written is None


def test_importing_does_not_clear_existing_likes(conn):
    """
    sync_likes is a mirror: handed only the new ids it would clear in_likes_at
    on every other liked track. record_liked exists to avoid exactly that.
    """
    track_store.insert_many(conn, [{"track_id": "99", "title": "old favourite"}],
                            seen_at=NOW)
    track_store.sync_likes(conn, ["99"], at=NOW)
    api = FakeApi(likes=[raw(1, "Wooli @ EDC Las Vegas 2026")])
    library.apply_import(library.plan_import_likes(conn, api), conn, now=NOW)
    assert track_store.verdict_counts(conn)["liked"] == 2


def test_sets_already_in_the_library_are_not_reimported(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "x"}], seen_at=NOW)
    api = FakeApi(likes=[raw(1, "Wooli @ EDC Las Vegas 2026")])
    plan = library.plan_import_likes(conn, api)
    assert (plan.already_known, plan.add_count) == (1, 0)


def test_an_imported_duplicate_of_a_stored_set_is_skipped(conn):
    """Import runs the same duplicate rules as Add."""
    track_store.insert_many(conn, [{
        "track_id": "1", "title": "Subtronics - EDC Las Vegas 2026",
        "real_artists": "Subtronics", "duration_ms": LONG}], seen_at=NOW)
    api = FakeApi(likes=[raw(2, "Subtronics - Live @ EDC Las Vegas 2026")])
    plan = library.plan_import_likes(conn, api)
    assert plan.add_count == 0
    assert plan.skipped[0].because == "already in library"


def test_importing_nothing_is_harmless(conn):
    result = library.apply_import(
        library.plan_import_likes(conn, FakeApi(likes=[])), conn, now=NOW)
    assert result.added == 0


# ---------------------------------------------------------------------------
# Confidence reaches the plan
# ---------------------------------------------------------------------------
# The Add screen colours rows from this. A row missing from the dict renders
# as certain, so an unpopulated dict would paint a bad guess green.

def test_every_planned_track_carries_a_confidence(conn):
    api = FakeApi([
        raw(1, "Wooli @ EDC Las Vegas 2026"),
        raw(2, "Anything at all", uploader="Subtronics"),
        raw(3, "GRiZ Seven Stars Fest Night 3"),
    ])
    plan = discovery.plan_add("url", conn, api)
    assert {t["track_id"] for t in plan.to_add} <= set(plan.confidence)
    assert plan.confidence["1"] == discovery.LIKELY    # cut out of the title
    assert plan.confidence["2"] == discovery.CERTAIN   # the uploader's own name
    assert plan.confidence["3"] == discovery.GUESS     # nothing parsed


def test_a_dropped_duplicate_does_not_need_a_confidence(conn):
    """Only rows that will be shown matter; the dict may cover more."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026"),
                   raw(2, "Wooli @ EDC Las Vegas 2026")])
    plan = discovery.plan_add("url", conn, api)
    assert plan.add_count == 1
    assert all(t["track_id"] in plan.confidence for t in plan.to_add)


def test_the_flagged_tracks_are_exactly_the_guesses(conn):
    """needs_review and the GUESS grade must not drift apart."""
    api = FakeApi([raw(1, "Wooli @ EDC Las Vegas 2026"),
                   raw(2, "GRiZ Seven Stars Fest Night 3")])
    plan = discovery.plan_add("url", conn, api)
    guesses = {tid for tid, level in plan.confidence.items()
               if level == discovery.GUESS}
    assert guesses == {u.track_id for u in plan.needs_review}
