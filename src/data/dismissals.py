"""
Sets turned down from the Find queue, and why.

Kept apart from `tracks` on purpose: a dismissed set was never heard, so it
must never surface as a verdict or count toward an artist. This module only
records the fact and answers "has this been turned down before?".

Public: record, dismissed_ids, dismissed_artists

Imports nothing from src/. Reason codes belong to services/finder.py; this
module stores whatever code it is handed.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable


def record(conn: sqlite3.Connection, rows: Iterable[dict], *, reason: str,
           note: str | None, at: str) -> int:
    """
    Store each row ({track_id, title, artist}) as dismissed. Returns how many.

    Re-dismissing a set replaces the earlier reason: the latest decision is
    the one that should steer the next search.
    """
    count = 0
    for row in rows:
        conn.execute(
            """INSERT OR REPLACE INTO dismissed_tracks
                   (track_id, title, artist, reason, note, dismissed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(row["track_id"]), row.get("title", ""),
             row.get("artist", ""), reason, note or None, at),
        )
        count += 1
    return count


def dismissed_ids(conn: sqlite3.Connection) -> set:
    """Every track id ever turned down - added to the 'seen it' check."""
    return {r[0] for r in conn.execute("SELECT track_id FROM dismissed_tracks")}


def dismissed_artists(conn: sqlite3.Connection, reason: str) -> set:
    """
    Every performer named on a set dismissed for `reason`, casefolded.

    A b2b row "A, B" yields both names - the dismiss dialog says so before the
    user confirms, so nobody is dropped by surprise.
    """
    names = set()
    for (artist,) in conn.execute(
            "SELECT artist FROM dismissed_tracks WHERE reason = ?", (reason,)):
        names |= {n.strip().casefold() for n in artist.split(",") if n.strip()}
    return names
