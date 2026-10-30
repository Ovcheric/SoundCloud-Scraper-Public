"""
The Add pipeline: a playlist URL in, new sets in Discovery out.

Runs without asking anything. The old version threw modal dialogs mid-flow -
one per repost account it could not parse, one per duplicate pair - which is
the actual reason this app became tiring to use. Every decision here is made
automatically and reported afterwards, so the user reviews rather than answers.
The review can then be corrected (services/review.py) and refresh() works the
plan out again from the playlist already fetched, with no network.

Public: AddPlan, AddResult, RawRow, Skipped, Unsure, Triage, MIN_DURATION_MS,
        triage, plan_add, refresh, apply_add, ADD, LIBRARY, DUPLICATE, SHORT,
        DISMISSED, CERTAIN, LIKELY, UNSURE, GUESS, CONFIDENCE_ORDER

Imports core, data and api. Never imports ui/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from api.client import NotFound
from core import artists, duplicates
from core.models import TrackRef
from data import dismissals, settings, tracks as track_store

# Re-exported for ui/, which may not import core/ directly. The Add screen
# colours each row by these, so they have to reach it through this layer.
from core.artists import (  # noqa: F401
    CERTAIN, CONFIDENCE_ORDER, GUESS, LIKELY, UNSURE)
# Triage moved to its own module; these names stay importable from here.
from .triage import Skipped, Triage, Unsure, triage  # noqa: F401

# A set is 20 minutes or more. Shorter uploads are songs.
MIN_DURATION_MS = 20 * 60 * 1000

# What became of one track in the playlist as uploaded.
ADD, LIBRARY, DUPLICATE, SHORT, DISMISSED = (
    "add", "library", "duplicate", "short", "dismissed")


@dataclass
class RawRow:
    """One playlist track exactly as uploaded, and what became of it."""

    track_id: str
    title: str
    uploader: str
    duration_ms: int
    status: str                 # ADD, LIBRARY, DUPLICATE, SHORT or DISMISSED
    note: str = ""              # why, in words
    reposter: bool = False      # uploader is a registered repost account
    official: bool = False      # uploader is the performer - the star


@dataclass
class AddPlan:
    playlist_title: str = ""
    playlist_id: str = ""
    source_url: str = ""
    total_in_playlist: int = 0
    too_short: int = 0
    already_known: int = 0
    dismissed_before: int = 0                       # turned down in Find
    to_add: list = field(default_factory=list)      # dicts ready for insert_many
    skipped: list = field(default_factory=list)     # Skipped
    replaced_existing: list = field(default_factory=list)  # (old_id, new_id)
    needs_review: list = field(default_factory=list)       # Unsure
    corrections: dict = field(default_factory=dict)        # track_id -> typed artist
    confidence: dict = field(default_factory=dict)         # track_id -> CERTAIN..GUESS
    raw: list = field(default_factory=list)         # the playlist as fetched
    same_recording: list = field(default_factory=list)     # frozensets the user grouped
    held_back: set = field(default_factory=set)     # left out of this add only
    rows: list = field(default_factory=list)        # RawRow per raw track
    official: set = field(default_factory=set)      # to_add ids from the performer

    @property
    def add_count(self) -> int:
        return len(self.to_add)


@dataclass
class AddResult:
    added_to_library: int = 0
    added_to_playlist: int = 0
    marked_duplicate: int = 0
    corrected: int = 0


def plan_add(source_url: str, conn, api) -> AddPlan:
    """
    Work out what a playlist would contribute. Reads only - nothing is written
    and nothing is sent to SoundCloud beyond fetching the playlist.
    """
    resource = api.resolve(source_url)
    if resource.get("kind") != "playlist":
        kind = resource.get("kind", "unknown")
        raise ValueError(f"That URL is a {kind}, not a playlist.")

    plan = AddPlan(
        playlist_title=resource.get("title", "Unknown"),
        playlist_id=str(resource["id"]),
        source_url=source_url,
        raw=api.playlist_tracks(resource["id"]),
    )
    return refresh(plan, conn)


def refresh(plan: AddPlan, conn) -> AddPlan:
    """
    The plan worked out again from the playlist it already holds.

    Called after every correction - an account marked, sets grouped, one
    skipped - so the review is always the current answer. No network; the
    corrections typed and the groups made carry over.
    """
    new = AddPlan(playlist_title=plan.playlist_title, playlist_id=plan.playlist_id,
                  source_url=plan.source_url, raw=plan.raw,
                  same_recording=list(plan.same_recording),
                  held_back=set(plan.held_back),
                  corrections=dict(plan.corrections))
    raw = new.raw
    new.total_in_playlist = len(raw)
    long_enough = [t for t in raw if int(t.get("duration") or 0) >= MIN_DURATION_MS]
    new.too_short = len(raw) - len(long_enough)
    known = track_store.known_ids(conn)
    unseen = [t for t in long_enough if str(t["id"]) not in known]
    new.already_known = len(long_enough) - len(unseen)
    # A set turned down in Find stays turned down, wherever it turns up.
    dismissed = dismissals.dismissed_ids(conn)
    fresh = [t for t in unseen if str(t["id"]) not in dismissed]
    new.dismissed_before = len(unseen) - len(fresh)
    fresh = [t for t in fresh if str(t["id"]) not in new.held_back]

    found = triage(fresh, conn, source_url=new.source_url,
                   batch_label="duplicate in this playlist",
                   same_recording=new.same_recording,
                   corrections=new.corrections) if fresh else Triage()
    new.to_add = found.to_add
    new.skipped = found.skipped
    new.needs_review = found.needs_review
    new.replaced_existing = found.replaced
    new.confidence = found.confidence
    new.rows = _raw_rows(new, conn, known, dismissed)
    new.official = {r.track_id for r in new.rows if r.official and r.status == ADD}
    return new


def _fate(raw: dict, known: set, dismissed: set, plan: AddPlan,
          titles: dict) -> tuple:
    """(status, note) for one raw track - the left pane's whole story."""
    track_id = str(raw["id"])
    if int(raw.get("duration") or 0) < MIN_DURATION_MS:
        return SHORT, "a song - under 20 minutes"
    if track_id in known:
        return LIBRARY, "already in your library"
    if track_id in dismissed:
        return DISMISSED, "turned down before"
    if track_id in plan.held_back:
        return DISMISSED, "left out of this add"
    skipped = next((s for s in plan.skipped if s.track_id == track_id), None)
    if skipped:
        kept = titles.get(skipped.duplicate_of) or "another copy"
        return DUPLICATE, (f"same set as “{kept}” - {skipped.because}, "
                           f"kept the {skipped.reason}")
    if any(new == track_id for _old, new in plan.replaced_existing):
        return ADD, "replaces a reposted copy in your library"
    return ADD, ""


