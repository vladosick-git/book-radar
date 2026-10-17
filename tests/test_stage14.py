"""Stage 14: ads (creatives) vs placements vs observations, the variant rule, a repeatable migration of real 0.6.2
data, server-side search beyond the former 500-row window, and the JSON/CSV exports."""
import csv
import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import catalog, config, db, main

LEGACY = Path(__file__).parent / 'fixtures' / 'radar-0.6.2-fixture.db'


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    db.init()
    return tmp_path


@pytest.fixture
def client(data, monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as instance:
        yield instance


def make_job(name='Профиль'):
    found = db.rows('SELECT id FROM profiles WHERE name=?', (name,))
    profile = found[0]['id'] if found else db.execute('INSERT INTO profiles(name,created_at) VALUES (?,?)', (name, db.now()))
    job_id = db.execute('INSERT INTO jobs(name,profile_id,urls,selector,wait_seconds,created_at) VALUES (?,?,?,?,?,?)',
                        (f'Сбор {name}', profile, '["https://site.test/"]', '', 2, db.now()))
    return db.rows('SELECT * FROM jobs WHERE id=?', (job_id,))[0]


BASE = {'title': 'Город за туманом', 'text': 'Прочитайте первую главу', 'destination': 'https://author.test/work/1', 'image_url': 'https://img.test/1.jpg'}


# ---------- variant rule ----------

@pytest.mark.parametrize('change', [
    {'title': '  город   за ТУМАНОМ '},                                        # whitespace and letter case
    {'destination': 'https://author.test/work/1?utm_source=x&utm_medium=y'},  # tracking parameters
    {'destination': 'http://author.test/work/1/'},                           # http/https and a trailing slash
    {'image_url': 'https://img.test/1.jpg?cb=123&rnd=8'},                    # cache busters of the image
    {'text': 'Скидка 30% до пятницы'},                                       # body text (counters, dates)
])
def test_same_creative_counterexamples(data, change):
    job = make_job()
    first = catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'a.png')
    second = catalog.save(job, {**BASE, **change}, 'https://site.test/', 'https://site.test/', 'b.png')
    assert second['ad_id'] == first['ad_id']
    assert db.rows('SELECT COUNT(*) AS n FROM ads')[0]['n'] == 1


@pytest.mark.parametrize('change,reason', [
    ({'title': 'Город за туманом. Новая обложка'}, 'title'),
    ({'image_url': 'https://img.test/1-new.jpg'}, 'image'),
    ({'title': 'Другая книга', 'image_url': 'https://img.test/2.jpg'}, 'title,image'),
])
def test_changed_title_or_image_is_a_new_variant(data, change, reason):
    job = make_job()
    first = catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'a.png')
    second = catalog.save(job, {**BASE, **change}, 'https://site.test/', 'https://site.test/', 'b.png')
    assert second['ad_id'] != first['ad_id']
    ad = db.rows('SELECT * FROM ads WHERE id=?', (second['ad_id'],))[0]
    assert (ad['variant_no'], ad['variant_of'], ad['variant_reason']) == (2, first['ad_id'], reason)


def test_variant_reason_is_relative_to_the_previous_variant(data):
    job = make_job()
    root = catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'a.png')
    catalog.save(job, {**BASE, 'title': 'Новый заголовок'}, 'https://site.test/', 'https://site.test/', 'b.png')
    third = catalog.save(job, {**BASE, 'title': 'Новый заголовок', 'image_url': 'https://img.test/3.jpg'}, 'https://site.test/', 'https://site.test/', 'c.png')
    ad = db.rows('SELECT * FROM ads WHERE id=?', (third['ad_id'],))[0]
    assert (ad['variant_no'], ad['variant_of'], ad['variant_reason']) == (3, root['ad_id'], 'image')


def test_other_destination_is_another_ad_not_a_variant(data):
    job = make_job()
    first = catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'a.png')
    other = catalog.save(job, {**BASE, 'destination': 'https://author.test/work/2'}, 'https://site.test/', 'https://site.test/', 'b.png')
    ad = db.rows('SELECT * FROM ads WHERE id=?', (other['ad_id'],))[0]
    assert other['ad_id'] != first['ad_id'] and ad['variant_no'] == 1 and ad['variant_of'] is None


def test_one_creative_many_placements_and_observations(data):
    a, b = make_job('А'), make_job('Б')
    for job, page in [(a, 'https://site.test/'), (a, 'https://site.test/'), (b, 'https://site.test/top')]:
        catalog.save(job, BASE, page, page, 'x.png', run_id=None)
    ad = db.rows('SELECT * FROM ads')[0]
    assert ad['sightings'] == 3
    assert db.rows('SELECT COUNT(*) AS n FROM findings')[0]['n'] == 2  # placement = profile × page × exact block
    assert db.rows('SELECT COUNT(*) AS n FROM observations')[0]['n'] == 3
    detail = catalog.ad_detail(ad['id'])
    assert len(detail['placements']) == 2 and detail['observations']['sightings'] == 3


