"""
Maintenance for the things the app has no screen for.

Most of the time you never need this. It exists for the situations that
recur: a new festival or repost channel starts posting other people's sets, an
artist turns up under a second spelling, or a festival name sticks to one.

    python scripts/manage.py stats
    python scripts/manage.py flagged
    python scripts/manage.py repost "REBiRTH Festival"
    python scripts/manage.py alias TapeB "Tape B"
    python scripts/manage.py event "Lost Lands"
    python scripts/manage.py place miami
    python scripts/manage.py fix 1234567890 "GRiZ"
    python scripts/manage.py reposts

Nothing here is destructive. Registering a repost account changes how FUTURE
tracks resolve; it does not rewrite what is already stored, because those may
carry corrections of their own.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Track titles carry emoji and non-Latin characters, and the Windows console
# defaults to cp1252 - printing one is otherwise a UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import artists                      # noqa: E402
from data import db, settings, tracks as track_store  # noqa: E402
from api import auth, client                        # noqa: E402
from services import library                        # noqa: E402


def cmd_stats(conn, _args) -> None:
    counts = track_store.verdict_counts(conn)
    decided = counts.get("liked", 0) + counts.get("skipped", 0)
    rate = f"{100 * counts.get('liked', 0) / decided:.0f}%" if decided else "n/a"
    print(f"tracks        : {sum(counts.values())}")
    for verdict in ("liked", "skipped", "pending"):
        print(f"  {verdict:<11} {counts.get(verdict, 0)}")
    print(f"keep rate     : {rate}")
    print(f"artists kept  : {len(track_store.artist_taste(conn))}")
    skip = track_store.artists_to_skip(conn)
    print(f"stop queueing : {len(skip)} artists, {sum(r['heard'] for r in skip)} sets")
    runs = conn.execute("SELECT COUNT(*), MAX(snapshot_at) FROM discovery_runs").fetchone()
    print(f"discovery runs: {runs[0]}  (last {runs[1] or 'never'})")


def cmd_flagged(conn, _args) -> None:
    """Tracks the resolver is unsure about and nobody has corrected."""
    cfg = settings.load_resolution_config(conn)
    rows = conn.execute("""
        SELECT t.track_id, t.uploader, t.title, a.artist, a.artist_source
        FROM tracks t JOIN track_artists a USING (track_id)""")
    found = 0
    for row in rows:
        if row["artist_source"] == "manual":
            continue                       # already decided by a human
        result = artists.resolve_detailed(row["uploader"], row["title"], cfg)
        if result.confident:
            continue
        found += 1
        print(f"[{row['track_id']}]  {result.reason}")
        print(f"    title : {row['title'][:70]}")
        print(f"    artist: {row['artist'][:60]!r}")
        print(f"    fix   : python scripts/manage.py fix {row['track_id']} \"NAME\"")
    print(f"\n{found} uncorrected track(s) the resolver is unsure about.")


def cmd_import_likes(conn, args) -> None:
    """Adopt liked sets that never came through Discovery."""
    api = client.SoundCloudClient(auth.load_fresh())
    plan = library.plan_import_likes(conn, api)
    print(f"liked tracks      : {plan.total_likes}")
    print(f"  sets (20min+)   : {plan.sets}")
    print(f"  songs (shorter) : {plan.songs}")
    print(f"  already known   : {plan.already_known}")
    print(f"  duplicates      : {len(plan.skipped)}")
    print(f"  TO IMPORT       : {plan.add_count}")
    if plan.needs_review:
        print(f"  artist unsure   : {len(plan.needs_review)}")
    print()
    for row in plan.to_add[:20]:
        print(f"  {row['duration_ms'] // 60000:>3}m  {row['real_artists'][:24]:<24} "
              f"{row['title'][:44]}")
    if plan.add_count > 20:
        print(f"  ... and {plan.add_count - 20} more")
    if not args.apply:
        print("\nDry run. Re-run with --apply to import.")
        return
    result = library.apply_import(plan, conn)
    print(f"\nimported {result.added}, marked {result.marked_liked} liked "
          f"and {result.marked_listened} listened, "
          f"{result.marked_duplicate} duplicate link(s)")


def cmd_repost(conn, args) -> None:
    settings.add_repost_account(conn, args.name)
    conn.commit()
    print(f"Registered {args.name!r} as a repost account.")
    print("Future tracks from it will have the performer parsed out of the "
          "title.\nExisting rows are left alone - they may carry corrections.")


def cmd_reposts(conn, _args) -> None:
    values = settings.settings_of_kind(conn, "repost_account")
    print(f"{len(values)} repost account(s):")
    for value in sorted(values, key=str.lower):
        print(f"  {value}")


def cmd_alias(conn, args) -> None:
    settings.add_alias(conn, args.alias, args.canonical)
    conn.commit()
    print(f"{args.alias!r} now resolves to {args.canonical!r}.")


def cmd_event(conn, args) -> None:
    settings.add_known_event(conn, args.name)
    conn.commit()
    print(f"{args.name!r} will be cut out of future titles, so it is never "
          "mistaken for the artist.")


def cmd_place(conn, args) -> None:
    settings.add_location_qualifier(conn, args.word)
    conn.commit()
    print(f"Two sets whose titles name different places, like {args.word!r} "
          "and another, are no longer treated as copies of each other.")


def cmd_fix(conn, args) -> None:
    row = conn.execute(
        "SELECT title FROM tracks WHERE track_id = ?", (args.track_id,)).fetchone()
    if row is None:
        sys.exit(f"No track {args.track_id} in the database.")
    track_store.set_manual_artist(conn, args.track_id, args.artist)
    conn.commit()
    print(f"{row['title'][:64]}\n  artist -> {args.artist!r} (manual; the "
          f"resolver can never overwrite it)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="counts, keep rate, last Discovery run")
    sub.add_parser("flagged", help="tracks the resolver is unsure about")
    sub.add_parser("reposts", help="list registered repost accounts")

    p = sub.add_parser("import-likes",
                       help="adopt liked sets that never came through Discovery")
    p.add_argument("--apply", action="store_true", help="actually write them")

    p = sub.add_parser("repost", help="register an account that posts others' sets")
    p.add_argument("name")

    p = sub.add_parser("alias", help="map a spelling to its canonical form")
    p.add_argument("alias")
    p.add_argument("canonical")

    p = sub.add_parser("fix", help="correct one track's artist")
    p.add_argument("track_id")
    p.add_argument("artist")

    sub.add_parser("event", help="a festival name to cut out of titles"
                   ).add_argument("name")
    sub.add_parser("place", help="a city or stage word that tells sets apart"
                   ).add_argument("word")

    args = parser.parse_args()
    conn = db.connect()
    try:
        {"stats": cmd_stats, "flagged": cmd_flagged, "reposts": cmd_reposts,
         "repost": cmd_repost, "alias": cmd_alias, "fix": cmd_fix,
         "event": cmd_event, "place": cmd_place,
         "import-likes": cmd_import_likes}[args.command](conn, args)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
