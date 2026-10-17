import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from backend import db
from backend.collector import collect
from playwright.async_api import async_playwright
from backend.browser import open_context, close_context


def test_persistent_cookies_and_separate_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    async def check():
        async with async_playwright() as pw:
            first = await open_context(pw, 1, headless=True)
            await first.add_cookies([{'name': 'radar_test', 'value': 'saved', 'url': 'http://127.0.0.1'}])
            await close_context(first, 1)
            reopened = await open_context(pw, 1, headless=True)
            try:
                assert any(c['name'] == 'radar_test' and c['value'] == 'saved' for c in await reopened.cookies())
            finally:
                await close_context(reopened, 1)
            second = await open_context(pw, 2, headless=True)
            try:
                assert not any(c['name'] == 'radar_test' for c in await second.cookies())
            finally:
                await close_context(second, 2)
    asyncio.run(check())


def test_visible_blocks_frames_and_error_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            content = '<article data-ad><h2>Фрейм</h2><a href="/book">Книга</a></article>' if self.path in ('/frame', '/hidden-frame') else '''
            <article data-ad><h2>Основное объявление</h2><div class="advertisement">Вложенный блок</div><a href="/book">Книга</a></article>
            <article data-ad style="display:none">Скрытая реклама</article>
            <section>Обычная статья</section><iframe src="/frame"></iframe><iframe style="display:none" src="/hidden-frame"></iframe>'''
            self.send_response(503 if self.path == '/fail' else 200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(content.encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Тест', db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                       ('Тест', profile, json.dumps([base, base+'/fail']), '', 2, db.now()))
    try:
        job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
        asyncio.run(collect(job))
        findings = db.rows('SELECT * FROM findings')
        assert len(findings) == 4  # Preserve advertisements extracted from the HTTP 503 page too.
        assert {item['title'] for item in findings} == {'Основное объявление', 'Фрейм'}
        assert all(item['destination'] == base+'/book' for item in findings)
        assert all((tmp_path / 'evidence' / item['screenshot']).is_file() for item in findings)
        result = db.rows('SELECT * FROM jobs')[0]
        assert result['status'] == 'partial'
        assert '503' in result['last_error']
        job['urls'] = json.dumps([base])
        job['selector'] = '['
        asyncio.run(collect(job))
        result = db.rows('SELECT * FROM jobs')[0]
        assert result['status'] == 'error'
        assert 'CSS' in result['last_error']
    finally:
        server.shutdown()
        thread.join()
