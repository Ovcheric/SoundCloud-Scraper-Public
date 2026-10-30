"""
Adopt sets you hearted outside the Discovery loop.

A set heard through a friend, a weekly wave or an artist's own page never
passes through Discovery, so the app has no idea it exists - it does not count
toward your taste, and a future playlist containing it would queue it again as
though it were new.

A liked track of 20 minutes or more is a set you kept, which is exactly the
rule the Add pipeline already uses to decide what a set is. Those are brought
in already marked liked and listened; they are NOT queued to Discovery, because
you have plainly already heard them.

Public: ImportPlan, plan_import_likes, apply_import

Imports core, data and api. Never imports ui/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from data import tracks as track_store

from .discovery import MIN_DURATION_MS, triage


@dataclass
class ImportPlan:
    total_likes: int = 0
    sets: int = 0                                   # likes long enough to be sets
    songs: int = 0                                  # likes too short to be sets
    already_known: int = 0
    to_add: list = field(default_factory=list)      # dicts ready for insert_many
    skipped: list = field(default_factory=list)     # Skipped, duplicates
    needs_review: list = field(default_factory=list)  # Unsure
    replaced_existing: list = field(default_factory=list)

    @property
    def add_count(self) -> int:
        return len(self.to_add)


@dataclass
class ImportResult:
    added: int = 0
    marked_liked: int = 0
    marked_listened: int = 0
    marked_duplicate: int = 0


def plan_import_likes(conn, api) -> ImportPlan:
    """
    Work out which liked sets the library is missing. Reads only.

    Runs the same triage as Add - same artist resolution, same duplicate rules -
    so an import cannot introduce a copy of something already stored.
    """
    liked = api.liked_tracks()
    plan = ImportPlan(total_likes=len(liked))

    sets = [t for t in liked if int(t.get("duration") or 0) >= MIN_DURATION_MS]
    plan.sets = len(sets)
    plan.songs = len(liked) - len(sets)

    known = track_store.known_ids(conn)
    fresh = [t for t in sets if str(t["id"]) not in known]
    plan.already_known = len(sets) - len(fresh)
    if not fresh:
        return plan

    found = triage(fresh, conn, source_url=None,
                   batch_label="duplicate in your likes")
    plan.to_add = found.to_add
    plan.skipped = found.skipped
    plan.needs_review = found.needs_review
    plan.replaced_existing = found.replaced
    return plan


def apply_import(plan: ImportPlan, conn, *, now: str | None = None) -> ImportResult:
    """
    Store the missing sets as already liked and already listened.

    Nothing is sent to SoundCloud: these tracks are in your likes already, and
    they must not go to Discovery - you have heard them, which is why you
    hearted them.
    """
    stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = ImportResult()
    if not plan.to_add:
        return result

    ids = [row["track_id"] for row in plan.to_add]
    result.added = track_store.insert_many(conn, plan.to_add, seen_at=stamp)

    # record_liked, NOT sync_likes. sync_likes is a mirror: handed only these
    # ids it would clear in_likes_at on every other liked track in the library.
    result.marked_liked = track_store.record_liked(conn, ids, at=stamp)
    result.marked_listened = track_store.mark_listened(conn, ids, at=stamp)

    for skipped in plan.skipped:
        if skipped.because == "already in library":
            track_store.set_duplicate_of(conn, skipped.track_id, skipped.duplicate_of)
            result.marked_duplicate += 1
    for old_id, new_id in plan.replaced_existing:
        track_store.set_duplicate_of(conn, old_id, new_id)
        result.marked_duplicate += 1

    conn.commit()
    return result
