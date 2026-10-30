"""
The Clean Up pipeline: learn what was listened to, then tidy the playlist.

Order matters and is not negotiable. Likes are synced and Discovery is
snapshotted BEFORE anything is removed, because removal is the very signal
being read: a track that was in the playlist and is gone was heard. Remove
first and that fact is destroyed, permanently - it is how 783 rows of signal
were nearly lost.

Public: CleanupPlan, QueuedTrack, plan_cleanup, apply_removals

Imports data and api. Never imports ui/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from data import tracks as track_store


@dataclass
class QueuedTrack:
    """One track sitting in the Discovery playlist right now."""

    track_id: str
    title: str
    artist: str
    verdict: str          # liked | skipped | pending | unknown
    in_library: bool


@dataclass
class CleanupPlan:
    playlist_id: str = ""
    snapshot_at: str = ""
    likes_added: int = 0
    likes_cleared: int = 0
    newly_listened: int = 0
    queued: list = field(default_factory=list)       # QueuedTrack
    removable: list = field(default_factory=list)    # track ids safe to remove

    @property
    def queued_count(self) -> int:
        return len(self.queued)


def plan_cleanup(conn, api, *, now: str | None = None) -> CleanupPlan:
    """
    Sync likes, snapshot Discovery, work out what was heard, and report.

    This writes to the database - the snapshot and the inferred verdicts are
    the whole point - but sends nothing to SoundCloud beyond reads.
    """
    stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plan = CleanupPlan(snapshot_at=stamp)

    # 1. Mirror the likes. Must come first: a track the user hearted AND
    #    deleted from the playlist would otherwise be recorded as a skip.
    liked_ids = api.liked_track_ids()
    plan.likes_added, plan.likes_cleared = track_store.sync_likes(
        conn, liked_ids, at=stamp)

    # 2. Record what is in the playlist, before touching it.
    playlist_id, member_ids = api.discovery_playlist()
    plan.playlist_id = str(playlist_id)
    track_store.record_discovery_snapshot(conn, member_ids, at=stamp)

    # 3. Anything that has left the playlist since a previous run was heard.
    plan.newly_listened = track_store.infer_listened(conn)
    conn.commit()

    # 4. Report the current queue.
    known = {
        row["track_id"]: row
        for row in conn.execute(
            """SELECT t.track_id, t.title, a.artist, v.verdict
                 FROM tracks t
                 JOIN track_artists a USING (track_id)
                 JOIN track_verdicts v USING (track_id)"""
        )
    }
    for track_id in member_ids:
        row = known.get(track_id)
        plan.queued.append(
            QueuedTrack(
                track_id=track_id,
                title=row["title"] if row else "(not in library)",
                artist=row["artist"] if row else "",
                verdict=row["verdict"] if row else "unknown",
                in_library=row is not None,
            )
        )

    # 5. A liked track has served its purpose here - it lives in the user's
    #    likes now. Anything else might simply not have been reached yet, and
    #    nothing distinguishes "heard and skipped" from "not yet played" while
    #    it is still in the playlist.
    plan.removable = [q.track_id for q in plan.queued if q.verdict == "liked"]
    return plan


def apply_removals(plan: CleanupPlan, conn, api, track_ids) -> int:
    """
    Remove the given tracks from Discovery. Returns how many were removed.

    SoundCloud has no remove endpoint, so the full keep-list is PUT. The list
    is built from this plan's snapshot rather than re-fetched, so a track added
    by another device mid-run cannot be silently deleted by a stale read.
    """
    removing = {str(t) for t in track_ids}
    if not removing:
        return 0

    keeping = [q.track_id for q in plan.queued if q.track_id not in removing]
    api.set_playlist_tracks(plan.playlist_id, keeping)
    return len(plan.queued) - len(keeping)
