PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_entries (
    namespace TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (namespace, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_cache_expiry
    ON cache_entries (expires_at);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    content_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    media_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    source_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_provider_time
    ON raw_snapshots (provider, fetched_at);
