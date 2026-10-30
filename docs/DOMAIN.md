# Domain rules

The knowledge here was tuned against real SoundCloud data over months and is
the actual value in this project. Change it only with a failing test in hand.

## What counts as a set

Duration >= 20 minutes. That is the whole filter. Short tracks are songs, not
sets, and the app is not interested in them.

## Resolving the real artist

SoundCloud's uploader is often not the performer. Large accounts repost other
people's sets, so the uploader on `Subtronics @ Wakaan Festival 2024` may be a
channel, not Subtronics. `real_artists` is the resolved performer; `uploader`
is kept as the raw fact.

Applied in order:

1. **Uploader is a known repost account** → parse the performer out of the
   title. Strip known event names first (`EDC Las Vegas`, `Lost Lands`, …) so
   the festival is not mistaken for the artist, then take what remains before
   the separator.
2. **Uploader contains `b2b`** → split into multiple artists. Unambiguous, so
   never ask.
3. **Otherwise** → the uploader is the artist.

Then always: apply aliases (`TapeB` → `Tape B`), and snap to canonical casing
seen elsewhere in the database, so one artist does not appear three ways.

**Event names are stripped longest-first.** The list contains overlapping
entries — both `Apocalypse` and `Zombie Apocalypse Festival`, both `EDC` and
`EDC Las Vegas`. The legacy code walked the list in config order, so stripping
the shorter name first left the remainder welded to the artist
(`Subtronics - Zombie Festival`, `Wooli - Las Vegas`). It only ever worked
because the list happened to be written longest-first; reordering
`artist_config.json` would have broken resolution silently. `core/artists.py`
sorts internally, so order no longer matters.

### Failure mode: event accounts

A festival or promoter account uploads other people's sets, so every title reads
`<performer> @ <event>`. If that account is not in `repost_accounts`, rule 3
applies and every set is credited to the festival. `REBiRTH Festival` held 6
sets this way before it was registered.

Detect new ones structurally, not by name: **the leading name in the title,
before the ` @ `, does not contain the uploader's name.** Measured across 926
tracks this found 11 candidates, of which 2 were worth registering — the rest
were B2B sets and support slots where the uploader really is a performer.

**Do not register an account whose titles are prose.** `borne drops 40+ insane
IDs at DEF: Atlanta` has nothing to parse; registering `DEF` fixed one track and
produced two garbage names. When the guess does not look like a name, keep the
uploader.

**`looks_like_repost_title()` cannot find these.** It bails as soon as the
uploader's name appears anywhere in the title — and a festival's own account is
named after the festival, which is in every title it posts. So the one heuristic
aimed at this problem is blind to its most common case. Use the leading-name
test above instead; keep `looks_like_repost_title` for ordinary reposters whose
name is absent from the title.

**A short or odd uploader name is not a failure.** `G` and `AYY0G` are real
artists with their own mix series (`KEY CODING VOL. XIII`, `WHERE THE BANGERS
AT VOL. 5`). What a listener makes of their sets is a taste signal, not a
parsing bug.

### Repost titles with no separator

Fan reposters often write `<artist> Live at <event>` or `<artist> <event> Set`.
The tail patterns cut at ` at `, and the event pass strips the festival and
year, but the words in between survived as part of the name: `ALLDAT Live`,
`Levity Set`, `LEVELTRONICS Live FULL Set`. On the repost branch only, trailing
`live` / `full` / `set` / `sets` are now cut from a single parsed name - never
from an uploader name (`Amp Live` is an artist), and never when nothing but
recording words would be left (`Live Set` stays whole, so it is still flagged
as unparsed). Measured on the 926 golden cases: 21 changed, every one a fix -
`Excision Full Live Set` -> `Excision`, `Tape B - Live Set` -> `Tape B`.

Rows already stored keep the old names until corrected: never bulk
re-resolve without checking `track_artists` first.

### Confidence is structural, never linguistic

`resolve_detailed()` returns a `confident` flag. It is set by **what the parser
did**, not by how the name reads:

- the guess is the whole title again, so no separator was found
- the result runs past eight words, so it is a sentence

