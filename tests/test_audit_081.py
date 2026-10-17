"""Acceptance of audit fixes: honest state, preserved raw records and isolated test findings."""
import csv
import io
import json
import pytest
from fastapi.testclient import TestClient
from backend import catalog, config, db, main, finding_quality, permanent_mail, backup


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    monkeypatch.setattr(config, 'SIMULATION', False)
    monkeypatch.setattr(config, 'AUTH', False)
    monkeypatch.setattr(config, 'ALLOW_TEST_COLLECTION', False)
    with TestClient(main.app) as client:
        yield client


def job(client):
    pid = client.post('/api/profiles', json={'name': 'Audit fixture'}).json()['id']
    jid = client.post('/api/jobs', json={'name': 'Ads of different subjects', 'profile_id': pid, 'urls': ['https://site.test/']}).json()['id']
    return db.rows('SELECT * FROM jobs WHERE id=?', (jid,))[0]


BASE = {'title': 'A book', 'text': 'Book text', 'destination': 'https://author.today/work/1', 'image_url': ''}


def test_sample_is_blocked_and_test_records_are_retained_but_excluded(client):
    current = job(client)
    response = client.post('/api/jobs', json={'name': 'Unsafe demo', 'profile_id': current['profile_id'], 'urls': ['http://127.0.0.1:8794/sample']})
    assert response.status_code == 422
    db.execute('UPDATE jobs SET urls=? WHERE id=?', (json.dumps(['http://127.0.0.1:8794/sample']), current['id']))
    assert client.post(f"/api/jobs/{current['id']}/run").status_code == 409
    assert not db.rows('SELECT id FROM runs')
    catalog.save(current, BASE, 'https://site.test/', 'https://site.test/', '')
    test = catalog.save(current, {**BASE, 'title': 'Artificial fixture'}, 'http://127.0.0.1:8801/sample', '', '')
    assert len(db.rows('SELECT id FROM findings')) == 2
    assert client.get('/api/findings').json()['total'] == 1
    assert client.get('/api/export/preview').json()['total'] == 1
    assert client.get('/api/export.json').json()['counts']['findings'] == 1
    assert len(list(csv.reader(io.StringIO(client.get('/api/export.csv').text)))) == 2
    assert 'Artificial fixture' not in client.get('/api/export.csv').text
    row = client.get('/api/findings', params={'data_scope': 'test'}).json()['items'][0]
    assert row['id'] == test['finding_id'] and row['is_test']
    assert client.get('/api/export.json', params={'data_scope': 'all'}).json()['counts']['findings'] == 2
    assert client.get('/api/findings/facets', params={'data_scope': 'test'}).json()['sources'][0]['host'] == '127.0.0.1'


def test_legacy_duplicate_marking_preserves_ids_payload_and_history(client):
    current = job(client)
    first = catalog.save(current, {**BASE, 'title': 'Header A'}, 'https://site.test/', '', '')
    second = catalog.save(current, {**BASE, 'title': 'Header B'}, 'https://site.test/', '', '')
    text = 'ИЗУЧИТЕ ВСЕ УСЛОВИЯ КРЕДИТА\nСООТВЕТСТВУЮЩЕМ РАЗДЕЛЕ\ndigital.sber.ru\nРеклама\nПодробнее на сайте digital.sber.ru'
    db.execute('UPDATE findings SET text=?,title=? WHERE id=?', (text, 'ИЗУЧИТЕ ВСЕ УСЛОВИЯ КРЕДИТА', first['finding_id']))
    db.execute('UPDATE findings SET text=?,title=? WHERE id=?', (text, 'СООТВЕТСТВУЮЩЕМ РАЗДЕЛЕ', second['finding_id']))
    raw = db.rows('SELECT id,title,text,sightings FROM findings ORDER BY id')
    observations = db.rows('SELECT * FROM observations ORDER BY id')
    db.init()
    assert db.rows('SELECT id,title,text,sightings FROM findings ORDER BY id') == raw
    assert db.rows('SELECT * FROM observations ORDER BY id') == observations
    assert client.get('/api/findings').json()['total'] == 1
    both = client.get('/api/findings', params={'include_duplicates': True}).json()
    assert both['total'] == 2
    duplicate = next(row for row in both['items'] if row['duplicate_of'])
    assert duplicate['duplicate_of'] == first['finding_id']
    assert duplicate['title'] == 'Реклама · digital.sber.ru (заголовок не распознан)'
    assert duplicate['original_title'] == 'СООТВЕТСТВУЮЩЕМ РАЗДЕЛЕ'
    result = catalog.save(current, {**BASE, 'title': 'ИЗУЧИТЕ ВСЕ УСЛОВИЯ КРЕДИТА', 'text': text}, 'https://site.test/', '', '')
    assert not result['new'] and result['finding_id'] == first['finding_id']


