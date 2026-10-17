import asyncio
import csv
import io
import json
import os
import re
import sqlite3
import time
import uuid
import httpx
from contextlib import asynccontextmanager
from typing import Literal, Optional
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
from playwright.async_api import async_playwright
from . import adapters, audit, auth, catalog, config, db, notify, ocr, runs, sessions, purchases
from .browser import open_context, close_context, save_context
from .evidence import evidence_path, cleanup
from .errors import readable_error, ERROR_CODES
from .worker import Worker
from . import preparation, mail_registration, onboarding, mail_setup, simulation, simulation_reading, simulation_batch, simulation_fleet, permanent_mail, collection_setup, real_mail

VERSION = config.VERSION
open_profiles = {}   # local headed windows: profile_id -> task
profile_contexts = {}  # live contexts for owner-operated registration/login
remote_activity = {}
mail_tasks = {}
mail_owners = {}
WINDOW_OWNER = f'window:{os.getpid()}:{uuid.uuid4().hex[:8]}'
WINDOW_LEASE_SECONDS = 60
embedded = {}


@asynccontextmanager
async def lifespan(app):
    db.init()
    real_mail.init()
    if not config.SIMULATION:
        permanent_mail.recover()
    if config.SIMULATION and not config.SERVER:
        simulation_fleet.recover()
        permanent_mail.recover()
    if config.EMBEDDED_WORKER and not config.SIMULATION:
        embedded['stop'] = asyncio.Event()
        embedded['task'] = asyncio.create_task(Worker(embedded=True).run(embedded['stop']))
    yield
    await permanent_mail.shutdown()
    await simulation_fleet.shutdown()
    if embedded.get('task'):
        embedded['stop'].set()
        await asyncio.gather(embedded.pop('task'), return_exceptions=True)
    pending = list(open_profiles.values()) + list(mail_tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for owner in list(mail_owners.values()):
        mail_registration.release_operation(owner)
    mail_owners.clear()


app = FastAPI(title='Книжный радар', lifespan=lifespan)
app.include_router(purchases.router)
app.include_router(onboarding.router)
app.include_router(mail_setup.router)
app.include_router(simulation.router)
app.include_router(simulation_reading.router)
app.include_router(simulation_batch.router)
app.include_router(simulation_fleet.router)
app.include_router(permanent_mail.router)
app.include_router(collection_setup.router)
app.include_router(real_mail.router)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
PUBLIC_API = {'/api/health', '/api/health/worker', '/api/login', '/api/logout', '/api/session'}


@app.exception_handler(RequestValidationError)
async def private_mail_validation(request, exc):
    if request.url.path.startswith(('/api/mail', '/api/notifications', '/api/purchases', '/api/onboarding', '/api/simulation')):
        # Validation input/context may echo passwords, OAuth or bot tokens.
        return JSONResponse({'detail': [{'loc': error['loc'], 'msg': error['msg'], 'type': error['type']}
            for error in exc.errors()]}, status_code=422, headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


@app.middleware('http')
async def guard(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
        return Response('Origin not allowed', status_code=403)
    path = request.url.path
    if config.SIMULATION and request.method not in ('GET', 'HEAD', 'OPTIONS') and not path.startswith('/api/simulation/'):
        return JSONResponse({'detail': 'В режиме симуляции доступны только тестовые действия.'}, status_code=409)
    if config.AUTH and path.startswith('/api/') and path not in PUBLIC_API and not auth.valid(request.cookies.get(auth.COOKIE)):
        return JSONResponse({'detail': 'Требуется вход в панель.'}, status_code=401)
    response = await call_next(request)
    if path.startswith(('/api/mail', '/api/notifications', '/api/purchases', '/api/onboarding', '/api/simulation')):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/health')
def health():
    return {'app': 'book-radar', 'version': VERSION, 'mode': config.MODE}


@app.get('/api/health/worker')
def worker_health():
    alive = [w for w in runs.worker_status() if w['alive']]
    return JSONResponse({'alive': bool(alive), 'workers': len(alive)}, status_code=200 if alive else 503)


class LoginInput(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@app.get('/api/session')
def session(request: Request):
    return {'auth_required': config.AUTH, 'authenticated': (not config.AUTH) or auth.valid(request.cookies.get(auth.COOKIE)), 'mode': config.MODE}


@app.post('/api/login')
async def login(payload: LoginInput, request: Request):
    if not config.AUTH:
        return {'authenticated': True}
    if not config.ADMIN_PASSWORD_HASH:
        raise HTTPException(503, 'Пароль администратора не настроен: задайте RADAR_ADMIN_PASSWORD_HASH.')
    client = request.client.host if request.client else 'unknown'
    if auth.throttled(client):
        raise HTTPException(429, 'Слишком много попыток входа. Подождите 15 минут.')
    if not auth.verify(payload.password, config.ADMIN_PASSWORD_HASH):
        auth.note_failure(client)
        audit.record('login_failed', who='неизвестный', client=client)
        await asyncio.sleep(1)
        raise HTTPException(401, 'Неверный пароль.')
    response = JSONResponse({'authenticated': True})
    token = auth.create_session()
    audit.record('login', who=f'администратор · сессия {__import__("hashlib").sha256(token.encode()).hexdigest()[:6]}', client=client)
    response.set_cookie(auth.COOKIE, token, max_age=config.SESSION_HOURS * 3600, httponly=True,
                        secure=config.COOKIE_SECURE, samesite='strict', path='/')
    return response


@app.post('/api/logout')
def logout(request: Request):
    auth.drop(request.cookies.get(auth.COOKIE))
    response = JSONResponse({'authenticated': False})
    response.delete_cookie(auth.COOKIE, path='/')
    return response


def validate_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Укажите полный HTTP/HTTPS URL без логина и пароля.')
    return value


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator('name')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Название не должно быть пустым.')
        return value.strip()


class JobFields(ProfileInput):
    urls: list[str] = Field(min_length=1, max_length=20)
    selector: str = Field(default='', max_length=500)
    wait_seconds: int = Field(default=5, ge=2, le=30)
    headed: bool = False
    login_selector: str = Field(default='', max_length=500)

    @field_validator('urls')
    @classmethod
    def clean_urls(cls, values):
        urls = list(dict.fromkeys(validate_url(value.strip()) for value in values))
        from .finding_quality import is_test_url
        if not config.ALLOW_TEST_COLLECTION and any(is_test_url(url) for url in urls):
            raise ValueError('Тестовый сбор разрешён только в отдельной QA-базе с RADAR_ALLOW_TEST_COLLECTION=1.')
        return urls


class JobInput(JobFields):
    profile_id: int


HHMM = r'([01]\d|2[0-3]):[0-5]\d'


class ScheduleInput(BaseModel):
    kind: Literal['none', 'interval', 'daily', 'once'] = 'none'
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=7 * 24 * 60)
    daily_time: Optional[str] = None
    run_at: Optional[str] = Field(default=None, max_length=32)  # local 'YYYY-MM-DDTHH:MM' in `timezone`
    timezone: str = Field(default='Europe/Moscow', max_length=64)
    missed_policy: Literal['one', 'skip', 'all'] = 'one'
    overlap_policy: Literal['skip', 'queue'] = 'skip'
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    paused: bool = False

    @model_validator(mode='after')
    def consistent(self):
        tz = runs.zone(self.timezone)
        if tz is None:
            raise ValueError('Неизвестный часовой пояс. Пример: Europe/Moscow.')
        if self.kind == 'interval' and not self.interval_minutes:
            raise ValueError('Для интервала укажите число минут.')
        if self.kind == 'daily' and not re.fullmatch(HHMM, self.daily_time or ''):
            raise ValueError('Время запуска — в формате ЧЧ:ММ, например 03:30.')
        if bool(self.window_start) != bool(self.window_end) or any(v and not re.fullmatch(HHMM, v) for v in (self.window_start, self.window_end)):
            raise ValueError('Окно работы: укажите начало и конец в формате ЧЧ:ММ или оставьте оба поля пустыми.')
        if self.kind == 'once':
            from datetime import datetime
            try:
                local = datetime.fromisoformat(self.run_at or '')
            except ValueError:
                raise ValueError('Дата и время разового запуска — в формате ГГГГ-ММ-ДДTЧЧ:ММ.')
            moment = (local if local.tzinfo else local.replace(tzinfo=tz))
            if moment <= runs.utcnow():
                raise ValueError('Время разового запуска уже прошло.')
            self.run_at = runs.iso(moment)
        return self


class OpenInput(BaseModel):
    url: str = 'https://author.today/'

    @field_validator('url')
    @classmethod
    def clean_url(cls, value):
        return validate_url(value)


def get_job(job_id):
    result = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))
    if not result:
        raise HTTPException(404, 'Задание не найдено.')
    return result[0]


def local_only():
    if config.SERVER:
        raise HTTPException(403, 'В серверном режиме это действие недоступно: раздел работает только в локальной версии.')


@app.get('/api/state')
def state():
    now = runs.utcnow()
    jobs = db.rows('SELECT jobs.*,profiles.name AS profile_name FROM jobs JOIN profiles ON profiles.id=jobs.profile_id ORDER BY jobs.id DESC')
    run_rows = runs.public_runs()
    active = {r['job_id']: r for r in reversed(run_rows) if r['status'] in runs.ACTIVE}
    leases = runs.leased_profiles()
    profiles = db.rows('SELECT * FROM profiles ORDER BY id DESC')
    at_checks = db.rows('''SELECT o.account_id,o.status,o.checked_at,o.updated_at,a.profile_id
        FROM onboarding_at o JOIN accounts a ON a.id=o.account_id ORDER BY o.updated_at''')
    at_by_profile = {row['profile_id']: row for row in at_checks}
    finding_counts = {row['profile_id']: row['n'] for row in db.rows('SELECT profile_id, COUNT(*) AS n FROM findings GROUP BY profile_id')}
    for profile in profiles:
        lease = leases.get(profile['id'])
        profile['open'] = profile['id'] in open_profiles or bool(lease and lease['purpose'] == 'window')
        profile['collecting'] = bool(lease and lease['purpose'] == 'collect')
        profile['session_state'] = sessions.state_of(profile)
        profile['at_session'] = at_by_profile.get(profile['id'])
        profile['findings_count'] = finding_counts.get(profile['id'], 0)
        profile['problems'] = audit.problems(profile['id'])
    for job in jobs:
        job['urls'] = json.loads(job['urls'])
        upcoming = runs.next_due(job, now)
        job['next_run_at'] = runs.iso(upcoming) if upcoming else None
        job['active_run'] = active.get(job['id'])
    workers = runs.worker_status()
    return {'profiles': profiles, 'jobs': jobs, 'runs': run_rows,
            'mailboxes': mail_registration.public_rows(), 'mail_running': mail_registration.operation_active(),
            'mail_operation': mail_registration.operation_kind(),
            'services': db.rows('SELECT * FROM services ORDER BY id'),
            'accounts': db.rows('''SELECT accounts.*,profiles.name AS profile_name,services.name AS service_name,
                services.kind AS service_kind,services.registration_url FROM accounts
                JOIN profiles ON profiles.id=accounts.profile_id JOIN services ON services.id=accounts.service_id
                ORDER BY profiles.id,services.kind,services.id'''),
            # 0.7.0: findings and the journal are searched on the server (/api/findings, /api/logs); the state carries
            # only counters that tell an open page when to refresh.
            'catalog': catalog.summary(),
            'journal': {'last_id': db.rows('SELECT MAX(id) AS n FROM logs')[0]['n'] or 0,
                        'last_audit_id': db.rows('SELECT MAX(id) AS n FROM audit')[0]['n'] or 0},
            'notifications': {key: value for key, value in notify.settings().items() if key in ('enabled', 'channel')},
            'server': {'version': VERSION, 'mode': config.MODE, 'time': runs.iso(now), 'auth': config.AUTH,
                       'simulation_enabled': config.SIMULATION and not config.SERVER, 'preview_fixture': config.VISUAL_PREVIEW,
                       'test_collection_enabled': config.ALLOW_TEST_COLLECTION,
                       'max_browsers': config.MAX_BROWSERS, 'stale_seconds': config.STALE_SECONDS, 'workers': workers,
                       'worker_alive': any(w['alive'] for w in workers), 'worker_enabled': (config.EMBEDDED_WORKER or config.SERVER) and not config.SIMULATION,
                       'queue': {'queued': sum(r['status'] == 'queued' for r in run_rows), 'running': sum(r['status'] == 'running' for r in run_rows)},
                       'running_jobs': sorted(j for j, r in active.items() if r['status'] == 'running'), 'open_profiles': sorted(open_profiles),
                       'mail_enabled': True, 'windows_enabled': not config.SERVER, 'error_codes': ERROR_CODES, 'ocr_mode': config.OCR}}


@app.post('/api/profiles', status_code=201)
def create_profile(payload: ProfileInput):
    try:
        profile_id = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (payload.name, db.now()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Профиль с таким названием уже есть.')
    db.log(None, 'info', f'Создан профиль «{payload.name}».', source='profile')
    audit.record('profile_create', 'profile', profile_id, name=payload.name)
    return {'id': profile_id}


async def profile_window(profile_id, url, ready, *, headless=False):
    try:
        async with async_playwright() as pw:
            context = await open_context(pw, profile_id, headless=headless)
            profile_contexts[profile_id] = context
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                loaded = False
                try:
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=40000)
                    loaded = response is None or response.status < 400
                except Exception:
                    db.log(None, 'warning', 'Окно профиля открыто, но страница не загрузилась. Можно ввести адрес вручную.', source='profile', profile_id=profile_id)
                ready.set_result({'loaded': loaded})
                ticks = 0
                while context.pages:
                    if headless and time.monotonic() - remote_activity.get(profile_id, 0) > 600:
                        break
                    await save_context(context, profile_id)
                    ticks += 1
                    if ticks % 5 == 0:
                        await asyncio.to_thread(runs.renew_lease, profile_id, WINDOW_OWNER, WINDOW_LEASE_SECONDS)
                    await asyncio.sleep(1)
            finally:
                await close_context(context, profile_id)
    except asyncio.CancelledError:
        if not ready.done():
            ready.cancel()
        raise
    except Exception as exc:
        db.log(None, 'error', readable_error(exc), source='profile', profile_id=profile_id)
        if not ready.done():
            ready.set_exception(RuntimeError('Не удалось открыть браузер. Проверьте установку Chromium.'))
    finally:
        profile_contexts.pop(profile_id, None)
        remote_activity.pop(profile_id, None)
        open_profiles.pop(profile_id, None)
        runs.release_lease(profile_id, WINDOW_OWNER)


@app.post('/api/profiles/{profile_id}/open')
async def open_profile(profile_id: int, payload: OpenInput):
    if config.SERVER:
        raise HTTPException(409, 'На сервере нет экрана для окна браузера. Вход в профиль выполняется в локальной версии, затем сессия переносится.')
    return await start_owner_browser(profile_id, payload.url)


async def start_owner_browser(profile_id, url, *, headless=False):
    """Internal entry point; server AT onboarding uses a headless owner-controlled window."""
    if not db.rows('SELECT id FROM profiles WHERE id=?', (profile_id,)):
        raise HTTPException(404, 'Профиль не найден.')
    if config.SERVER and headless and open_profiles:
        raise HTTPException(409, 'На сервере уже открыт браузер подтверждений. Сначала закройте его в панели.')
    if profile_id in open_profiles or not runs.acquire_lease(profile_id, WINDOW_OWNER, 'window', WINDOW_LEASE_SECONDS):
        raise HTTPException(409, 'Этот профиль уже открыт или выполняет сбор.')
    ready = asyncio.get_running_loop().create_future()
    if headless:
        remote_activity[profile_id] = time.monotonic()
    open_profiles[profile_id] = asyncio.create_task(profile_window(profile_id, url, ready, headless=headless))
    try:
        result = await asyncio.wait_for(asyncio.shield(ready), timeout=60)
    except Exception as exc:
        pending = open_profiles.get(profile_id)
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        runs.release_lease(profile_id, WINDOW_OWNER)
        raise HTTPException(500, str(exc) or 'Открытие браузера заняло слишком много времени.')
    db.execute('UPDATE profiles SET last_used_at=? WHERE id=?', (db.now(), profile_id))
    audit.record('profile_window_open', 'profile', profile_id, url=url, remote=headless)
    return {'status': 'open', **result}


@app.post('/api/profiles/{profile_id}/close')
async def close_profile(profile_id: int):
    task = open_profiles.get(profile_id)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return {'status': 'closed'}


@app.post('/api/profiles/batch', status_code=201)
def batch_profiles(payload: preparation.BatchProfilesInput):
    result = preparation.create_batch(payload)
    audit.record('profile_batch_create', 'profiles', None, ids=result['ids'], count=len(result['ids']))
    return result


@app.post('/api/services', status_code=201)
def add_service(payload: preparation.ServiceInput):
    return preparation.create_service(payload)


@app.post('/api/accounts/assign', status_code=201)
def assign_accounts(payload: preparation.AssignInput):
    return preparation.assign_accounts(payload)


@app.post('/api/services/{service_id}')
def edit_service(service_id: int, payload: preparation.ServiceInput):
    return preparation.update_service(service_id, payload)


@app.post('/api/accounts/{account_id}')
def update_account(account_id: int, payload: preparation.AccountInput):
    return preparation.update_account(account_id, payload)


@app.post('/api/accounts/{account_id}/open')
async def open_registration(account_id: int):
    account = preparation.get_account(account_id)
    result = await open_profile(account['profile_id'], OpenInput(url=account['registration_url']))
    preparation.mark_opened(account_id)
    # A loaded page is neither a completed registration nor an authenticated account.
    return {**result, 'account_status': preparation.get_account(account_id)['status']}


@app.post('/api/jobs', status_code=201)
def create_job(payload: JobInput, request: Request):
    if not db.rows('SELECT id FROM profiles WHERE id=?', (payload.profile_id,)):
        raise HTTPException(404, 'Сначала создайте профиль.')
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at,headed,login_selector) VALUES (?,?,?,?,?,?,?,?)',
                        (payload.name, payload.profile_id, json.dumps(payload.urls), payload.selector.strip(), payload.wait_seconds, db.now(),
                         int(payload.headed), payload.login_selector.strip()))
    audit.record('job_create', 'job', job_id, request, name=payload.name, pages=len(payload.urls))
    return {'id': job_id}


