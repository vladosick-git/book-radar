"""Provision domain mailboxes through Zoho's documented administrator API.

Preparing is local. Only an explicit run transmits credentials to Zoho.
An uncertain POST outcome is never retried automatically.
"""
import asyncio
import os
import re
import secrets
import string
import time
import uuid
from contextlib import contextmanager
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator

from . import db, preparation, config
from .protection import protect, unprotect, PREFIX, VAULT_PREFIX

REGIONS = {'eu': 'https://mail.zoho.eu', 'us': 'https://mail.zoho.com', 'in': 'https://mail.zoho.in'}
PUBLIC_COLUMNS = '''m.id,m.account_id,a.profile_id,m.provider,m.source,m.region,m.zoid,m.email,
    CASE WHEN m.source='manual' THEN 'attached' ELSE m.status END AS status,
    CASE WHEN m.source='manual' THEN 'attached' ELSE m.status END AS creation_status,
    m.zoid AS organization_id,m.remote_account_id AS external_id,
    m.remote_account_id,m.error,m.error_code,m.password_state,m.reconciled_at,m.created_at,m.updated_at'''
PROVIDERS = {'Yandex': 'https://mail.yandex.ru', 'Zoho': 'https://mail.zoho.com', 'Tuta': 'https://app.tuta.com',
    'Proton': 'https://account.proton.me', 'GMX': 'https://www.gmx.com',
    'Rambler': 'https://mail.rambler.ru', 'Mail.ru': 'https://account.mail.ru',
    'OpenMailBox': 'https://openmailbox.org', 'Mailcow': 'https://mailcow.email', 'Other': 'https://example.org'}
LEASE_SECONDS = 120


def migrate(connection):
    connection.execute('BEGIN IMMEDIATE')
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(mailboxes)')}
    for name, definition in {
        'provider': "TEXT NOT NULL DEFAULT 'Zoho'", 'source': "TEXT NOT NULL DEFAULT 'api'",
        'error_code': "TEXT NOT NULL DEFAULT ''", 'password_state': "TEXT NOT NULL DEFAULT 'generated'",
        'reconciled_at': 'TEXT',
    }.items():
        if name not in columns:
            connection.execute(f'ALTER TABLE mailboxes ADD COLUMN {name} {definition}')
    connection.execute('''CREATE TABLE IF NOT EXISTS mail_operations (
        id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, kind TEXT NOT NULL, expires_at REAL NOT NULL)''')
    connection.execute('DELETE FROM mail_operations WHERE expires_at<=?', (time.time(),))
    if not connection.execute('SELECT 1 FROM mail_operations').fetchone():
        connection.execute("""UPDATE mailboxes SET status='unknown',error_code='INTERRUPTED',
            error='Программа завершилась во время запроса. Сначала выполните сверку с Zoho.',updated_at=?
            WHERE status='creating'""", (db.now(),))
    connection.execute("UPDATE mailboxes SET password_state='provider_confirmed' WHERE status='created' AND source='api' AND password_state='generated'")
    # Repair only the automatic confirmation produced by older manual-attach code.
    # A later explicit owner's edit has its own updated_at and remains untouched.
    connection.execute("""UPDATE accounts SET status='in_progress' WHERE status='confirmed' AND id IN (
        SELECT m.account_id FROM mailboxes m WHERE m.source='manual'
        AND accounts.confirmed_at=m.created_at AND accounts.updated_at=m.updated_at)""")
    connection.execute("UPDATE mailboxes SET status='prepared' WHERE source='manual' AND status='created'")


def storage_ready():
    if config.SERVER and (not config.AUTH or not config.VAULT_KEY_FILE):
        raise HTTPException(503, 'Для серверной почты нужны вход администратора и RADAR_VAULT_KEY_FILE.')
    if os.name != 'nt' and not config.VAULT_KEY_FILE:
        raise HTTPException(503, 'Задайте RADAR_VAULT_KEY_FILE: почтовые пароли нельзя хранить открытым текстом.')
    try:
        protect(b'storage-probe')
    except Exception:
        raise HTTPException(503, 'Ключ хранилища недоступен или повреждён. Почтовые данные не изменены.')


def operation_active():
    return bool(db.rows('SELECT 1 FROM mail_operations WHERE expires_at>?', (time.time(),)))


def operation_kind():
    rows = db.rows('SELECT kind FROM mail_operations WHERE expires_at>?', (time.time(),))
    return rows[0]['kind'] if rows else None


