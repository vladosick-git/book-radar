import React from 'react';
import { Play, Plus, Square, Mail, ArrowRight } from 'lucide-react';
import { Field, Busy, Notice } from './components';
import { usePref } from './api';
import { Disclosure } from './SimulationUI';

const statuses = { running: 'Боты работают', stopping: 'Останавливаем', stopped: 'Партия остановлена', done: 'Партия завершена', failed: 'Ошибка партии', interrupted: 'Прервана перезапуском' };
const phases = { waiting: 'В очереди', registering: 'Проверка аккаунта', reading: 'Читает книгу', complete: 'Готово' };

export function FleetStatus({ fleet, busy, stop }) {
  const [limit, setLimit] = React.useState(12);
  if (!fleet) return null;
  const active = ['running', 'stopping'].includes(fleet.status), c = fleet.counts;
  const finished = c.done + c.failed + c.stopped;
  return <section className={`fleet-status${active ? ' is-active' : ''}`} aria-label="Состояние партии">
    <div className="fleet-heading"><div><strong>{statuses[fleet.status] || fleet.status}</strong><span className="meta">{finished} из {fleet.target} обработано · до {fleet.concurrency} браузеров одновременно</span></div>
      {active && <button className="btn danger-ghost sm" disabled={busy || fleet.status === 'stopping'} onClick={stop}><Square size={13} aria-hidden="true" />{fleet.status === 'stopping' ? 'Остановка…' : 'Остановить партию'}</button>}</div>
    <div className="fleet-progress" role="progressbar" aria-label="Прогресс партии" aria-valuemin={0} aria-valuemax={fleet.target} aria-valuenow={finished}><span style={{ width: `${Math.min(100, finished / Math.max(1, fleet.target) * 100)}%` }} /></div>
    <div className="fleet-counts" aria-live="polite"><span>В очереди <strong>{c.queued}</strong></span><span>Работают <strong>{c.running}</strong></span><span>Готово <strong>{c.done}</strong></span><span className={c.failed ? 'error-text' : ''}>Ошибок <strong>{c.failed}</strong></span>{c.stopped > 0 && <span>Остановлено <strong>{c.stopped}</strong></span>}</div>
    {active && <Disclosure title="Подробности партии" caption={`${fleet.members.length} ботов`}><ul className="fleet-members">{fleet.members.slice(0, limit).map(m => <li key={m.profile_id}><span>{m.name}</span><span>{m.status === 'done' ? 'Готово' : m.status === 'failed' ? 'Ошибка' : phases[m.phase] || m.status}</span></li>)}</ul>{fleet.members.length > limit && <button className="btn ghost sm" onClick={() => setLimit(n => n + 20)}>Показать ещё</button>}</Disclosure>}
  </section>;
}

