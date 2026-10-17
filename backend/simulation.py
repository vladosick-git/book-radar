"""Local-only registration benchmark. No production-site adapter or proxy support."""
import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator
from playwright.async_api import async_playwright

from . import config, db
from .simulation_presets import CITIES, DEVICES

router = APIRouter(prefix='/api/simulation')
MARKER = 'book-radar-local-simulation-v1'
COOKIE = 'radar_simulation'
PUBLIC = 'id,name,city,device,email,status,message,observed,created_at,updated_at'


def require_mode():
    if not config.SIMULATION or config.SERVER:
        raise HTTPException(409, 'Откройте отдельный локальный режим через Simulation.cmd.')


def init():
    require_mode()
    init_storage()


def init_storage():
    """Schema only. Shared encrypted mail can initialise storage without enabling simulation routes."""
    with db.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS simulation_profiles (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, city TEXT NOT NULL, device TEXT NOT NULL,
          email TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planned',
          message TEXT NOT NULL DEFAULT '', observed TEXT NOT NULL DEFAULT '{}', lease_until REAL NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS simulation_accounts (
          id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL UNIQUE REFERENCES simulation_profiles(id),
          email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, observations TEXT NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS simulation_sessions (
          token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES simulation_accounts(id), expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS simulation_events (
          id INTEGER PRIMARY KEY, profile_id INTEGER, action TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS simulation_mail_pool (
          id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          profile_id INTEGER UNIQUE REFERENCES simulation_profiles(id), created_at TEXT NOT NULL);''')


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    city: str
    device: str

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Укажите название профиля.')
        return value.strip()

    @field_validator('city')
    @classmethod
    def city_value(cls, value):
        if value not in CITIES:
            raise ValueError('Выберите город симуляции.')
        return value

    @field_validator('device')
    @classmethod
    def device_value(cls, value):
        if value not in DEVICES:
            raise ValueError('Выберите устройство симуляции.')
        return value


class ProfilesInput(BaseModel):
    profiles: list[ProfileInput] = Field(min_length=1, max_length=10)


class RunInput(BaseModel):
    profile_ids: list[int] = Field(min_length=1, max_length=10)

    @field_validator('profile_ids')
    @classmethod
    def ids(cls, values):
        if any(v < 1 for v in values) or len(set(values)) != len(values):
            raise ValueError('Нужны разные существующие профили.')
        return values


@router.get('/state')
def state():
    init()
    rows = db.rows(f'SELECT {PUBLIC},lease_until FROM simulation_profiles ORDER BY id')
    queued = set()
    mail = {}
    if db.rows("SELECT 1 FROM sqlite_master WHERE name='permanent_mail_accounts'"):
        mail = {r['profile_id']: r for r in db.rows('SELECT profile_id,status,login_verified FROM permanent_mail_accounts WHERE profile_id IS NOT NULL')}
    if db.rows("SELECT 1 FROM sqlite_master WHERE name='simulation_fleet_members'"):
        queued = {r['profile_id'] for r in db.rows("""SELECT m.profile_id FROM simulation_fleet_members m
            JOIN simulation_fleet_runs f ON f.id=m.run_id WHERE m.status IN ('queued','running') AND f.status IN ('running','stopping')""")}
    for row in rows:
        row['observed'] = json.loads(row['observed'])
        row['active'] = row.pop('lease_until') > time.time() or row['id'] in queued
        record = mail.get(row['id'])
        row['mail_ready'] = bool(record and record['login_verified'] and record['status'] in ('created', 'attached'))
        row['mail_status'] = record['status'] if record else 'unmanaged'
    return {'marker': MARKER, 'profiles': rows, 'cities': CITIES, 'devices': DEVICES,
            'registered': db.rows('SELECT COUNT(*) AS n FROM simulation_accounts')[0]['n'],
            'mail_pool': db.rows('SELECT COUNT(*) AS total,COUNT(profile_id) AS used,COUNT(*)-COUNT(profile_id) AS available FROM simulation_mail_pool')[0],
            'events': db.rows('SELECT * FROM simulation_events ORDER BY id DESC LIMIT 100')}


def fixture_email(c, pid):
    value = f'sim-{pid}@example.invalid'
    while (c.execute('SELECT 1 FROM simulation_profiles WHERE lower(email)=?', (value,)).fetchone()
           or c.execute('SELECT 1 FROM simulation_mail_pool WHERE email=?', (value,)).fetchone()):
        value = f'sim-{pid}-{secrets.token_hex(4)}@example.invalid'
    return value


@router.post('/profiles', status_code=201)
def create_profiles(payload: ProfilesInput):
    init()
    result = []
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for item in payload.profiles:
            prior = c.execute('SELECT * FROM simulation_profiles WHERE name=?', (item.name,)).fetchone()
            if prior and (prior['city'] != item.city or prior['device'] != item.device):
                raise HTTPException(409, 'Профиль с таким названием уже имеет другую среду. Выберите новое название.')
            if prior:
                result.append(prior['id'])
                continue
            now = db.now()
            pid = c.execute('INSERT INTO simulation_profiles(name,city,device,created_at,updated_at) VALUES (?,?,?,?,?)',
                            (item.name, item.city, item.device, now, now)).lastrowid
            c.execute('UPDATE simulation_profiles SET email=? WHERE id=?', (fixture_email(c, pid), pid))
            c.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (?,?,?)', (pid, 'profile_created', now))
            result.append(pid)
    return {'profile_ids': result}


def local_origin(value):
    try:
        u = urlsplit(value)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost', '::1') or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ValueError()
        port = u.port or 80
        host = f'[{u.hostname}]' if ':' in u.hostname else u.hostname
        return f'http://{host}:{port}'
    except ValueError:
        raise HTTPException(400, 'Симуляция поддерживает только локальный HTTP-адрес без пути и параметров.')


def allowed_request(url, origin):
    try:
        u = urlsplit(url)
        return local_origin(f'{u.scheme}://{u.netloc}') == origin and u.path.startswith('/api/simulation/site/')
    except HTTPException:
        return False


def device_options(pw, profile):
    options = dict(pw.devices[DEVICES[profile['device']]['preset']])
    options.pop('default_browser_type', None)
    city = CITIES[profile['city']]
    options.update(locale='ru-RU', timezone_id=city['timezone'],
                   geolocation={k: city[k] for k in ('latitude', 'longitude')}, permissions=['geolocation'],
                   accept_downloads=False, service_workers='block')
    return options


class SignupInput(BaseModel):
    profile_id: int = Field(ge=1)
    password: SecretStr = Field(min_length=12, max_length=200)
    width: int = Field(ge=1, le=10000)
    height: int = Field(ge=1, le=10000)
    touch: int = Field(ge=0, le=20)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(default='', max_length=80)
    dpr: float = Field(default=1, ge=.25, le=8)


def password_for(pid):
    # Deliberately a public fixture password, never a mailbox or production credential.
    return f'simulation-only-{pid}-not-a-real-account'


def password_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@router.get('/site/meta')
def meta():
    require_mode()
    return {'marker': MARKER, 'fixture': True}


@router.post('/site/register')
def signup(payload: SignupInput, request: Request):
    init()
    profile = db.rows('SELECT * FROM simulation_profiles WHERE id=?', (payload.profile_id,))
    if not profile or payload.password.get_secret_value() != password_for(payload.profile_id):
        raise HTTPException(400, 'Неизвестный тестовый профиль или неверный пароль фикстуры.')
    profile = profile[0]
    observations = {**payload.model_dump(exclude={'password'}), 'user_agent': request.headers.get('user-agent', '')[:512],
                    'city': profile['city'], 'device': profile['device'], 'engine': 'chromium', 'emulated': True}
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        created = c.execute('INSERT OR IGNORE INTO simulation_accounts(profile_id,email,password_hash,observations,created_at) VALUES (?,?,?,?,?)',
                  (profile['id'], profile['email'], password_hash(payload.password.get_secret_value()), json.dumps(observations), db.now())).rowcount
        account = c.execute('SELECT * FROM simulation_accounts WHERE profile_id=?', (profile['id'],)).fetchone()
        token = secrets.token_urlsafe(32)
        c.execute('DELETE FROM simulation_sessions WHERE expires_at<=?', (time.time(),))
        c.execute('INSERT INTO simulation_sessions VALUES (?,?,?)', (password_hash(token), account['id'], time.time() + 86400))
        c.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (?,?,?)', (profile['id'], 'site_register' if created else 'site_login', db.now()))
    response = JSONResponse({'account_id': account['id'], 'fixture': True}, status_code=201)
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', path='/api/simulation/site/', max_age=86400)
    return response


@router.get('/site/me')
def me(request: Request):
    init()
    rows = db.rows('''SELECT a.id,a.profile_id,a.email,a.observations FROM simulation_accounts a
        JOIN simulation_sessions s ON a.id=s.account_id WHERE s.token_hash=? AND s.expires_at>?''',
        (password_hash(request.cookies.get(COOKIE, '')), time.time()))
    if not rows:
        raise HTTPException(401, 'Тестовый вход не выполнен.')
    return {**rows[0], 'observations': json.loads(rows[0]['observations']), 'fixture': True}


SITE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Регистрация · симуляция Книжного радара</title>
<style>body{font:16px system-ui;max-width:520px;margin:32px auto;padding:16px}label{display:block;margin:16px 0}input,button{font:inherit;padding:10px;box-sizing:border-box;max-width:100%}input{display:block;width:100%}button{cursor:pointer}aside{background:#e9f4ef;padding:16px}</style>
<h1>Тестовая регистрация</h1><aside>Локальная симуляция. Аккаунты и почтовые адреса вымышленные; город и устройство эмулируются.</aside>
<form id="signup"><label>Номер профиля<input name="profile_id" type="number" required></label><label>Пароль фикстуры<input name="password" type="password" required></label><button type="submit">Создать тестовый аккаунт</button></form><p id="result" role="status"></p>
<script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();const out=document.querySelector('#result');try{const f=new FormData(e.target);const g=await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,reject,{timeout:5000}));const body={profile_id:Number(f.get('profile_id')),password:f.get('password'),width:innerWidth,height:innerHeight,touch:navigator.maxTouchPoints,latitude:g.coords.latitude,longitude:g.coords.longitude,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,dpr:devicePixelRatio};const r=await fetch('/api/simulation/site/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error('Регистрация не выполнена');const me=await fetch('/api/simulation/site/me');if(!me.ok)throw Error('Вход не подтверждён');const data=await me.json();out.textContent='Тестовый вход подтверждён: '+data.email;out.dataset.verified='true';}catch(err){out.textContent='Ошибка симуляции';out.dataset.error='true';}};</script></html>'''


@router.get('/site/register', response_class=HTMLResponse)
def register_page():
    require_mode()
    return HTMLResponse(SITE, headers={'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'"})


async def register_one(pid, origin, fleet_run_id=None):
    init()
    origin = local_origin(origin)
    profile = claim_profile(pid, 'registering', fleet_run_id=fleet_run_id)
    denied = []
    context = None
    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(str(db.DATA / 'simulation-browsers' / str(pid)),
                headless=True, **device_options(pw, profile))
            denied = await install_guard(context, origin)
            page = context.pages[0] if context.pages else await context.new_page()
            # Navigation and form requests share the same approved-origin, no-redirect transport.
            response = await page.goto(origin + '/api/simulation/site/meta', timeout=15000)
            if not response or response.status != 200 or (await response.json()).get('marker') != MARKER:
                raise ValueError('Not our simulator')
            identity = await browser_identity(page, origin, profile, allow_signed_out=True)
            saved = identity is not None
            if not saved:
                await page.goto(origin + '/api/simulation/site/register', timeout=15000)
                await page.locator('[name=profile_id]').fill(str(pid))
                await page.locator('[name=password]').fill(password_for(pid))
                await page.locator('button[type=submit]').click()
                await page.locator('#result[data-verified=true]').wait_for(timeout=15000)
                identity = await browser_identity(page, origin, profile)
            if denied:
                raise ValueError('Network check failed')
            observed = identity['observations']
            await context.close()
            context = None
        db.execute("UPDATE simulation_profiles SET status='registered',observed=?,message=?,lease_until=0,updated_at=? WHERE id=?",
                   (json.dumps(observed), 'Тестовый аккаунт создан, вход и среда проверены в локальном браузере.', db.now(), pid))
        if saved:
            event(pid, 'session_verified')
        return {'profile_id': pid, 'status': 'registered', 'observed': observed, 'used_saved_session': saved}
    except (Exception, asyncio.CancelledError) as error:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        db.execute("UPDATE simulation_profiles SET status='failed',message=?,lease_until=0,updated_at=? WHERE id=?",
                   ('Симуляция остановлена. Проверьте локальный сервер и установленный Chromium.', db.now(), pid))
        if isinstance(error, asyncio.CancelledError):
            raise
        return {'profile_id': pid, 'status': 'failed'}


@router.post('/register')
async def register(payload: RunInput, request: Request):
    init()
    origin = local_origin(str(request.base_url))
    found = {r['id'] for r in db.rows('SELECT id FROM simulation_profiles')}
    if not set(payload.profile_ids) <= found:
        raise HTTPException(404, 'Один из тестовых профилей не найден.')
    results = []
    for pid in payload.profile_ids:
        try:
            results.append(await register_one(pid, origin))
        except HTTPException as exc:
            results.append({'profile_id': pid, 'status': 'blocked', 'message': str(exc.detail), 'code': exc.status_code})
    return {'results': results}


def event(pid, action):
    db.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (?,?,?)', (pid, action, db.now()))


def claim_profile(pid, stage, lease=90, required_status=None, fleet_run_id=None):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM simulation_profiles WHERE id=?', (pid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Тестовый профиль не найден.')
        if row['lease_until'] > time.time():
            raise HTTPException(409, 'Этот тестовый профиль уже занят. Дождитесь завершения операции.')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='permanent_mail_accounts'").fetchone():
            mailbox = c.execute('SELECT status,login_verified FROM permanent_mail_accounts WHERE profile_id=?', (pid,)).fetchone()
            if mailbox and (not mailbox['login_verified'] or mailbox['status'] not in ('created', 'attached')):
                raise HTTPException(409, 'Постоянная почта этого бота ещё не создана или вход не проверен.')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='simulation_fleet_members'").fetchone():
            member = c.execute("""SELECT m.run_id FROM simulation_fleet_members m JOIN simulation_fleet_runs r ON r.id=m.run_id
                WHERE m.profile_id=? AND m.status IN ('queued','running') AND r.status IN ('running','stopping')""", (pid,)).fetchone()
            if member and member['run_id'] != fleet_run_id:
                raise HTTPException(409, 'Этот бот уже находится в работающей партии.')
        if required_status and row['status'] != required_status:
            raise HTTPException(409, 'Сначала выполните авторег или проверьте сохранённый вход выбранного профиля.')
        c.execute('UPDATE simulation_profiles SET status=?,lease_until=?,message=?,updated_at=? WHERE id=?',
                  (stage, time.time() + lease, '', db.now(), pid))
        return dict(row)


async def install_guard(context, origin):
    denied = []
    async def guard(route):
        if route.request.url == origin + '/favicon.ico':
            await route.fulfill(status=204)
        elif allowed_request(route.request.url, origin):
            response = await route.fetch(max_redirects=0, timeout=15000)
            if 300 <= response.status < 400:
                denied.append(True)
                await route.abort()
            else:
                await route.fulfill(response=response)
        else:
            denied.append(True)
            await route.abort()
    await context.route('**/*', guard)
    async def no_socket(ws):
        denied.append(True)
        await ws.close()
    await context.route_web_socket('**/*', no_socket)
    return denied


async def browser_identity(page, origin, profile, allow_signed_out=False):
    response = await page.goto(origin + '/api/simulation/site/me', timeout=15000)
    if response and response.status == 401 and allow_signed_out:
        return None
    if not response or response.status != 200:
        raise ValueError('Session absent')
    identity = await response.json()
    if identity.get('profile_id') != profile['id'] or identity.get('email') != profile['email']:
        raise ValueError('Wrong profile session')
    return identity


@asynccontextmanager
async def browser_session(profile, origin, headless=True):
    origin = local_origin(origin)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(db.DATA / 'simulation-browsers' / str(profile['id'])),
            headless=headless, **device_options(pw, profile))
        try:
            denied = await install_guard(context, origin)
            page = context.pages[0] if context.pages else await context.new_page()
            response = await page.goto(origin + '/api/simulation/site/meta', timeout=15000)
            if not response or response.status != 200 or (await response.json()).get('marker') != MARKER:
                raise ValueError('Not our simulator')
            yield context, page, denied
            if denied:
                raise ValueError('Unexpected request')
        finally:
            await context.close()


async def verify_one(pid, origin):
    init()
    profile = claim_profile(pid, 'verifying')
    try:
        async with browser_session(profile, origin) as (_, page, _denied):
            await browser_identity(page, origin, profile)
        db.execute("UPDATE simulation_profiles SET status='registered',lease_until=0,message=?,updated_at=? WHERE id=?",
                   ('Сохранённый вход проверен в новом процессе браузера без повторной регистрации.', db.now(), pid))
        event(pid, 'session_verified')
        return {'profile_id': pid, 'status': 'registered', 'used_saved_session': True}
    except (Exception, asyncio.CancelledError) as error:
        db.execute("UPDATE simulation_profiles SET status='failed',lease_until=0,message=?,updated_at=? WHERE id=?",
                   ('Сохранённая сессия не прошла проверку. Выполните авторег тестового профиля заново.', db.now(), pid))
        if isinstance(error, asyncio.CancelledError):
            raise
        return {'profile_id': pid, 'status': 'failed'}


@router.post('/verify')
async def verify(payload: RunInput, request: Request):
    init()
    origin = local_origin(str(request.base_url))
    found = {r['id'] for r in db.rows('SELECT id FROM simulation_profiles')}
    if not set(payload.profile_ids) <= found:
        raise HTTPException(404, 'Один из тестовых профилей не найден.')
    return {'results': [await verify_one(pid, origin) for pid in payload.profile_ids]}
