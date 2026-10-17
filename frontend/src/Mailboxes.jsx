import React from 'react';
import { Plus, Square, KeyRound, Send, ChevronDown, ExternalLink, Mail, RefreshCw, Link } from 'lucide-react';
import { Empty, Modal, PageHeader, Field, Notice, Reveal, Busy, useModalClose, useToast } from './components';
import { Status } from './status';
import { api, usePref, useFlash } from './api';
import { emailServices } from './emailServices';
import MailSetup from './MailSetup';

const regions = { eu: 'Европа · mail.zoho.eu', us: 'США · mail.zoho.com', in: 'Индия · mail.zoho.in' };
const STAGES = ['Профиль', 'Адрес подготовлен', 'Запрос в Zoho', 'Результат'];
const stageOf = { prepared: [2, 'done'], creating: [3, 'active'], created: [4, 'success'], attached: [4, 'done'], failed: [4, 'danger'], unknown: [4, 'warning'] };
const runnable = row => ['prepared', 'failed'].includes(row.status);

function Track({ status }) {
  const [reached, tone] = stageOf[status] || [1, 'done'];
  return <ol className={`track tone-${tone}`} aria-label={`Этап ${reached} из 4`}>{STAGES.map((label, i) =>
    <li key={label} className={i + 1 < reached ? 'past' : i + 1 === reached ? 'now' : ''} title={label}><span className="visually-hidden">{label}</span></li>)}</ol>;
}

