"""
Resolve the real performer behind a SoundCloud upload.

SoundCloud's uploader is often not the artist: festival channels and repost
accounts post other people's sets. This module turns (uploader, title) into a
canonical performer string. See docs/DOMAIN.md for the rules and their history.

Public: ResolutionConfig, Resolution, resolve, resolve_detailed,
        CERTAIN, LIKELY, UNSURE, GUESS, CONFIDENCE_ORDER,
        guess_from_title, strip_known_events,
        apply_aliases, snap_casing, build_canonical_casing,
        looks_like_repost_title, is_official_upload, alnum_norm

Pure. Imports nothing from src/ - no file, network or database access. The
caller supplies a ResolutionConfig; building one is data/'s job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# Stage/direction suffixes festival recap channels tack on after the artist
# name, e.g. "... I Hate Models Eastside".
_STAGE_SUFFIXES = (
    r"Eastside|Westside|Downtown|Uptown|Northside|Southside|Main\s*Stage|"
    r"Side\s*Stage|Stage"
)

# b2b, b3b, b4b and the spelled-out forms. Three- and four-way sets are common
# enough that matching only "b2b" left "Doctor P b3b Flux Pavilion b3b Funtcase"
# as a single name.
_B2B_SPLIT = re.compile(
    r"\s+(?:b\d+b|back\s*2\s*back|back\s*to\s*back)\s+", re.IGNORECASE
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

# Words that describe the recording, not the performer. A part made entirely of
# these is not a name at all.
_NOISE_WORDS = frozenset({
    "live", "set", "sets", "full", "dj", "mix", "sunrise", "official",
    "presents", "feat", "ft", "b2b", "the", "a", "and", "with",
})

# Trailing words that describe the recording, not the performer. Only ever
# stripped from a b2b part - "Amp Live" is a real artist and must survive as an
# uploader name.
_PART_NOISE = re.compile(
    r"\s+\b(?:live|set|full|dj|mix|sunrise|official|presents|feat|ft)\b.*$",
    re.IGNORECASE,
)

# Left after the performer by a separator-less repost title ("Levity Set").
# Cut only from names parsed out of titles - never from an uploader name.
_TRAILING_RECORDING_WORDS = re.compile(r"(?:\s+(?:live|full|set|sets))+\s*$", re.I)

# A performer name is short. Anything longer is a sentence, and splitting it
# produces worse output than leaving the uploader alone.
MAX_NAME_WORDS = 5
# How long a part may be and still count as "this is the uploader". Loose
# matching on a long phrase is how "Noetika On RAVENSCOON Radio" would be
# mistaken for Ravenscoon performing.
MAX_UPLOADER_MATCH_WORDS = 3

# Applied in order. Each strips a trailing "<separator> <event stuff>" from a
# title, leaving the performer. Order matters: see guess_from_title.
_TAIL_PATTERNS = (
    r"\s*(?:[-–—]\s*)?(?:Live|LIVE)\s*@.*",
    r"\s*@\s+.*",
    r"\s+at\s+.*",
    r"\s*\|.*",
    r"\s*[-–—]\s*(?:Live\s+)?(?:Full\s*Set|DJ\s*Set|Live\s*Set|Full\s*Mix)\b.*",
    r"\s*[-–—]\s*\d{4}\b.*",
    r"\s*\(.*?\)\s*$",
    r"\s*#\w+.*$",
    r"\s*\[.*?\]\s*$",
    r"\b\d+\.\d+\b",
)


@dataclass(frozen=True)
class ResolutionConfig:
    """Everything resolve() needs. Built by data/, never read from disk here."""

    aliases: Mapping[str, str] = field(default_factory=dict)
    repost_accounts: frozenset = frozenset()  # lowercased
    known_events: Sequence[str] = ()
    canonical_casing: Mapping[str, str] = field(default_factory=dict)

    def is_repost_account(self, uploader: str) -> bool:
        return uploader.lower() in self.repost_accounts


def alnum_norm(s: str) -> str:
    """Lowercase, letters and digits only - for loose name matching."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def strip_known_events(title: str, known_events: Sequence[str] | None) -> str:
    """
    Remove known festival names, stray years and a trailing stage word.
    Catches titles with no separator at all:
    "Skyline 2026 I Hate Models Eastside" -> "I Hate Models".
    """
    # Longest first, so "EDC Las Vegas" is removed before plain "EDC" can
    # strand a dangling "Las Vegas" in the artist name. The legacy code walked
    # the list in config order and only worked because the list happened to be
    # written that way - reordering artist_config.json silently broke it.
    clean = title
    for event in sorted(known_events or (), key=len, reverse=True):
        clean = re.sub(re.escape(event), " ", clean, flags=re.IGNORECASE)
    clean = _YEAR.sub(" ", clean)
    clean = re.sub(rf"\b(?:{_STAGE_SUFFIXES})\b\s*$", " ", clean, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean).strip(" -–—|@")


