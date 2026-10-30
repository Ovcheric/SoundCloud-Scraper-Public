"""
Golden tests for src/core/artists.py.

tests/golden/artists.json holds this module's output for every track in
tests/golden/corpus.json - invented uploads chosen to walk every branch of the
resolver. Its job is to make any change deliberate and visible. If a case
fails, either something regressed or you meant it - if you meant it, run
scripts/gen_golden_artists.py and review the diff.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core import artists  # noqa: E402

GOLDEN = json.loads((ROOT / "tests" / "golden" / "artists.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfg() -> artists.ResolutionConfig:
    c = GOLDEN["config"]
    return artists.ResolutionConfig(
        aliases=c["aliases"],
        repost_accounts=frozenset(a.lower() for a in c["repost_accounts"]),
        known_events=tuple(c["known_events"]),
        canonical_casing=GOLDEN["canonical_casing"],
    )


@pytest.mark.parametrize(
    "case",
    GOLDEN["cases"],
    ids=[f"{c['uploader'][:18]}|{c['title'][:28]}" for c in GOLDEN["cases"]],
)
def test_resolution_is_unchanged(case, cfg):
    """
    A failure here means either a regression or a change you meant to make -
    if you meant it, regenerate and review `git diff tests/golden/`.
    """
    assert artists.resolve(case["uploader"], case["title"], cfg) == case["expected"]


def test_event_stripping_is_order_independent():
    """
    known_events holds both "Apocalypse" and "Zombie Apocalypse Festival". The
    legacy code walked the list in config order, so the shorter name matched
    first and left "Zombie Festival" welded to the artist.
    """
    events_bad_order = ["Apocalypse", "Zombie Apocalypse Festival"]
    events_good_order = ["Zombie Apocalypse Festival", "Apocalypse"]
    assert artists.guess_from_title(
        "Subtronics - Zombie Apocalypse Festival 2026", events_bad_order
    ) == artists.guess_from_title(
        "Subtronics - Zombie Apocalypse Festival 2026", events_good_order
    ) == ["Subtronics"]


# ---------------------------------------------------------------------------
# Unit tests for the pieces, so a failure points at a rule rather than a track
# ---------------------------------------------------------------------------

def test_alnum_norm_strips_punctuation_and_case():
    assert artists.alnum_norm("D-Block & S-te-Fan") == "dblockstefan"
    assert artists.alnum_norm("") == ""


def test_strip_known_events_removes_event_year_and_stage():
    got = artists.strip_known_events("Skyline 2026 I Hate Models Eastside", ["Skyline"])
    assert got == "I Hate Models"


def test_guess_from_title_cuts_at_separator():
    assert artists.guess_from_title("Kruelty @ REBiRTH Festival 2026") == ["Kruelty"]
    assert artists.guess_from_title("Subtronics - Live @ Lost Lands 2024") == ["Subtronics"]


def test_guess_from_title_splits_b2b():
    assert artists.guess_from_title("Wooli B2B Crankdat @ EDC 2026") == ["Wooli", "Crankdat"]


def test_guess_from_title_can_find_nothing():
    assert artists.guess_from_title("") == []


def test_guess_from_title_keeps_junk_rather_than_returning_nothing():
    """
    Every tail pattern is skipped when it would empty the string, so a title
    made entirely of separators survives as itself. Ugly, but deliberate: the
    caller falls back to the uploader only on an EMPTY result, and returning
    junk here is what lets resolve() notice it got nothing useful. Documented
    because it looks like a bug.
    """
    assert artists.guess_from_title("@ 2026") == ["@ 2026"]


def test_apply_aliases_is_case_insensitive():
    assert artists.apply_aliases(["tapeb"], {"TapeB": "Tape B"}) == ["Tape B"]


def test_snap_casing_uses_established_spelling():
    assert artists.snap_casing(["CRANKDAT"], {"crankdat": "Crankdat"}) == ["Crankdat"]
    assert artists.snap_casing(["Unknown"], {}) == ["Unknown"]


def test_build_canonical_casing_prefers_common_then_non_allcaps():
    # Crankdat appears twice, CRANKDAT once -> the common spelling wins.
    assert artists.build_canonical_casing(
        ["Crankdat", "CRANKDAT", "Crankdat"]
    ) == {"crankdat": "Crankdat"}
    # On a tie, the non-ALLCAPS spelling breaks it.
    assert artists.build_canonical_casing(["WOOLI", "Wooli"]) == {"wooli": "Wooli"}


def test_build_canonical_casing_splits_multi_artist_values():
    assert artists.build_canonical_casing(["Wooli, Crankdat"]) == {
        "wooli": "Wooli",
        "crankdat": "Crankdat",
    }


def test_looks_like_repost_title_false_when_uploader_named():
    assert not artists.looks_like_repost_title(
        "Samplifire", "Samplifire B2B Calcium Live @ Thunderdome 2026"
    )


def test_looks_like_repost_title_true_for_event_channel():
    assert artists.looks_like_repost_title(
        "Johnnykham", "Wooli - EDC Las Vegas 2026 (Full Set)"
    )


def test_looks_like_repost_title_is_blind_to_festival_own_accounts():
    """
    KNOWN BLIND SPOT. The heuristic bails as soon as the uploader's name occurs
    anywhere in the title - but a festival's own account is named after the
    festival, which is in every title it posts. So it cannot flag exactly the
    case it is most needed for. REBiRTH Festival had to be registered by hand.

    Detect these structurally instead: the leading name before " @ " does not
    contain the uploader's name. See docs/DOMAIN.md.
    """
    assert not artists.looks_like_repost_title(
        "REBiRTH Festival", "Kruelty @ REBiRTH Festival 2026"
    )


def test_is_official_upload_false_for_repost_account():
    assert not artists.is_official_upload(
        "REBiRTH Festival", "Kruelty", frozenset({"rebirth festival"})
    )


def test_is_official_upload_true_when_uploader_matches_artist():
    assert artists.is_official_upload("Subtronics", "Subtronics", frozenset())


def test_resolve_falls_back_to_uploader_when_title_yields_nothing():
    cfg = artists.ResolutionConfig(repost_accounts=frozenset({"some channel"}))
    assert artists.resolve("some channel", "", cfg) == "some channel"


def test_resolve_splits_b2b_in_uploader():
    cfg = artists.ResolutionConfig()
    assert artists.resolve("Wooli b2b Crankdat", "Live at EDC", cfg) == "Wooli, Crankdat"


# ---------------------------------------------------------------------------
# b2b / b3b named in the TITLE
# ---------------------------------------------------------------------------

def test_b3b_and_b4b_split_too():
    """Matching only 'b2b' left three-way sets as one name."""
    assert artists.guess_from_title("Doctor P b3b Flux Pavilion b3b Funtcase") == [
        "Doctor P", "Flux Pavilion", "Funtcase"
    ]
    assert artists.guess_from_title("A b4b B") == ["A", "B"]
    assert artists.guess_from_title("A back 2 back B") == ["A", "B"]


def test_an_artist_posting_their_own_b2b_names_the_collaborator():
    """The uploader names only themselves; the partner is in the title."""
    cfg = artists.ResolutionConfig()
    assert artists.resolve(
        "Samplifire", "Samplifire B2B Calcium (Bonefire) Live @ Thunderdome 2026", cfg
    ) == "Samplifire, Calcium"


def test_a_partial_uploader_name_still_matches():
    """jantsenmusic is the account; JANTSEN is the name in the title."""
    cfg = artists.ResolutionConfig()
    assert artists.resolve(
        "jantsenmusic", "RAVENSCOON B2B JANTSEN Live @ WAKAAN Fest 2025", cfg
    ) == "RAVENSCOON, JANTSEN"


def test_a_b2b_the_uploader_is_not_in_is_left_alone():
    """
    "DEBSTEP @ ... W/ WOOLI B2B CRANKDAT" is debstep's set at an event where
    others played b2b. Splitting it would credit the wrong people.
    """
    cfg = artists.ResolutionConfig()
    assert artists.resolve(
        "debstep", "DEBSTEP @ BEYOND WONDERLAND AFTERS W/ WOOLI B2B CRANKDAT", cfg
    ) == "debstep"


def test_a_show_named_after_its_host_is_not_a_b2b():
    """
    "In The Nest With Chozen b2b Noetika On RAVENSCOON Radio" is Ravenscoon's
    radio show, not Ravenscoon performing. The uploader appears in a long
    phrase rather than as a name, which is what the word limit catches.
    """
    cfg = artists.ResolutionConfig()
    assert artists.resolve(
        "Ravenscoon",
        "In The Nest With Chozen b2b Noetika On RAVENSCOON Radio: Episode 049",
        cfg,
    ) == "Ravenscoon"


def test_a_descriptive_b2b_is_not_a_separator():
    """"... - b2b live set" describes the recording; "live set" is not a person."""
    assert artists.clean_b2b_part("live set") == ""
    assert artists.clean_b2b_part("DJ Set") == ""


def test_clean_b2b_part_keeps_the_name_and_drops_the_event():
    assert artists.clean_b2b_part("Smoakland Live") == "Smoakland"
    assert artists.clean_b2b_part("OBEY - LIVE") == "OBEY"
    assert artists.clean_b2b_part("Franky Rizardo: 4 Hour Sunrise Set") == "Franky Rizardo"
    assert artists.clean_b2b_part("THE OUTLAW LIVE WORLDWIDE") == "THE OUTLAW"
    assert artists.clean_b2b_part("Feld - LIB Sunrise Set") == "Feld"


def test_an_artist_whose_name_ends_in_live_survives_as_an_uploader():
    """"Amp Live" is a real artist. Part-cleaning must not reach rule 4."""
    cfg = artists.ResolutionConfig()
    assert artists.resolve("Amp Live", "Amp Live @ Some Festival 2026", cfg) == "Amp Live"


def test_a_reposters_trailing_live_and_set_words_are_not_the_artist():
    """
    Nova Dubz posts "<artist> Live at <event>" and "<artist> <event> Set".
    Before this, marking it as a reposter gave "ALLDAT Live" and "Levity Set"
    - wrong names that also stopped the copy matching the official upload.
    """
    cfg = artists.ResolutionConfig(repost_accounts=frozenset({"nova dubz"}),
                                   known_events=("Lost Lands",))
    cases = {
        "ALLDAT Live at Lost Lands 2026": "ALLDAT",
        "LEVELTRONICS Live Lost Lands FULL Set 2026": "LEVELTRONICS",
        "Levity Lost Lands Set 2026": "Levity",
        "Wooli - Lost Lands 2026 live set": "Wooli",
    }
    for title, artist in cases.items():
        assert artists.resolve("Nova Dubz", title, cfg) == artist, title


def test_a_name_made_only_of_recording_words_is_left_alone():
    cfg = artists.ResolutionConfig(repost_accounts=frozenset({"nova dubz"}))
    assert artists.resolve("Nova Dubz", "Live Set", cfg) == "Live Set"


def test_a_sentence_is_never_split_into_artists():
    cfg = artists.ResolutionConfig()
    long_title = ("HUGEL presents Make The Girls Dance Boat Miami - A&R Session "
                  "B3B GROSSOMODDO & DARMON")
    assert artists.resolve("John Summit Fan Club", long_title, cfg) == "John Summit Fan Club"


# ---------------------------------------------------------------------------
# Confidence — structural, never linguistic
# ---------------------------------------------------------------------------

REPOST = artists.ResolutionConfig(repost_accounts=frozenset({"some channel"}))


def test_a_title_with_no_separator_is_flagged():
    """Nothing matched, so the "artist" is just the title read back."""
    got = artists.resolve_detailed(
        "some channel", "GRiZ Seven Stars Fest Night 3", REPOST)
    assert got.confident is False
    assert "no separator" in got.reason


def test_a_sentence_length_result_is_flagged():
    got = artists.resolve_detailed(
        "some channel", "FISHER LIVE FROM BAY OVAL PARK IN NEW ZEALAND 2026", REPOST)
    assert got.confident is False
    assert "sentence" in got.reason


def test_a_clean_parse_is_confident():
    got = artists.resolve_detailed(
        "some channel", "Wooli @ EDC Las Vegas 2026", REPOST)
    assert (got.name, got.source, got.confident) == ("Wooli", "repost_title", True)


def test_an_uploader_name_is_always_trusted():
    """It is an account name, not a parse. Nothing to be unsure about."""
    got = artists.resolve_detailed("Subtronics", "anything at all", REPOST)
    assert (got.source, got.confident) == ("uploader", True)


@pytest.mark.parametrize(
    "name", ["999999999", "1991", "HOL!", "I Hate Models", "chris_who?",
             "K.o.E.", "F333LZ", "Brutalismus 3000", "Instant Party!"]
)
def test_real_artists_with_odd_names_are_never_flagged(name):
    """
    These are all genuine acts. Judging a name by its shape - digits,
    punctuation, lowercase - rejects a dozen real artists to catch one bad
    one, which is why confidence is structural instead.
    """
    got = artists.resolve_detailed(name, f"{name} @ Some Festival 2026", REPOST)
    assert got.confident is True


def test_resolve_returns_the_name_whatever_the_confidence():
    """A flagged guess is still stored. Dropping the set would be worse."""
    assert artists.resolve(
        "some channel", "GRiZ Seven Stars Fest Night 3", REPOST
    ) == "GRiZ Seven Stars Fest Night 3"


# ---------------------------------------------------------------------------
# Graded confidence — which branch produced the name
# ---------------------------------------------------------------------------
# The grades exist so the Add screen can colour a row. They are ordered by the
# share of tracks a human later had to correct, measured on the real database:
# CERTAIN 2% of 564, LIKELY 25% of 377, UNSURE 41% of 17, GUESS 100% of 10.


def test_an_uploader_name_is_certain():
    got = artists.resolve_detailed("Subtronics", "anything at all", REPOST)
    assert got.confidence == artists.CERTAIN


def test_a_b2b_in_the_uploader_name_is_certain():
    """Splitting a real account name is not a guess about free text."""
    got = artists.resolve_detailed("Wooli b2b Crankdat", "Live somewhere", REPOST)
    assert (got.confidence, got.source) == (artists.CERTAIN, "uploader_b2b")


def test_a_name_cut_out_of_a_title_is_only_likely():
    got = artists.resolve_detailed(
        "some channel", "Wooli @ EDC Las Vegas 2026", REPOST)
    assert (got.confidence, got.source) == (artists.LIKELY, "repost_title")


def test_a_b2b_split_out_of_a_title_is_unsure():
    """
    The weakest split the parser makes: it claims two performers on the
    evidence of a marker in free text, and a wrong one writes two bad names
    into the taste metrics instead of one.
    """
    got = artists.resolve_detailed(
        "Jessica Audiffred", "Jessica Audiffred B2B LAYZ - Live @ EDC 2026",
        artists.ResolutionConfig())
    assert (got.confidence, got.source) == (artists.UNSURE, "title_b2b")


def test_a_failed_parse_is_a_guess():
    got = artists.resolve_detailed(
        "some channel", "GRiZ Seven Stars Fest Night 3", REPOST)
    assert got.confidence == artists.GUESS


def test_confident_means_anything_but_a_guess():
    """
    The older boolean is still what manage.py flagged and the pipeline read,
    so the grades must not quietly change which tracks it reports.
    """
    for level in (artists.CERTAIN, artists.LIKELY, artists.UNSURE):
        assert artists.Resolution("X", "uploader", level).confident is True
    assert artists.Resolution("X", "uploader", artists.GUESS).confident is False


def test_the_order_runs_worst_to_best_and_covers_every_level():
    assert artists.CONFIDENCE_ORDER == (
        artists.GUESS, artists.UNSURE, artists.LIKELY, artists.CERTAIN)
