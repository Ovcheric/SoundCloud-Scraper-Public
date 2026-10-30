# Decisions

Append-only. Read before "fixing" something that looks wrong on purpose.
Newest last.

### 2026-09-19 — Restructure, do not rewrite
The old app was 5,728 lines in one file, but lines 1–1080 contained exactly one
tkinter reference (the import). Config, auth, database I/O, artist resolution,
duplicate detection and the API client were already module-level functions. The
god class was only the GUI. The tuned heuristics are the value and were
discovered empirically against real data, so they get ported, not reinvented.

### 2026-09-19 — SQLite, not CSV
927 rows with duplicate relations and per-track verdicts stored as text with a
`.bak` beside it. SQLite gives atomic writes, real queries, and deletes
`metrics.json` (232 KB of derived data) outright — every number in it is a
`SELECT` over fewer than a thousand rows.

### 2026-09-19 — Verdicts are derived, never stored
The CSV's `liked` column had two writers: `mark_liked()` (SoundCloud sync, only
ever wrote `yes`) and an edit dialog (wrote anything). They overwrote each other
silently — one import flipped a hand-typed `no` to `yes` with no record. Now
facts are stored and `track_verdicts` derives the answer. Do not add a `liked`
column back.

### 2026-09-19 — Backfill ran once, and could only ever run once
Discovery was empty and every archived track had been judged, so blank rows
were briefly unambiguous: in likes = liked, absent = skipped. That converted 783
rows into real negative signal. After the next Add run the two become
indistinguishable — hence the snapshot rule.

### 2026-09-19 — A like is never downgraded
`liked=yes` with no matching API like is kept, not cleared. A like can be
legitimately derived: the duplicate logic transfers it to the surviving copy
(a set reads as liked when only its duplicate child was hearted). Likes are
rare against skips, so losing a real like costs far more than keeping a
questionable one. Divergences are recorded, not resolved.

### 2026-09-19 — Two screens, not seven tabs
Only Add Sets and Clean Up Discovery were ever used. Duplicate detection and
artist resolution already ran inside Add — the Library tab's buttons were
retroactive backfill, and the modal dialogs mid-flow were the actual complaint.
Import Likes becomes an automatic step. Library, Insights, Import from
Discovery, Listening Queue and the in-app Help tabs are dropped.

### 2026-09-19 — Metrics are two queries, not a tab
`artist_taste` gives artists by liked count. The actionable half is the inverse:
artists with 4+ heard and zero liked. Stop queueing those. The Insights tab was 366 lines answering a question a `GROUP BY`
answers.

### 2026-09-19 — discovery.db is committed to git
Binary, so no readable diffs, but it is the irreplaceable asset and it is small.
Git is the backup. If diffable history is wanted later, add a CSV export step —
do not move the source of truth back to text. Superseded for the public copy:
see 2026-10-30.

### 2026-09-19 — DEF is deliberately not a repost account
`DEF` is an event brand and two of its three titles name someone other than the
uploader, so it looks like a mis-attribution. It is left alone: its titles are
prose (`borne drops 40+ insane IDs at DEF: Atlanta`, `we threw a party with
ISOxo in an underground parking garage!`), and registering it fixed one track
while producing two garbage names. Do not add it without a guard that rejects a
guess which does not look like a performer name.

### 2026-09-19 — `G` and `AYY0G` are real artists
Both were wrongly called parsing failures on the strength of their short names.
Their uploaders are genuinely named that and the titles are their own mix series.
Resolution is correct; do not "fix" it.

### 2026-09-19 — Golden fixtures are the contract for ported logic
`tests/golden/artists.json` freezes the legacy resolver's output for all 926
tracks. Porting against it caught a bug on the first run: the fixture generator
sorted `known_events`, which reordered the list and stranded "Las Vegas" in 45
artist names. The list was silently order-dependent and nothing said so.
Deliberate improvements live in `INTENTIONAL_DIFFS` in the test, with a guard
that fails if legacy ever catches up — never by quietly re-freezing fixtures.

