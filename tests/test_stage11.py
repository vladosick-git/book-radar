"""Stage 11, programmatic part: site adapters, block type «ad / recommendation / unknown» with the reason,
an empty slot told apart from an access error, and old fingerprints untouched. Acceptance on the client's real
Author.Today examples is NOT_TESTED (docs/STAGE11.md)."""
import asyncio
import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend import adapters, catalog, db
from backend.collector import collect
from backend.identity import fingerprint

FIXTURES = Path(__file__).parent / 'fixtures'


# ---------- classification table ----------

AT = adapters.AuthorToday()
GENERIC = adapters.GENERIC
CASES = [
    # adapter, item, signals, expected type, words in the reason
    (AT, {'text': 'Реклама\nГород за туманом'}, {'label': 'Реклама', 'erid': '2VtzqwABCD12'}, 'ad', ['Реклама', 'erid 2VtzqwABCD12']),
    (AT, {'text': 'Книга', 'destination': 'https://author.today/work/1?erid=2VtzqwXYZ987'}, {}, 'ad', ['erid 2VtzqwXYZ987']),
    (AT, {'text': 'Скидка'}, {'hosts': ['an.yandex.ru']}, 'ad', ['рекламная сеть an.yandex.ru']),
    (GENERIC, {'text': '', 'frame_url': 'https://ads.adfox.ru/123/frame'}, {}, 'ad', ['adfox']),
    (GENERIC, {'text': 'Блок'}, {'markers': ['yandex_rtb_R-A-1-2']}, 'ad', ['рекламный контейнер']),
    (GENERIC, {'text': 'erid: 2VtzqwTEXT01\nКнига'}, {}, 'ad', ['erid 2VtzqwTEXT01']),
    (AT, {'text': 'Третье правило'}, {'markers': ['f-book-container', 'slick-slide', 'atRecs']}, 'recommendation', ['карусель рекомендаций AT']),
    (AT, {'text': 'Что это?', 'destination': 'https://author.today/collection/6463?utm_source=AT&utm_medium=banner'},
     {'markers': ['aside-banner', 'banner.at-col-24.08']}, 'recommendation', ['боковой баннер AT', 'собственную подборку AT']),
    (AT, {'text': 'Полка'}, {'work_links': 5}, 'recommendation', ['5 ссылок на /work/']),
    (AT, {'text': 'Неизвестный блок'}, {'markers': ['panel']}, 'unknown', ['нужна проверка']),
    (GENERIC, {'text': 'Купите книгу'}, {'markers': ['card']}, 'unknown', ['нужна проверка']),
    # Counterexamples: words that only look like ads.
    (AT, {'text': 'Реклама книг: как продвигать роман'}, {}, 'unknown', ['нужна проверка']),
    (GENERIC, {'text': 'Блок'}, {'markers': ['header-download', 'loading']}, 'unknown', ['нужна проверка']),
]


@pytest.mark.parametrize('adapter,item,signals,expected,words', CASES)
def test_block_type_and_reason(adapter, item, signals, expected, words):
    block_type, reason = adapter.classify(item, signals)
    assert block_type == expected, reason
    assert all(word in reason for word in words), reason


def test_adapter_is_chosen_by_domain():
    assert adapters.for_url('https://author.today/work/genre/all').id == 'author_today'
    assert adapters.for_url('https://m.author.today/').id == 'author_today'
    assert adapters.for_url('https://author.today.evil.example/').id == 'generic'
    assert adapters.for_url('https://litnet.com/').id == 'generic'
    assert {a['id'] for a in adapters.catalog()} == {'author_today', 'generic'}


# ---------- collection on AT-like fixtures ----------

