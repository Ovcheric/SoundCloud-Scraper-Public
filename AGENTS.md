# AGENTS.md

SoundCloud Discovery finds long DJ sets and mixes inside SoundCloud playlists,
queues the genuinely-new ones into a playlist called **Discovery**, and records
which ones the user kept so it never queues the same thing twice.

**The loop.** Paste a playlist URL → filter to 20min+ tracks → drop anything
already in the database → resolve the real performer behind repost accounts →
drop duplicate recordings → add the rest to Discovery. The user listens *in
SoundCloud*, hearts what they like and deletes the rest. The next run diffs
Discovery membership and the likes list to learn the verdict.

**Find** is the same loop with a search in place of the pasted playlist: it
starts from the artists the user keeps, reads their uploads, likes, follows
and related tracks, and queues what survives the same checks for review.

The app never plays audio and never asks the user to rate anything. Every
verdict is inferred from what they did in SoundCloud.

---

## Rules

1. **400 lines per file, 50 per function. Hard caps.** At the cap, split — not
   "consider splitting". The previous version of this app was one 5,728-line
   file with a 164-method class; these numbers are why.
2. **One-way imports:** `ui → services → {core, data, api}`. `core/` imports
   nothing from `src/`. A cross-layer import is a bug even if it works.
3. **Never share a database connection across threads.** SQLite objects belong
   to the thread that created them. Pipelines run on worker threads, so a
   widget callback must open its own via `App.db()` and never hold one. Every
   test passed while the app failed on its first click, because tests call
   services directly on one thread — `tests/test_app.py` now covers the
   crossing.
4. **Error handling at boundaries only** — network, file, subprocess, user
   input. Pure functions in `core/` raise; callers handle. Do not wrap pure
   logic in speculative try/except.
5. **Never store a verdict.** Verdicts are views over recorded facts. See
   *Data invariants* below. This is the single most important rule here.
6. **Update the map in this file in the same change** that adds, moves or
   removes a module. A stale map is worse than none — it sends the next agent
   confidently to the wrong file.
7. **Every module opens with a 10-line header:** what it is for, its public
   names, what it must not import. Read headers to locate code; do not grep
   the tree.
8. **Scope.** Touch only what the task needs. No unrequested refactors,
   renames, features or abstractions. Flag them instead.
9. **Stop and ask** before changing the DB schema or any signature that
   crosses a layer boundary. Everything else: use judgement and proceed.
10. **No secrets in source.** Credentials come from the environment, falling
   back to `config.json`, which is gitignored.
11. **Naming carries no history.** The old app had tabs called `_opt1.._opt9`
    with no `_opt3`, long after the CLI menu they referenced was deleted.
    Name things for what they do now.

---

## Where things are

`[ ]` = not built yet. Build it there, then tick it here.

