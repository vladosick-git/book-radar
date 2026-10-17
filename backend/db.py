import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('RADAR_DATA_DIR', str(ROOT / 'data')))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / 'radar.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    # SQLite lower()/LIKE fold only ASCII; journal and audit search need Cyrillic case folding.
    db.create_function('casefold', 1, lambda value: value.casefold() if isinstance(value, str) else value, deterministic=True)
    return db


def init():
    """Create or update the schema. Stage 18: one process at a time (file lock), and a verified backup of the
    database is made before the first start of a new version on existing data."""
    from . import upgrade
    with upgrade.migration_lock(DATA):
        upgrade.before_migrations(DATA)
        _migrate()
        upgrade.after_migrations(DATA)
    (DATA / 'profiles').mkdir(exist_ok=True)
    (DATA / 'evidence').mkdir(exist_ok=True)


def _migrate():
    with connect() as db:
        # WAL lets the API read while a separate worker process writes.
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, profile_id INTEGER NOT NULL REFERENCES profiles(id),
            urls TEXT NOT NULL, selector TEXT NOT NULL, wait_seconds INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL, last_run TEXT,
            last_count INTEGER NOT NULL DEFAULT 0, last_error TEXT);
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
            profile_id INTEGER NOT NULL REFERENCES profiles(id), job_id INTEGER NOT NULL REFERENCES jobs(id),
            title TEXT NOT NULL, text TEXT NOT NULL, destination TEXT NOT NULL, image_url TEXT NOT NULL,
            source_url TEXT NOT NULL, frame_url TEXT NOT NULL, screenshot TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, sightings INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY, job_id INTEGER, level TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL CHECK(kind IN ('email','site')),
            registration_url TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id),
            service_id INTEGER NOT NULL REFERENCES services(id),
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','in_progress','confirmed')),
            identifier TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, opened_at TEXT, confirmed_at TEXT,
            UNIQUE(profile_id,service_id));
        ''')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS mailboxes (
            id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
            region TEXT NOT NULL, zoid TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
            password_cipher BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','creating','created','failed','unknown')),
            remote_account_id TEXT, error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        ''')
        from .mail_registration import migrate
        migrate(db)
        from .onboarding import migrate as migrate_onboarding
        migrate_onboarding(db)
        job_columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
        if 'headed' not in job_columns:
            db.execute('ALTER TABLE jobs ADD COLUMN headed INTEGER NOT NULL DEFAULT 0')
        if 'updated_at' not in job_columns:
            db.execute('ALTER TABLE jobs ADD COLUMN updated_at TEXT')
        log_columns = {row['name'] for row in db.execute('PRAGMA table_info(logs)')}
        if 'source' not in log_columns:
            db.execute("ALTER TABLE logs ADD COLUMN source TEXT NOT NULL DEFAULT ''")
        if 'profile_id' not in log_columns:
            db.execute('ALTER TABLE logs ADD COLUMN profile_id INTEGER')
        # Repeatable backfill: only rows that never had a context get one; messages stay untouched.
        db.execute("UPDATE logs SET source='job' WHERE source='' AND job_id IS NOT NULL")
        db.execute("UPDATE logs SET source='mail' WHERE source='' AND (message LIKE '%Zoho%' OR message LIKE '%ящик%')")
        db.execute("UPDATE logs SET source='system' WHERE source=''")
        finding_columns = {row['name'] for row in db.execute('PRAGMA table_info(findings)')}
        from .finding_quality import refresh as refresh_quality
        refresh_quality(db)
        if 'first_screenshot' not in finding_columns:
            db.execute("ALTER TABLE findings ADD COLUMN first_screenshot TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE findings SET first_screenshot=screenshot WHERE first_screenshot=''")
        if not db.execute("SELECT 1 FROM settings WHERE key='fingerprint_version' AND value='2'").fetchone():
            migrate_fingerprints(db)
        for column, ddl in [('schedule_kind', "TEXT NOT NULL DEFAULT 'none'"), ('interval_minutes', 'INTEGER'), ('daily_time', 'TEXT'),
                            ('timezone', "TEXT NOT NULL DEFAULT 'UTC'"), ('paused', 'INTEGER NOT NULL DEFAULT 0'), ('schedule_from', 'TEXT')]:
            if column not in job_columns:
                db.execute(f'ALTER TABLE jobs ADD COLUMN {column} {ddl}')
        if 'run_id' not in {row['name'] for row in db.execute('PRAGMA table_info(logs)')}:
            db.execute('ALTER TABLE logs ADD COLUMN run_id INTEGER')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
            trigger TEXT NOT NULL CHECK(trigger IN ('manual','schedule','retry')),
            status TEXT NOT NULL CHECK(status IN ('queued','running','done','partial','error','failed','waiting_user','stopped','interrupted','cancelled','skipped')),
            attempt INTEGER NOT NULL DEFAULT 1, scheduled_for TEXT, not_before TEXT NOT NULL,
            queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, heartbeat_at TEXT,
            worker_id TEXT, resume_from INTEGER NOT NULL DEFAULT 0, pages_done INTEGER NOT NULL DEFAULT 0,
            pages_total INTEGER NOT NULL DEFAULT 0, found INTEGER NOT NULL DEFAULT 0, error TEXT,
            stop_requested INTEGER NOT NULL DEFAULT 0, retry_of INTEGER REFERENCES runs(id));
        CREATE UNIQUE INDEX IF NOT EXISTS runs_occurrence ON runs(job_id, scheduled_for) WHERE scheduled_for IS NOT NULL AND trigger='schedule';
        CREATE INDEX IF NOT EXISTS runs_status ON runs(status, not_before);
        CREATE TABLE IF NOT EXISTS workers (
            id TEXT PRIMARY KEY, pid INTEGER, host TEXT, mode TEXT NOT NULL, version TEXT NOT NULL,
            started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, stopped_at TEXT, max_browsers INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS profile_leases (
            profile_id INTEGER PRIMARY KEY REFERENCES profiles(id), owner TEXT NOT NULL, purpose TEXT NOT NULL,
            acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        ''')
        from .schema_v062 import migrate as migrate_v062
        migrate_v062(db)
        from .schema_v070 import migrate as migrate_v070
        migrate_v070(db)
        from .purchases import migrate as migrate_purchases
        migrate_purchases(db)
        # A job marked running/queued without an active run belongs to a process that is gone.
        db.execute("""UPDATE jobs SET status='interrupted', last_error='Программа завершилась во время сбора. Можно запустить повторно.'
            WHERE status IN ('running','queued') AND NOT EXISTS (
                SELECT 1 FROM runs WHERE runs.job_id=jobs.id AND runs.status IN ('queued','running'))""")


def migrate_fingerprints(connection):
    from .identity import fingerprint
    groups = {}
    for row in connection.execute('SELECT * FROM findings ORDER BY first_seen,id'):
        row = dict(row)
        digest = fingerprint(row['profile_id'], row['source_url'], row['frame_url'], row)
        groups.setdefault(digest, []).append(row)
    # Remove duplicate rows before updating keys to avoid collisions with legacy hashes.
    for digest, group in groups.items():
        keeper = group[0]
        latest = max(group, key=lambda row: (row['last_seen'], row['id']))
        for duplicate in group[1:]:
            connection.execute('DELETE FROM findings WHERE id=?', (duplicate['id'],))
        connection.execute('''UPDATE findings SET fingerprint=?,sightings=?,first_seen=?,last_seen=?,
            first_screenshot=?,screenshot=?,job_id=?,destination=?,image_url=?,frame_url=? WHERE id=?''',
            (digest, sum(row['sightings'] for row in group), min(row['first_seen'] for row in group),
             latest['last_seen'], keeper['first_screenshot'], latest['screenshot'], latest['job_id'],
             latest['destination'], latest['image_url'], latest['frame_url'], keeper['id']))
    connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('fingerprint_version','2')")


def rows(sql, params=()):
    with connect() as db:
        return [dict(row) for row in db.execute(sql, params)]


def execute(sql, params=()):
    with connect() as db:
        cursor = db.execute(sql, params)
        return cursor.lastrowid


LOG_SOURCES = ('job', 'mail', 'profile', 'system')


def log(job_id, level, message, source=None, profile_id=None, run_id=None, error_code=None):
    source = source if source in LOG_SOURCES else 'job' if job_id else 'system'
    execute('INSERT INTO logs(job_id,level,message,created_at,source,profile_id,run_id,error_code) VALUES (?,?,?,?,?,?,?,?)',
            (job_id, level, message, now(), source, profile_id, run_id, error_code))
