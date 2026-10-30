"""
Corrections made while reviewing a playlist on the Add screen.

Each one changes what discovery.refresh() works out next. Marking an account
is a fact about that account, so it is written at once and holds for every
later playlist. Grouping sets as one recording, or splitting a pair the
detector joined, belongs to this plan and is kept in it until it is added.
Nothing here asks a question; the screen asks, then calls these.

Public: uploaders, mark_reposter, mark_official, mark_same, mark_different,
        skip, always_read_as

Imports data and services.discovery. Never imports ui/.
"""

from __future__ import annotations

from datetime import datetime

from data import dismissals, settings

from .discovery import AddPlan


def uploaders(plan: AddPlan, track_ids) -> list:
    """The distinct accounts that posted these playlist tracks, in order."""
    ids = {str(t) for t in track_ids}
    return list(dict.fromkeys(r.uploader for r in plan.rows if r.track_id in ids))


def mark_reposter(conn, names, reposter: bool) -> None:
    """
    These accounts post other people's sets - or, with False, they do not.
    Every set from them is then read from its title instead of credited to
    the account, and loses its star.
    """
    for name in names:
        if reposter:
            settings.add_repost_account(conn, name)
        else:
            settings.remove_repost_account(conn, name)


def mark_official(conn, names, official: bool) -> None:
    """These accounts are the performers' own: their uploads get the star."""
    for name in names:
        settings.set_official_account(conn, name, official)


def always_read_as(conn, name: str, artists: str) -> None:
    """
    From now on, read `name` as `artists` wherever it is parsed - a duo's
    shared name as both members ("LEVELTRONICS" -> "Subtronics, Level Up"),
    so their sets are credited to each and match each other's uploads.
    """
    settings.add_alias(conn, name, artists)


def mark_same(plan: AddPlan, track_ids) -> None:
    """
    These playlist tracks are one recording. Any group they already share
    is merged in, so marking A+B then B+C leaves one group of three.
    """
    group = frozenset(str(t) for t in track_ids)
    if len(group) < 2:
        return
    overlapping = [g for g in plan.same_recording if g & group]
    for g in overlapping:
        plan.same_recording.remove(g)
        group |= g
    plan.same_recording.append(group)


def mark_different(plan: AddPlan, conn, track_ids) -> int:
    """
    Undo a duplicate decision for these tracks. Returns how many pairs split.

    A group the user made is simply taken apart. A pair the detector found is
    recorded as keep-both - the same table `manage.py` writes - so it is never
    raised again for these two tracks.
    """
    ids = {str(t) for t in track_ids}
    split = 0
    for skipped in plan.skipped:
        if not {skipped.track_id, skipped.duplicate_of} & ids:
            continue
        if skipped.marked:
            plan.same_recording[:] = [
                g - {skipped.track_id} for g in plan.same_recording]
            plan.same_recording[:] = [g for g in plan.same_recording if len(g) > 1]
        else:
            settings.add_keep_both(conn, skipped.track_id, skipped.duplicate_of)
        split += 1
    return split


def skip(plan: AddPlan, conn, track_ids, reason: str, note: str = "",
         *, now: str | None = None) -> int:
    """
    Turn these sets down, remembered with a reason, exactly as Find does -
    no playlist or search offers them again. Returns how many.
    """
    ids = {str(t) for t in track_ids}
    rows = [{"track_id": r["track_id"], "title": r["title"],
             "artist": plan.corrections.get(r["track_id"], r["real_artists"])}
            for r in plan.to_add if r["track_id"] in ids]
    stamp = now or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return dismissals.record(conn, rows, reason=reason, note=note, at=stamp)
