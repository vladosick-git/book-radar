import React from 'react';
import { api } from './api';
import { Notice, Busy, PageHeader } from './components';
import PermanentMail from './PermanentMail';

export default function RealMail({ state, refresh }) {
  const [data, setData] = React.useState(null), [busy, setBusy] = React.useState(false), [operation, setOperation] = React.useState('');
  const [error, setError] = React.useState(''), [ids, setIds] = React.useState([]);
  const load = React.useCallback(async () => { setData(await api('/mail/permanent/state')); }, []);
  React.useEffect(() => { load().catch(err => setError(err.message)); const timer = setInterval(() => load().catch(() => {}), 2500); return () => clearInterval(timer); }, [load]);
  async function update() { await load(); await refresh(); }
  async function perform(path, body) {
    setBusy(true); setOperation(path); setError('');
    try { const result = await api(`/mail/permanent/${path.replace(/^mail\//, '')}`, body); await update(); return result; }
    catch (err) { setError(err.message); return null; } finally { setBusy(false); setOperation(''); }
  }
  const available = state.profiles.filter(p => !data?.accounts.some(m => m.profile_id === p.id));
  const selected = ids.filter(id => available.some(p => p.id === id));
  return <>
    <PageHeader title="Постоянная почта" actions={<a className="btn secondary" href="?page=bots">К ботам и входу AT</a>}>Настоящие почтовые ящики и подготовка регистрации AT</PageHeader>
    {error && <Notice tone="danger">{error}</Notice>}
    <section className="block"><h2>Создать почту выбранным ботам</h2><p className="meta">До 10 отдельных постоянных ящиков за одну серию. Нужен подключённый почтовый сервис; подтверждённые ящики появятся в карточках ботов.</p>
      <fieldset className="checklist"><legend>Боты без постоянной почты</legend>{available.map(p => <label className="check" key={p.id}><input type="checkbox" checked={selected.includes(p.id)} disabled={busy || (!selected.includes(p.id) && selected.length >= 10)} onChange={e => setIds(prev => e.target.checked ? [...prev, p.id] : prev.filter(id => id !== p.id))} />{p.name}</label>)}</fieldset>
      <button className="btn primary" disabled={busy || data?.running || !data?.configured || !selected.length} onClick={() => perform('mail/create', { profile_ids: selected })}><Busy busy={busy && operation === 'mail/create'} busyText="Запуск…">Создать ящики: {selected.length}</Busy></button>
      {!data?.configured && <p className="meta">Сначала подключите домен ниже или добавьте готовую почту. Заготовленные логины не считаются созданными ящиками.</p>}
    </section>
    <PermanentMail data={data} profiles={state.profiles} busy={busy} operation={operation} perform={perform} refresh={update} endpoint="/mail/permanent" realProfiles />
  </>;
}
