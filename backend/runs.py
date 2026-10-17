"""Persistent run queue, profile leases and schedules. Shared by the API and any worker process.

Everything lives in SQLite so the queue survives restarts of both processes. State changes that
must not race between processes run inside BEGIN IMMEDIATE transactions.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, db

ACTIVE = ('queued', 'running')
FINISHED = ('done', 'partial', 'error', 'failed', 'waiting_user', 'stopped', 'interrupted', 'cancelled', 'skipped')


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse(value):
    return datetime.fromisoformat(value) if value else None


@contextmanager
def tx():
    connection = db.connect()
    connection.isolation_level = None
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.execute('COMMIT')
    except BaseException:
        connection.execute('ROLLBACK')
        raise
    finally:
        connection.close()


# ---------- queue ----------

def active_run(job_id):
    rows = db.rows("SELECT * FROM runs WHERE job_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1", (job_id,))
    return rows[0] if rows else None


def _insert_run(connection, job, trigger, *, status='queued', scheduled_for=None, attempt=1, resume_from=0, delay=0, retry_of=None, error=None):
    timestamp = utcnow()
    total = len(json.loads(job['urls']))
    cursor = connection.execute('''INSERT OR IGNORE INTO runs(job_id,trigger,status,attempt,scheduled_for,not_before,queued_at,
        resume_from,pages_done,pages_total,retry_of,error,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (job['id'], trigger, status, attempt, scheduled_for, iso(timestamp + timedelta(seconds=delay)), iso(timestamp),
         resume_from, resume_from, total, retry_of, error, iso(timestamp) if status not in ACTIVE else None))
    if cursor.rowcount and status == 'queued':
        connection.execute("UPDATE jobs SET status='queued' WHERE id=? AND status!='running'", (job['id'],))
    return cursor.lastrowid if cursor.rowcount else None


def enqueue(job_id, trigger='manual'):
    """Queue a manual run. Returns (run_id, None) or (None, reason)."""
    with tx() as connection:
        job = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if job is None:
            return None, 'missing'
        from .finding_quality import is_test_url
        if not config.ALLOW_TEST_COLLECTION and any(is_test_url(url) for url in json.loads(job['urls'])):
            return None, 'test_disabled'
        if connection.execute("SELECT 1 FROM runs WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone():
            return None, 'active'
        holder = connection.execute('SELECT * FROM profile_leases WHERE profile_id=? AND expires_at>?', (job['profile_id'], iso(utcnow()))).fetchone()
        if holder and holder['purpose'] == 'window':
            return None, 'window'
        return _insert_run(connection, dict(job), trigger), None


def request_stop(job_id):
    """Queued runs are cancelled at once; a running run is asked to stop by its worker."""
    with tx() as connection:
        run = connection.execute("SELECT * FROM runs WHERE job_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
        if run is None:
            return 'idle'
        if run['status'] == 'queued':
            connection.execute("UPDATE runs SET status='cancelled',finished_at=?,error='Отменено до начала.' WHERE id=?", (iso(utcnow()), run['id']))
            connection.execute("UPDATE jobs SET status='stopped' WHERE id=?", (job_id,))
            return 'cancelled'
        connection.execute('UPDATE runs SET stop_requested=1 WHERE id=?', (run['id'],))
        return 'stopping'


def claim(worker_id, max_browsers, busy_profiles=()):
    """Atomically take the next due queued run whose profile is free. Returns the run row or None."""
    now = iso(utcnow())
    with tx() as connection:
        connection.execute('DELETE FROM profile_leases WHERE expires_at<=?', (now,))
        running = connection.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
        if running >= max_browsers:
            return None
        for run in connection.execute("SELECT runs.*,jobs.profile_id FROM runs JOIN jobs ON jobs.id=runs.job_id WHERE runs.status='queued' AND runs.not_before<=? ORDER BY runs.not_before,runs.id", (now,)).fetchall():
            if run['profile_id'] in busy_profiles or connection.execute('SELECT 1 FROM profile_leases WHERE profile_id=?', (run['profile_id'],)).fetchone():
                continue
            if connection.execute("SELECT 1 FROM runs WHERE job_id=? AND status='running'", (run['job_id'],)).fetchone():
                continue  # overlap policy 'queue': runs of one job execute one after another
            expires = iso(utcnow() + timedelta(seconds=config.STALE_SECONDS))
            connection.execute('INSERT INTO profile_leases(profile_id,owner,purpose,acquired_at,expires_at) VALUES (?,?,?,?,?)',
                               (run['profile_id'], worker_id, 'collect', now, expires))
            connection.execute("UPDATE runs SET status='running',worker_id=?,started_at=?,heartbeat_at=? WHERE id=?", (worker_id, now, now, run['id']))
            connection.execute("UPDATE jobs SET status='running',last_run=?,last_error=NULL,last_count=0 WHERE id=?", (now, run['job_id']))
            connection.execute('UPDATE profiles SET last_used_at=? WHERE id=?', (now, run['profile_id']))
            return dict(connection.execute('SELECT * FROM runs WHERE id=?', (run['id'],)).fetchone())
    return None


def heartbeat(worker_id, run_ids):
    now, expires = iso(utcnow()), iso(utcnow() + timedelta(seconds=config.STALE_SECONDS))
    with tx() as connection:
        connection.execute('UPDATE workers SET heartbeat_at=? WHERE id=?', (now, worker_id))
        for run_id in run_ids:
            connection.execute("UPDATE runs SET heartbeat_at=? WHERE id=? AND status='running'", (now, run_id))
        connection.execute('UPDATE profile_leases SET expires_at=? WHERE owner=?', (expires, worker_id))


def progress(run_id, pages_done, found):
    db.execute('UPDATE runs SET pages_done=?,found=? WHERE id=?', (pages_done, found, run_id))


def record_page(run_id, page):
    """Per-URL checkpoint (stage 08): the outcome of each processed page is kept with its code."""
    db.execute('''INSERT OR REPLACE INTO run_pages(run_id,idx,url,status,found,error_code,message,screenshot,login,started_at,finished_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (run_id, page['index'], page['url'], page['status'], page['found'], page['error_code'],
        page['message'], page['screenshot'], None if page['login'] is None else int(page['login']), page.get('started_at'), page.get('finished_at')))


def run_pages(run_id):
    return db.rows('SELECT idx,url,status,found,error_code,message,screenshot,login,started_at,finished_at FROM run_pages WHERE run_id=? ORDER BY idx', (run_id,))


def stop_requested(run_id):
    rows = db.rows('SELECT stop_requested FROM runs WHERE id=?', (run_id,))
    return bool(rows and rows[0]['stop_requested'])


def finish(run, status, error=None, *, worker_id=None, retry=False, delay=None, error_code=None):
    """Close a run, release its lease and, for crash-like outcomes, queue a bounded resumable retry."""
    with tx() as connection:
        current = connection.execute('SELECT * FROM runs WHERE id=?', (run['id'],)).fetchone()
        if current is None or current['status'] != 'running':
            return None
        connection.execute('UPDATE runs SET status=?,finished_at=?,error=?,error_code=? WHERE id=?', (status, iso(utcnow()), error, error_code, run['id']))
        job = connection.execute('SELECT * FROM jobs WHERE id=?', (current['job_id'],)).fetchone()
        connection.execute('DELETE FROM profile_leases WHERE profile_id=? AND owner=?', (job['profile_id'], worker_id or current['worker_id']))
        retry_id = None
        if retry and current['attempt'] <= len(config.RETRY_DELAYS):
            delay = config.RETRY_DELAYS[current['attempt'] - 1] if delay is None else delay
            retry_id = _insert_run(connection, dict(job), 'retry', attempt=current['attempt'] + 1, resume_from=current['pages_done'],
                                   delay=delay, retry_of=current['id'])
        return retry_id


def reconcile(stale_seconds=None):
    """Runs whose worker stopped sending heartbeats become interrupted and resume from the next page."""
    stale = iso(utcnow() - timedelta(seconds=stale_seconds or config.STALE_SECONDS))
    lost = db.rows("SELECT * FROM runs WHERE status='running' AND COALESCE(heartbeat_at, started_at)<?", (stale,))
    for run in lost:
        message = f'Worker перестал отвечать во время запуска #{run["id"]}. Готовые страницы сохранены: {run["pages_done"]} из {run["pages_total"]}.'
        retry_id = finish(run, 'interrupted', message, retry=True, error_code='WORKER_LOST')
        db.execute("UPDATE jobs SET status=?,last_error=? WHERE id=?", ('queued' if retry_id else 'interrupted', message, run['job_id']))
        db.log(run['job_id'], 'warning', message + (f' Повтор #{retry_id} продолжит со страницы {run["pages_done"] + 1}.' if retry_id else ' Лимит повторов исчерпан.'), run_id=run['id'],
               error_code='WORKER_LOST')
        if not retry_id:
            from . import notify
            notify.on_run_finished(run['id'], False)
    return len(lost)


# ---------- profile leases (windows and workers share one lock per profile) ----------

def acquire_lease(profile_id, owner, purpose, seconds=24 * 3600):
    now = utcnow()
    with tx() as connection:
        connection.execute('DELETE FROM profile_leases WHERE expires_at<=?', (iso(now),))
        if connection.execute('SELECT 1 FROM profile_leases WHERE profile_id=?', (profile_id,)).fetchone():
            return False
        connection.execute('INSERT INTO profile_leases(profile_id,owner,purpose,acquired_at,expires_at) VALUES (?,?,?,?,?)',
                           (profile_id, owner, purpose, iso(now), iso(now + timedelta(seconds=seconds))))
        return True


def renew_lease(profile_id, owner, seconds):
    db.execute('UPDATE profile_leases SET expires_at=? WHERE profile_id=? AND owner=?',
               (iso(utcnow() + timedelta(seconds=seconds)), profile_id, owner))


def release_lease(profile_id, owner):
    db.execute('DELETE FROM profile_leases WHERE profile_id=? AND owner=?', (profile_id, owner))


def leased_profiles():
    return {row['profile_id']: row for row in db.rows('SELECT * FROM profile_leases WHERE expires_at>?', (iso(utcnow()),))}


# ---------- schedules (stage 09) ----------

KINDS = ('interval', 'daily', 'once')
GRACE = timedelta(minutes=10)  # an occurrence older than this counts as missed for the 'skip' policy


def zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def in_window(job, moment):
    """Working window in the job's time zone; 22:00-06:00 crosses midnight; no window means always."""
    start, end = job.get('window_start'), job.get('window_end')
    if not start or not end:
        return True
    tz = zone(job.get('timezone') or 'UTC') or timezone.utc
    local = moment.astimezone(tz)
    now, a, b = local.hour * 60 + local.minute, _minutes(start), _minutes(end)
    if a == b:
        return True
    return a <= now < b if a < b else (now >= a or now < b)