@app.post('/api/jobs/{job_id}')
def update_job(job_id: int, payload: JobFields, request: Request):
    job = get_job(job_id)
    if runs.active_run(job_id) or job['status'] == 'running':
        raise HTTPException(409, 'Задание в очереди или выполняется. Остановите запуск или дождитесь завершения, затем измените параметры.')
    db.execute('UPDATE jobs SET name=?,urls=?,selector=?,wait_seconds=?,headed=?,login_selector=?,updated_at=? WHERE id=?',
               (payload.name, json.dumps(payload.urls), payload.selector.strip(), payload.wait_seconds, int(payload.headed),
                payload.login_selector.strip(), db.now(), job_id))
    db.log(job_id, 'info', 'Параметры задания изменены. Прежние находки и журнал сохранены; изменения применятся со следующего запуска.')
    changed = [key for key, old, new in [('name', job['name'], payload.name), ('urls', job['urls'], json.dumps(payload.urls)),
               ('selector', job['selector'], payload.selector.strip()), ('wait_seconds', job['wait_seconds'], payload.wait_seconds),
               ('headed', job['headed'], int(payload.headed)), ('login_selector', job.get('login_selector') or '', payload.login_selector.strip())] if old != new]
    audit.record('job_edit', 'job', job_id, request, changed=changed)
    return {'id': job_id}