def guess_from_title(title: str, known_events: Sequence[str] | None = None) -> list[str]:
    """Best guess at the performer(s) named in a title. May return []."""
    # Pass 1 - cut the tail at an explicit separator. This MUST run before the
    # known-events pass: removing the event name first can eat the very
    # separator these patterns look for, stranding a dangling "- Live" that
    # nothing would then clean up.
    clean = title
    for pattern in _TAIL_PATTERNS:
        cleaned = re.sub(pattern, "", clean, flags=re.IGNORECASE).strip()
        if cleaned:
            clean = cleaned

    # Pass 2 - for titles with no clean separator, strip event names, years
    # and trailing stage words from whatever is left.
    stripped = strip_known_events(clean, known_events)
    if stripped:
        clean = stripped
    clean = re.sub(
        rf"\b(?:{_STAGE_SUFFIXES})\b\s*$", "", clean, flags=re.IGNORECASE
    ).strip()
    if not clean:
        return []
    return [p.strip() for p in _B2B_SPLIT.split(clean) if p.strip()]


def clean_b2b_part(name: str) -> str:
    """
    Trim event noise off one side of a b2b, keeping the name at the front.

    Everything after " - " or ":" is set or venue description in practice
    ("Feld - LIB Sunrise Set", "Franky Rizardo: 4 Hour Sunrise Set"), and
    everything from the first descriptive word onward is too ("THE OUTLAW LIVE
    WORLDWIDE" -> "THE OUTLAW").

    Only ever applied to a b2b part. "Amp Live" is a real artist and survives
    untouched as an uploader name.
    """
    name = re.split(r"\s+[-–—]\s+|:", name, maxsplit=1)[0]
    name = _PART_NOISE.sub("", name).strip(" -–—|,&")
    # Nothing but descriptive words: the marker was a description, not a
    # separator. "... - b2b live set" splits into a name and the phrase
    # "live set", which is not a performer.
    words = [w.strip(".,&|-").lower() for w in name.split()]
    if words and all(w in _NOISE_WORDS for w in words if w):
        return ""
    return name


# How far to trust a resolution. Set by what the parser DID, never by how the
# name reads. Measured against the 121 hand-corrected tracks in the real
# database - the share a human later had to fix rises cleanly down this list:
#
#   CERTAIN   2% of 564    LIKELY  25% of 377
#   UNSURE   41% of  17    GUESS  100% of  10
#
# Ordered worst-first, which is the order the review list is shown in.
CERTAIN = "certain"   # the uploader's own account name - nothing was parsed
LIKELY = "likely"     # one name cut out of a title at a separator
UNSURE = "unsure"     # a title split into several performers
GUESS = "guess"       # the parse failed; the stored name is a placeholder

CONFIDENCE_ORDER = (GUESS, UNSURE, LIKELY, CERTAIN)