def _daily(day, job, tz):
    hour, minute = map(int, job['daily_time'].split(':'))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)


def occurrences_back(job, now, limit=1):
    """Occurrences at or before `now`, newest first, after the schedule was set and inside the window."""
    start = parse(job.get('schedule_from'))
    kind = job.get('schedule_kind')
    if not start or kind not in KINDS:
        return []
    if kind == 'once':
        at = parse(job.get('run_at'))
        return [at] if at and at <= now else []
    found = []
    if kind == 'interval' and job.get('interval_minutes'):
        step = timedelta(minutes=job['interval_minutes'])
        k = (now - start) // step if now >= start else 0
        guard = 0
        while k >= 1 and len(found) < limit and guard < 5000:
            moment = start + step * k
            if in_window(job, moment):
                found.append(moment)
            k, guard = k - 1, guard + 1
    elif kind == 'daily' and job.get('daily_time'):
        tz = zone(job.get('timezone') or 'UTC')
        if tz is None:
            return []
        day = now.astimezone(tz).date()
        for _ in range(400):
            candidate = _daily(day, job, tz)
            if candidate <= now:
                if candidate <= start or len(found) >= limit:
                    break
                if in_window(job, candidate):
                    found.append(candidate)
            day -= timedelta(days=1)
    return found


