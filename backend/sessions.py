"""Stage 26: move the owner's signed-in profile session to the server.

The owner signs in manually in the local version (profile window), exports the session into a file
encrypted with a passphrase, and imports it on the server into a profile. The passphrase is never
stored; the file contains cookies and must be treated as a secret. The server writes the session
through the vault (RADAR_VAULT_KEY_FILE) like any other profile snapshot.
"""
import base64
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from . import db, runs
from .browser import read_state, write_state

FORMAT = 'radar-session-export-1'
SCRYPT = {'n': 2 ** 15, 'r': 8, 'p': 1}
MAX_FILE = 5 * 1024 * 1024
MAX_COOKIES = 5000
MIN_PASSPHRASE = 12


def _key(passphrase, salt, params=SCRYPT):
    import hashlib
    raw = hashlib.scrypt(passphrase.encode('utf-8'), salt=salt, n=params['n'], r=params['r'], p=params['p'], dklen=32, maxmem=128 * 1024 * 1024)
    return base64.urlsafe_b64encode(raw)


def check_passphrase(passphrase):
    if len(passphrase or '') < MIN_PASSPHRASE:
        raise HTTPException(400, f'Фраза для файла сессии — не короче {MIN_PASSPHRASE} символов.')


def _profile(profile_id):
    rows = db.rows('SELECT * FROM profiles WHERE id=?', (profile_id,))
    if not rows:
        raise HTTPException(404, 'Профиль не найден.')
    return rows[0]


def validate_state(state):
    if not isinstance(state, dict) or not isinstance(state.get('cookies', []), list) or not isinstance(state.get('origins', []), list):
        raise HTTPException(400, 'Файл не содержит снимок сессии браузера.')
    cookies = state.get('cookies', [])
    if len(cookies) > MAX_COOKIES:
        raise HTTPException(400, 'Слишком много cookies в файле сессии.')
    for cookie in cookies:
        if not isinstance(cookie, dict) or not {'name', 'value'} <= cookie.keys() or not (cookie.get('domain') or cookie.get('url')):
            raise HTTPException(400, 'Неверный формат cookie в файле сессии.')
    return {'cookies': cookies, 'origins': state.get('origins', [])}


def summary(state):
    domains = sorted({(c.get('domain') or '').lstrip('.') for c in state.get('cookies', []) if c.get('domain')})
    return {'cookies': len(state.get('cookies', [])), 'domains': domains[:50]}


def export_session(profile_id, passphrase):
    check_passphrase(passphrase)
    profile = _profile(profile_id)
    state = read_state(profile_id)
    if not state or not state.get('cookies'):
        raise HTTPException(409, 'В профиле нет сохранённой сессии. Откройте браузер профиля, войдите на сайт и закройте окно.')
    salt = os.urandom(16)
    payload = json.dumps({'profile_name': profile['name'], 'state': validate_state(state)}, ensure_ascii=False).encode('utf-8')
    token = Fernet(_key(passphrase, salt)).encrypt(payload).decode('ascii')
    exported_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    document = {'format': FORMAT, 'kdf': 'scrypt', **SCRYPT, 'salt': base64.b64encode(salt).decode('ascii'),
                'profile_name': profile['name'], 'exported_at': exported_at, **summary(state), 'token': token}
    return json.dumps(document, ensure_ascii=False, indent=1).encode('utf-8'), profile['name']


def import_session(profile_id, file_text, passphrase):
    check_passphrase(passphrase)
    _profile(profile_id)
    if not file_text or len(file_text.encode('utf-8')) > MAX_FILE:
        raise HTTPException(400, 'Файл сессии пуст или больше 5 МБ.')
    try:
        document = json.loads(file_text)
        if document.get('format') != FORMAT or document.get('kdf') != 'scrypt':
            raise ValueError
        params = {name: int(document[name]) for name in ('n', 'r', 'p')}
        if params['n'] > 2 ** 20 or params['r'] > 16 or params['p'] > 4:
            raise ValueError
        salt = base64.b64decode(document['salt'])
        payload = Fernet(_key(passphrase, salt, params)).decrypt(document['token'].encode('ascii'))
    except InvalidToken:
        raise HTTPException(400, 'Фраза не подходит к файлу или файл повреждён. Данные профиля не изменены.')
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(400, 'Это не файл сессии «Книжного радара» или он повреждён.')
    state = validate_state(json.loads(payload).get('state'))
    owner = f'import:{os.getpid()}'
    # One lease per profile: an import never races a running collection or an open window.
    if not runs.acquire_lease(profile_id, owner, 'import', 60):
        raise HTTPException(409, 'Профиль сейчас занят сбором или окном браузера. Дождитесь завершения и повторите импорт.')
    try:
        write_state(profile_id, state)
        db.execute("UPDATE profiles SET session_state='imported',session_imported_at=?,session_checked_at=NULL WHERE id=?", (db.now(), profile_id))
        # A new snapshot invalidates an earlier site's verification without erasing its history.
        db.execute("UPDATE onboarding_at SET status='needs_owner',message='Сессия заменена импортом. Повторите проверку AT.',checked_at=NULL,updated_at=? WHERE account_id IN (SELECT id FROM accounts WHERE profile_id=?)", (db.now(), profile_id))
        db.execute("UPDATE accounts SET status='in_progress',updated_at=? WHERE profile_id=? AND id IN (SELECT account_id FROM onboarding_at)", (db.now(), profile_id))
    finally:
        runs.release_lease(profile_id, owner)
    db.log(None, 'info', f'Сессия профиля импортирована: cookies {len(state["cookies"])}. Вход подтвердится при сборе с признаком входа.', source='profile', profile_id=profile_id)
    return summary(state)


def state_of(profile):
    """Public session state; content of the snapshot is never returned."""
    from .browser import state_path
    path = state_path(profile['id'])
    stored = path.exists() or any(path.with_name(name).exists() for name in ('radar-state.json', 'radar-state.dpapi', 'radar-state.vault'))
    value = profile.get('session_state') or 'unknown'
    if not stored:
        return 'empty'
    return 'stored' if value in ('unknown', 'empty') else value
