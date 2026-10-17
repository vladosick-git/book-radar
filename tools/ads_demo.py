"""Local live-ad demonstration. Persistent isolated data, manual jobs, no invented findings."""
import argparse
import json
import os
import re
from pathlib import Path
import socket
import sys
import threading
import webbrowser
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'ads-live-demo-v1'


def seed():
    from backend import db
    current = db.rows('SELECT value FROM settings WHERE key=?', (MARKER,))
    if current:
        return json.loads(current[0]['value'])
    if db.rows('SELECT id FROM profiles LIMIT 1'):
        raise RuntimeError('Для демонстрации нужен пустой отдельный каталог данных.')
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Книжная реклама · живой сбор', db.now()))
    job = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
        ('Книжная реклама AT · Librain', profile, json.dumps(['https://librain.ru/']), '', 30, db.now()))
    result = {'profile': profile, 'job': job}
    db.execute('INSERT INTO settings(key,value) VALUES (?,?)', (MARKER, json.dumps(result)))
    return result


def application(shutdown=None):
    from backend import db, config, main
    from contextlib import asynccontextmanager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from tools import local_control
    from starlette.routing import Mount, Route

    @asynccontextmanager
    async def lifespan(app):
        async with main.app.router.lifespan_context(main.app):
            seed()
            yield

    async def info(request):
        return JSONResponse({'purpose': MARKER, 'version': config.VERSION, 'root': str(ROOT),
            'data': str(db.DATA.resolve()), 'browser': Path(config.BROWSER_EXECUTABLE).name or 'chromium'})
    async def stop(request):
        if shutdown is None or not local_control.allowed(request, db.DATA):
            return JSONResponse({'detail': 'Управление доступно только локальному launcher.'}, status_code=403)
        from backend import runs
        if main.open_profiles or main.mail_tasks or runs.leased_profiles() or any(r['status'] in runs.ACTIVE for r in runs.public_runs()):
            return JSONResponse({'detail': 'Сначала остановите задания и закройте окна ботов.'}, status_code=409)
        shutdown()
        return JSONResponse({'status': 'stopping'})
    return Starlette(routes=[Route('/api/ads-demo/info', info), Route('/api/ads-demo/stop', stop, methods=['POST']), Mount('/', main.app)], lifespan=lifespan)


def serve():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8794)
    parser.add_argument('--data', type=Path, default=ROOT / 'data/ads-demo')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    data = args.data.resolve()
    if data in {ROOT / 'data', ROOT / 'data/at-demo', ROOT / 'data/at-onboarding'}:
        parser.error('Use a separate ads-demo data directory.')
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid port.')
    url = f'http://127.0.0.1:{args.port}'
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            with urlopen(url + '/api/ads-demo/info', timeout=3) as response:
                info = json.load(response)
            if info.get('purpose') != MARKER or info.get('root') != str(ROOT) or info.get('data') != str(data):
                raise SystemExit('Port occupied by another process. Nothing was stopped.')
            with urlopen(url + '/api/state', timeout=3) as response:
                state = json.load(response)['server']
            if state.get('simulation_enabled') or state.get('preview_fixture') or not state.get('worker_enabled'):
                raise SystemExit('Running panel has simulation, preview, or a disabled worker. Restart it in normal mode. Nothing was stopped.')
            # After an update the old process keeps serving the old code: say so instead of silently opening it.
            # backend.config is not imported here: it reads RADAR_* at import time, before the demo sets them.
            current = re.search(r"^VERSION = '([^']+)'", (ROOT / 'backend/config.py').read_text(encoding='utf-8'), re.M)
            if current and info.get('version') != current.group(1):
                raise SystemExit(f'Running demo is version {info.get("version")}, files are {current.group(1)}. '
                                 'Close the demo window (Ctrl+C), then start Demo_Ads.cmd again. Nothing was stopped.')
            if args.open:
                webbrowser.open(url)
            return
    executable = os.environ.get('RADAR_BROWSER_EXECUTABLE', '')
    if not executable and os.name == 'nt':
        choices = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Yandex/YandexBrowser/Application/browser.exe',
                   Path(os.environ.get('LOCALAPPDATA', '')) / 'Yandex/YandexBrowser/Application/browser.exe']
        executable = next((str(p) for p in choices if p.is_file()), '')
    # Without a vendor browser, use the installed Playwright Chromium. TLS remains strict.
    os.environ.update(RADAR_BROWSER_EXECUTABLE=executable, RADAR_DATA_DIR=str(data), RADAR_MODE='local',
        RADAR_AUTH='0', RADAR_SIMULATION='0', RADAR_VISUAL_PREVIEW='0', RADAR_ALLOW_TEST_COLLECTION='0', RADAR_PRESERVE_DATA='1', RADAR_EMBEDDED_WORKER='1', RADAR_MAX_BROWSERS='1',
        RADAR_ALLOWED_HOSTS='127.0.0.1,localhost', RADAR_COOKIE_SECURE='0')
    sys.path.insert(0, str(ROOT))
    import uvicorn
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f'Book Radar: {url}\nData: {data}\nNormal mode, collection worker enabled. Stop: Ctrl+C.')
    if os.environ.get('RADAR_DESKTOP_BACKGROUND') == '1':
        from tools import local_control
        local_control.write_token(data)
        server = uvicorn.Server(uvicorn.Config(application(lambda: setattr(server, 'should_exit', True)), host='127.0.0.1', port=args.port, log_level='warning'))
        server.run()
    else:
        uvicorn.run(application(), host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    serve()