@app.post('/api/jobs/{job_id}/schedule')
def update_schedule(job_id: int, payload: ScheduleInput, request: Request):
    job = get_job(job_id)
    interval = payload.interval_minutes if payload.kind == 'interval' else None
    daily = payload.daily_time if payload.kind == 'daily' else None
    run_at = payload.run_at if payload.kind == 'once' else None
    changed = (payload.kind, interval, daily, run_at, payload.timezone) != \
              (job['schedule_kind'], job['interval_minutes'], job['daily_time'], job.get('run_at'), job['timezone'])
    # Counting restarts only when the rule changes; pausing or a policy change keeps the rhythm.
    # The run already in progress is never touched: a schedule only creates future runs.
    schedule_from = db.now() if changed or not job['schedule_from'] else job['schedule_from']
    who = audit.actor(request)
    db.execute('''UPDATE jobs SET schedule_kind=?,interval_minutes=?,daily_time=?,run_at=?,timezone=?,paused=?,schedule_from=?,
        missed_policy=?,overlap_policy=?,window_start=?,window_end=?,schedule_updated_at=?,schedule_updated_by=? WHERE id=?''',
               (payload.kind, interval, daily, run_at, payload.timezone, int(payload.paused), schedule_from, payload.missed_policy,
                payload.overlap_policy, payload.window_start, payload.window_end, db.now(), who, job_id))
    rule = {'none': 'без расписания', 'interval': f'каждые {interval} мин', 'daily': f'ежедневно в {daily} ({payload.timezone})',
            'once': f'разово {run_at}'}[payload.kind]
    window = f', окно {payload.window_start}-{payload.window_end}' if payload.window_start else ''
    db.log(job_id, 'info', f'Расписание: {rule}{window}{" — на паузе" if payload.paused and payload.kind != "none" else ""}. Изменил: {who}.')
    audit.record('schedule_change', 'job', job_id, request, who=who, kind=payload.kind, interval_minutes=interval, daily_time=daily, run_at=run_at,
                 timezone=payload.timezone, missed_policy=payload.missed_policy, overlap_policy=payload.overlap_policy,
                 window_start=payload.window_start, window_end=payload.window_end, paused=payload.paused)
    upcoming = runs.next_due(dict(get_job(job_id)), runs.utcnow())
    return {'id': job_id, 'next_run_at': runs.iso(upcoming) if upcoming else None}


