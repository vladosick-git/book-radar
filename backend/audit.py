"""Action journal: who started, stopped or changed what (stages 09/17), and server-side journal search (stage 17).

Details must never contain secrets: callers pass field names and safe values only.
"""
import hashlib
import json

from . import auth, config, db

SECRET_KEYS = ('password', 'passphrase', 'token', 'cookie', 'secret', 'key')
# Russian names of actions, so that a search typed in the panel language finds them (the panel shows the same names).
ACTION_LABELS = {
    'collection_setup': 'Сбор настроен выбранным ботам', 'at_remote_control': 'Ручное действие в браузере AT',
    'permanent_mail_link': 'Постоянная почта связана с ботом', 'permanent_mail_attach': 'Готовая постоянная почта добавлена',
    'mail_credentials_viewed': 'Просмотр данных входа в почту', 'at_form_submit': 'Форма AT отправлена',
    'at_form_fill': 'Форма AT заполнена', 'at_session_check': 'Проверен вход AT',
    'profile_batch_create': 'Создана серия профилей', 'export_csv': 'Экспорт находок в CSV',
    'purchase_import': 'Импорт ручных покупок или отчёта', 'purchase_export': 'Экспорт сверки покупок',
    'login': 'Вход в панель', 'login_failed': 'Неудачный вход', 'profile_create': 'Создан профиль', 'profile_window_open': 'Открыто окно профиля',
    'job_create': 'Создано задание', 'job_edit': 'Изменено задание', 'job_duplicate': 'Копия задания', 'schedule_change': 'Изменено расписание',
    'run_queue': 'Запуск поставлен в очередь', 'run_stop': 'Остановка запуска', 'session_export': 'Экспорт сессии', 'session_import': 'Импорт сессии',
    'export_json': 'Экспорт находок в JSON', 'notify_settings': 'Настройки уведомлений', 'notify_test': 'Проверка уведомлений',
}


def actor(request=None):
    """Single administrator on a server; the session hash prefix distinguishes browser sessions."""
    if not config.AUTH:
        return 'локальный пользователь'
    token = request.cookies.get(auth.COOKIE) if request is not None else None
    return f'администратор · сессия {hashlib.sha256(token.encode()).hexdigest()[:6]}' if token else 'администратор'


def record(action, target_type=None, target_id=None, request=None, who=None, **details):
    clean = {key: value for key, value in details.items() if not any(word in key.lower() for word in SECRET_KEYS)}
    db.execute('INSERT INTO audit(at,actor,action,target_type,target_id,details) VALUES (?,?,?,?,?,?)',
               (db.now(), who or actor(request), action, target_type, target_id, json.dumps(clean, ensure_ascii=False)))


def like(value):
    """Case-insensitive (Cyrillic too, via the casefold() SQL function) substring pattern; '!' escapes % and _."""
    escaped = value.casefold().replace('!', '!!').replace('%', '!%').replace('_', '!_')
    return f'%{escaped}%'


def search(query='', action='', target_type='', since='', until='', limit=100, offset=0):
    clauses, params = [], []
    if query:
        named = [action for action, label in ACTION_LABELS.items() if query.casefold() in label.casefold()]
        by_name = f' OR action IN ({",".join("?" * len(named))})' if named else ''
        clauses.append(f"(casefold(details) LIKE ? ESCAPE '!' OR casefold(action) LIKE ? ESCAPE '!' OR casefold(actor) LIKE ? ESCAPE '!'{by_name})")
        params += [like(query)] * 3 + named
    if action:
        clauses.append('action=?'); params.append(action)
    if target_type:
        clauses.append('target_type=?'); params.append(target_type)
    if since:
        clauses.append('at>=?'); params.append(since)
    if until:
        clauses.append('at<?'); params.append(until)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = db.rows(f'SELECT COUNT(*) AS n FROM audit{where}', params)[0]['n']
    rows = db.rows(f'SELECT * FROM audit{where} ORDER BY id DESC LIMIT ? OFFSET ?', params + [limit, offset])
    for row in rows:
        row['details'] = json.loads(row['details'] or '{}')
    actions = [row['action'] for row in db.rows('SELECT DISTINCT action FROM audit ORDER BY action')]
    return {'total': total, 'items': rows, 'actions': actions, 'labels': ACTION_LABELS}


def search_logs(query='', level='', source='', profile_id=None, job_id=None, run_id=None, error_code='', since=None, until=None, limit=100, offset=0):
    """Stage 17: the event journal for any period. A profile matches its own events and those of its jobs.
    error_code='*' means «any code». `levels` counts all levels under the other filters (for the level switch)."""
    clauses, params = [], []
    if query:
        clauses.append("(casefold(logs.message) LIKE ? ESCAPE '!' OR casefold(jobs.name) LIKE ? ESCAPE '!')")
        params += [like(query)] * 2
    for column, value in (('logs.source', source), ('logs.job_id', job_id), ('logs.run_id', run_id)):
        if value:
            clauses.append(f'{column}=?'); params.append(value)
    if error_code == '*':
        clauses.append('logs.error_code IS NOT NULL')
    elif error_code:
        clauses.append('logs.error_code=?'); params.append(error_code)
    if profile_id:
        clauses.append('(logs.profile_id=? OR jobs.profile_id=?)'); params += [profile_id, profile_id]
    if since:
        clauses.append('logs.created_at>=?'); params.append(since)
    if until:
        clauses.append('logs.created_at<?'); params.append(until)
    base = 'FROM logs LEFT JOIN jobs ON jobs.id=logs.job_id'
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    levels = {row['level']: row['n'] for row in db.rows(f'SELECT logs.level, COUNT(*) AS n {base}{where} GROUP BY logs.level', params)}
    if level:
        clauses.append('logs.level=?'); params.append(level)
        where = ' WHERE ' + ' AND '.join(clauses)
    total = db.rows(f'SELECT COUNT(*) AS n {base}{where}', params)[0]['n']
    items = db.rows(f'''SELECT logs.*, jobs.name AS job_name, COALESCE(logs.profile_id, jobs.profile_id) AS profile_ref {base}{where}
        ORDER BY logs.id DESC LIMIT ? OFFSET ?''', params + [limit, offset])
    return {'total': total, 'levels': levels, 'items': items}


def problems(profile_id, limit=4):
    """Latest warnings and errors of a profile and its jobs (profile card)."""
    return db.rows('''SELECT logs.* FROM logs LEFT JOIN jobs ON jobs.id=logs.job_id WHERE logs.level!='info'
        AND (logs.profile_id=? OR jobs.profile_id=?) ORDER BY logs.id DESC LIMIT ?''', (profile_id, profile_id, limit))
