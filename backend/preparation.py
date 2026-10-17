"""Local preparation records. Opening a URL never verifies account registration."""
import sqlite3
from typing import Literal
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from . import db


class BatchProfilesInput(BaseModel):
    prefix: str = Field(min_length=1, max_length=65)
    count: int = Field(ge=1, le=10)
    start: int = Field(default=1, ge=1, le=9999)

    @field_validator('prefix')
    @classmethod
    def clean_prefix(cls, value):
        if not value.strip():
            raise ValueError('Укажите название серии.')
        return value.strip()


class ServiceInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['email', 'site']
    registration_url: str = Field(min_length=1, max_length=2048)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('Название не должно быть пустым.')
        return value.strip()

    @field_validator('registration_url')
    @classmethod
    def clean_url(cls, value):
        value = value.strip()
        try:
            parsed = urlparse(value)
            parsed.port  # Validate malformed port numbers too.
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('Укажите полный HTTP/HTTPS URL без логина и пароля.')
        return value


class AssignInput(BaseModel):
    profile_ids: list[int] = Field(min_length=1, max_length=10)
    service_ids: list[int] = Field(min_length=1, max_length=20)

    @field_validator('profile_ids', 'service_ids')
    @classmethod
    def unique_ids(cls, values):
        if any(value < 1 for value in values):
            raise ValueError('Выберите существующие профили и сервисы.')
        return list(dict.fromkeys(values))


class AccountInput(BaseModel):
    status: Literal['planned', 'in_progress', 'confirmed']
    identifier: str = Field(default='', max_length=160)
    note: str = Field(default='', max_length=1000)

    @field_validator('identifier', 'note')
    @classmethod
    def clean_text(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def confirmation_needs_identifier(self):
        if self.status == 'confirmed' and not self.identifier:
            raise ValueError('Для ручного подтверждения укажите почту или логин.')
        return self


def create_batch(payload):
    names = [f'{payload.prefix} {number:02d}' for number in range(payload.start, payload.start + payload.count)]
    ids = []
    try:
        with db.connect() as connection:
            for name in names:
                cursor = connection.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (name, db.now()))
                ids.append(cursor.lastrowid)
    except sqlite3.IntegrityError:
        # The entire series rolls back: no half-created batch after a name conflict.
        raise HTTPException(409, 'Название одного из профилей уже занято. Измените название серии или начальный номер.')
    db.log(None, 'info', f'Создана серия профилей «{payload.prefix}»: {len(ids)}.', source='profile')
    return {'ids': ids, 'names': names}


def create_service(payload):
    try:
        service_id = db.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
                                (payload.name, payload.name.casefold(), payload.kind, payload.registration_url, db.now()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Сервис с таким названием уже есть.')
    return {'id': service_id}


def update_service(service_id, payload):
    with db.connect() as connection:
        if not connection.execute('SELECT 1 FROM services WHERE id=?', (service_id,)).fetchone():
            raise HTTPException(404, 'Сервис не найден.')
        try:
            connection.execute('UPDATE services SET name=?,name_key=?,kind=?,registration_url=? WHERE id=?',
                               (payload.name, payload.name.casefold(), payload.kind, payload.registration_url, service_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Сервис с таким названием уже есть.')
    return {'id': service_id}


def assign_accounts(payload):
    created = 0
    with db.connect() as connection:
        for table, ids in [('profiles', payload.profile_ids), ('services', payload.service_ids)]:
            placeholders = ','.join('?' for _ in ids)
            found = {row['id'] for row in connection.execute(f'SELECT id FROM {table} WHERE id IN ({placeholders})', ids)}
            if found != set(ids):
                raise HTTPException(404, 'Профиль или сервис не найден. Обновите список.')
        timestamp = db.now()
        for profile_id in payload.profile_ids:
            for service_id in payload.service_ids:
                cursor = connection.execute('INSERT OR IGNORE INTO accounts(profile_id,service_id,created_at,updated_at) VALUES (?,?,?,?)',
                                            (profile_id, service_id, timestamp, timestamp))
                created += cursor.rowcount
    db.log(None, 'info', f'Добавлено записей подготовки: {created}. Регистрация на сайтах ещё не выполнена.', source='profile')
    return {'created': created, 'existing': len(payload.profile_ids) * len(payload.service_ids) - created}


def get_account(account_id):
    rows = db.rows('''SELECT accounts.*, services.name AS service_name, services.kind AS service_kind,
        services.registration_url, profiles.name AS profile_name FROM accounts
        JOIN services ON services.id=accounts.service_id JOIN profiles ON profiles.id=accounts.profile_id
        WHERE accounts.id=?''', (account_id,))
    if not rows:
        raise HTTPException(404, 'Запись подготовки не найдена.')
    return rows[0]


def update_account(account_id, payload):
    with db.connect() as connection:
        row = connection.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Запись подготовки не найдена.')
        linked = connection.execute('SELECT email FROM mailboxes WHERE account_id=?', (account_id,)).fetchone()
        if linked and payload.identifier != linked['email']:
            raise HTTPException(409, 'Адрес закреплён почтовым модулем. Изменение нарушило бы связь профиля с ящиком.')
        timestamp = db.now()
        confirmed_at = None
        if payload.status == 'confirmed':
            confirmed_at = row['confirmed_at'] if row['status'] == 'confirmed' and row['identifier'] == payload.identifier else timestamp
        connection.execute('UPDATE accounts SET status=?,identifier=?,note=?,updated_at=?,confirmed_at=? WHERE id=?',
                           (payload.status, payload.identifier, payload.note, timestamp, confirmed_at, account_id))
    return {'id': account_id, 'status': payload.status}


def mark_opened(account_id):
    db.execute('''UPDATE accounts SET opened_at=?,updated_at=?,
        status=CASE WHEN status='planned' THEN 'in_progress' ELSE status END WHERE id=?''',
        (db.now(), db.now(), account_id))
