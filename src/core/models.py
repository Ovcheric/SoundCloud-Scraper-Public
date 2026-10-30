"""
Plain data shapes shared across core/.

These are deliberately minimal: just enough for the pure rules to work on, with
no database, API or UI concepts attached. data/ and api/ build them; core/
reads them and never mutates them.

Public: TrackRef, DuplicateMatch

Pure. Imports nothing from src/.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrackRef:
    """
    A track as the pure rules see it.

    `artist` is the resolved performer - what the track_artists view returns,
    so a manual correction is already applied. `uploader` is the raw SoundCloud
    account, kept because official-vs-repost decides which copy of a duplicate
    survives.
    """

    track_id: str
    title: str
    artist: str = ""          # resolved performer(s), comma-separated
    uploader: str = ""        # the account that posted it
    duration_ms: int = 0
    duplicate_of: str = ""    # empty when this is not a duplicate

    @property
    def is_duplicate(self) -> bool:
        return bool(self.duplicate_of)


@dataclass(frozen=True)
class DuplicateMatch:
    """
    One track judged to be the same recording as another.

    `official` flags say whether each side came from the performer's own
    account, which is the first tiebreaker when deciding which copy to keep.
    """

    new: TrackRef
    existing: TrackRef
    new_is_official: bool
    existing_is_official: bool

    def preferred(self) -> TrackRef:
        """The copy worth keeping: the official upload, else the existing one."""
        if self.new_is_official and not self.existing_is_official:
            return self.new
        return self.existing
