"""Owner-operated mailbox setup. Local records never prove external registration."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import audit, config, db, onboarding

router = APIRouter(prefix='/api/mailboxes')
YANDEX_REGISTER = 'https://passport.yandex.ru/registration'


class SetupInput(BaseModel):
    profile_id: int = Field(ge=1)


def service(connection, name, kind, url):
    row = connection.execute('SELECT * FROM services WHERE name_key=?', (name.casefold(),)).fetchone()
    if row:
        if row['kind'] != kind or row['registration_url'] != url:
            raise HTTPException(409, 'Название сервиса занято другой записью. Существующие данные сохранены.')
        return row['id']
    return connection.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
        (name, name.casefold(), kind, url, db.now())).lastrowid


@router.post('/setup', status_code=201)
def prepare(payload: SetupInput):
    if config.SERVER:
        raise HTTPException(409, 'Почта создаётся владельцем в локальном окне; на сервер переносится готовая сессия.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM profiles WHERE id=?', (payload.profile_id,)).fetchone():
            raise HTTPException(404, 'Профиль не найден.')
        sid = service(c, 'Яндекс · ручная регистрация', 'email', YANDEX_REGISTER)
        existing = c.execute('SELECT id FROM accounts WHERE profile_id=? AND service_id=?', (payload.profile_id, sid)).fetchone()
        if existing:
            return {'account_id': existing['id'], 'created': False, 'registration_verified': False}
        now = db.now()
        aid = c.execute('INSERT INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)',
            (payload.profile_id, sid, now, now)).lastrowid
    audit.record('mail_owner_prepare', 'profile', payload.profile_id)
    return {'account_id': aid, 'created': True, 'registration_verified': False}


@router.post('/{mailbox_id}/at-preparation', status_code=201)
def prepare_at(mailbox_id: int):
    """Bind an owner's existing mailbox to a local AT checklist without submitting a form."""
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        mailbox = c.execute('SELECT m.*,a.profile_id FROM mailboxes m JOIN accounts a ON a.id=m.account_id WHERE m.id=?',
            (mailbox_id,)).fetchone()
        if not mailbox:
            raise HTTPException(404, 'Ящик не найден.')
        if mailbox['source'] != 'manual' and mailbox['status'] != 'created':
            raise HTTPException(409, 'Сначала подключите готовую почту или подтвердите создание через почтовый модуль.')
        # Respect any AT record on this profile, including the original owner pilot.
        prior = c.execute("SELECT a.* FROM accounts a JOIN services s ON s.id=a.service_id WHERE a.profile_id=? AND s.kind='site'",
            (mailbox['profile_id'],)).fetchall()
        accounts = [a for a in prior if onboarding.is_at(c.execute('SELECT registration_url FROM services WHERE id=?',
            (a['service_id'],)).fetchone()['registration_url'])]
        if len(accounts) > 1:
            raise HTTPException(409, 'У профиля несколько записей AT. Выберите нужную в разделе «Подготовка».')
        account = accounts[0] if accounts else None
        if account and account['identifier'] and account['identifier'].casefold() != mailbox['email'].casefold():
            raise HTTPException(409, 'У профиля уже указан другой аккаунт AT. Существующие данные сохранены.')
        now = db.now()
        if account:
            aid = account['id']
            if not account['identifier']:
                c.execute('UPDATE accounts SET identifier=?,updated_at=? WHERE id=?', (mailbox['email'], now, aid))
        else:
            sid = service(c, 'Author.Today · подготовка', 'site', onboarding.REGISTER)
            aid = c.execute('INSERT INTO accounts(profile_id,service_id,identifier,created_at,updated_at) VALUES (?,?,?,?,?)',
                (mailbox['profile_id'], sid, mailbox['email'], now, now)).lastrowid
    audit.record('at_mail_prepare', 'profile', mailbox['profile_id'])
    return {'account_id': aid, 'created': account is None, 'registration_verified': False}
