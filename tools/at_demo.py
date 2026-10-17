"""Persistent, explicitly labelled local AT demo. Bind to loopback only; never use the normal client DB."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount, Route

from backend import config, db, main

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'at-demo-v1'


def seed(port):
    """Idempotent setup of one fresh profile and two unscheduled jobs, without invented findings."""
    rows = db.rows('SELECT value FROM settings WHERE key=?', (MARKER,))
    if rows:
        return json.loads(rows[0]['value'])
    if db.rows('SELECT id FROM profiles LIMIT 1'):
        raise RuntimeError('Refusing to seed a non-empty database without the demo marker.')
    profile = db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', ('Демо AT · отдельный профиль', db.now()))
    jobs = {}
    for key, name, urls in [
        ('live', 'AT · публичные страницы — реальный сайт', ['https://author.today/', 'https://author.today/work/genre/all']),
        ('sample', 'ТЕСТ · вымышленная реклама — не результат AT', [f'http://127.0.0.1:{port}/demo/sample']),
    ]:
        jobs[key] = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                              (name, profile, json.dumps(urls), '', 8 if key == 'live' else 2, db.now()))
    result = {'profile': profile, 'jobs': jobs, 'port': port}
    db.execute('INSERT INTO settings(key,value) VALUES (?,?)', (MARKER, json.dumps(result)))
    db.log(None, 'info', 'Демопрофиль AT: задания без расписания. Тестовая реклама явно помечена; аккаунт AT не создан.')
    return result


def application(port):
    @asynccontextmanager
    async def lifespan(app):
        async with main.app.router.lifespan_context(main.app):
            state = seed(port)
            # A moved demo can use another free local port; its fixture job must stay local to this server.
            db.execute('UPDATE jobs SET urls=? WHERE id=?', (json.dumps([f'http://127.0.0.1:{port}/demo/sample']), state['jobs']['sample']))
            state['port'] = port
            db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(state), MARKER))
            yield

    async def status(request):
        rows = db.rows('SELECT value FROM settings WHERE key=?', (MARKER,))
        # ASCII escapes also work in Windows PowerShell 5.1, which otherwise misdecodes UTF-8 paths.
        return Response(json.dumps({'purpose': MARKER, 'root': str(ROOT), 'data': str(db.DATA.resolve()),
                                    'version': config.VERSION, 'setup': json.loads(rows[0]['value']) if rows else {}},
                                   ensure_ascii=True), media_type='application/json')

    async def sample(request):
        return HTMLResponse((ROOT / 'tools/demo_at.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-store'})

    return Starlette(routes=[Route('/demo/status', status), Route('/demo/sample', sample), Mount('/', main.app)], lifespan=lifespan)


if __name__ == '__main__':
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8780)
    args = parser.parse_args()
    if db.DATA.resolve() == (ROOT / 'data').resolve():
        raise SystemExit('Specify a dedicated RADAR_DATA_DIR, for example data/at-demo.')
    stop_file = db.DATA / 'stop.request'
    stop_file.unlink(missing_ok=True)
    server = uvicorn.Server(uvicorn.Config(application(args.port), host='127.0.0.1', port=args.port))
    async def serve():
        async def watch():
            while not server.should_exit:
                if stop_file.exists():
                    server.should_exit = True
                    return
                await asyncio.sleep(0.3)
        watcher = asyncio.create_task(watch())
        try:
            await server.serve()
        finally:
            watcher.cancel()
    asyncio.run(serve())