# ---------- repeatable migration of real 0.6.2 data ----------

def legacy(tmp_path, monkeypatch):
    shutil.copy(LEGACY, tmp_path / 'radar.sqlite')
    monkeypatch.setattr(db, 'DATA', tmp_path)
    import sqlite3
    connection = sqlite3.connect(tmp_path / 'radar.sqlite')
    before = {row[0]: row[1:] for row in connection.execute('SELECT id,fingerprint,sightings,first_seen,last_seen,profile_id FROM findings')}
    connection.close()
    return before


def snapshot():
    return (db.rows('SELECT id,ad_id,kind,source_host,sightings FROM findings ORDER BY id'),
            db.rows('SELECT * FROM ads ORDER BY id'), db.rows('SELECT * FROM observations ORDER BY id'))


def test_migration_from_062_keeps_ids_counts_and_is_repeatable(tmp_path, monkeypatch):
    before = legacy(tmp_path, monkeypatch)
    assert len(before) == 6
    db.init()
    after = {row['id']: row for row in db.rows('SELECT * FROM findings')}
    # IDs, fingerprints and counters of every 0.6.2 finding are unchanged.
    assert {k: (v['fingerprint'], v['sightings'], v['first_seen'], v['last_seen'], v['profile_id']) for k, v in after.items()} == before
    assert all(row['ad_id'] for row in after.values())
    # Sum of observation counts per placement equals its sightings; creatives sum their placements.
    for finding_id, row in after.items():
        assert db.rows('SELECT SUM(count) AS n FROM observations WHERE finding_id=?', (finding_id,))[0]['n'] == row['sightings']
    for ad in db.rows('SELECT * FROM ads'):
        assert ad['sightings'] == db.rows('SELECT SUM(sightings) AS n FROM findings WHERE ad_id=?', (ad['id'],))[0]['n']
    ads = db.rows('SELECT * FROM ads ORDER BY id')
    families = {}
    for ad in ads:
        families.setdefault(ad['family_key'], []).append(ad)
    work1 = families['dest:author.test/work/1']
    # Two profiles + a text-only change = one creative; the new cover (title and image) = variant 2 of the same family.
    assert [(ad['variant_no'], ad['variant_reason']) for ad in work1] == [(1, ''), (2, 'title,image')]
    assert work1[0]['sightings'] == 5 + 2 + 1
    first_state = snapshot()
    # Running the migration again (new start of the program, or directly) duplicates nothing.
    db.init()
    with db.connect() as connection:
        assert catalog.migrate(connection) == 0
    assert snapshot() == first_state
    assert db.rows("SELECT value FROM settings WHERE key='app_version'")[0]['value'] == config.VERSION


def test_upgraded_legacy_data_keeps_collecting(tmp_path, monkeypatch):
    legacy(tmp_path, monkeypatch)
    db.init()
    job = db.rows('SELECT * FROM jobs WHERE profile_id=1')[0]
    old = db.rows('SELECT * FROM findings WHERE id=1')[0]
    item = {key: old[key] for key in ('title', 'text', 'destination', 'image_url')}
    result = catalog.save(job, item, old['source_url'], old['frame_url'], 'new.png')
    assert result['finding_id'] == 1 and not result['new']
    assert db.rows('SELECT sightings FROM findings WHERE id=1')[0]['sightings'] == old['sightings'] + 1
    assert db.rows('SELECT SUM(count) AS n FROM observations WHERE finding_id=1')[0]['n'] == old['sightings'] + 1


# ---------- server search ----------