def claim_operation(kind):
    owner = uuid.uuid4().hex
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT 1 FROM mail_operations WHERE expires_at>?', (time.time(),)).fetchone():
            raise HTTPException(409, 'Почтовая операция уже выполняется. Дождитесь её завершения.')
        connection.execute('DELETE FROM mail_operations')
        connection.execute("UPDATE mailboxes SET status='unknown',error_code='INTERRUPTED',error='Предыдущий запрос прерван. Выполните сверку с Zoho.',updated_at=? WHERE status='creating'", (db.now(),))
        connection.execute('INSERT INTO mail_operations VALUES (1,?,?,?)', (owner, kind, time.time() + LEASE_SECONDS))
    return owner


def release_operation(owner):
    db.execute('DELETE FROM mail_operations WHERE owner=?', (owner,))


async def heartbeat(owner):
    while True:
        await asyncio.sleep(5)
        db.execute('UPDATE mail_operations SET expires_at=? WHERE owner=?', (time.time() + LEASE_SECONDS, owner))


@contextmanager
def local_operation(kind):
    owner = claim_operation(kind)
    try:
        yield
    finally:
        release_operation(owner)


class PrepareInput(BaseModel):
    profile_ids: list[int] = Field(min_length=1, max_length=10)
    domain: str = Field(min_length=3, max_length=253)
    prefix: str = Field(default='reader', min_length=1, max_length=30)
    zoid: str = Field(pattern=r'^\d{1,30}$')
    region: Literal['eu', 'us', 'in'] = 'eu'

    @field_validator('profile_ids')
    @classmethod
    def unique_profiles(cls, values):
        if any(value < 1 for value in values) or len(set(values)) != len(values):
            raise ValueError('Выберите от одного до десяти разных профилей.')
        return values

    @field_validator('domain')
    @classmethod
    def domain_name(cls, value):
        value = value.strip().lower().encode('idna').decode('ascii')
        if len(value) > 253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.')):
            raise ValueError('Укажите домен, например example.org, без URL и символа @.')
        return value

    @field_validator('prefix')
    @classmethod
    def login_prefix(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z][a-z0-9]{0,29}', value):
            raise ValueError('Префикс: латинские буквы и цифры, первая — буква.')
        return value


class RunInput(BaseModel):
    mailbox_ids: list[int] = Field(min_length=1, max_length=10)
    access_token: SecretStr


class ReconcileInput(BaseModel):
    access_token: SecretStr


class CreateResult(BaseModel):
    id: int
    outcome: Literal['created', 'rejected', 'unknown', 'not_started']
    status: str
    error_code: str


class ReconcileResult(BaseModel):
    id: int
    outcome: Literal['confirmed', 'not_found', 'inactive_or_unconfirmed', 'limit_reached', 'unavailable']
    status: str


class AttachInput(BaseModel):
    profile_id: int = Field(ge=1)
    provider: Literal['Yandex', 'Zoho', 'Tuta', 'Proton', 'GMX', 'Rambler', 'Mail.ru', 'OpenMailBox', 'Mailcow', 'Other']
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr | None = None
    webmail_url: str = Field(default='', max_length=500)

    @field_validator('webmail_url')
    @classmethod
    def webmail(cls, value):
        if not value:
            return ''
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Для почтового интерфейса нужен HTTPS-адрес без учётных данных.')
        return value.rstrip('/')

    @field_validator('email')
    @classmethod
    def email_address(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9._+-]{0,63}@[a-z0-9.-]+', value):
            raise ValueError('Укажите полный почтовый адрес латиницей.')
        local, domain = value.split('@')
        PrepareInput.domain_name(domain)
        if '..' in local or local.endswith('.'):
            raise ValueError('Некорректный почтовый адрес.')
        return value

    @field_validator('password')
    @classmethod
    def secret_length(cls, value):
        if value is not None and not 1 <= len(value.get_secret_value()) <= 256:
            raise ValueError('Пароль должен содержать от 1 до 256 символов.')
        return value