def latest_due(job, now):
    found = occurrences_back(job, now, 1)
    return found[0] if found else None


def next_due(job, now):
    if job.get('paused') or job.get('schedule_kind') not in KINDS:
        return None
    start = parse(job.get('schedule_from'))
    if job['schedule_kind'] == 'once':
        at = parse(job.get('run_at'))
        return at if at and at > now else None
    if not start:
        return None
    if job['schedule_kind'] == 'interval' and job.get('interval_minutes'):
        step = timedelta(minutes=job['interval_minutes'])
        k = max(1, (now - start) // step + 1) if now >= start else 1
        for _ in range(5000):
            moment = start + step * k
            if moment > now and in_window(job, moment):
                return moment
            k += 1
        return None
    if job['schedule_kind'] == 'daily' and job.get('daily_time'):
        tz = zone(job.get('timezone') or 'UTC')
        if tz is None:
            return None
        day = now.astimezone(tz).date()
        for _ in range(400):
            candidate = _daily(day, job, tz)
            if candidate > now and candidate > start and in_window(job, candidate):
                return candidate
            day += timedelta(days=1)
    return None


def schedule_tick(now=None):
    """Queue due occurrences once each. Policies:
    missed 'one' (default) - only the latest missed occurrence; 'skip' - a late occurrence is recorded as skipped;
    'all' - up to 20 missed occurrences. Overlap 'skip' (default) - skip while a run of the job is active;
    'queue' - queue behind it (runs of one job still execute one after another)."""
    now = now or utcnow()
    created = []
    for job in db.rows("SELECT * FROM jobs WHERE schedule_kind IN ('interval','daily','once') AND paused=0"):
        from .finding_quality import is_test_url
        if not config.ALLOW_TEST_COLLECTION and any(is_test_url(url) for url in json.loads(job['urls'])):
            continue
        policy, overlap = job.get('missed_policy') or 'one', job.get('overlap_policy') or 'skip'
        occurrences = occurrences_back(job, now, 20 if policy == 'all' else 1)
        if not occurrences:
            continue
        with tx() as connection:
            existing = {row['scheduled_for'] for row in connection.execute(
                "SELECT scheduled_for FROM runs WHERE job_id=? AND trigger='schedule'", (job['id'],))}
            last = max(existing) if existing else None
            for occurrence in reversed(occurrences):
                key = iso(occurrence)
                if key in existing or (last is not None and key <= last):
                    continue
                busy = connection.execute("SELECT 1 FROM runs WHERE job_id=? AND status IN ('queued','running')", (job['id'],)).fetchone()
                if policy == 'skip' and now - occurrence > GRACE:
                    status, reason = 'skipped', 'Пропущено: время запуска прошло, пока программа не работала (правило «пропускать опоздавшие»).'
                elif busy and overlap == 'skip':
                    status, reason = 'skipped', 'Пропущено: предыдущий запуск этого задания ещё не завершён.'
                else:
                    status, reason = 'queued', None
                run_id = _insert_run(connection, job, 'schedule', status=status, scheduled_for=key, error=reason)
                if run_id:
                    created.append((job['id'], run_id, key, status, reason))
        for job_id, run_id, key, status, reason in [c for c in created if c[0] == job['id']]:
            db.log(job_id, 'info' if status == 'queued' else 'warning',
                   f'Расписание: запуск #{run_id} на {key} ' + ('поставлен в очередь.' if status == 'queued' else reason), run_id=run_id)
    return [run_id for _, run_id, *_ in created]


def public_runs(limit=150):
    return db.rows('''SELECT runs.id,runs.job_id,jobs.name AS job_name,jobs.profile_id,runs.trigger,runs.status,runs.attempt,runs.scheduled_for,
        runs.not_before,runs.queued_at,runs.started_at,runs.finished_at,runs.heartbeat_at,runs.resume_from,runs.pages_done,runs.pages_total,runs.found,
        runs.error,runs.error_code,runs.stop_requested,runs.retry_of FROM runs JOIN jobs ON jobs.id=runs.job_id ORDER BY runs.id DESC LIMIT ?''', (limit,))


def search_runs(job_id=None, status='', error_code='', since=None, until=None, limit=50, offset=0):
    """Stage 17: run history for any period, newest first. since/until are UTC ISO strings (until exclusive)."""
    clauses, params = [], []
    for column, value in (('runs.job_id', job_id), ('runs.status', status), ('runs.error_code', error_code)):
        if value:
            clauses.append(f'{column}=?'); params.append(value)
    if since:
        clauses.append('runs.queued_at>=?'); params.append(since)
    if until:
        clauses.append('runs.queued_at<?'); params.append(until)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    total = db.rows(f'SELECT COUNT(*) AS n FROM runs{where}', params)[0]['n']
    items = db.rows(f'''SELECT runs.id,runs.job_id,jobs.name AS job_name,jobs.profile_id,runs.trigger,runs.status,runs.attempt,runs.scheduled_for,
        runs.not_before,runs.queued_at,runs.started_at,runs.finished_at,runs.heartbeat_at,runs.resume_from,runs.pages_done,runs.pages_total,runs.found,
        runs.error,runs.error_code,runs.stop_requested,runs.retry_of FROM runs JOIN jobs ON jobs.id=runs.job_id{where} ORDER BY runs.id DESC LIMIT ? OFFSET ?''',
        params + [limit, offset])
    return {'total': total, 'items': items}


def worker_status():
    rows = db.rows('SELECT * FROM workers WHERE stopped_at IS NULL ORDER BY heartbeat_at DESC LIMIT 5')
    now = utcnow()
    result = []
    for row in rows:
        age = (now - parse(row['heartbeat_at'])).total_seconds()
        result.append({'id': row['id'][:8], 'mode': row['mode'], 'version': row['version'], 'started_at': row['started_at'],
                       'heartbeat_at': row['heartbeat_at'], 'age_seconds': int(age), 'alive': age <= config.STALE_SECONDS,
                       'max_browsers': row['max_browsers']})
    return result