export default function SimulationBatch({ pool, profiles, mail, fleet, busy, operation, perform, refreshMail, refreshFleet, fixtureOnly, setFixtureOnly, openMail }) {
  const [target, setTarget] = usePref('simulation-target', 3), [count, setCount] = usePref('simulation-new-count', 3);
  const [newEmails, setNewEmails] = usePref('simulation-new-addresses', true), [addresses, setAddresses] = React.useState('');
  const [concurrency, setConcurrency] = usePref('simulation-concurrency', 2), [readBooks, setReadBooks] = usePref('simulation-read-books', true), [pages, setPages] = usePref('simulation-pages', 1);
  const [message, setMessage] = React.useState('');
  const free = fixtureOnly ? pool?.available || 0 : mail?.free || 0;
  const existing = fixtureOnly ? profiles.length : profiles.filter(p => p.mail_ready).length;
  const available = existing + free, shortage = Math.max(0, Number(target) - available);
  const active = ['running', 'stopping'].includes(fleet?.status);
  const targetValid = Number.isInteger(Number(target)) && Number(target) >= 1 && Number(target) <= 500;
  const countValid = Number.isInteger(Number(count)) && Number(count) >= 1 && Number(count) <= 500;
  async function generate(amount = Number(count), fresh = newEmails) {
    setMessage('');
    const result = await perform(fixtureOnly ? 'batch' : fresh ? 'mail/create' : 'mail/assign', fixtureOnly ? { count: amount, new_emails: fresh } : { count: amount });
    if (result) { setMessage(fixtureOnly ? `Создано ботов: ${result.profile_ids.length}. Адреса используются только на локальном сайте.` : fresh ? `Создание почты запущено для ${result.profile_ids.length} ботов. Результаты появятся на вкладке «Почта».` : `Ящиков закреплено за ботами: ${result.profile_ids.length}.`); await refreshMail(); }
  }
  async function start(event) {
    event.preventDefault(); setMessage('');
    const result = await perform('fleet/start', { target: Number(target), concurrency: Number(concurrency), read_books: readBooks, pages: Number(pages), permanent_only: !fixtureOnly });
    if (result) await refreshFleet();
  }
  async function importAddresses(event) {
    event.preventDefault(); setMessage('');
    const result = await perform('mail-pool/import', { addresses: addresses.split(/\r?\n/).map(s => s.trim()).filter(Boolean) });
    if (result) { setAddresses(''); setMessage(`Добавлено адресов: ${result.added}. Уже были в базе: ${result.duplicates}.`); }
  }
  return <div className="batch-workspace">
    <div className="address-mode"><span>Почта для запуска</span><div className="mode-options" role="group" aria-label="Тип адресов">
      <label className={fixtureOnly ? '' : 'on'}><input type="radio" name="address-mode" checked={!fixtureOnly} disabled={busy || active} onChange={() => setFixtureOnly(false)} />Постоянная почта</label>
      <label className={fixtureOnly ? 'on' : ''}><input type="radio" name="address-mode" checked={fixtureOnly} disabled={busy || active} onChange={() => setFixtureOnly(true)} />Локальные адреса</label>
    </div></div>
    <p className="mode-description">{fixtureOnly ? 'Адреса без почтовых ящиков. Работают только как логины встроенного локального сайта.' : 'Используются только ящики с подтверждённым входом. Каждый адрес закрепляется за одним ботом.'}</p>
    <div className="availability"><span>Ботов с {fixtureOnly ? 'адресами' : 'готовой почтой'} <strong>{existing}</strong></span><span>Свободных {fixtureOnly ? 'адресов' : 'ящиков'} <strong>{free}</strong></span><span>Можно запустить <strong>{available}</strong></span></div>
    {!fixtureOnly && !mail?.configured && <div className="mail-setup-hint"><Mail size={18} aria-hidden="true" /><div><strong>Подключите почту для новых ботов</strong><span>Свой домен через Mailcow или уже готовые ящики.</span></div><button type="button" className="btn secondary sm" onClick={openMail}>Настроить почту<ArrowRight size={14} aria-hidden="true" /></button></div>}
    {message && <Notice tone="success">{message}</Notice>}
    <div className="batch-columns">
      <form className="form batch-form" onSubmit={start}>
        <div className="workflow-heading"><span><Play size={15} aria-hidden="true" /></span><div><h2>Запустить ботов</h2><p>Существующие боты и свободные адреса из базы</p></div></div>
        <Field label="Сколько ботов должно работать"><input className="count-input" type="number" min="1" max="500" required value={target} onChange={e => setTarget(e.target.value)} /></Field>
        {targetValid && (shortage > 0 ? <div className="shortage-message"><strong>Не хватает {shortage} {fixtureOnly ? 'адресов' : 'ящиков'}</strong><p>Для {target} ботов доступно {available}.</p>
          <button type="button" className="text-button" disabled={busy || active || (!fixtureOnly && (!mail?.configured || mail.running))} onClick={() => generate(shortage, true)}>Создать недостающих: {shortage}<ArrowRight size={14} aria-hidden="true" /></button>
        </div> : <p className="ready-message">Адресов достаточно для запуска.</p>)}
        <label className="check"><input type="checkbox" checked={readBooks} onChange={e => setReadBooks(e.target.checked)} />Читать книгу после входа</label>
        <Disclosure title="Параметры запуска" caption={`${concurrency} браузера · ${readBooks ? `${pages} стр. по 1–2 мин.` : 'только вход'}`}>
          <Field label="Одновременно открытых браузеров"><input type="number" min="1" max="3" required value={concurrency} onChange={e => setConcurrency(e.target.value)} /></Field>
          {readBooks && <Field label="Страниц на каждого бота"><input type="number" min="1" max="6" required value={pages} onChange={e => setPages(e.target.value)} /></Field>}
          <p className="meta">Остальные боты ждут в очереди. На каждой странице случайная задержка 60–120 секунд.</p>
        </Disclosure>
        <div className="toolbar"><button className="btn primary" disabled={busy || active || shortage > 0 || !targetValid}><Busy busy={busy && operation === 'fleet/start'} busyText="Запуск…"><Play size={15} aria-hidden="true" />Запустить ботов: {target || 0}</Busy></button></div>
      </form>
      <form className="form batch-form" onSubmit={e => { e.preventDefault(); generate(); }}>
        <div className="workflow-heading"><span><Plus size={17} aria-hidden="true" /></span><div><h2>Создать новых ботов</h2><p>Пополнить базу перед следующим запуском</p></div></div>
        <Field label="Сколько новых ботов создать"><input className="count-input" type="number" min="1" max="500" required value={count} onChange={e => setCount(e.target.value)} /></Field>
        <label className="check"><input type="checkbox" checked={newEmails} onChange={e => setNewEmails(e.target.checked)} />{fixtureOnly ? 'Создавать новые локальные адреса' : 'Создавать новые постоянные ящики'}</label>
        <p className="meta">{newEmails ? (fixtureOnly ? 'Новые логины @example.invalid для встроенного сайта. Почтовых ящиков за ними нет.' : 'Именные адреса на вашем домене с отдельными паролями и проверкой входа.') : `Возьмём свободные ${fixtureOnly ? 'адреса' : 'проверенные ящики'} из базы. Сейчас доступно ${free}.`}</p>
        {!fixtureOnly && newEmails && !mail?.configured && <p className="setup-required">Сначала подключите сервер на вкладке «Почта».</p>}
        {!newEmails && countValid && Number(count) > free && <p className="setup-required">Не хватает {Number(count) - free} адресов. Добавьте их в базу.</p>}
        <p className="meta">Город и устройство назначатся автоматически. После создания запустите ботов в соседнем блоке.</p>
        <div className="toolbar"><button className="btn secondary" disabled={busy || active || !countValid || (!newEmails && Number(count) > free) || (!fixtureOnly && (mail?.running || (newEmails && !mail?.configured)))}><Busy busy={busy && ['batch', 'mail/create', 'mail/assign'].includes(operation)} busyText="Создание…"><Plus size={15} aria-hidden="true" />Создать новых ботов</Busy></button></div>
      </form>
    </div>
    {fixtureOnly && <Disclosure title="Добавить свою базу адресов" caption={`Свободно ${free}`}>
      <p className="meta">По одному адресу на строку. Адреса станут логинами локального сайта. Писем и запросов к почтовым провайдерам не будет.</p>
      <form className="form compact-form" onSubmit={importAddresses}>
        <Field label="Адреса почты для базы"><textarea rows="4" value={addresses} onChange={e => setAddresses(e.target.value)} placeholder={'reader-one@example.invalid\nreader-two@example.invalid'} required /></Field>
        <div className="toolbar"><button className="btn secondary" disabled={busy || active || !addresses.trim()}><Busy busy={busy && operation === 'mail-pool/import'} busyText="Добавление…">Добавить адреса в базу</Busy></button></div>
      </form>
    </Disclosure>}
  </div>;
}
