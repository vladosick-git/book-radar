import json
import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.main import app
from backend.collector import save_finding


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    with TestClient(app) as instance:
        yield instance


def test_validation_and_profile_creation(client):
    assert client.post('/api/profiles', json={'name': ' '}).status_code == 422
    assert client.post('/api/profiles', json={'name': 'Читатель'}).status_code == 201
    assert client.post('/api/profiles', json={'name': 'Читатель'}).status_code == 409
    assert client.post('/api/jobs', json={'name': 'Тест', 'profile_id': 999, 'urls': ['https://example.com']}).status_code == 404
    for url in ['file:///C:/secret', 'javascript:alert(1)', 'https://user:password@example.com']:
        assert client.post('/api/jobs', json={'name': 'Тест', 'profile_id': 1, 'urls': [url]}).status_code == 422
    result = client.post('/api/jobs', json={'name': 'Тест', 'profile_id': 1, 'urls': ['https://example.com', 'https://example.com']})
    assert result.status_code == 201
    assert client.get('/api/state').json()['jobs'][0]['urls'] == ['https://example.com']


def test_deduplication_profile_isolation_and_csv(client):
    for name in ['Первый', 'Второй']:
        client.post('/api/profiles', json={'name': name})
    for profile_id in [1, 2]:
        client.post('/api/jobs', json={'name': 'Тест', 'profile_id': profile_id, 'urls': ['https://example.com']})
    item = {'title': '=1+1', 'text': 'Текст рекламы', 'destination': 'https://example.com/book', 'image_url': ''}
    first_job = db.rows('SELECT * FROM jobs WHERE id=1')[0]
    second_job = db.rows('SELECT * FROM jobs WHERE id=2')[0]
    for job in [first_job, first_job, second_job]:
        save_finding(job, item, 'https://example.com', 'https://example.com', 'test.png')
    findings = client.get('/api/findings').json()['items']
    assert len(findings) == 2
    assert sorted(f['sightings'] for f in findings) == [1, 2]
    export = client.get('/api/export.csv')
    assert "'=1+1" in export.text
    assert 'Текст рекламы' in export.text


def test_restart_and_local_origin(client):
    client.post('/api/profiles', json={'name': 'Первый'})
    job_id = client.post('/api/jobs', json={'name': 'Тест', 'profile_id': 1, 'urls': ['https://example.com']}).json()['id']
    db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    db.init()
    assert client.get('/api/state').json()['jobs'][0]['status'] == 'interrupted'
    assert client.post('/api/profiles', json={'name': 'Другой'}, headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.get('/api/evidence/missing.png').status_code == 404
    assert client.post('/api/jobs/999/run').status_code == 404
