"""
Tests for reviewing a playlist on the Add screen: services/review.py and
discovery.refresh().

Built on the case that prompted it: one ALLDAT set posted by both members of
the duo (ALLEYCVT and Crankdat) and copied by a fan account, Nova Dubz. The
detector cannot join those - the duo's name matches neither member - so the
user marks the reposter and groups the copies, and each mark must show at once.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core import duplicates  # noqa: E402
from core.models import TrackRef  # noqa: E402
from data import db, dismissals, settings, tracks as track_store  # noqa: E402
from services import discovery, review  # noqa: E402

NOW = "2026-10-07 18:00:00"
HOUR = 60 * 60 * 1000


def raw(track_id, title, uploader, minutes=60):
    return {"id": track_id, "title": title, "duration": minutes * 60 * 1000,
            "user": {"username": uploader}, "created_at": "2026/09/01 00:00:00 +0000"}


ALLEYCVT = raw(1, "ALLDAT @ Lost Lands 2026", "ALLEYCVT", 62)
NOVA = raw(2, "ALLDAT Live at Lost Lands 2026", "Nova Dubz", 32)
CRANKDAT = raw(3, "ALLDAT - Lost Lands 2026 (Full Set)", "Crankdat", 61)
SONG = raw(4, "Crankdat - New Single", "Crankdat", 3)


class FakeApi:
    def __init__(self, tracks):
        self.tracks, self.written, self.discovery_ids = list(tracks), None, []

    def resolve(self, url):
        return {"kind": "playlist", "id": 1, "title": "Lost Lands 2026"}

    def playlist_tracks(self, playlist_id):
        return self.tracks

    def discovery_playlist(self):
        return 99, list(self.discovery_ids)

    def set_playlist_tracks(self, playlist_id, ids):
        self.written = self.discovery_ids = list(ids)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.execute("INSERT INTO artist_settings VALUES ('known_event', 'Lost Lands')")
    yield c
    c.close()


def plan_of(conn, *tracks):
    return discovery.plan_add("url", conn, FakeApi(tracks))


def row(plan, track_id):
    return next(r for r in plan.rows if r.track_id == str(track_id))


def adding(plan):
    return sorted(t["track_id"] for t in plan.to_add)


# ---------------------------------------------------------------------------
# The raw playlist, as uploaded
# ---------------------------------------------------------------------------

def test_every_playlist_track_gets_a_row_in_upload_order(conn):
    plan = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT, SONG)
    assert [r.track_id for r in plan.rows] == ["1", "2", "3", "4"]
    assert row(plan, 4).status == discovery.SHORT


def test_a_track_already_stored_is_marked_as_in_the_library(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "t",
                                    "real_artists": "ALLEYCVT"}], seen_at=NOW)
    assert row(plan_of(conn, ALLEYCVT), 1).status == discovery.LIBRARY


def test_an_unknown_reposter_wrongly_looks_official_until_marked(conn):
    """The problem in one line: any unregistered account reads as the artist."""
    plan = plan_of(conn, NOVA)
    assert row(plan, 2).official and not row(plan, 2).reposter
    review.mark_reposter(conn, ["Nova Dubz"], True)
    plan = discovery.refresh(plan, conn)
    assert row(plan, 2).reposter and not row(plan, 2).official
    assert plan.to_add[0]["real_artists"] == "ALLDAT"


def test_only_the_performers_own_uploads_carry_the_star(conn):
    review.mark_reposter(conn, ["Nova Dubz"], True)
    plan = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)
    assert plan.official == {"1", "3"}


# ---------------------------------------------------------------------------
# Grouping copies the detector cannot see
# ---------------------------------------------------------------------------

def test_the_detector_alone_keeps_all_three_copies(conn):
    assert adding(plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)) == ["1", "2", "3"]


def test_marking_them_one_set_keeps_the_best_official_copy(conn):
    review.mark_reposter(conn, ["Nova Dubz"], True)
    plan = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)
    review.mark_same(plan, ["1", "2", "3"])
    plan = discovery.refresh(plan, conn)
    assert adding(plan) == ["1"]                    # official, and the longest
    assert row(plan, 2).status == discovery.DUPLICATE
    assert "official upload" in row(plan, 2).note
    assert "longer copy" in row(plan, 3).note


def test_groups_that_share_a_track_merge(conn):
    plan = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)
    review.mark_same(plan, ["1", "2"])
    review.mark_same(plan, ["2", "3"])
    assert plan.same_recording == [frozenset({"1", "2", "3"})]


def test_a_copy_grouped_with_a_stored_track_is_dropped_for_it(conn):
    track_store.insert_many(conn, [{"track_id": "1", "title": "ALLDAT @ Lost Lands 2026",
                                    "real_artists": "ALLEYCVT"}], seen_at=NOW)
    plan = plan_of(conn, ALLEYCVT, NOVA)
    review.mark_same(plan, ["1", "2"])
    plan = discovery.refresh(plan, conn)
    assert adding(plan) == []
    assert "already in library" in row(plan, 2).note


def test_splitting_a_group_puts_the_copy_back(conn):
    plan = plan_of(conn, ALLEYCVT, CRANKDAT)
    review.mark_same(plan, ["1", "3"])
    plan = discovery.refresh(plan, conn)
    assert review.mark_different(plan, conn, ["3"]) == 1
    assert adding(discovery.refresh(plan, conn)) == ["1", "3"]


def test_splitting_a_detected_pair_is_remembered_for_good(conn):
    first = raw(5, "Wooli @ Lost Lands 2026", "Wooli", 60)
    copy = raw(6, "Wooli - Live @ Lost Lands 2026", "Wooli", 59)
    plan = plan_of(conn, first, copy)
    assert adding(plan) == ["5"]
    review.mark_different(plan, conn, ["6"])
    assert frozenset({"5", "6"}) in settings.keep_both_pairs(conn)
    assert adding(plan_of(conn, first, copy)) == ["5", "6"]


# ---------------------------------------------------------------------------
# Corrections carry over, and adding remembers the grouping
# ---------------------------------------------------------------------------

def test_a_typed_artist_survives_a_refresh(conn):
    plan = plan_of(conn, NOVA)
    plan.corrections["2"] = "ALLEYCVT, Crankdat"
    assert discovery.refresh(plan, conn).corrections == {"2": "ALLEYCVT, Crankdat"}


def test_a_grouped_away_copy_is_never_offered_again(conn):
    review.mark_reposter(conn, ["Nova Dubz"], True)
    plan = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)
    review.mark_same(plan, ["1", "2", "3"])
    plan = discovery.refresh(plan, conn)
    discovery.apply_add(plan, conn, FakeApi([]), now=NOW)
    assert dismissals.dismissed_ids(conn) == {"2", "3"}
    again = plan_of(conn, ALLEYCVT, NOVA, CRANKDAT)
    assert [r.status for r in again.rows] == [
        discovery.LIBRARY, discovery.DISMISSED, discovery.DISMISSED]


def test_skipping_with_a_reason_turns_the_set_down(conn):
    plan = plan_of(conn, ALLEYCVT, CRANKDAT)
    assert review.skip(plan, conn, ["3"], "style", now=NOW) == 1
    assert row(discovery.refresh(plan, conn), 3).status == discovery.DISMISSED


def test_uploaders_are_listed_once_each(conn):
    plan = plan_of(conn, CRANKDAT, SONG, NOVA)
    assert review.uploaders(plan, ["3", "4", "2"]) == ["Crankdat", "Nova Dubz"]


# ---------------------------------------------------------------------------
# Official accounts
# ---------------------------------------------------------------------------

def test_a_duo_account_marked_official_gets_the_star(conn):
    """The duo's own account is named after neither member."""
    duo = raw(7, "Lost Lands 2026 full set", "ALLDAT", 60)
    track = TrackRef("7", duo["title"], "ALLEYCVT, Crankdat", "ALLDAT", HOUR)
    assert not duplicates.is_official(track, frozenset())
    assert duplicates.is_official(track, frozenset(), frozenset({"alldat"}))
    review.mark_official(conn, ["ALLDAT"], True)
    assert row(plan_of(conn, duo), 7).official