It is explicitly NOT set by judging the name's shape. A "does this look like a
name" filter was written and measured against the real database: of 13 names it
rejected, **12 were real artists** — `999999999`, `1991`, `HOL!`, `K.o.E.`,
`I Hate Models`, `chris_who?`, `F333LZ`, `Brutalismus 3000`, `Instant Party!`.
In this genre names are digits, punctuation and lowercase. Do not reintroduce
it.

On the real database the structural test flags 10 of 926 tracks, every one
genuinely wrong (`GRiZ Seven Stars Fest Night 3`, `Fred again Thomas
Bangalter`, `Odd Mob Space Miami 1-30-26`), and none of the odd-but-real names.

A flagged track is still added — dropping a set is worse than a wrong label —
but it is reported so it can be corrected once through
`data.tracks.set_manual_artist()` instead of quietly becoming a one-off artist.

### The four grades, and what they are worth

`confidence` grades the same structural idea, so the Add screen can colour a
row. The grade is the **branch that produced the name**, and the ordering was
checked against the 121 hand-corrected tracks in the real database — the share
a human later had to fix:

| grade | branch | later corrected |
|---|---|---|
| `CERTAIN` | uploader, or a b2b in the uploader's own account name | **2%** of 564 |
| `LIKELY` | `repost_title` — one name cut out of a title | **25%** of 377 |
| `UNSURE` | `title_b2b` — a title split into several performers | **41%** of 17 |
| `GUESS` | the parse failed | **100%** of 10 |

`UNSURE` sits below `LIKELY` on 17 tracks, so the gap is not firm. It is
ordered that way on structure too: a b2b split makes the stronger claim — that
there were two performers — on the weaker evidence of a marker in free text,
and a wrong split writes two bad names into `artist_taste` instead of one.

`confident` is still the boolean the pipeline and `manage.py flagged` read, and
is now `confidence != GUESS`. Grades are not a reason to drop a track; every
one is still added.

### What CERTAIN does not mean

It means **the parser did not guess**, not that the name is right. The one
failure it cannot see is an unregistered channel — a label, festival or fan
account — posting someone else's set, where the uploader is simply not the
performer: `Aggressive Records` for a KROWDEXX set, `Pastabagel` for GRiZ,
`DodgingPolaris18` for LSZEE.

**No single-track signal separates those, and two were measured and rejected:**

- `looks_like_repost_title()` fires on **66** of the 564 uploader-branch
  tracks to catch roughly 8. It cannot tell `A-Trak — Live at Club Space
  Miami` (right) from `DJ SNAKE — The OUTLAW - LOST LANDS '24` (wrong).
- A tighter test — *would parsing this title yield a different, name-shaped
  result?* — is worse, firing on **167**, nearly all of them mix-series titles
  where the uploader is the artist: `WHERE THE BANGERS AT VOL. 1`,
  `CarTunes Vol: 4`, `Ghosty Toasty Mix`, `summer 26 mix v1.0`.

At ~10% precision either would train the user to ignore the colour. The real
signal is cross-account — *does this uploader post many different performers?*
— which needs history rather than one track, and is not built. Until it is,
the correction dialog says plainly what green means and points at
`manage.py repost`. Do not wire either rejected signal to a colour.

### B2B named in the title

An artist posting their own b2b names only themselves as the uploader, so the
collaborator exists only in the title. `resolve()` takes both, but only when
all three guards pass, because a bad split puts a garbled name into the metrics:

- at least two parts survive cleaning
- every part is short enough to be a name (5 words)
- **one part is recognisably the uploader** — otherwise the title describes
  someone else's set. `DEBSTEP @ ... W/ WOOLI B2B CRANKDAT` is debstep playing
  at an event where others went b2b, and `In The Nest With Chozen b2b Noetika
  On RAVENSCOON Radio` is Ravenscoon's *show*, not Ravenscoon performing. The
  3-word limit on that match is what separates them.

`b2b`, `b3b`, `b4b` and `back 2 back` all split. Matching only `b2b` left
`Doctor P b3b Flux Pavilion b3b Funtcase` as one name.

