"""
The Find pipeline: search SoundCloud for unheard sets, queue them for review.

Runs without asking anything, like Add. The sources in search_sources.py
gather candidates; this module drops what you have heard, seen, liked or
turned down, applies your filters, runs the SAME triage as Add (artist
resolution, duplicates against the library and within the batch), ranks, and
hands back a queue. Adding the queue is discovery.apply_add, unchanged - so
Find and Add Sets cannot drift apart.

Public: FindOptions, FindPlan, Filters, SOURCES, DEPTHS, DISMISS_REASONS,
        LEARNS, NOT_NOW, plan_find, merge, dismiss, apply_find

Imports core, data and api (via search_sources). Never imports ui/.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from core import duplicates
from core.models import TrackRef
# Filters is re-exported for ui/, which builds a FindOptions but may not
# import core/ directly.
from core.ranking import Filters, pick, rejection, score  # noqa: F401
from data import dismissals, settings, tracks as track_store

from . import search_sources as sources
from .discovery import AddPlan, AddResult, apply_add, triage

# key -> (label, what it does). Order is the order they run and are shown.
SOURCES = {
    "artists": ("Your kept artists",
                "Sets you have not heard by artists you keep, best keepers first."),
    "followed": ("Accounts you follow",
                 "New uploads from everyone you follow on SoundCloud."),
    "circle": ("Their circle",
               "What your top artists like, and who several of them follow."),
    "similar": ("Similar sets",
                "SoundCloud's related tracks for sets you kept recently."),
    "genres": ("Your genres",
               "Long sets in the genres you keep most - mostly artists new "
               "to you."),
}
DEPTHS = tuple(sources.DEPTHS)         # quick, normal, deep - for the UI

# Why a set was turned down. Stored in dismissed_tracks.reason.
DISMISS_REASONS = {
    "style": "Not my style",
    "artist": "Not into this artist",
    "heard": "Already heard it",
    "quality": "Poor recording or quality",
    "not_a_set": "Not really a set",
    "other": "Something else",
}
LEARNS = "artist"      # the reason that keeps its performers out of searches
NOT_NOW = "not_now"    # out of this queue only - not recorded, may come back


@dataclass
class FindOptions:
    sources: frozenset = frozenset(SOURCES)
    seed_artists: int = 25           # how many kept artists to search from
    skip_stop_list: bool = True      # leave out heard-often, never-kept artists
    depth: str = "normal"            # a key of DEPTHS
    filters: Filters = field(default_factory=Filters)


@dataclass
class FindPlan:
    queue: AddPlan = field(default_factory=AddPlan)
    found_via: dict = field(default_factory=dict)    # track_id -> words
    scores: dict = field(default_factory=dict)       # track_id -> float
    seen: int = 0                                    # distinct sets turned up
    left_out: Counter = field(default_factory=Counter)  # reason -> count
    seeds: list = field(default_factory=list)        # artists searched from
    unmatched: list = field(default_factory=list)    # kept, but no account found
    requests: int = 0
    stopped: str = ""                                # why it ended early
    asked: int = 0                                   # max_results requested
    queued_by_source: Counter = field(default_factory=Counter)  # SOURCES key


def _blocked(conn, options: FindOptions) -> set:
    """Casefolded performers no search should offer."""
    blocked = dismissals.dismissed_artists(conn, LEARNS)
    if options.skip_stop_list:
        blocked |= {r["artist"].casefold()
                    for r in track_store.artists_to_skip(conn)}
    return blocked


def _known(conn, filters: Filters) -> dict:
    """{casefolded performer: sets in the library}, when the filter needs it."""
    if filters.max_known_sets is None:
        return {}
    return {r["artist"].casefold(): r["heard"]
            for r in track_store.artist_taste(conn, min_liked=0)}


def _gather(options: FindOptions, conn, liked_raw: list, blocked: set,
            ctx: sources.SearchContext, now: datetime) -> tuple:
    """Run the chosen sources. Returns (seeds, unmatched)."""
    seeds, unmatched = [], []
    f = options.filters
    try:
        if options.sources & {"artists", "circle"}:
            taste = [r for r in track_store.artist_taste(conn)
                     if r["artist"].casefold() not in blocked]
            seeds, unmatched = sources.find_seeds(
                ctx, taste, liked_raw, options.seed_artists)
        if "artists" in options.sources:
            sources.kept_artists(ctx, seeds)
        if "followed" in options.sources:
            sources.followed(ctx)
        if "circle" in options.sources:
            sources.circle(ctx, seeds)
        if "similar" in options.sources:
            sources.similar(ctx, liked_raw)
        if "genres" in options.sources:
            sources.genres(
                ctx, liked_raw, min_ms=f.min_minutes * 60_000,
                max_ms=f.max_minutes * 60_000,
                since=now - timedelta(days=f.within_days) if f.within_days else None)
    except sources.Stopped:
        pass                     # ctx.stopped says why; keep what was found
    return seeds, unmatched


def _fresh(ctx, seen_ids: set, filters: Filters, now, left_out: Counter) -> list:
    """The candidates not already heard, seen or filtered out."""
    fresh = []
    for track_id, candidate in ctx.found.items():
        if track_id in seen_ids:
            left_out["already heard or seen"] += 1
            continue
        why = rejection(candidate.raw, filters, now)
        if why:
            left_out[why] += 1
        else:
            fresh.append(candidate.raw)
    return fresh


def _ref(row: dict) -> TrackRef:
    return TrackRef(row["track_id"], row["title"], row["real_artists"],
                    row.get("uploader", ""), row["duration_ms"])


def _not_in_queue(rows: list, queued: list, conn) -> tuple:
    """Drop new rows that are the same recording as one already queued."""
    if not queued:
        return rows, 0
    cfg = settings.load_duplicate_config(conn)
    queued_refs = [_ref(r) for r in queued]
    kept = [r for r in rows
            if not duplicates.find_candidates(_ref(r), queued_refs, cfg)]
    return kept, len(rows) - len(kept)


def _not_reheard(found, left_out: Counter) -> list:
    """
    Drop better copies of recordings already in the library.

    Add Sets lets an official upload replace a stored repost. Find must not:
    the stored copy may already have been heard, and offering the same
    recording again is exactly what a search for NEW sets should never do.
    """
    better_copies = {new_id for _old, new_id in found.replaced}
    left_out["another copy already heard or seen"] += len(better_copies)
    return [r for r in found.to_add if r["track_id"] not in better_copies]


def plan_find(options: FindOptions, conn, api, *, report=lambda _m: None,
              should_stop=lambda: False, queued: list = (),
              now: datetime | None = None) -> FindPlan:
    """
    Search, filter, triage and rank. Reads only - nothing is written to the
    library or sent to the Discovery playlist.

    `queued` is what is already waiting in the queue, so a second search
    neither repeats a set nor adds another copy of one.
    """
    now = now or datetime.now(timezone.utc)
    report("Reading your likes…")
    liked_raw = api.liked_tracks()
    blocked = _blocked(conn, options)
    ctx = sources.SearchContext(api, report, should_stop,
                                depth=sources.DEPTHS[options.depth])
    seeds, unmatched = _gather(options, conn, liked_raw, blocked, ctx, now)

    plan = FindPlan(seen=len(ctx.found), requests=ctx.requests,
                    stopped=ctx.stopped, unmatched=unmatched,
                    seeds=[s.name for s in seeds],
                    asked=options.filters.max_results)
    report("Filtering and checking for duplicates…")
    seen_ids = (track_store.known_ids(conn) | dismissals.dismissed_ids(conn)
                | {str(t["id"]) for t in liked_raw}
                | {r["track_id"] for r in queued})
    fresh = _fresh(ctx, seen_ids, options.filters, now, plan.left_out)
    if not fresh:
        return plan

    found = triage(fresh, conn, batch_label="duplicate in this search")
    plan.left_out["duplicate"] += len(found.skipped)
    rows = _not_reheard(found, plan.left_out)
    rows, repeats = _not_in_queue(rows, list(queued), conn)
    plan.left_out["already in the queue"] += repeats

    scores = {tid: score(c, now) for tid, c in ctx.found.items()}
    chosen, left_out = pick(
        [(r["track_id"], r["real_artists"], scores[r["track_id"]]) for r in rows],
        options.filters, blocked, _known(conn, options.filters))
    plan.left_out.update(left_out)
    plan.left_out = +plan.left_out          # drop the zero counts
    _fill_queue(plan, found, rows, chosen, ctx, scores)
    return plan


def _fill_queue(plan: FindPlan, found, rows: list, chosen: list, ctx, scores
                ) -> None:
    keep = set(chosen)
    by_id = {r["track_id"]: r for r in rows}
    plan.queue = AddPlan(
        playlist_title="Find",
        to_add=[by_id[tid] for tid in chosen],
        skipped=found.skipped,
        needs_review=[u for u in found.needs_review if u.track_id in keep],
        confidence={tid: found.confidence[tid] for tid in chosen},
    )
    for tid in chosen:
        plan.scores[tid] = scores[tid]
        hits = ctx.found[tid].hits
        plan.found_via[tid] = "; ".join(dict.fromkeys(h.detail for h in hits))
        plan.queued_by_source.update({h.source for h in hits})


def merge(queue: FindPlan, new: FindPlan) -> FindPlan:
    """
    Add a fresh search's results to what is already queued. The counts are
    the latest search's, since those describe what was just done.
    """
    q, n = queue.queue, new.queue
    q.to_add += n.to_add
    q.skipped += n.skipped
    q.needs_review += n.needs_review
    q.replaced_existing += n.replaced_existing
    q.confidence.update(n.confidence)
    queue.found_via.update(new.found_via)
    queue.scores.update(new.scores)
    for name in ("seen", "left_out", "seeds", "unmatched", "requests",
                 "stopped", "asked", "queued_by_source"):
        setattr(queue, name, getattr(new, name))
    return queue


def dismiss(plan: FindPlan, conn, track_ids, reason: str, note: str = "",
            *, now: str | None = None) -> int:
    """
    Take sets out of the queue. Unless the reason is NOT_NOW, record why, so
    no later search or playlist offers them again. Returns how many left.
    """
    ids = {str(t) for t in track_ids}
    q = plan.queue
    leaving = [r for r in q.to_add if r["track_id"] in ids]
    if reason != NOT_NOW:
        stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dismissals.record(conn, [
            {"track_id": r["track_id"], "title": r["title"],
             "artist": q.corrections.get(r["track_id"], r["real_artists"])}
            for r in leaving], reason=reason, note=note, at=stamp)
    q.to_add = [r for r in q.to_add if r["track_id"] not in ids]
    q.needs_review = [u for u in q.needs_review if u.track_id not in ids]
    q.replaced_existing = [p for p in q.replaced_existing if p[1] not in ids]
    for table in (q.corrections, q.confidence, plan.found_via, plan.scores):
        for tid in ids:
            table.pop(tid, None)
    return len(leaving)


def apply_find(plan: FindPlan, conn, api, *, now: str | None = None) -> AddResult:
    """Add the queue to Discovery - exactly what the Add Sets button does."""
    return apply_add(plan.queue, conn, api, now=now)