| path | holds | may import |
|---|---|---|
| `src/data/schema.sql` | tables + the verdict views | — |
| `src/data/db.py` | connection; applies schema on every open (additive only) | — |
| `src/data/tracks.py` | track queries, likes sync, snapshots | `core` |
| `src/data/settings.py` | builds core's config objects | `core` |
| `src/data/dismissals.py` | sets turned down in Find, and why | — |
| `src/data/starter.py` | festival and place lists a brand-new library starts with | — |
| `src/core/artists.py` | resolve performer from uploader+title | nothing |
| `src/core/duplicates.py` | same-recording detection | `core.models` |
| `src/core/models.py` | TrackRef, DuplicateMatch | nothing |
| `src/core/ranking.py` | Find's filters, scores, per-artist cap | nothing |
| `src/api/auth.py` | OAuth PKCE, token refresh, config.json | — |
| `src/api/client.py` | typed SoundCloud calls | `api.auth` |
| `src/services/discovery.py` | the Add pipeline: plan, refresh, apply | `core data api` |
| `src/services/triage.py` | artist resolution + dedupe for any batch | `core data` |
| `src/services/review.py` | Add-screen corrections: accounts, groups, skips | `data services` |
| `src/services/cleanup.py` | snapshot, diff, sync likes | `data api` |
| `src/services/library.py` | adopt sets liked outside Discovery | `data api` |
| `src/services/finder.py` | the Find pipeline: search, filter, queue, dismiss | `core data api` |
| `src/services/search_sources.py` | where Find looks — one function per source | `core api` |
| `src/ui/app.py` | window shell, screen registry, sign-in, crash log | `api data ui` |
| `src/ui/onboarding.py` | first launch: the connect window and the How it works guide | `ui` |
| `run.bat` | double-click launcher; builds `.venv` on first run, then starts the app | — |
| `requirements.txt` | the two libraries `run.bat` installs | — |
| `src/ui/task.py` | THE background-worker helper — reuse it | — |
| `src/ui/theme.py` | palette, fonts, Treeview styling, window icon — every colour | — |
| `src/ui/sidebar.py` | left rail: screen buttons, library counts | `ui.theme` |
| `src/ui/widgets.py` | buttons, cards, pills, stat tiles, table, status bar | `ui.theme` |
| `src/ui/dialogs.py` | themed modals: artist correction, dismissal, confirm, error | `ui.theme ui.widgets` |
| `src/ui/confidence.py` | row colours, legend, explanations — shared by Add and Find | `services ui` |
| `src/ui/tabs/add.py` | Add screen: entry, actions, corrections | `services ui` |
| `src/ui/tabs/add_review.py` | Add's two panes: as uploaded / as stored | `services ui` |
| `src/ui/tabs/find.py` | Find screen: the search queue | `services ui` |
| `src/ui/tabs/find_options.py` | Find's left panel: sources and filters | `services ui` |
| `src/ui/tabs/cleanup.py` | Clean Up screen, and importing liked sets | `services ui` |
| `src/ui/tabs/taste.py` | Taste screen — two queries, no modelling | `data ui` |
| `tests/test_artists.py` | golden + unit tests for the resolver | `core` |
| `tests/test_schema.py` | the derived views, on an in-memory db | — |
| `tests/test_duplicates.py` | golden + guard tests for the detector | `core` |
| `tests/test_data.py` | data layer, on an in-memory db | `data` |
| `tests/test_api.py` | api layer, offline via a stub session | `api` |
| `tests/test_services.py` | Add, Clean Up and import, fake api + real db | `services` |
| `tests/test_review.py` | Add-screen corrections, on the ALLDAT case | `services` |
| `tests/test_finder.py` | the Find pipeline, fake api + real db | `services` |
| `tests/test_ranking.py` | Find's filters, scores and picks | `core` |
| `tests/test_task.py` | the worker helper, headless | `ui` |
| `tests/test_app.py` | the startup crash handler, screen switching | `ui` |
| `tests/test_add_tab.py` | confidence colouring, row order, dark-theme readability | `ui services` |
| `tests/test_find_tab.py` | the Find queue: rows, removing, fixing | `ui services` |
| `tests/test_onboarding.py` | connect window, guide, importing liked sets | `ui data` |
| `tests/conftest.py` | THE one Tk root — never make a second | — |
| `docs/images/` | README screenshots — invented data only, never a real library | — |
| `tests/golden/corpus.json` | invented uploads the golden fixtures are built from | — |
| `tests/golden/duplicates.json` | frozen detector verdicts, incl. non-matches | — |
| `tests/golden/artists.json` | frozen resolver output — regenerate deliberately | — |
| `scripts/manage.py` | repost accounts, aliases, one-off artist fixes | `core data` |
| `scripts/gen_golden_artists.py` | regenerates `artists.json` from the corpus | `core` |
| `scripts/gen_golden_duplicates.py` | same, for the duplicate detector | `core` |

