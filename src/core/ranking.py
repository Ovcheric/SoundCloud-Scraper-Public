"""
Which found sets are worth queueing, and in what order.

Pure: raw SoundCloud track dicts and numbers in, decisions out. The Find
search in services/ gathers candidates from several sources; this module
filters them by the user's settings, scores them by how they were found, and
picks the best few without letting one artist take over the queue.

Public: Filters, Hit, Candidate, rejection, uploaded_at, score, pick,
        performers

Imports nothing from src/.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

MINUTE_MS = 60_000
FRESH_DAYS = 180        # a set this old gets no freshness bonus at all


@dataclass(frozen=True)
class Filters:
    """What the user asked for. Zero means "no limit" for every bound."""

    min_minutes: int = 20
    max_minutes: int = 0
    within_days: int = 0
    min_likes: int = 0
    include_words: tuple = ()     # a result must mention at least one
    exclude_words: tuple = ()     # a result may mention none
    playable_only: bool = True    # drop Go+ previews and blocked tracks
    max_results: int = 30
    per_artist: int = 2
    # Only artists with at most this many sets in the library - 0 is "never
    # heard". None is no limit; unlike the bounds above, 0 here means 0.
    max_known_sets: int | None = None


@dataclass(frozen=True)
class Hit:
    """One reason a set was found. A set found three ways carries three."""

    source: str        # which search produced it - see services/search_sources
    weight: float
    detail: str        # in words, for the queue: "liked by Wooli"


@dataclass
class Candidate:
    raw: dict                                   # the API track, untouched
    hits: list = field(default_factory=list)    # Hit

    @property
    def track_id(self) -> str:
        return str(self.raw["id"])


def uploaded_at(raw: dict) -> datetime | None:
    """When it was posted. SoundCloud writes '2026/08/12 18:55:13 +0000'."""
    text = raw.get("created_at") or ""
    for fmt in ("%Y/%m/%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text.replace("Z", "+0000"), fmt)
        except ValueError:
            continue
    return None


def _mentions(text: str, words: tuple) -> bool:
    return any(re.search(rf"\b{re.escape(w.casefold())}\b", text) for w in words)


def rejection(raw: dict, filters: Filters, now: datetime) -> str | None:
    """
    Why `raw` fails the user's filters, in a few words - or None if it passes.

    The words are counted and shown after a search, so a filter that is
    quietly discarding everything is visible rather than looking like "found
    nothing".
    """
    duration = int(raw.get("duration") or 0)
    if filters.playable_only and raw.get("access", "playable") != "playable":
        return "preview only"
    if duration < filters.min_minutes * MINUTE_MS:
        return "too short"
    if filters.max_minutes and duration > filters.max_minutes * MINUTE_MS:
        return "too long"
    posted = uploaded_at(raw)
    if filters.within_days and posted and (now - posted).days > filters.within_days:
        return "too old"
    if int(raw.get("favoritings_count") or 0) < filters.min_likes:
        return "too few likes"
    text = " ".join((raw.get("title") or "", raw.get("genre") or "",
                     raw.get("tag_list") or "")).casefold()
    if filters.exclude_words and _mentions(text, filters.exclude_words):
        return "excluded word"
    if filters.include_words and not _mentions(text, filters.include_words):
        return "missing keyword"
    return None


def score(candidate: Candidate, now: datetime) -> float:
    """
    Sum of the ways it was found, plus up to one point for being recent.

    Additive on purpose: a set your artists liked AND that is related to one
    you kept is a stronger pick than either alone, and the queue shows each
    reason so the order can be second-guessed.
    """
    total = sum(hit.weight for hit in candidate.hits)
    posted = uploaded_at(candidate.raw)
    if posted:
        age = max((now - posted).days, 0)
        total += max(0.0, 1 - age / FRESH_DAYS)
    return total


def performers(artist: str) -> list:
    """'Wooli, Crankdat' -> ['Wooli', 'Crankdat'], as track_artist_names splits."""
    return [name.strip() for name in artist.split(",") if name.strip()]


def _too_familiar(names: list, filters: Filters, known: dict) -> bool:
    """True unless at least one performer is as new to you as asked."""
    if filters.max_known_sets is None or not names:
        return False
    return all(known.get(n, 0) > filters.max_known_sets for n in names)


def pick(ranked: list, filters: Filters, blocked: set,
         known: dict | None = None) -> tuple:
    """
    Choose what goes in the queue. Returns (track_ids, Counter of why not).

    `ranked` is [(track_id, artist, score)], `blocked` casefolded performer
    names, `known` {casefolded performer: sets in the library}. A b2b is
    judged by its best member: it is blocked only when EVERY performer is,
    and it counts as new when ANY performer is - a b2b is how a new name
    usually arrives, next to one you already know.
    """
    known = known or {}
    left_out: Counter = Counter()
    chosen, per_artist = [], Counter()
    for track_id, artist, _ in sorted(ranked, key=lambda r: -r[2]):
        names = [n.casefold() for n in performers(artist)]
        if names and all(n in blocked for n in names):
            left_out["artist left out"] += 1
            continue
        if _too_familiar(names, filters, known):
            left_out["artist already familiar"] += 1
            continue
        key = artist.casefold()
        if filters.per_artist and per_artist[key] >= filters.per_artist:
            left_out["per-artist cap"] += 1
            continue
        if len(chosen) >= filters.max_results:
            left_out["over the limit"] += 1
            continue
        chosen.append(track_id)
        per_artist[key] += 1
    return chosen, left_out
