"""
Turn a batch of raw SoundCloud tracks into rows ready to store.

Resolve each artist, then drop duplicates three ways: against the library,
within the batch, and within any groups the user marked as one recording.
Shared by Add Sets, Find and the likes import, so all three get the same
artist rules and the same keep-or-drop tiebreak.

Public: Skipped, Unsure, Triage, triage, MARKED_SAME

Imports core and data. Never imports api/ or ui/.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import artists, duplicates
from core.models import TrackRef
from data import settings, tracks as track_store

# Skipped.because for a copy dropped from a group the user made.
MARKED_SAME = "you marked these as one set"


@dataclass
class Skipped:
    """One incoming track dropped as a duplicate, with the reason."""

    track_id: str
    title: str
    because: str          # "already in library" | "duplicate in this ..." | MARKED_SAME
    duplicate_of: str
    reason: str           # which rule decided it
    marked: bool = False  # the user grouped these, not the detector


@dataclass
class Unsure:
    """One track whose artist the parser could not confidently work out."""

    track_id: str
    title: str
    guessed: str
    reason: str


@dataclass
class Triage:
    """What a batch of fresh tracks resolves to, once deduped."""

    to_add: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    needs_review: list = field(default_factory=list)
    replaced: list = field(default_factory=list)
    confidence: dict = field(default_factory=dict)  # track_id -> CERTAIN..GUESS


@dataclass
class _Known:
    """What triage reads from the database, read once per batch."""

    resolution: artists.ResolutionConfig
    dup: duplicates.DuplicateConfig
    keep_both: set
    liked_ids: set
    library: list


def _load(conn) -> _Known:
    return _Known(
        resolution=settings.load_resolution_config(conn),
        dup=settings.load_duplicate_config(conn),
        keep_both=settings.keep_both_pairs(conn),
        liked_ids={row[0] for row in conn.execute(
            "SELECT track_id FROM track_verdicts WHERE verdict = 'liked'")},
        library=track_store.all_refs(conn),
    )


def _to_ref(raw: dict, artist: str) -> TrackRef:
    return TrackRef(
        track_id=str(raw["id"]),
        title=raw.get("title", ""),
        artist=artist,
        uploader=raw.get("user", {}).get("username", ""),
        duration_ms=int(raw.get("duration") or 0),
    )


def _better_of(a: TrackRef, b: TrackRef, liked_ids: set,
               cfg: duplicates.DuplicateConfig) -> tuple:
    """
    Which copy of one recording to keep, and why. Never returns "ask the user".

    Official upload beats a repost; failing that a liked copy beats an unliked
    one; failing that the longer file wins, because reposts are trimmed. The
    last rule is what the old code lacked - with no tiebreaker it asked, and
    when nobody answered, all three copies of the EDC set were added.
    """
    a_official = duplicates.is_official(a, cfg.repost_accounts, cfg.official_accounts)
    b_official = duplicates.is_official(b, cfg.repost_accounts, cfg.official_accounts)
    if a_official != b_official:
        return (a, b, "official upload") if a_official else (b, a, "official upload")

    a_liked, b_liked = a.track_id in liked_ids, b.track_id in liked_ids
    if a_liked != b_liked:
        return (a, b, "already liked") if a_liked else (b, a, "already liked")

    if a.duration_ms != b.duration_ms:
        return ((a, b, "longer copy") if a.duration_ms > b.duration_ms
                else (b, a, "longer copy"))
    return a, b, "first seen"


def _resolve(fresh: list, known: _Known, out: Triage, typed: dict) -> dict:
    """
    {track_id: (TrackRef, raw, resolved name)} with every artist worked out.

    The TrackRef - what duplicates are judged on - carries the artist the user
    typed, when there is one: a typed correction always wins. The resolved
    name rides alongside, because it alone may be stored as real_artists.
    """
    incoming = {}
    for raw in fresh:
        track_id = str(raw["id"])
        uploader = raw.get("user", {}).get("username", "")
        title = raw.get("title", "")
        resolved = artists.resolve_detailed(uploader, title, known.resolution)
        out.confidence[track_id] = resolved.confidence
        if not resolved.confident and track_id not in typed:
            # Stored anyway - a flagged guess beats dropping the set - but
            # surfaced so it can be corrected once instead of quietly becoming
            # a one-off artist nobody notices.
            out.needs_review.append(
                Unsure(track_id, title, resolved.name, resolved.reason))
        incoming[track_id] = (
            _to_ref(raw, typed.get(track_id) or resolved.name), raw, resolved.name)
    return incoming


def _against_library(incoming: dict, known: _Known, out: Triage,
                     dropped: dict) -> None:
    """
    The stored copy normally wins: it may already have been listened to, and
    re-queueing it wastes the user's evening.
    """
    for track_id, (ref, _, _) in incoming.items():
        for match in duplicates.find_candidates(ref, known.library, known.dup):
            if frozenset((track_id, match.existing.track_id)) in known.keep_both:
                continue
            keep, drop, reason = _better_of(
                ref, match.existing, known.liked_ids, known.dup)
            if drop.track_id == track_id:
                dropped[track_id] = Skipped(
                    track_id, ref.title, "already in library",
                    match.existing.track_id, reason)
            else:
                out.replaced.append((match.existing.track_id, track_id))
            break


def _within_batch(incoming: dict, known: _Known, label: str,
                  dropped: dict) -> None:
    """The old code never did this, so three copies arriving together all got in."""
    batch = [ref for tid, (ref, _, _) in incoming.items() if tid not in dropped]
    for match in duplicates.find_pairs_within(batch, known.dup):
        pair = frozenset((match.new.track_id, match.existing.track_id))
        if pair & set(dropped) or pair in known.keep_both:
            continue
        keep, drop, reason = _better_of(
            match.new, match.existing, known.liked_ids, known.dup)
        dropped[drop.track_id] = Skipped(
            drop.track_id, drop.title, label, keep.track_id, reason)


def _marked_same(incoming: dict, groups, known: _Known, dropped: dict) -> None:
    """
    Groups the user said are one recording - what the detector cannot see,
    such as a duo posting one set from both accounts. One copy survives each:
    a stored copy if the group has one, otherwise the best by the tiebreak.
    """
    stored = {ref.track_id: ref for ref in known.library}
    for group in groups:
        live = [incoming[t][0] for t in sorted(group)
                if t in incoming and t not in dropped]
        in_library = [stored[t] for t in sorted(group) if t in stored]
        if in_library:
            for ref in live:
                dropped[ref.track_id] = Skipped(
                    ref.track_id, ref.title, "already in library",
                    in_library[0].track_id, "you marked it the same set", True)
            continue
        if len(live) < 2:
            continue
        best = live[0]
        for ref in live[1:]:
            best = _better_of(best, ref, known.liked_ids, known.dup)[0]
        for ref in live:
            if ref is not best:
                reason = _better_of(best, ref, known.liked_ids, known.dup)[2]
                dropped[ref.track_id] = Skipped(
                    ref.track_id, ref.title, MARKED_SAME, best.track_id, reason, True)


def _rows(incoming: dict, dropped: dict, source_url: str | None) -> list:
    """What survived, as dicts ready for insert_many, in title order."""
    return [
        {
            "track_id": track_id,
            "title": raw.get("title", ""),
            "uploader": raw.get("user", {}).get("username", ""),
            "real_artists": resolved,
            "duration_ms": ref.duration_ms,
            "uploaded_at": raw.get("created_at"),
            "source_playlist": source_url,
        }
        for track_id, (ref, raw, resolved) in sorted(
            incoming.items(), key=lambda kv: kv[1][0].title.lower())
        if track_id not in dropped
    ]


def triage(fresh: list, conn, *, source_url: str | None = None,
           batch_label: str = "duplicate in this batch",
           same_recording=(), corrections=None) -> Triage:
    """
    Resolve artists and remove duplicates from a batch of raw API tracks.

    Compares against BOTH the library and the rest of the batch - see the
    data invariants in AGENTS.md - and then applies `same_recording`, the
    groups of track ids the user marked as one set. `corrections` is
    {track_id: artist typed}, used for judging duplicates only.
    """
    out = Triage()
    known = _load(conn)
    incoming = _resolve(fresh, known, out, corrections or {})
    dropped: dict = {}
    _against_library(incoming, known, out, dropped)
    _within_batch(incoming, known, batch_label, dropped)
    _marked_same(incoming, same_recording, known, dropped)
    # A copy the user grouped away cannot also replace a stored one.
    out.replaced = [p for p in out.replaced if p[1] not in dropped]
    out.skipped = sorted(dropped.values(), key=lambda s: s.title.lower())
    out.to_add = _rows(incoming, dropped, source_url)
    return out