**Task → file.** Artist resolution wrong → `core/artists.py`; if the track has
a manual correction, fix the correction, not the resolver. Same set added
twice → `core/duplicates.py`. Verdict wrong → `data/schema.sql` views, never
Python. Auth/401 → `api/auth.py`. Playlist writes → `api/client.py`, and note
SoundCloud has no add/remove endpoint: the full track list is PUT every time,
so anything omitted is deleted. A colour or font → `ui/theme.py`; the
exceptions are row colours that carry meaning, kept beside what they render:
confidence grades in `ui/confidence.py`, the Add screen's "what happened"
highlights in `ui/tabs/add_review.py`. A reposter or official account the app
got wrong → the Add screen's buttons, which write `artist_settings`. A first
launch or sign-in that confuses people → `ui/onboarding.py` for the window,
`api/auth.py` for the messages it shows. Something hangs the window, or a 'same thread' error → `ui/task.py` and
`App.db()`; never call a service directly
from a widget callback.
A liked set missing from the library → `services/library.py`.
Find offering the wrong things → `services/search_sources.py` for where it
looks, `core/ranking.py` for the order and filters; a set it should never
have offered → the seen-check in `services/finder.py`.
A duplicate got through, or a good set was dropped → `services/discovery.py`,
where the keep-or-drop tiebreak lives. A wrong artist on a specific track is
fixed by correcting it, not by changing the rules — double-click it in Add.

---

## Data invariants

**`discovery.db` is the only live store.** It is NOT rebuildable — it holds
Discovery snapshot history, which exists only in the difference between two
observations and cannot be reconstructed from anything else. Losing it loses
listening history permanently.

It is created on first launch and is gitignored: it is the user's personal
listening history, and must never be committed to a public repository.

- **Verdicts are derived.** `track_verdicts` is the only thing that may decide
  liked/skipped/pending. Precedence: SoundCloud likes → a liked duplicate →
  the user's typed verdict → listened-with-no-like. Never add a `liked` column.
- **The likes sync is a mirror, not an append.** Set `in_likes_at` when the API
  reports a track, clear it when it does not. The old `mark_liked()` only ever
  added, so un-hearting never propagated. `record_liked()` is the one additive
  exception, for tracks read straight out of the likes list — passing a partial
  id set to `sync_likes` would clear every other like in the library.
- **The sync must never write `manual_verdict`.** That column exists so a sync
  cannot clobber a human decision. One column with two writers is the bug this
  schema was built to prevent.
- **Never rebuild `discovery.db`.** The snapshot history in it cannot be
  recreated. Schema changes are additive only (see `data/db.py`); anything
  that rewrites existing rows needs a backup first.
- **A typed artist correction always wins, and the resolver may never write
  it.** `real_artists` is resolver output and can be recomputed freely;
  `real_artists_manual` is human and authoritative. Read through the
  `track_artists` view, never the columns. 121 rows carry corrections the
  resolver cannot reproduce — it turns "Tomorrowland Friendship Mix with
  Subtronics" into "Tomorrowland". Never bulk re-resolve into `real_artists`
  without checking that view first.
- **Never ask the user a question mid-pipeline.** Every duplicate and artist
  decision resolves automatically and is reported afterwards. The modal
  dialogs are the reason the old app became tiring; `_better_of` in
  `services/discovery.py` exists so there is always an answer.
- **Adding takes a baseline snapshot** of the whole playlist, because a
  removal can only be detected relative to an earlier observation. Never make
  the user remember to do this.
- **Snapshot Discovery on every run**, before removing anything. Membership
  diffs are the *only* way the app can learn that a track was heard, because
  the user deletes tracks by hand in SoundCloud. Losing this history once
  already cost 783 rows of signal. **Record the run even when the playlist is
  empty** — empty means "everything queued has been worked through", and with
  membership rows alone that is indistinguishable from never having run.
- **Duplicates never count toward artist metrics** — `artist_taste` filters
  `duplicate_of IS NULL`, so a reposted-and-liked set counts once.
