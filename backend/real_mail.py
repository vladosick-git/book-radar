"""Permanent mail for real browser profiles; the provider engine is shared, identities are not."""
import asyncio
import secrets
import time
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from . import audit, db, mail_registration as registry, permanent_mail as mail

router = APIRouter(prefix='/api/mail/permanent')


def init():
    mail.init()
    with db.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS real_mail_links (
            mid INTEGER PRIMARY KEY REFERENCES permanent_mail_accounts(id),
            profile_id INTEGER NOT NULL UNIQUE REFERENCES profiles(id),
            mailbox_id INTEGER REFERENCES mailboxes(id))''')


def normal(mid):
    rows = db.rows('''SELECT m.*,l.profile_id AS normal_profile_id,l.mailbox_id FROM permanent_mail_accounts m
        JOIN real_mail_links l ON l.mid=m.id WHERE m.id=? AND m.profile_id IS NULL''', (mid,))
    if not rows:
        raise HTTPException(404, 'Ящик обычного профиля не найден.')
    return rows[0]


def sync(mid):
    """Link verified credentials to the existing registry, preserving all prior records."""
    row = normal(mid)
    if row['status'] not in ('created', 'attached') or not row['login_verified']:
        return
    if row['mailbox_id']:
        return
    secret = mail.decrypted(row['secret_cipher'])
    result = registry.attach(registry.AttachInput(profile_id=row['normal_profile_id'], provider=row['provider'],
        email=row['email'], password=secret['password'],
        webmail_url=f"https://{row['imap_host']}" if row['provider'] in ('Mailcow', 'Other') and row['imap_host'] else ''))
    db.execute('UPDATE real_mail_links SET mailbox_id=? WHERE mid=?', (result['id'], mid))
    audit.record('permanent_mail_link', 'profile', row['normal_profile_id'], mailbox_id=result['id'])


@router.get('/state')
def state():
    init()
    data = mail.state()
    data['accounts'] = [{**row, 'profile_id': row.pop('normal_profile_id')} for row in db.rows('''SELECT
        m.id,m.email,m.display_name,m.provider,m.source,m.status,m.login_verified,m.message,m.imap_host,m.created_at,m.updated_at,
        l.profile_id AS normal_profile_id,l.mailbox_id FROM permanent_mail_accounts m JOIN real_mail_links l ON l.mid=m.id
        WHERE m.profile_id IS NULL ORDER BY m.id DESC''')]
    for row in data['accounts']:
        row['login'] = row['email']
        row['ready'] = bool(row['login_verified'] and row['status'] in ('created', 'attached'))
    data['free'] = 0
    data['linked'] = sum(row['ready'] for row in data['accounts'])
    return data


@router.post('/connect')
async def connect(payload: mail.ConnectionInput):
    init()
    return await mail.connect(payload)


class AttachInput(mail.AttachInput):
    profile_id: int = Field(ge=1)
    password: SecretStr = Field(min_length=1, max_length=256)


def available_profiles(c, ids):
    for pid in ids:
        if not c.execute('SELECT 1 FROM profiles WHERE id=?', (pid,)).fetchone():
            raise HTTPException(404, 'Профиль не найден. Ничего не создано.')
        if c.execute('SELECT 1 FROM real_mail_links WHERE profile_id=?', (pid,)).fetchone():
            raise HTTPException(409, 'У одного из выбранных ботов уже есть постоянная почта. Существующие данные сохранены.')


@router.post('/attach', status_code=201)
def attach(payload: AttachInput):
    init()
    # No external request. A saved mailbox is not a verified mailbox.
    secret = mail.encrypted({'password': payload.password.get_secret_value(),
        'app_password': payload.app_password.get_secret_value() if payload.app_password else '',
        'recovery_email': payload.recovery_email})
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        available_profiles(c, [payload.profile_id])
        if c.execute('SELECT 1 FROM permanent_mail_accounts WHERE email=?', (payload.email,)).fetchone():
            raise HTTPException(409, 'Этот адрес уже сохранён. Его данные не перезаписаны.')
        host = payload.imap_host or {'Yandex': 'imap.yandex.ru', 'Mail.ru': 'imap.mail.ru'}.get(payload.provider, '')
        mid = c.execute('''INSERT INTO permanent_mail_accounts(email,display_name,provider,source,status,secret_cipher,imap_host,created_at,updated_at)
            VALUES (?,?,?,'manual','attached',?,?,?,?)''', (payload.email, payload.display_name, payload.provider, secret, host, db.now(), db.now())).lastrowid
        c.execute('INSERT INTO real_mail_links(mid,profile_id) VALUES (?,?)', (mid, payload.profile_id))
    audit.record('permanent_mail_attach', 'profile', payload.profile_id, mailbox_id=mid)
    return {'id': mid, 'status': 'attached', 'login_verified': False}


class CreateInput(BaseModel):
    profile_ids: list[int] = Field(min_length=1, max_length=10)

    @field_validator('profile_ids')
    @classmethod
    def ids(cls, values):
        if len(set(values)) != len(values) or any(value < 1 for value in values):
            raise ValueError('Выберите от одного до десяти разных профилей.')
        return values


async def process(ids, settings, owner):
    try:
        for mid in ids:
            if mail.stop_requested:
                break
            db.execute('UPDATE permanent_mail_operation SET expires_at=? WHERE owner=?', (time.time() + 120, owner))
            await mail.create_one(mid, settings)
            try:
                sync(mid)
            except HTTPException:
                db.execute('UPDATE permanent_mail_accounts SET message=? WHERE id=?',
                    ('Ящик создан, но связь со старым почтовым реестром требует проверки. Исходные записи сохранены.', mid))
            if normal(mid)['status'] in ('unknown', 'failed'):
                break
    finally:
        with db.connect() as c:
            for mid in ids:
                c.execute("UPDATE permanent_mail_accounts SET status='failed',message='Не отправлено: серия остановлена.',updated_at=? WHERE id=? AND status='queued'", (db.now(), mid))
        mail.release(owner)
        mail.task = None


@router.post('/create', status_code=202)
async def create(payload: CreateInput):
    init()
    settings = mail.configuration()
    mail.storage_ready()
    owner = mail.claim()
    try:
        remote = await mail.provider.Mailcow(settings['origin'], settings['key']).domain(settings['domain'])
        if not remote or not mail.provider.active(remote) or int(remote.get('mboxes_left', 0)) < len(payload.profile_ids):
            raise HTTPException(409, 'На домене недостаточно свободных ящиков. Ничего не создано.')
        ids = []
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            available_profiles(c, payload.profile_ids)
            for pid in payload.profile_ids:
                name, email = mail.identity(c, settings['domain'])
                blob = mail.encrypted({'password': 'Aa9!' + secrets.token_urlsafe(20), 'app_password': '', 'recovery_email': ''})
                mid = c.execute('''INSERT INTO permanent_mail_accounts(email,display_name,provider,source,status,secret_cipher,imap_host,created_at,updated_at)
                    VALUES (?,?,'Mailcow','api','queued',?,?,?,?)''', (email, name, blob, urlsplit(settings['origin']).hostname, db.now(), db.now())).lastrowid
                c.execute('INSERT INTO real_mail_links(mid,profile_id) VALUES (?,?)', (mid, pid))
                ids.append(mid)
        mail.stop_requested = False
        mail.task = asyncio.create_task(process(ids, settings, owner))
        return {'mailbox_ids': ids, 'profile_ids': payload.profile_ids, 'started': True}
    except mail.provider.ProviderError:
        mail.release(owner)
        raise HTTPException(502, 'Почтовый сервис не подтвердил подключение. Новых адресов не создано.') from None
    except BaseException:
        mail.release(owner)
        raise


@router.post('/stop')
def stop():
    return mail.stop()


@router.post('/{mid}/verify')
async def verify(mid: int):
    init(); normal(mid)
    result = await mail.verify(mid)
    sync(mid)
    return result


@router.post('/{mid}/reconcile')
async def reconcile(mid: int):
    init(); normal(mid)
    result = await mail.reconcile(mid)
    sync(mid)
    return result


@router.post('/{mid}/credentials')
def credentials(mid: int):
    init(); normal(mid)
    return mail.credentials(mid)


@router.post('/{mid}/at-preparation', status_code=201)
def prepare_at(mid: int):
    from . import mail_setup
    init()
    row = normal(mid)
    if not row['login_verified'] or not row['mailbox_id']:
        raise HTTPException(409, 'Сначала проверьте вход IMAP и связь почты с ботом.')
    return mail_setup.prepare_at(row['mailbox_id'])
