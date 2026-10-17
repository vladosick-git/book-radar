"""Run the isolated local registration simulator; never use the owner's AT data."""
import json
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PORT = 8797
DATA = ROOT / 'data/simulation'


def main():
    url = f'http://127.0.0.1:{PORT}'
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', PORT))
        except OSError:
            try:
                with urlopen(url + '/api/simulation/info', timeout=3) as r:
                    info = json.load(r)
                from backend.config import VERSION
                assert info == {'purpose': 'book-radar-local-simulation', 'version': VERSION, 'root': str(ROOT), 'data': str(DATA)}
            except Exception:
                raise SystemExit('Port 8797 is occupied or the running version differs. Nothing was stopped.')
            if '--open' in sys.argv:
                webbrowser.open(url)
            return
    os.environ.update(RADAR_DATA_DIR=str(DATA), RADAR_MODE='local', RADAR_AUTH='0', RADAR_SIMULATION='1', RADAR_VISUAL_PREVIEW='0',
                      RADAR_EMBEDDED_WORKER='0', RADAR_ALLOWED_HOSTS='127.0.0.1,localhost', RADAR_COOKIE_SECURE='0')
    sys.path.insert(0, str(ROOT))
    from backend import config, main as panel
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    async def info(request):
        return JSONResponse({'purpose': 'book-radar-local-simulation', 'version': config.VERSION, 'root': str(ROOT), 'data': str(DATA)})
    app = Starlette(routes=[Route('/api/simulation/info', info), Mount('/', panel.app)], lifespan=panel.lifespan)
    if '--open' in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f'Local simulation: {url}\nData: {DATA}\nNo production registration or collection worker.')
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='warning')


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
