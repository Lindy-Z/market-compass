-- =============================================================================
-- market-compass — SQLite schema v1
-- =============================================================================
-- Tables:
--   items        — one row per deduplicated news/data item
--   runs         — one row per daily/weekly/trigger execution
--   deliveries   — one row per push attempt (primary + fallback channels)
--   triggers     — event-trigger audit trail
--
-- Version: managed by PRAGMA user_version (set by src/storage/db.py).
-- DO NOT bump user_version in this file — see init_db() in db.py.
-- schema 版本由 db.py::init_db() 管理,本文件不写 user_version。
--
-- Journal mode (WAL): set per-connection in db.py, not here, because
-- executescript() opens its own transaction which conflicts with WAL setup.
-- WAL 日志模式在 db.py 按连接设置,不在本文件里 (executescript 会冲突)。
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- items: deduplicated news/data archive
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash    TEXT    NOT NULL UNIQUE,
        -- SHA-256 hex of title + body[:2048] + source + pub_date.
        -- Used for dedup on ingest.
    source          TEXT    NOT NULL,
        -- e.g. 'reuters-rss', 'finnhub', 'edgar-8k', 'gdelt'
    source_url      TEXT,
    title           TEXT    NOT NULL,
    body            TEXT,
    pub_ts          TEXT    NOT NULL,
        -- ISO-8601 UTC, e.g. '2026-04-23T12:34:56Z'
    fetched_ts      TEXT    NOT NULL,
        -- When ingestion recorded the item.
    processed_ts    TEXT,
        -- When reasoning completed on it; NULL until then.
    track           TEXT
        CHECK (track IS NULL OR track IN ('macro','na_fx','deals','other')),
    importance      INTEGER
        CHECK (importance IS NULL OR importance BETWEEN 0 AND 100),
    summary_en      TEXT,
    summary_zh      TEXT,
    causal_chain    TEXT,
        -- JSON blob: {event, first_order, asset_reaction, second_order,
        --            cross_market, confidence, caveats}
    meta            TEXT
        -- JSON blob for source-specific extras (feed-fields we don't flatten).
);

CREATE INDEX IF NOT EXISTS idx_items_pub_ts     ON items(pub_ts);
CREATE INDEX IF NOT EXISTS idx_items_track      ON items(track);
CREATE INDEX IF NOT EXISTS idx_items_importance ON items(importance);
CREATE INDEX IF NOT EXISTS idx_items_fetched_ts ON items(fetched_ts);

-- -----------------------------------------------------------------------------
-- runs: one row per pipeline execution
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type            TEXT    NOT NULL
        CHECK (run_type IN ('daily','weekly','trigger')),
    started_ts          TEXT    NOT NULL,
    finished_ts         TEXT,
    items_in            INTEGER NOT NULL DEFAULT 0,
    items_delivered     INTEGER NOT NULL DEFAULT 0,
    llm_cost_usd        REAL    NOT NULL DEFAULT 0.0,
        -- Rolling sum; compared against the $15/month soft ceiling.
    status              TEXT    NOT NULL
        CHECK (status IN ('running','ok','partial','failed')),
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started_ts ON runs(started_ts);
CREATE INDEX IF NOT EXISTS idx_runs_run_type   ON runs(run_type);

-- -----------------------------------------------------------------------------
-- deliveries: one row per push attempt (Telegram / Email, with fallback chain)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    channel     TEXT    NOT NULL
        CHECK (channel IN ('telegram','email')),
    ok          INTEGER NOT NULL
        CHECK (ok IN (0, 1)),
    error       TEXT,
    attempt     INTEGER NOT NULL
        CHECK (attempt >= 1),
    ts          TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deliveries_run_id ON deliveries(run_id);

-- -----------------------------------------------------------------------------
-- triggers: event-trigger audit trail
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS triggers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,
        -- 'deal_size' | 'cb_surprise' | 'geo' | 'market_move' (extensible)
    fired_ts    TEXT    NOT NULL,
    run_id      INTEGER REFERENCES runs(id) ON DELETE SET NULL,
        -- FK nullable: the trigger can fire before the deep-analysis run
        -- is recorded; the run is back-linked when it completes.
    details     TEXT
        -- JSON blob with trigger-specific context (deal size, CB surprise
        -- magnitude, market-move size, etc.)
);

CREATE INDEX IF NOT EXISTS idx_triggers_item_id  ON triggers(item_id);
CREATE INDEX IF NOT EXISTS idx_triggers_fired_ts ON triggers(fired_ts);
CREATE INDEX IF NOT EXISTS idx_triggers_kind     ON triggers(kind);