@app.post('/api/jobs/{job_id}/duplicate', status_code=201)
def duplicate_job(job_id: int, request: Request):
    job = get_job(job_id)
    name = f"{job['name'][:72]} (копия)"
    new_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at,headed,login_selector) VALUES (?,?,?,?,?,?,?,?)',
                        (name, job['profile_id'], job['urls'], job['selector'], job['wait_seconds'], db.now(), job['headed'], job.get('login_selector') or ''))
    audit.record('job_duplicate', 'job', new_id, request, source_job=job_id)
    db.log(new_id, 'info', f'Задание создано копированием задания #{job_id}. Находки и расписание исходного задания не переносятся.')
    return {'id': new_id}


class SessionExportInput(BaseModel):
    passphrase: str = Field(default='', max_length=256)


class SessionImportInput(BaseModel):
    passphrase: str = Field(default='', max_length=256)
    file_text: str = Field(default='')


@app.post('/api/profiles/{profile_id}/session/export')
def export_session(profile_id: int, payload: SessionExportInput, request: Request):
    content, name = sessions.export_session(profile_id, payload.passphrase)
    audit.record('session_export', 'profile', profile_id, request)
    safe = re.sub(r'[^0-9A-Za-zА-Яа-яЁё_-]+', '_', name).strip('_') or f'profile-{profile_id}'
    from urllib.parse import quote
    return Response(content, media_type='application/json', headers={
        'Cache-Control': 'no-store', 'Content-Disposition': f"attachment; filename=\"session-{profile_id}.radar-session.json\"; filename*=UTF-8''{quote(safe)}.radar-session.json"})