### 2026-09-19 — A typed artist correction always wins
`real_artists` had the same two-writer flaw as `liked`: the resolver wrote it
and the old artist dialogs wrote it, with no record of which. 121 of 926 stored
values cannot be reproduced by the resolver — it turns "Tomorrowland Friendship
Mix with Subtronics" into "Tomorrowland". 36 of the 83 multi-artist values use a
comma with no space, which the resolver never emits, and some carry typos only
a human makes.

Split into `real_artists` (resolver, recomputable) and `real_artists_manual`
(human, authoritative), read through `track_artists`. All 121 divergent rows
were marked manual rather than only the 36 with hard evidence: the stored value
was better in every case examined, and preserving a correct value costs nothing
while losing one is unrecoverable.

### 2026-09-19 — An empty Discovery snapshot is recorded as a run
discovery_runs exists separately from discovery_snapshots because a run that
finds an empty playlist writes no membership rows, making it indistinguishable
from never having run. That is not a corner case: the playlist is empty today,
so the very first real run would have recorded nothing and inferred nothing. A
test caught it before any code depended on it.

### 2026-09-19 — The manual-artist set is frozen data, not a derivation
migrate_to_sqlite.py used to decide "manual" by comparing each stored value
against the resolver and treating any difference as human. That was true on the
day and false the moment the resolver improved: fixing b2b/b3b parsing changed
129 outputs, and recomputing would have relabelled those improvements as
corrections the user never made. The 121 ids were frozen in manual_artists.json
and consumed by the migration.

### 2026-09-19 — Golden fixtures now come from core/, not legacy
They were generated from the legacy resolver to prove the port was faithful.
That is done and committed, so they are regenerated from core/artists.py and
their job is detecting unintended change. Review `git diff tests/golden/`
before accepting a regeneration.

### 2026-09-19 — artist_taste splits on commas
A b2b was stored as "Wooli, Crankdat" and grouped as a single artist, crediting
neither performer. Each performer's counts came out short, and an artist with
a liked b2b set could sit in the stop-queueing list on zero likes — the skip
filter giving actively wrong advice. track_artist_names splits with a
recursive CTE. Counts get more honest, not more flattering: heard counts rise
as well as liked ones.

### 2026-09-19 — Artist names are never judged by their shape
A "does this look like a name" filter was written, and measured against the
real database it rejected 13 names of which 12 were real artists: 999999999,
1991, HOL!, K.o.E., I Hate Models, chris_who?, F333LZ, Brutalismus 3000,
Instant Party!. Electronic acts are named with digits, punctuation and
lowercase, so vocabulary and character-class heuristics cannot work here.

Confidence is structural instead — the guess is the whole title again, or the
result is sentence-length. That flags 10 of 926 tracks, all genuinely wrong,
and none of the odd-but-real names. Do not reintroduce the shape filter.

### 2026-09-19 — No shared database connection
The first live click produced "SQLite objects created in a thread can only be
used in that same thread". App held one connection made on the UI thread while
the pipelines run on workers. All 1951 tests passed, because every one of them
calls the services directly on a single thread - the fake API removed the
network, and nothing ever crossed a thread boundary.

App.db() now opens a short-lived connection per caller, committed and closed on
exit. Opening a local SQLite file costs microseconds and it removes the whole
class of bug, including the stale-snapshot problem a long-lived reader would
have had after a worker wrote. tests/test_app.py covers the crossing directly.

### 2026-09-19 — Cutover: discovery.db is the only live store
Every path ran against real SoundCloud: Add wrote three tracks to Discovery,
the snapshot diff inferred a skip, hearting outranked removal on a track that
was both, and the removal button cleared the playlist. So the CSV era ended.

database.csv and the JSON files are frozen exports and nothing reads them. The
migrations that consumed them moved to scripts/one-shot/ and refuse to run once
the database holds any Discovery run — rebuilding would discard snapshot
history, which exists only in the difference between two observations and
cannot be reconstructed from the CSV or from SoundCloud.

The legacy app still runs and still writes database.csv, but nothing reads it,
so those edits go nowhere. It stays as the oracle behind the golden fixtures.

