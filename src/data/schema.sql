-- SoundCloud Discovery — schema v1
--
-- PRINCIPLE: store observations, derive interpretations.
--
-- The old CSV had a single `liked` column written by two different things
-- (the SoundCloud sync, which only ever wrote "yes", and a hand-edit dialog,
-- which wrote anything). They silently overwrote each other and no row
-- recorded which had won. Every verdict here is a VIEW over recorded facts,
-- so the same mistake cannot be made twice.

PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- tracks — one row per SoundCloud track we have ever seen
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tracks (
    track_id        TEXT    PRIMARY KEY,           -- SoundCloud's id, as text
    title           TEXT    NOT NULL,
    uploader        TEXT    NOT NULL DEFAULT '',   -- account that posted it (may be a repost account)
    duration_ms     INTEGER NOT NULL DEFAULT 0,

    -- Two writers, so two columns. real_artists is whatever the resolver
    -- produced and may be recomputed freely. real_artists_manual is a human
    -- correction and ALWAYS wins - the resolver must never write it. Read
    -- through the track_artists view, never these columns directly.
    --
    -- The resolver cannot reproduce the manual values: "Tomorrowland
    -- Friendship Mix with Subtronics" resolves to "Tomorrowland", and
    -- "Distinct Motive | OUT-OF-STUDIO Live" to an emoji. 121 rows differ.
    real_artists        TEXT NOT NULL DEFAULT '',
    real_artists_manual TEXT,

    -- Era signal. Many titles carry no year ("Fibonacci Tour | MGM Boston"),
    -- so upload date is the only way to place them in time. The old app
    -- discarded this; it is free in the API response.
    uploaded_at     TEXT,

    first_seen_at   TEXT    NOT NULL,              -- when WE first recorded it
    source_playlist TEXT,                          -- playlist URL it arrived from, if known

    -- Same *recording* as another track (a repost of one event). NOT the same
    -- as "same artist, similar set" — that is a different relation and is
    -- deliberately not modelled. See docs/DOMAIN.md.
    duplicate_of    TEXT    REFERENCES tracks(track_id) ON DELETE SET NULL,

    -- ── Recorded facts about the verdict. Never combined at write time. ──

    -- Mirrors SoundCloud likes. Set on every sync when present, CLEARED when
    -- absent, so un-hearting propagates. The old mark_liked() only ever added.
    in_likes_at     TEXT,

    -- Concluded heard: it was in a Discovery snapshot and later was not.
    listened_at     TEXT,

    -- A judgement typed by the user. The sync must NEVER write this column,
    -- which is what keeps a sync from clobbering a hand-made decision.
    manual_verdict  TEXT    CHECK (manual_verdict IS NULL
                                   OR manual_verdict IN ('liked','skipped'))
);

CREATE INDEX IF NOT EXISTS idx_tracks_real_artists ON tracks(real_artists);
CREATE INDEX IF NOT EXISTS idx_tracks_duplicate_of ON tracks(duplicate_of);
CREATE INDEX IF NOT EXISTS idx_tracks_in_likes     ON tracks(in_likes_at);


