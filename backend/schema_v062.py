"""Repeatable migration for stages 02/08/09/26 and the action journal (0.6.2).

Adds columns and tables only; existing rows keep their IDs and values.
"""


def _columns(connection, table):
    return {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}


def _add(connection, table, column, ddl):
    if column not in _columns(connection, table):
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')


def migrate(connection):
    # Stage 02/26: session state of a profile, never its content.
    _add(connection, 'profiles', 'session_state', "TEXT NOT NULL DEFAULT 'unknown'")
    _add(connection, 'profiles', 'session_checked_at', 'TEXT')
    _add(connection, 'profiles', 'session_imported_at', 'TEXT')
    _add(connection, 'profiles', 'last_used_at', 'TEXT')
    # Stage 26/02: optional CSS marker that is present only for a signed-in user.
    _add(connection, 'jobs', 'login_selector', "TEXT NOT NULL DEFAULT ''")
    # Stage 09: one-off runs, policies, working window and who changed the schedule.
    _add(connection, 'jobs', 'run_at', 'TEXT')
    _add(connection, 'jobs', 'missed_policy', "TEXT NOT NULL DEFAULT 'one'")
    _add(connection, 'jobs', 'overlap_policy', "TEXT NOT NULL DEFAULT 'skip'")
    _add(connection, 'jobs', 'window_start', 'TEXT')
    _add(connection, 'jobs', 'window_end', 'TEXT')
    _add(connection, 'jobs', 'schedule_updated_at', 'TEXT')
    _add(connection, 'jobs', 'schedule_updated_by', 'TEXT')
    # Stage 08: run-level error code and per-URL checkpoints.
    _add(connection, 'runs', 'error_code', 'TEXT')
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS run_pages (
        id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), idx INTEGER NOT NULL, url TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ok','empty','error','user_action')), found INTEGER NOT NULL DEFAULT 0,
        error_code TEXT, message TEXT, screenshot TEXT, login INTEGER, started_at TEXT, finished_at TEXT,
        UNIQUE(run_id, idx));
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        target_type TEXT, target_id INTEGER, details TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX IF NOT EXISTS audit_at ON audit(at);
    CREATE INDEX IF NOT EXISTS logs_created ON logs(created_at);
    ''')
    # The 'once' kind needs the CHECK-free text column only; validate in the API.