### 2026-09-19 — Corrections are typed before a track lands, not after
The Add preview flagged uncertain artists and then offered no way to fix them.
Double-clicking a row now opens an edit dialog, and the value is written to
real_artists_manual rather than real_artists, so re-resolving can never undo
it. Correcting before the write means the track lands right the first time
instead of becoming a one-off artist that has to be hunted down later.

### 2026-09-19 — Artist confidence is graded and coloured, worst first
The Add preview had one amber colour for "flagged" and nothing else, so a
clean uploader name and a name carved out of a festival title looked alike.
`resolve_detailed()` now returns one of four grades — CERTAIN, LIKELY, UNSURE,
GUESS — set by the branch that produced the name, and the preview colours rows
green to red and sorts the worst to the top, so a run is reviewed by reading
the head of the list instead of all of it.

The ordering was earned, not chosen: against the 121 hand-corrected tracks the
later-correction rate runs 2%, 25%, 41%, 100% down the grades. UNSURE below
LIKELY rests on only 17 tracks, but a wrong b2b split writes two bad names
into artist_taste instead of one, so the structural argument agrees.

The grades are a rendering decision only. Nothing is dropped, filtered or
stored differently because of one, and `confident` is still the boolean the
pipeline reads — now `confidence != GUESS`.

### 2026-09-19 — Green says "not guessed", and no signal was added to fix that
CERTAIN covers the uploader branch, whose one blind spot is an unregistered
channel posting other people's sets — `Aggressive Records` for a KROWDEXX set,
`Pastabagel` for GRiZ. Two single-track detectors were measured against the
real library and both rejected: `looks_like_repost_title()` fires 66 times to
catch about 8, and a tighter "the title names someone else" test fires 167
times, nearly all mix-series titles where the uploader really is the artist.

At roughly 10% precision either would have taught the user to ignore the
colour, which costs more than the handful it finds. The correction dialog
states what green means and points at `manage.py repost` instead. The signal
that would work is cross-account — does this uploader post many different
performers? — and needs history rather than one track. Not built.

### 2026-09-19 — One Tk root for the whole test session
tests/test_add_tab.py made its own root and broke tests/test_app.py, which
made another: Tk does not tolerate several roots created and destroyed in one
process, and the second fails with "tk wasn't installed properly", which reads
like a broken environment rather than a test-ordering bug. A session-scoped
`tk_root` fixture in tests/conftest.py is now the only root, and UI tests
build on it.

### 2026-10-07 — CustomTkinter, not Qt, for the dark theme
The window was restyled dark, with SoundCloud orange for the one action to
press on each screen and blues for everything secondary. CustomTkinter was
chosen over PySide6 because it is still Tk underneath: `ui/task.py`'s
after()-polled queue, the one-root test fixture and every existing UI test
carry over unchanged. Qt would have meant a new threading bridge and
rewriting those tests for a look CTk gets most of the way to.

CTk has no table, so the Treeview stays ttk and is dressed by hand in
`theme.style_tree()`, including DPI scaling of row height and fonts that ttk
does not do itself. CTk also leaves a disabled button its full colour and only
greys the text, which reads as pressable; `widgets.Button` swaps the palette
with the state.

One trap, found only by rendering the real window: CTk's `mainloop()` hides a
root that was withdrawn before the loop started and never shows it again, so
`deiconify()` before `mainloop()` leaves the app running invisibly. `main()`
shows the window from inside the loop with `after(0, ...)`. Moving it back
looks like a harmless tidy-up and is not.