@dataclass(frozen=True)
class Resolution:
    """
    A resolved artist plus how it was reached.

    `confidence` reflects what the parser did - not whether the name looks odd.
    Electronic artists are called `999999999`, `HOL!`, `I Hate Models`,
    `chris_who?` and `K.o.E.`, so judging a name by its shape rejects a dozen
    real acts to catch one bad one. Structure is reliable where vocabulary is
    not.

    UNSURE sits below LIKELY because a b2b split makes the stronger claim -
    that there were two performers - on the weaker evidence of a marker in free
    text, and a wrong split writes two bad names into the taste metrics instead
    of one. The measured rates agree, though on only 17 tracks.
    """

    name: str
    source: str        # repost_title | uploader_b2b | title_b2b | uploader
    confidence: str = CERTAIN
    reason: str = ""   # why not, when it is a GUESS

    @property
    def confident(self) -> bool:
        """Did the parser reach an answer at all? GUESS is the only no."""
        return self.confidence != GUESS


# Beyond this a "name" is a sentence. Deliberately loose: real acts include
# "D-Block & S-te-Fan vs Phuture Noize" at six words.
FLAG_NAME_WORDS = 8


def _unparsed(title: str, guess: str) -> bool:
    """
    True if the guess is just the title again.

    Every tail pattern is skipped when it would empty the string, so a title
    with no separator survives whole - "we threw a party with ISOxo in an
    underground parking garage!" comes back as itself. That is the parser
    saying it found nothing, and it is worth flagging rather than storing.
    """
    return alnum_norm(guess) == alnum_norm(title)


def _looks_like_the_uploader(part: str, uploader_norm: str) -> bool:
    """Is this side of the b2b the account that posted it?"""
    part_norm = alnum_norm(part)
    if not part_norm or not uploader_norm:
        return False
    if part_norm == uploader_norm:
        return True
    if len(part.split()) > MAX_UPLOADER_MATCH_WORDS:
        return False
    return part_norm in uploader_norm or uploader_norm in part_norm