def test_an_account_cannot_be_both_reposter_and_official(conn):
    review.mark_reposter(conn, ["Nova Dubz"], True)
    review.mark_official(conn, ["nova dubz"], True)
    cfg = settings.load_duplicate_config(conn)
    assert "nova dubz" in cfg.official_accounts
    assert "nova dubz" not in cfg.repost_accounts
    review.mark_reposter(conn, ["NOVA DUBZ"], True)
    cfg = settings.load_duplicate_config(conn)
    assert "nova dubz" in cfg.repost_accounts
    assert "nova dubz" not in cfg.official_accounts


def test_unmarking_a_reposter_ignores_case(conn):
    settings.add_repost_account(conn, "Nova Dubz")
    review.mark_reposter(conn, ["nova dubz"], False)
    assert settings.load_duplicate_config(conn).repost_accounts == frozenset()


# ---------------------------------------------------------------------------
# Lost Lands 2026, as it actually arrived
# ---------------------------------------------------------------------------

LEVELTRONICS = raw(20, "LEVELTRONICS Live Lost Lands FULL Set 2026", "Nova Dubz", 70)
EDM_SUBTRONICS = raw(21, "Subtronics B2B Level Up - Live @ Lost Lands 2026",
                     "EDM Livesets", 70)


@pytest.fixture
def reposters(conn):
    for name in ("Nova Dubz", "EDM Livesets", "No Collar"):
        settings.add_repost_account(conn, name)
    return conn


def test_a_typed_artist_decides_duplicates_but_is_not_stored_as_parsed(reposters):
    plan = plan_of(reposters, LEVELTRONICS, EDM_SUBTRONICS)
    assert adding(plan) == ["20", "21"]          # a duo name matches nobody
    plan.corrections["20"] = "Subtronics, Level Up"
    plan = discovery.refresh(plan, reposters)
    assert len(plan.to_add) == 1
    assert row(plan, 20).status == discovery.DUPLICATE or \
        row(plan, 21).status == discovery.DUPLICATE
    kept = plan.to_add[0]
    assert kept["real_artists"] in ("LEVELTRONICS", "Subtronics, Level Up")
    if kept["track_id"] == "20":                 # the resolver's own answer
        assert kept["real_artists"] == "LEVELTRONICS"


def test_always_reading_a_duo_name_as_its_members_joins_their_copies(reposters):
    review.always_read_as(reposters, "LEVELTRONICS", "Subtronics, Level Up")
    plan = plan_of(reposters, LEVELTRONICS, EDM_SUBTRONICS)
    assert len(plan.to_add) == 1
    assert plan.to_add[0]["real_artists"] == "Subtronics, Level Up"


def test_partial_rips_of_one_set_are_one_set(reposters):
    """63, 64 and 78 minutes - three rips of one Excision b2b Space Laces set."""
    rips = [raw(30, "Excision b2b Space Laces - Lost Lands 2026 (Full Set)", "Nova Dubz", 63),
            raw(31, "X B2B Space Laces - Live @ Lost Lands 2026", "EDM Livesets", 78),
            raw(32, "Excision b2b Space Laces Live @ Lost Lands 2026", "No Collar", 64)]
    reposters.execute("INSERT INTO artist_aliases VALUES ('X', 'Excision')")
    plan = plan_of(reposters, *rips)
    assert adding(plan) == ["31"]                # the longest rip