- **Every performer in a b2b is credited.** `artist_taste` splits
  `real_artists` on commas via `track_artist_names`. Never group by the joined
  string: it credits nobody, and can put an artist in the skip list on zero
  likes while they have a liked b2b set.
- **Never judge an artist name by its shape.** `999999999`, `HOL!`,
  `I Hate Models` and `chris_who?` are real acts. Confidence comes from what
  the parser did — no separator found, or a sentence-length result — never
  from vocabulary or punctuation.
- **Confidence grades the branch, and grades nothing else.** `CERTAIN`,
  `LIKELY`, `UNSURE`, `GUESS` say which path produced the name; the Add screen
  colours rows from them. Never drop, filter or store a track differently
  because of a grade. `CERTAIN` means the parser did not guess, **not** that
  the name was checked — its blind spot is an unregistered repost channel, and
  two single-track detectors for that were measured and rejected at ~10%
  precision. See docs/DOMAIN.md before wiring one to a colour.
- **Compare incoming tracks against each other, not just the database.** The
  old code compared each new track only against what was already stored, so
  three copies of one set arriving in one playlist were all added. The Add
  pipeline must call BOTH `find_candidates` (against stored tracks) and
  `find_pairs_within` (against the rest of the batch) before writing.
- **A dismissed set is a fact, never a verdict.** `dismissed_tracks` is its own
  table because those sets were never heard: they must not reach
  `track_verdicts`, `artist_taste` or the `tracks` table. They are read only to
  keep a set from being offered again - by Find *and* by Add Sets.
- **Find offers only what the user has never met.** Its seen-check is the
  library, plus dismissals, plus the whole SoundCloud likes list (sets hearted
  outside the loop are not in the library), plus the current queue. It never
  takes the "better copy" swap Add Sets allows - a new upload of a recording
  already stored is a recording already heard.
- **Account marks are facts, written at once; grouping waits for Add.**
  Marking an account a reposter or official (`artist_settings` kinds
  `repost_account` / `self_uploader`) holds for every playlist, so it is saved
  the moment it is made, and one account is never both. Grouping copies as one
  set belongs to the plan; on Add, the copies grouped away are recorded in
  `dismissed_tracks` (reason `duplicate`) - they were never stored, so nothing
  else would stop the next playlist offering them again.
- **Discovery is kept in title order, and sorting is a reorder, never a
  filter.** Every write PUTs the full list sorted A to Z; an id SoundCloud can
  no longer name goes last rather than missing, because anything omitted from
  the PUT is deleted from the playlist.

---

## Running

```
run.bat                                       # the app - double-click this
.venv/Scripts/python.exe -m src.ui.app        # the app, from a shell
.venv/Scripts/python.exe -m pytest tests/ -q  # tests          574 passing
```

`run.bat` uses `pythonw.exe`, so no console window sits behind the app. That
also means a startup crash has nowhere to print — `main()` catches it, writes
`crash.log` and shows a dialog. **Do not remove that handler**, or a failure to
start looks like nothing happening at all.

Auth: `config.json` holds the OAuth tokens and is gitignored; shape is in
`config.example.json`, and `SOUNDCLOUD_CLIENT_ID` / `SOUNDCLOUD_CLIENT_SECRET`
override it. Call `auth.load_fresh()` — it refreshes and saves but **never**
opens a browser, raising `ReauthRequired` instead. Only
`auth.authorize_interactively()` opens one, and only when called directly, so
no background job can block on a consent screen nobody is watching.

---

## Deeper docs

Read these only when the task needs them.

| file | when |
|---|---|
| `docs/DOMAIN.md` | changing artist resolution or duplicate rules |
| `docs/DECISIONS.md` | before "fixing" something that looks wrong on purpose |
| `README.md` | the user's workflow — what they actually do day to day |

Documenting code means module headers and these files. Not a wiki, not comments
restating the code.