-- ---------------------------------------------------------------------------
-- discovery_runs / discovery_snapshots — what was in Discovery, per run
-- ---------------------------------------------------------------------------
-- The user removes tracks by hand in SoundCloud, so the app can only learn
-- "listened" by diffing membership between runs. Without this history, a blank
-- verdict is permanently ambiguous — which is exactly how 783 rows of signal
-- were nearly lost. Cheap to keep: ~900 rows per run.
--
-- Runs are recorded separately from membership because AN EMPTY PLAYLIST IS
-- MEANINGFUL: it is the state that says everything queued has been worked
-- through. With membership alone, an empty snapshot writes no rows and is
-- indistinguishable from never having run.
CREATE TABLE IF NOT EXISTS discovery_runs (
    snapshot_at TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS discovery_snapshots (
    snapshot_at TEXT NOT NULL REFERENCES discovery_runs(snapshot_at),
    track_id    TEXT NOT NULL,
    PRIMARY KEY (snapshot_at, track_id)
);


-- ---------------------------------------------------------------------------
-- Artist resolution inputs (was artist_config.json)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artist_aliases (
    alias     TEXT PRIMARY KEY,    -- "TapeB"
    canonical TEXT NOT NULL        -- "Tape B"
);

CREATE TABLE IF NOT EXISTS artist_settings (
    kind  TEXT NOT NULL CHECK (kind IN ('repost_account','self_uploader',
                                        'known_event','location_qualifier')),
    value TEXT NOT NULL,
    PRIMARY KEY (kind, value)
);


-- ---------------------------------------------------------------------------
-- duplicate_ignores — pairs the user explicitly said to keep both of
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS duplicate_ignores (
    track_id_a TEXT NOT NULL,
    track_id_b TEXT NOT NULL,
    PRIMARY KEY (track_id_a, track_id_b),
    CHECK (track_id_a < track_id_b)   -- canonical order: a pair is stored once
);


-- ---------------------------------------------------------------------------
-- dismissed_tracks — sets the user turned down from the Find queue, and why
-- ---------------------------------------------------------------------------
-- A separate table, NOT rows in `tracks`: a dismissed set was never heard, so
-- it must not reach track_verdicts as pending or skipped, nor count toward
-- artist_taste. It is a recorded fact - "turned down on this date, for this
-- reason" - and is only ever read to keep the set from being offered again.
--
-- `reason` is a code from services/finder.py (style, artist, heard, quality,
-- not_a_set, other), or 'duplicate' for a copy grouped away on the Add
-- screen. No CHECK, so a reason can be added without a migration.
-- reason = 'artist' is the one that teaches: those performers are left out
-- of every later search.
CREATE TABLE IF NOT EXISTS dismissed_tracks (
    track_id     TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL DEFAULT '',  -- performer(s) as shown, "A, B" for a b2b
    reason       TEXT NOT NULL,
    note         TEXT,                      -- optional free text
    dismissed_at TEXT NOT NULL
);


-- ---------------------------------------------------------------------------
-- track_verdicts — THE derived verdict. Nothing else may compute this.
-- ---------------------------------------------------------------------------
-- Precedence, highest first:
--   1. in SoundCloud likes            — the most recent explicit user action
--   2. a duplicate of it is liked     — the old code physically copied the
--                                       like onto the other row; here it is
--                                       simply a join
--   3. a typed manual verdict         — for what the sync cannot observe
--   4. known listened, no like        — skipped
--   5. otherwise                      — pending
CREATE VIEW IF NOT EXISTS track_verdicts AS
SELECT
    t.track_id,
    CASE
        WHEN t.in_likes_at IS NOT NULL                      THEN 'liked'
        WHEN EXISTS (SELECT 1 FROM tracks d
                      WHERE d.duplicate_of = t.track_id
                        AND d.in_likes_at IS NOT NULL)      THEN 'liked'
        WHEN t.manual_verdict IS NOT NULL                   THEN t.manual_verdict
        WHEN t.listened_at IS NOT NULL                      THEN 'skipped'
        ELSE 'pending'
    END AS verdict,
    CASE
        WHEN t.in_likes_at IS NOT NULL                      THEN 'soundcloud'
        WHEN EXISTS (SELECT 1 FROM tracks d
                      WHERE d.duplicate_of = t.track_id
                        AND d.in_likes_at IS NOT NULL)      THEN 'duplicate'
        WHEN t.manual_verdict IS NOT NULL                   THEN 'manual'
        WHEN t.listened_at IS NOT NULL                      THEN 'inferred'
        ELSE 'none'
    END AS verdict_source
FROM tracks t;


-- ---------------------------------------------------------------------------
-- track_artists — THE performer for a track. A typed correction always wins.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS track_artists AS
SELECT
    t.track_id,
    COALESCE(NULLIF(t.real_artists_manual, ''), t.real_artists) AS artist,
    CASE WHEN COALESCE(t.real_artists_manual, '') <> ''
         THEN 'manual' ELSE 'resolved' END                      AS artist_source
FROM tracks t;


-- ---------------------------------------------------------------------------
-- track_artist_names — one row per PERFORMER per track
-- ---------------------------------------------------------------------------
-- A b2b set is stored as "Wooli, Crankdat". Grouping on that whole string
-- credits neither of them: each performer's counts come out short, and an
-- artist with a liked b2b set can land in the stop-queueing list on zero likes.
-- Splitting is the difference between a metric and a liability.
CREATE VIEW IF NOT EXISTS track_artist_names AS
WITH RECURSIVE split(track_id, name, rest) AS (
    SELECT track_id, '', artist || ',' FROM track_artists WHERE TRIM(artist) <> ''
    UNION ALL
    SELECT track_id,
           TRIM(SUBSTR(rest, 1, INSTR(rest, ',') - 1)),
           SUBSTR(rest, INSTR(rest, ',') + 1)
      FROM split
     WHERE rest <> ''
)
SELECT DISTINCT track_id, name FROM split WHERE name <> '';


-- ---------------------------------------------------------------------------
-- artist_taste — the whole metrics feature. Duplicates excluded so a
-- reposted-and-liked set cannot count twice for its artist.
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS artist_taste AS
SELECT
    n.name                                                 AS artist,
    COUNT(*)                                               AS heard,
    SUM(CASE WHEN v.verdict = 'liked'   THEN 1 ELSE 0 END) AS liked,
    SUM(CASE WHEN v.verdict = 'pending' THEN 1 ELSE 0 END) AS pending
FROM tracks t
JOIN track_verdicts    v USING (track_id)
JOIN track_artist_names n USING (track_id)
WHERE t.duplicate_of IS NULL
GROUP BY n.name;