def test_manual_mail_is_not_verified_and_batch_and_csv_are_audited(client):
    ids = client.post('/api/profiles/batch', json={'prefix': 'Audit', 'count': 2, 'start': 1}).json()['ids']
    result = client.post('/api/mailboxes/attach', json={'profile_id': ids[0], 'email': 'owner@example.test', 'provider': 'Yandex'})
    assert result.status_code == 201
    state = client.get('/api/state').json()
    assert state['accounts'][0]['status'] == 'in_progress'
    assert state['accounts'][0]['confirmed_at'] is None
    assert not state['mailboxes'][0]['provisioned'] and not state['mailboxes'][0]['login_verified']
    assert db.rows('SELECT status FROM mailboxes')[0]['status'] == 'prepared'
    # Repair older automatic confirmations, while preserving IDs and stored credentials.
    mid = result.json()['id']
    mailbox = db.rows('SELECT * FROM mailboxes WHERE id=?', (mid,))[0]
    db.execute("UPDATE accounts SET status='confirmed',confirmed_at=?,updated_at=? WHERE id=?", (mailbox['created_at'],mailbox['updated_at'],mailbox['account_id']))
    db.execute("UPDATE mailboxes SET status='created' WHERE id=?", (mid,))
    db.init()
    assert db.rows('SELECT id,status FROM mailboxes') == [{'id': mid, 'status': 'prepared'}]
    assert db.rows('SELECT status FROM accounts')[0]['status'] == 'in_progress'
    client.get('/api/export.csv')
    assert {'profile_batch_create', 'export_csv'} <= {row['action'] for row in db.rows('SELECT action FROM audit')}


def test_secret_disclosure_is_post_only_and_no_store(client, monkeypatch):
    monkeypatch.setattr(config, 'SIMULATION', True)
    mid = client.post('/api/simulation/mail/attach', json={'email': 'qa@example.test', 'password': 'private-fixture-only'}).json()['id']
    response = client.get(f'/api/simulation/mail/{mid}/credentials')
    assert response.status_code in (404, 405) and 'private-fixture-only' not in response.text
    response = client.post(f'/api/simulation/mail/{mid}/credentials', json={})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['password'] == 'private-fixture-only'


def test_preserve_data_disables_automatic_backup_pruning(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ('radar-backup-1.zip', 'radar-backup-2.zip', 'abandoned.zip.partial')]
    for path in paths:
        path.write_bytes(b'fixture')
    monkeypatch.setenv('RADAR_PRESERVE_DATA', '1')
    backup.prune(tmp_path, 1)
    assert all(path.exists() for path in paths)


def test_caption_handles_whitespace_soft_hyphens_and_keeps_real_titles():
    assert finding_quality.caption({'title': 'rutube.ru ', 'text': 'РЕКЛАМА\n18+\nrutube.ru\nНазвание се\xadриала'}) == 'Название сериала'
    assert finding_quality.caption({'title': 'Real headline', 'text': 'Other body'}) == 'Real headline'


def test_local_stop_requires_owner_token_and_refuses_busy_profiles(client):
    from tools.ads_demo import application
    from tools import local_control
    from backend import runs
    called = []
    local_control.write_token(db.DATA)
    token = local_control.read_token(db.DATA)
    pid = client.post('/api/profiles', json={'name': 'Local stop fixture'}).json()['id']
    db.execute('INSERT INTO settings(key,value) VALUES (?,?)', ('ads-live-demo-v1', json.dumps({'profile': pid, 'job': 0})))
    with TestClient(application(lambda: called.append(True))) as panel:
        assert panel.post('/api/ads-demo/stop', json={}).status_code == 403
        headers = {'X-Radar-Control': token}
        assert panel.post('/api/ads-demo/stop', headers={**headers, 'Origin': 'https://foreign.test'}).status_code == 403
        assert runs.acquire_lease(pid, 'test-window', 'window', 60)
        try:
            assert panel.post('/api/ads-demo/stop', headers=headers).status_code == 409
            assert not called
        finally:
            runs.release_lease(pid, 'test-window')
        assert panel.post('/api/ads-demo/stop', headers=headers).status_code == 200
        assert called == [True]
