"""Repeatable migration for 0.7.0: stages 14 (ads/observations), 13 (field provenance, OCR), 17 (journal codes,
notifications) and 18 (schema version). Adds columns, tables and indexes only; existing IDs and values are kept.
"""
from .schema_v062 import _add

SCHEMA_VERSION = 7


def migrate(connection):
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS ads (
        id INTEGER PRIMARY KEY, ad_key TEXT NOT NULL UNIQUE, family_key TEXT NOT NULL,
        variant_of INTEGER REFERENCES ads(id), variant_no INTEGER NOT NULL DEFAULT 1, variant_reason TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, sightings INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS observations (
        id INTEGER PRIMARY KEY, finding_id INTEGER NOT NULL REFERENCES findings(id), ad_id INTEGER REFERENCES ads(id),
        profile_id INTEGER NOT NULL, job_id INTEGER, run_id INTEGER, source_url TEXT NOT NULL, seen_at TEXT NOT NULL,
        screenshot TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 1, legacy TEXT,
        UNIQUE(finding_id, legacy));
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, channel TEXT NOT NULL, event TEXT NOT NULL, job_id INTEGER, run_id INTEGER,
        dedupe_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL CHECK(status IN ('sent','failed','suppressed','skipped')),
        detail TEXT NOT NULL DEFAULT '');
    ''')
    # Stage 14: a placement points to its creative; kind and host make server-side filters cheap.
    _add(connection, 'findings', 'ad_id', 'INTEGER REFERENCES ads(id)')
    _add(connection, 'findings', 'kind', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'source_host', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'search_text', "TEXT NOT NULL DEFAULT ''")
    # Stage 13: DOM text stays in `text` (it is part of the fingerprint); the cleaned copy and provenance are separate.
    _add(connection, 'findings', 'clean_text', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'field_sources', "TEXT NOT NULL DEFAULT '{}'")
    _add(connection, 'findings', 'image_source', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'crop', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'ocr_text', "TEXT NOT NULL DEFAULT ''")
    _add(connection, 'findings', 'ocr_status', "TEXT NOT NULL DEFAULT ''")
    # Stage 17: machine-readable code next to a journal message.
    _add(connection, 'logs', 'error_code', 'TEXT')
    connection.executescript('''
    CREATE INDEX IF NOT EXISTS ads_family ON ads(family_key, variant_no);
    CREATE INDEX IF NOT EXISTS observations_finding ON observations(finding_id, seen_at);
    CREATE INDEX IF NOT EXISTS observations_seen ON observations(seen_at);
    CREATE INDEX IF NOT EXISTS observations_ad ON observations(ad_id, seen_at);
    CREATE INDEX IF NOT EXISTS findings_last ON findings(last_seen);
    CREATE INDEX IF NOT EXISTS findings_profile ON findings(profile_id, last_seen);
    CREATE INDEX IF NOT EXISTS findings_ad ON findings(ad_id);
    CREATE INDEX IF NOT EXISTS findings_host ON findings(source_host);
    CREATE INDEX IF NOT EXISTS logs_job ON logs(job_id, id);
    CREATE INDEX IF NOT EXISTS logs_profile ON logs(profile_id, id);
    CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id, id);
    CREATE INDEX IF NOT EXISTS notifications_job ON notifications(job_id, id);
    ''')
    from .catalog import migrate as link_findings
    link_findings(connection)
    from .schema_v071 import migrate as migrate_v071  # stage 11: block type and adapter
    migrate_v071(connection)
    connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))
