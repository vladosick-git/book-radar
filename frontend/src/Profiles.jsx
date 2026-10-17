import React from 'react';
import { Plus, ExternalLink, X, Users, ArrowRight, IdCard, Layers } from 'lucide-react';
import { Empty, Modal, PageHeader, SearchInput, Select, Field, Notice, Busy, useModalClose, useToast } from './components';
import { Status } from './status';
import { api, plural, shortStamp, stamp, usePref, useFlash } from './api';
import { hintFor } from './Jobs';
import { SessionPanel } from './Session';

const profileState = p => p.collecting ? 'collecting' : p.open ? 'open' : 'ready';

function BatchForm({ refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [prefix, setPrefix] = React.useState('Профиль'), [count, setCount] = React.useState(10), [start, setStart] = React.useState(1);
  const [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const label = n => `${prefix.trim()} ${String(n).padStart(2, '0')}`;
  const invalid = !prefix.trim() ? 'Укажите название серии.' : !(count >= 1 && count <= 10) ? 'Количество — от 1 до 10.' : !(start >= 1 && start <= 9999) ? 'Номер — от 1 до 9999.' : '';
  async function submit(event) {
    event.preventDefault(); if (invalid) return; setSaving(true); setError('');
    try { const result = await api('/profiles/batch', { prefix, count: Number(count), start: Number(start) }); await refresh(); toast(`Создано профилей: ${result.ids.length}.`); requestClose(); }
    catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit} noValidate>
    <Field label="Название серии" error={!prefix.trim() && 'Укажите название серии.'}><input value={prefix} onChange={e => setPrefix(e.target.value)} maxLength={65} autoFocus /></Field>
    <div className="form-pair">
      <Field label="Количество" hint="От 1 до 10"><input type="number" value={count} onChange={e => setCount(Number(e.target.value))} min={1} max={10} /></Field>
      <Field label="Начальный номер"><input type="number" value={start} onChange={e => setStart(Number(e.target.value))} min={1} max={9999} /></Field>
    </div>
    {!invalid && <p className="preview-line">Будут созданы: <strong>{label(start)}</strong> … <strong>{label(start + count - 1)}</strong></p>}
    {invalid && <p className="field-error" role="alert">{invalid}</p>}
    <p className="meta">Каждый профиль хранит собственную сессию браузера. Если хотя бы одно название занято, серия не создаётся целиком.</p>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving || Boolean(invalid)}><Busy busy={saving} busyText="Создание…">Создать серию</Busy></button></div>
  </form>;
}

function SingleForm({ refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [name, setName] = React.useState(''), [touched, setTouched] = React.useState(false), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setTouched(true); if (!name.trim()) return; setSaving(true); setError('');
    try { await api('/profiles', { name }); await refresh(); toast(`Профиль «${name.trim()}» создан.`); requestClose(); } catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit} noValidate>
    <Field label="Название" error={touched && !name.trim() && 'Укажите название.'} hint="Например, по назначению: «Основной», «Чистый без входа»."><input value={name} onChange={e => setName(e.target.value)} maxLength={80} autoFocus /></Field>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Сохранение…">Создать профиль</Busy></button></div>
  </form>;
}

function BrowserButton({ profile, act, busy, compact, windows = true }) {
  const cls = `btn secondary${compact ? ' sm' : ''}`;
  if (!windows) return <button className={cls} disabled title="На сервере нет экрана: вход в профиль выполняется в локальной версии"><ExternalLink size={14} />Открыть браузер</button>;
  if (profile.open) return <button className={cls} disabled={busy} onClick={() => act(`/profiles/${profile.id}/close`, {}, 'Окно профиля закрыто, сессия сохранена.')}><X size={14} />Закрыть браузер</button>;
  return <button className={cls} disabled={busy || profile.collecting} title={profile.collecting ? 'Во время сбора окно профиля открыть нельзя' : undefined}
    onClick={() => act(`/profiles/${profile.id}/open`, { url: 'https://author.today/' }, r => r.loaded ? 'Окно профиля открыто. Войдите вручную и закройте окно перед сбором.' : 'Окно открыто, но страница не загрузилась. Введите адрес вручную.')}><ExternalLink size={14} />Открыть браузер</button>;
}

function nextStep(profile, data) {
  if (profile.collecting) return ['Дождитесь окончания сбора или остановите его на панели сверху.', null];
  if (profile.open) return ['Закройте окно браузера, чтобы профиль был доступен для сбора.', null];
  if (data.mailboxes.some(row => row.status === 'unknown')) return ['Проверьте в Zoho адрес с неизвестным результатом.', ['mail', {}]];
  if (!data.jobs.length) return ['Создайте задание для этого профиля.', ['jobs', { 'jobs.createFor': profile.id }]];
  if (data.jobs.some(job => ['error', 'partial', 'interrupted'].includes(job.status))) return ['Разберите ошибки последнего сбора в журнале.', ['logs', { 'logs.level': '', 'logs.profile': String(profile.id) }]];
  if (data.accounts.some(a => a.status !== 'confirmed')) return ['Продолжите ручную регистрацию в «Подготовке».', ['preparation', { 'prep.profile': String(profile.id), 'prep.status': '' }]];
  return ['Профиль готов: запустите задание в разделе «Задания».', ['jobs', { 'jobs.profile': String(profile.id) }]];
}

function ProfileCard({ profile, state, act, busy, go, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const jobs = state.jobs.filter(job => job.profile_id === profile.id);
  const accounts = state.accounts.filter(a => a.profile_id === profile.id);
  const accountIds = new Set(accounts.map(a => a.id));
  const mailboxes = state.mailboxes.filter(row => accountIds.has(row.account_id));
  const findings = profile.findings_count || 0;
  const problems = profile.problems || [];
  const [text, target] = nextStep(profile, { jobs, accounts, mailboxes });
  const jump = (tab, presets) => { requestClose(); go(tab, presets); };
  return <div className="form profile-card">
    <div className="profile-head"><Status kind="profile" value={profileState(profile)} /><span className="meta">Профиль #{profile.id} · создан {stamp(profile.created_at)}</span><BrowserButton profile={profile} act={act} busy={busy} compact windows={state.server?.windows_enabled !== false} /></div>
    <div className="next-step"><span className="eyebrow">Следующий шаг</span><p>{text}</p>{target && <button className="btn secondary sm" onClick={() => jump(...target)}>Перейти<ArrowRight size={14} /></button>}</div>
    <SessionPanel profile={profile} server={state.server} refresh={refresh} toast={toast} />
    <section><h3>Задания <span className="meta">{jobs.length}</span></h3>
      {jobs.length ? <ul className="mini-list">{jobs.map(job => <li key={job.id}><span className="truncate">{job.name}</span><Status kind="job" value={job.status} /></li>)}</ul> : <p className="meta">Заданий нет.</p>}
      {jobs.length > 0 && <button className="text-button" onClick={() => jump('jobs', { 'jobs.profile': String(profile.id), 'jobs.status': '', 'jobs.search': '' })}>Все задания профиля<ArrowRight size={13} /></button>}
    </section>
    <section><h3>Почта и площадки <span className="meta">{accounts.length}</span></h3>
      {accounts.length ? <ul className="mini-list">{accounts.map(a => { const box = mailboxes.find(m => m.account_id === a.id); return <li key={a.id}>
        <span className="truncate">{a.service_name}<small>{a.identifier || 'логин не указан'}</small></span>
        <span className="badges">{box && <Status kind="mailbox" value={box.status} />}<Status kind="account" value={a.status} /></span></li>; })}</ul> : <p className="meta">Сервисы не назначены.</p>}
      {accounts.length > 0 && <button className="text-button" onClick={() => jump('preparation', { 'prep.profile': String(profile.id), 'prep.status': '' })}>Открыть в «Подготовке»<ArrowRight size={13} /></button>}
    </section>
    <section><h3>Последние ошибки и предупреждения</h3>
      {problems.length ? <ul className="mini-list problems">{problems.map(log => <li key={log.id}><Status kind="log" value={log.level} /><span><span className="clamp-2">{log.message}</span>{hintFor(log.message) && <small>{hintFor(log.message)}</small>}<small>{shortStamp(log.created_at)}</small></span></li>)}</ul>
        : <p className="meta">Предупреждений и ошибок в журнале нет.</p>}
    </section>
    <p className="meta">Находок у профиля: {findings}. Окно браузера и сбор одного профиля не работают одновременно.</p>
  </div>;
}

export default function Profiles({ state, refresh, act, busy, go }) {
  const [modal, setModal] = React.useState(null);
  const [search, setSearch] = usePref('profiles.search', '');
  const [filter, setFilter] = usePref('profiles.state', '');
  const flash = useFlash(state.profiles, p => p.id, profileState);
  const counts = React.useMemo(() => {
    const jobs = new Map(), finds = new Map(), accounts = new Map();
    state.jobs.forEach(j => jobs.set(j.profile_id, (jobs.get(j.profile_id) || 0) + 1));
    state.profiles.forEach(p => finds.set(p.id, p.findings_count || 0));
    state.accounts.forEach(a => accounts.set(a.profile_id, (accounts.get(a.profile_id) || 0) + 1));
    return { jobs, finds, accounts };
  }, [state.jobs, state.profiles, state.accounts]);
  const q = search.trim().toLowerCase();
  const profiles = state.profiles.filter(p => (!filter || profileState(p) === filter) && (!q || p.name.toLowerCase().includes(q)));
  const current = modal?.card && state.profiles.find(p => p.id === modal.card);
  const actions = <><button className="btn secondary" onClick={() => setModal({ batch: true })}><Layers size={15} />Создать серию</button><button className="btn primary" onClick={() => setModal({ single: true })}><Plus size={16} />Создать профиль</button></>;
  return <>
    <PageHeader title="Профили браузера" actions={actions}>Отдельные браузеры: у каждого свои cookies и вход</PageHeader>
    {!state.profiles.length
      ? <Empty icon={Users} title="Создайте первый профиль" action={<button className="btn primary" onClick={() => setModal({ single: true })}><Plus size={16} />Создать профиль</button>}>Его сессия хранится отдельно от вашего обычного браузера. Вход на сайты выполняется вручную в окне профиля.</Empty>
      : <>
        <div className="toolbar">
          <SearchInput label="Поиск профилей" value={search} onChange={setSearch} placeholder="Название профиля" />
          <Select label="Состояние" value={filter} onChange={setFilter} options={[['', 'Все состояния'], ['ready', 'Свободен'], ['open', 'Окно открыто'], ['collecting', 'Идёт сбор']]} />
        </div>
        <div className="list-head profiles-cols" aria-hidden="true"><span>Профиль</span><span>Состояние</span><span>Связано</span><span /></div>
        {profiles.length ? <ul className="list">{profiles.map(p => <li key={p.id} className={`row profile${flash.has(p.id) ? ' flash' : ''}`}><div className="row-main">
          <div className="row-title"><strong>{p.name}</strong><span className="meta">#{p.id} · создан {shortStamp(p.created_at)}</span></div>
          <div className="row-status"><Status kind="profile" value={profileState(p)} flash={flash.has(p.id)} />{p.session_state && p.session_state !== 'empty' && <Status kind="session" value={p.session_state} />}</div>
          <div className="row-result meta">{counts.jobs.get(p.id) || 0} {plural(counts.jobs.get(p.id) || 0, 'задание', 'задания', 'заданий')} · {counts.finds.get(p.id) || 0} {plural(counts.finds.get(p.id) || 0, 'находка', 'находки', 'находок')} · {counts.accounts.get(p.id) || 0} {plural(counts.accounts.get(p.id) || 0, 'запись', 'записи', 'записей')}</div>
          <div className="row-actions"><BrowserButton profile={p} act={act} busy={busy} compact windows={state.server?.windows_enabled !== false} /><button className="btn ghost sm" onClick={() => setModal({ card: p.id })}><IdCard size={14} />Карточка</button></div>
        </div></li>)}</ul>
          : <Empty compact title="Нет профилей с такими условиями" action={<button className="btn secondary" onClick={() => { setSearch(''); setFilter(''); }}>Сбросить фильтры</button>} />}
        <p className="footnote">{state.server?.windows_enabled === false ? 'На сервере окно профиля недоступно: сбор идёт с сохранённой сессией профиля или без входа. Сессии профилей хранятся зашифрованными.' : 'Войдите на нужный сайт в окне профиля и закройте окно — cookies сохранятся для сбора. Остальные профили их не получают.'}</p>
      </>}
    {modal?.batch && <Modal title="Создать серию профилей" close={() => setModal(null)}><BatchForm refresh={refresh} /></Modal>}
    {modal?.single && <Modal title="Создать профиль" close={() => setModal(null)}><SingleForm refresh={refresh} /></Modal>}
    {current && <Modal size="lg" title={current.name} subtitle="Карточка профиля" close={() => setModal(null)}><ProfileCard profile={current} state={state} act={act} busy={busy} go={go} refresh={refresh} /></Modal>}
  </>;
}