@app.post('/api/profiles/{profile_id}/session/import')
def import_session(profile_id: int, payload: SessionImportInput, request: Request):
    result = sessions.import_session(profile_id, payload.file_text, payload.passphrase)
    audit.record('session_import', 'profile', profile_id, request, cookies=result['cookies'], domains=result['domains'][:10])
    return {'status': 'imported', **result}


@app.get('/api/runs/{run_id}/pages')
def run_pages(run_id: int):
    if not db.rows('SELECT id FROM runs WHERE id=?', (run_id,)):
        raise HTTPException(404, 'Запуск не найден.')
    return {'run_id': run_id, 'pages': runs.run_pages(run_id)}


def period(since, until):
    """Dates from the panel: 'YYYY-MM-DD' (until = the whole day) or ISO 8601 with an offset → UTC; until is exclusive."""
    try:
        return catalog.moment(since[:40]), catalog.moment(until[:40], end=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def page_args(limit, offset, high=200):
    return max(1, min(int(limit), high)), max(0, int(offset))


@app.get('/api/audit')
def audit_search(q: str = '', action: str = '', target_type: str = '', since: str = '', until: str = '', limit: int = 100, offset: int = 0):
    since, until = period(since, until)
    limit, offset = page_args(limit, offset, 500)
    return audit.search(q.strip()[:100], action[:40], target_type[:20], since or '', until or '', limit, offset)


# ---------- stage 17: journal and run history for any period ----------

@app.get('/api/logs')
def logs_search(q: str = '', level: str = '', source: str = '', profile_id: int = 0, job_id: int = 0, run_id: int = 0, error_code: str = '',
                since: str = '', until: str = '', limit: int = 100, offset: int = 0):
    since, until = period(since, until)
    limit, offset = page_args(limit, offset, 500)
    return audit.search_logs(q.strip()[:100], level[:10], source[:10], profile_id or None, job_id or None, run_id or None, error_code[:30],
                             since, until, limit, offset)


@app.get('/api/runs')
def runs_search(job_id: int = 0, status: str = '', error_code: str = '', since: str = '', until: str = '', limit: int = 50, offset: int = 0):
    since, until = period(since, until)
    limit, offset = page_args(limit, offset)
    return runs.search_runs(job_id or None, status[:20], error_code[:30], since, until, limit, offset)


# ---------- stage 14: catalog of advertisements ----------

def finding_filters(q='', profile_id=0, job_id=0, source='', kind='', since='', until='', ad_id=0, block_type='', advertiser='', data_scope='real', include_duplicates=False):
    since_value, until_value = period(since, until)
    if kind and kind not in catalog.KINDS:
        raise HTTPException(400, 'Неизвестный тип находки.')
    if block_type and block_type not in adapters.BLOCK_TYPES:
        raise HTTPException(400, 'Неизвестный тип блока: ad, recommendation или unknown.')
    return {'q': q.strip()[:200], 'profile_id': profile_id or None, 'job_id': job_id or None, 'source': source[:200], 'kind': kind,
            'since': since_value, 'until': until_value, 'ad_id': ad_id or None, 'block_type': block_type, 'advertiser': advertiser.strip().lower()[:253],
            'data_scope': data_scope, 'include_duplicates': include_duplicates}


@app.get('/api/adapters')
def site_adapters():
    """Stage 11: site adapters and block types (adapter is chosen by the page domain; a job selector wins)."""
    return {'adapters': adapters.catalog(), 'block_types': adapters.BLOCK_TYPES}


@app.get('/api/findings')
def findings_search(q: str = '', profile_id: int = 0, job_id: int = 0, source: str = '', kind: str = '', since: str = '', until: str = '', block_type: str = '',
                    ad_id: int = 0, sort: Literal['last', 'first', 'seen'] = 'last', limit: int = 60, offset: int = 0, advertiser: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    filters = finding_filters(q, profile_id, job_id, source, kind, since, until, ad_id, block_type, advertiser, data_scope, include_duplicates)
    limit, offset = page_args(limit, offset)
    return catalog.search(filters, limit, offset, sort)


@app.get('/api/findings/facets')
def findings_facets(new_since: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    return catalog.facets(period(new_since, '')[0] if new_since else '', data_scope, include_duplicates)


@app.get('/api/findings/{finding_id}')
def finding_detail(finding_id: int):
    item = catalog.finding_detail(finding_id)
    if item is None:
        raise HTTPException(404, 'Находка не найдена.')
    return item


@app.get('/api/ads')
def ads_search(q: str = '', profile_id: int = 0, job_id: int = 0, source: str = '', kind: str = '', since: str = '', until: str = '', block_type: str = '',
               sort: Literal['last', 'first', 'seen'] = 'last', limit: int = 60, offset: int = 0, advertiser: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    filters = finding_filters(q, profile_id, job_id, source, kind, since, until, 0, block_type, advertiser, data_scope, include_duplicates)
    limit, offset = page_args(limit, offset)
    return catalog.search_ads(filters, limit, offset, sort)


@app.get('/api/ads/{ad_id}')
def ad_detail(ad_id: int):
    item = catalog.ad_detail(ad_id)
    if item is None:
        raise HTTPException(404, 'Объявление не найдено.')
    return item


@app.get('/api/export.json')
def export_json(request: Request, q: str = '', profile_id: int = 0, job_id: int = 0, source: str = '', kind: str = '', since: str = '', until: str = '', block_type: str = '',
                observations: bool = False, advertiser: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    filters = finding_filters(q, profile_id, job_id, source, kind, since, until, 0, block_type, advertiser, data_scope, include_duplicates)
    document = catalog.export(filters, with_observations=observations, version=VERSION)
    audit.record('export_json', 'findings', None, request, findings=document['counts']['findings'], observations=observations)
    return Response(json.dumps(document, ensure_ascii=False, indent=1), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="book-radar.json"'})


@app.get('/api/export/preview')
def export_preview(q: str = '', profile_id: int = 0, job_id: int = 0, source: str = '', kind: str = '', since: str = '', until: str = '', block_type: str = '', advertiser: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    filters = finding_filters(q, profile_id, job_id, source, kind, since, until, 0, block_type, advertiser, data_scope, include_duplicates)
    return catalog.export_preview(filters)


# ---------- stage 17: notifications ----------

class NotifyInput(BaseModel):
    enabled: bool = False
    chat_id: str = Field(default='', max_length=64)
    events: list[Literal['failure', 'user', 'done', 'recovered']] = Field(default_factory=lambda: list(notify.DEFAULT_EVENTS))
    token: Optional[str] = Field(default=None, max_length=120)
    clear_token: bool = False

    @field_validator('chat_id')
    @classmethod
    def clean_chat(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r'-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,63}', value):
            raise ValueError('Chat id - число (например -1001234567890) или имя канала вида @channel.')
        return value

    @field_validator('token')
    @classmethod
    def clean_token(cls, value):
        value = (value or '').strip()
        if value and not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,80}', value):
            raise ValueError('Токен бота имеет вид 123456789:AA... - скопируйте его из @BotFather целиком.')
        return value or None


@app.get('/api/notifications')
def notifications_state():
    return {'settings': notify.settings(), 'events': notify.EVENTS, 'recent': notify.recent()}


@app.post('/api/notifications/settings')
def notifications_save(payload: NotifyInput, request: Request):
    has_token = bool(payload.token) or (notify.settings()['token_set'] and not payload.clear_token)
    if payload.enabled and not has_token:
        raise HTTPException(400, 'Чтобы включить уведомления, укажите токен бота.')
    if payload.enabled and not payload.chat_id:
        raise HTTPException(400, 'Чтобы включить уведомления, укажите chat id получателя.')
    try:
        result = notify.save_settings(payload.enabled, payload.chat_id, payload.events, payload.token, payload.clear_token)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    audit.record('notify_settings', 'notifications', None, request, enabled=result['enabled'], chat_id=payload.chat_id, events=payload.events,
                 bot_changed=bool(payload.token), bot_removed=payload.clear_token)
    return result


@app.post('/api/notifications/test')
def notifications_test(request: Request):
    if not notify.settings()['token_set']:
        raise HTTPException(400, 'Сначала сохраните токен бота и chat id.')
    ok, detail = notify.test()
    audit.record('notify_test', 'notifications', None, request, delivered=ok)
    if not ok:
        raise HTTPException(502, f'Тестовое сообщение не доставлено: {detail}')
    return {'status': 'sent', 'detail': detail}


@app.get('/api/ocr')
def ocr_state():
    return ocr.engine()


@app.post('/api/mailboxes/prepare', status_code=201)
def prepare_mailboxes(payload: mail_registration.PrepareInput):
    return mail_registration.adapter.prepare(payload)


async def mailbox_runner(rows, token, owner):
    pulse = asyncio.create_task(mail_registration.heartbeat(owner))
    try:
        await mail_registration.run_batch(rows, token)
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        mail_registration.release_operation(owner)
        mail_owners.pop('zoho', None)
        mail_tasks.pop('zoho', None)


@app.post('/api/mailboxes/run', status_code=202)
async def run_mailboxes(payload: mail_registration.RunInput):
    if mail_tasks:
        raise HTTPException(409, 'Дождитесь завершения текущей регистрации.')
    owner = mail_registration.claim_operation('create')
    try:
        rows, token = mail_registration.select_for_run(payload)
        mail_owners['zoho'] = owner
        mail_tasks['zoho'] = asyncio.create_task(mailbox_runner(rows, token, owner))
    except BaseException:
        mail_registration.release_operation(owner)
        raise
    return {'status': 'started', 'count': len(rows)}


@app.post('/api/mailboxes/stop')
async def stop_mailboxes():
    if not mail_tasks and mail_registration.operation_active():
        raise HTTPException(409, 'Операция выполняется другим процессом API или это сверка. Дождитесь завершения.')
    pending = list(mail_tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    mail_tasks.clear()
    for owner in list(mail_owners.values()):
        mail_registration.release_operation(owner)
    mail_owners.clear()
    return {'status': 'stopped'}


@app.post('/api/mailboxes/{mailbox_id}/credentials')
def mailbox_credentials(mailbox_id: int):
    return Response(json.dumps(mail_registration.credentials(mailbox_id)), media_type='application/json',
        headers={'Cache-Control': 'no-store'})


@app.post('/api/mailboxes/attach', status_code=201)
def attach_mailbox(payload: mail_registration.AttachInput):
    return mail_registration.attach(payload)


@app.post('/api/mailboxes/{mailbox_id}/reconcile')
async def reconcile_mailbox(mailbox_id: int, payload: mail_registration.ReconcileInput):
    mail_registration.storage_ready()
    token = mail_registration.token_value(payload.access_token)
    owner = mail_registration.claim_operation('reconcile')
    pulse = asyncio.create_task(mail_registration.heartbeat(owner))
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            return await mail_registration.adapter.reconcile(client, mailbox_id, token)
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        mail_registration.release_operation(owner)


@app.post('/api/jobs/{job_id}/run', status_code=202)
def run_job(job_id: int, request: Request):
    get_job(job_id)
    run_id, reason = runs.enqueue(job_id)
    if reason == 'test_disabled':
        raise HTTPException(409, 'Тестовый сбор выключен в рабочей базе. Используйте отдельную QA-базу.')
    if reason == 'active':
        raise HTTPException(409, 'Задание уже в очереди или выполняется.')
    if reason == 'window':
        raise HTTPException(409, 'Закройте окно профиля перед сбором.')
    db.log(job_id, 'info', f'Запуск #{run_id} поставлен в очередь.', run_id=run_id)
    audit.record('run_queue', 'job', job_id, request, run_id=run_id)
    return {'status': 'queued', 'run_id': run_id}


@app.post('/api/jobs/{job_id}/stop')
def stop_job(job_id: int, request: Request):
    get_job(job_id)
    result = runs.request_stop(job_id)
    if result != 'idle':
        audit.record('run_stop', 'job', job_id, request, result=result)
    if result == 'cancelled':
        db.log(job_id, 'warning', 'Запуск отменён до начала.')
    return {'status': result}


@app.get('/api/export.csv')
def export(request: Request, q: str = '', profile_id: int = 0, job_id: int = 0, source: str = '', kind: str = '', since: str = '', until: str = '', block_type: str = '', advertiser: str = '', data_scope: Literal['real', 'test', 'all'] = 'real', include_duplicates: bool = False):
    out = io.StringIO()
    writer = csv.writer(out)
    # The 0.6 columns come first and are unchanged; stage 13/14 columns are appended.
    columns = ['id', 'title', 'text', 'destination', 'image_url', 'source_url', 'frame_url', 'profile_id', 'first_seen', 'last_seen', 'sightings', 'first_screenshot', 'screenshot',
               'ad_id', 'variant_no', 'kind', 'source_host', 'clean_text', 'image_source', 'ocr_text', 'crop',
               'block_type', 'block_reason', 'adapter']
    # 0.7.8: the advertiser shown on the card (block_evidence) is appended, earlier columns keep their order.
    columns += ['advertiser', 'target_url', 'original_title', 'is_test', 'duplicate_of', 'destination_status']
    writer.writerow(columns)
    clause, params = catalog.where(finding_filters(q, profile_id, job_id, source, kind, since, until, 0, block_type, advertiser, data_scope, include_duplicates))
    for item in db.rows(f'SELECT f.*, ads.variant_no FROM findings f LEFT JOIN ads ON ads.id=f.ad_id{clause} ORDER BY f.id', params):
        try:
            evidence = json.loads(item.get('block_evidence') or '{}')
        except (TypeError, ValueError):
            evidence = {}
        evidence = evidence if isinstance(evidence, dict) else {}
        item = catalog.finding_quality.present(item)
        item = {**item, 'advertiser': ', '.join(evidence.get('target_domains') or []), 'target_url': evidence.get('target_url') or ''}
        values = ['' if item[key] is None else str(item[key]) for key in columns]
        writer.writerow(["'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value for value in values])
    audit.record('export_csv', 'findings', None, request, filters=finding_filters(q, profile_id, job_id, source, kind, since, until, 0, block_type, advertiser, data_scope, include_duplicates))
    return Response('﻿' + out.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="book-radar.csv"'})


@app.get('/api/evidence/{filename}')
def evidence(filename: str):
    path = evidence_path(filename)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path)


@app.post('/api/evidence/cleanup')
def cleanup_screenshots():
    if open_profiles or runs.leased_profiles() or db.rows("SELECT 1 FROM runs WHERE status='running' LIMIT 1"):
        raise HTTPException(409, 'Завершите сбор и закройте профили перед очисткой.')
    removed = cleanup()
    db.log(None, 'info', f'Удалено старых скриншотов без ссылок: {removed}. Первые и последние скриншоты находок сохранены.', source='system')
    return {'removed': removed}


@app.get('/sample', response_class=HTMLResponse)
def sample():
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Тестовая страница радара</title>
    <style>body{font:18px system-ui;margin:60px;max-width:850px}article{padding:30px;border:1px solid #ccc;margin-top:30px}</style>
    <h1>Тестовая страница</h1><p>Искусственный рекламный блок для проверки сборщика. Это не реальная реклама.</p>
    <article data-ad><small>Реклама · тест</small><h2>Книга: Город за туманом</h2><p>Приключение начинается с исчезнувшей карты. Прочитайте первую главу.</p>
    <a href="https://example.com/book">Открыть книгу</a></article><section><h2>Обычный текст</h2><p>Этот раздел не должен попасть в находки.</p></section></html>'''


DIST = db.ROOT / 'frontend' / 'dist'
if DIST.is_dir():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='frontend')
