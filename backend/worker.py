"""Browser worker: executes queued runs independently of the web panel.

Local mode embeds one worker in the API process. Server mode runs it separately:
    python -m backend.worker
The worker owns heartbeats, schedules, recovery of lost runs and the browser limit.
"""
import asyncio
import json
import os
import signal
import socket
import uuid

from . import config, db, notify, runs
from .collector import collect


class Worker:
    def __init__(self, embedded=False):
        self.id = uuid.uuid4().hex
        self.embedded = embedded
        self.active = {}       # run_id -> asyncio.Task
        self.reasons = {}      # run_id -> 'stop' | 'shutdown'
        self.runs = {}         # run_id -> run row
        self.stop_event = None
        self.alive = 1         # live workers sharing the browser limit

    def register(self):
        now = db.now()
        db.execute('INSERT INTO workers(id,pid,host,mode,version,started_at,heartbeat_at,max_browsers) VALUES (?,?,?,?,?,?,?,?)',
                   (self.id, os.getpid(), socket.gethostname(), 'embedded' if self.embedded else 'process', config.VERSION, now, now, config.MAX_BROWSERS))
        db.log(None, 'info', f'Worker запущен ({"внутри панели" if self.embedded else "отдельный процесс"}), лимит браузеров: {config.MAX_BROWSERS}.')

    async def run(self, stop: asyncio.Event):
        self.stop_event = stop
        db.init()
        self.register()
        loop = asyncio.get_running_loop()
        next_beat = next_schedule = 0.0
        try:
            while not stop.is_set():
                now = loop.time()
                if now >= next_beat:
                    await asyncio.to_thread(runs.heartbeat, self.id, list(self.active))
                    self.alive = await asyncio.to_thread(lambda: max(1, sum(w['alive'] for w in runs.worker_status())))
                    next_beat = now + config.HEARTBEAT_SECONDS
                if now >= next_schedule:
                    await asyncio.to_thread(runs.reconcile)
                    await asyncio.to_thread(runs.schedule_tick)
                    next_schedule = now + config.SCHEDULER_SECONDS
                await self.check_stops()
                # Each live worker takes its share of the browser limit; a single worker uses all of it.
                share = max(1, -(-config.MAX_BROWSERS // self.alive))
                while len(self.active) < share:
                    run = await asyncio.to_thread(runs.claim, self.id, config.MAX_BROWSERS)
                    if run is None:
                        break
                    self.runs[run['id']] = run
                    self.active[run['id']] = asyncio.create_task(self.execute(run))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.shutdown()

    async def check_stops(self):
        for run_id, task in list(self.active.items()):
            if run_id not in self.reasons and await asyncio.to_thread(runs.stop_requested, run_id):
                self.reasons[run_id] = 'stop'
                task.cancel()

    async def shutdown(self):
        for run_id, task in list(self.active.items()):
            self.reasons.setdefault(run_id, 'shutdown')
            task.cancel()
        if self.active:
            await asyncio.gather(*self.active.values(), return_exceptions=True)
        db.execute('UPDATE workers SET stopped_at=? WHERE id=?', (db.now(), self.id))
        db.log(None, 'info', 'Worker остановлен.')

    async def execute(self, run):
        run_id = run['id']
        try:
            job = db.rows('SELECT * FROM jobs WHERE id=?', (run['job_id'],))[0]
            pages = len(json.loads(job['urls']))
            headless = True if config.SERVER else None  # no display on a server
            if config.SERVER and job['headed']:
                db.log(job['id'], 'warning', 'Видимый браузер на сервере недоступен: запуск выполняется в фоновом режиме.', run_id=run_id)
            found = {'n': 0}

            def on_page(done, count, page=None):
                found['n'] = count
                runs.progress(run_id, done, count)
                if page is not None:
                    runs.record_page(run_id, page)

            result = await asyncio.wait_for(
                collect(job, start=run['resume_from'], on_page=on_page, headless=headless, run_id=run_id),
                timeout=100 + max(1, pages - run['resume_from']) * 100)
            if result['status'] != 'done' and self.stop_event is not None:
                # A stop signal can reach the browser before the worker loop: treat that as a shutdown, not a failure.
                await asyncio.sleep(0.5)
                if self.stop_event.is_set():
                    raise asyncio.CancelledError
            if result.get('session'):
                # Stage 02/26: the login marker tells an active session from an expired one.
                db.execute('UPDATE profiles SET session_state=?,session_checked_at=? WHERE id=?', (result['session'], db.now(), job['profile_id']))
                if result['session'] == 'expired':
                    db.log(job['id'], 'warning', 'Сессия профиля истекла: признак входа не найден ни на одной странице. Импортируйте сессию заново.', run_id=run_id,
                           error_code='SESSION_EXPIRED')
            code = result.get('error_code')
            if result['user_action']:
                status, error, code = 'waiting_user', 'Сайт запросил проверку или вход. Откройте профиль вручную, затем запустите снова.', 'USER_ACTION'
                db.execute("UPDATE jobs SET status='waiting_user',last_error=? WHERE id=?", (error, job['id']))
            elif result['status'] == 'failed':
                status, error = 'failed', result['errors'][0]
            else:
                status, error = result['status'], '\n'.join(result['errors']) or None
            retry_id = runs.finish(run, status, error, worker_id=self.id, retry=status == 'failed', error_code=code if status != 'done' else None)
            if retry_id:
                db.execute("UPDATE jobs SET status='queued' WHERE id=?", (job['id'],))
                db.log(job['id'], 'warning', f'Запуск #{run_id} не завершён из-за сбоя. Повтор #{retry_id} продолжит с готового места.', run_id=run_id, error_code=code)
            await asyncio.to_thread(notify.on_run_finished, run_id, bool(retry_id))
        except asyncio.TimeoutError:
            runs.finish(run, 'error', 'Превышено время выполнения.', worker_id=self.id, error_code='RUN_TIMEOUT')
            db.execute("UPDATE jobs SET status='error',last_error='Превышено время выполнения.' WHERE id=?", (run['job_id'],))
            db.log(run['job_id'], 'error', 'Превышено время выполнения.', run_id=run_id, error_code='RUN_TIMEOUT')
            await asyncio.to_thread(notify.on_run_finished, run_id, False)
        except asyncio.CancelledError:
            if self.reasons.get(run_id) == 'shutdown' or (self.stop_event is not None and self.stop_event.is_set() and run_id not in self.reasons):
                retry_id = runs.finish(run, 'interrupted', 'Worker остановлен во время запуска.', worker_id=self.id, retry=True, delay=0, error_code='WORKER_STOPPED')
                db.execute('UPDATE jobs SET status=? WHERE id=?', ('queued' if retry_id else 'interrupted', run['job_id']))
                db.log(run['job_id'], 'warning', f'Worker остановлен во время запуска #{run_id}.' + (f' Повтор #{retry_id} продолжит со следующей незавершённой страницы.' if retry_id else ''),
                       run_id=run_id, error_code='WORKER_STOPPED')
                if not retry_id:
                    await asyncio.to_thread(notify.on_run_finished, run_id, False)
            else:
                runs.finish(run, 'stopped', 'Остановлено пользователем.', worker_id=self.id, error_code='STOPPED')
        except Exception as exc:  # never let one run kill the worker
            retry_id = runs.finish(run, 'failed', f'Сбой выполнения: {type(exc).__name__}.', worker_id=self.id, retry=True, error_code='UNKNOWN')
            db.log(run['job_id'], 'error', f'Сбой выполнения запуска #{run_id}: {type(exc).__name__}. Worker продолжает работу.', run_id=run_id, error_code='UNKNOWN')
            await asyncio.to_thread(notify.on_run_finished, run_id, bool(retry_id))
        finally:
            self.active.pop(run_id, None)
            self.reasons.pop(run_id, None)
            self.runs.pop(run_id, None)


def main():
    stop = asyncio.Event()

    async def runner():
        loop = asyncio.get_running_loop()
        request = lambda *_: loop.call_soon_threadsafe(stop.set)
        for name in ('SIGTERM', 'SIGINT', 'SIGBREAK'):
            if hasattr(signal, name):
                try:
                    loop.add_signal_handler(getattr(signal, name), request)
                except (NotImplementedError, RuntimeError):
                    signal.signal(getattr(signal, name), request)
        await Worker(embedded=False).run(stop)

    print(f'Книжный радар worker {config.VERSION}: режим {config.MODE}, лимит браузеров {config.MAX_BROWSERS}. Ctrl+C — остановка.', flush=True)
    asyncio.run(runner())


if __name__ == '__main__':
    import sys
    if '--health' in sys.argv:  # container healthcheck: a live worker heartbeat exists
        db.init()
        sys.exit(0 if any(w['alive'] for w in runs.worker_status()) else 1)
    main()
