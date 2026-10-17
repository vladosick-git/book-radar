"""Isolated design preview; never uses the user's DB or contacts mail providers."""
import json
import os
from pathlib import Path
import shutil
import sys
from contextlib import asynccontextmanager

ROOT = Path(__file__).resolve().parents[1]
os.environ['RADAR_DATA_DIR'] = str(ROOT / 'review-data')
sys.path.insert(0, str(ROOT))
from backend import db, main
from backend.identity import fingerprint
from backend.protection import protect
from starlette.responses import JSONResponse
import uvicorn


def seed():
    if db.rows("SELECT value FROM settings WHERE key='handoff_demo_seed'"):
        return
    timestamp = db.now()
    profiles = []
    for title in ('Демо · Книжная реклама', 'Демо · Проверка регистрации', 'Демо · Чистый профиль'):
        profiles.append(db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (title, timestamp)))
    service = db.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
        ('Демо · Zoho Mail', 'демо · zoho mail', 'email', 'https://mail.zoho.eu', timestamp))
    for index, profile in enumerate(profiles, 1):
        email = f'reader{index:02d}@example.org'
        account = db.execute('INSERT INTO accounts(profile_id,service_id,status,identifier,created_at,updated_at) VALUES (?,?,?,?,?,?)',
            (profile, service, 'planned' if index == 1 else 'in_progress', email, timestamp, timestamp))
        status = ['prepared', 'created', 'unknown'][index-1]
        db.execute('''INSERT INTO mailboxes(account_id,region,zoid,email,password_cipher,status,remote_account_id,error,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (account, 'eu', '1234', email, protect(b'DEMO-PASSWORD-NOT-FOR-LOGIN'), status,
            '9876' if index == 2 else None, 'Демо: ответ запроса потерян; сначала проверить в Zoho.' if index == 3 else '', timestamp, timestamp))
    jobs = []
    for name, status, count, error in [
        ('Демо · Новый сбор', 'ready', 0, None),
        ('Демо · Книжные объявления', 'done', 3, None),
        ('Демо · Частичный результат', 'partial', 1, 'Демо: одна страница недоступна; найденные блоки сохранены.')]:
        jobs.append(db.execute('''INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,status,created_at,last_run,last_count,last_error,headed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (name, profiles[0], json.dumps(['http://127.0.0.1:8770/sample']), '', 5, status,
                timestamp, timestamp if status != 'ready' else None, count, error, 0)))
    filename = f'job-{jobs[1]}-' + '1'*32 + '.png'
    shutil.copy2(ROOT / 'docs/test-finding.png', db.DATA / 'evidence' / filename)
    for index, (title, text) in enumerate([
        ('Демо · Город за туманом', 'Синтетическое объявление: приключение начинается с исчезнувшей карты.'),
        ('Демо · Библиотека забытых имён', 'Пример длинного заголовка и текста для проверки карточки находки.'),
        ('Демо · Путь к северному морю', 'Демонстрационные данные. Это не результат сбора рекламы с реального сайта.')], 1):
        ad = {'title': title, 'text': text, 'destination': f'https://example.org/book/{index}', 'image_url': ''}
        source = 'http://127.0.0.1:8770/sample'
        db.execute('''INSERT INTO findings(fingerprint,profile_id,job_id,title,text,destination,image_url,source_url,frame_url,
            screenshot,first_screenshot,first_seen,last_seen,sightings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (fingerprint(profiles[0], source, source, ad), profiles[0], jobs[1], title, text, ad['destination'], '', source, source,
                filename, filename, timestamp, timestamp, index*3))
    db.log(jobs[1], 'info', 'Демо: три синтетические находки добавлены для проверки интерфейса.')
    db.log(jobs[2], 'warning', 'Демо: часть страниц недоступна, результаты сохранены.')
    db.log(None, 'error', 'Демо: результат регистрации неизвестен. Повтор запрещён до проверки.')
    db.execute("INSERT INTO settings(key,value) VALUES ('handoff_demo_seed','1')")


def seed_edge_cases():
    """0.5 review: long names, more catalog items and every mailbox/account status. Synthetic only."""
    if db.rows("SELECT value FROM settings WHERE key='handoff_demo_seed_v2'"):
        return
    timestamp = db.now()
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)',
        ('Демо · Профиль с очень длинным названием для проверки переносов в карточках и списках', timestamp))
    job = db.execute('''INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,status,created_at,last_run,last_count,last_error,headed)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''', ('Демо · Ошибка загрузки: очень длинное название задания, которое не должно ломать строку списка',
        profile, json.dumps(['http://127.0.0.1:8770/sample', 'https://example.org/very/long/path/that/keeps/going/and/going?ref=demo']),
        '#atRecs .f-book-container', 8, 'error', timestamp, timestamp, 0, 'Демо: Страница вернула HTTP 503. Найденные блоки сохранены.', 1))
    db.log(job, 'error', 'Демо: https://example.org/very/long/path: Страница вернула HTTP 503. Найденные блоки сохранены.')
    db.log(job, 'warning', 'Блоки не найдены. Проверьте скриншот и CSS-селектор: это не доказывает отсутствие рекламы.')
    first_profile = db.rows('SELECT id FROM profiles ORDER BY id LIMIT 1')[0]['id']
    shot = db.rows('SELECT screenshot FROM findings LIMIT 1')[0]['screenshot']
    source = 'http://127.0.0.1:8770/sample'
    for index, title in enumerate(['Демо · Очень длинный заголовок объявления, который занимает больше двух строк в карточке каталога и должен аккуратно обрезаться',
                                   'Демо · Сага о ветре', 'Демо · Без снимка', 'Демо · Хроники маяка', 'Демо · Северный экспресс'], 4):
        ad = {'title': title, 'text': f'Синтетический текст объявления №{index}.', 'destination': f'https://example.org/book/{index}', 'image_url': ''}
        db.execute('''INSERT INTO findings(fingerprint,profile_id,job_id,title,text,destination,image_url,source_url,frame_url,
            screenshot,first_screenshot,first_seen,last_seen,sightings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (fingerprint(first_profile, source, source, ad), first_profile, job, title, ad['text'], ad['destination'], '', source, source,
             '' if 'Без снимка' in title else shot, '' if 'Без снимка' in title else shot, timestamp, timestamp, index))
    service = db.rows('SELECT id FROM services LIMIT 1')[0]['id']
    site = db.execute('INSERT INTO services(name,name_key,kind,registration_url,created_at) VALUES (?,?,?,?,?)',
        ('Демо · Площадка', 'демо · площадка', 'site', 'https://example.org/signup', timestamp))
    db.execute('INSERT INTO accounts(profile_id,service_id,status,identifier,note,created_at,updated_at,confirmed_at) VALUES (?,?,?,?,?,?,?,?)',
        (first_profile, site, 'confirmed', 'demo-reader', 'Демо: подтверждено вручную пользователем.', timestamp, timestamp, timestamp))
    account = db.execute('INSERT INTO accounts(profile_id,service_id,status,identifier,created_at,updated_at) VALUES (?,?,?,?,?,?)',
        (profile, service, 'in_progress', 'reader04@example.org', timestamp, timestamp))
    db.execute('''INSERT INTO mailboxes(account_id,region,zoid,email,password_cipher,status,error,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)''', (account, 'eu', '1234', 'reader04@example.org', protect(b'DEMO-PASSWORD-NOT-FOR-LOGIN'), 'failed',
        'Демо: Нет права создавать пользователей или доступ запрещён. HTTP 403.', timestamp, timestamp))
    db.execute("INSERT INTO settings(key,value) VALUES ('handoff_demo_seed_v2','1')")


@asynccontextmanager
async def demo_lifespan(app):
    async with main.lifespan(app):
        seed()
        seed_edge_cases()
        yield


main.app.router.lifespan_context = demo_lifespan


class DemoApplication:
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path = scope['path']
            blocked = (scope['method'] == 'POST' and (
                path == '/api/mailboxes/run'
                or path.endswith('/open')
                or (path.startswith('/api/jobs/') and path.endswith('/run'))))
            if blocked:
                await JSONResponse({'detail': 'Деморежим: внешняя регистрация, открытие профилей и запуск браузерного сбора отключены.'}, status_code=403)(scope, receive, send)
                return
        await main.app(scope, receive, send)


if __name__ == '__main__':
    print('Design demo on 127.0.0.1:8770. Synthetic records only. Ctrl+C to stop.', flush=True)
    uvicorn.run(DemoApplication(), host='127.0.0.1', port=8770)
