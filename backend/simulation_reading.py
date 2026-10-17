"""A visible, measured reading workflow on original local fixture books only."""
import asyncio
import html
import json
import math
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import Field, BaseModel, field_validator, model_validator

from . import db, simulation as sim

router = APIRouter(prefix='/api/simulation')
BOOKS = {
    'cloud-library': {'title': 'Библиотека на облаках', 'pages': 6, 'lines': [
        'Утром библиотекарь открыл окно, и в читальный зал вплыло небольшое облако. Оно попросило книгу о морских путешествиях.',
        'На верхней полке стоял атлас городов, которых ещё никто не построил. Между его страницами пахло дождём и свежей краской.',
        'Девочка оставила на столе бумажный кораблик. К вечеру кораблик успел обойти вокруг чашки и вернулся с запиской.',
        'Смотритель записал услышанную историю в зелёную тетрадь. За дверью терпеливо ждал следующий посетитель с зонтом.']},
    'future-post': {'title': 'Почтальон будущего', 'pages': 6, 'lines': [
        'Почтальон получил письмо, адресованное завтрашнему утру. Он положил его в карман рядом с компасом, который показывал время.',
        'У остановки росло дерево с металлическими листьями. Когда проезжал трамвай, дерево тихо звенело, будто встречало старого знакомого.',
        'В мастерской робот собирал часы из потерянных минут. Самую длинную минуту он берёг для разговора с другом.',
        'На площади появился новый почтовый ящик. В него опускали вопросы, а ответы приходили в виде совершенно обычных открыток.']},
}


def lines_for(book, page):
    # Our own synthetic prose, explicitly identified as a fixture; no downloaded books.
    return [f'{BOOKS[book]["lines"][i % 4]} Запись {page}.{i + 1} из тестового дневника.' for i in range(24)]


