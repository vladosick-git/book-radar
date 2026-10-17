"""Non-destructive captions, fixture provenance and exact duplicate markers."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from .identity import stable_url

DOMAIN = re.compile(r'^(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+/?$', re.I)
SERVICE = re.compile(r'^(?:реклама|advertisement|sponsored|ad|[•·|—–-]+|\d{1,2}\+|подробнее(?: на сайте.*)?|купить|читать|перейти(?: на сайт)?|узнать больше)$', re.I)
WARNING = re.compile(r'^(?:изучите все условия|соответствующем разделе|оценивайте свои финансовые|есть противопоказания|не является лекарств|проконсультируйтесь|необходима консультация|ознакомьтесь с условиями)', re.I)


def clean(value):
    return ' '.join((value or '').replace('\xad', '').split())


def caption(item):
    title = clean(item.get('title'))
    if title and not (DOMAIN.fullmatch(title) or SERVICE.fullmatch(title) or WARNING.match(title)):
        return title[:300]
    lines = [clean(line) for line in (item.get('text') or item.get('clean_text') or '').splitlines()]
    for line in lines:
        if line and not (DOMAIN.fullmatch(line) or SERVICE.fullmatch(line) or WARNING.match(line)):
            return line[:300]
    domain = next((line for line in lines if DOMAIN.fullmatch(line)), '')
    return f'Реклама · {domain} (заголовок не распознан)' if domain else title or 'Заголовок не распознан'


def is_test_url(value):
    try:
        parsed = urlsplit(value or '')
        return parsed.hostname in ('127.0.0.1', 'localhost', '::1', 'testserver') and (parsed.path.rstrip('/') == '/sample' or parsed.path.startswith('/api/simulation/site/'))
    except ValueError:
        return False


def exact_key(row):
    # The original payload and IDs remain intact. Only exact same-page creatives are marked.
    signature = [row['profile_id'], stable_url(row['source_url']), stable_url(row['frame_url']),
                 caption(row).casefold(), clean(row['text']).casefold(), stable_url(row['destination']), stable_url(row['image_url'])]
    return hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()


def refresh(connection):
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(findings)')}
    for name, ddl in [('display_title', "TEXT NOT NULL DEFAULT ''"), ('is_test', 'INTEGER NOT NULL DEFAULT 0'), ('duplicate_of', 'INTEGER')]:
        if name not in columns:
            connection.execute(f'ALTER TABLE findings ADD COLUMN {name} {ddl}')
    seen = {}
    for record in connection.execute('SELECT * FROM findings ORDER BY id').fetchall():
        row = dict(record)
        key = exact_key(row)
        original = seen.setdefault(key, row['id'])
        connection.execute('UPDATE findings SET display_title=?,is_test=?,duplicate_of=? WHERE id=?',
            (caption(row), int(is_test_url(row['source_url'])), original if original != row['id'] else None, row['id']))


def present(row):
    row['original_title'] = row.get('title') or ''
    row['title'] = row.get('display_title') or caption(row)
    row['is_test'] = bool(row.get('is_test') or is_test_url(row.get('source_url')))
    row['destination_status'] = 'tracking_link' if '/an/count/' in (row.get('destination') or '') else 'available' if row.get('destination') else 'not_detected'
    return row
