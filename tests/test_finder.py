"""
Tests for the Find pipeline: services/finder.py and services/search_sources.py.

A fake SoundCloud serves canned accounts, uploads, likes and follows; the
database underneath is real and in memory. The cases are the promises the
screen makes: nothing heard, seen, liked or turned down comes back; a reason
given once keeps steering later searches; and adding behaves exactly like
Add Sets.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from api.client import NotFound, RateLimited  # noqa: E402
from data import db, dismissals, settings, tracks as track_store  # noqa: E402
from services import finder  # noqa: E402

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
STAMP = "2026-10-07 10:00:00"
HOUR = 60 * 60 * 1000


def track(track_id, title, uploader, user_id, minutes=60):
    return {"id": track_id, "kind": "track", "title": title,
            "duration": minutes * 60 * 1000, "access": "playable",
            "created_at": "2026/09/01 12:00:00 +0000", "favoritings_count": 50,
            "user": {"id": user_id, "username": uploader}}


class FakeFindApi:
    """Serves what it was built with and records every read."""

    def __init__(self, likes=(), users=(), uploads=None, user_likes=None,
                 followings=None, feed=(), related=None, limit_on=None,
                 by_genre=None):
        self.likes, self.users, self.feed = list(likes), list(users), list(feed)
        self.by_genre = by_genre or {}
        self.genre_queries = []               # (genre, limit, filters) sent
        self.uploads = uploads or {}
        self.user_likes = user_likes or {}
        self.followings = followings or {}
        self.related = related or {}
        self.limit_on = limit_on              # read name that answers 429
        self.calls = []
        self.discovery_ids, self.written = [], None

    def _read(self, name, table, key=None):
        self.calls.append(name)
        if name == self.limit_on:
            raise RateLimited(name)
        return list(table if key is None else table.get(str(key), []))

    def liked_tracks(self):
        return self.likes

    def search_users(self, q):
        return self._read("search", [u for u in self.users
                                     if q.casefold() in u["username"].casefold()])

    def user_tracks(self, uid, limit=50):
        return self._read("tracks", self.uploads, uid)

    def user_liked_tracks(self, uid, limit=50):
        return self._read("likes", self.user_likes, uid)

    def user_followings(self, uid, limit=200):
        return self._read("followings", self.followings, uid)

    def followings_tracks(self, limit=100):
        return self._read("feed", self.feed)

    def related_tracks(self, track_id, limit=20):
        return self._read("related", self.related, track_id)

    def genre_tracks(self, genre, limit=50, **filters):
        self.genre_queries.append((genre, limit, filters))
        return self._read("genre", self.by_genre, genre.casefold())

    def discovery_playlist(self):
        return 99, list(self.discovery_ids)

    def set_playlist_tracks(self, playlist_id, track_ids):
        self.written = self.discovery_ids = list(track_ids)

    def track(self, track_id):
        raise NotFound(track_id)


@pytest.fixture
def conn():
    """Wooli is a kept artist: one set in the library, and it is liked."""
    c = db.connect(":memory:")
    settings.add_repost_account(c, "Johnnykham")
    c.execute("INSERT INTO artist_settings VALUES ('known_event', 'Lost Lands')")
    track_store.insert_many(c, [{"track_id": "1", "title": "Wooli @ Bass Canyon 2025",
                                 "uploader": "Wooli", "real_artists": "Wooli",
                                 "duration_ms": HOUR}], seen_at=STAMP)
    track_store.record_liked(c, ["1"], at=STAMP)
    yield c
    c.close()


WOOLI_LIKED = track(1, "Wooli @ Bass Canyon 2025", "Wooli", 50)


def search(conn, api, sources=("artists",), **kwargs):
    options = finder.FindOptions(sources=frozenset(sources),
                                 **{k: v for k, v in kwargs.items()
                                    if k in ("seed_artists", "skip_stop_list",
                                             "depth")})
    if "filters" in kwargs:
        options.filters = kwargs["filters"]
    return finder.plan_find(options, conn, api, now=NOW,
                            should_stop=kwargs.get("should_stop", lambda: False),
                            queued=kwargs.get("queued", ()))


def queued_ids(plan):
    return [r["track_id"] for r in plan.queue.to_add]


# ---------------------------------------------------------------------------
# What gets offered
# ---------------------------------------------------------------------------

def test_an_unheard_set_by_a_kept_artist_is_queued(conn):
    api = FakeFindApi(likes=[WOOLI_LIKED], uploads={"50": [
        WOOLI_LIKED,
        track(2, "Wooli @ Lost Lands 2026", "Wooli", 50),
        track(3, "Wooli - New Single", "Wooli", 50, minutes=3)]})
    plan = search(conn, api)
    assert queued_ids(plan) == ["2"]
    assert "by Wooli" in plan.found_via["2"]
    assert plan.left_out["too short"] == 1
    assert plan.left_out["already heard or seen"] == 1


def test_the_kept_artists_account_is_taken_from_your_likes(conn):
    """No name search needed when you have liked something they posted."""
    api = FakeFindApi(likes=[WOOLI_LIKED], uploads={"50": []})
    search(conn, api)
    assert "search" not in api.calls


def test_a_name_search_only_trusts_an_exact_match(conn):
    """Fan re-upload accounts borrow the name; the biggest exact match wins."""
    api = FakeFindApi(users=[
        {"id": 7, "username": "Wooli Fan Uploads", "followers_count": 9000},
        {"id": 8, "username": "WOOLI", "followers_count": 10},
        {"id": 9, "username": "Wooli", "followers_count": 1000}],
        uploads={"9": [track(2, "Wooli @ Lost Lands 2026", "Wooli", 9)]})
    assert queued_ids(search(conn, api)) == ["2"]


def test_an_artist_with_no_account_is_reported(conn):
    plan = search(conn, FakeFindApi())
    assert plan.unmatched == ["Wooli"]


def test_a_liked_set_the_library_never_saw_is_not_offered(conn):
    hearted = track(5, "Wooli @ Lost Lands 2026", "Wooli", 50)
    api = FakeFindApi(likes=[WOOLI_LIKED, hearted], uploads={"50": [hearted]})
    assert queued_ids(search(conn, api)) == []


def test_a_better_copy_of_a_set_already_heard_is_not_offered(conn):
    """Add Sets may swap in an official upload; Find must not re-queue it."""
    track_store.insert_many(conn, [{
        "track_id": "40", "title": "Wooli @ Lost Lands 2026", "uploader": "Johnnykham",
        "real_artists": "Wooli", "duration_ms": HOUR}], seen_at=STAMP)
    official = track(41, "Wooli - Live @ Lost Lands 2026", "Wooli", 50)
    api = FakeFindApi(likes=[WOOLI_LIKED], uploads={"50": [official]})
    plan = search(conn, api)
    assert queued_ids(plan) == []
    assert plan.left_out["another copy already heard or seen"] == 1


def test_the_stop_list_is_respected_only_when_asked(conn):
    for i in range(4):          # heard four times, never kept
        track_store.insert_many(conn, [{
            "track_id": f"s{i}", "title": f"Skrillex set {i}", "uploader": "Skrillex",
            "real_artists": "Skrillex", "duration_ms": HOUR}], seen_at=STAMP)
    api = FakeFindApi(feed=[track(6, "Skrillex @ Lost Lands 2026", "Skrillex", 60)])
    assert queued_ids(search(conn, api, ["followed"])) == []
    assert queued_ids(search(conn, api, ["followed"], skip_stop_list=False)) == ["6"]


def test_a_set_found_two_ways_ranks_first(conn):
    a = track(10, "Levity @ Lost Lands 2026", "Levity", 61)
    b = track(11, "Ahee @ Lost Lands 2026", "Ahee", 62)
    api = FakeFindApi(likes=[WOOLI_LIKED], feed=[a, b], related={"1": [b]})
    plan = search(conn, api, ["followed", "similar"])
    assert queued_ids(plan) == ["11", "10"]
    assert "who you follow" in plan.found_via["11"]
    assert "like “Wooli @ Bass Canyon 2025”" in plan.found_via["11"]


# ---------------------------------------------------------------------------
# Stopping early
# ---------------------------------------------------------------------------

def test_stop_keeps_what_was_already_found(conn):
    api = FakeFindApi(likes=[WOOLI_LIKED],
                      feed=[track(10, "Levity @ Lost Lands 2026", "Levity", 61)],
                      related={"1": [track(11, "Ahee @ Lost Lands 2026", "Ahee", 62)]})
    plan = search(conn, api, ["followed", "similar"],
                  should_stop=lambda: len(api.calls) >= 1)
    assert queued_ids(plan) == ["10"]
    assert plan.stopped == "stopped"


def test_a_rate_limit_ends_the_search_but_keeps_results(conn):
    api = FakeFindApi(likes=[WOOLI_LIKED],
                      feed=[track(10, "Levity @ Lost Lands 2026", "Levity", 61)],
                      limit_on="related")
    plan = search(conn, api, ["followed", "similar"])
    assert queued_ids(plan) == ["10"]
    assert plan.stopped == "SoundCloud rate limit"


# ---------------------------------------------------------------------------
# The queue across searches
# ---------------------------------------------------------------------------

def test_a_second_search_neither_repeats_nor_doubles_a_queued_set(conn):
    first = track(2, "Wooli @ Lost Lands 2026", "Wooli", 50)
    copy = track(7, "Wooli - Live @ Lost Lands 2026", "Wooli", 50)
    api = FakeFindApi(likes=[WOOLI_LIKED], uploads={"50": [first]})
    queue = search(conn, api)
    api.uploads = {"50": [first, copy]}
    again = search(conn, api, queued=queue.queue.to_add)
    assert queued_ids(again) == []
    assert again.left_out["already in the queue"] == 1


def test_merge_keeps_the_queue_and_adds_the_new(conn):
    api = FakeFindApi(likes=[WOOLI_LIKED],
                      uploads={"50": [track(2, "Wooli @ Lost Lands 2026", "Wooli", 50)]})
    queue = search(conn, api)
    api.feed = [track(10, "Levity @ Lost Lands 2026", "Levity", 61)]
    merged = finder.merge(queue, search(conn, api, ["followed"],
                                        queued=queue.queue.to_add))
    assert sorted(queued_ids(merged)) == ["10", "2"]
    assert set(merged.found_via) == {"2", "10"}


# ---------------------------------------------------------------------------
# Removing with a reason
# ---------------------------------------------------------------------------

def _queue_of_levity(conn, api):
    api.feed = [track(10, "Levity @ Lost Lands 2026", "Levity", 61)]
    return search(conn, api, ["followed"])


def test_a_removed_set_is_remembered_and_never_offered_again(conn):
    api = FakeFindApi()
    plan = _queue_of_levity(conn, api)
    assert finder.dismiss(plan, conn, ["10"], "style", "too melodic", now=STAMP) == 1
    assert queued_ids(plan) == []
    assert queued_ids(_queue_of_levity(conn, api)) == []


def test_not_into_this_artist_leaves_them_out_of_later_searches(conn):
    api = FakeFindApi()
    finder.dismiss(_queue_of_levity(conn, api), conn, ["10"], finder.LEARNS,
                   now=STAMP)
    api.feed = [track(12, "Levity @ Forbidden Kingdom 2026", "Levity", 61)]
    plan = search(conn, api, ["followed"])
    assert queued_ids(plan) == []
    assert plan.left_out["artist left out"] == 1


def test_the_corrected_artist_is_the_one_that_is_learned(conn):
    api = FakeFindApi()
    plan = _queue_of_levity(conn, api)
    plan.queue.corrections["10"] = "Levity, Ahee"
    finder.dismiss(plan, conn, ["10"], finder.LEARNS, now=STAMP)
    assert dismissals.dismissed_artists(conn, finder.LEARNS) == {"levity", "ahee"}


def test_not_now_is_not_remembered(conn):
    api = FakeFindApi()
    plan = _queue_of_levity(conn, api)
    finder.dismiss(plan, conn, ["10"], finder.NOT_NOW)
    assert queued_ids(plan) == []
    assert dismissals.dismissed_ids(conn) == set()
    assert queued_ids(_queue_of_levity(conn, api)) == ["10"]


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------

def test_adding_the_queue_behaves_exactly_like_add_sets(conn):
    api = FakeFindApi()
    plan = _queue_of_levity(conn, api)
    plan.queue.corrections["10"] = "Levity, Ahee"
    api.discovery_ids = ["1"]
    result = finder.apply_find(plan, conn, api, now=STAMP)
    assert (result.added_to_library, result.added_to_playlist) == (1, 1)
    assert api.written == ["10", "1"]          # Levity... before Wooli...
    artist = conn.execute(
        "SELECT artist FROM track_artists WHERE track_id = '10'").fetchone()[0]
    assert artist == "Levity, Ahee"
    assert track_store.latest_snapshot(conn) == STAMP


# ---------------------------------------------------------------------------
# New artists, genres and depth
# ---------------------------------------------------------------------------

def _wooli_and_a_b2b(conn):
    return FakeFindApi(likes=[WOOLI_LIKED], uploads={"50": [
        track(2, "Wooli @ Lost Lands 2026", "Wooli", 50),
        track(3, "Wooli b2b Nobody Yet @ Lost Lands 2026", "Wooli b2b Nobody Yet", 50)]})


def test_never_heard_leaves_out_artists_already_in_the_library(conn):
    """Wooli has one set in the library; only the b2b brings a new name."""
    plan = search(conn, _wooli_and_a_b2b(conn),
                  filters=finder.Filters(max_known_sets=0))
    assert queued_ids(plan) == ["3"]
    assert plan.left_out["artist already familiar"] == 1


def test_once_at_most_still_allows_an_artist_heard_once(conn):
    plan = search(conn, _wooli_and_a_b2b(conn),
                  filters=finder.Filters(max_known_sets=1))
    assert sorted(queued_ids(plan)) == ["2", "3"]


DUBSTEP_SET = dict(track(90, "Wooli @ Bass Canyon 2024", "Wooli", 50), genre="Dubstep")


def test_genre_search_reads_your_kept_genres_with_filters_sent_to_soundcloud(conn):
    likes = [dict(WOOLI_LIKED, genre="Dubstep"), DUBSTEP_SET,
             dict(track(91, "Odd one @ X 2025", "Odd", 70), genre="XOTIX")]
    api = FakeFindApi(likes=likes, by_genre={"dubstep": [
        dict(track(20, "Nobody Yet @ Lost Lands 2026", "Nobody Yet", 80),
             genre="Dubstep", created_at="2026/10/01 12:00:00 +0000")]})
    plan = search(conn, api, ["genres"],
                  filters=finder.Filters(min_minutes=45, within_days=30))
    assert queued_ids(plan) == ["20"]
    assert "2 of your kept sets are Dubstep" in plan.found_via["20"]
    [(genre, limit, sent)] = api.genre_queries     # XOTIX: only one set, skipped
    assert genre == "Dubstep" and limit == 100
    assert sent["min_ms"] == 45 * 60_000
    assert sent["since"] == "2026-09-07 00:00:00"


def test_deep_reads_more_per_request(conn):
    likes = [dict(WOOLI_LIKED, genre="Dubstep"), DUBSTEP_SET]
    api = FakeFindApi(likes=likes)
    search(conn, api, ["genres"], depth="deep")
    assert api.genre_queries[0][1] == 200


def test_the_plan_says_which_sources_filled_the_queue(conn):
    a = track(10, "Levity @ Lost Lands 2026", "Levity", 61)
    b = track(11, "Ahee @ Lost Lands 2026", "Ahee", 62)
    api = FakeFindApi(likes=[WOOLI_LIKED], feed=[a, b], related={"1": [b]})
    plan = search(conn, api, ["followed", "similar"])
    assert plan.queued_by_source == {"followed": 2, "similar": 1}
    assert plan.asked == 30
