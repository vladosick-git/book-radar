"""Stage 20: manual evidence ledger. Imports owner data; never contacts or purchases from a site."""
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import audit, db

router = APIRouter(prefix='/api/purchases', tags=['Manual purchases'])
STATUS = Literal['paid', 'pending', 'rejected', 'refunded', 'unknown']
LABELS = {'matched': 'Подтверждено отчётом', 'possible': 'Возможное совпадение', 'ambiguous': 'Несколько соответствий',
          'mismatch': 'Поля не совпадают', 'refunded': 'Возврат в отчёте', 'rejected': 'Отказ в отчёте',
          'delayed': 'Ожидаем обновления', 'unmatched': 'Соответствие не найдено', 'unconfirmed': 'Оплата не подтверждена владельцем'}


def aware(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Время должно содержать часовой пояс, например +03:00.')
    return value.astimezone(timezone.utc)


class PurchaseRow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    record_key: str = Field(min_length=1, max_length=120)
    book: str = Field(min_length=1, max_length=300)
    account: str = Field(default='', max_length=120)
    occurred_at: datetime
    amount: Decimal = Field(ge=0, le=100000000, decimal_places=2, max_digits=11)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    status: STATUS = 'unknown'
    order_id: str = Field(default='', max_length=120)
    receipt_ref: str = Field(default='', max_length=300)

    @field_validator('occurred_at')
    @classmethod
    def timestamp(cls, value):
        return aware(value)

    @field_validator('record_key', 'book', 'account', 'order_id', 'receipt_ref')
    @classmethod
    def text(cls, value, info):
        value = value.strip()
        if info.field_name in ('record_key', 'book') and not value:
            raise ValueError('Обязательное поле не должно быть пустым.')
        return value

    def stored(self):
        row = self.model_dump(mode='json')
        row['amount'] = format(self.amount, '.2f')
        row['occurred_at'] = self.occurred_at.isoformat()
        return row


class ImportBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['control', 'author']
    source: str = Field(min_length=1, max_length=100)
    captured_at: datetime
    rows: list[PurchaseRow] = Field(min_length=1, max_length=1000)

    @field_validator('source')
    @classmethod
    def source_name(cls, value):
        if not value.strip():
            raise ValueError('Укажите источник.')
        return value.strip().casefold()

    @field_validator('captured_at')
    @classmethod
    def timestamp(cls, value):
        return aware(value)


class CsvBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['control', 'author']
    source: str = Field(min_length=1, max_length=100)
    captured_at: datetime
    csv_text: str = Field(min_length=1, max_length=1_000_000)


def migrate(connection):
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS purchase_batches (
        id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
        source TEXT NOT NULL, captured_at TEXT NOT NULL, imported_at TEXT NOT NULL, row_count INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS purchase_controls (
        id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES purchase_batches(id),
        source TEXT NOT NULL, record_key TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(source,record_key));
    CREATE TABLE IF NOT EXISTS purchase_report_rows (
        id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES purchase_batches(id),
        record_key TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(batch_id,record_key));
    CREATE INDEX IF NOT EXISTS purchase_batch_source ON purchase_batches(kind,source,captured_at);
    ''')


def import_batch(payload):
    rows = sorted((row.stored() for row in payload.rows), key=lambda row: row['record_key'])
    if len({r['record_key'] for r in rows}) != len(rows):
        raise HTTPException(422, 'В одном файле повторяется record_key; импорт не выполнен.')
    canonical = json.dumps({'kind': payload.kind, 'source': payload.source, 'captured_at': payload.captured_at.isoformat(), 'rows': rows},
                           ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT id FROM purchase_batches WHERE fingerprint=?', (fingerprint,)).fetchone()
        if old:
            return {'batch_id': old['id'], 'inserted': 0, 'unchanged': len(rows), 'repeated': True}
        inserted, unchanged = 0, 0
        batch_id = connection.execute('INSERT INTO purchase_batches(fingerprint,kind,source,captured_at,imported_at,row_count) VALUES (?,?,?,?,?,?)',
            (fingerprint, payload.kind, payload.source, payload.captured_at.isoformat(), db.now(), len(rows))).lastrowid
        for row in rows:
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if payload.kind == 'control':
                old = connection.execute('SELECT payload FROM purchase_controls WHERE source=? AND record_key=?',
                                         (payload.source, row['record_key'])).fetchone()
                if old:
                    if old['payload'] != encoded:
                        raise HTTPException(409, 'Уже сохранённый record_key содержит другие сведения. Импорт целиком отменён; исправления нужно согласовать отдельно.')
                    unchanged += 1; continue
                if row['order_id']:
                    existing = connection.execute('SELECT payload FROM purchase_controls WHERE source=?', (payload.source,)).fetchall()
                    if any(json.loads(r['payload'])['order_id'] == row['order_id'] for r in existing):
                        raise HTTPException(409, 'Этот внешний номер заказа уже учтён под другим record_key. Импорт целиком отменён.')
                connection.execute('INSERT INTO purchase_controls(batch_id,source,record_key,payload) VALUES (?,?,?,?)',
                                   (batch_id, payload.source, row['record_key'], encoded))
            else:
                connection.execute('INSERT INTO purchase_report_rows(batch_id,record_key,payload) VALUES (?,?,?)', (batch_id, row['record_key'], encoded))
            inserted += 1
    return {'batch_id': batch_id, 'inserted': inserted, 'unchanged': unchanged, 'repeated': False}


def reconcile(window_minutes=60, delay_hours=24, report_batch_id=0):
    batches = db.rows("SELECT * FROM purchase_batches WHERE kind='author' ORDER BY captured_at DESC,id DESC")
    selected = {}
    if report_batch_id:
        chosen = next((b for b in batches if b['id'] == report_batch_id), None)
        if not chosen:
            raise HTTPException(404, 'Снимок отчёта не найден.')
        selected[chosen['source']] = chosen
    else:
        for batch in batches:
            selected.setdefault(batch['source'], batch)
    author_rows = {}
    for source, batch in selected.items():
        author_rows[source] = [{**json.loads(row['payload']), 'id': row['id']} for row in db.rows(
            'SELECT id,payload FROM purchase_report_rows WHERE batch_id=?', (batch['id'],))]
    controls = db.rows('SELECT c.*,b.captured_at,b.imported_at FROM purchase_controls c JOIN purchase_batches b ON b.id=c.batch_id ORDER BY c.id DESC')
    results = []
    now = datetime.now(timezone.utc)
    for saved in controls:
        control = json.loads(saved['payload'])
        result = {'id': saved['id'], 'source': saved['source'], 'control': control, 'control_batch_id': saved['batch_id'],
                  'captured_at': saved['captured_at'], 'report_batch_id': selected.get(saved['source'], {}).get('id'),
                  'status': 'unmatched', 'reason': '', 'candidates': [], 'report': None}
        report_rows = author_rows.get(saved['source'], [])
        if control['status'] != 'paid':
            result.update(status='unconfirmed', reason='Состояние владельца: ' + control['status'] + '. Автоматически успешной оплатой не считается.')
        else:
            exact = [r for r in report_rows if control['order_id'] and r['order_id'] == control['order_id']]
            possible = [r for r in report_rows if not control['order_id'] and control['account'] and r['account'] == control['account']
                and r['book'] == control['book'] and r['currency'] == control['currency'] and r['amount'] == control['amount']
                and abs((datetime.fromisoformat(r['occurred_at']) - datetime.fromisoformat(control['occurred_at'])).total_seconds()) <= window_minutes * 60]
            candidates = exact or possible
            result['candidates'] = [r['id'] for r in candidates]
            if len(candidates) > 1:
                result.update(status='ambiguous', reason='Несколько строк подходят. Требуется ручная проверка.')
            elif candidates:
                row = candidates[0]; result['report'] = row
                conflict = [key for key in ('book', 'amount', 'currency') if row[key] != control[key]]
                if row['account'] and control['account'] and row['account'] != control['account']:
                    conflict.append('account')
                if conflict:
                    result.update(status='mismatch', reason='Совпадает номер заказа, но расходятся поля: ' + ', '.join(conflict))
                elif not exact:
                    result.update(status='possible', reason='Совпадают поля и окно времени, но нет устойчивого номера заказа. Это не подтверждение.')
                elif row['status'] in ('refunded', 'rejected'):
                    result.update(status=row['status'], reason='Состояние в выбранном отчёте: ' + row['status'])
                elif row['status'] == 'paid':
                    result.update(status='matched', reason='Совпали внешний номер заказа, книга, сумма и валюта; отчёт подтверждает оплату.')
                else:
                    result.update(status='delayed', reason='В отчёте пока нет подтверждения оплаты: ' + row['status'])
            else:
                batch = selected.get(saved['source'])
                purchase_time = datetime.fromisoformat(control['occurred_at'])
                fresh = batch and datetime.fromisoformat(batch['captured_at']) >= purchase_time
                waiting = not fresh or (now - purchase_time).total_seconds() <= delay_hours * 3600
                result.update(status='delayed' if waiting else 'unmatched', reason='Нет подходящей строки в актуальном снимке отчёта. Это не доказательство ошибки учёта.' if fresh else 'Нет снимка отчёта, выгруженного после покупки.')
        results.append(result)
    # A single report row cannot confirm two separate controls, even through different matching paths.
    usage = Counter(r['report']['id'] for r in results if r['report'] and r['status'] in ('matched', 'possible', 'refunded', 'rejected'))
    for result in results:
        if result['report'] and usage[result['report']['id']] > 1:
            result.update(status='ambiguous', reason='Одна строка отчёта подходит нескольким контрольным случаям. Проверьте вручную.')
    return {'items': results, 'total': len(results), 'counts': dict(Counter(r['status'] for r in results)),
            'labels': LABELS, 'reports': batches, 'rules': {'window_minutes': window_minutes, 'delay_hours': delay_hours,
            'confirmation': 'Только устойчивый номер заказа и подтверждённая оплата; близость полей не подтверждает покупку.'}}


@router.post('/import')
def import_json(payload: ImportBatch, request: Request):
    result = import_batch(payload)
    audit.record('purchase_import', 'purchases', result['batch_id'], request, kind=payload.kind, inserted=result['inserted'], repeated=result['repeated'])
    return result


@router.post('/import/csv')
def import_csv(payload: CsvBatch, request: Request):
    try:
        reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip('\ufeff')), strict=True)
        allowed = set(PurchaseRow.model_fields)
        fields = reader.fieldnames or []
        if not fields or len(set(fields)) != len(fields) or set(fields) - allowed:
            raise ValueError('Неизвестные или повторяющиеся колонки CSV. Используйте шаблон.')
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError('Число полей в строке CSV не совпадает с заголовком.')
            rows.append(row)
            if len(rows) > 1000:
                raise ValueError('За один импорт допускается до 1000 строк.')
        batch = ImportBatch(kind=payload.kind, source=payload.source, captured_at=payload.captured_at, rows=rows)
    except (ValueError, csv.Error):
        raise HTTPException(422, 'CSV не принят: проверьте колонки, статусы, суммы и время с часовым поясом по шаблону. Импорт не выполнен.') from None
    return import_json(batch, request)


@router.get('')
def listing(window_minutes: int = Query(60, ge=0, le=10080), delay_hours: int = Query(24, ge=0, le=720),
            report_batch_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    result = reconcile(window_minutes, delay_hours, report_batch_id)
    result['items'] = result['items'][offset:offset + limit]
    return result


@router.get('/template.csv')
def template():
    return Response('record_key,book,account,occurred_at,amount,currency,status,order_id,receipt_ref\n',
                    media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="purchase-template.csv"'})


@router.get('/export.json')
def export(request: Request, window_minutes: int = Query(60, ge=0, le=10080), delay_hours: int = Query(24, ge=0, le=720), report_batch_id: int = Query(0, ge=0)):
    result = reconcile(window_minutes, delay_hours, report_batch_id)
    result['schema'] = 'book-radar-purchases-1'; result['exported_at'] = db.now()
    audit.record('purchase_export', 'purchases', request=request, rows=result['total'])
    return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="purchase-checks.json"', 'Cache-Control': 'no-store'})
