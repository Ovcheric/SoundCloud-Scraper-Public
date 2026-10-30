"""
SQLite connection and schema setup.

The only module that decides where the database lives or how it is opened.
Everything else takes an open connection, which is what makes the rest of
data/ testable against an in-memory database.

Public: connect, initialize, DEFAULT_DB_PATH, SCHEMA_PATH

Imports nothing from src/. Opening a database is I/O, so errors surface here
rather than in core/.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "discovery.db"


def connect(path: str | Path | None = None, *, apply_schema: bool = True
            ) -> sqlite3.Connection:
    """
    Open the database with foreign keys on and rows accessible by name.

    Foreign keys are OFF by default in SQLite and must be set per connection,
    which is why this is the only sanctioned way in. duplicate_of points at
    tracks(track_id), so without it a duplicate can outlive its target.

    The schema is applied on every open, not just to a new file. Every
    statement is IF NOT EXISTS, so on an existing database this only adds
    what is new - which is how a new table reaches a discovery.db that already
    holds history, without ever rebuilding it. Nothing is altered or dropped.
    """
    target = Path(path) if path is not None else DEFAULT_DB_PATH

    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if apply_schema:
        initialize(conn)
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Apply schema.sql. Safe to re-run - every statement is IF NOT EXISTS."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
