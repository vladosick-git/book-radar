"""Explicit visual demo on a separate loopback-only database. Never collect from the fixture URLs."""
import asyncio
import argparse
import base64
import html
import json
import os
import shutil
import socket
import sys
import threading
import uuid
import webbrowser
from datetime import timedelta
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/design-preview'
PORT = 8799
ASSETS = ROOT / 'assets/visual-preview'
TITLES = ['Цикл книг. Хранитель забытой библиотеки', 'Изобретатель в городе магов', 'Лекарь северного королевства', 'Хроники ледяной столицы', 'Новый сезон любимых историй', 'Коллекция для спокойных вечеров']


async def seed():
    from backend import catalog, db, runs
    from playwright.async_api import async_playwright
    DATA.mkdir(parents=True, exist_ok=True)
    marker = DATA / '.visual-preview'
    if (DATA / 'radar.sqlite').exists() and not marker.exists():
        raise RuntimeError('Preview refused: existing database has no visual-preview marker.')
    marker.write_text('Illustrative design fixtures only. No actual ad collection or provider registration.\n', encoding='utf-8')
    db.init()
    if db.rows('SELECT id FROM jobs LIMIT 1'):
        return
    now = db.now()
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Основной браузер', now))
    plans = [('Книжные площадки', ['https://librain.ru/', 'https://readboom.net/'], 'interval', 30), ('Обзор Author.Today', ['https://librain.ru/', 'https://litnet.com/'], 'interval', 120), ('Вечерний сбор', ['https://readboom.net/'], 'daily', 0)]
    jobs = []
    for name, urls, kind, minutes in plans:
        job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at,schedule_kind,interval_minutes,daily_time,timezone,status,last_run,last_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (name, profile, json.dumps(urls), '', 2, now, kind, minutes, '20:00', 'Europe/Moscow', 'done', now, [12, 5, 3][len(jobs)]))
        jobs.append(db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={'width': 900, 'height': 620})
        for i, title in enumerate(TITLES):
            domain = ['author.today'] * 4 + ['rutube.ru', 'belleyou.ru']
            source = ['librain.ru', 'readboom.net', 'litnet.com', 'librain.ru', 'readboom.net', 'librain.ru'][i]
            job = jobs[i % 3]
            image = ASSETS / f'creative-{i + 1}.png'
            if not image.is_file():
                raise RuntimeError(f'Missing packaged visual fixture: {image.name}')
            crop = f'job-{job["id"]}-{uuid.uuid4().hex}.png'
            shot = f'job-{job["id"]}-{uuid.uuid4().hex}.png'
            shutil.copy2(image, DATA / 'evidence' / crop)
            encoded = base64.b64encode(image.read_bytes()).decode('ascii')
            await page.set_content(f'''<!doctype html><html lang="ru"><meta charset="utf-8"><style>
                *{{box-sizing:border-box}}body{{margin:0;background:#f5f6f4;font:16px Arial;color:#34423c}}header{{padding:20px 35px;border-bottom:1px solid #ddd;background:white}}
                main{{padding:25px 35px;display:grid;grid-template-columns:540px 240px;gap:30px}}article{{padding:18px;background:white}}h1{{font-size:24px}}p{{line-height:1.8;color:#66746b}}
                aside{{background:white;padding:12px;height:310px}}aside img{{width:216px;height:146px;object-fit:cover}}aside h2{{font-size:15px;line-height:1.4}}small{{color:#8a8d88}}
                </style><header>Книжная библиотека</header><main><article><h1>Истории, которые хочется читать</h1><p>Приключения · Фантастика · История</p><hr><p>Новые книги и продолжения знакомых историй.</p><p>Подборка книг за эту неделю</p><p>Библиотека · Авторы · Обсуждения</p></article><aside><small>Реклама · 16+</small><img src="data:image/png;base64,{encoded}"><h2>{html.escape(title)}</h2><small>{domain[i]}</small></aside></main></html>''')
            await page.screenshot(path=str(DATA / 'evidence' / shot))
            rect = await page.locator('aside').bounding_box()
            block_type = 'recommendation' if i == 2 else 'unknown' if i == 5 else 'ad'
            text = 'История о потерянной библиотеке и её новом хранителе. Читать первую книгу цикла.' if i == 0 else title
            result = catalog.save(job, {'title': title, 'text': text, 'clean_text': text, 'destination': f'https://{domain[i]}/', 'image_url': f'https://illustrative.invalid/creative-{i + 1}.png', 'crop': crop, 'block_type': block_type,
                'block_reason': 'Есть пометка «Реклама»' if block_type == 'ad' else 'Блок рекомендаций на книжном сайте' if block_type == 'recommendation' else 'Недостаточно рекламных признаков', 'ocr_text': 'Хранитель забытой библиотеки. Читать!' if i == 0 else '', 'ocr_status': 'done' if i == 0 else 'empty',
                'block_evidence': {'target_domains': [domain[i]], 'age_mark': '16+', 'page_rect': {**rect, 'page_width': 900, 'page_height': 620}}, 'field_sources': {'crop': 'loaded_image_pixels'}}, f'https://{source}/', f'https://{source}/', shot)
            count = [49, 18, 12, 31, 7, 4][i]
            seen = runs.iso(runs.utcnow() - timedelta(minutes=i))
            db.execute('UPDATE findings SET sightings=?,first_seen=?,last_seen=? WHERE id=?', (count, seen, seen, result['finding_id']))
            db.execute('UPDATE ads SET sightings=?,first_seen=?,last_seen=? WHERE id=?', (count, seen, seen, result['ad_id']))
            db.execute('UPDATE observations SET count=?,seen_at=? WHERE finding_id=?', (count, seen, result['finding_id']))
        await browser.close()
    for number in range(2, 11):
        db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (f'Обзор книг · профиль {number:02d}', now))
    for index, (status, done, total) in enumerate([('waiting_user', 11, 12), ('error', 8, 20), ('done', 20, 20), ('queued', 0, 12), ('running', 7, 20)]):
        job = jobs[[1, 2, 0, 1, 0][index]]
        with runs.tx() as connection:
            run_id = runs._insert_run(connection, job, 'manual', status=status)
            connection.execute('UPDATE runs SET pages_done=?,pages_total=?,found=?,started_at=?,heartbeat_at=?,error=? WHERE id=?', (done, total, done, now if status != 'queued' else None, now, 'Сайт не ответил вовремя.' if status == 'error' else 'Нужна ручная проверка страницы.' if status == 'waiting_user' else None, run_id))
    db.log(None, 'info', 'Загружены иллюстративные данные визуального предпросмотра. Внешний сбор выключен.')


