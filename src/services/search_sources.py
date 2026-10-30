"""
Where the Find search looks. One function per source, each adding Candidates.

Sources only gather - every filter, the "already heard" check, duplicates and
ranking happen afterwards in services/finder.py, once, over everything found.
A set found by two sources is one Candidate with two Hits, which is what
lets it outrank a set found only once.

Weights are relative, not tuned: they order the queue, and every hit is shown
next to the set, so a wrong order is visible rather than silent.

Every Hit's `source` is the finder.SOURCES key that produced it, so the queue
can say which sources earned their place.

Public: SearchContext, Seed, Stopped, Depth, DEPTHS, find_seeds,
        kept_artists, followed, circle, similar, top_genres, genres

Imports core, api, and services.discovery for the set-length rule. Never
imports data/ or ui/ - finder.py hands in what the library knows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from api.client import ApiError, NotFound, RateLimited
from core.ranking import Candidate, Hit

from .discovery import MIN_DURATION_MS


@dataclass(frozen=True)
class Depth:
    """How far each source reads. Deeper costs time, not just requests."""

    page: int              # tracks per read - SoundCloud allows up to 200
    circle_seeds: int      # top artists whose likes and follows are read
    circle_accounts: int   # accounts followed by several of them, then read
    similar_seeds: int     # recently kept sets whose related tracks are read
    genres: int            # your most-kept genres searched


# Measured on a 25-artist search: about two thirds of everything most
# sources return is songs under 20 minutes, so reading more is the only way
# a narrow source yields more sets. Genre search is the exception - it is
# length-filtered by SoundCloud and returns almost nothing but sets.
DEPTHS = {
    "quick": Depth(page=50, circle_seeds=6, circle_accounts=5,
                   similar_seeds=5, genres=2),
    "normal": Depth(page=100, circle_seeds=12, circle_accounts=10,
                    similar_seeds=10, genres=3),
    "deep": Depth(page=200, circle_seeds=20, circle_accounts=25,
                  similar_seeds=25, genres=5),
}


class Stopped(Exception):
    """Gathering ends early - the user pressed Stop, or SoundCloud said 429."""


@dataclass
class SearchContext:
    api: object
    report: Callable[[str], None]
    should_stop: Callable[[], bool]
    depth: Depth = DEPTHS["normal"]
    found: dict = field(default_factory=dict)    # track_id -> Candidate
    requests: int = 0
    errors: int = 0
    stopped: str = ""                             # why it ended early, if it did

    def call(self, fn, *args, **kwargs) -> list:
        """
        One read. A missing or failing item is skipped, not fatal: one
        deleted account must not cost the whole search. A rate limit or a
        Stop press ends gathering; what was found so far is kept.
        """
        if self.should_stop():
            self.stopped = "stopped"
            raise Stopped
        self.requests += 1
        try:
            return fn(*args, **kwargs)
        except RateLimited as exc:
            self.stopped = "SoundCloud rate limit"
            raise Stopped from exc
        except NotFound:
            return []
        except ApiError:
            self.errors += 1
            return []

    def add(self, raw: dict, hit: Hit) -> None:
        if raw.get("kind", "track") != "track" or "id" not in raw:
            return
        candidate = self.found.setdefault(str(raw["id"]), Candidate(raw))
        candidate.hits.append(hit)


@dataclass(frozen=True)
class Seed:
    """A kept artist, matched to their SoundCloud account."""

    name: str
    user_id: str
    liked: int
    heard: int

    @property
    def keep_rate(self) -> float:
        """Smoothed, so one lucky like does not outrank six of fourteen."""
        return (self.liked + 1) / (self.heard + 2)


def find_seeds(ctx: SearchContext, taste: list, liked_raw: list, limit: int
               ) -> tuple:
    """
    Match the best `limit` kept artists to accounts. Returns (seeds, unmatched).

    An account you have liked a set from is trusted outright. Otherwise the
    name is searched and only an exact (case-blind) username match counts -
    the biggest such account, since fan re-uploads borrow the name too.
    """
    from_likes = {}
    for raw in liked_raw:
        user = raw.get("user") or {}
        if user.get("username") and user.get("id"):
            from_likes.setdefault(user["username"].casefold(), user)

    ranked = sorted(taste, key=lambda r: (
        -r["liked"], -(r["liked"] + 1) / (r["heard"] + 2)))
    seeds, unmatched = [], []
    for i, row in enumerate(ranked[:limit], 1):
        ctx.report(f"Finding your artists on SoundCloud ({i}/{min(limit, len(ranked))})…")
        name = row["artist"]
        user = from_likes.get(name.casefold())
        if user is None:
            exact = [u for u in ctx.call(ctx.api.search_users, name)
                     if (u.get("username") or "").casefold() == name.casefold()]
            user = max(exact, key=lambda u: u.get("followers_count") or 0,
                       default=None)
        if user is None:
            unmatched.append(name)
            continue
        seeds.append(Seed(name, str(user["id"]), row["liked"], row["heard"]))
    return seeds, unmatched


def kept_artists(ctx: SearchContext, seeds: list) -> None:
    """Every upload by an artist you keep, weighted by how often you keep them."""
    for i, seed in enumerate(seeds, 1):
        ctx.report(f"Reading {seed.name}'s uploads ({i}/{len(seeds)})…")
        detail = f"by {seed.name} - you kept {seed.liked} of {seed.heard}"
        for raw in ctx.call(ctx.api.user_tracks, seed.user_id, ctx.depth.page):
            ctx.add(raw, Hit("artists", 2 + 4 * seed.keep_rate, detail))


def followed(ctx: SearchContext) -> None:
    """Recent uploads from accounts you follow on SoundCloud."""
    ctx.report("Reading new uploads from accounts you follow…")
    for raw in ctx.call(ctx.api.followings_tracks, ctx.depth.page):
        uploader = (raw.get("user") or {}).get("username", "someone")
        ctx.add(raw, Hit("followed", 2.0, f"new from {uploader}, who you follow"))


def circle(ctx: SearchContext, seeds: list) -> None:
    """
    What your top artists like, and who several of them follow.

    One artist following an account is a favour to a friend; three following
    it is a scene. Only accounts followed by two or more are read.
    """
    top = seeds[:ctx.depth.circle_seeds]
    followed_by = defaultdict(list)            # user id -> [seed names]
    accounts = {}
    for i, seed in enumerate(top, 1):
        ctx.report(f"Reading what {seed.name} likes and follows ({i}/{len(top)})…")
        for raw in ctx.call(ctx.api.user_liked_tracks, seed.user_id,
                            ctx.depth.page):
            ctx.add(raw, Hit("circle", 2.0, f"liked by {seed.name}"))
        for user in ctx.call(ctx.api.user_followings, seed.user_id):
            followed_by[str(user["id"])].append(seed.name)
            accounts[str(user["id"])] = user

    seed_ids = {s.user_id for s in seeds}
    shared = sorted(((uid, names) for uid, names in followed_by.items()
                     if len(names) >= 2 and uid not in seed_ids),
                    key=lambda kv: -len(kv[1]))[:ctx.depth.circle_accounts]
    for i, (uid, names) in enumerate(shared, 1):
        name = accounts[uid].get("username", "an account")
        ctx.report(f"Reading {name}, followed by {len(names)} of your artists "
                   f"({i}/{len(shared)})…")
        detail = f"{name} is followed by {len(names)} of your artists"
        for raw in ctx.call(ctx.api.user_tracks, uid, ctx.depth.page):
            ctx.add(raw, Hit("circle", 1.0 + len(names), detail))


def similar(ctx: SearchContext, liked_raw: list) -> None:
    """SoundCloud's related tracks for the sets you liked most recently."""
    recent = [t for t in liked_raw if int(t.get("duration") or 0)
              >= MIN_DURATION_MS][:ctx.depth.similar_seeds]
    for i, kept in enumerate(recent, 1):
        title = (kept.get("title") or "a set you kept")[:40]
        ctx.report(f"Finding sets like “{title}” ({i}/{len(recent)})…")
        for raw in ctx.call(ctx.api.related_tracks, kept["id"],
                            min(ctx.depth.page, 50)):
            ctx.add(raw, Hit("similar", 1.5, f"like “{title}”"))