def init():
    sim.init()
    with db.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS simulation_read_runs (
          id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL REFERENCES simulation_profiles(id), book TEXT NOT NULL,
          start_page INTEGER NOT NULL, pages_requested INTEGER NOT NULL, seconds_per_page REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'running', completed INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS simulation_read_pages (
          run_id INTEGER NOT NULL REFERENCES simulation_read_runs(id), page INTEGER NOT NULL,
          opened_at REAL NOT NULL, completed_at TEXT, elapsed_ms INTEGER, scroll_events INTEGER,
          scroll_fraction REAL, sample_index INTEGER, sample_text TEXT, screenshot TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(run_id,page));
        CREATE TABLE IF NOT EXISTS simulation_bookmarks (
          profile_id INTEGER NOT NULL REFERENCES simulation_profiles(id), book TEXT NOT NULL,
          last_page INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(profile_id,book));''')
        # Additive migration preserves previous fixture accounts, runs and bookmarks.
        c.execute('BEGIN IMMEDIATE')
        for table, column, kind in (
            ('simulation_read_runs', 'max_seconds_per_page', 'REAL'),
            ('simulation_read_pages', 'target_seconds', 'REAL')):
            if column not in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')


class ReadInput(sim.RunInput):
    book: str = 'cloud-library'
    pages: int = Field(default=3, ge=1, le=6)
    # Subsecond values are available to isolated automated checks; the panel uses >=60.
    min_seconds_per_page: float = Field(default=60, ge=.2, le=600)
    max_seconds_per_page: float = Field(default=120, ge=.2, le=600)
    resume: bool = True
    show_browser: bool = False

    @model_validator(mode='after')
    def ordered_time_range(self):
        if self.max_seconds_per_page < self.min_seconds_per_page:
            raise ValueError('Максимальное время должно быть не меньше минимального.')
        return self

    @field_validator('book')
    @classmethod
    def known_book(cls, value):
        if value not in BOOKS:
            raise ValueError('Выберите тестовую книгу.')
        return value


class ProgressInput(BaseModel):
    page: int = Field(ge=1, le=6)
    elapsed_ms: int = Field(ge=0, le=3600000)
    scroll_events: int = Field(ge=0, le=100000)
    scroll_fraction: float = Field(ge=0, le=1)
    sample_index: int = Field(ge=0, le=23)


@router.get('/reading')
def state():
    init()
    runs = db.rows('''SELECT r.*,p.name AS profile_name FROM simulation_read_runs r
        JOIN simulation_profiles p ON p.id=r.profile_id ORDER BY r.id DESC LIMIT 30''')
    for run in runs:
        run['pages'] = db.rows('''SELECT page,completed_at,elapsed_ms,scroll_events,scroll_fraction,sample_text,screenshot,target_seconds
            FROM simulation_read_pages WHERE run_id=? ORDER BY page''', (run['id'],))
    return {'books': {key: {'title': b['title'], 'pages': b['pages'], 'fixture': True} for key, b in BOOKS.items()},
            'runs': runs, 'bookmarks': db.rows('SELECT * FROM simulation_bookmarks')}


def owned_run(run_id, request):
    identity = sim.me(request)
    runs = db.rows('SELECT * FROM simulation_read_runs WHERE id=? AND profile_id=?', (run_id, identity['profile_id']))
    if not runs:
        raise HTTPException(404, 'Тестовый сеанс чтения не найден у этого аккаунта.')
    return runs[0]


def draw_dwell(minimum, maximum):
    low, high = math.ceil(minimum * 1000), math.floor(maximum * 1000)
    return (low + secrets.randbelow(max(0, high - low) + 1)) / 1000


@router.get('/site/read/{run_id}/{page}', response_class=HTMLResponse)
def reader(run_id: int, page: int, request: Request):
    init()
    run = owned_run(run_id, request)
    expected = run['start_page'] + run['completed']
    if run['status'] != 'running' or page != expected or not 1 <= page <= BOOKS[run['book']]['pages']:
        raise HTTPException(409, 'Страницы открываются последовательно в активном сеансе.')
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        prior = c.execute('SELECT target_seconds FROM simulation_read_pages WHERE run_id=? AND page=?', (run_id, page)).fetchone()
        dwell = prior['target_seconds'] if prior and prior['target_seconds'] is not None else draw_dwell(run['seconds_per_page'], run['max_seconds_per_page'] or run['seconds_per_page'])
        c.execute('INSERT OR IGNORE INTO simulation_read_pages(run_id,page,opened_at,target_seconds) VALUES (?,?,?,?)', (run_id, page, time.time(), dwell))
        if prior and prior['target_seconds'] is None:
            c.execute('UPDATE simulation_read_pages SET target_seconds=? WHERE run_id=? AND page=?', (dwell, run_id, page))
    body = ''.join(f'<p data-line="{i}">{html.escape(line)}</p>' for i, line in enumerate(lines_for(run['book'], page)))
    last = page == run['start_page'] + run['pages_requested'] - 1
    title = html.escape(BOOKS[run['book']]['title'])
    doc = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · страница {page}</title><style>body{{font:18px/1.8 Georgia,serif;max-width:680px;margin:24px auto;padding:16px}}p{{margin:24px 0}}aside,nav{{font:16px/1.5 system-ui;background:#e9f4ef;padding:16px}}button{{padding:14px;font:inherit;cursor:pointer}}nav{{margin-top:32px}}</style>
<aside>Встроенная симуляция · оригинальная тестовая книга · не метрика настоящей площадки</aside>
<h1>{title}</h1><p>Страница {page} из {BOOKS[run['book']]['pages']}</p>
<article id="reader" data-run="{run_id}" data-page="{page}">{body}</article>
<nav>Страница {page}. Выбрано случайное время: {dwell:g} сек.<br>
<input id="sample-index" type="hidden" value="0"><button id="next">{'Завершить чтение' if last else 'Следующая страница'}</button><p id="result" role="status"></p></nav>
<script>const started=performance.now();let steps=0,maxFraction=0;function fraction(){{const max=document.documentElement.scrollHeight-innerHeight;return max<=0?1:Math.min(1,scrollY/max);}}addEventListener('scroll',()=>{{steps++;maxFraction=Math.max(maxFraction,fraction());}});document.querySelector('#next').onclick=async()=>{{const out=document.querySelector('#result');try{{const page=Number(document.querySelector('#reader').dataset.page),run=Number(document.querySelector('#reader').dataset.run);const r=await fetch('/api/simulation/site/read/'+run+'/progress',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{page,elapsed_ms:Math.round(performance.now()-started),scroll_events:steps,scroll_fraction:Math.max(maxFraction,fraction()),sample_index:Number(document.querySelector('#sample-index').value)}})}});if(!r.ok)throw Error('Сначала прокрутите страницу и дождитесь указанного времени');const data=await r.json();if(data.next_page)location.assign('/api/simulation/site/read/'+run+'/'+data.next_page);else{{out.id='reading-complete';out.textContent='Чтение завершено. Прогресс сохранён.';document.querySelector('#next').disabled=true;}}}}catch(err){{out.textContent=err.message;out.dataset.error='true';}}}};</script></html>'''
    return HTMLResponse(doc, headers={'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'none'; object-src 'none'"})


@router.post('/site/read/{run_id}/progress')
def progress(run_id: int, payload: ProgressInput, request: Request):
    init()
    run = owned_run(run_id, request)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        # Re-read inside the write transaction: a replay may arrive after the first commit.
        run = dict(c.execute('SELECT * FROM simulation_read_runs WHERE id=?', (run_id,)).fetchone())
        page = c.execute('SELECT * FROM simulation_read_pages WHERE run_id=? AND page=?', (run_id, payload.page)).fetchone()
        if page and page['completed_at']:
            next_page = payload.page + 1 if payload.page < run['start_page'] + run['pages_requested'] - 1 else None
            return {'saved': True, 'next_page': next_page}
        if run['status'] != 'running' or payload.page != run['start_page'] + run['completed'] or not page:
            raise HTTPException(409, 'Сначала откройте следующую страницу текущего сеанса.')
        dwell = page['target_seconds'] if page['target_seconds'] is not None else run['seconds_per_page']
        if payload.scroll_fraction < .95 or payload.scroll_events < 1 or payload.elapsed_ms < dwell * 1000 or time.time() - page['opened_at'] < dwell:
            raise HTTPException(409, 'Прокрутка или время на странице ещё не подтверждены.')
        text = lines_for(run['book'], payload.page)[payload.sample_index]
        screenshot = f'sim-read-{run_id}-{payload.page}.png'
        if not (db.DATA / 'evidence' / screenshot).is_file():
            screenshot = ''
        now = db.now()
        c.execute('''UPDATE simulation_read_pages SET completed_at=?,elapsed_ms=?,scroll_events=?,scroll_fraction=?,
            sample_index=?,sample_text=?,screenshot=? WHERE run_id=? AND page=?''',
            (now, payload.elapsed_ms, payload.scroll_events, payload.scroll_fraction, payload.sample_index, text, screenshot, run_id, payload.page))
        completed = run['completed'] + 1
        done = completed == run['pages_requested']
        c.execute('UPDATE simulation_read_runs SET completed=?,status=?,finished_at=? WHERE id=?',
                  (completed, 'done' if done else 'running', now if done else None, run_id))
        c.execute('''INSERT INTO simulation_bookmarks(profile_id,book,last_page,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(profile_id,book) DO UPDATE SET last_page=excluded.last_page,updated_at=excluded.updated_at''',
            (run['profile_id'], run['book'], payload.page, now))
        c.execute('INSERT INTO simulation_events(profile_id,action,at) VALUES (?,?,?)', (run['profile_id'], 'page_read', now))
    return {'saved': True, 'next_page': None if done else payload.page + 1}


async def read_one(pid, origin, payload, fleet_run_id=None):
    init()
    origin = sim.local_origin(origin)
    profile = sim.claim_profile(pid, 'reading', lease=90 + payload.pages * (payload.max_seconds_per_page + 20), required_status='registered', fleet_run_id=fleet_run_id)
    run_id = None
    try:
        bookmark = db.rows('SELECT last_page FROM simulation_bookmarks WHERE profile_id=? AND book=?', (pid, payload.book))
        start = bookmark[0]['last_page'] + 1 if bookmark and payload.resume else 1
        count = min(payload.pages, BOOKS[payload.book]['pages'] - start + 1)
        if count <= 0:
            db.execute("UPDATE simulation_profiles SET status='registered',lease_until=0,message=? WHERE id=?", ('Тестовая книга уже завершена. Для нового чтения выключите продолжение с закладки.', pid))
            return {'profile_id': pid, 'status': 'already_finished'}
        async with sim.browser_session(profile, origin, headless=not payload.show_browser) as (_, page, denied):
            await sim.browser_identity(page, origin, profile)
            run_id = db.execute('''INSERT INTO simulation_read_runs(profile_id,book,start_page,pages_requested,seconds_per_page,max_seconds_per_page,created_at)
                VALUES (?,?,?,?,?,?,?)''', (pid, payload.book, start, count, payload.min_seconds_per_page, payload.max_seconds_per_page, db.now()))
            await page.goto(origin + f'/api/simulation/site/read/{run_id}/{start}', timeout=15000)
            for current in range(start, start + count):
                await page.locator(f'#reader[data-page="{current}"]').wait_for(timeout=15000)
                started = time.monotonic()
                dwell = db.rows('SELECT target_seconds FROM simulation_read_pages WHERE run_id=? AND page=?', (run_id, current))[0]['target_seconds']
                db.execute('UPDATE simulation_profiles SET lease_until=? WHERE id=?', (time.time() + payload.max_seconds_per_page + 90, pid))
                viewport = page.viewport_size
                await page.mouse.move(viewport['width'] // 2, viewport['height'] // 2)
                distance = await page.evaluate('Math.max(0,document.documentElement.scrollHeight-innerHeight)')
                wheel = max(120, int(viewport['height'] * .65))
                interval = max(.03, dwell / (math.ceil(distance / wheel) + 1))
                for _ in range(100):
                    bottom = await page.evaluate('scrollY + innerHeight >= document.documentElement.scrollHeight - 2')
                    if bottom:
                        break
                    await asyncio.sleep(interval)
                    await page.mouse.wheel(0, wheel)
                    await page.wait_for_timeout(30)
                else:
                    raise ValueError('Page did not finish scrolling')
                await asyncio.sleep(max(0, dwell + .05 - (time.monotonic() - started)))
                await page.locator('#sample-index').evaluate('(el, value) => el.value = value', secrets.randbelow(24))
                evidence = db.DATA / 'evidence' / f'sim-read-{run_id}-{current}.png'
                await page.screenshot(path=str(evidence), full_page=True)
                await page.locator('#next').click()
                await page.wait_for_function('previous => document.querySelector("#reading-complete") || Number(document.querySelector("#reader")?.dataset.page) !== previous', arg=current, timeout=15000)
                saved = db.rows('SELECT completed_at FROM simulation_read_pages WHERE run_id=? AND page=?', (run_id, current))
                if not saved or not saved[0]['completed_at'] or denied:
                    raise ValueError('Page progress not confirmed')
                db.execute('UPDATE simulation_profiles SET lease_until=? WHERE id=?', (time.time() + payload.max_seconds_per_page + 90, pid))
        final = db.rows('SELECT status,completed FROM simulation_read_runs WHERE id=?', (run_id,))[0]
        if final['status'] != 'done' or final['completed'] != count:
            raise ValueError('Reading incomplete')
        db.execute("UPDATE simulation_profiles SET status='registered',lease_until=0,message=?,updated_at=? WHERE id=?",
                   (f'Чтение симуляции завершено: страницы {start}–{start + count - 1}.', db.now(), pid))
        return {'profile_id': pid, 'status': 'done', 'run_id': run_id, 'pages': count}
    except (Exception, asyncio.CancelledError) as error:
        if run_id:
            db.execute("UPDATE simulation_read_runs SET status='failed',message=?,finished_at=? WHERE id=?", ('Чтение прервано; завершённые страницы и закладка сохранены.', db.now(), run_id))
        db.execute("UPDATE simulation_profiles SET status='failed',lease_until=0,message=?,updated_at=? WHERE id=?",
                   ('Чтение остановлено. Проверьте сессию и повторите с сохранённой закладки.', db.now(), pid))
        if isinstance(error, asyncio.CancelledError):
            raise
        return {'profile_id': pid, 'status': 'failed', 'run_id': run_id}


@router.post('/read')
async def read(payload: ReadInput, request: Request):
    init()
    origin = sim.local_origin(str(request.base_url))
    found = {r['id'] for r in db.rows('SELECT id FROM simulation_profiles')}
    if not set(payload.profile_ids) <= found:
        raise HTTPException(404, 'Один из тестовых профилей не найден.')
    return {'results': [await read_one(pid, origin, payload) for pid in payload.profile_ids]}


@router.get('/reading/{run_id}/screenshot/{page}')
def screenshot(run_id: int, page: int):
    init()
    rows = db.rows('SELECT screenshot FROM simulation_read_pages WHERE run_id=? AND page=? AND completed_at IS NOT NULL', (run_id, page))
    if not rows or not rows[0]['screenshot']:
        raise HTTPException(404, 'Снимок ещё не сохранён.')
    path = db.DATA / 'evidence' / Path(rows[0]['screenshot']).name
    if not path.is_file():
        raise HTTPException(404, 'Снимок страницы больше не доступен.')
    return FileResponse(path, media_type='image/png')
