"""
Build the pure modules' config objects from the database.

core/ takes its inputs as frozen config objects and never reads a file. This is
where those objects come from, and where the database read that used to be
baked into _load_canonical_casing_map() now lives.

Public: load_resolution_config, load_duplicate_config, keep_both_pairs,
        add_keep_both, add_repost_account, remove_repost_account,
        set_official_account, add_alias, add_known_event,
        add_location_qualifier, settings_of_kind, OFFICIAL

Imports core/ (allowed: data -> core). Never imports api/, services/ or ui/.
"""

from __future__ import annotations

import sqlite3

from core import artists, duplicates

# The artist_settings kind for "this account posts its own sets".
OFFICIAL = "self_uploader"


def settings_of_kind(conn: sqlite3.Connection, kind: str) -> list[str]:
    """Every value registered under one artist_settings kind, in insert order."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT value FROM artist_settings WHERE kind = ?", (kind,)
        )
    ]


def load_resolution_config(conn: sqlite3.Connection) -> artists.ResolutionConfig:
    """
    Everything core.artists.resolve() needs.

    The canonical casing map is built from the artists already in the database -
    including manual corrections, since those are the spellings worth matching.
    """
    aliases = {
        row["alias"]: row["canonical"]
        for row in conn.execute("SELECT alias, canonical FROM artist_aliases")
    }
    established = (row[0] for row in conn.execute("SELECT artist FROM track_artists"))
    return artists.ResolutionConfig(
        aliases=aliases,
        repost_accounts=frozenset(
            a.lower() for a in settings_of_kind(conn, "repost_account")
        ),
        known_events=tuple(settings_of_kind(conn, "known_event")),
        canonical_casing=artists.build_canonical_casing(established),
    )


def load_duplicate_config(conn: sqlite3.Connection) -> duplicates.DuplicateConfig:
    """Everything core.duplicates needs."""
    return duplicates.DuplicateConfig(
        location_qualifiers=frozenset(
            q.lower() for q in settings_of_kind(conn, "location_qualifier")
        ),
        repost_accounts=frozenset(
            a.lower() for a in settings_of_kind(conn, "repost_account")
        ),
        official_accounts=frozenset(
            a.lower() for a in settings_of_kind(conn, OFFICIAL)
        ),
    )


def keep_both_pairs(conn: sqlite3.Connection) -> set:
    """
    {frozenset({id_a, id_b})} the user decided are NOT duplicates.

    These must never be raised again - 60 of the 62 duplicate pairs in the
    database are already settled this way. Re-asking would be the most annoying
    thing this app could do.
    """
    return {
        frozenset(row)
        for row in conn.execute("SELECT track_id_a, track_id_b FROM duplicate_ignores")
    }


def add_keep_both(conn: sqlite3.Connection, track_id_a: str, track_id_b: str) -> None:
    """Record a keep-both decision. Stored in canonical order, so once only."""
    low, high = sorted((str(track_id_a), str(track_id_b)))
    if low == high:
        return
    conn.execute(
        "INSERT OR IGNORE INTO duplicate_ignores (track_id_a, track_id_b) VALUES (?, ?)",
        (low, high),
    )


def add_repost_account(conn: sqlite3.Connection, name: str) -> None:
    """
    Register an account that posts other people's sets. Clears an official
    mark on the same account, since it cannot be both.

    Only for accounts whose titles actually parse: registering one whose titles
    are prose produces garbage names. See docs/DOMAIN.md.
    """
    _remove(conn, OFFICIAL, name)
    conn.execute("INSERT OR IGNORE INTO artist_settings (kind, value) "
                 "VALUES ('repost_account', ?)", (name,))


def _remove(conn: sqlite3.Connection, kind: str, name: str) -> None:
    conn.execute(
        "DELETE FROM artist_settings WHERE kind = ? AND lower(value) = ?",
        (kind, name.lower()))


def remove_repost_account(conn: sqlite3.Connection, name: str) -> None:
    """Undo add_repost_account. Matches the name whatever its case."""
    _remove(conn, "repost_account", name)


def set_official_account(conn: sqlite3.Connection, name: str, official: bool
                         ) -> None:
    """
    Mark an account as a performer's own, or unmark it.

    Stored as kind 'self_uploader', which the schema has always allowed. An
    account cannot be both a reposter and official, so marking one clears
    the other - the latest decision wins.
    """
    if official:
        _remove(conn, "repost_account", name)
        conn.execute("INSERT OR IGNORE INTO artist_settings (kind, value) "
                     "VALUES (?, ?)", (OFFICIAL, name))
    else:
        _remove(conn, OFFICIAL, name)


def add_known_event(conn: sqlite3.Connection, name: str) -> None:
    """A festival or event name to cut out of titles: "Lost Lands"."""
    conn.execute("INSERT OR IGNORE INTO artist_settings (kind, value) "
                 "VALUES ('known_event', ?)", (name,))


def add_location_qualifier(conn: sqlite3.Connection, word: str) -> None:
    """A city or stage word that tells two recordings apart: "miami"."""
    conn.execute("INSERT OR IGNORE INTO artist_settings (kind, value) "
                 "VALUES ('location_qualifier', ?)", (word.lower(),))


def add_alias(conn: sqlite3.Connection, alias: str, canonical: str) -> None:
    """Map a spelling to its canonical form: TapeB -> Tape B."""
    conn.execute(
        "INSERT INTO artist_aliases (alias, canonical) VALUES (?, ?) "
        "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical",
        (alias, canonical),
    )