def public_rows():
    rows = db.rows(f'SELECT {PUBLIC_COLUMNS} FROM mailboxes m JOIN accounts a ON a.id=m.account_id ORDER BY m.id')
    for row in rows:
        row['provisioned'] = row['source'] == 'api' and row['status'] == 'created'
        row['login_verified'] = False  # This registry does not perform IMAP checks.
    if db.rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name='real_mail_links'"):
        verified = {r['mailbox_id']: r for r in db.rows('''SELECT l.mailbox_id,m.login_verified,m.status,m.source FROM real_mail_links l
            JOIN permanent_mail_accounts m ON m.id=l.mid WHERE l.mailbox_id IS NOT NULL''')}
        for row in rows:
            proof = verified.get(row['id'])
            if proof:
                row['login_verified'] = bool(proof['login_verified'])
                row['provisioned'] = proof['source'] == 'api' and proof['status'] == 'created'
                row['creation_status'] = proof['status']
    return rows


def prepare(payload):
    storage_ready()
    with local_operation('prepare'):
        return _prepare(payload)


def _prepare(payload):
    # Validate the entire batch before writing; any conflict rolls it all back.
    with db.connect() as connection:
        placeholders = ','.join('?' for _ in payload.profile_ids)
        profiles = {row['id']: row['name'] for row in connection.execute(f'SELECT id,name FROM profiles WHERE id IN ({placeholders})', payload.profile_ids)}
        if set(profiles) != set(payload.profile_ids):
            raise HTTPException(404, 'Один из профилей не найден.')
        name = f'Zoho Mail · {payload.domain}'
        row = connection.execute('SELECT * FROM services WHERE name_key=?', (name.casefold(),)).fetchone()
        if row and (row['kind'] != 'email' or row['registration_url'] != REGIONS[payload.region]):
            raise HTTPException(409, 'Сервис для этого домена уже задан с другим регионом или адресом.')
        if row:
            service_id = row['id']
        else:
            service_id = connection.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
                (name, name.casefold(), 'email', REGIONS[payload.region], db.now())).lastrowid
        ids = []
        for profile_id in payload.profile_ids:
            email = f'{payload.prefix}{profile_id:02d}@{payload.domain}'
            timestamp = db.now()
            connection.execute('INSERT OR IGNORE INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)',
                (profile_id, service_id, timestamp, timestamp))
            account = connection.execute('SELECT * FROM accounts WHERE profile_id=? AND service_id=?', (profile_id, service_id)).fetchone()
            existing = connection.execute('SELECT * FROM mailboxes WHERE account_id=? OR email=?', (account['id'], email)).fetchone()
            if existing:
                if existing['source'] != 'api' or (existing['account_id'], existing['email'], existing['region'], existing['zoid']) != (account['id'], email, payload.region, payload.zoid):
                    raise HTTPException(409, 'Профиль или адрес уже подготовлен для другой конфигурации. Существующая запись сохранена.')
                ids.append(existing['id'])
                continue
            if account['status'] != 'planned' or account['identifier']:
                raise HTTPException(409, 'В профиле уже есть заполненная запись почты. Она сохранена; выберите другой профиль.')
            alphabet = string.ascii_letters + string.digits + '!@#_-'
            password = 'A9a!' + ''.join(secrets.choice(alphabet) for _ in range(24))
            cursor = connection.execute('''INSERT INTO mailboxes(account_id,region,zoid,email,password_cipher,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)''', (account['id'], payload.region, payload.zoid, email, protect(password.encode('utf-8')), timestamp, timestamp))
            connection.execute('UPDATE accounts SET identifier=?,updated_at=? WHERE id=?', (email, timestamp, account['id']))
            ids.append(cursor.lastrowid)
    db.log(None, 'info', f'Подготовлено ящиков Zoho: {len(ids)}. Запросы регистрации ещё не отправлены.', source='mail')
    return {'ids': ids, 'mailboxes': [row for row in public_rows() if row['id'] in ids]}


def token_value(secret):
    token = secret.get_secret_value().strip()
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise HTTPException(400, 'Введите действующий OAuth access token без пробелов.')
    return token


def select_for_run(payload):
    storage_ready()
    token = token_value(payload.access_token)
    if len(set(payload.mailbox_ids)) != len(payload.mailbox_ids):
        raise HTTPException(400, 'Выберите разные ящики.')
    placeholders = ','.join('?' for _ in payload.mailbox_ids)
    rows = db.rows(f'SELECT * FROM mailboxes WHERE id IN ({placeholders}) ORDER BY id', payload.mailbox_ids)
    if len(rows) != len(payload.mailbox_ids):
        raise HTTPException(404, 'Один из ящиков не найден.')
    if any(row['source'] != 'api' or row['status'] not in ('prepared', 'failed') for row in rows):
        raise HTTPException(409, 'Запуск доступен только подготовленным ящикам или после явного отказа. Неизвестный результат сначала проверьте в Zoho.')
    if len({(row['region'], row['zoid']) for row in rows}) != 1:
        raise HTTPException(400, 'За один запуск выберите ящики одной организации и региона.')
    return rows, token