### 2026-10-07 — Neutral greys, orange as an accent only
The first dark theme - near-black navy, pure white text, solid orange buttons,
blue for everything secondary - was too dark and too high-contrast to look at
for long. It is now Material greys around Lead (#212121) with White Smoke
(#f5f5f5) text, and International Orange (#ff5722) is kept to small things:
the current-screen bar, the progress bar, the mark, and a wash behind the one
button to press. The blues are gone. The confidence row colours moved to
Material 300 tones on faint tints, still checked for 4.5:1 by
tests/test_add_tab.py. Putting the solid orange buttons back is the change
this entry is here to stop.

### 2026-10-07 — Find: a search that starts from what you keep
A requested screen, not a return of the tabs dropped above: it feeds the
same loop as Add Sets and ends in the same `apply_add`. Three sources were
chosen - your kept artists (plus accounts you follow), their circle (what
your top artists like, and accounts two or more of them follow), and
SoundCloud's related tracks. Festival and repost-channel searches were
offered and deliberately not built.

Seeds are artists from `artist_taste` with a like, ranked by likes then a
smoothed keep rate. An artist's account comes from your likes when you have
hearted something they posted; otherwise only an exact, case-blind username
match counts, and the biggest such account wins, because fan re-upload
channels borrow the name. Reads are one page each, so a 900-upload artist
costs one request. A first live run: 8 seeds, 46 requests, 21 s, 763 tracks
looked at, 30 queued.

Score weights are relative and untuned. They only order the queue, and every
row shows why it was found, so a bad order is visible rather than silent.

### 2026-10-07 — Dismissals get their own table
Removing a set from the Find queue asks why, and the answer is stored in
`dismissed_tracks`, not `tracks`: the set was never heard, so it must not
become a pending or skipped verdict or count against an artist. "Not into
this artist" is the one reason that teaches - its performers are left out of
later searches, and a b2b names everyone on it before you confirm. "Not now"
records nothing. A dismissed set is also dropped from pasted playlists in
Add Sets, because "never offered again" should not depend on the route.

### 2026-10-07 — Discovery is sorted by title
Chosen over artist-then-title. Every write to the playlist, from Add Sets or
Find, PUTs the whole list sorted A to Z ignoring case. Titles come from the
library; one it lacks is fetched; one SoundCloud no longer has sorts last.
Sorting must never drop an id - the PUT replaces the playlist outright.

### 2026-10-07 — The schema is applied on every open
`db.connect` used to run schema.sql only on a brand-new file, so a new table
could never reach the real discovery.db short of rebuilding it - which is
the one thing that must never happen. Every statement is IF NOT EXISTS, so
applying it on each open only adds what is missing. A view whose definition
changes will NOT update this way; that still needs a deliberate migration.

### 2026-10-07 — Find goes wider: depth, genres, and "new to you"
A 50-set search returned 19. Measured per source on a 25-artist search, two
thirds of everything read was songs under 20 minutes: of 527 tracks the top
artists had liked, 11 survived; of 120 uploads from accounts they follow, 2.
Only related tracks were rich (93 of 180). Then the per-artist cap trimmed
the kept-artist results, which come from a handful of names. With related
tracks off, about 19 is what that breadth yields - not a bug.

Three changes. Search depth (Quick / Normal / Deep) replaces fixed constants
and also raises the page size, which costs time, not requests. A "Your genres"
source searches the genres of your liked sets with length and age filtered by
SoundCloud itself, so nearly every result is a set; a genre needs two of your
sets behind it, because genre is free text. "Artist already in your library:
Any / Once at most / Never" filters by `artist_taste.heard`, and a b2b counts
as new when ANY performer is - the opposite of the block rule, on purpose,
since a b2b is how new names usually arrive.

Measured after: a "Never" search asking for 50 queued 50, by 50 different
artists, at Normal (76 requests, 34 s) with 302 more that qualified, and at
Deep (124 requests, 58 s). Genre results are weighted lowest, so at Normal
they mostly lose to related tracks; Deep is where they show.

### 2026-10-07 — Add Sets shows the playlist twice, and can be corrected
A playlist with one ALLDAT set posted by both ALLEYCVT and Crankdat and copied
by a fan account, Nova Dubz, went through with all three copies, and nothing
on screen allowed fixing it. The preview is now two panes: the playlist as
uploaded (red in library, blue duplicate, yellow known reposter, grey song or
turned down) beside what will be stored (coloured by confidence), with a star
on uploads from the performer's own account. Below the left pane: Reposter,
Official, Same set, Not the same, Skip. Each re-runs `discovery.refresh()`
from the playlist already fetched - no network - so the right side is always
the answer.

Account marks are saved immediately: whether Nova Dubz is a reposter is a fact
about Nova Dubz, true for every playlist. "Official" reuses the schema's
existing `self_uploader` kind, so no schema change was needed. Grouping stays
in the plan until Add, when the copies grouped away are recorded as dismissed
with reason `duplicate`; they were never stored, and the detector could not
see them, so nothing else would stop them coming back. "Not the same" on a
detected pair writes `duplicate_ignores`, the existing keep-both table.

Marking alone did not fix Nova Dubz: its titles parsed to `ALLDAT Live` and
`Levity Set`, wrong names that also stopped the copy matching the official
upload. See DOMAIN.md, "Repost titles with no separator" - 21 of 926 golden
cases changed, all fixes, updated in place rather than regenerated so the diff
is exactly those 21 lines.

`triage()` moved to services/triage.py and was split under the 50-line cap,
since it had to change; discovery.py re-exports its names.

### 2026-10-07 — Length is no evidence when the titles agree
The "Lost Lands 2026 Live Sets" playlist (88 tracks, a dozen reposters) let
five sets through twice. Two were the 10% length rule: rips of one Excision b2b
Space Laces set at 63/64/78 minutes, and Tynan's own 54-minute upload beside a
29-minute livestream rip. Length is now ignored when event tokens and every
standalone number match; numbers are checked because tokens drop them, and two
golden pairs (Gutter Music 7 vs 3, Bass Angels Part 1 vs 3) depended on the
length rule to stay apart. With the number check, no golden verdict changes.
The unit test that said identical titles 40 vs 80 minutes apart are NOT one set
was rewritten - it encoded the assumption this replaces.

The other three needed the user's knowledge. A typed artist now drives
duplicate detection (it never did - corrections only reached the stored row),
while `real_artists` stays the resolver's own answer. The correction dialog can
also save the original as an alias - "always read LEVELTRONICS as Subtronics,
Level Up" - since a duo's name recurs in every playlist. With those and one
"Same set", the playlist went from 63 sets to add to 58; the one same-artist
pair left is Excision's main set and his Detox set, which are different sets.

### 2026-10-30 — Published without the data
This repository is the public copy. `discovery.db` is gitignored, not
committed: the 2026-09-19 entry that tracks it describes the author's private
copy, where git was the backup. A public repository must never hold anyone's
listening history, so back the file up somewhere private instead.

Per-artist like counts and other figures from the author's listening history
were removed from these docs; aggregate measurements of the rules were kept.
The legacy app, the one-shot migrations and the frozen CSV and JSON exports
they consumed are not included, so references to them above are history.

The golden fixtures were rebuilt from `tests/golden/corpus.json`: about ninety
invented uploads chosen to walk every resolver branch and every duplicate
guard, in place of the 926 real tracks they used to freeze. They still catch
unintended change. They no longer prove the rules against a real library; the
measurements in DOMAIN.md are the record of that.

### 2026-10-30 — A first launch needs no README
A stranger opening the public copy met a console message about config.json,
then a sign-in box saying "approve access again", then four empty screens
that assumed they knew the loop. Now `run.bat` builds its own `.venv`; a
missing client id opens a connect window that walks through registering a
SoundCloud app; the common failures (port 8080 busy, secret rejected,
redirect URI wrong, offline) are worded as what to do, in `api/auth.py`,
where they are caught; and an empty library opens a four-step guide.

The guide opens from `main()`, never `App.__init__`: it is modal, and tests
build an App. Importing liked sets moved from `manage.py` alone onto the
Clean Up screen, because without kept artists Find has nothing to start from.

A new library is seeded once with common festival names and city and stage
words (`data/starter.py`) - only when it has no tracks and no lists of those
kinds. Not in schema.sql: that runs on every open, so it would put back an
entry someone had removed. Bare words that are also parts of artist names
("Ultra") are left out on purpose.
