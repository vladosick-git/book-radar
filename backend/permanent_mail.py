"""Permanent domain mailboxes and an encrypted mixed-provider registry.

The external provider is contacted only by explicit connection/create/verify actions.
Book browsers retain their local-only request guard.
"""
import asyncio
import json
import secrets
import time
from urllib.parse import urlsplit
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, SecretStr, field_validator

from . import db, simulation as sim, simulation_batch as batch, mailcow_client as provider, mail_registration
from .protection import protect, unprotect

router = APIRouter(prefix='/api/simulation/mail', dependencies=[Depends(sim.require_mode)])
task = None
stop_requested = False
FIRST = [('Иван', 'ivan'), ('Алексей', 'alexey'), ('Дмитрий', 'dmitry'), ('Михаил', 'mikhail'), ('Андрей', 'andrey'), ('Сергей', 'sergey'), ('Павел', 'pavel'), ('Никита', 'nikita'), ('Антон', 'anton'), ('Егор', 'egor')]
LAST = [('Петров', 'petrov'), ('Иванов', 'ivanov'), ('Соколов', 'sokolov'), ('Смирнов', 'smirnov'), ('Волков', 'volkov'), ('Лебедев', 'lebedev'), ('Орлов', 'orlov'), ('Морозов', 'morozov'), ('Егоров', 'egorov'), ('Макаров', 'makarov')]
PUBLIC = 'id,profile_id,email,display_name,provider,source,status,login_verified,message,imap_host,created_at,updated_at'