function PrepareForm({ state, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const [ids, setIds] = React.useState([]), [domain, setDomain] = React.useState(''), [zoid, setZoid] = React.useState(''), [region, setRegion] = React.useState('eu'), [prefix, setPrefix] = React.useState('reader');
  const [touched, setTouched] = React.useState(false), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const errors = {
    domain: !/^[a-z0-9.-]+\.[a-z0-9-]{2,}$/i.test(domain.trim()) && 'Домен без http:// и символа @, например example.org.',
    zoid: !/^\d{1,30}$/.test(zoid.trim()) && 'Только цифры — номер организации из панели администратора Zoho.',
    prefix: !/^[a-zA-Z][a-zA-Z0-9]{0,29}$/.test(prefix.trim()) && 'Латинские буквы и цифры, первая — буква.',
  };
  async function submit(event) {
    event.preventDefault(); setTouched(true); if (Object.values(errors).some(Boolean) || !ids.length) return; setSaving(true); setError('');
    try { const r = await api('/mailboxes/prepare', { profile_ids: ids, domain, prefix, zoid, region }); await refresh(); toast(`Подготовлено адресов: ${r.ids.length}. В Zoho ничего не отправлено.`); requestClose(); }
    catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  const sample = `${prefix.trim().toLowerCase() || 'reader'}01@${domain.trim().toLowerCase() || 'example.org'}`;
  return <form className="form" onSubmit={submit} noValidate>
    <Notice tone="info">Этот шаг только сохраняет адреса и зашифрованные пароли в программе. Запросов в Zoho нет.</Notice>
    <Field label="Домен организации" error={touched && errors.domain}><input value={domain} onChange={e => setDomain(e.target.value)} placeholder="example.org" autoFocus spellCheck={false} /></Field>
    <div className="form-pair">
      <Field label="Номер организации (ZOID)" error={touched && errors.zoid}><input value={zoid} onChange={e => setZoid(e.target.value)} inputMode="numeric" placeholder="Цифры" /></Field>
      <Field label="Регион Zoho"><select value={region} onChange={e => setRegion(e.target.value)}>{Object.entries(regions).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></Field>
    </div>
    <Field label="Префикс адресов" error={touched && errors.prefix} hint={`Номер профиля добавляется к префиксу: ${sample}. Повторная подготовка тех же адресов сохраняет прежние пароли.`}><input value={prefix} onChange={e => setPrefix(e.target.value)} maxLength={30} spellCheck={false} /></Field>
    <fieldset className="checklist"><legend>Профили <span className="meta">выбрано {ids.length}, не больше 10</span></legend>{state.profiles.map(p => { const on = ids.includes(p.id); return <label className="check" key={p.id}>
      <input type="checkbox" checked={on} disabled={!on && ids.length >= 10} onChange={e => setIds(prev => e.target.checked ? [...prev, p.id] : prev.filter(id => id !== p.id))} /><span>{p.name}</span></label>; })}</fieldset>
    {touched && !ids.length && <p className="field-error" role="alert">Выберите хотя бы один профиль.</p>}
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Подготовка…">Подготовить адреса: {ids.length}</Busy></button></div>
  </form>;
}

function RunForm({ rows, refresh, onStarted }) {
  const requestClose = useModalClose(), toast = useToast();
  const [token, setToken] = React.useState(''), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try { await api('/mailboxes/run', { mailbox_ids: rows.map(row => row.id), access_token: token }); setToken(''); onStarted(); await refresh(); toast('Запуск начат. Результат каждого адреса появится в списке.'); requestClose(); }
    catch (err) { setToken(''); setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <p>Будет отправлено запросов: <strong>{rows.length}</strong>, по одному на адрес, в организацию <strong>{rows[0]?.zoid}</strong> ({regions[rows[0]?.region]}).</p>
    <ul className="mail-preview">{rows.map(row => <li key={row.id}>{row.email}</li>)}</ul>
    <Field label="OAuth access token администратора" hint="Нужно право ZohoMail.organization.accounts.CREATE. Токен хранится только в памяти на время запуска и не сохраняется в браузере.">
      <input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} required autoFocus /></Field>
    <Notice tone="warning">При первой ошибке серия останавливается. Если ответ Zoho потеряется, адрес получит статус «Нужно проверить в Zoho» и повтор будет заблокирован.</Notice>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving || !token.trim()}><Busy busy={saving} busyText="Запуск…"><Send size={14} />Отправить запросы</Busy></button></div>
  </form>;
}

function Secret({ secret }) {
  const requestClose = useModalClose(), [show, setShow] = React.useState(false);
  return <div className="form"><p className="meta">Пароль хранится зашифрованным. Закройте окно после использования.</p>
    {secret.password_state === 'unverified' && <Notice tone="warning">Сверка подтвердила существование ящика, но не совпадение пароля. Проверьте доступ вручную в Zoho.</Notice>}
    <Field label="Адрес"><input value={secret.email} readOnly /></Field>
    <Field label="Пароль"><input type={show ? 'text' : 'password'} value={secret.password} readOnly autoComplete="off" /></Field>
    <label className="check"><input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} /><span>Показать символы</span></label>
    <div className="form-actions"><button className="btn secondary" onClick={requestClose}>Закрыть</button></div></div>;
}

const WHY = {
  unknown: 'Повтор запрещён: запрос мог выполниться в Zoho, а ответ не подтвердил результат. Повторная отправка может создать дубль или конфликт. Проверьте адрес в панели администратора Zoho.',
  failed: 'Zoho явно отказал — ящик не создан. После исправления причины адрес можно выбрать снова.',
  created: 'Zoho подтвердил создание: совпали адрес, accountId и статус ящика. Доставка писем отдельно не проверялась.',
  prepared: 'Адрес и зашифрованный пароль сохранены в программе. Запрос в Zoho ещё не отправлялся.',
  attached: 'Готовый ящик привязан владельцем вручную. Программа не проверяла создание, пароль и доставку писем.',
  creating: 'Запрос отправлен, ждём ответа.',
};

function AttachForm({ state, refresh }) {
  const close = useModalClose(), toast = useToast();
  const [profile, setProfile] = React.useState(state.profiles[0]?.id || ''), [provider, setProvider] = React.useState('Zoho');
  const [email, setEmail] = React.useState(''), [password, setPassword] = React.useState(''), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try { await api('/mailboxes/attach', { profile_id: Number(profile), provider, email, ...(password ? { password } : {}) }); setPassword(''); await refresh(); toast('Готовая почта привязана к профилю вручную.'); close(); }
    catch (err) { setPassword(''); setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <Notice tone="info">Подключите уже существующий независимый ящик. Здесь нет запросов регистрации или проверки пароля.</Notice>
    <Field label="Профиль"><select value={profile} onChange={e => setProfile(e.target.value)}>{state.profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
    <Field label="Провайдер"><select value={provider} onChange={e => setProvider(e.target.value)}>{['Yandex', 'Zoho', 'Tuta', 'Proton', 'GMX', 'Rambler', 'Mail.ru', 'OpenMailBox'].map(p => <option key={p}>{p}</option>)}</select></Field>
    <Field label="Почтовый адрес"><input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus autoComplete="off" /></Field>
    <Field label="Пароль — необязательно" hint="Можно оставить пустым и хранить доступ у владельца. Сохранённый пароль шифруется."><input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" maxLength={256} /></Field>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Сохранение…">Подключить готовую почту</Busy></button></div>
  </form>;
}

function ReconcileForm({ row, refresh }) {
  const close = useModalClose(), toast = useToast();
  const [token, setToken] = React.useState(''), [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try {
      const result = await api(`/mailboxes/${row.id}/reconcile`, { access_token: token }); setToken(''); await refresh();
      if (result.outcome === 'confirmed') { toast('Zoho подтвердил активный ящик. Пароль и доставка отдельно не проверялись.'); close(); }
      else setError('Сверка не подтвердила активный ящик. Причина сохранена в списке. Повтор неизвестного запроса по-прежнему заблокирован.');
    } catch (err) { setToken(''); setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit}>
    <p>Проверка адреса <strong>{row.email}</strong> в организации <strong>{row.zoid}</strong>.</p>
    <Notice tone="info">Отправляются только запросы чтения. Отсутствие адреса в ответе не разрешает повторное создание после неопределённого результата.</Notice>
    <Field label="OAuth access token администратора" hint="Нужно право ZohoMail.organization.accounts.READ. Токен не сохраняется."><input type="password" value={token} onChange={e => setToken(e.target.value)} required autoFocus autoComplete="off" /></Field>
    {error && <Notice tone="warning">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close}>Закрыть</button><button className="btn primary" disabled={saving || !token.trim()}><Busy busy={saving} busyText="Сверка…">Проверить в Zoho</Busy></button></div>
  </form>;
}

export default function Mailboxes({ state, refresh, act, busy, go }) {
  const [modal, setModal] = React.useState(null), [selected, setSelected] = React.useState([]), [error, setError] = React.useState('');
  const [terms, setTerms] = usePref('mail.terms', false);
  const [legend, setLegend] = usePref('mail.legend', false);
  const rows = state.mailboxes, running = state.mail_running;
  const flash = useFlash(rows, r => r.id, r => r.status);
  const chosen = rows.filter(row => selected.includes(row.id) && row.source !== 'manual' && runnable(row));
  const accounts = new Map(state.accounts.map(a => [a.id, a.profile_name]));
  const count = s => rows.filter(r => r.status === s).length;
  if (state.server?.mail_enabled === false) return <><PageHeader title="Почта">Ящики Zoho Mail на собственном домене</PageHeader>
    <Notice tone="info">В серверном режиме раздел отключён: подготовка адресов и пароли остаются в локальной версии на Windows. Серверная часть отвечает только за сбор рекламы.</Notice></>;
  async function reveal(row) { try { setError(''); setModal({ secret: await api(`/mailboxes/${row.id}/credentials`, {}) }); } catch (err) { setError(err.message); } }
  const actions = <><button className="btn secondary" disabled={!state.profiles.length || running} onClick={() => setModal({ prepare: true })}><Plus size={15} />Адреса Zoho</button>
    <button className="btn secondary" disabled={!state.profiles.length || running} onClick={() => setModal({ attach: true })}><Link size={15} />Готовая почта</button>
    {running ? <button className="btn danger-ghost" disabled={busy || state.mail_operation !== 'create'} onClick={() => act('/mailboxes/stop', {}, 'Запуск остановлен.')}><Square size={14} />Остановить</button>
      : <button className="btn primary" disabled={!chosen.length} onClick={() => setModal({ run: true })}><Send size={15} />Создать в Zoho{chosen.length ? `: ${chosen.length}` : ''}</button>}</>;
  return <>
    <PageHeader title="Почта" actions={actions}>Почта владельца и подключение адресов к профилям</PageHeader>
    <section className="block"><div className="block-head"><h2>Готовая почта или ручная регистрация</h2></div><p>Добавьте существующий адрес или откройте регистрацию Яндекса вручную. Автоматической регистрации бесплатных ящиков здесь нет. Zoho требует настроенную организацию, домен и OAuth-доступ.</p>
      <button className="btn primary" disabled={!state.profiles.length || running} onClick={() => setModal({ setup: true })}>Почта → подготовка AT</button>
      {!state.profiles.length && <p className="meta">Сначала создайте профиль в разделе «Профили».</p>}
    </section>
    <ol className="flow" aria-label="Последовательность">{[['Профиль', `${state.profiles.length} в программе`], ['Адрес подготовлен', `${count('prepared')} локально`], ['Запрос в Zoho', running ? 'выполняется' : `${count('failed')} отклонено`], ['Результат', `${count('created')} создано · ${count('unknown')} проверить`]].map(([title, note], i) =>
      <li key={title}><span className="flow-n">{i + 1}</span><span><strong>{title}</strong><small>{note}</small></span></li>)}</ol>
    {error && <Notice tone="danger" onClose={() => setError('')}>{error}</Notice>}
    <section className="block"><div className="block-head"><h2>Рабочий список</h2><span className="meta">{rows.length ? 'Выбрать можно подготовленные адреса и адреса с явным отказом' : ''}</span></div>
      {rows.length ? <ul className="list">{rows.map(row => <li key={row.id} className={`row mailbox${flash.has(row.id) ? ' flash' : ''}`}><div className="row-main">
        <label className="row-check"><input type="checkbox" aria-label={`Выбрать ${row.email}`} checked={selected.includes(row.id)} disabled={running || !runnable(row)} onChange={e => setSelected(prev => e.target.checked ? [...prev, row.id] : prev.filter(id => id !== row.id))} /></label>
        <div className="row-title"><strong className="truncate">{row.email}</strong><span className="meta">{accounts.get(row.account_id)} · {row.provider || 'Zoho'}{row.source !== 'manual' && <> · {regions[row.region]} · ZOID {row.zoid}</>}</span></div>
        <div className="row-track"><Track status={row.status} /></div>
        <div className="row-status"><Status kind="mailbox" value={row.status} flash={flash.has(row.id)} />{row.remote_account_id && <span className="meta">ID {row.remote_account_id}</span>}</div>
        <div className="row-actions">{row.source !== 'manual' && ['unknown', 'failed'].includes(row.status) && <button className="btn ghost sm" disabled={running} onClick={() => setModal({ reconcile: row })}><RefreshCw size={13} />Сверить</button>}<button className="btn ghost sm" disabled={row.password_state === 'not_stored'} onClick={() => reveal(row)}><KeyRound size={13} />Пароль</button></div>
      </div>
        {(row.status === 'unknown' || row.status === 'failed') && <p className={`row-error${row.status === 'unknown' ? ' warn' : ''}`}>{row.error_code && <strong>{row.error_code}: </strong>}{row.error && <>{row.error} </>}<span className="hint-inline">{WHY[row.status]}</span></p>}
        {row.password_state === 'unverified' && <p className="row-error warn">Ящик найден в Zoho. Совпадение сохранённого пароля не проверено.</p>}
      </li>)}</ul>
        : <Empty compact icon={Mail} title="Почта ещё не подключена" action={state.profiles.length ? <button className="btn secondary" onClick={() => setModal({ setup: true })}>Почта → подготовка AT</button> : <button className="btn secondary" onClick={() => go('profiles')}>Создать профили</button>}>Начните с готового ящика владельца. Организация Zoho нужна только для отдельного сценария через её API.</Empty>}
      <button className="disclosure legend-toggle" aria-expanded={legend} aria-controls="mail-legend" onClick={() => setLegend(!legend)}><ChevronDown size={15} /><span>Что означают статусы</span></button>
      <Reveal open={legend} id="mail-legend"><div className="legend">{Object.entries(WHY).filter(([k]) => k !== 'creating').map(([k, text]) => <p key={k}><Status kind="mailbox" value={k} /><span className="meta">{text}</span></p>)}</div></Reveal>
    </section>
    <section className="block">
      <button className="disclosure" aria-expanded={terms} aria-controls="mail-terms" onClick={() => setTerms(!terms)}><ChevronDown size={16} /><span><strong>Условия почтовых сервисов из списка клиента</strong><small>Проверено 14 октября 2026. Справочно: эти сервисы программа не регистрирует.</small></span></button>
      <Reveal open={terms} id="mail-terms"><ul className="terms">{emailServices.map(s => <li key={s.name}><div><strong>{s.name}</strong><span className="service-note">{s.verdict}</span></div><p className="meta">{s.note}</p><a href={s.source} target="_blank" rel="noreferrer" className="inline-link">Источник<ExternalLink size={12} /></a></li>)}</ul></Reveal>
    </section>
    {modal?.prepare && <Modal title="Подготовить адреса Zoho" close={() => setModal(null)}><PrepareForm state={state} refresh={refresh} /></Modal>}
    {modal?.run && <Modal title="Создать ящики в Zoho" close={() => setModal(null)}><RunForm rows={chosen} refresh={refresh} onStarted={() => setSelected([])} /></Modal>}
    {modal?.secret && <Modal size="sm" title="Доступ к почте" close={() => setModal(null)}><Secret secret={modal.secret} /></Modal>}
    {modal?.attach && <Modal title="Подключить готовый ящик" close={() => setModal(null)}><AttachForm state={state} refresh={refresh} /></Modal>}
    {modal?.reconcile && <Modal title="Сверить ящик с Zoho" close={() => setModal(null)}><ReconcileForm row={modal.reconcile} refresh={refresh} /></Modal>}
    {modal?.setup && <Modal title="Почта и подготовка AT" close={() => setModal(null)}><MailSetup state={state} refresh={refresh} /></Modal>}
  </>;
}
