"""
Reading and writing tracks.

Verdicts and artist names are read through the views (track_verdicts,
track_artists, artist_taste) and never recomputed here. Two write rules are
enforced by this module and must not be worked around:

  * sync_likes mirrors SoundCloud - it clears in_likes_at as well as setting it
  * nothing here ever writes manual_verdict or real_artists_manual

Public: all_refs, known_ids, titles, insert_many, set_duplicate_of, set_manual_artist,
        sync_likes, record_liked, mark_listened, record_discovery_snapshot,
        infer_listened,
        latest_snapshot,
        artist_taste, artists_to_skip, verdict_counts

Imports core/ for TrackRef only (allowed: data -> core).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

from core.models import TrackRef

_REF_QUERY = """
    SELECT t.track_id, t.title, a.artist, t.uploader, t.duration_ms,
           COALESCE(t.duplicate_of, '') AS duplicate_of
    FROM tracks t
    JOIN track_artists a USING (track_id)
"""


def _to_ref(row: sqlite3.Row) -> TrackRef:
    return TrackRef(
        track_id=row["track_id"],
        title=row["title"],
        artist=row["artist"],
        uploader=row["uploader"],
        duration_ms=row["duration_ms"],
        duplicate_of=row["duplicate_of"],
    )


def all_refs(conn: sqlite3.Connection) -> list[TrackRef]:
    """Every track as core/ sees it, with manual artist corrections applied."""
    return [_to_ref(r) for r in conn.execute(_REF_QUERY)]


def known_ids(conn: sqlite3.Connection) -> set:
    """Every track id ever recorded - the 'have I seen this' check."""
    return {r[0] for r in conn.execute("SELECT track_id FROM tracks")}


def titles(conn: sqlite3.Connection, track_ids: Iterable[str]) -> dict:
    """{track_id: title} for those of `track_ids` the library holds."""
    wanted = {str(t) for t in track_ids}
    return {r[0]: r[1] for r in conn.execute("SELECT track_id, title FROM tracks")
            if r[0] in wanted}


def insert_many(conn: sqlite3.Connection, rows: Iterable[dict], *, seen_at: str) -> int:
    """
    Add newly discovered tracks. Existing ids are left untouched, so re-running
    an import cannot overwrite a correction or a verdict.

    Each row needs track_id, title, uploader, real_artists, duration_ms and may
    carry uploaded_at and source_playlist.
    """
    added = 0
    for row in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO tracks
                   (track_id, title, uploader, real_artists, duration_ms,
                    uploaded_at, first_seen_at, source_playlist)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(row["track_id"]),
                row.get("title", ""),
                row.get("uploader", ""),
                row.get("real_artists", ""),
                int(row.get("duration_ms") or 0),
                row.get("uploaded_at"),
                seen_at,
                row.get("source_playlist"),
            ),
        )
        added += cur.rowcount
    return added


def set_duplicate_of(conn: sqlite3.Connection, track_id: str, target_id: str | None
                     ) -> None:
    """Flag a track as the same recording as another, or clear the flag."""
    conn.execute(
        "UPDATE tracks SET duplicate_of = ? WHERE track_id = ?",
        (target_id, str(track_id)),
    )


def set_manual_artist(conn: sqlite3.Connection, track_id: str, artist: str | None
                      ) -> None:
    """
    Record a human correction to the performer. The resolver must never call
    this - it exists precisely so a re-resolve cannot destroy a typed value.
    """
    conn.execute(
        "UPDATE tracks SET real_artists_manual = ? WHERE track_id = ?",
        (artist or None, str(track_id)),
    )


def sync_likes(conn: sqlite3.Connection, liked_ids: Iterable[str], *, at: str) -> tuple:
    """
    Mirror SoundCloud's likes onto in_likes_at. Returns (added, cleared).

    A mirror, not an append: a track absent from the API is cleared, so
    un-hearting propagates. The old mark_liked() only ever set the flag, which
    is why a like could never be taken back.
    """
    wanted = {str(i) for i in liked_ids}
    current = {r[0] for r in conn.execute(
        "SELECT track_id FROM tracks WHERE in_likes_at IS NOT NULL")}
    known = known_ids(conn)

    to_add = (wanted & known) - current
    to_clear = current - wanted

    conn.executemany(
        "UPDATE tracks SET in_likes_at = ? WHERE track_id = ?",
        [(at, tid) for tid in to_add],
    )
    conn.executemany(
        "UPDATE tracks SET in_likes_at = NULL WHERE track_id = ?",
        [(tid,) for tid in to_clear],
    )
    return len(to_add), len(to_clear)


def record_liked(conn: sqlite3.Connection, track_ids: Iterable[str], *, at: str
                 ) -> int:
    """
    Set in_likes_at on these and no others. Additive on purpose.

    ONLY for tracks read straight out of the likes list, where membership is
    not in question. This is NOT a substitute for sync_likes: it cannot clear
    an un-hearted track, and using it as the general path would recreate the
    old mark_liked() bug where a like could never be taken back. The next
    sync_likes reconciles everything anyway.
    """
    ids = [str(t) for t in track_ids]
    conn.executemany(
        "UPDATE tracks SET in_likes_at = ? WHERE track_id = ? AND in_likes_at IS NULL",
        [(at, tid) for tid in ids],
    )
    return len(ids)


def mark_listened(conn: sqlite3.Connection, track_ids: Iterable[str], *, at: str
                  ) -> int:
    """
    Record that these were heard, without disturbing one already marked.

    Used when adopting a liked set from outside the Discovery loop: a like is
    proof it was listened to, even though no snapshot ever saw it.
    """
    ids = [str(t) for t in track_ids]
    cur = conn.executemany(
        "UPDATE tracks SET listened_at = ? WHERE track_id = ? AND listened_at IS NULL",
        [(at, tid) for tid in ids],
    )
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def record_discovery_snapshot(conn: sqlite3.Connection, track_ids: Sequence[str],
                              *, at: str) -> int:
    """
    Record what is in the Discovery playlist right now.

    Taken every run, BEFORE anything is removed. Membership diffs are the only
    way to learn a track was heard, because the user deletes tracks by hand in
    SoundCloud. Losing this history once already cost 783 rows of signal.

    The run is always recorded, even for an empty playlist - an empty Discovery
    is the state that says everything queued has been worked through, and it is
    the state the playlist is in today.
    """
    conn.execute(
        "INSERT OR IGNORE INTO discovery_runs (snapshot_at) VALUES (?)", (at,)
    )
    conn.executemany(
        "INSERT OR IGNORE INTO discovery_snapshots (snapshot_at, track_id) VALUES (?,?)",
        [(at, str(tid)) for tid in track_ids],
    )
    return len(track_ids)


def latest_snapshot(conn: sqlite3.Connection) -> str | None:
    """
    Timestamp of the most recent Discovery run, or None.

    Read from discovery_runs, not from membership: a run that found an empty
    playlist has no membership rows but still happened.
    """
    return conn.execute("SELECT MAX(snapshot_at) FROM discovery_runs").fetchone()[0]


def infer_listened(conn: sqlite3.Connection) -> int:
    """
    Mark as listened every track that was in Discovery before and is not in the
    latest snapshot. Returns how many were newly marked.

    Gone from the playlist means heard - the user removes what they have worked
    through. Compares against ALL earlier snapshots rather than only the
    previous one, so a skipped run cannot lose a track.
    """
    newest = latest_snapshot(conn)
    if newest is None:
        return 0
    cur = conn.execute(
        """UPDATE tracks SET listened_at = ?
            WHERE listened_at IS NULL
              AND track_id IN (SELECT track_id FROM discovery_snapshots
                                WHERE snapshot_at < ?)
              AND track_id NOT IN (SELECT track_id FROM discovery_snapshots
                                    WHERE snapshot_at = ?)""",
        (newest, newest, newest),
    )
    return cur.rowcount


def verdict_counts(conn: sqlite3.Connection) -> dict:
    """{verdict: count} across every track."""
    return {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT verdict, COUNT(*) FROM track_verdicts GROUP BY verdict")
    }


def artist_taste(conn: sqlite3.Connection, *, min_liked: int = 1) -> list:
    """Artists you have kept something from, most-liked first."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT artist, heard, liked, pending FROM artist_taste
                WHERE liked >= ? ORDER BY liked DESC, artist""",
            (min_liked,),
        )
    ]


def artists_to_skip(conn: sqlite3.Connection, *, min_heard: int = 4) -> list:
    """
    Artists heard repeatedly and never kept - candidates to stop queueing.

    Check the name before acting on it: an unregistered festival account shows
    up here looking exactly like an artist you dislike. See docs/DOMAIN.md.
    """
    return [
        dict(r)
        for r in conn.execute(
            """SELECT artist, heard FROM artist_taste
                WHERE liked = 0 AND heard >= ? ORDER BY heard DESC, artist""",
            (min_heard,),
        )
    ]