def fill(count=650):
    job_a, job_b = make_job('Первый'), make_job('Второй')
    with db.connect() as connection:
        for index in range(count):
            job = job_a if index % 2 else job_b
            seen = f'2026-{9 + index // 400:02d}-{1 + index % 28:02d}T10:00:{index % 60:02d}+00:00'
            item = {'title': f'Книга номер {index}', 'text': 'Обычный текст', 'destination': f'https://author.test/work/{index}',
                    'image_url': '' if index % 3 else f'https://img.test/{index}.jpg'}
            host = 'other.test' if index % 5 == 0 else 'author.test'
            item_id = connection.execute('''INSERT INTO findings(fingerprint,profile_id,job_id,title,text,destination,image_url,source_url,frame_url,
                screenshot,first_screenshot,first_seen,last_seen,sightings,kind,source_host,search_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (f'fp{index}', job['profile_id'], job['id'], item['title'], item['text'], item['destination'], item['image_url'],
                 f'https://{host}/', f'https://{host}/', 'x.png', 'x.png', seen, seen, 1, catalog.kind_of(item), host,
                 catalog.search_text(item, f'https://{host}/'))).lastrowid
            connection.execute('INSERT INTO observations(finding_id,profile_id,job_id,source_url,seen_at,screenshot) VALUES (?,?,?,?,?,?)',
                               (item_id, job['profile_id'], job['id'], f'https://{host}/', seen, 'x.png'))
    return job_a, job_b


def test_search_reaches_beyond_500_with_filters_and_pages(client):
    job_a, job_b = fill()
    everything = client.get('/api/findings', params={'limit': 200}).json()
    assert everything['total'] == 650 and len(everything['items']) == 200
    # The oldest record (number 0) is far beyond the first 500 by date and is still found, case-insensitively.
    oldest = client.get('/api/findings', params={'q': 'КНИГА НОМЕР 0'}).json()
    assert any(item['title'] == 'Книга номер 0' for item in oldest['items'])
    pages = [client.get('/api/findings', params={'limit': 200, 'offset': offset}).json()['items'] for offset in (0, 200, 400, 600)]
    ids = [item['id'] for page in pages for item in page]
    assert len(ids) == 650 and len(set(ids)) == 650
    assert client.get('/api/findings', params={'profile_id': job_a['profile_id']}).json()['total'] == 325
    assert client.get('/api/findings', params={'source': 'other.test'}).json()['total'] == 130
    banners = client.get('/api/findings', params={'kind': 'mixed'}).json()['total']
    assert banners == 217 and client.get('/api/findings', params={'kind': 'text'}).json()['total'] == 433
    september = client.get('/api/findings', params={'since': '2026-09-01', 'until': '2026-09-30'}).json()['total']
    assert september == 400
    assert client.get('/api/findings', params={'since': '2026-10-01', 'until': '2026-10-31', 'source': 'other.test'}).json()['total'] == 50
    assert client.get('/api/findings', params={'since': 'вчера'}).status_code == 400
    assert client.get('/api/findings', params={'kind': 'unknown'}).status_code == 400
    assert client.get('/api/findings', params={'q': '100%'}).json()['total'] == 0  # % is literal, not a wildcard
    facets = client.get('/api/findings/facets').json()
    assert {f['host']: f['n'] for f in facets['sources']} == {'author.test': 520, 'other.test': 130}
    state = client.get('/api/state').json()
    assert 'findings' not in state and 'logs' not in state and state['catalog']['findings'] == 650


def test_detail_ads_view_and_exports(client):
    job = make_job()
    first = catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'a.png')
    catalog.save(job, BASE, 'https://site.test/', 'https://site.test/', 'b.png')
    variant = catalog.save(job, {**BASE, 'title': '=Новая обложка'}, 'https://site.test/', 'https://site.test/', 'c.png')
    detail = client.get(f'/api/findings/{first["finding_id"]}').json()
    assert detail['observations']['total'] == 2 and [v['variant_no'] for v in detail['variants']] == [1, 2]
    assert client.get('/api/findings/999').status_code == 404
    ads = client.get('/api/ads').json()
    assert ads['total'] == 2 and {a['variants'] for a in ads['items']} == {2}
    assert client.get(f'/api/ads/{variant["ad_id"]}').json()['variant_reason'] == 'title'
    exported = client.get('/api/export.json', params={'observations': 'true'})
    document = json.loads(exported.text)
    assert document['schema'] == catalog.EXPORT_SCHEMA and document['schema_version'] == 1 and document['app_version'] == config.VERSION
    assert document['counts'] == {'ads': 2, 'findings': 2, 'observations': 3}
    assert set(document['findings'][0]) == set(catalog.FINDING_FIELDS) and set(document['ads'][0]) == set(catalog.AD_FIELDS)
    schema = json.loads((Path(__file__).parents[1] / 'docs' / 'EXPORT_SCHEMA_0.7.json').read_text(encoding='utf-8'))
    assert set(schema['$defs']['finding']['required']) == set(catalog.FINDING_FIELDS)
    assert set(schema['$defs']['ad']['required']) == set(catalog.AD_FIELDS)
    assert set(schema['$defs']['observation']['required']) == set(catalog.OBSERVATION_FIELDS)
    filtered = json.loads(client.get('/api/export.json', params={'q': 'новая'}).text)
    assert filtered['counts']['findings'] == 1 and 'observations' not in filtered
    table = list(csv.reader(io.StringIO(client.get('/api/export.csv').text.lstrip('﻿'))))
    assert table[0][:13] == ['id', 'title', 'text', 'destination', 'image_url', 'source_url', 'frame_url', 'profile_id', 'first_seen', 'last_seen',
                             'sightings', 'first_screenshot', 'screenshot']
    assert "'=Новая обложка" in [row[1] for row in table[1:]]  # formula injection stays neutralised
    assert 'export_json' in json.dumps(client.get('/api/audit').json())
