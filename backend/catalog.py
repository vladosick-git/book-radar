"""Stage 14: catalog of advertisements.

Three levels, all in SQLite:
- ads           — a creative («объявление»): normalised title + image + destination. A family groups the
                  creatives of one destination; a changed title or image inside a family is a new variant.
- findings      — a placement: one exact block on one page for one profile (the 0.6 table, IDs kept).
- observations  — one sighting: when, by which run, with which page screenshot.

Variant rule (tests hold the counterexamples):
- new variant: the title or the image of a creative with the same destination changed;
- the same creative: whitespace or letter case of the title, tracking/cache parameters of the URLs,
  http/https, a changed body text (counters, dates) — these never create a new ad.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import db, finding_quality
from .identity import click_counter, fingerprint, stable_url

KINDS = {'banner': 'Баннер без текста', 'text': 'Текстовый блок', 'mixed': 'Изображение и текст', 'link': 'Только ссылка',
         'iframe': 'Закрытый рекламный фрейм'}
SORTS = {'last': 'f.last_seen DESC, f.id DESC', 'first': 'f.first_seen ASC, f.id ASC', 'seen': 'f.sightings DESC, f.last_seen DESC, f.id DESC'}
AD_SORTS = {'last': 'last_seen DESC, ads.id DESC', 'first': 'first_seen ASC, ads.id ASC', 'seen': 'sightings DESC, last_seen DESC, ads.id DESC'}
MAX_PAGE = 200


# ---------- normalisation ----------

def norm_title(value):
    return ' '.join((value or '').split()).casefold()


def norm_url(value):
    if not value:
        return ''
    stable = stable_url(value.strip())
    parts = urlsplit(stable)
    if parts.scheme in ('http', 'https'):
        # http/https and a trailing slash do not make a different creative.
        return f'{parts.netloc}{parts.path.rstrip("/") or "/"}' + (f'?{parts.query}' if parts.query else '')
    return stable


def ad_keys(item):
    title, image = norm_title(finding_quality.caption(item)), norm_url(item.get('image_url'))
    # A click counter says nothing about where the ad leads: one shared counter address must not
    # turn every creative of the network into «variants» of one family.
    destination = '' if click_counter(item.get('destination')) else norm_url(item.get('destination'))
    key = hashlib.sha256(json.dumps([title, image, destination], ensure_ascii=False).encode()).hexdigest()
    family = f'dest:{destination}' if destination else f'ad:{key}'
    return key, family, title, image


def kind_of(item):
    if item.get('image_source') == 'iframe' and not (item.get('text') or '').strip():
        return 'iframe'
    image, text = bool(item.get('image_url')) or item.get('image_source') in ('canvas',), bool((item.get('text') or '').strip())
    return 'mixed' if image and text else 'banner' if image else 'text' if text else 'link'


def host_of(url):
    try:
        return (urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def search_text(item, source=''):
    parts = [item.get('title'), item.get('text'), item.get('clean_text'), item.get('ocr_text'), source, item.get('destination')]
    return '\n'.join(p for p in parts if p).casefold()


# ---------- writing ----------

def _ad_for(connection, item, timestamp):
    """ID of the creative for `item`; creates it (with zero counters) and decides its variant number."""
    key, family, title, image = ad_keys(item)
    row = connection.execute('SELECT id FROM ads WHERE ad_key=?', (key,)).fetchone()
    if row:
        return row['id']
    first = connection.execute('SELECT * FROM ads WHERE family_key=? ORDER BY variant_no, id LIMIT 1', (family,)).fetchone()
    variant_no, variant_of, reason = 1, None, ''
    if first is not None:
        # variant_of points to the root of the family; the reason says what differs from the previous variant.
        previous = connection.execute('SELECT * FROM ads WHERE family_key=? ORDER BY variant_no DESC, id DESC LIMIT 1', (family,)).fetchone()
        variant_no, variant_of = previous['variant_no'] + 1, first['id']
        changed = [name for name, old, new in (('title', norm_title(previous['title']), title), ('image', norm_url(previous['image_url']), image)) if old != new]
        reason = ','.join(changed)
    cursor = connection.execute('''INSERT OR IGNORE INTO ads(ad_key,family_key,variant_of,variant_no,variant_reason,title,text,destination,image_url,kind,
        first_seen,last_seen,sightings,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)''',
        (key, family, variant_of, variant_no, reason, item.get('title') or '', item.get('clean_text') or item.get('text') or '',
         item.get('destination') or '', item.get('image_url') or '', kind_of(item), timestamp, timestamp, db.now()))
    if cursor.rowcount:
        return cursor.lastrowid
    return connection.execute('SELECT id FROM ads WHERE ad_key=?', (key,)).fetchone()['id']


def save(job, item, source, frame, screenshot, run_id=None, extra=None):
    """Record one sighting. Returns {'fingerprint','finding_id','ad_id','new'}.

    The placement keeps the 0.6 behaviour (same fingerprint, counters, first/last screenshot); the sighting
    is added to `observations` and to the creative's counters in the same transaction.
    """
    from .runs import tx
    item = {**item, **(extra or {})}
    if not item.get('block_type'):
        # Saved without a site adapter (API, tests, imports): the adapter of the page still decides the type.
        from .adapters import for_url
        adapter = for_url(source)
        item['block_type'], item['block_reason'] = adapter.classify({**item, 'frame_url': frame}, {})
        item.setdefault('adapter', f'{adapter.id}@{adapter.version}')
    digest = fingerprint(job['profile_id'], source, frame, item)
    timestamp = db.now()
    kind = kind_of(item)
    sources = json.dumps(item.get('field_sources') or {}, ensure_ascii=False)
    evidence = json.dumps(item.get('block_evidence') or {}, ensure_ascii=False)
    with tx() as connection:
        ad_id = _ad_for(connection, item, timestamp)
        found = connection.execute('SELECT id,fingerprint FROM findings WHERE fingerprint=?', (digest,)).fetchone()
        if found is None:
            target = {**item, 'profile_id': job['profile_id'], 'source_url': source, 'frame_url': frame}
            key = finding_quality.exact_key(target)
            found = next((row for row in connection.execute('SELECT * FROM findings WHERE profile_id=? AND source_url=? ORDER BY id', (job['profile_id'], source)).fetchall() if finding_quality.exact_key(dict(row)) == key), None)
            if found:
                digest = found['fingerprint']
        if found:
            finding_id = found['id']
            connection.execute('''UPDATE findings SET last_seen=?,sightings=sightings+1,screenshot=?,job_id=?,image_url=?,destination=?,
                frame_url=?,ad_id=?,kind=?,clean_text=?,field_sources=?,image_source=?,search_text=?,
                block_type=COALESCE(NULLIF(?,''),block_type),block_reason=COALESCE(NULLIF(?,''),block_reason),adapter=COALESCE(NULLIF(?,''),adapter),
                block_evidence=COALESCE(NULLIF(?,'{}'),block_evidence) WHERE id=?''',
                (timestamp, screenshot, job['id'], item.get('image_url') or '', item.get('destination') or '', frame, ad_id, kind,
                 item.get('clean_text') or '', sources, item.get('image_source') or '', search_text(item, source),
                 item.get('block_type') or '', item.get('block_reason') or '', item.get('adapter') or '', evidence, finding_id))
            if item.get('crop'):
                connection.execute("UPDATE findings SET crop=? WHERE id=? AND crop=''", (item['crop'], finding_id))
        else:
            finding_id = connection.execute('''INSERT INTO findings(fingerprint,profile_id,job_id,title,text,destination,image_url,source_url,
                frame_url,screenshot,first_screenshot,first_seen,last_seen,ad_id,kind,source_host,clean_text,field_sources,image_source,crop,
                ocr_text,ocr_status,search_text,block_type,block_reason,adapter,block_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (digest, job['profile_id'], job['id'], item.get('title') or '', item.get('text') or '', item.get('destination') or '',
                 item.get('image_url') or '', source, frame, screenshot, screenshot, timestamp, timestamp, ad_id, kind, host_of(source),
                 item.get('clean_text') or '', sources, item.get('image_source') or '', item.get('crop') or '',
                 item.get('ocr_text') or '', item.get('ocr_status') or '', search_text(item, source),
                 item.get('block_type') or 'unknown', item.get('block_reason') or '', item.get('adapter') or '', evidence)).lastrowid
        connection.execute('''INSERT INTO observations(finding_id,ad_id,profile_id,job_id,run_id,source_url,seen_at,screenshot,count)
            VALUES (?,?,?,?,?,?,?,?,1)''', (finding_id, ad_id, job['profile_id'], job['id'], run_id, source, timestamp, screenshot))
        # Additive metadata: never rewrite raw titles, delete placements or merge their history.
        row = dict(connection.execute('SELECT * FROM findings WHERE id=?', (finding_id,)).fetchone())
        key = finding_quality.exact_key(row)
        duplicate = next((r['id'] for r in connection.execute('SELECT * FROM findings WHERE profile_id=? AND source_url=? AND id<? ORDER BY id', (row['profile_id'], source, finding_id)).fetchall() if finding_quality.exact_key(dict(r)) == key), None)
        connection.execute('UPDATE findings SET display_title=?,is_test=?,duplicate_of=? WHERE id=?', (finding_quality.caption(row), int(finding_quality.is_test_url(source)), duplicate, finding_id))
        connection.execute('UPDATE ads SET sightings=sightings+1,last_seen=MAX(last_seen,?),first_seen=MIN(first_seen,?) WHERE id=?',
                           (timestamp, timestamp, ad_id))
    return {'fingerprint': digest, 'finding_id': finding_id, 'ad_id': ad_id, 'new': not found}


def set_ocr(finding_id, text, status):
    from .runs import tx
    with tx() as connection:
        saved = connection.execute('SELECT * FROM findings WHERE id=?', (finding_id,)).fetchone()
        if saved is None:
            return
        row = {**dict(saved), 'ocr_text': text}
        connection.execute('UPDATE findings SET ocr_text=?,ocr_status=?,search_text=? WHERE id=?', (text, status, search_text(row, row['source_url']), finding_id))
        try:
            sources = json.loads(row['field_sources'])
        except (ValueError, TypeError):
            sources = {}
        # The creative stores the display text too (JSON export / advertisement view).
        # Refresh only an image-only caption explicitly derived from OCR, leaving DOM text intact.
        if status == 'done' and not row['text'] and isinstance(sources, dict) and sources.get('clean_text') == 'ocr' and row['clean_text'] == text:
            connection.execute('UPDATE ads SET text=? WHERE id=?', (text, row['ad_id']))


# ---------- repeatable migration from 0.6 findings ----------

def migrate(connection):
    """Link 0.6 findings to creatives and seed observations. Safe to run any number of times:
    only findings without ad_id are processed, legacy observations are unique per finding, and the
    creative counters are recomputed from the placements (sum of observation counts == sightings)."""
    todo = connection.execute('SELECT * FROM findings WHERE ad_id IS NULL ORDER BY first_seen, id').fetchall()
    touched = set()
    for row in todo:
        row = dict(row)
        ad_id = _ad_for(connection, row, row['first_seen'])
        touched.add(ad_id)
        connection.execute('UPDATE findings SET ad_id=?,kind=?,source_host=?,search_text=? WHERE id=?',
                           (ad_id, kind_of(row), host_of(row['source_url']), search_text(row, row['source_url']), row['id']))
        connection.execute('''INSERT OR IGNORE INTO observations(finding_id,ad_id,profile_id,job_id,run_id,source_url,seen_at,screenshot,count,legacy)
            VALUES (?,?,?,?,NULL,?,?,?,1,'first')''', (row['id'], ad_id, row['profile_id'], row['job_id'], row['source_url'], row['first_seen'],
                                                      row['first_screenshot'] or row['screenshot']))
        if row['sightings'] > 1:
            connection.execute('''INSERT OR IGNORE INTO observations(finding_id,ad_id,profile_id,job_id,run_id,source_url,seen_at,screenshot,count,legacy)
                VALUES (?,?,?,?,NULL,?,?,?,?,'last')''', (row['id'], ad_id, row['profile_id'], row['job_id'], row['source_url'], row['last_seen'],
                                                         row['screenshot'], row['sightings'] - 1))
    for ad_id in touched:
        connection.execute('''UPDATE ads SET sightings=(SELECT COALESCE(SUM(sightings),0) FROM findings WHERE ad_id=ads.id),
            first_seen=(SELECT MIN(first_seen) FROM findings WHERE ad_id=ads.id), last_seen=(SELECT MAX(last_seen) FROM findings WHERE ad_id=ads.id)
            WHERE id=?''', (ad_id,))
    return len(todo)


# ---------- search ----------

def moment(value, end=False):
    """'YYYY-MM-DD' or ISO with offset → UTC ISO used in the database. A bare date as `end` means the whole day."""
    value = (value or '').strip()
    if not value:
        return None
    try:
        if len(value) == 10:
            day = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            return (day + timedelta(days=1) if end else day).isoformat(timespec='seconds')
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')
    except ValueError:
        raise ValueError('Дата — в формате ГГГГ-ММ-ДД или ISO 8601.')


def like(value):
    escaped = value.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


def where(filters):
    clauses, params = [], []
    scope = filters.get('data_scope', 'all')
    if scope in ('real', 'test'):
        clauses.append('f.is_test=?'); params.append(int(scope == 'test'))
    if filters.get('include_duplicates') is False:
        clauses.append('f.duplicate_of IS NULL')
    q = (filters.get('q') or '').strip()
    if q:
        clauses.append("f.search_text LIKE ? ESCAPE '\\'"); params.append(like(q))
    for key, column in (('profile_id', 'f.profile_id'), ('job_id', 'f.job_id'), ('ad_id', 'f.ad_id')):
        if filters.get(key):
            clauses.append(f'{column}=?'); params.append(int(filters[key]))
    if filters.get('source'):
        clauses.append('f.source_host=?'); params.append(filters['source'].strip().lower())
    if filters.get('kind'):
        clauses.append('f.kind=?'); params.append(filters['kind'])
    if filters.get('block_type'):
        clauses.append('f.block_type=?'); params.append(filters['block_type'])
    if filters.get('advertiser'):
        # Advertiser evidence only. A destination/source containing the domain is not proof of the advertiser.
        clauses.append(f"EXISTS (SELECT 1 FROM {ADVERTISER_VALUES} d WHERE json_type({EVIDENCE_JSON}, '$.target_domains')='array' AND d.type='text' AND lower(trim(d.value))=?)")
        params.append(filters['advertiser'].strip().lower())
    since, until = moment(filters.get('since')), moment(filters.get('until'), end=True)
    if since or until:
        # A placement is in the period when it was seen in it, not when it was created.
        sub, sub_params = ['o.finding_id=f.id'], []
        if since:
            sub.append('o.seen_at>=?'); sub_params.append(since)
        if until:
            sub.append('o.seen_at<?'); sub_params.append(until)
        clauses.append(f'EXISTS (SELECT 1 FROM observations o WHERE {" AND ".join(sub)})'); params += sub_params
    return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params


FINDING_COLUMNS = '''f.id,f.profile_id,f.job_id,f.title,f.display_title,f.is_test,f.duplicate_of,f.text,f.clean_text,f.destination,f.image_url,f.source_url,f.source_host,f.frame_url,
    f.screenshot,f.first_screenshot,f.crop,f.first_seen,f.last_seen,f.sightings,f.ad_id,f.kind,f.image_source,f.field_sources,f.ocr_text,f.ocr_status,
    f.block_type,f.block_reason,f.adapter,f.block_evidence,
    profiles.name AS profile_name,ads.variant_no,ads.variant_reason,ads.variant_of,ads.sightings AS ad_sightings'''

EVIDENCE_JSON = "CASE WHEN json_valid(f.block_evidence) THEN f.block_evidence ELSE '{}' END"
ADVERTISER_VALUES = f"json_each({EVIDENCE_JSON}, '$.target_domains')"


def _object(value):
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or '{}')
        return decoded if isinstance(decoded, dict) else {}
    except (ValueError, TypeError):
        return {}


def _decode(row):
    row = finding_quality.present(row)
    row['field_sources'] = _object(row.get('field_sources'))
    if 'block_evidence' in row:
        row['block_evidence'] = _object(row.get('block_evidence'))
    domains = row.get('block_evidence', {}).get('target_domains', [])
    row['advertisers'] = sorted({d.strip().lower() for d in domains if isinstance(d, str) and d.strip()}) if isinstance(domains, list) else []
    return row


def search(filters, limit=60, offset=0, sort='last'):
    clause, params = where(filters)
    order = SORTS.get(sort, SORTS['last'])
    base = f'FROM findings f JOIN profiles ON profiles.id=f.profile_id LEFT JOIN ads ON ads.id=f.ad_id{clause}'
    total = db.rows(f'SELECT COUNT(*) AS n {base}', params)[0]['n']
    items = [_decode(row) for row in db.rows(f'SELECT {FINDING_COLUMNS} {base} ORDER BY {order} LIMIT ? OFFSET ?',
                                              params + [max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))])]
    return {'total': total, 'items': items, 'limit': limit, 'offset': offset}


def search_ads(filters, limit=60, offset=0, sort='last'):
    clause, params = where(filters)
    order = AD_SORTS.get(sort, AD_SORTS['last'])
    matched = f'SELECT f.* FROM findings f{clause}'
    total = db.rows(f'SELECT COUNT(DISTINCT ad_id) AS n FROM ({matched})', params)[0]['n']
    items = db.rows(f'''WITH matched AS ({matched}),
            ranked AS (SELECT matched.*, ROW_NUMBER() OVER (PARTITION BY ad_id ORDER BY last_seen DESC, id DESC) AS rn FROM matched)
        SELECT ads.*, m.placements, m.profiles, m.matched_sightings,
            r.id AS finding_id, r.screenshot, r.crop, r.source_url, r.source_host, r.profile_id,
            r.last_seen AS matched_last_seen, r.block_type, r.block_reason, r.block_evidence,
            r.ocr_text, r.ocr_status, r.field_sources, r.clean_text,r.display_title,r.is_test,r.duplicate_of,
            (SELECT name FROM profiles WHERE profiles.id=r.profile_id) AS profile_name,
            (SELECT COUNT(*) FROM ads a2 WHERE a2.family_key=ads.family_key) AS variants
        FROM ads JOIN (SELECT ad_id, COUNT(*) AS placements, COUNT(DISTINCT profile_id) AS profiles, SUM(sightings) AS matched_sightings
                       FROM matched GROUP BY ad_id) m ON m.ad_id=ads.id
        JOIN ranked r ON r.ad_id=ads.id AND r.rn=1
        ORDER BY {order} LIMIT ? OFFSET ?''', params + [max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))])
    return {'total': total, 'items': [_decode(r) for r in items], 'limit': limit, 'offset': offset}


def facets(new_since='', data_scope='all', include_duplicates=True):
    clause, params = where({'data_scope': data_scope, 'include_duplicates': include_duplicates})
    base = f'(SELECT f.* FROM findings f{clause})'
    return {'sources': db.rows(f"SELECT source_host AS host, COUNT(*) AS n FROM {base} f WHERE source_host!='' GROUP BY source_host ORDER BY n DESC, host LIMIT 100", params),
            'kinds': db.rows(f'SELECT kind, COUNT(*) AS n FROM {base} f GROUP BY kind ORDER BY n DESC', params),
            'block_types': db.rows(f'SELECT block_type, COUNT(*) AS n FROM {base} f GROUP BY block_type ORDER BY n DESC', params),
            'advertisers': db.rows(f"SELECT lower(trim(d.value)) AS domain, COUNT(DISTINCT f.id) AS n FROM {base} f, {ADVERTISER_VALUES} d WHERE json_type({EVIDENCE_JSON}, '$.target_domains')='array' AND d.type='text' AND trim(d.value)!='' GROUP BY domain ORDER BY n DESC, domain LIMIT 100", params),
            'new_today': db.rows(f'SELECT COUNT(*) AS n FROM {base} f WHERE first_seen>=?', params + [moment(new_since)])[0]['n'] if new_since else 0,
            'new_ads_today': db.rows(f'SELECT COUNT(DISTINCT a.id) AS n FROM {base} f JOIN ads a ON a.id=f.ad_id WHERE a.first_seen>=?', params + [moment(new_since)])[0]['n'] if new_since else 0,
            'labels': KINDS}


def export_preview(filters, limit=5):
    clause, params = where(filters)
    base = f'FROM findings f JOIN profiles ON profiles.id=f.profile_id LEFT JOIN ads ON ads.id=f.ad_id{clause}'
    count = db.rows(f'SELECT COUNT(*) AS n {base}', params)[0]['n']
    items = [_decode(r) for r in db.rows(f'SELECT {FINDING_COLUMNS} {base} ORDER BY f.id LIMIT ?', params + [max(1, min(5, int(limit)))])]
    return {'total': count, 'items': items}


def summary():
    row = db.rows('SELECT COUNT(*) AS findings, MAX(last_seen) AS last_seen, COALESCE(SUM(sightings),0) AS sightings FROM findings')[0]
    row['ads'] = db.rows('SELECT COUNT(*) AS n FROM ads')[0]['n']
    row['test_findings'] = db.rows('SELECT COUNT(*) AS n FROM findings WHERE is_test=1')[0]['n']
    row['duplicate_findings'] = db.rows('SELECT COUNT(*) AS n FROM findings WHERE duplicate_of IS NOT NULL')[0]['n']
    return row


def observations(finding_id=None, ad_id=None, limit=100):
    column, value = ('o.finding_id', finding_id) if finding_id else ('o.ad_id', ad_id)
    total = db.rows(f'SELECT COUNT(*) AS n, COALESCE(SUM(count),0) AS sightings FROM observations o WHERE {column}=?', (value,))[0]
    items = db.rows(f'''SELECT o.id,o.finding_id,o.ad_id,o.profile_id,profiles.name AS profile_name,o.job_id,o.run_id,o.source_url,o.seen_at,
        o.screenshot,o.count,o.legacy FROM observations o JOIN profiles ON profiles.id=o.profile_id WHERE {column}=? ORDER BY o.seen_at DESC, o.id DESC LIMIT ?''',
        (value, limit))
    return {'total': total['n'], 'sightings': total['sightings'], 'items': items}


def family(ad):
    return db.rows('SELECT id,variant_no,variant_reason,title,image_url,first_seen,last_seen,sightings FROM ads WHERE family_key=? ORDER BY variant_no, id',
                   (ad['family_key'],))


def finding_detail(finding_id):
    rows = db.rows(f'SELECT {FINDING_COLUMNS} FROM findings f JOIN profiles ON profiles.id=f.profile_id LEFT JOIN ads ON ads.id=f.ad_id WHERE f.id=?', (finding_id,))
    if not rows:
        return None
    item = _decode(rows[0])
    ad = db.rows('SELECT * FROM ads WHERE id=?', (item['ad_id'],))
    item['ad'] = ad[0] if ad else None
    item['variants'] = family(ad[0]) if ad else []
    item['observations'] = observations(finding_id=finding_id)
    item['placements'] = [_decode(r) for r in db.rows(f'''SELECT {FINDING_COLUMNS} FROM findings f JOIN profiles ON profiles.id=f.profile_id
        LEFT JOIN ads ON ads.id=f.ad_id WHERE f.ad_id=? AND f.id!=? ORDER BY f.last_seen DESC LIMIT 50''', (item['ad_id'], finding_id))] if item['ad_id'] else []
    return item


def ad_detail(ad_id):
    rows = db.rows('SELECT * FROM ads WHERE id=?', (ad_id,))
    if not rows:
        return None
    ad = rows[0]
    ad['variants'] = family(ad)
    ad['placements'] = [_decode(r) for r in db.rows(f'''SELECT {FINDING_COLUMNS} FROM findings f JOIN profiles ON profiles.id=f.profile_id
        LEFT JOIN ads ON ads.id=f.ad_id WHERE f.ad_id=? ORDER BY f.last_seen DESC LIMIT 100''', (ad_id,))]
    ad['observations'] = observations(ad_id=ad_id)
    return ad


# ---------- export ----------

EXPORT_SCHEMA = 'book-radar/findings-export'
EXPORT_VERSION = 1
AD_FIELDS = ('id', 'family_key', 'variant_no', 'variant_of', 'variant_reason', 'title', 'text', 'destination', 'image_url', 'kind',
             'first_seen', 'last_seen', 'sightings')
FINDING_FIELDS = ('id', 'ad_id', 'profile_id', 'profile_name', 'job_id', 'title', 'text', 'clean_text', 'destination', 'image_url',
                  'source_url', 'source_host', 'frame_url', 'kind', 'image_source', 'field_sources', 'ocr_text', 'ocr_status',
                  'first_seen', 'last_seen', 'sightings', 'first_screenshot', 'screenshot', 'crop',
                  'block_type', 'block_reason', 'adapter', 'block_evidence','original_title','is_test','duplicate_of','destination_status')
OBSERVATION_FIELDS = ('id', 'finding_id', 'ad_id', 'profile_id', 'job_id', 'run_id', 'source_url', 'seen_at', 'screenshot', 'count', 'legacy')


def export(filters, with_observations=False, version=''):
    clause, params = where(filters)
    findings = [_decode(row) for row in db.rows(f'''SELECT f.*,profiles.name AS profile_name FROM findings f JOIN profiles ON profiles.id=f.profile_id
        {clause} ORDER BY f.id''', params)]
    ad_ids = sorted({f['ad_id'] for f in findings if f['ad_id']})
    ads = []
    for start in range(0, len(ad_ids), 500):
        chunk = ad_ids[start:start + 500]
        ads += db.rows(f'SELECT * FROM ads WHERE id IN ({",".join("?" * len(chunk))}) ORDER BY id', chunk)
    document = {'schema': EXPORT_SCHEMA, 'schema_version': EXPORT_VERSION, 'app_version': version, 'exported_at': db.now(),
                'filters': {k: v for k, v in filters.items() if v}, 'counts': {'ads': len(ads), 'findings': len(findings)},
                'ads': [{k: ad[k] for k in AD_FIELDS} for ad in ads],
                'findings': [{k: f.get(k) for k in FINDING_FIELDS} for f in findings]}
    if with_observations:
        ids = [f['id'] for f in findings]
        rows = []
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows += db.rows(f'SELECT * FROM observations WHERE finding_id IN ({",".join("?" * len(chunk))}) ORDER BY id', chunk)
        document['observations'] = [{k: r[k] for k in OBSERVATION_FIELDS} for r in rows]
        document['counts']['observations'] = len(rows)
    return document
