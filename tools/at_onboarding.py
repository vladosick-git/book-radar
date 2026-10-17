"""Isolated owner-operated AT pilot, with no collection worker or scheduled actions."""
import argparse
import json
import os
import re
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def application():
    from backend import config, db, main
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def info(request):
        return JSONResponse({'purpose': 'at-owner-onboarding', 'version': config.VERSION,
            'root': str(ROOT), 'data': str(db.DATA.resolve())}, headers={'Cache-Control': 'no-store'})

    # Mounting the frontend happens at import time in main; our info route must precede that catch-all.
    return Starlette(routes=[Route('/api/pilot/info', info), Mount('/', main.app)], lifespan=main.lifespan)


def serve():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8792)
    parser.add_argument('--data', type=Path, default=ROOT / 'data/at-onboarding')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    data = args.data.resolve()
    if data == (ROOT / 'data').resolve() or data == (ROOT / 'data/at-demo').resolve():
        parser.error('Use a separate pilot data directory.')
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535.')
    url = f'http://127.0.0.1:{args.port}'
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError:
            try:
                with urlopen(url + '/api/pilot/info', timeout=3) as response:
                    info = json.load(response)
                matches = info.get('purpose') == 'at-owner-onboarding' and info.get('root') == str(ROOT) and info.get('data') == str(data)
            except Exception:
                matches = False
            if not matches:
                raise SystemExit(f'Port {args.port} is occupied by another process. Nothing was stopped.')
            current = re.search(r"^VERSION = '([^']+)'", (ROOT / 'backend/config.py').read_text(encoding='utf-8'), re.M)
            if current and info.get('version') != current.group(1):
                raise SystemExit(f'Running pilot is version {info.get("version")}, files are {current.group(1)}. '
                                 'Close the pilot window (Ctrl+C), then start it again. Nothing was stopped.')
            print(f'Pilot already running: {url}')
            if args.open:
                webbrowser.open(url)
            return
    os.environ.update(RADAR_DATA_DIR=str(data), RADAR_MODE='local', RADAR_AUTH='0', RADAR_SIMULATION='0', RADAR_VISUAL_PREVIEW='0',
        RADAR_EMBEDDED_WORKER='0', RADAR_ALLOWED_HOSTS='127.0.0.1,localhost', RADAR_COOKIE_SECURE='0')
    sys.path.insert(0, str(ROOT))
    import uvicorn
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f'Owner pilot: {url}\nSeparate data: {data}\nNo collection worker. Stop: Ctrl+C.')
    uvicorn.run(application(), host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    serve()