def main():
    global DATA, PORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--data-dir', type=Path, default=DATA)
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Use a local unprivileged port from 1024 to 65535.')
    DATA, PORT = args.data_dir.resolve(), args.port
    sys.path.insert(0, str(ROOT))
    url = f'http://127.0.0.1:{PORT}'
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', PORT))
        except OSError:
            try:
                from backend.config import VERSION
                with urlopen(url + '/api/design/info', timeout=3) as response:
                    info = json.load(response)
                assert info == {'purpose': 'book-radar-visual-preview', 'version': VERSION, 'root': str(ROOT), 'data': str(DATA)}
            except Exception:
                raise SystemExit('Port 8799 is occupied or an older preview is running. Nothing was stopped.')
            if '--open' in sys.argv:
                webbrowser.open(url)
            return
    os.environ.update(RADAR_DATA_DIR=str(DATA), RADAR_MODE='local', RADAR_AUTH='0', RADAR_SIMULATION='1', RADAR_VISUAL_PREVIEW='1', RADAR_EMBEDDED_WORKER='0', RADAR_ALLOWED_HOSTS='127.0.0.1,localhost', RADAR_COOKIE_SECURE='0')
    from backend import config, main as panel
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    import uvicorn
    asyncio.run(seed())

    async def info(request):
        return JSONResponse({'purpose': 'book-radar-visual-preview', 'version': config.VERSION, 'root': str(ROOT), 'data': str(DATA)})
    app = Starlette(routes=[Route('/api/design/info', info), Mount('/', panel.app)], lifespan=panel.lifespan)
    if '--open' in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f'Visual preview fixtures: {url}\nIsolated data: {DATA}\nCollection worker is disabled. These are illustrative examples.')
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')


if __name__ == '__main__':
    main()
