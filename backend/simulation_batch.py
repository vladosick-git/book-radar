"""Local simulation profile batches and an address-only pool. No email provider calls."""
import re
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import db, simulation as sim

router = APIRouter(prefix='/api/simulation')


class MailPoolInput(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=500)

    @field_validator('addresses')
    @classmethod
    def clean_addresses(cls, values):
        result = []
        for raw in values:
            value = raw.strip().lower()
            # One address per line; deliberately reject password-bearing CSV rows.
            if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
                raise ValueError('Нужны только адреса почты, по одному на строку, без паролей.')
            local, domain = value.split('@')
            if local.startswith('.') or local.endswith('.') or '..' in local or '..' in domain:
                raise ValueError('Проверьте формат адресов почты.')
            if value not in result:
                result.append(value)
        return result


class BatchInput(BaseModel):
    count: int = Field(default=3, ge=1, le=500)
    new_emails: bool = True
    prefix: str = Field(default='Бот', min_length=1, max_length=40)

    @field_validator('prefix')
    @classmethod
    def clean_prefix(cls, value):
        if not value.strip():
            raise ValueError('Укажите название партии.')
        return value.strip()


@router.post('/mail-pool/import')
def import_mail_pool(payload: MailPoolInput):
    sim.init()
    added = 0
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for email in payload.addresses:
            prior = c.execute('SELECT id FROM simulation_profiles WHERE lower(email)=?', (email,)).fetchone()
            added += c.execute('INSERT OR IGNORE INTO simulation_mail_pool(email,profile_id,created_at) VALUES (?,?,?)',
                               (email, prior['id'] if prior else None, db.now())).rowcount
    return {'added': added, 'duplicates': len(payload.addresses) - added}


def allocate(c, count, new_emails=True, prefix='Бот'):
    cities, devices = list(sim.CITIES), list(sim.DEVICES)
    rng = secrets.SystemRandom()
    rng.shuffle(cities)
    rng.shuffle(devices)
    available = []
    if not new_emails:
        available = c.execute("""SELECT m.id,m.email FROM simulation_mail_pool m WHERE m.profile_id IS NULL
            AND NOT EXISTS (SELECT 1 FROM simulation_profiles p WHERE lower(p.email)=lower(m.email))
            ORDER BY m.id LIMIT ?""", (count,)).fetchall()
        if len(available) < count:
            raise HTTPException(409, f'Недостаточно свободных адресов: нужно {count}, доступно {len(available)}. Добавьте адреса в базу или уменьшите количество.')
    ids = []
    for i in range(count):
        city, device = cities[i % len(cities)], devices[i % len(devices)]
        now = db.now()
        name = f'{prefix} · {secrets.token_hex(10)}'
        pid = c.execute('INSERT INTO simulation_profiles(name,city,device,created_at,updated_at) VALUES (?,?,?,?,?)',
                        (name, city, device, now, now)).lastrowid
        readable = f'{prefix} {pid} · {sim.CITIES[city]["name"]}'
        if not c.execute('SELECT 1 FROM simulation_profiles WHERE name=?', (readable,)).fetchone():
            c.execute('UPDATE simulation_profiles SET name=? WHERE id=?', (readable, pid))
        email = sim.fixture_email(c, pid) if new_emails else available[i]['email']
        c.execute('UPDATE simulation_profiles SET email=? WHERE id=?', (email, pid))
        if not new_emails:
            c.execute('UPDATE simulation_mail_pool SET profile_id=? WHERE id=?', (pid, available[i]['id']))
        c.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (?,?,?)', (pid, 'profile_created', now))
        ids.append(pid)
    return ids


@router.post('/batch', status_code=201)
def create_batch(payload: BatchInput):
    sim.init()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        ids = allocate(c, payload.count, payload.new_emails, payload.prefix)
    return {'profile_ids': ids, 'address_source': 'new_fixture' if payload.new_emails else 'pool'}