def b2b_names_from_title(title: str, uploader: str,
                         known_events: Sequence[str] | None = None) -> list[str]:
    """
    Performers when the TITLE describes a b2b, or [] when it is not safe to say.

    Guarded three ways, because the cost of splitting wrongly is a garbled
    artist that pollutes the metrics:
      * at least two parts survive cleaning
      * every part is short enough to be a name
      * one part is recognisably the uploader - otherwise the title is
        describing someone else's set, as in "DEBSTEP @ ... W/ WOOLI B2B
        CRANKDAT" or a radio show named after its host
    """
    parts = [clean_b2b_part(p) for p in guess_from_title(title, known_events)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return []
    if any(len(p.split()) > MAX_NAME_WORDS for p in parts):
        return []
    uploader_norm = alnum_norm(uploader)
    if not any(_looks_like_the_uploader(p, uploader_norm) for p in parts):
        return []
    return parts


def apply_aliases(names: Iterable[str], aliases: Mapping[str, str]) -> list[str]:
    """Map known spellings to their canonical form: TapeB -> Tape B."""
    lower = {k.lower(): v for k, v in aliases.items()}
    return [lower.get(n.lower(), n) for n in names]


def snap_casing(names: Iterable[str], canonical: Mapping[str, str]) -> list[str]:
    """Replace each name with the casing already established in the database."""
    return [canonical.get(alnum_norm(n), n) for n in names]


def build_canonical_casing(artist_strings: Iterable[str]) -> dict:
    """
    {normalized_name: best display casing} from existing real_artists values,
    so CRANKDAT snaps to Crankdat without a hand-written alias per variant.
    Most common spelling wins; a non-ALLCAPS spelling breaks ties.
    """
    counts: dict = {}
    for value in artist_strings:
        for part in (p.strip() for p in (value or "").split(",")):
            key = alnum_norm(part)
            if not part or not key:
                continue
            counts.setdefault(key, {})
            counts[key][part] = counts[key].get(part, 0) + 1
    return {
        key: max(variants.items(), key=lambda kv: (kv[1], kv[0] != kv[0].upper()))[0]
        for key, variants in counts.items()
    }


def looks_like_repost_title(
    uploader: str, title: str, known_events: Sequence[str] | None = None
) -> bool:
    """
    True if the title looks like it names someone OTHER than the uploader -
    i.e. the uploader is probably an unregistered repost or festival channel.
    Used to surface candidates for the repost list, never to act silently.
    """
    uploader_norm = alnum_norm(uploader)
    if uploader_norm and uploader_norm in alnum_norm(title):
        return False  # uploader's own name is in the title -> it is them
    has_marker = bool(
        re.search(
            r"@|\bb2b\b|full\s*set|dj\s*set|live\s*set|-\s*live\b|\bpresents\b",
            title,
            re.IGNORECASE,
        )
    )
    has_known_event = any(e.lower() in title.lower() for e in (known_events or ()))
    return has_marker or bool(_YEAR.search(title)) or has_known_event


def is_official_upload(
    uploader: str, real_artists: str, repost_accounts: frozenset
) -> bool:
    """True if the upload appears to come from the performer's own account."""
    if uploader.lower() in repost_accounts:
        return False
    lowered = uploader.lower()
    return any(
        name in lowered or lowered in name
        for name in (a.strip().lower() for a in real_artists.split(","))
        if name
    )


def resolve(uploader: str, title: str, cfg: ResolutionConfig) -> str:
    """
    The performer(s) behind an upload, comma-separated.

      1. uploader is a known repost account -> parse the performer from the title
      2. uploader contains "b2b"            -> split it (unambiguous)
      3. title describes a b2b the uploader is in -> take both performers
      4. otherwise                          -> the uploader is the artist

    Aliases and canonical casing are applied in every branch.
    """
    return resolve_detailed(uploader, title, cfg).name


def _names_from_repost_title(title: str, cfg: ResolutionConfig) -> list[str]:
    """The performer(s) a repost channel's title names, tidied. May be []."""
    names = guess_from_title(title, cfg.known_events)
    if len(names) > 1:
        # Multiple names can only have come from a b2b split, so the same
        # trimming applies. If a part still reads as a sentence the split
        # was spurious, and the unsplit guess is the safer answer.
        cleaned = [c for c in (clean_b2b_part(n) for n in names) if c]
        if cleaned and not any(len(c.split()) > MAX_NAME_WORDS for c in cleaned):
            return cleaned
        return [" ".join(names)]
    if names:
        # Only if a real name is left - "Live Set" must not become "Live".
        trimmed = _TRAILING_RECORDING_WORDS.sub("", names[0]).strip(" -–—|@,")
        if any(w.lower() not in _NOISE_WORDS for w in trimmed.split()):
            return [trimmed]
    return names


def resolve_detailed(uploader: str, title: str, cfg: ResolutionConfig) -> Resolution:
    """resolve(), plus how the answer was reached and whether to trust it."""

    def finish(names: list[str]) -> str:
        return ", ".join(
            snap_casing(apply_aliases(names, cfg.aliases), cfg.canonical_casing))

    if cfg.is_repost_account(uploader):
        names = _names_from_repost_title(title, cfg)
        if not names:
            return Resolution(uploader, "repost_title", GUESS,
                              "nothing could be parsed from the title")
        name = finish(names)
        if _unparsed(title, name):
            return Resolution(name, "repost_title", GUESS,
                              "no separator found - this is the whole title")
        if len(name.split()) > FLAG_NAME_WORDS:
            return Resolution(name, "repost_title", GUESS,
                              f"{len(name.split())} words is a sentence, not a name")
        return Resolution(name, "repost_title", LIKELY)

    if _B2B_SPLIT.search(uploader):
        # The account name itself says b2b. Splitting a real account name is
        # not a guess about free text, so this ranks with the uploader branch.
        return Resolution(
            finish([p.strip() for p in _B2B_SPLIT.split(uploader) if p.strip()]),
            "uploader_b2b", CERTAIN)

    # An artist posting their own b2b names only themselves as the uploader,
    # so the collaborator is recoverable only from the title.
    shared = b2b_names_from_title(title, uploader, cfg.known_events)
    if shared:
        return Resolution(finish(shared), "title_b2b", UNSURE)

    # The uploader is a real account name, never a parse. Always trusted.
    return Resolution(finish([uploader]) or uploader, "uploader", CERTAIN)
