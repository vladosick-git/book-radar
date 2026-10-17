import React from 'react';
import { Plus, Upload, Download } from 'lucide-react';
import { Field, Modal, PageHeader, Notice, Empty, Pager, Select, useModalClose } from './components';
import { api, qs, stamp, useServerList } from './api';

const STATES = [['unknown', 'Неизвестно'], ['paid', 'Оплачено вручную'], ['pending', 'Ожидается'], ['rejected', 'Отказ'], ['refunded', 'Возврат']];
const localNow = () => { const date = new Date(); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

function ManualPurchase({ done }) {
  const close = useModalClose();
  const [value, setValue] = React.useState({ source: 'author.today', book: '', account: '', occurred_at: localNow(), amount: '', currency: '', status: 'unknown', order_id: '', receipt_ref: '' });
  const [recordKey] = React.useState(() => crypto.randomUUID());
  const [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const field = name => ({ value: value[name], onChange: event => setValue(previous => ({ ...previous, [name]: event.target.value })) });
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const { source, ...row } = value;
      await api('/purchases/import', { kind: 'control', source, captured_at: new Date().toISOString(), rows: [{ ...row, record_key: recordKey, occurred_at: new Date(row.occurred_at).toISOString() }] });
      done(); close();
    } catch (e) { setError(e.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <p className="meta">Запишите уже выполненное ручное действие. Банковские реквизиты и пароли сюда не добавляйте.</p>
    <Field label="Источник / площадка" hint="Должен совпадать с источником импортируемого отчёта."><input {...field('source')} required maxLength={100} /></Field>
    <Field label="Книга или её устойчивый ID"><input {...field('book')} required maxLength={300} /></Field>
    <Field label="Метка аккаунта" optional><input {...field('account')} maxLength={120} placeholder="Например, контроль-01" /></Field>
    <Field label="Время покупки"><input type="datetime-local" {...field('occurred_at')} required /></Field>
    <div className="form-pair">
      <Field label="Сумма"><input {...field('amount')} type="number" min="0" step="0.01" required /></Field>
      <Field label="Валюта" hint="Три буквы: RUB, UAH, EUR…"><input {...field('currency')} required pattern="[A-Z]{3}" maxLength={3} /></Field>
    </div>
    <Field label="Состояние по сведениям владельца"><select aria-label="Состояние по сведениям владельца" {...field('status')}>{STATES.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></Field>
    <Field label="Внешний номер заказа" optional hint="Без него совпадение по времени и сумме будет только предположением."><input {...field('order_id')} maxLength={120} /></Field>
    <Field label="Ссылка или обозначение чека" optional hint="Только справочная запись; программа не открывает и не загружает чек."><input {...field('receipt_ref')} maxLength={300} /></Field>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close}>Отмена</button><button className="btn primary" disabled={saving}>{saving ? 'Сохранение…' : 'Сохранить запись'}</button></div>
  </form>;
}

function ImportFile({ done }) {
  const close = useModalClose();
  const [kind, setKind] = React.useState('author'), [source, setSource] = React.useState('author.today'), [captured, setCaptured] = React.useState(localNow());
  const [file, setFile] = React.useState(null), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      if (!file || file.size > 1000000) throw new Error('Выберите CSV до 1 МБ.');
      const result = await api('/purchases/import/csv', { kind, source, captured_at: new Date(captured).toISOString(), csv_text: await file.text() });
      done(result); close();
    } catch (e) { setError(e.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <Notice>Используйте обезличенный CSV по шаблону. Повторный импорт не дублирует покупки. Новый отчёт сохраняется отдельным снимком.</Notice>
    <Field label="Что импортировать"><select aria-label="Что импортировать" value={kind} onChange={e => setKind(e.target.value)}><option value="author">Отчёт автора</option><option value="control">Ручные покупки владельца</option></select></Field>
    <Field label="Источник / площадка"><input value={source} onChange={e => setSource(e.target.value)} required maxLength={100} /></Field>
    <Field label="Когда выгружен отчёт или реестр"><input type="datetime-local" value={captured} onChange={e => setCaptured(e.target.value)} required /></Field>
    <Field label="CSV-файл" hint="UTF-8, разделитель — запятая, до 1000 строк. Время в строках — с часовым поясом."><input type="file" accept=".csv,text/csv" onChange={e => setFile(e.target.files?.[0] || null)} required /></Field>
    <p className="meta">Состояния: paid, pending, rejected, refunded, unknown. record_key — стабильная метка строки; book — одинаковый ID книги в обоих реестрах.</p>
    <a className="inline-link" href="/api/purchases/template.csv" download>Скачать шаблон CSV</a>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close}>Отмена</button><button className="btn primary" disabled={saving || !file}>{saving ? 'Импорт…' : 'Импортировать'}</button></div>
  </form>;
}

export default function Purchases() {
  const [modal, setModal] = React.useState(''), [offset, setOffset] = React.useState(0), [reportId, setReportId] = React.useState('');
  const [windowMinutes, setWindowMinutes] = React.useState(60), [delayHours, setDelayHours] = React.useState(24), [message, setMessage] = React.useState('');
  const filters = { report_batch_id: reportId, window_minutes: windowMinutes, delay_hours: delayHours };
  const { data, loading, error, reload } = useServerList(`/purchases${qs({ ...filters, offset, limit: 50 })}`, 0);
  const done = result => { setMessage(result ? `Импортировано: ${result.inserted}; уже учтено: ${result.unchanged}.` : 'Ручная запись сохранена.'); setOffset(0); reload(); };
  return <>
    <PageHeader title="Сверка покупок" actions={<><button className="btn secondary" onClick={() => setModal('import')}><Upload size={15} />Импорт CSV</button><button className="btn primary" onClick={() => setModal('manual')}><Plus size={15} />Ручная запись</button></>}>
      Покупки владельца и доступный отчёт автора. Расхождение требует проверки, а не вывода о причине.
    </PageHeader>
    <div className="toolbar purchase-filters">
      <Select label="Снимок отчёта" value={reportId} onChange={v => { setReportId(v); setOffset(0); }} options={[
        ['', 'Последний снимок каждого источника'], ...(data?.reports || []).map(r => [String(r.id), `#${r.id} · ${r.source} · ${stamp(r.captured_at)}`])]} />
      <Field label="Окно возможного совпадения, мин"><input type="number" value={windowMinutes} min={0} max={10080} onChange={e => { setWindowMinutes(Math.min(10080, Math.max(0, Number(e.target.value)))); setOffset(0); }} /></Field>
      <Field label="Ожидание обновления, ч"><input type="number" value={delayHours} min={0} max={720} onChange={e => { setDelayHours(Math.min(720, Math.max(0, Number(e.target.value)))); setOffset(0); }} /></Field>
    </div>
    <p className="meta">Настройки 60 минут и 24 часа — рабочие значения для проверки, их можно изменить. Совпадение без номера заказа не подтверждает оплату.</p>
    {message && <Notice tone="success" onClose={() => setMessage('')}>{message}</Notice>}
    {error && <Notice tone="danger">{error}</Notice>}
    {data && <div className="purchase-counts">{Object.entries(data.counts).map(([key, count]) => <span key={key}>{data.labels[key]}: <strong>{count}</strong></span>)}</div>}
    {data?.items.length ? <ol className="purchase-list">{data.items.map(item => <li key={item.id} className="purchase-card">
      <div className="purchase-card-head"><h3>{item.control.book}</h3><strong className={`purchase-state state-${item.status}`}>{data.labels[item.status]}</strong></div>
      <p>{item.control.amount} {item.control.currency} · {item.source} · {stamp(item.control.occurred_at)}</p>
      <p className="meta">Аккаунт: {item.control.account || 'не указан'} · Заказ: {item.control.order_id || 'нет номера'} · Состояние владельца: {STATES.find(([key]) => key === item.control.status)?.[1]}</p>
      <p>{item.reason}</p>
      <p className="meta">Реестр #{item.control_batch_id}{item.report_batch_id ? ` · снимок отчёта #${item.report_batch_id}` : ' · отчёт ещё не загружен'}{item.report ? ` · строка ${item.report.record_key}, состояние ${item.report.status}` : ''}</p>
      {item.control.receipt_ref && <p className="meta">Чек: {item.control.receipt_ref}</p>}
    </li>)}</ol> : data && <Empty title="Ручных покупок пока нет">Добавьте запись уже выполненной покупки или импортируйте реестр. Затем загрузите отчёт автора для сверки.</Empty>}
    <Pager total={data?.total || 0} offset={offset} limit={50} loading={loading} onChange={setOffset} />
    <a className="btn secondary" href={`/api/purchases/export.json${qs(filters)}`} download><Download size={15} />Выгрузить сверку JSON</a>
    {modal && <Modal title={modal === 'manual' ? 'Учесть ручную покупку' : 'Импортировать реестр'} close={() => setModal('')}>{modal === 'manual' ? <ManualPurchase done={done} /> : <ImportFile done={done} />}</Modal>}
  </>;
}