A part is trimmed at the first ` - `, `:` or descriptive word, so
`THE OUTLAW LIVE WORLDWIDE` becomes `THE OUTLAW`. A part made entirely of
descriptive words is dropped — in `... - b2b live set` the marker describes the
recording and `live set` is not a performer.

**The bare `x` separator is deliberately not used.** It appears between brands
(`1001Tracklists x DJ Lovers Club`) and inside collab titles as often as
between performers.

Trimming applies only to b2b parts. `Amp Live` is a real artist and must
survive untouched as an uploader name.

## Duplicate recordings

A duplicate is **the same recording posted twice** — a repost of one event.
All of these must hold:

- `real_artists` match exactly after normalisation
- both titles yield 2+ event tokens, and their Jaccard overlap is >= 0.5
- if both titles name a year, the years match
- if both name a series number (`Vol. 3`, `Episode 12`), the numbers match
- if both name a location qualifier (`Miami`, `Vegas`, `Camp`), they match —
  `CAMP EDC` and `EDC` are different recordings. One side naming a qualifier
  and the other not is just an under-specified title, not a conflict.
- durations within 10% — **unless the titles agree word for word**: identical
  event tokens and identical standalone numbers. Festival livestreams are
  ripped by several accounts, partial or trimmed, so copies of one set differ
  by minutes: at Lost Lands 2026 one Excision b2b Space Laces set arrived as
  63, 64 and 78 minutes, and Tynan's own 54-minute upload beside a 29-minute
  rip. Numbers are compared separately because event tokens drop them -
  `Gutter Music 7 (Part 1)` and `Gutter Music 3 (Part 1)` have identical tokens
  and are different mixes; the length rule is what kept them apart before.
  Measured on the golden set: no verdict changes. `partial` is a stop word.

When a duplicate pair is found, prefer the official upload over the repost;
failing that the copy that is already liked; failing that the longer file,
since reposts are trimmed. There is always an answer — the old code asked the
user, and when nobody answered, all three copies were added.

**Every performer is credited.** A b2b is stored as "Wooli, Crankdat" and
`artist_taste` splits on the comma. Grouping on the joined string credited
neither performer, and could put an artist in the stop-queueing list on zero
likes while they had a liked b2b set.

Pairs the user chose to keep both of live in `duplicate_ignores` and must never
be raised again. These are load-bearing: of the 62 duplicate pairs the detector
finds across the database, 60 are already keep-both decisions. Re-raising them
would be the single most annoying thing this app could do.

**Only `b2b` splits, not `b3b`.** "Doctor P b3b Flux Pavilion b3b Funtcase" is a
three-way set and the regex does not match it, so it stays one name while the
comma-separated copy of the same set resolves to three. That is one of the two
unresolved duplicate pairs in the database.

### What the detector cannot join, by construction

Duplicates are only compared within the same normalised artist set. A duo
posting one set from both members' accounts (ALLDAT from ALLEYCVT *and*
Crankdat) resolves to two different artists, and a reposter's copy titled
`ALLDAT ...` to a third, so no pair is ever even compared. Loosening the artist
match would join different sets by the same act, which is the error this
module is built to avoid. Instead the Add screen lets the user group copies as
one set; the tiebreak still picks the survivor, and the copies grouped away are
recorded in `dismissed_tracks` so they are not offered again.

An account the user marks official (`artist_settings` kind `self_uploader`)
counts as the performer's own in `is_official` whatever its name - a duo's
shared account rarely contains either member's name.

## What is deliberately NOT modelled

**Same artist, different event, similar set.** Artists play similar sets across
a festival run, and the user skips the later ones as redundant. This looks like
a dislike but is not.

It is not modelled, and the reason matters: the user cannot tell in advance
which of five sets are interchangeable, and a mainstage festival set differs
from the same artist's club show in the same month. Measured on real data, a
favourite artist had 5 kept sets among 18 heard — suppressing same-artist
siblings would have cost liked sets from exactly the artists the user likes
most.

Record `uploaded_at` so this stays answerable later. Do not build it on a guess.

**Set type** (festival / club / radio / compilation). Tested against 915 judged
tracks, the two buckets with the highest like rates were the two smallest, and
the classifier was a hand-written regex. Too weak to build on. Revisit with
more data.
