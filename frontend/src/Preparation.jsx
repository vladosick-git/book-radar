import React from 'react';
import { Plus, ExternalLink, Pencil, X, Link2, ClipboardList } from 'lucide-react';
import { Empty, Modal, PageHeader, Select, Field, Notice, Busy, useModalClose, useToast } from './components';
import { Status, STATUS } from './status';
import { api, host, safeLink, stamp, usePref, useFlash } from './api';
import { emailServices } from './emailServices';
import ATOnboarding, { isAT } from './ATOnboarding';

const kinds = { email: 'Почта', site: 'Площадка' };
function adviceFor(service) { try { return emailServices.find(example => new URL(example.url).hostname === new URL(service.registration_url).hostname); } catch { return null; } }
const validUrl = value => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password; } catch { return false; } };

function ServiceForm({ service, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [name, setName] = React.useState(service?.name || ''), [kind, setKind] = React.useState(service?.kind || 'email'), [url, setUrl] = React.useState(service?.registration_url || '');
  const [touched, setTouched] = React.useState(false), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const example = emailServices.find(item => item.url === url);
  const errors = { name: !name.trim() && 'Укажите название.', url: !validUrl(url.trim()) && 'Нужен полный адрес http:// или https:// без логина и пароля.' };
  async function submit(event) {
    event.preventDefault(); setTouched(true); if (errors.name || errors.url) return; setSaving(true); setError('');
    try { await api(service ? `/services/${service.id}` : '/services', { name, kind, registration_url: url }); await refresh(); toast(service ? 'Сервис обновлён.' : 'Сервис добавлен.'); requestClose(); }
    catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit} noValidate>
    {!service && <Field label="Шаблон из списка клиента" optional><select defaultValue="" onChange={e => { const c = emailServices.find(i => i.name === e.target.value); if (c) { setName(c.name); setKind('email'); setUrl(c.url); } }}>
      <option value="">Заполнить вручную</option>{emailServices.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></Field>}
    <Field label="Название" error={touched && errors.name}><input value={name} onChange={e => setName(e.target.value)} maxLength={80} placeholder="Название почты или площадки" autoFocus /></Field>
    <Field label="Тип"><select value={kind} onChange={e => setKind(e.target.value)}><option value="email">Почта</option><option value="site">Площадка</option></select></Field>
    <Field label="Адрес регистрации" error={touched && errors.url} hint="Эта страница откроется в окне выбранного профиля для ручной регистрации."><input value={url} onChange={e => setUrl(e.target.value)} type="url" maxLength={2048} placeholder="https://…" spellCheck={false} /></Field>
    {example && <Notice tone="warning"><strong>{example.verdict}.</strong> {example.note} <a href={example.source} target="_blank" rel="noreferrer">Условия сервиса</a></Notice>}
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Сохранение…">{service ? 'Сохранить сервис' : 'Добавить сервис'}</Busy></button></div>
  </form>;
}

function Checklist({ legend, items, chosen, setChosen, limit, render }) {
  return <fieldset className="checklist"><legend>{legend} <span className="meta">выбрано {chosen.length} из {limit} возможных</span></legend>
    {items.map(item => { const on = chosen.includes(item.id); return <label className="check" key={item.id}>
      <input type="checkbox" checked={on} disabled={!on && chosen.length >= limit} onChange={e => setChosen(prev => e.target.checked ? [...prev, item.id] : prev.filter(id => id !== item.id))} />{render(item)}</label>; })}</fieldset>;
}

function AssignForm({ state, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [profiles, setProfiles] = React.useState([]), [services, setServices] = React.useState([]), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try { const r = await api('/accounts/assign', { profile_ids: profiles, service_ids: services }); await refresh(); toast(`Добавлено записей: ${r.created}. Уже существовало: ${r.existing}.`); requestClose(); }
    catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <Checklist legend="Профили" items={state.profiles} chosen={profiles} setChosen={setProfiles} limit={10} render={p => <span>{p.name}</span>} />
    <Checklist legend="Сервисы" items={state.services} chosen={services} setChosen={setServices} limit={20} render={s => <span>{s.name}<small>{kinds[s.kind]}</small></span>} />
    <p className="preview-line">Будет до <strong>{profiles.length * services.length}</strong> записей. Уже заполненные записи не меняются.</p>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving || !profiles.length || !services.length}><Busy busy={saving} busyText="Сохранение…">Назначить</Busy></button></div>
  </form>;
}

function AccountForm({ account, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [status, setStatus] = React.useState(account.status), [identifier, setIdentifier] = React.useState(account.identifier), [note, setNote] = React.useState(account.note);
  const [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const needId = status === 'confirmed' && !identifier.trim();
  async function submit(event) {
    event.preventDefault(); if (needId) return; setSaving(true); setError('');
    try { await api(`/accounts/${account.id}`, { status, identifier, note }); await refresh(); toast('Этап сохранён.'); requestClose(); } catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit} noValidate>
    <Field label="Этап"><select value={status} onChange={e => setStatus(e.target.value)}>{Object.entries(STATUS.account).map(([value, [label]]) => <option key={value} value={value}>{label}</option>)}</select></Field>
    <Field label="Почта или логин" error={needId && 'Для ручного подтверждения укажите почту или логин.'}><input value={identifier} onChange={e => setIdentifier(e.target.value)} maxLength={160} autoFocus /></Field>
    <Field label="Заметка" optional><textarea value={note} onChange={e => setNote(e.target.value)} maxLength={1000} rows={3} placeholder="Например: требуется подтверждение почты" /></Field>
    <Notice tone="info">Подтверждайте только после успешной ручной регистрации: программа не проверяет аккаунт у сервиса. Пароли и коды сюда не записывайте.</Notice>
    {(account.opened_at || account.confirmed_at) && <p className="meta">{account.opened_at && `Окно открывали: ${stamp(account.opened_at)}. `}{account.confirmed_at && `Подтверждено вручную: ${stamp(account.confirmed_at)}.`}</p>}
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Сохранение…">Сохранить этап</Busy></button></div>
  </form>;
}

export default function Preparation({ state, refresh, act, busy, go }) {
  const [modal, setModal] = React.useState(null);
  const [profileFilter, setProfileFilter] = usePref('prep.profile', '');
  const [statusFilter, setStatusFilter] = usePref('prep.status', '');
  const profiles = new Map(state.profiles.map(p => [p.id, p]));
  const flash = useFlash(state.accounts, a => a.id, a => `${a.status}|${a.identifier}`);
  const accounts = state.accounts.filter(a => (!profileFilter || String(a.profile_id) === String(profileFilter)) && (!statusFilter || a.status === statusFilter));
  const byStatus = s => state.accounts.filter(a => a.status === s).length;
  const editing = modal?.account && state.accounts.find(a => a.id === modal.account);
  const pilot = modal?.pilot && state.accounts.find(a => a.id === modal.pilot);
  async function prepareAT() {
    const result = await act('/onboarding/at/pilot', {}, 'Локальный профиль AT подготовлен. Аккаунт на сайте ещё не создан.');
    if (result) setModal({ pilot: result.account_id });
  }
  async function open(account) {
    const r = await act(`/accounts/${account.id}/open`, {}, r => r.loaded ? 'Страница открыта в окне профиля. Завершите регистрацию вручную и сохраните этап.' : 'Окно открыто, но страница не загрузилась. Регистрация не подтверждена.');
    return r;
  }
  const actions = <><button className="btn secondary" onClick={() => setModal({ service: true })}><Plus size={15} />Добавить сервис</button>
    <button className="btn primary" disabled={!state.profiles.length || !state.services.length} title={!state.services.length ? 'Сначала добавьте сервис' : undefined} onClick={() => setModal({ assign: true })}><Link2 size={15} />Назначить профилям</button></>;
  return <>
    <PageHeader title="Подготовка" actions={actions}>Сервисы и этапы ручной регистрации по профилям</PageHeader>
    <section className="block"><div className="block-head"><h2>Первый вход на Author.Today</h2></div><p>Начните с одного профиля: заполните регистрацию или вход и проверьте, что сессия сохраняется после перезапуска браузера.</p><button className="btn primary" disabled={busy || state.server?.windows_enabled === false} onClick={prepareAT}>Подготовить первый профиль AT</button></section>
    <section className="block"><div className="block-head"><h2>Сервисы</h2><span className="meta">{state.services.length}</span></div>
      {state.services.length ? <ul className="service-grid">{state.services.map(service => { const advice = adviceFor(service); return <li className="service" key={service.id}>
        <div className="service-top"><strong>{service.name}</strong><button className="icon-button" aria-label={`Изменить сервис ${service.name}`} onClick={() => setModal({ service: service })}><Pencil size={14} /></button></div>
        <span className="meta truncate">{kinds[service.kind]} · <a href={safeLink(service.registration_url) || undefined} target="_blank" rel="noreferrer">{host(service.registration_url)}</a></span>
        {advice && <span className="service-note">{advice.verdict}</span>}</li>; })}</ul>
        : <Empty compact title="Сервисов пока нет" action={<button className="btn secondary" onClick={() => setModal({ service: true })}><Plus size={15} />Добавить сервис</button>}>Нужен адрес регистрации почты или площадки.</Empty>}
    </section>
    <section className="block"><div className="block-head"><h2>Учёт регистрации</h2><span className="meta">{Object.entries(STATUS.account).map(([key, [label]]) => `${label.toLowerCase()}: ${byStatus(key)}`).join(' · ')}</span></div>
      {!state.accounts.length
        ? <Empty compact icon={ClipboardList} title="Записей пока нет">Назначьте сервисы профилям. Это создаёт записи учёта, а не аккаунты на сайтах.</Empty>
        : <>
          <div className="toolbar">
            <Select label="Профиль" value={profileFilter} onChange={setProfileFilter} options={[['', 'Все профили'], ...state.profiles.map(p => [String(p.id), p.name])]} />
            <Select label="Этап" value={statusFilter} onChange={setStatusFilter} options={[['', 'Все этапы'], ...Object.entries(STATUS.account).map(([k, [l]]) => [k, l])]} />
          </div>
          {accounts.length ? <ul className="list">{accounts.map(a => { const p = profiles.get(a.profile_id); return <li key={a.id} className={`row account${flash.has(a.id) ? ' flash' : ''}`}><div className="row-main">
            <div className="row-title"><strong>{a.profile_name}</strong><span className="meta">{a.service_name} · {kinds[a.service_kind]}</span></div>
            <div className="row-ident"><span className={a.identifier ? '' : 'meta'}>{a.identifier || 'логин не указан'}</span>{a.note && <span className="meta clamp-2">{a.note}</span>}</div>
            <div className="row-status"><Status kind="account" value={a.status} flash={flash.has(a.id)} /></div>
            <div className="row-actions">
              {p?.open ? <button className="btn secondary sm" disabled={busy} onClick={() => act(`/profiles/${a.profile_id}/close`, {}, 'Окно профиля закрыто.')}><X size={13} />Закрыть окно</button>
                : <button className="btn secondary sm" disabled={busy || p?.collecting || state.server?.windows_enabled === false} title={state.server?.windows_enabled === false ? 'На сервере нет экрана: регистрация выполняется в локальной версии' : p?.collecting ? 'Профиль сейчас выполняет сбор' : undefined} onClick={() => open(a)}><ExternalLink size={13} />Открыть регистрацию</button>}
              <button className="btn ghost sm" onClick={() => setModal({ account: a.id })}><Pencil size={13} />Этап</button>
              {isAT(a) && <button className="btn secondary sm" disabled={busy || p?.collecting || state.server?.windows_enabled === false} onClick={() => setModal({ pilot: a.id })}>Регистрация и вход AT</button>}
            </div></div></li>; })}</ul>
            : <Empty compact title="Нет записей с такими фильтрами" action={<button className="btn secondary" onClick={() => { setProfileFilter(''); setStatusFilter(''); }}>Сбросить фильтры</button>} />}
        </>}
    </section>
    {modal?.service && <Modal title={modal.service === true ? 'Добавить сервис' : 'Изменить сервис'} close={() => setModal(null)}><ServiceForm service={modal.service === true ? null : modal.service} refresh={refresh} /></Modal>}
    {modal?.assign && <Modal title="Назначить сервисы профилям" close={() => setModal(null)}><AssignForm state={state} refresh={refresh} /></Modal>}
    {editing && <Modal title={editing.service_name} subtitle={editing.profile_name} close={() => setModal(null)}><AccountForm account={editing} refresh={refresh} /></Modal>}
    {pilot && <Modal title="Регистрация и вход AT" subtitle={pilot.profile_name} close={() => setModal(null)}><ATOnboarding key={pilot.id} account={pilot} refresh={refresh} /></Modal>}
  </>;
}
