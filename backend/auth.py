"""Single-administrator login for the server panel.

Password hash:  python -m backend.auth hash   (prints RADAR_ADMIN_PASSWORD_HASH for the .env file)
Sessions are random tokens in an httpOnly cookie; only their SHA-256 is stored.
"""
import base64
import getpass
import hashlib
import hmac
import secrets
import sys
import time
from datetime import timedelta

from . import config, db
from .runs import iso, utcnow

COOKIE = 'radar_session'
ITERATIONS = 390_000
_failures = {}  # client -> [timestamps]


def make_hash(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f'pbkdf2_sha256:{iterations}:{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}'


def verify(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split(':')
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def throttled(client):
    window = time.monotonic() - 900
    attempts = [t for t in _failures.get(client, []) if t > window]
    _failures[client] = attempts
    return len(attempts) >= 10


def note_failure(client):
    _failures.setdefault(client, []).append(time.monotonic())


def create_session():
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.execute('DELETE FROM sessions WHERE expires_at<=?', (iso(now),))
    db.execute('INSERT INTO sessions(token_hash,created_at,expires_at) VALUES (?,?,?)',
               (hashlib.sha256(token.encode()).hexdigest(), iso(now), iso(now + timedelta(hours=config.SESSION_HOURS))))
    return token


def valid(token):
    if not token:
        return False
    return bool(db.rows('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?', (hashlib.sha256(token.encode()).hexdigest(), iso(utcnow()))))


def drop(token):
    if token:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))


if __name__ == '__main__':
    if sys.argv[1:] != ['hash']:
        print('Использование: python -m backend.auth hash')
        sys.exit(2)
    first = getpass.getpass('Пароль администратора (от 12 символов): ')
    if len(first) < 12 or first != getpass.getpass('Повторите пароль: '):
        print('Пароль короче 12 символов или не совпал.')
        sys.exit(1)
    print(make_hash(first))
