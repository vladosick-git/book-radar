import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend import config, db, main, purchases

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setattr(config, 'EMBEDDED_WORKER', False)
    monkeypatch.setattr(config, 'AUTH', False)
    with TestClient(main.app) as instance:
        yield instance


def row(key='c1', **changes):
    return {'record_key': key, 'book': 'book-42', 'account': 'reader-01', 'occurred_at': (NOW - timedelta(hours=2)).isoformat(),
            'amount': '10.00', 'currency': 'EUR', 'status': 'paid', 'order_id': 'order-42', 'receipt_ref': 'owner-receipt', **changes}


def load(client, kind, rows, captured_at=None, source='author.today'):
    return client.post('/api/purchases/import', json={'kind': kind, 'source': source, 'captured_at': captured_at or NOW.isoformat(), 'rows': rows})


def result(client, **params):
    response = client.get('/api/purchases', params=params)
    assert response.status_code == 200
    return response.json()


def test_exact_paid_match_and_source_provenance(client):
    control = load(client, 'control', [row()]).json()
    report = load(client, 'author', [row('report-1')]).json()
    item = result(client)['items'][0]
    assert item['status'] == 'matched'
    assert item['control_batch_id'] == control['batch_id'] and item['report_batch_id'] == report['batch_id']
    assert item['report']['record_key'] == 'report-1'


def test_reimport_idempotent_and_changed_control_atomic(client):
    assert load(client, 'control', [row('z')]).status_code == 200
    assert load(client, 'control', [row('z')]).json()['repeated']
    response = load(client, 'control', [row('a', order_id='different'), row('z', amount='20')])
    assert response.status_code == 409
    assert result(client)['total'] == 1
    assert len(db.rows('SELECT * FROM purchase_batches')) == 1


def test_duplicate_external_order_cannot_be_new_purchase(client):
    load(client, 'control', [row()])
    assert load(client, 'control', [row('reread-chapter')]).status_code == 409
    assert result(client)['total'] == 1


@pytest.mark.parametrize('field,value', [('book', 'other-book'), ('account', 'other-reader'), ('currency', 'UAH'), ('amount', '11.00')])
def test_order_id_does_not_override_conflicting_fields(client, field, value):
    load(client, 'control', [row()]); load(client, 'author', [row('report-1', **{field: value})])
    assert result(client)['items'][0]['status'] == 'mismatch'


@pytest.mark.parametrize('status,expected', [('refunded', 'refunded'), ('rejected', 'rejected'), ('pending', 'delayed'), ('unknown', 'delayed')])
def test_report_outcomes_remain_visible(client, status, expected):
    load(client, 'control', [row()]); load(client, 'author', [row('report', status=status)])
    assert result(client)['items'][0]['status'] == expected


def test_owner_unconfirmed_not_upgraded_by_report(client):
    load(client, 'control', [row(status='pending')]); load(client, 'author', [row('report')])
    assert result(client)['items'][0]['status'] == 'unconfirmed'


def test_possible_not_confirmed_and_no_double_allocation(client):
    load(client, 'control', [row('a', order_id='')]); load(client, 'author', [row('report', order_id='')])
    assert result(client)['items'][0]['status'] == 'possible'
    load(client, 'control', [row('b', order_id='', occurred_at=(NOW - timedelta(hours=2, minutes=5)).isoformat())])
    assert {item['status'] for item in result(client)['items']} == {'ambiguous'}


def test_fallback_time_window_and_ambiguous_exact_id(client):
    load(client, 'control', [row(order_id='')]); load(client, 'author', [row('report', occurred_at=NOW.isoformat())])
    assert result(client, window_minutes=10, delay_hours=0)['items'][0]['status'] == 'unmatched'
    load(client, 'control', [row('order', order_id='another')])
    load(client, 'author', [row('r1', order_id='another'), row('r2', order_id='another')], captured_at=(NOW + timedelta(seconds=1)).isoformat())
    assert next(i for i in result(client)['items'] if i['control']['record_key'] == 'order')['status'] == 'ambiguous'


def test_snapshots_latest_and_historical_selected(client):
    load(client, 'control', [row()])
    paid = load(client, 'author', [row('report')]).json()['batch_id']
    load(client, 'author', [row('report', status='refunded')], captured_at=(NOW + timedelta(minutes=1)).isoformat())
    assert result(client)['items'][0]['status'] == 'refunded'
    assert result(client, report_batch_id=paid)['items'][0]['status'] == 'matched'
    assert client.get('/api/purchases?report_batch_id=999').status_code == 404


def test_old_or_missing_report_does_not_claim_missing_sale(client):
    load(client, 'control', [row()])
    assert result(client, delay_hours=0)['items'][0]['status'] == 'delayed'
    load(client, 'author', [row('other', order_id='not-this-order')], captured_at=(NOW - timedelta(days=1)).isoformat())
    assert result(client, delay_hours=0)['items'][0]['status'] == 'delayed'
    load(client, 'author', [row('other', order_id='not-this-order')])
    assert result(client, delay_hours=0)['items'][0]['status'] == 'unmatched'


def test_csv_validation_bom_money_precision_no_secret_echo(client):
    header = client.get('/api/purchases/template.csv').text
    body = {'kind': 'control', 'source': 'author.today', 'captured_at': NOW.isoformat(),
            'csv_text': '\ufeff' + header + f'csv1,book-42,reader-01,{NOW.isoformat()},10.00,EUR,paid,order-42,receipt\n'}
    assert client.post('/api/purchases/import/csv', json=body).status_code == 200
    body['csv_text'] = header + 'wrong,row\n'
    assert client.post('/api/purchases/import/csv', json=body).status_code == 422
    invalid = row('bad', card_number='PRIVATE-CARD-NUMBER')
    response = load(client, 'control', [invalid])
    assert response.status_code == 422 and 'PRIVATE-CARD-NUMBER' not in response.text
    assert load(client, 'control', [row('bad', amount='1.001')]).status_code == 422
    assert load(client, 'control', [row('bad', occurred_at='2026-10-14T12:00:00')]).status_code == 422
    assert result(client)['total'] == 1


def test_export_auth_audit_and_pagination(client, monkeypatch):
    load(client, 'control', [row('a', order_id=''), row('b', order_id='')])
    assert len(result(client, limit=1)['items']) == 1 and result(client, offset=1)['total'] == 2
    exported = client.get('/api/purchases/export.json')
    assert exported.json()['schema'] == 'book-radar-purchases-1'
    assert exported.headers['cache-control'] == 'no-store'
    audit = db.rows("SELECT details FROM audit WHERE action='purchase_import'")
    assert 'owner-receipt' not in json.dumps(audit) and 'reader-01' not in json.dumps(audit)
    monkeypatch.setattr(config, 'AUTH', True)
    assert client.get('/api/purchases').status_code == 401
    assert client.get('/api/purchases/export.json').status_code == 401
    assert load(client, 'control', [row('new')]).status_code == 401


def test_migration_preserves_previous_fixture_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    shutil.copyfile(Path(__file__).parent / 'fixtures/radar-0.6.2-fixture.db', tmp_path / 'radar.sqlite')
    before = db.rows('SELECT id,name FROM profiles')
    db.init(); db.init()
    assert db.rows('SELECT id,name FROM profiles') == before
    assert db.rows('PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert (tmp_path / 'backups').exists()
    assert not db.rows('PRAGMA foreign_key_check')