def set_status(mailbox_id, status, error='', remote_id=None, error_code=''):
    db.execute('UPDATE mailboxes SET status=?,error=?,error_code=?,remote_account_id=COALESCE(?,remote_account_id),updated_at=? WHERE id=?',
        (status, error, error_code, remote_id, db.now(), mailbox_id))


async def create_one(client, row, token):
    current = db.rows('SELECT status,source FROM mailboxes WHERE id=?', (row['id'],))
    if not current or current[0]['source'] != 'api' or current[0]['status'] not in ('prepared', 'failed'):
        return False
    # Read the durable secret before entering the potentially side-effecting state.
    try:
        password = read_password(row)
    except Exception:
        db.execute("UPDATE mailboxes SET status='failed',error_code='VAULT_UNREADABLE',error='Не удалось прочитать пароль. Проверьте исходный ключ хранилища или учётную запись Windows.',updated_at=? WHERE id=? AND source='api' AND status IN ('prepared','failed')", (db.now(), row['id']))
        return False
    with db.connect() as connection:
        changed = connection.execute("UPDATE mailboxes SET status='creating',error='',error_code='',updated_at=? WHERE id=? AND status IN ('prepared','failed') AND source='api'", (db.now(), row['id'])).rowcount
    if not changed:
        return False
    db.execute("UPDATE accounts SET status='in_progress',updated_at=? WHERE id=?", (db.now(), row['account_id']))
    try:
        response = await client.post(f"{REGIONS[row['region']]}/api/organization/{row['zoid']}/accounts",
            headers={'Authorization': f'Zoho-oauthtoken {token}', 'Accept': 'application/json'},
            json={'primaryEmailAddress': row['email'], 'password': password,
                  'displayName': row['email'].split('@')[0], 'role': 'member', 'oneTimePassword': False})
        # Provider output can contain credentials: never put it into logs or the UI.
        if response.status_code in (400, 401, 403, 404, 409, 422, 429):
            message = {401: 'OAuth token истёк или неверен.', 403: 'Нет права создавать пользователей или доступ запрещён.',
                       409: 'Адрес уже существует. Проверьте его в организации.', 429: 'Ограничение запросов Zoho. Повторите позднее.'}.get(response.status_code,
                       'Zoho отклонил запрос. Проверьте домен, свободные лицензии и параметры в панели администратора.')
            set_status(row['id'], 'unknown' if response.status_code == 409 else 'failed', f'{message} HTTP {response.status_code}.', error_code=f'HTTP_{response.status_code}')
            return False
        body = response.json()
        data = body.get('data', {})
        remote_id = str(data.get('accountId', '')) if isinstance(data, dict) else ''
        success = (response.status_code in (200, 201) and body.get('status', {}).get('code') == 201
            and isinstance(data, dict) and data.get('primaryEmailAddress', '').casefold() == row['email'].casefold()
            and remote_id.isdigit() and data.get('mailboxStatus') == 'enabled')
        if not success:
            set_status(row['id'], 'unknown', 'Ответ не подтвердил создание активного ящика. Выполните сверку с Zoho; повторная отправка заблокирована.', error_code='UNCONFIRMED_RESPONSE')
            return False
        set_status(row['id'], 'created', remote_id=remote_id)
        db.execute("UPDATE mailboxes SET password_state='provider_confirmed' WHERE id=?", (row['id'],))
        return True
    except asyncio.CancelledError:
        set_status(row['id'], 'unknown', 'Запрос был прерван. Ящик мог создаться — сначала выполните сверку с Zoho.', error_code='INTERRUPTED')
        raise
    except Exception:
        # Timeout, 5xx, malformed JSON and broken connections can follow creation.
        set_status(row['id'], 'unknown', 'Связь или ответ не подтвердили результат. Ящик мог создаться — сначала выполните сверку с Zoho.', error_code='TRANSPORT_OR_RESPONSE')
        return False


async def run_batch(rows, token):
    created = 0
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            for row in rows:
                if (await adapter.create(client, row, token)).outcome != 'created':
                    break
                created += 1
                await asyncio.sleep(1)
    finally:
        db.log(None, 'info', f'Запуск Zoho завершён: API подтвердил ящиков — {created} из {len(rows)}. Остальные смотрите в разделе «Почта».', source='mail')