class Site:
    def __init__(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                name = {'/': 'home.html', '/empty': 'empty_slot.html', '/banner-text': 'banner_text.html'}.get(self.path)
                if self.path == '/blocked':
                    self.send_response(403); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers()
                    self.wfile.write('<h1>Доступ запрещён</h1><div id="atRecs" style="width:400px;height:200px"></div>'.encode()); return
                if self.path == '/plain':
                    body = '<html><body><h1>Статья</h1><p>Текст без рекламных мест.</p></body></html>'.encode()
                elif name:
                    body = (FIXTURES / 'at' / name).read_bytes()
                else:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'


@pytest.fixture
def site(monkeypatch):
    instance = Site()
    # The local server stands in for author.today: the AT adapter is chosen for its host.
    monkeypatch.setattr(adapters.AuthorToday, 'domains', ('author.today', '127.0.0.1'))
    yield instance
    instance.server.shutdown()


def run(tmp_path, monkeypatch, urls, selector=''):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Профиль', db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                        ('AT', profile, json.dumps(urls), selector, 2, db.now()))
    job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    pages = []
    result = asyncio.run(collect(job, on_page=lambda done, count, page: pages.append(page)))
    rows = catalog.search({}, limit=50)['items']
    return job, result, rows, pages


def test_at_fixture_types_reasons_and_evidence(tmp_path, monkeypatch, site):
    _, result, rows, pages = run(tmp_path, monkeypatch, [site.base + '/'])
    by_type = {}
    for row in rows:
        by_type.setdefault(row['block_type'], []).append(row)
    assert sorted(by_type) == ['ad', 'recommendation'], [(r['title'], r['block_type']) for r in rows]
    ad = by_type['ad'][0]
    assert ad['title'] == 'Город за туманом' and 'erid 2VtzqwTESTab12' in ad['block_reason'] and 'Реклама' in ad['block_reason']
    assert ad['block_evidence']['erid'] == '2VtzqwTESTab12' and ad['adapter'] == 'author_today@0.2'
    recommendations = {r['title']: r for r in by_type['recommendation']}
    assert set(recommendations) == {'Третье правило диверсанта', 'Подборка АТ'}
    banner = recommendations['Подборка АТ']
    assert '/collection/6463' in banner['destination'] and '/u/at_collections' not in banner['destination']
    assert banner['text'] == '' and banner['clean_text'] == ''
    assert banner['field_sources']['title'] == 'alt'
    assert all('маркировки «Реклама» и erid нет' in r['block_reason'] for r in recommendations.values())
    assert not any(r['title'] == 'Скрытый слайд' for r in rows)  # a hidden carousel slide is not an observation
    assert pages[0]['status'] == 'ok' and pages[0]['found'] == 3
    facets = catalog.facets()
    assert {f['block_type']: f['n'] for f in facets['block_types']} == {'ad': 1, 'recommendation': 2}
    assert catalog.search({'block_type': 'ad'})['total'] == 1


def test_at_image_banner_ocr_survives_restart_and_late_enable(tmp_path, monkeypatch, site):
    from backend import config, ocr
    monkeypatch.setattr(config, 'OCR', 'off')
    job, _, rows, _ = run(tmp_path, monkeypatch, [site.base + '/'])
    banner = next(r for r in rows if r['title'] == 'Подборка АТ')
    assert banner['ocr_status'] == 'off'
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    calls = []
    def recognize(path, **kwargs):
        calls.append(path)
        assert kwargs == {'psm': 11}
        return 'Школа волшебства\nНовая книжная подборка', 'done'
    monkeypatch.setattr(ocr, 'recognize', recognize)
    asyncio.run(collect(job))
    db.init()  # the state is in SQLite, not a process cache
    asyncio.run(collect(job))
    current = next(r for r in catalog.search({})['items'] if r['id'] == banner['id'])
    assert current['title'] == 'Подборка АТ' and current['text'] == ''
    assert current['clean_text'] == current['ocr_text'] == 'Школа волшебства\nНовая книжная подборка'
    assert current['field_sources']['clean_text'] == 'ocr' and current['ocr_status'] == 'done'
    assert current['sightings'] == 3 and len(calls) == 1
    assert catalog.search({'q': 'волшебства'})['total'] == 1
    assert len(catalog.search({})['items']) == 3


def test_at_cached_ocr_refreshes_once_after_pipeline_change(tmp_path, monkeypatch, site):
    from backend import config, ocr
    monkeypatch.setattr(config, 'OCR', 'off')
    job, _, rows, _ = run(tmp_path, monkeypatch, [site.base + '/'])
    banner = next(r for r in rows if r['title'] == 'Подборка АТ')
    sources = {**banner['field_sources'], 'ocr_pipeline': 'old', 'clean_text': 'ocr'}
    db.execute('UPDATE findings SET clean_text=?,field_sources=? WHERE id=?',
               ('Тородское фэнтези', json.dumps(sources), banner['id']))
    catalog.set_ocr(banner['id'], 'Тородское фэнтези', 'done')
    before = db.rows('SELECT id,fingerprint FROM findings WHERE id=?', (banner['id'],))[0]
    calls = []
    def recognize(path, **kwargs):
        calls.append(path)
        assert kwargs == {'psm': 11}
        return 'Городское фэнтези', 'done'
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    monkeypatch.setattr(ocr, 'recognize', recognize)
    asyncio.run(collect(job))
    asyncio.run(collect(job))
    current = next(r for r in catalog.search({})['items'] if r['id'] == banner['id'])
    assert current['ocr_text'] == current['clean_text'] == 'Городское фэнтези'
    assert current['field_sources']['ocr_pipeline'] == ocr.SPARSE_REVISION
    assert current['field_sources']['crop'] == 'loaded_image_pixels'
    assert db.rows('SELECT text FROM ads WHERE id=?', (current['ad_id'],))[0]['text'] == 'Городское фэнтези'
    exported = catalog.export({'q': 'Городское'}, with_observations=True)
    assert len(exported['findings']) == len(exported['ads']) == 1
    assert exported['ads'][0]['text'] == 'Городское фэнтези'
    assert all(f['ocr_text'] == f['clean_text'] == 'Городское фэнтези' for f in exported['findings'])
    assert current['sightings'] == 3 and len(calls) == 1
    assert db.rows('SELECT id,fingerprint FROM findings WHERE id=?', (banner['id'],))[0] == before
    assert catalog.search({'q': 'Городское'})['total'] == 1
    assert catalog.search({'q': 'Тородское'})['total'] == 0


def test_at_banner_keeps_content_and_native_image_without_overlay(tmp_path, monkeypatch, site):
    import struct
    from backend import config
    monkeypatch.setattr(config, 'OCR', 'off')
    _, result, rows, _ = run(tmp_path, monkeypatch, [site.base + '/banner-text'])
    assert result['status'] == 'done' and len(rows) == 1
    row = rows[0]
    assert row['title'] == 'Северный ветер' and '/collection/99' in row['destination']
    assert 'Новые приключения' in row['clean_text'] and 'Что это?' not in row['text'] and 'Скрыть' not in row['text']
    assert 'COOKIE OVERLAY' not in row['clean_text']
    assert row['field_sources']['crop'] == 'loaded_image_pixels'
    raw = (tmp_path / 'evidence' / row['crop']).read_bytes()
    assert struct.unpack('>II', raw[16:24]) == (200, 200)  # natural image, not its 100x100 covered screen rectangle


def test_sparse_banner_ocr_does_not_promote_artwork_noise():
    from backend.ocr import sparse_text
    tsv = 'page_num\tblock_num\tpar_num\tline_num\tconf\ttext\n'
    tsv += '1\t1\t1\t1\t27.3\t(Г\n1\t2\t1\t1\t35.4\tAf\n'
    tsv += '1\t3\t1\t1\t90.7\tЮмор\n1\t3\t1\t1\t88.2\tи\n1\t3\t1\t1\t91\tмагия\n'
    tsv += '1\t4\t1\t1\t90\t—\n1\t4\t1\t1\t75\t®\n'
    assert sparse_text(tsv) == 'Юмор и магия'


def test_real_tesseract_reads_city_fantasy_without_changing_evidence(monkeypatch):
    import hashlib
    import os
    from backend import config, ocr
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    data = Path(os.environ.get('LOCALAPPDATA', '.')) / 'Tesseract-OCR' / 'tessdata'
    if (data / 'rus.traineddata').is_file():
        monkeypatch.setattr(config, 'TESSDATA_DIR', str(data))
    if not ocr.command() or 'rus' not in ocr.engine()['installed_langs']:
        pytest.skip('Требуется Tesseract с русским языком')
    image = FIXTURES / 'at' / 'urban_fantasy.png'
    original = hashlib.sha256(image.read_bytes()).hexdigest()
    assert ocr.recognize(image, psm=11) == ('Городское фэнтези', 'done')
    assert hashlib.sha256(image.read_bytes()).hexdigest() == original


def test_empty_slot_no_blocks_and_access_error_differ(tmp_path, monkeypatch, site):
    _, result, rows, pages = run(tmp_path, monkeypatch, [site.base + '/empty', site.base + '/plain', site.base + '/blocked'])
    empty, plain, blocked = pages
    assert (empty['status'], empty['error_code']) == ('empty', 'EMPTY_SLOT') and 'пустой слот' in empty['message']
    assert (plain['status'], plain['error_code']) == ('empty', 'NO_BLOCKS') and 'известных рекламных мест на странице нет' in plain['message']
    assert blocked['status'] == 'user_action' and blocked['error_code'] == 'USER_ACTION'
    assert all(page['screenshot'] for page in pages)  # a zero result keeps its snapshot
    codes = {row['error_code'] for row in db.rows('SELECT error_code FROM logs WHERE error_code IS NOT NULL')}
    assert {'EMPTY_SLOT', 'NO_BLOCKS', 'USER_ACTION'} <= codes


def test_job_selector_wins_and_keeps_fingerprints(tmp_path, monkeypatch, site):
    # With an own selector the adapter neither adds blocks nor changes their borders: 0.6 fingerprints stay valid.
    job, _, rows, _ = run(tmp_path, monkeypatch, [site.base + '/'], selector='#paid-1')
    assert len(rows) == 1 and rows[0]['block_type'] == 'ad'
    row = db.rows('SELECT * FROM findings')[0]
    assert row['fingerprint'] == fingerprint(job['profile_id'], site.base + '/', row['frame_url'], row)
    assert row['text'].startswith('Реклама')  # the exact block of the selector, not a wider label container


def test_old_findings_get_a_type_once(tmp_path, monkeypatch):
    shutil.copy(FIXTURES / 'radar-0.6.2-fixture.db', tmp_path / 'radar.sqlite')
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    rows = db.rows('SELECT id,block_type,block_reason,adapter FROM findings ORDER BY id')
    assert len(rows) == 6 and all(r['block_type'] in ('ad', 'recommendation', 'unknown') and r['adapter'] == 'legacy' for r in rows)
    assert all('запись до этапа 11' in r['block_reason'] for r in rows)
    db.execute("UPDATE findings SET block_type='ad', block_reason='проверено вручную' WHERE id=1")
    db.init()
    assert db.rows('SELECT block_type,block_reason FROM findings WHERE id=1')[0] == {'block_type': 'ad', 'block_reason': 'проверено вручную'}
    assert db.rows('SELECT id,block_type,block_reason,adapter FROM findings WHERE id>1 ORDER BY id') == rows[1:]


def test_api_filter_adapters_and_exports(tmp_path, monkeypatch, site):
    from fastapi.testclient import TestClient
    from backend import config, main
    run(tmp_path, monkeypatch, [site.base + '/'])
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as client:
        assert client.get('/api/findings', params={'block_type': 'ad'}).json()['total'] == 1
        assert client.get('/api/findings', params={'block_type': 'recommendation'}).json()['total'] == 2
        assert client.get('/api/ads', params={'block_type': 'ad'}).json()['total'] == 1
        assert client.get('/api/findings', params={'block_type': 'banner'}).status_code == 400
        listed = client.get('/api/adapters').json()
        assert {a['id'] for a in listed['adapters']} == {'author_today', 'generic'} and set(listed['block_types']) == {'ad', 'recommendation', 'unknown'}
        import csv, io
        table = list(csv.reader(io.StringIO(client.get('/api/export.csv', params={'block_type': 'ad'}).text.lstrip('﻿'))))
        assert table[0][21:26] == ['block_type', 'block_reason', 'adapter', 'advertiser', 'target_url'] and len(table) == 2 and table[1][21] == 'ad'
        document = client.get('/api/export.json', params={'block_type': 'recommendation'}).json()
        assert document['counts']['findings'] == 2 and all(f['block_type'] == 'recommendation' for f in document['findings'])
