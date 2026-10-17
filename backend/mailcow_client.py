"""Small HTTPS-only client for the official Mailcow API, plus TLS IMAP login.

Contract: mailcow/mailcow-dockerized data/web/api/openapi.yaml (2026-10-17).
Provider responses can contain request logs; never persist or expose their raw body.
"""
import imaplib
import re
import ssl
from urllib.parse import urlsplit, quote

import httpx


class ProviderError(Exception):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def domain_name(value):
    value = value.strip().lower().encode('idna').decode('ascii')
    if len(value) > 253 or '.' not in value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.')):
        raise ValueError('Укажите домен без URL и символа @.')
    return value


def api_origin(value):
    try:
        u = urlsplit(value.strip())
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ValueError()
        host = domain_name(u.hostname)
        port = u.port
        if port == 0:
            raise ValueError()
        return f'https://{host}' + (f':{port}' if port and port != 443 else '')
    except (ValueError, UnicodeError):
        raise ValueError('Нужен HTTPS-адрес почтового сервера без пути, логина и параметров.')


def http_client(origin, key):
    return httpx.AsyncClient(base_url=origin, headers={'X-API-Key': key}, timeout=httpx.Timeout(15, connect=5),
                             follow_redirects=False, trust_env=False)


def active(record):
    return record.get('active') in (True, 1, '1')


def matching(data, field, expected):
    if data in ({}, []):
        return None
    rows = data if isinstance(data, list) else [data]
    if not all(isinstance(r, dict) for r in rows) or any(r.get('type') in ('danger', 'error') for r in rows):
        raise ProviderError('INVALID_RESPONSE')
    matches = [r for r in rows if str(r.get(field, '')).lower() == expected.lower()]
    if len(matches) != 1:
        raise ProviderError('INVALID_RESPONSE')
    return matches[0]


class Mailcow:
    def __init__(self, origin, key):
        self.origin, self.key = api_origin(origin), key

    async def request(self, method, path, body=None):
        modifying = method == 'POST'
        try:
            async with http_client(self.origin, self.key) as c:
                response = await c.request(method, path, json=body)
            if response.status_code != 200:
                raise ProviderError('HTTP_REJECTED', uncertain=modifying and response.status_code not in (400, 401, 403, 404, 405, 413, 422, 429))
            return response.json()
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError):
            raise ProviderError('NO_CONFIRMED_RESPONSE', uncertain=modifying) from None

    async def domain(self, domain):
        return matching(await self.request('GET', '/api/v1/get/domain/' + quote(domain, safe='')), 'domain_name', domain)

    async def mailbox(self, email):
        return matching(await self.request('GET', '/api/v1/get/mailbox/' + quote(email, safe='')), 'username', email)

    async def create(self, email, name, password, quota):
        local, domain = email.split('@')
        data = await self.request('POST', '/api/v1/add/mailbox', {'local_part': local, 'domain': domain,
            'name': name, 'password': password, 'password2': password, 'quota': quota, 'active': True, 'authsource': 'mailcow'})
        rows = data if isinstance(data, list) else [data]
        if not rows or not all(isinstance(r, dict) for r in rows):
            raise ProviderError('INVALID_RESPONSE', uncertain=True)
        if any(r.get('type') in ('danger', 'error') for r in rows):
            raise ProviderError('CREATE_REJECTED')
        if not all(r.get('type') == 'success' and isinstance(r.get('msg'), list) and r['msg'][:2] == ['mailbox_added', email] for r in rows):
            raise ProviderError('INVALID_RESPONSE', uncertain=True)


def imap_login(host, email, password):
    """Authentication only; no messages are fetched, sent or deleted."""
    with imaplib.IMAP4_SSL(domain_name(host), 993, ssl_context=ssl.create_default_context(), timeout=12) as client:
        result, _ = client.login(email, password)
        if result != 'OK':
            raise ValueError('Login not confirmed')
