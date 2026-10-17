"""Start/stop only this checkout's local panel, without a persistent console window."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--port', type=int, default=8794)
    parser.add_argument('--data', type=Path, default=ROOT / 'data/ads-demo')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Invalid port')
    data = args.data.resolve()
    base = f'http://127.0.0.1:{args.port}'

    def info():
        with urlopen(base + '/api/ads-demo/info', timeout=2) as response:
            result = json.load(response)
        if result.get('purpose') != 'ads-live-demo-v1' or result.get('root') != str(ROOT) or result.get('data') != str(data):
            raise RuntimeError('Порт занят другой программой. Ничего не остановлено.')
        return result

    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', args.port))
            occupied = False
        except OSError:
            occupied = True
    if args.stop:
        if not occupied:
            print('Панель уже выключена.'); return
        info()
        os.environ['RADAR_DATA_DIR'] = str(data)
        from tools.local_control import read_token
        request = Request(base + '/api/ads-demo/stop', method='POST', data=b'{}',
                          headers={'X-Radar-Control': read_token(data), 'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            json.load(response)
        for _ in range(80):
            try:
                info()
            except OSError:
                print('Панель штатно завершена. Данные сохранены.'); return
            time.sleep(.25)
        raise RuntimeError('Остановка ещё выполняется. Принудительное завершение отключено.')
    if occupied:
        current = info()
        from backend.config import VERSION
        if current.get('version') != VERSION:
            raise RuntimeError('Работает прежняя версия. Штатно остановите её и запустите Radar.cmd снова.')
    else:
        data.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'RADAR_DESKTOP_BACKGROUND': '1', 'PYTHONUNBUFFERED': '1'}
        with (data / 'panel.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, '-m', 'tools.ads_demo', '--port', str(args.port), '--data', str(data)],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError(f'Панель не запустилась. Подробности: {data / "panel.log"}')
            try:
                info(); break
            except OSError:
                time.sleep(.3)
        else:
            raise RuntimeError(f'Панель ещё не отвечает. Подробности: {data / "panel.log"}')
    with urlopen(base + '/api/state', timeout=3) as response:
        state = json.load(response)['server']
    if state.get('simulation_enabled') or state.get('preview_fixture') or not state.get('worker_enabled'):
        raise RuntimeError('На этом порту другой режим или выключен сборщик. Ничего не остановлено.')
    if not args.no_open:
        webbrowser.open(base)
    print(f'Панель работает в фоне: {base}. Штатная остановка: Radar_Stop.cmd.')


if __name__ == '__main__':
    try:
        main()
    except HTTPError as error:
        try:
            detail = json.load(error).get('detail', str(error))
        except (ValueError, OSError):
            detail = str(error)
        print(detail)
        raise SystemExit(1)
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error))
        raise SystemExit(1)