def read_password(row):
    cipher = row['password_cipher']
    if not cipher.startswith((PREFIX, VAULT_PREFIX)):
        raise RuntimeError('Unencrypted mailbox password refused')
    password = unprotect(cipher).decode('utf-8')
    if config.VAULT_KEY_FILE and cipher.startswith(PREFIX):
        db.execute('UPDATE mailboxes SET password_cipher=? WHERE id=? AND password_cipher=?', (protect(password.encode()), row['id'], cipher))
    return password


def attach(payload):
    storage_ready()
    password = payload.password.get_secret_value() if payload.password else None
    cipher = protect(password.encode()) if password else b''
    with local_operation('attach'), db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute('SELECT 1 FROM profiles WHERE id=?', (payload.profile_id,)).fetchone():
            raise HTTPException(404, 'Профиль не найден.')
        existing = connection.execute('SELECT m.*,a.profile_id FROM mailboxes m JOIN accounts a ON a.id=m.account_id WHERE m.email=?', (payload.email,)).fetchone()
        if existing:
            if existing['profile_id'] != payload.profile_id or existing['provider'] != payload.provider or existing['source'] != 'manual':
                raise HTTPException(409, 'Этот адрес уже связан с другой записью. Данные сохранены.')
            if password:
                try:
                    same = bool(existing['password_cipher']) and unprotect(existing['password_cipher']).decode() == password
                except Exception:
                    raise HTTPException(400, 'Не удалось прочитать существующий пароль. Проверьте исходный ключ хранилища.')
                if not same:
                    raise HTTPException(409, 'Повтор подключения не изменяет пароль. Существующие данные сохранены.')
            return {'id': existing['id'], 'status': 'attached'}
        timestamp = db.now()
        name = f'{payload.provider} · готовая почта'
        webmail_url = PROVIDERS[payload.provider]
        if payload.provider in ('Mailcow', 'Other') and payload.webmail_url:
            webmail_url = payload.webmail_url
            from urllib.parse import urlsplit
            name = f'{payload.provider} · {urlsplit(webmail_url).hostname}'
        connection.execute('INSERT OR IGNORE INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
            (name, name.casefold(), 'email', webmail_url, timestamp))
        service = connection.execute('SELECT * FROM services WHERE name_key=?', (name.casefold(),)).fetchone()
        if service['kind'] != 'email' or service['registration_url'] != webmail_url:
            raise HTTPException(409, 'Сервис с таким названием уже задан с другими параметрами.')
        connection.execute('INSERT OR IGNORE INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)',
            (payload.profile_id, service['id'], timestamp, timestamp))
        account = connection.execute('SELECT * FROM accounts WHERE profile_id=? AND service_id=?', (payload.profile_id, service['id'])).fetchone()
        if connection.execute('SELECT 1 FROM mailboxes WHERE account_id=?', (account['id'],)).fetchone() or account['identifier']:
            raise HTTPException(409, 'У профиля уже есть почта этого провайдера. Запись не изменена.')
        # Internal CHECK remains compatible with 0.6. Public status is derived from source=manual.
        mailbox_id = connection.execute('''INSERT INTO mailboxes(account_id,provider,source,region,zoid,email,password_cipher,
            status,password_state,created_at,updated_at) VALUES (?,?,'manual','','',?,?,'prepared',?,?,?)''',
            (account['id'], payload.provider, payload.email, cipher, 'owner_supplied' if password else 'not_stored', timestamp, timestamp)).lastrowid
        connection.execute("UPDATE accounts SET identifier=?,status='in_progress',updated_at=? WHERE id=?",
            (payload.email, timestamp, account['id']))
    db.log(None, 'info', f'Готовый ящик подключён владельцем к профилю #{payload.profile_id}. Создание и доставка сервисом не проверялись.', source='mail', profile_id=payload.profile_id)
    return {'id': mailbox_id, 'status': 'attached'}


