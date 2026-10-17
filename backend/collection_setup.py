"""Atomic, repeat-safe assignment of real collection jobs to existing profiles."""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from . import audit, db

router = APIRouter(prefix='/api/collection')


class SetupInput(BaseModel):
    profile_ids: list[int] = Field(min_length=1, max_length=10)
    name: str = Field(default='Книжная реклама', min_length=1, max_length=60)
    urls: list[str] = Field(min_length=1, max_length=20)
    wait_seconds: int = Field(default=15, ge=2, le=30)

    @field_validator('profile_ids')
    @classmethod
    def unique_profiles(cls, ids):
        if len(set(ids)) != len(ids) or any(i < 1 for i in ids):
            raise ValueError('Выберите от одного до десяти разных профилей.')
        return ids

    @field_validator('urls')
    @classmethod
    def valid_urls(cls, values):
        from .main import JobInput
        return JobInput.clean_urls(values)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if not value.strip():
            raise ValueError('Укажите название сбора.')
        return value.strip()


@router.post('/setup', status_code=201)
def setup(payload: SetupInput):
    from .main import JobInput
    # Use exactly the same URL and name validation as the single-job editor.
    jobs = [JobInput(name=payload.name, urls=payload.urls, wait_seconds=payload.wait_seconds, profile_id=pid)
            for pid in payload.profile_ids]
    created, existing = [], []
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for job in jobs:
            if not c.execute('SELECT 1 FROM profiles WHERE id=?', (job.profile_id,)).fetchone():
                raise HTTPException(404, 'Один из выбранных профилей больше не существует. Ничего не создано.')
        for job in jobs:
            encoded = json.dumps(job.urls)
            prior = c.execute('''SELECT id FROM jobs WHERE profile_id=? AND urls=? AND selector=''
                AND wait_seconds=? AND headed=0 AND login_selector='' ORDER BY id LIMIT 1''',
                (job.profile_id, encoded, job.wait_seconds)).fetchone()
            if prior:
                existing.append(prior['id'])
                continue
            created.append(c.execute('''INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at,headed,login_selector)
                VALUES (?,?,?,'',?,?,0,'')''', (job.name, job.profile_id, encoded, job.wait_seconds, db.now())).lastrowid)
    audit.record('collection_setup', 'jobs', None, created=created, reused=existing)
    return {'created': created, 'existing': existing, 'job_ids': created + existing, 'queued': False}
