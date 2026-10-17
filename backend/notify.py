"""Stage 17: Telegram notifications about runs. Off by default.

Settings live in the `settings` table (keys notify.*). The bot token is stored only as a vault/DPAPI blob
(protection.protect) and is never returned by the API, written to the journal or put into an error text.
Events:
- failure   — the run ended with an error and no automatic retry is left;
- user      — the site asks for a human check or sign-in («Нужен пользователь»);
- done      — the run finished (off by default: schedules would make it noisy);
- recovered — the first successful run after a reported problem.
One problem is reported once: a repeat of the same job/event/code is recorded as suppressed until the job
finishes successfully again.
"""
import base64
import json

import httpx

from . import config, db
from .errors import ERROR_CODES

CHANNEL = 'telegram'
EVENTS = {'failure': 'Сбой запуска', 'user': 'Нужен пользователь', 'done': 'Запуск завершён', 'recovered': 'Задание снова работает'}
DEFAULT_EVENTS = ['failure', 'user', 'recovered']
TOKEN_KEY = 'notify.telegram_token'


def _get(key, default=None):
    rows = db.rows('SELECT value FROM settings WHERE key=?', (key,))
    return rows[0]['value'] if rows else default


def _set(key, value):
    if value is None:
        db.execute('DELETE FROM settings WHERE key=?', (key,))
    else:
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)', (key, value))


def settings():
    events = json.loads(_get('notify.events') or json.dumps(DEFAULT_EVENTS))
    return {'channel': CHANNEL, 'enabled': _get('notify.enabled') == '1', 'chat_id': _get('notify.chat_id', ''),
            'events': [e for e in events if e in EVENTS], 'token_set': bool(_get(TOKEN_KEY))}


def save_settings(enabled, chat_id, events, token=None, clear_token=False):
    from .protection import protect
    if token:
        _set(TOKEN_KEY, base64.b64encode(protect(token.encode())).decode())
    elif clear_token:
        _set(TOKEN_KEY, None)
    _set('notify.chat_id', chat_id or '')
    _set('notify.events', json.dumps([e for e in events if e in EVENTS]))
    current = settings()
    _set('notify.enabled', '1' if enabled and current['token_set'] and current['chat_id'] else '0')
    return settings()


def _token():
    from .protection import unprotect
    stored = _get(TOKEN_KEY)
    return unprotect(base64.b64decode(stored)).decode() if stored else None


def _record(event, status, detail='', job_id=None, run_id=None, key=''):
    db.execute('INSERT INTO notifications(at,channel,event,job_id,run_id,dedupe_key,status,detail) VALUES (?,?,?,?,?,?,?,?)',
               (db.now(), CHANNEL, event, job_id, run_id, key, status, detail[:300]))


def send(text):
    """(ok, detail). The token is only inside the request URL; error details never include it."""
    try:
        token = _token()
    except RuntimeError as exc:
        return False, str(exc)[:200]
    chat_id = _get('notify.chat_id', '')
    if not token or not chat_id:
        return False, 'Не заданы токен бота или chat id.'
    try:
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = client.post(f'{config.TELEGRAM_API}/bot{token}/sendMessage',
                                   json={'chat_id': chat_id, 'text': text[:3500], 'disable_web_page_preview': True})
    except httpx.TimeoutException:
        return False, 'Telegram не ответил вовремя.'
    except httpx.HTTPError as exc:
        return False, f'Нет соединения с Telegram ({type(exc).__name__}).'
    if response.status_code == 200:
        return True, 'доставлено'
    try:
        description = str(response.json().get('description', ''))[:150].replace(token, '***')
    except ValueError:
        description = ''
    return False, f'Telegram ответил {response.status_code}' + (f': {description}' if description else '')


def classify(status, retried):
    if status in ('done', 'partial'):
        return 'done'
    if status == 'waiting_user':
        return 'user'
    if status in ('failed', 'error', 'interrupted') and not retried:
        return 'failure'
    return None  # stopped/cancelled by a person, skipped, or a retry is already queued


def _message(event, run, job):
    head = {'failure': 'сбой запуска', 'user': 'нужен пользователь', 'done': 'запуск завершён', 'recovered': 'задание снова работает'}[event]
    lines = [f'Книжный радар: {head}', f'Задание «{job["name"]}», запуск #{run["id"]}']
    if run.get('error_code') and event != 'done':
        lines.append(f'Код: {run["error_code"]} - {ERROR_CODES.get(run["error_code"], "")}'.rstrip(' -'))
    lines.append(f'Страниц {run["pages_done"]} из {run["pages_total"]}, найдено блоков {run["found"]}.')
    if run.get('error') and event in ('failure', 'user'):
        lines.append(str(run['error'])[:300])
    if event == 'user':
        lines.append('Откройте профиль вручную или импортируйте сессию, затем запустите снова.')
    return '\n'.join(lines)


def on_run_finished(run_id, retried=False):
    """Called by the worker after a run is closed. Never raises: a notification must not break collection."""
    try:
        config_now = settings()
        if not config_now['enabled']:
            return None
        rows = db.rows('SELECT runs.*, jobs.name AS job_name FROM runs JOIN jobs ON jobs.id=runs.job_id WHERE runs.id=?', (run_id,))
        if not rows:
            return None
        run = rows[0]
        job = {'id': run['job_id'], 'name': run['job_name']}
        event = classify(run['status'], retried)
        if event is None:
            return None
        open_key = f'notify.open.{job["id"]}'
        sent = []
        if event in ('failure', 'user'):
            key = f'{job["id"]}:{event}:{run.get("error_code") or ""}'
            if _get(open_key) == key:
                _record(event, 'suppressed', 'Та же проблема уже отправлена; повтор до успешного запуска не шлётся.', job['id'], run_id, key)
                return 'suppressed'
            _set(open_key, key)
            if event in config_now['events']:
                sent.append((event, key))
        else:
            if _get(open_key):
                _set(open_key, None)
                if 'recovered' in config_now['events']:
                    sent.append(('recovered', ''))
            if 'done' in config_now['events']:
                sent.append(('done', ''))
        result = None
        for name, key in sent:
            ok, detail = send(_message(name, run, job))
            _record(name, 'sent' if ok else 'failed', detail, job['id'], run_id, key)
            if not ok:
                db.log(job['id'], 'warning', f'Уведомление «{EVENTS[name]}» не доставлено: {detail}', run_id=run_id, error_code='NOTIFY_FAILED')
            result = 'sent' if ok else 'failed'
        return result
    except Exception as exc:  # pragma: no cover - defensive
        try:
            db.log(None, 'warning', f'Уведомление не обработано: {type(exc).__name__}.', error_code='NOTIFY_FAILED')
        except Exception:
            pass
        return 'error'


def test():
    ok, detail = send('Книжный радар: проверка уведомлений. Если вы видите это сообщение, канал настроен.')
    _record('test', 'sent' if ok else 'failed', detail)
    return ok, detail


def recent(limit=20):
    return db.rows('''SELECT notifications.*, jobs.name AS job_name FROM notifications LEFT JOIN jobs ON jobs.id=notifications.job_id
        ORDER BY notifications.id DESC LIMIT ?''', (limit,))