async def reconcile_one(client, mailbox_id, token):
    rows = db.rows('SELECT * FROM mailboxes WHERE id=?', (mailbox_id,))
    if not rows:
        raise HTTPException(404, 'Ящик не найден.')
    row = rows[0]
    if row['source'] != 'api' or row['status'] not in ('unknown', 'failed'):
        raise HTTPException(409, 'Сверка нужна только для отказа или неопределённого результата Zoho.')
    outcome = 'not_found'
    try:
        for page in range(10):
            response = await client.get(f"{REGIONS[row['region']]}/api/organization/{row['zoid']}/accounts",
                params={'start': 1 + page * 100, 'limit': 100},
                headers={'Authorization': f'Zoho-oauthtoken {token}', 'Accept': 'application/json'})
            body = response.json()
            if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get('status'), dict) or body['status'].get('code') != 200:
                raise ValueError('unconfirmed read')
            data = body.get('data')
            records = [data] if isinstance(data, dict) else data
            if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
                raise ValueError('malformed read')
            matches = [record for record in records if isinstance(record.get('primaryEmailAddress'), str)
                and record['primaryEmailAddress'].casefold() == row['email'].casefold()]
            if len(matches) > 1:
                raise ValueError('ambiguous identity')
            if matches:
                match = matches[0]
                remote_id = str(match.get('accountId', ''))
                if not remote_id.isdigit() or match.get('mailboxStatus') != 'enabled':
                    outcome = 'inactive_or_unconfirmed'
                    break
                set_status(row['id'], 'created', remote_id=remote_id)
                # GET proves the mailbox exists, but cannot prove its password matches our saved one.
                db.execute("UPDATE mailboxes SET password_state='unverified',reconciled_at=? WHERE id=?", (db.now(), row['id']))
                db.execute("UPDATE accounts SET status=CASE WHEN status='planned' THEN 'in_progress' ELSE status END,updated_at=? WHERE id=?", (db.now(), row['account_id']))
                db.log(None, 'info', f'Сверка Zoho подтвердила ящик #{mailbox_id}. Совпадение пароля и доставка не проверялись.', source='mail')
                return {'id': mailbox_id, 'outcome': 'confirmed', 'status': 'created'}
            if len(records) < 100:
                break
        else:
            outcome = 'limit_reached'
    except asyncio.CancelledError:
        raise
    except Exception:
        outcome = 'unavailable'
    # Absence in a read can be temporary: it NEVER authorizes replaying an uncertain POST.
    message = {'not_found': 'Адрес не найден при чтении. Неопределённый запрос повторять нельзя: нужна проверка владельцем.',
        'inactive_or_unconfirmed': 'Адрес найден, но активный независимый ящик не подтверждён.',
        'limit_reached': 'Достигнут лимит сверки: просмотрено до 1000 пользователей. Нужна проверка владельцем.',
        'unavailable': 'Сверка недоступна. Проверьте токен, право READ и доступность Zoho. Ответ провайдера не сохранялся.'}[outcome]
    with db.connect() as connection:
        connection.execute("UPDATE mailboxes SET error=?,error_code=?,reconciled_at=?,updated_at=?,status=CASE WHEN ?='inactive_or_unconfirmed' THEN 'unknown' ELSE status END WHERE id=?",
            (message, f'RECONCILE_{outcome.upper()}', db.now(), db.now(), outcome, mailbox_id))
    return {'id': mailbox_id, 'outcome': outcome, 'status': db.rows('SELECT status FROM mailboxes WHERE id=?', (mailbox_id,))[0]['status']}


def credentials(mailbox_id):
    storage_ready()
    rows = db.rows('SELECT id,email,password_cipher,password_state FROM mailboxes WHERE id=?', (mailbox_id,))
    if not rows:
        raise HTTPException(404, 'Ящик не найден.')
    try:
        if not rows[0]['password_cipher']:
            raise HTTPException(404, 'Пароль этого ящика не сохранён. Он хранится у владельца.')
        return {'email': rows[0]['email'], 'password': read_password(rows[0]), 'password_state': rows[0]['password_state']}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, 'Не удалось прочитать пароль. Проверьте исходный ключ хранилища или учётную запись Windows.')


class ZohoAdapter:
    """Stable stage-01 integration boundary. Secrets never belong to its result models."""
    def prepare(self, payload: PrepareInput) -> dict:
        return prepare(payload)

    async def create(self, client, row: dict, token: str) -> CreateResult:
        performed = await create_one(client, row, token)
        current = db.rows('SELECT status,error_code FROM mailboxes WHERE id=?', (row['id'],))[0]
        outcome = 'created' if performed else 'rejected' if current['status'] == 'failed' else 'unknown' if current['status'] == 'unknown' else 'not_started'
        return CreateResult(id=row['id'], outcome=outcome, **current)

    async def reconcile(self, client, mailbox_id: int, token: str) -> ReconcileResult:
        return ReconcileResult(**await reconcile_one(client, mailbox_id, token))


adapter = ZohoAdapter()
