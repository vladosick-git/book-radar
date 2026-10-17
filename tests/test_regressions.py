import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from fastapi.testclient import TestClient
from backend import db
from backend.collector import collect, save_finding
from backend.identity import stable_url, fingerprint
from backend.browser import state_path, read_state, write_state
from backend.evidence import cleanup
from backend.main import app


def test_url_identity_preserves_meaningful_parameters():
    assert stable_url('https://x.test/a?id=3&rnd=100&utm_source=x&width=153') == 'https://x.test/a?id=3&width=153'
    assert stable_url('https://x.test/a?id=4') != stable_url('https://x.test/a?id=3')
    assert stable_url('https://x.test/a?width=153') != stable_url('https://x.test/a?width=300')
    assert stable_url('https://x.test/a?sig=a') != stable_url('https://x.test/a?sig=b')
    assert stable_url('https://x.test/a#book-1') != stable_url('https://x.test/a#book-2')


def test_migration_merges_legacy_duplicates_without_losing_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Профиль', db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                        ('Тест', profile, '[]', '', 2, db.now()))
    for index, count in [(1, 2), (2, 3)]:
        timestamp = f'2026-10-0{index}T00:00:00+00:00'
        db.execute('''INSERT INTO findings(fingerprint,profile_id,job_id,title,text,destination,image_url,
            source_url,frame_url,screenshot,first_screenshot,first_seen,last_seen,sightings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (f'legacy-{index}', profile, job_id, 'Книга', 'Текст', 'https://x.test/book?id=1',
             f'https://x.test/img?cb={index}', 'https://x.test', f'https://ads.test/frame?rnd={index}',
             f'last-{index}.png', f'first-{index}.png', timestamp, timestamp, count))
    db.execute("DELETE FROM settings WHERE key='fingerprint_version'")
    db.init()
    result = db.rows('SELECT * FROM findings')
    assert len(result) == 1 and result[0]['sightings'] == 5
    assert result[0]['first_screenshot'] == 'first-1.png'
    assert result[0]['screenshot'] == 'last-2.png'
    assert result[0]['fingerprint'] == fingerprint(profile, 'https://x.test', 'https://ads.test/frame?rnd=9', result[0])
    db.init()
    assert db.rows('SELECT * FROM findings')[0]['sightings'] == 5


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_legacy_cookie_state_is_encrypted_and_recoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    target = state_path(1)
    target.parent.mkdir()
    legacy = target.with_name('radar-state.json')
    state = {'cookies': [{'name': 'sess', 'value': 'private-cookie-value', 'httpOnly': True}], 'origins': []}
    legacy.write_text(json.dumps(state), encoding='utf-8')
    assert read_state(1) == state
    assert target.is_file() and not legacy.exists()
    assert b'private-cookie-value' not in target.read_bytes()
    assert read_state(1) == state
    write_state(1, state)
    assert read_state(1) == state


def test_evidence_paths_and_cleanup_preserve_references(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    with TestClient(app) as client:
        profile = client.post('/api/profiles', json={'name': 'Профиль'}).json()['id']
        job_id = client.post('/api/jobs', json={'name': 'Тест', 'profile_id': profile, 'urls': ['https://x.test'], 'headed': True}).json()['id']
        job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
        assert job['headed'] == 1
        files = [f'job-{job_id}-{index:032x}.png' for index in range(1, 6)]
        for filename in files:
            path = tmp_path / 'evidence' / filename
            path.write_bytes(b'png')
            os.utime(path, (time.time() - 10*86400, time.time() - 10*86400))
        os.utime(tmp_path / 'evidence' / files[4], None)
        item = {'title': 'Книга', 'text': 'Текст', 'destination': 'https://x.test/book', 'image_url': ''}
        save_finding(job, item, 'https://x.test', 'https://x.test', files[0])
        save_finding(job, item, 'https://x.test', 'https://x.test', files[1])
        db.log(job_id, 'info', f'Скриншот страницы: {files[2]}')
        assert cleanup(dry_run=True) == 1
        response = client.post('/api/evidence/cleanup', json={})
        assert response.json()['removed'] == 1
        assert not (tmp_path / 'evidence' / files[3]).exists()
        assert all((tmp_path / 'evidence' / files[i]).exists() for i in [0, 1, 2, 4])
        assert client.get('/api/evidence/'+files[0]).status_code == 200
        for name in ['C:x.png', 'foo.png', 'job-1-'+'g'*32+'.png']:
            assert client.get('/api/evidence/'+name).status_code == 404
        exported = client.get('/api/export.csv').text
        assert 'first_screenshot' in exported and files[0] in exported and files[1] in exported


def test_audited_collector_edge_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    class Handler(BaseHTTPRequestHandler):
        counter = 0
        def log_message(self, *args):
            pass
        def do_GET(self):
            type(self).counter += 1
            number = type(self).counter
            route = self.path.split('?')[0]
            ad = '<article data-ad><h2>Книга</h2><a href="/book?id=1">Подробнее</a></article>'
            pages = {
                '/frame': ad,
                '/random-frame': f'<iframe src="/frame?rnd={number}" width="400" height="200"></iframe>',
                '/random-img': f'<article data-ad><h2>Книга</h2><img src="/image.png?cb={number}" alt="Обложка" width="90" height="90"><a href="/book?id=1">Подробнее</a></article>',
                '/article': ad + '<p>Статья: как работает проверка браузера; сообщение access denied в программе.</p>',
                '/wrapper': '<div class="advertising" style="height:0;overflow:visible"><article data-ad style="height:120px;width:300px"><h2>Книга</h2><a href="/book">Ссылка</a></article></div>',
                '/clipped': '<div class="advertising" style="height:0;overflow:hidden"><article data-ad style="height:120px;width:300px"><h2>Книга</h2></article></div>',
                '/false-selector': '<header class="site-head-banner">Меню</header><div class="thread-banner">Обсуждение</div>',
                '/heading': '<article data-ad><a href="/book"><img src="/image.png" alt="Обложка" width="90" height="90"></a><a href="/book">Правильное название книги</a></article>',
                '/background': '<article data-ad style="height:100px;width:300px;background-image:url(\'/image.png\')"><h2>Баннер с фоном</h2></article>',
                '/challenge': '<title>Just a moment...</title>' + ad,
                '/forbidden': ad,
            }
            self.send_response(403 if route == '/forbidden' else 200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(('<!doctype html><meta charset="utf-8"><body>'+pages.get(route, '')+'</body>').encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Тест', db.now()))
    async def run(route, times=1):
        job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                           (route, profile, json.dumps([base+route]), '', 2, db.now()))
        job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
        for _ in range(times):
            await collect(job)
        return db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0], db.rows('SELECT * FROM findings WHERE job_id=?', (job_id,))
    async def check():
        for route in ['/random-frame', '/random-img']:
            job, found = await run(route, times=2)
            assert job['status'] == 'done' and len(found) == 1 and found[0]['sightings'] == 2
            assert found[0]['first_screenshot'] != found[0]['screenshot']
        for route in ['/article', '/wrapper']:
            job, found = await run(route)
            assert job['status'] == 'done' and len(found) == 1
        for route in ['/clipped', '/false-selector']:
            job, found = await run(route)
            assert job['status'] == 'done' and not found
        job, found = await run('/heading')
        assert found[0]['title'] == 'Правильное название книги'
        job, found = await run('/background')
        assert found[0]['image_url'] == base+'/image.png'
        for route in ['/challenge', '/forbidden']:
            job, found = await run(route)
            assert job['status'] == 'partial' and len(found) == 1 and job['last_count'] == 1
            assert 'Доступ ограничен' in job['last_error']
    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        thread.join()
