"""
Detect when two uploads are the same recording of the same event.

A duplicate is one event posted twice, not one artist playing similar sets at
two festivals - that is a different relation and is deliberately not modelled.
See docs/DOMAIN.md.

Public: DuplicateConfig, is_probable_duplicate, find_candidates,
        find_pairs_within, is_official, event_tokens, jaccard, series_number,
        normalize_artists

Pure. Imports nothing from src/ except core.models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterable, Sequence

from .models import DuplicateMatch, TrackRef

# Words that describe the upload rather than the event, so they must not make
# two unrelated titles look alike.
_STOP = frozenset({
    "live", "set", "dj", "full", "mix", "b2b", "official", "video",
    "at", "the", "a", "an", "in", "of", "and", "with", "ft", "feat",
    "recording", "audio", "hd", "4k", "presents", "by", "from", "partial",
})

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SERIES = re.compile(
    r"\b(?:vol\.?|volume|pt\.?|part|ep\.?|episode|#)\s*(\d+)", re.IGNORECASE
)

# The same recording reposted differs by seconds of trimming, never minutes.
MIN_DURATION_RATIO = 0.90
# Below this much title overlap, two sets are different events.
MIN_TOKEN_OVERLAP = 0.5


@dataclass(frozen=True)
class DuplicateConfig:
    """Built by data/; never read from disk here."""

    location_qualifiers: frozenset = frozenset()  # lowercased: miami, vegas, camp
    repost_accounts: frozenset = frozenset()      # lowercased
    official_accounts: frozenset = frozenset()    # lowercased; marked by the user


def normalize_artists(value: str) -> frozenset:
    """{lowercased artist name} - two tracks must share this exactly."""
    return frozenset(s.strip().lower() for s in value.split(",") if s.strip())


def event_tokens(title: str, artist: str) -> frozenset:
    """
    Words identifying the EVENT: venue, city, year, festival. The artist name
    is removed first, since every copy carries it and it would inflate overlap.
    """
    text = title.lower()
    for name in artist.lower().split(","):
        name = name.strip()
        if name:
            text = text.replace(name, " ")
    return frozenset(
        t for t in re.findall(r"\w+", text) if t not in _STOP and len(t) > 1
    )


def jaccard(a: frozenset, b: frozenset) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def series_number(title: str) -> str | None:
    """The Vol/Part/Episode number in a title, or None."""
    match = _SERIES.search(title)
    return match.group(1) if match else None


def _numbers(title: str) -> frozenset:
    """Standalone numbers - "b2b" is not a 2."""
    return frozenset(re.findall(r"\b\d+\b", title))


def is_probable_duplicate(
    a: TrackRef, b: TrackRef, qualifiers: frozenset = frozenset()
) -> bool:
    """
    True if these are the same recording. Every guard below is a reason to say
    no; the default is that two same-artist sets are DIFFERENT events.
    """
    a_tokens = event_tokens(a.title, a.artist)
    b_tokens = event_tokens(b.title, b.artist)
    if len(a_tokens) < 2 or len(b_tokens) < 2:
        return False
    if jaccard(a_tokens, b_tokens) < MIN_TOKEN_OVERLAP:
        return False

    # Same artist and venue but a different year is a different event.
    a_years = frozenset(_YEAR.findall(a.title))
    b_years = frozenset(_YEAR.findall(b.title))
    if a_years and b_years and a_years != b_years:
        return False

    # Vol. 3 is not Vol. 4.
    a_series, b_series = series_number(a.title), series_number(b.title)
    if a_series is not None and b_series is not None and a_series != b_series:
        return False

    # Both name a stage or city, but a DIFFERENT one: "CAMP EDC" vs "EDC".
    # Only one side naming a qualifier is an under-specified title, not a
    # conflict, so it does not veto.
    a_quals = a_tokens & qualifiers
    b_quals = b_tokens & qualifiers
    if a_quals and b_quals and a_quals != b_quals:
        return False

    # Length is only evidence when the titles leave room for doubt. A festival
    # livestream is ripped by several accounts, partial or trimmed, so copies
    # of one set differ by minutes - but when the event words AND every number
    # agree, the titles say the same thing. Numbers count because event tokens
    # drop them: "Gutter Music 7 (Part 1)" is not "Gutter Music 3 (Part 1)".
    if (a.duration_ms > 0 and b.duration_ms > 0
            and (a_tokens != b_tokens or _numbers(a.title) != _numbers(b.title))):
        shorter, longer = sorted((a.duration_ms, b.duration_ms))
        if shorter / longer < MIN_DURATION_RATIO:
            return False

    return True


def is_official(track: TrackRef, repost_accounts: frozenset,
                official_accounts: frozenset = frozenset()) -> bool:
    """
    True if the upload came from the performer's own account.

    An account the user marked official counts whatever its name - a duo's
    shared account rarely contains either member's name. Otherwise the
    uploader must be one of the performers, and never a known reposter.
    """
    uploader = track.uploader.lower()
    if uploader in official_accounts:
        return True
    if uploader in repost_accounts:
        return False
    return any(
        name in uploader or uploader in name
        for name in (a.strip().lower() for a in track.artist.split(","))
        if name
    )


def find_candidates(
    new: TrackRef, existing: Iterable[TrackRef], cfg: DuplicateConfig
) -> list[DuplicateMatch]:
    """
    Every already-known track that looks like the same recording as `new`.

    Tracks already flagged as duplicates are skipped - they have been resolved
    once and must not be re-raised against their own replacement.
    """
    new_artists = normalize_artists(new.artist)
    if not new_artists:
        return []

    matches = []
    for other in existing:
        if other.is_duplicate or other.track_id == new.track_id:
            continue
        if normalize_artists(other.artist) != new_artists:
            continue
        if not is_probable_duplicate(new, other, cfg.location_qualifiers):
            continue
        matches.append(
            DuplicateMatch(
                new=new,
                existing=other,
                new_is_official=is_official(
                    new, cfg.repost_accounts, cfg.official_accounts),
                existing_is_official=is_official(
                    other, cfg.repost_accounts, cfg.official_accounts),
            )
        )
    return matches


def find_pairs_within(
    tracks: Sequence[TrackRef], cfg: DuplicateConfig
) -> list[DuplicateMatch]:
    """
    Duplicates INSIDE one batch of incoming tracks.

    The old code only ever compared an incoming track against the stored
    database, so three copies of one set arriving in the same playlist were all
    added - "Subtronics - EDC Las Vegas 2026", "Subtronics - Live @ EDC Las
    Vegas 2026" and "SUBTRONICS - Live @ EDC Las Vegas 2026" share identical
    event tokens and all three are in the database. Run this as well as
    find_candidates before writing anything.
    """
    matches = []
    for a, b in combinations(tracks, 2):
        artists = normalize_artists(a.artist)
        if not artists or artists != normalize_artists(b.artist):
            continue
        if not is_probable_duplicate(a, b, cfg.location_qualifiers):
            continue
        matches.append(
            DuplicateMatch(
                new=b,
                existing=a,
                new_is_official=is_official(
                    b, cfg.repost_accounts, cfg.official_accounts),
                existing_is_official=is_official(
                    a, cfg.repost_accounts, cfg.official_accounts),
            )
        )
    return matches