def init():
    sim.init_storage()
    with db.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS permanent_mail_config (
          id INTEGER PRIMARY KEY CHECK(id=1),origin TEXT NOT NULL,domain TEXT NOT NULL,
          key_cipher BLOB NOT NULL,quota INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS permanent_mail_accounts (
          id INTEGER PRIMARY KEY,profile_id INTEGER UNIQUE REFERENCES simulation_profiles(id),
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,display_name TEXT NOT NULL,provider TEXT NOT NULL,
          source TEXT NOT NULL,status TEXT NOT NULL,login_verified INTEGER NOT NULL DEFAULT 0,
          secret_cipher BLOB NOT NULL,imap_host TEXT NOT NULL,message TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS permanent_mail_operation (
          id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT NOT NULL,expires_at REAL NOT NULL);''')


def storage_ready():
    mail_registration.storage_ready()


def encrypted(value):
    storage_ready()
    return protect(json.dumps(value, ensure_ascii=False).encode())


def decrypted(blob):
    try:
        return json.loads(unprotect(bytes(blob)))
    except Exception:
        raise HTTPException(503, 'Ключ хранилища недоступен. Данные не изменены.') from None


def claim():
    owner = secrets.token_hex(20)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM permanent_mail_operation WHERE expires_at>?', (time.time(),)).fetchone():
            raise HTTPException(409, 'Почтовая операция уже выполняется.')
        c.execute('DELETE FROM permanent_mail_operation')
        c.execute('INSERT INTO permanent_mail_operation VALUES (1,?,?)', (owner, time.time() + 120))
    return owner


def release(owner):
    db.execute('DELETE FROM permanent_mail_operation WHERE owner=?', (owner,))


def configuration():
    rows = db.rows('SELECT * FROM permanent_mail_config')
    if not rows:
        raise HTTPException(409, 'Подключите почтовый сервер и домен. Настоящие ящики ещё не создавались.')
    row = rows[0]
    row['key'] = decrypted(row.pop('key_cipher'))['api_key']
    return row


class ConnectionInput(BaseModel):
    origin: str
    domain: str
    api_key: SecretStr = Field(min_length=8, max_length=500)
    quota: int = Field(default=256, ge=1, le=10240)
    _origin = field_validator('origin')(provider.api_origin)
    _domain = field_validator('domain')(provider.domain_name)


class CreateInput(BaseModel):
    count: int = Field(ge=1, le=500)


class AttachInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(default='', max_length=100)
    provider: Literal['Yandex', 'Mail.ru', 'Tuta', 'Proton', 'Zoho', 'Other'] = 'Yandex'
    password: SecretStr = Field(min_length=1, max_length=500)
    app_password: SecretStr | None = Field(default=None, max_length=500)
    recovery_email: str = Field(default='', max_length=254)
    imap_host: str = ''

    @field_validator('email', 'recovery_email')
    @classmethod
    def email_value(cls, value, info):
        if not value and info.field_name == 'recovery_email':
            return ''
        return batch.MailPoolInput(addresses=[value]).addresses[0]

    @field_validator('imap_host')
    @classmethod
    def host_value(cls, value):
        return provider.domain_name(value) if value else ''


@router.get('/state')
def state():
    init()
    settings = db.rows('SELECT origin,domain,quota,updated_at FROM permanent_mail_config')
    rows = db.rows(f'SELECT {PUBLIC} FROM permanent_mail_accounts ORDER BY id DESC')
    for row in rows:
        row['login'] = row['email']
        row['ready'] = bool(row['login_verified'] and row['status'] in ('created', 'attached'))
    return {'configured': bool(settings), 'settings': settings[0] if settings else None, 'creating_task': task is not None,
            'stopping': task is not None and stop_requested,
            'accounts': rows, 'running': bool(db.rows('SELECT 1 FROM permanent_mail_operation WHERE expires_at>?', (time.time(),))),
            'free': sum(r['ready'] and r['profile_id'] is None for r in rows),
            'linked': sum(r['ready'] and r['profile_id'] is not None for r in rows)}


@router.post('/connect')
async def connect(payload: ConnectionInput):
    init()
    cipher = encrypted({'api_key': payload.api_key.get_secret_value()})
    owner = claim()
    try:
        remote = await provider.Mailcow(payload.origin, payload.api_key.get_secret_value()).domain(payload.domain)
        if not remote or not provider.active(remote):
            raise HTTPException(409, 'Домен не найден или выключен на почтовом сервере.')
        db.execute('''INSERT INTO permanent_mail_config VALUES (1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            origin=excluded.origin,domain=excluded.domain,key_cipher=excluded.key_cipher,quota=excluded.quota,updated_at=excluded.updated_at''',
            (payload.origin, payload.domain, cipher, payload.quota, db.now()))
        return {'connected': True, 'domain': payload.domain}
    except provider.ProviderError:
        raise HTTPException(502, 'Подключение не подтверждено. Проверьте HTTPS, домен, ключ API и разрешённый IP в Mailcow.') from None
    finally:
        release(owner)


def identity(c, domain):
    while True:
        first, last = secrets.choice(FIRST), secrets.choice(LAST)
        login = f'{first[1]}.{last[1]}{10 + secrets.randbelow(9990)}'
        email = f'{login}@{domain}'
        if not c.execute('SELECT 1 FROM permanent_mail_accounts WHERE email=?', (email,)).fetchone() and not c.execute('SELECT 1 FROM simulation_profiles WHERE lower(email)=?', (email,)).fetchone():
            return f'{first[0]} {last[0]}', email


def allocate_verified(c, count):
    rows = c.execute("SELECT id,email,display_name FROM permanent_mail_accounts WHERE profile_id IS NULL AND login_verified=1 AND status IN ('created','attached') ORDER BY id LIMIT ?", (count,)).fetchall()
    if len(rows) < count:
        raise HTTPException(409, f'Нужно {count} свободных проверенных ящиков, доступно {len(rows)}.')
    ids = batch.allocate(c, count)
    for pid, row in zip(ids, rows):
        c.execute('UPDATE simulation_profiles SET email=? WHERE id=?', (row['email'], pid))
        c.execute('UPDATE permanent_mail_accounts SET profile_id=? WHERE id=?', (pid, row['id']))
    return ids


@router.post('/assign', status_code=201)
def assign(payload: CreateInput):
    init()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        ids = allocate_verified(c, payload.count)
    return {'profile_ids': ids}


@router.post('/attach', status_code=201)
def attach(payload: AttachInput):
    init()
    secret = encrypted({'password': payload.password.get_secret_value(),
        'app_password': payload.app_password.get_secret_value() if payload.app_password else '', 'recovery_email': payload.recovery_email})
    # Provider is an explicit label; credentials go nowhere until the owner presses Verify.
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM permanent_mail_accounts WHERE email=?', (payload.email,)).fetchone():
            raise HTTPException(409, 'Этот ящик уже есть. Его данные не перезаписаны.')
        host = payload.imap_host or {'Yandex': 'imap.yandex.ru', 'Mail.ru': 'imap.mail.ru'}.get(payload.provider, '')
        mid = c.execute('''INSERT INTO permanent_mail_accounts(email,display_name,provider,source,status,secret_cipher,imap_host,created_at,updated_at)
            VALUES (?,?,?,'manual','attached',?,?,?,?)''', (payload.email, payload.display_name, payload.provider, secret, host, db.now(), db.now())).lastrowid
    return {'id': mid, 'status': 'attached', 'login_verified': False}


@router.post('/{mid}/credentials')
def credentials(mid: int):
    init()
    rows = db.rows('SELECT email,secret_cipher FROM permanent_mail_accounts WHERE id=?', (mid,))
    if not rows:
        raise HTTPException(404, 'Ящик не найден.')
    from . import audit
    audit.record('mail_credentials_viewed', 'mailbox', mid)
    return {'email': rows[0]['email'], **decrypted(rows[0]['secret_cipher'])}


async def verify_login(mid):
    row = db.rows('SELECT * FROM permanent_mail_accounts WHERE id=?', (mid,))[0]
    secret = decrypted(row['secret_cipher'])
    try:
        if not row['imap_host']:
            raise ValueError()
        await asyncio.to_thread(provider.imap_login, row['imap_host'], row['email'], secret.get('app_password') or secret['password'])
        db.execute('UPDATE permanent_mail_accounts SET login_verified=1,message=?,updated_at=? WHERE id=?', ('Вход IMAP по TLS проверен. Доставка писем отдельно не проверялась.', db.now(), mid))
        return True
    except Exception:
        db.execute('UPDATE permanent_mail_accounts SET login_verified=0,message=?,updated_at=? WHERE id=?', ('Вход не подтверждён. Проверьте IMAP и пароль приложения; сохранённые данные не изменены.', db.now(), mid))
        return False


@router.post('/{mid}/verify')
async def verify(mid: int):
    init()
    rows = db.rows('SELECT status FROM permanent_mail_accounts WHERE id=?', (mid,))
    if not rows:
        raise HTTPException(404, 'Ящик не найден.')
    if rows[0]['status'] not in ('created', 'attached'):
        raise HTTPException(409, 'Сначала создайте ящик или выполните сверку неизвестного результата.')
    owner = claim()
    try:
        return {'verified': await verify_login(mid)}
    finally:
        release(owner)


async def create_one(mid, settings):
    row = db.rows('SELECT * FROM permanent_mail_accounts WHERE id=?', (mid,))[0]
    if row['status'] != 'queued':
        return
    remote = provider.Mailcow(settings['origin'], settings['key'])
    posted = False
    try:
        prior = await remote.mailbox(row['email'])
        if prior:
            raise provider.ProviderError('ADDRESS_ALREADY_EXISTS')
        db.execute("UPDATE permanent_mail_accounts SET status='creating',updated_at=? WHERE id=?", (db.now(), mid))
        posted = True
        await remote.create(row['email'], row['display_name'], decrypted(row['secret_cipher'])['password'], settings['quota'])
        found = await remote.mailbox(row['email'])
        if not found or not provider.active(found):
            raise provider.ProviderError('NOT_CONFIRMED', uncertain=True)
        db.execute("UPDATE permanent_mail_accounts SET status='created',updated_at=? WHERE id=?", (db.now(), mid))
        await verify_login(mid)
        if row['profile_id'] is not None:
            sim.event(row['profile_id'], 'mail_created')
    except (Exception, asyncio.CancelledError) as error:
        uncertain = posted and not (isinstance(error, provider.ProviderError) and not error.uncertain)
        status = 'unknown' if uncertain else 'failed'
        db.execute('UPDATE permanent_mail_accounts SET status=?,login_verified=0,message=?,updated_at=? WHERE id=?',
            (status, 'Результат создания неизвестен. Повтор запрещён: сначала выполните сверку.' if uncertain else 'Ящик не создан. Проверьте подключение, доступный объём и права API.', db.now(), mid))
        if isinstance(error, asyncio.CancelledError):
            raise


async def process(ids, settings, owner):
    global task
    try:
        for mid in ids:
            if stop_requested:
                db.execute("UPDATE permanent_mail_accounts SET status='failed',message='Не отправлено: создание остановлено владельцем.',updated_at=? WHERE status='queued'", (db.now(),))
                break
            db.execute('UPDATE permanent_mail_operation SET expires_at=? WHERE owner=?', (time.time() + 120, owner))
            await create_one(mid, settings)
    finally:
        release(owner)
        task = None


@router.post('/create', status_code=202)
async def create(payload: CreateInput):
    global task, stop_requested
    init()
    settings = configuration()  # No prepared placeholders when there is no real connection.
    storage_ready()
    owner = claim()
    try:
        remote = await provider.Mailcow(settings['origin'], settings['key']).domain(settings['domain'])
        if not remote or not provider.active(remote) or int(remote.get('mboxes_left', 0)) < payload.count:
            raise HTTPException(409, 'На домене недостаточно свободных ящиков или домен выключен. Ничего не создано.')
        ids = []
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            profiles = batch.allocate(c, payload.count)
            for pid in profiles:
                name, email = identity(c, settings['domain'])
                blob = encrypted({'password': 'Aa9!' + secrets.token_urlsafe(20), 'app_password': '', 'recovery_email': ''})
                c.execute('UPDATE simulation_profiles SET email=?,name=? WHERE id=?', (email, f'{name} · {pid}', pid))
                ids.append(c.execute('''INSERT INTO permanent_mail_accounts(profile_id,email,display_name,provider,source,status,secret_cipher,imap_host,created_at,updated_at)
                    VALUES (?,?,?,'Mailcow','api','queued',?,?,?,?)''', (pid, email, name, blob, urlsplit(settings['origin']).hostname, db.now(), db.now())).lastrowid)
        stop_requested = False
        task = asyncio.create_task(process(ids, settings, owner))
        return {'profile_ids': profiles, 'mailbox_ids': ids, 'started': True}
    except provider.ProviderError:
        release(owner)
        raise HTTPException(502, 'Почтовый сервер не подтвердил доступный домен. Ничего не создано.') from None
    except BaseException:
        release(owner)
        raise


@router.post('/stop')
def stop():
    global stop_requested
    init()
    if task:
        stop_requested = True
    return {'stopping': task is not None}


@router.post('/{mid}/reconcile')
async def reconcile(mid: int):
    init()
    rows = db.rows('SELECT * FROM permanent_mail_accounts WHERE id=?', (mid,))
    if not rows or rows[0]['provider'] != 'Mailcow' or rows[0]['status'] != 'unknown':
        raise HTTPException(409, 'Сверка нужна для неизвестного результата создания Mailcow.')
    owner = claim()
    try:
        settings = configuration()
        if rows[0]['email'].split('@')[1] != settings['domain'] or rows[0]['imap_host'] != urlsplit(settings['origin']).hostname:
            raise HTTPException(409, 'Подключён другой почтовый сервер. Верните исходные настройки для сверки.')
        found = await provider.Mailcow(settings['origin'], settings['key']).mailbox(rows[0]['email'])
        if found and provider.active(found) and await verify_login(mid):
            db.execute("UPDATE permanent_mail_accounts SET status='created',updated_at=? WHERE id=?", (db.now(), mid))
            return {'confirmed': True}
        return {'confirmed': False}
    except provider.ProviderError:
        raise HTTPException(502, 'Сверка не завершилась; неизвестный запрос не повторялся.') from None
    finally:
        release(owner)


def recover():
    init()
    db.execute("UPDATE permanent_mail_accounts SET status='unknown',login_verified=0,message='Создание прервано. Сначала выполните сверку.' WHERE status='creating'")
    db.execute("UPDATE permanent_mail_accounts SET status='failed',message='Не отправлено: приложение перезапущено.' WHERE status='queued'")
    db.execute('DELETE FROM permanent_mail_operation')


async def shutdown():
    global task
    if task:
        pending = task
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        task = None
        recover()
