"""Analyst UI contracts: the same evidence-based filter, count and order in preview and exports."""
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from backend import catalog, config, db, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    with TestClient(main.app) as instance:
        yield instance


def put(client, title, advertiser='author.today', source='librain.ru', destination='https://author.today/work/1'):
    profiles = client.get('/api/state').json()['profiles']
    if not profiles:
        client.post('/api/profiles', json={'name': 'Отдельная фикстура'})
        profiles = client.get('/api/state').json()['profiles']
    jobs = client.get('/api/state').json()['jobs']
    if not jobs:
        response = client.post('/api/jobs', json={'name': 'Фикстура', 'profile_id': profiles[0]['id'], 'urls': ['https://librain.ru/'], 'selector': '', 'wait_seconds': 2, 'headed': False})
        assert response.status_code < 300, response.text
        jobs = client.get('/api/state').json()['jobs']
    job = db.rows('SELECT * FROM jobs WHERE id=?', (jobs[0]['id'],))[0]
    return catalog.save(job, {'title': title, 'text': title, 'destination': destination, 'image_url': f'https://images.example/{title}.png', 'block_type': 'ad', 'block_reason': 'Есть пометка «Реклама»', 'block_evidence': {'target_domains': [advertiser] if advertiser else []}}, f'https://{source}/page', f'https://{source}/page', '')


def test_advertiser_filter_is_evidence_not_source_or_destination(client):
    yes = put(client, 'Маг', ' Author.Today ')
    put(client, 'Фильм', 'rutube.ru', destination='https://author.today/work/1?text=author.today')
    put(client, 'Неизвестный', '', source='author.today')
    response = client.get('/api/findings', params={'advertiser': 'AUTHOR.TODAY'}).json()
    assert response['total'] == 1
    assert response['items'][0]['id'] == yes['finding_id']
    assert response['items'][0]['advertisers'] == ['author.today']
    assert client.get('/api/findings', params={'advertiser': "author.today' OR 1=1 --"}).json()['total'] == 0


def test_preview_and_both_exports_use_same_filter_count_and_order(client):
    for i in range(8):
        put(client, f'Книга {i}', 'author.today' if i < 7 else 'rutube.ru')
    params = {'advertiser': 'author.today', 'block_type': 'ad'}
    preview = client.get('/api/export/preview', params=params).json()
    document = client.get('/api/export.json', params=params).json()
    rows = list(csv.DictReader(io.StringIO(client.get('/api/export.csv', params=params).text.lstrip('\ufeff'))))
    assert preview['total'] == document['counts']['findings'] == len(rows) == 7
    assert len(preview['items']) == 5
    assert [r['id'] for r in preview['items']] == [r['id'] for r in document['findings'][:5]] == [int(r['id']) for r in rows[:5]]
    assert all(r['advertiser'].strip().lower() == 'author.today' for r in rows)


def test_ads_representative_respects_source_and_advertiser_filters(client):
    first = put(client, 'Один креатив', 'author.today', 'librain.ru')
    second = put(client, 'Один креатив', 'rutube.ru', 'readboom.net')
    assert first['ad_id'] == second['ad_id']
    result = client.get('/api/ads', params={'source': 'librain.ru', 'advertiser': 'author.today'}).json()
    assert result['total'] == 1
    item = result['items'][0]
    assert item['source_host'] == 'librain.ru'
    assert item['finding_id'] == first['finding_id']
    assert item['advertisers'] == ['author.today']
    assert item['matched_sightings'] == 1


def test_advertiser_facets_ignore_malformed_evidence_and_duplicate_domains(client):
    first = put(client, 'Обычная книга')
    second = put(client, 'Повреждённая запись', 'rutube.ru')
    db.execute('UPDATE findings SET block_evidence=? WHERE id=?', ('not json', second['finding_id']))
    db.execute('UPDATE findings SET block_evidence=? WHERE id=?', (json.dumps({'target_domains': ['author.today', 'AUTHOR.TODAY']}), first['finding_id']))
    response = client.get('/api/findings/facets')
    assert response.status_code == 200
    assert response.json()['advertisers'] == [{'domain': 'author.today', 'n': 1}]
    assert client.get('/api/findings').status_code == 200


def test_new_count_uses_first_seen_and_not_repeated_observations(client):
    item = put(client, 'Старая книга')
    db.execute('UPDATE findings SET first_seen=? WHERE id=?', ('2020-01-01T00:00:00+00:00', item['finding_id']))
    db.execute('UPDATE ads SET first_seen=? WHERE id=?', ('2020-01-01T00:00:00+00:00', item['ad_id']))
    result = client.get('/api/findings/facets', params={'new_since': '2026-01-01T00:00:00+00:00'}).json()
    assert result['new_today'] == result['new_ads_today'] == 0
    assert client.get('/api/findings/facets', params={'new_since': 'not-a-date'}).status_code == 400


def test_empty_preview_has_no_sample_records(client):
    assert client.get('/api/export/preview').json() == {'total': 0, 'items': []}
