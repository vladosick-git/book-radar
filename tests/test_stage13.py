"""Stage 13: DOM text separate from the cleaned text, the source of every field, explicit cases (image, CSS background,
canvas, closed and readable iframe, no picture), a crop of each block cut at the right place, and local OCR through
the Tesseract adapter (here a stand-in binary: the real engine is checked by tools/checks/batch2_live.py when installed)."""
import asyncio
import json
import os
import struct
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend import catalog, config, db, ocr
from backend.collector import collect
from backend.identity import fingerprint

COLORS = {'img': (200, 30, 30), 'css': (30, 160, 60), 'canvas': (40, 70, 200), 'text': (230, 180, 20), 'inner': (120, 40, 160)}


def png_pixel(path, x, y):
    """Minimal PNG reader (8-bit RGB/RGBA, no interlace) for checking where a crop was cut."""
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, chunks, header = 8, b'', None
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            chunks += body
        pos += 12 + length
    width, height, depth, color, _, _, interlace = header
    assert depth == 8 and interlace == 0 and color in (2, 6)
    channels = 3 if color == 2 else 4
    raw, stride = zlib.decompress(chunks), width * channels
    previous, offset = bytearray(stride), 0
    for row in range(y + 1):
        kind, line = raw[offset], bytearray(raw[offset + 1:offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        previous = line
    return tuple(previous[x * channels:x * channels + 3]), (width, height)


def close(color, expected, tolerance=12):
    return all(abs(a - b) <= tolerance for a, b in zip(color, expected))


def rgb(name):
    return 'rgb({},{},{})'.format(*COLORS[name])


PIXEL = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><rect width='10' height='10' fill='red'/></svg>"


class Sites:
    """Main site and a second origin for frames."""

    def __init__(self):
        outer = self

        class Ads(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/closed':
                    body = '<body style="margin:0;background:#333"><p style="color:#fff">Внутри закрытого фрейма</p></body>'
                elif self.path == '/readable':
                    body = (f'<body style="margin:0"><article data-ad style="background:{rgb("inner")};padding:12px;width:240px;height:70px">'
                            '<h3>Книга во фрейме</h3><a href="https://author.test/frame-book">Читать</a></article></body>')
                else:
                    body = ''
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body.encode())

        self.ads = ThreadingHTTPServer(('127.0.0.1', 0), Ads)
        ads_base = f'http://127.0.0.1:{self.ads.server_port}'
        self.ads_base = ads_base

        class Main(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                block = 'style="width:300px;height:110px;margin:10px;padding:8px;box-sizing:border-box;'
                body = f'''<!doctype html><html><body style="margin:0;font:14px sans-serif">
                <article data-ad id="img" {block}background:{rgb("img")}">
                  <small>Реклама</small><h2>Город за туманом</h2><img src="{PIXEL}" alt="обложка" width="20" height="20">
                  <p>Прочитайте   первую главу</p><p>Прочитайте   первую главу</p><a href="https://author.test/work/1?utm_source=ad">Открыть книгу</a></article>
                <div data-ad id="css" {block}background:{rgb("css")} url('/bg.png') no-repeat 400px 400px"><span>Новинка недели</span></div>
                <div data-ad id="canvas" {block}background:{rgb("canvas")}"><canvas width="200" height="60" style="margin:10px"></canvas></div>
                <div data-ad id="closed" style="width:320px;height:120px;margin:10px"><iframe src="{ads_base}/closed" width="300" height="100" style="border:0"></iframe></div>
                <div data-ad id="readable" style="width:320px;height:120px;margin:10px"><iframe src="{ads_base}/readable" width="300" height="100" style="border:0"></iframe></div>
                <div style="height:1400px"></div>
                <section data-ad id="text" {block}background:{rgb("text")}">Реклама<br>Скидки на всю серию<br>Только до пятницы</section>
                <script>const c=document.querySelector('canvas').getContext('2d');c.fillStyle="#fff";c.fillRect(0,0,200,60);c.fillStyle="#000";c.font="bold 26px sans-serif";c.fillText("ЧИТАЙ ДАРОМ",8,40);</script>
                </body></html>'''
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body.encode())

        self.main = ThreadingHTTPServer(('127.0.0.1', 0), Main)
        self.base = f'http://127.0.0.1:{self.main.server_port}'
        for server in (self.ads, self.main):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self):
        self.main.shutdown(); self.ads.shutdown()


@pytest.fixture
def sites():
    instance = Sites()
    yield instance
    instance.close()


def run_job(tmp_path, sites, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Профиль', db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                        ('Стенд', profile, json.dumps([sites.base]), '[data-ad]', 2, db.now()))
    job = db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]
    result = asyncio.run(collect(job))
    rows = db.rows('SELECT * FROM findings ORDER BY id')
    for row in rows:
        row['field_sources'] = json.loads(row['field_sources'])
    return job, result, rows


def by_title(rows, title):
    found = [row for row in rows if row['title'] == title]
    assert len(found) == 1, [row['title'] for row in rows]
    return found[0]


def test_explicit_cases_text_and_field_sources(tmp_path, sites, monkeypatch):
    monkeypatch.setattr(config, 'OCR', 'off')
    job, result, rows = run_job(tmp_path, sites, monkeypatch)
    assert result['status'] == 'done'
    titles = sorted(row['title'] for row in rows)
    assert titles == sorted(['Город за туманом', 'Новинка недели', 'Баннер без текста', 'Рекламный фрейм', 'Книга во фрейме', 'Скидки на всю серию'])
    image = by_title(rows, 'Город за туманом')
    # DOM text is kept as read (it is part of the fingerprint); the cleaned copy drops the label and the repeated line.
    assert 'Реклама' in image['text'] and image['text'].count('Прочитайте') == 2
    assert image['clean_text'] == 'Город за туманом\nПрочитайте первую главу\nОткрыть книгу'
    assert image['image_source'] == 'img' and image['kind'] == 'mixed'
    assert image['field_sources'] == {'title': 'heading', 'text': 'dom', 'destination': 'link', 'image': 'img', 'frame_src': ''}
    css = by_title(rows, 'Новинка недели')
    assert css['image_source'] == 'css' and css['image_url'].endswith('/bg.png') and css['field_sources']['title'] == 'text'
    canvas = by_title(rows, 'Баннер без текста')
    assert canvas['image_source'] == 'canvas' and canvas['image_url'] == '' and canvas['kind'] == 'banner'
    closed = by_title(rows, 'Рекламный фрейм')
    assert closed['image_source'] == 'iframe' and closed['kind'] == 'iframe' and closed['image_url'] == f'{sites.ads_base}/closed'
    inner = by_title(rows, 'Книга во фрейме')
    assert inner['frame_url'] == f'{sites.ads_base}/readable' and inner['destination'] == 'https://author.test/frame-book'
    assert not any(row['image_url'] == f'{sites.ads_base}/readable' for row in rows)  # the readable frame has no placeholder
    text = by_title(rows, 'Скидки на всю серию')
    assert text['image_source'] == 'none' and text['kind'] == 'text' and text['clean_text'] == 'Скидки на всю серию\nТолько до пятницы'
    assert all(row['ocr_status'] in ('off', '') for row in rows)
    # Fingerprints still follow the 0.6 rule: a repeat run adds sightings, not new placements.
    asyncio.run(collect(job))
    assert db.rows('SELECT COUNT(*) AS n FROM findings')[0]['n'] == 6
    assert {row['sightings'] for row in db.rows('SELECT sightings FROM findings')} == {2}
    assert all(row['fingerprint'] == fingerprint(job['profile_id'], sites.base, row['frame_url'], row) for row in db.rows('SELECT * FROM findings'))


def test_crops_are_cut_at_the_block(tmp_path, sites, monkeypatch):
    monkeypatch.setattr(config, 'OCR', 'off')
    _, _, rows = run_job(tmp_path, sites, monkeypatch)
    expected = {'Город за туманом': 'img', 'Новинка недели': 'css', 'Баннер без текста': 'canvas', 'Скидки на всю серию': 'text', 'Книга во фрейме': 'inner'}
    for title, color in expected.items():
        row = by_title(rows, title)
        assert row['crop'] and row['crop'] != row['screenshot']
        pixel, size = png_pixel(tmp_path / 'evidence' / row['crop'], 3, 3)
        assert close(pixel, COLORS[color]), (title, pixel)
        if color != 'inner':
            assert abs(size[0] - 300) <= 2 and abs(size[1] - 110) <= 2, (title, size)  # also the block 1400 px below the fold
    first_crops = {row['id']: row['crop'] for row in rows}
    # Crops are taken once per placement, not on every sighting.
    job = db.rows('SELECT * FROM jobs')[0]
    asyncio.run(collect(job))
    assert {row['id']: row['crop'] for row in db.rows('SELECT id,crop FROM findings')} == first_crops


@pytest.fixture
def fake_tesseract(tmp_path, monkeypatch):
    script = tmp_path / 'fake_tesseract.py'
    script.write_text('import sys\n'
                      'open(sys.argv[0] + ".args", "a", encoding="utf-8").write(" ".join(sys.argv[1:]) + "\\n")\n'
                      'if "--version" in sys.argv: print("tesseract 5.4.0 (stand-in)"); sys.exit(0)\n'
                      'if "--list-langs" in sys.argv: print("List of available languages:\\neng\\nrus"); sys.exit(0)\n'
                      'sys.stdout.buffer.write("  РАСПРОДАЖА   КНИГ |\\n\\n Скидка 50% \\n".encode("utf-8"))\n', encoding='utf-8')
    launcher = tmp_path / ('tesseract.cmd' if sys.platform == 'win32' else 'tesseract')
    if sys.platform == 'win32':
        # cmd.exe reads a batch file in the console code page: switch to UTF-8 first (the Python path may contain Cyrillic).
        launcher.write_text(f'@chcp 65001 >nul\r\n@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    monkeypatch.setattr(config, 'TESSERACT_CMD', str(launcher))
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    return launcher


def test_ocr_reads_image_blocks_only_and_feeds_search(tmp_path, sites, monkeypatch, fake_tesseract):
    data = tmp_path / 'data'
    _, _, rows = run_job(data, sites, monkeypatch)
    for row in rows:
        if row['image_source'] in ocr.IMAGE_SOURCES:
            assert row['ocr_status'] == 'done' and row['ocr_text'] == 'РАСПРОДАЖА КНИГ\nСкидка 50%', row['title']
        else:
            assert row['ocr_status'] == '' and row['ocr_text'] == ''
    found = catalog.search({'q': 'распродажа'})
    assert found['total'] == 4 and {item['image_source'] for item in found['items']} == {'img', 'css', 'canvas', 'iframe'}
    info = ocr.engine()
    assert info['available'] and info['enabled'] and 'rus' in info['installed_langs'] and info['version'].startswith('tesseract')


def test_missing_engine_is_a_status_not_a_failure(tmp_path, sites, monkeypatch):
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    monkeypatch.setattr(config, 'TESSERACT_CMD', str(tmp_path / 'nowhere.exe'))
    monkeypatch.setattr(ocr, 'WINDOWS_DEFAULT', tmp_path / 'nowhere' / 'tesseract.exe')
    monkeypatch.setattr(ocr.shutil, 'which', lambda name: None)
    _, result, rows = run_job(tmp_path / 'data', sites, monkeypatch)
    assert result['status'] == 'done'
    assert {row['ocr_status'] for row in rows if row['image_source'] in ocr.IMAGE_SOURCES} == {'missing'}
    assert ocr.recognize(tmp_path / 'x.png') == ('', 'missing')
    monkeypatch.setattr(config, 'OCR', 'off')
    assert ocr.recognize(tmp_path / 'x.png') == ('', 'off')


def test_tessdata_dir_is_passed_to_the_engine(tmp_path, sites, monkeypatch, fake_tesseract):
    # Windows without administrator rights: languages live in a user folder, passed as --tessdata-dir.
    folder = str(tmp_path / 'my-tessdata')
    monkeypatch.setattr(config, 'TESSDATA_DIR', folder)
    run_job(tmp_path / 'data', sites, monkeypatch)
    assert ocr.engine()['installed_langs'] == ['eng', 'rus'] and ocr.engine()['missing_langs'] == []
    calls = (tmp_path / 'fake_tesseract.py.args').read_text(encoding='utf-8').splitlines()
    recognitions = [c for c in calls if ' stdout ' in c]
    assert recognitions and all(c.startswith(f'--tessdata-dir {folder} ') for c in recognitions)
    assert all(c.startswith(f'--tessdata-dir {folder} ') for c in calls if '--list-langs' in c)


REAL_TESSDATA = os.environ.get('RADAR_TESSDATA_DIR') or str(Path(os.environ.get('LOCALAPPDATA', '.')) / 'Tesseract-OCR' / 'tessdata')


@pytest.mark.skipif(ocr.command() is None, reason='Tesseract не установлен')
def test_real_tesseract_reads_cyrillic_on_canvas(tmp_path, sites, monkeypatch):
    monkeypatch.setattr(config, 'OCR', 'tesseract')
    monkeypatch.setattr(config, 'TESSERACT_CMD', '')
    if Path(REAL_TESSDATA, 'rus.traineddata').is_file():
        monkeypatch.setattr(config, 'TESSDATA_DIR', REAL_TESSDATA)
    if 'rus' not in ocr.engine()['installed_langs']:
        pytest.skip('нет русского пакета Tesseract')
    _, _, rows = run_job(tmp_path / 'data', sites, monkeypatch)
    canvas = by_title(rows, 'Баннер без текста')
    assert canvas['ocr_status'] == 'done' and 'ЧИТАЙ' in canvas['ocr_text'].upper(), canvas['ocr_text']
    assert catalog.search({'q': 'читай'})['total'] >= 1