def _raw_rows(plan: AddPlan, conn, known: set, dismissed: set) -> list:
    """Every playlist track in upload order, with its fate and its flags."""
    resolution = settings.load_resolution_config(conn)
    dup = settings.load_duplicate_config(conn)
    titles = {str(t["id"]): t.get("title", "") for t in plan.raw}
    titles.update(track_store.titles(conn, [s.duplicate_of for s in plan.skipped]))
    rows = []
    for raw in plan.raw:
        uploader = raw.get("user", {}).get("username", "")
        title = raw.get("title", "")
        ref = TrackRef(str(raw["id"]), title,
                       artists.resolve(uploader, title, resolution), uploader,
                       int(raw.get("duration") or 0))
        status, note = _fate(raw, known, dismissed, plan, titles)
        rows.append(RawRow(
            ref.track_id, title, uploader, ref.duration_ms, status, note,
            reposter=uploader.lower() in dup.repost_accounts,
            official=duplicates.is_official(
                ref, dup.repost_accounts, dup.official_accounts)))
    return rows


def _by_title(track_ids: list, conn, api) -> list:
    """
    The Discovery playlist in title order, A to Z, ignoring case.

    A reorder, never a filter: the full list is PUT, so every id that went in
    must come out. A track the library has never seen (added by hand in
    SoundCloud) has its title fetched; one SoundCloud no longer has sorts last
    rather than being dropped.
    """
    titles = track_store.titles(conn, track_ids)
    for track_id in track_ids:
        if track_id not in titles:
            try:
                titles[track_id] = api.track(track_id).get("title", "")
            except NotFound:
                titles[track_id] = ""
    return sorted(track_ids, key=lambda tid: (
        not titles[tid].strip(), titles[tid].strip().casefold()))


def _record_decisions(plan: AddPlan, conn, stamp: str) -> AddResult:
    """Corrections, duplicate links, and copies the user grouped away."""
    result = AddResult()
    adding = {t["track_id"] for t in plan.to_add}
    # Typed in the preview before adding. Written as a manual correction, not
    # as real_artists, so re-resolving can never overwrite it.
    for track_id, artist in plan.corrections.items():
        if track_id in adding:
            track_store.set_manual_artist(conn, track_id, artist)
            result.corrected += 1

    for skipped in plan.skipped:
        if skipped.because == "already in library":
            track_store.set_duplicate_of(conn, skipped.track_id, skipped.duplicate_of)
            result.marked_duplicate += 1
    for old_id, new_id in plan.replaced_existing:
        track_store.set_duplicate_of(conn, old_id, new_id)
        result.marked_duplicate += 1

    # A copy the user grouped away was never stored, so nothing else would
    # stop the next playlist offering it again. The detector could not see it
    # the first time, and would not the second.
    dismissals.record(conn, [
        {"track_id": s.track_id, "title": s.title, "artist": ""}
        for s in plan.skipped if s.marked],
        reason="duplicate", note="you marked it the same set", at=stamp)
    return result


def apply_add(plan: AddPlan, conn, api, *, now: str | None = None) -> AddResult:
    """
    Commit a plan: write the tracks, then put them in the Discovery playlist,
    which is left sorted by title.

    The library is written first. If the playlist update then fails, the tracks
    are recorded and the next run repairs the playlist - the reverse would lose
    the record of what was queued.
    """
    stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = track_store.insert_many(conn, plan.to_add, seen_at=stamp)
    result = _record_decisions(plan, conn, stamp)
    result.added_to_library = added
    conn.commit()

    if plan.to_add:
        playlist_id, existing = api.discovery_playlist()
        new_ids = [t["track_id"] for t in plan.to_add]
        api.set_playlist_tracks(
            playlist_id, _by_title(existing + new_ids, conn, api))
        result.added_to_playlist = len(new_ids)

        # Baseline snapshot, taken while these tracks are definitely still in
        # the playlist. Without one, removing a track later cannot be detected
        # at all - "gone from Discovery" only means anything relative to an
        # earlier observation. Leaving this to the user to remember is how 783
        # verdicts were nearly lost the first time.
        track_store.record_discovery_snapshot(conn, existing + new_ids, at=stamp)
        conn.commit()

    return result
