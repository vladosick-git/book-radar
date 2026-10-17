"""Bounded background queue for the local simulator; no production adapters."""
import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db, simulation as sim, simulation_batch as batch, simulation_reading as reading

router = APIRouter(prefix='/api/simulation')
tasks = {}


def init():
    sim.init()
    with db.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS simulation_fleet_runs (
          id INTEGER PRIMARY KEY, target INTEGER NOT NULL, concurrency INTEGER NOT NULL,
          read_books INTEGER NOT NULL, book TEXT NOT NULL, pages INTEGER NOT NULL,
          min_seconds REAL NOT NULL, max_seconds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'running',
          created_at TEXT NOT NULL, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS simulation_fleet_members (
          run_id INTEGER NOT NULL REFERENCES simulation_fleet_runs(id),
          profile_id INTEGER NOT NULL REFERENCES simulation_profiles(id),
          status TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'waiting',
          message TEXT NOT NULL DEFAULT '', read_run_id INTEGER,
          PRIMARY KEY(run_id,profile_id));''')


def plan(c, target, permanent_only=False):
    if permanent_only:
        existing = c.execute("SELECT COUNT(*) AS n FROM permanent_mail_accounts WHERE profile_id IS NOT NULL AND login_verified=1 AND status IN ('created','attached')").fetchone()['n']
        free = c.execute("SELECT COUNT(*) AS n FROM permanent_mail_accounts WHERE profile_id IS NULL AND login_verified=1 AND status IN ('created','attached')").fetchone()['n']
        needed = max(0, target - existing)
        return {'target': target, 'existing_bots': existing, 'free_addresses': free,
                'available': existing + free, 'need_profiles': needed, 'shortage': max(0, needed - free)}
    existing = c.execute('SELECT COUNT(*) AS n FROM simulation_profiles').fetchone()['n']
    free = c.execute('''SELECT COUNT(*) AS n FROM simulation_mail_pool m WHERE profile_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM simulation_profiles p WHERE lower(p.email)=lower(m.email))''').fetchone()['n']
    needed = max(0, target - existing)
    return {'target': target, 'existing_bots': existing, 'free_addresses': free,
            'available': existing + free, 'need_profiles': needed, 'shortage': max(0, needed - free)}


class StartInput(BaseModel):
    target: int = Field(default=3, ge=1, le=500)
    concurrency: int = Field(default=2, ge=1, le=3)
    read_books: bool = True
    permanent_only: bool = False
    book: str = 'cloud-library'
    pages: int = Field(default=1, ge=1, le=6)
    min_seconds_per_page: float = Field(default=60, ge=.2, le=600)
    max_seconds_per_page: float = Field(default=120, ge=.2, le=600)

    @field_validator('book')
    @classmethod
    def known_book(cls, value):
        if value not in reading.BOOKS:
            raise ValueError('Выберите тестовую книгу.')
        return value

    @model_validator(mode='after')
    def ordered_range(self):
        if self.max_seconds_per_page < self.min_seconds_per_page:
            raise ValueError('Максимум времени должен быть не меньше минимума.')
        return self


@router.get('/fleet')
def state():
    init()
    rows = db.rows('SELECT * FROM simulation_fleet_runs ORDER BY id DESC LIMIT 1')
    if not rows:
        return {'run': None}
    run = rows[0]
    run['counts'] = {key: 0 for key in ('queued', 'running', 'done', 'failed', 'stopped')}
    for row in db.rows('SELECT status,COUNT(*) AS n FROM simulation_fleet_members WHERE run_id=? GROUP BY status', (run['id'],)):
        run['counts'][row['status']] = row['n']
    run['members'] = db.rows('''SELECT m.*,p.name FROM simulation_fleet_members m JOIN simulation_profiles p ON p.id=m.profile_id
        WHERE run_id=? ORDER BY CASE m.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,p.id LIMIT 12''', (run['id'],))
    return {'run': run}


def enqueue(payload):
    init()
    if payload.permanent_only:
        from . import permanent_mail
        permanent_mail.init()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM simulation_fleet_runs WHERE status IN ('running','stopping')").fetchone():
            raise HTTPException(409, 'Партия уже работает. Остановите её перед новым запуском.')
        info = plan(c, payload.target, payload.permanent_only)
        if info['shortage']:
            raise HTTPException(409, f"Для {payload.target} ботов есть только {info['available']} адресов. Не хватает {info['shortage']}. Создайте недостающих или добавьте адреса в базу.")
        if payload.permanent_only:
            profiles = c.execute('''SELECT p.id,p.lease_until FROM simulation_profiles p JOIN permanent_mail_accounts m ON m.profile_id=p.id
                WHERE m.login_verified=1 AND m.status IN ('created','attached') ORDER BY p.id LIMIT ?''', (payload.target,)).fetchall()
        else:
            profiles = c.execute('SELECT id,lease_until FROM simulation_profiles ORDER BY id LIMIT ?', (payload.target,)).fetchall()
        if any(p['lease_until'] > time.time() for p in profiles):
            raise HTTPException(409, 'Один из ботов сейчас занят отдельной операцией. Дождитесь её завершения.')
        ids = [p['id'] for p in profiles]
        if payload.permanent_only:
            from . import permanent_mail
            ids += permanent_mail.allocate_verified(c, info['need_profiles'])
        else:
            ids += batch.allocate(c, info['need_profiles'], new_emails=False)
        rid = c.execute('''INSERT INTO simulation_fleet_runs(target,concurrency,read_books,book,pages,min_seconds,max_seconds,created_at)
            VALUES (?,?,?,?,?,?,?,?)''', (payload.target, payload.concurrency, int(payload.read_books), payload.book,
                payload.pages, payload.min_seconds_per_page, payload.max_seconds_per_page, db.now())).lastrowid
        c.executemany('INSERT INTO simulation_fleet_members(run_id,profile_id) VALUES (?,?)', [(rid, pid) for pid in ids])
        c.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (NULL,?,?)', ('fleet_started', db.now()))
    return rid


async def worker(run_id, origin, payload):
    while True:
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            run = c.execute('SELECT status FROM simulation_fleet_runs WHERE id=?', (run_id,)).fetchone()
            if not run or run['status'] != 'running':
                return
            member = c.execute("SELECT profile_id FROM simulation_fleet_members WHERE run_id=? AND status='queued' ORDER BY profile_id LIMIT 1", (run_id,)).fetchone()
            if not member:
                return
            pid = member['profile_id']
            c.execute("UPDATE simulation_fleet_members SET status='running',phase='registering' WHERE run_id=? AND profile_id=?", (run_id, pid))
        try:
            result = await sim.register_one(pid, origin, fleet_run_id=run_id)
            if result['status'] != 'registered':
                raise ValueError('Registration failed')
            read_id = None
            if payload.read_books:
                db.execute("UPDATE simulation_fleet_members SET phase='reading' WHERE run_id=? AND profile_id=?", (run_id, pid))
                result = await reading.read_one(pid, origin, reading.ReadInput(profile_ids=[pid], book=payload.book,
                    pages=payload.pages, min_seconds_per_page=payload.min_seconds_per_page,
                    max_seconds_per_page=payload.max_seconds_per_page), fleet_run_id=run_id)
                if result['status'] not in ('done', 'already_finished'):
                    raise ValueError('Reading failed')
                read_id = result.get('run_id')
            db.execute("UPDATE simulation_fleet_members SET status='done',phase='complete',read_run_id=? WHERE run_id=? AND profile_id=?", (read_id, run_id, pid))
        except asyncio.CancelledError:
            raise
        except Exception:
            db.execute("UPDATE simulation_fleet_members SET status='failed',message=? WHERE run_id=? AND profile_id=?",
                       ('Операция не завершилась. Проверьте состояние профиля и повторите запуск.', run_id, pid))


async def execute(run_id, origin, payload):
    children = [asyncio.create_task(worker(run_id, origin, payload)) for _ in range(min(payload.target, payload.concurrency))]
    status = 'done'
    try:
        await asyncio.gather(*children)
    except asyncio.CancelledError:
        status = 'stopped'
    except Exception:
        status = 'failed'
    finally:
        for task in children:
            if not task.done():
                task.cancel()
        await asyncio.gather(*children, return_exceptions=True)
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE simulation_fleet_members SET status='stopped' WHERE run_id=? AND status IN ('queued','running')", (run_id,))
            c.execute('UPDATE simulation_fleet_runs SET status=?,finished_at=? WHERE id=?', (status, db.now(), run_id))
        tasks.pop(run_id, None)


@router.post('/fleet/start', status_code=202)
async def start(payload: StartInput, request: Request):
    origin = sim.local_origin(str(request.base_url))
    rid = enqueue(payload)
    tasks[rid] = asyncio.create_task(execute(rid, origin, payload))
    return {'run_id': rid, 'target': payload.target, 'status': 'running'}


@router.post('/fleet/{run_id}/stop')
async def stop(run_id: int):
    init()
    rows = db.rows('SELECT status FROM simulation_fleet_runs WHERE id=?', (run_id,))
    if not rows:
        raise HTTPException(404, 'Партия не найдена.')
    if rows[0]['status'] in ('running', 'stopping'):
        db.execute("UPDATE simulation_fleet_runs SET status='stopping' WHERE id=?", (run_id,))
        task = tasks.get(run_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Cancellation can arrive before execute() gets its first event-loop turn.
        finish_unstarted(run_id)
    return {'stopped': True}


def finish_unstarted(run_id):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE simulation_fleet_members SET status='stopped' WHERE run_id=? AND status IN ('queued','running')", (run_id,))
        c.execute("UPDATE simulation_fleet_runs SET status='stopped',finished_at=? WHERE id=? AND status IN ('running','stopping')", (db.now(), run_id))
    tasks.pop(run_id, None)


def recover():
    """Do not silently resume browsers after a process restart."""
    init()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("""UPDATE simulation_profiles SET status='failed',lease_until=0,message='Предыдущая партия прервана перезапуском. Запустите её снова.'
            WHERE id IN (SELECT m.profile_id FROM simulation_fleet_members m JOIN simulation_fleet_runs r ON r.id=m.run_id
                WHERE m.status='running' AND r.status IN ('running','stopping'))""")
        c.execute("UPDATE simulation_fleet_members SET status='stopped' WHERE status IN ('queued','running')")
        c.execute("UPDATE simulation_fleet_runs SET status='interrupted',finished_at=? WHERE status IN ('running','stopping')", (db.now(),))


async def shutdown():
    ids = list(tasks)
    pending = [tasks[rid] for rid in ids]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for rid in ids:
        finish_unstarted(rid)