def top_genres(liked_raw: list, limit: int) -> list:
    """
    [(genre, sets)] for the genres of your liked sets, most kept first.

    Genre is free text typed by the uploader, so a genre needs at least two
    of your sets behind it - one stray "XOTIX" is a label, not a taste.
    """
    counts, spelling = Counter(), {}
    for raw in liked_raw:
        genre = (raw.get("genre") or "").strip()
        if genre and int(raw.get("duration") or 0) >= MIN_DURATION_MS:
            counts[genre.casefold()] += 1
            spelling.setdefault(genre.casefold(), genre)
    return [(spelling[g], n) for g, n in counts.most_common() if n >= 2][:limit]


def genres(ctx: SearchContext, liked_raw: list, *, min_ms: int, max_ms: int,
           since: datetime | None) -> None:
    """
    Long sets in the genres you keep most - the widest net for new names.

    Weighted lowest of all the sources: the only thing tying a result to you
    is its genre, so anything found another way as well should outrank it.
    """
    chosen = top_genres(liked_raw, ctx.depth.genres)
    stamp = since.strftime("%Y-%m-%d %H:%M:%S") if since else None
    for i, (genre, kept) in enumerate(chosen, 1):
        ctx.report(f"Searching {genre} sets ({i}/{len(chosen)})…")
        detail = f"{genre} - {kept} of your kept sets are {genre}"
        for raw in ctx.call(ctx.api.genre_tracks, genre, ctx.depth.page,
                            min_ms=min_ms, max_ms=max_ms, since=stamp):
            ctx.add(raw, Hit("genres", 1.0, detail))
