import React from 'react';
import CollectionSetup from './CollectionSetup';
import { Users, Plus, Play, Square, ExternalLink, X, Settings2, ScrollText, CalendarClock } from 'lucide-react';
import { api, usePref, shortStamp, scheduleText } from './api';
import { Busy, Empty, Field, Modal, Notice, SearchInput, Select, useModalClose, useToast } from './components';
import { Status } from './status';
import { SessionPanel } from './Session';
import ATOnboarding, { isAT } from './ATOnboarding';
import { JobForm, ScheduleForm } from './Jobs';
import { botState, planBotJobs, executeBotJobs } from './botOperations';
import './Bots.css';

const STATES = { running: 'Выполняет задание', queued: 'В очереди', open: 'Открыт браузер', expired: 'Нужно войти заново', ready: 'Есть задания', no_jobs: 'Нет заданий' };
const PROVIDERS = ['Yandex', 'Mail.ru', 'Zoho', 'Tuta', 'Proton', 'GMX', 'Rambler', 'OpenMailBox'];
const siteAccounts = (state, id) => state.accounts.filter(account => account.profile_id === id && account.service_kind === 'site');
const addedMail = row => row.source === 'manual' || row.status === 'created';
function BotSession({ profile }) {
  const check = profile.at_session;
  if (!check) return <Status kind="session" value={profile.session_state || 'empty'} />;
  const status = ['signed_in', 'session_verified'].includes(check.status) ? 'active' : check.status === 'signed_out' ? 'expired' : 'unknown';
  return <span>AT: <Status kind="session" value={status} title={check.checked_at ? `Проверено ${shortStamp(check.checked_at)}` : 'Повторите проверку AT'} /></span>;
}

function CreateBots({ refresh, created }) {
  const close = useModalClose(), toast = useToast();
  const [name, setName] = React.useState('Бот'), [count, setCount] = React.useState(1), [start, setStart] = React.useState(1);
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
  const locked = React.useRef(false);
  const valid = name.trim() && Number.isInteger(count) && count >= 1 && count <= 10 && Number.isInteger(start) && start >= 1 && start <= 9999;
  async function submit(event) {
    event.preventDefault(); if (!valid || locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try {
      const result = count === 1 ? await api('/profiles', { name: name.trim() }) : await api('/profiles/batch', { prefix: name.trim(), count, start });
      const ids = result.ids || [result.id];
      created(ids); await refresh(); toast(`Создано ботов: ${ids.length}. Подключите почту, площадку и задание.`); close();
    } catch (err) { setError(err.message); }
    finally { locked.current = false; setBusy(false); }
  }
  return <form className="form" onSubmit={submit}>
    <p className="meta">У каждого бота будет отдельная сессия браузера. Входы на сайты подключаются после создания.</p>
    <Field label={count > 1 ? 'Название серии ботов' : 'Имя бота'}><input required value={name} onChange={e => setName(e.target.value)} maxLength={65} autoFocus disabled={busy} /></Field>
    <div className="form-pair"><Field label="Количество ботов" hint="От 1 до 10 за одну серию"><input type="number" min={1} max={10} value={count} onChange={e => setCount(Number(e.target.value))} disabled={busy} /></Field>
      {count > 1 && <Field label="Первый номер"><input type="number" min={1} max={9999} value={start} onChange={e => setStart(Number(e.target.value))} disabled={busy} /></Field>}</div>
    {count > 1 && <p className="meta">Имена: {name.trim()} {String(start).padStart(2, '0')} … {name.trim()} {String(start + count - 1).padStart(2, '0')}.</p>}
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close} disabled={busy}>Отмена</button><button className="btn primary" disabled={busy || !valid}><Busy busy={busy} busyText="Создание…">Создать ботов</Busy></button></div>
  </form>;
}

function BotMail({ profile, state, refresh }) {
  const toast = useToast();
  const [email, setEmail] = React.useState(''), [provider, setProvider] = React.useState('Yandex'), [password, setPassword] = React.useState('');
  const [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false), [secret, setSecret] = React.useState(null);
  const mailboxes = state.mailboxes.filter(row => row.profile_id === profile.id);
  const local = state.server?.mode !== 'server';
  React.useEffect(() => { if (!secret) return undefined; const timer = setTimeout(() => setSecret(null), 60000); return () => clearTimeout(timer); }, [secret]);
  async function attach(event) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try { await api('/mailboxes/attach', { profile_id: profile.id, provider, email, ...(password ? { password } : {}) }); setEmail(''); await refresh(); toast('Готовая почта подключена. Доставка писем отдельно не проверялась.'); }
    catch (err) { setError(err.message); } finally { setBusy(false); setPassword(''); }
  }
  async function reveal(id) {
    setBusy(true); setError('');
    try { setSecret(await api(`/mailboxes/${id}/credentials`, {})); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  return <section className="bot-detail-section"><h3>Почта бота</h3>
    {mailboxes.map(row => <div className="bot-mailbox" key={row.id}><strong>{row.email}</strong><span className="meta">{row.provider} · запись добавлена {shortStamp(row.created_at)}</span><p className="meta">Состояние: <Status kind="mailbox" value={row.status} />. {row.login_verified ? 'Вход IMAP проверен. Доставка писем отдельно не проверялась.' : 'Вход и доставка писем не проверены.'}</p><button className="btn secondary sm" disabled={busy || row.password_state === 'not_stored'} onClick={() => reveal(row.id)}>Показать сохранённый доступ</button></div>)}
    {secret && <div className="form bot-secret"><Field label="Сохранённый пароль почты"><input readOnly value={secret.password} autoComplete="off" /></Field><button type="button" className="btn secondary sm" onClick={() => setSecret(null)}>Скрыть доступ</button><p className="meta">Автоматически скроется через минуту.</p></div>}
    {local ? <details className="bot-subsection" open={!mailboxes.length || undefined}><summary>{mailboxes.length ? 'Подключить ещё готовый ящик' : 'Подключить готовую почту'}</summary>
      <form className="form" onSubmit={attach}><Field label="Провайдер почты"><select aria-label="Провайдер почты" value={provider} disabled={busy} onChange={e => setProvider(e.target.value)}>{PROVIDERS.map(item => <option key={item}>{item}</option>)}</select></Field>
        <Field label="Почтовый адрес бота"><input type="email" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} autoComplete="off" disabled={busy} /></Field>
        <Field label="Пароль почты" hint="Можно оставить пустым. При сохранении шифруется."><input type="password" maxLength={256} value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" disabled={busy} /></Field>
        <button className="btn secondary" disabled={busy || !email.trim()}><Busy busy={busy} busyText="Подключение…">Подключить почту боту</Busy></button>
      </form></details> : <a className="btn secondary" href="?page=mail">Подключить постоянную почту</a>}
    {error && <Notice tone="danger">{error}</Notice>}
  </section>;
}

function BotAccounts({ profile, state, refresh, browser }) {
  const toast = useToast();
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState(''), [serviceId, setServiceId] = React.useState('');
  const [name, setName] = React.useState(''), [url, setUrl] = React.useState('');
  const accounts = siteAccounts(state, profile.id);
  const available = state.services.filter(service => service.kind === 'site' && !accounts.some(account => account.service_id === service.id));
  const windows = state.server?.windows_enabled !== false;
  async function run(operation) {
    if (busy) return; setBusy(true); setError('');
    try { await operation(); await refresh(); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function assign(id) { await api('/accounts/assign', { profile_ids: [profile.id], service_ids: [Number(id)] }); }
  function prepareAT() {
    run(async () => {
      const match = state.services.find(service => isAT({ ...service, service_kind: service.kind }));
      let id = match?.id;
      if (!id) {
        try { id = (await api('/services', { name: 'Author.Today', kind: 'site', registration_url: 'https://author.today/account/register' })).id; }
        catch (err) {
          const fresh = await api('/state');
          id = fresh.services.find(service => isAT({ ...service, service_kind: service.kind }))?.id;
          if (!id) throw err;
        }
      }
      await assign(id); toast('Author.Today подключён к боту. Войдите в существующий аккаунт или подготовьте регистрацию.');
    });
  }
  function createSite(event) {
    event.preventDefault();
    run(async () => { const result = await api('/services', { name, kind: 'site', registration_url: url }); await assign(result.id); setName(''); setUrl(''); });
  }
  return <>
    <BotMail profile={profile} state={state} refresh={refresh} />
    <section className="bot-detail-section"><h3>Аккаунты на площадках</h3>
      {!accounts.length && <p className="meta">Площадка ещё не подключена. Добавление площадки создаёт запись подготовки; сам аккаунт появляется после завершения входа или регистрации на сайте.</p>}
      {accounts.map(account => <details className="bot-subsection" key={account.id} open={accounts.length === 1 || undefined}><summary><span>{account.service_name}<small>{account.identifier || 'Логин ещё не указан'}</small></span></summary>
        {isAT(account) ? <ATOnboarding account={account} refresh={refresh} initialAction="login" initialEmail={state.mailboxes.filter(row => row.profile_id === profile.id).length === 1 ? state.mailboxes.find(row => row.profile_id === profile.id).email : ''} /> : <div className="form"><Status kind="account" value={account.status} /><p className="meta">{account.registration_url}</p><button className="btn secondary" disabled={busy || profile.collecting || !windows} onClick={() => run(() => browser(`/accounts/${account.id}/open`, {}))}><ExternalLink size={14} />Открыть площадку для входа</button>{windows && <AccountIdentity account={account} refresh={refresh} />}</div>}
      </details>)}
      <div className="bot-inline-actions">{!accounts.some(isAT) && <button className="btn secondary" disabled={busy} onClick={prepareAT}>Подключить Author.Today</button>}
        {!windows && <p className="meta">Вход и подтверждения AT доступны в серверном браузере внутри этой панели.</p>}</div>
      {windows && available.length > 0 && <form className="bot-site-assign" onSubmit={event => { event.preventDefault(); if (serviceId) run(() => assign(serviceId)); }}><Field label="Существующая площадка"><select aria-label="Существующая площадка" value={serviceId} onChange={e => setServiceId(e.target.value)} disabled={busy}><option value="">Выберите площадку</option>{available.map(service => <option key={service.id} value={service.id}>{service.name}</option>)}</select></Field><button className="btn secondary" disabled={!serviceId || busy}>Назначить площадку боту</button></form>}
      {windows && <details className="bot-subsection"><summary>Добавить другую площадку</summary><form className="form" onSubmit={createSite}><Field label="Название площадки"><input required maxLength={80} value={name} onChange={e => setName(e.target.value)} disabled={busy} /></Field><Field label="Адрес страницы входа"><input type="url" required maxLength={2048} value={url} onChange={e => setUrl(e.target.value)} placeholder="https://…" disabled={busy} /></Field><button className="btn secondary" disabled={busy || !name.trim() || !url.trim()}>Добавить площадку боту</button></form></details>}
      {error && <Notice tone="danger">{error}</Notice>}
    </section>
  </>;
}

function AccountIdentity({ account, refresh }) {
  const [identifier, setIdentifier] = React.useState(account.identifier || ''), [note, setNote] = React.useState(account.note || '');
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('');
    try { await api(`/accounts/${account.id}`, { identifier, note, status: account.status }); await refresh(); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  return <form className="form" onSubmit={save}><Field label="Логин на площадке"><input value={identifier} onChange={e => setIdentifier(e.target.value)} maxLength={160} disabled={busy} /></Field><Field label="Заметка об аккаунте"><textarea value={note} onChange={e => setNote(e.target.value)} maxLength={1000} rows={2} disabled={busy} /></Field><button className="btn secondary sm" disabled={busy}>Сохранить данные аккаунта</button>{error && <Notice tone="danger">{error}</Notice>}</form>;
}

function OperationFeedback({ error, result, progress, clearError, clearResult }) {
  return <>
    {error && <Notice tone="danger" onClose={clearError}>{error}</Notice>}
    {progress && <p className="meta" role="status">Команда {progress.index} из {progress.total}: {progress.name}</p>}
    {result && <Notice tone={result.errors.length ? 'warning' : 'info'} onClose={clearResult}><strong>{result.action === 'run' ? 'Поставлено в очередь' : 'Команд остановки принято'}: {result.completed.length}.</strong>{result.errors.length > 0 && <ul>{result.errors.map(item => <li key={item.id}>{item.name}: {item.message}</li>)}</ul>}{result.skipped.length > 0 && <details><summary>Пропущено: {result.skipped.length}</summary><ul>{result.skipped.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}{!result.jobs.length && <p>Нет подходящих заданий. Откройте бота и добавьте задание или проверьте его состояние.</p>}</Notice>}
  </>;
}

function BotPage({ profile, state, browser, busy }) {
  const [url, setUrl] = usePref(`bots.open-url.${profile.id}`, 'https://author.today/reader/224588');
  const [error, setError] = React.useState('');
  const toast = useToast();
  async function open(event) {
    event.preventDefault(); setError('');
    try { const result = await browser(`/profiles/${profile.id}/open`, { url }); if (result) toast('Страница открыта в отдельном браузере этого бота.'); }
    catch (err) { setError(err.message); }
  }
  return <section className="bot-detail-section"><h3>Открыть страницу в сессии бота</h3>
    <form className="form" onSubmit={open}><Field label="Страница для открытия"><input type="url" value={url} onChange={e => setUrl(e.target.value)} required maxLength={2048} disabled={busy} /></Field>
      <div className="bot-inline-actions"><button className="btn secondary" disabled={busy || profile.open || profile.collecting || state.server?.windows_enabled === false}><ExternalLink size={14} />Открыть страницу</button>
        {profile.open && <button type="button" className="btn secondary" disabled={busy} onClick={() => browser(`/profiles/${profile.id}/close`, {}).catch(err => setError(err.message))}><X size={14} />Закрыть окно бота</button>}</div>
      <p className="meta">Используются сохранённые cookies этого профиля. Открытие страницы не запускает автоматическое чтение.</p>
      {error && <Notice tone="danger">{error}</Notice>}
    </form>
  </section>;
}

function BotDetails({ profile, state, refresh, action, browser, jobForm, schedule, go, busy, feedback }) {
  const close = useModalClose(), toast = useToast();
  const [tab, setTab] = React.useState('accounts');
  const jobs = state.jobs.filter(job => job.profile_id === profile.id);
  return <div className="bot-details">
    <div className="bot-detail-summary"><Status kind="session" value={profile.session_state || 'empty'} /><span className="meta">Бот #{profile.id} · {profile.name}</span></div>
    <div className="bot-detail-tabs" role="tablist" aria-label="Управление ботом">{[['accounts', 'Почта и аккаунты'], ['jobs', 'Задания'], ['session', 'Сессия входа']].map(([key, label]) => <button type="button" role="tab" id={`bot-tab-${key}`} aria-controls={`bot-panel-${key}`} aria-selected={tab === key} tabIndex={tab === key ? 0 : -1} key={key} onClick={() => setTab(key)} onKeyDown={event => { const keys = ['accounts', 'jobs', 'session']; if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (keys.indexOf(key) + (event.key === 'ArrowRight' ? 1 : 2)) % 3; setTab(keys[index]); event.currentTarget.parentElement.querySelector(`#bot-tab-${keys[index]}`)?.focus(); } }}>{label}</button>)}</div>
    <div role="tabpanel" id={`bot-panel-${tab}`} aria-labelledby={`bot-tab-${tab}`} key={tab}>
      <OperationFeedback {...feedback} />
      {tab === 'accounts' && <BotAccounts profile={profile} state={state} refresh={refresh} browser={browser} />}
      {tab === 'session' && <><SessionPanel profile={profile} server={state.server} refresh={refresh} toast={toast} /><BotPage profile={profile} state={state} browser={browser} busy={busy} /></>}
      {tab === 'jobs' && <section className="bot-detail-section"><div className="bot-section-title"><h3>Задания бота</h3><button className="btn secondary sm" disabled={busy} onClick={() => jobForm(profile.id)}><Plus size={13} />Добавить задание боту</button></div>
        <p className="meta">Задания выполняет сборщик. Расписание каждого задания настраивается отдельно.</p>
        {!jobs.length && <Empty compact title="Нет заданий" />}
        {jobs.map(job => <div className="bot-job" key={job.id}><strong>{job.name}</strong><span className="meta">{scheduleText(job) || 'Только вручную'}</span><span className="meta">{job.urls.join(', ')}</span>{job.active_run && <span className="meta">Запуск #{job.active_run.id} · {job.active_run.status === 'queued' ? 'в очереди' : 'выполняется'}</span>}<div className="bot-inline-actions"><button className="btn secondary sm" disabled={busy || !!job.active_run || job.status === 'running'} onClick={() => jobForm(profile.id, job)}>Изменить</button><button className="btn secondary sm" disabled={busy} onClick={() => schedule(job)}><CalendarClock size={13} />Расписание</button><button className="btn secondary sm" disabled={busy || profile.open || !!job.active_run || job.status === 'running' || !state.server?.worker_alive} onClick={() => action('run', [profile.id], [job.id])}><Play size={13} />Запустить задание</button>{(job.active_run || job.status === 'running') && <button className="btn secondary sm" disabled={busy} onClick={() => action('stop', [profile.id], [job.id])}><Square size={13} />Остановить задание</button>}</div></div>)}
      </section>}
    </div>
    <div className="bot-detail-footer"><button className="text-button" onClick={() => { close(); go('logs', { 'logs.profile': String(profile.id), 'logs.source': '', 'logs.search': '', 'logs.since': '', 'logs.until': '' }); }}><ScrollText size={14} />Журнал этого бота</button></div>
  </div>;
}

export default function Bots({ state, refresh, busy: appBusy, go }) {
  const [search, setSearch] = usePref('bots.search', ''), [filter, setFilter] = usePref('bots.filter', '');
  const [selectedIds, setSelectedIds] = React.useState([]), [modal, setModal] = React.useState(null);
  const [working, setWorking] = React.useState(false), [error, setError] = React.useState(''), [result, setResult] = React.useState(null), [progress, setProgress] = React.useState(null);
  const locked = React.useRef(false), toast = useToast();
  const busy = appBusy || working;
  const models = state.profiles.map(profile => {
    const jobs = state.jobs.filter(job => job.profile_id === profile.id);
    const accounts = siteAccounts(state, profile.id), mail = state.mailboxes.filter(row => row.profile_id === profile.id);
    return { profile, jobs, accounts, mail, status: botState(profile, jobs) };
  });
  const query = search.trim().toLocaleLowerCase('ru');
  const filtered = models.filter(bot => (!filter || bot.status === filter) && (!query || [bot.profile.name, ...bot.accounts.map(a => a.identifier), ...bot.mail.map(m => m.email)].join(' ').toLocaleLowerCase('ru').includes(query)));
  const selected = selectedIds.filter(id => state.profiles.some(profile => profile.id === id));
  const allSelected = filtered.length > 0 && filtered.every(bot => selected.includes(bot.profile.id));
  const current = modal?.profile && state.profiles.find(profile => profile.id === modal.profile);
  const currentJob = modal?.schedule && state.jobs.find(job => job.id === modal.schedule);
  async function action(kind, ids, jobIds = null) {
    if (locked.current || appBusy) return;
    locked.current = true; setWorking(true); setError(''); setResult(null);
    try {
      const fresh = await api('/state');
      if (kind === 'run' && !fresh.server?.worker_alive) throw new Error('Сборщик сейчас не отвечает. Проверьте его состояние в разделе «Запуски».');
      const plan = planBotJobs(fresh, ids, kind);
      if (jobIds) { const wanted = new Set(jobIds); plan.jobs = plan.jobs.filter(job => wanted.has(job.id)); }
      const outcome = await executeBotJobs(plan, api, setProgress);
      setResult(outcome); await refresh();
      if (outcome.completed.length) toast(kind === 'run' ? `В очередь поставлено заданий: ${outcome.completed.length}.` : `Обработано команд остановки: ${outcome.completed.length}.`);
    } catch (err) { setError(err.message); }
    finally { locked.current = false; setWorking(false); setProgress(null); }
  }
  async function browser(path, body) {
    if (locked.current || appBusy) return;
    locked.current = true; setWorking(true); setError('');
    try { const response = await api(path, body); await refresh(); if (response.loaded === false) throw new Error('Браузер открыт, но страница не загрузилась. Проверьте адрес в его окне.'); return response; }
    catch (err) { setError(err.message); throw err; }
    finally { locked.current = false; setWorking(false); }
  }
  const browserAction = (profile, accounts) => {
    const path = `/profiles/${profile.id}/${profile.open ? 'close' : 'open'}`;
    browser(path, profile.open ? {} : { url: accounts[0]?.registration_url || 'https://author.today/' }).catch(() => {});
  };
  const jobForm = (profileId, job = null) => setModal({ jobForm: profileId, job });
  const feedback = { error, result, progress, clearError: () => setError(''), clearResult: () => setResult(null) };
  return <div className="bots-page">
    <header className="analyst-page-header"><div><div className="analyst-title-line"><h1>Боты</h1><span className="catalog-total">{models.length} в базе</span></div><p className="meta">Браузеры, почта, входы на площадки и задания</p></div><button className="btn primary" disabled={busy} onClick={() => setModal({ create: true })}><Plus size={17} />Создать ботов</button></header>
    <p className="meta">Число профилей не равно числу зарегистрированных аккаунтов. Без почты, площадок и заданий: {models.filter(bot => !bot.mail.length && !bot.accounts.length && !bot.jobs.length).length}. Все существующие записи сохранены.</p>
    <div className="bot-overview" aria-label="Состояние ботов">{[['Всего профилей', models.length], ['Выполняют задания', models.filter(bot => bot.status === 'running').length], ['В очереди', models.filter(bot => bot.status === 'queued').length], ['С добавленной почтой', models.filter(bot => bot.mail.some(addedMail)).length]].map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
    <div className="bot-worker-line"><span className={`collector-state${state.server?.worker_alive ? ' ok' : ''}`}><i />{state.server?.worker_alive ? 'Сборщик отвечает' : 'Сборщик не отвечает'}</span><span className="meta">Одновременно браузеров: до {state.server?.max_browsers || 1}. Остальные задания ждут в очереди.</span></div>
    <OperationFeedback {...feedback} />
    <div className="bot-controls"><SearchInput label="Поиск ботов" placeholder="Имя, почта или логин" value={search} onChange={setSearch} /><Select label="Состояние ботов" value={filter} onChange={setFilter} options={[[ '', 'Все состояния'], ...Object.entries(STATES)]} /></div>
    <div className="bot-batch-bar"><label className="check"><input type="checkbox" aria-label="Выбрать всех видимых ботов" checked={allSelected} disabled={busy || !filtered.length} onChange={() => setSelectedIds(ids => allSelected ? ids.filter(id => !filtered.some(bot => bot.profile.id === id)) : [...new Set([...ids, ...filtered.map(bot => bot.profile.id)])])} />Выбрано: {selected.length}</label><div className="bot-inline-actions"><button className="btn primary sm" disabled={busy || !selected.length || !state.server?.worker_alive} onClick={() => action('run', selected)}><Play size={13} />Запустить выбранных</button><button className="btn secondary sm" disabled={busy || !selected.length} onClick={() => action('stop', selected)}><Square size={13} />Остановить выбранных</button></div></div>
    <p className="meta bot-batch-help">Запуск ставит задания выбранных ботов в очередь. Остановка завершает текущие запуски; отдельные расписания сохраняются.</p>
    <div className="bot-inline-actions bot-setup-actions"><button className="btn secondary" disabled={busy || !selected.length || selected.length > 10} onClick={() => setModal({ collection: true })}>Настроить сбор выбранным</button><button className="btn secondary" onClick={() => go('mail')}>Почта и регистрация</button><span className="meta">Адреса и отдельное задание каждому боту · до 10 за раз</span></div>
    {filtered.length ? <ul className="bot-grid">{filtered.map(({ profile, jobs, accounts, mail, status }) => <li className="bot-card" key={profile.id}>
      <div className="bot-card-heading"><label className="check"><input type="checkbox" aria-label={`Выбрать бота ${profile.name}`} checked={selected.includes(profile.id)} disabled={busy} onChange={() => setSelectedIds(ids => ids.includes(profile.id) ? ids.filter(id => id !== profile.id) : [...ids, profile.id])} /><strong>{profile.name}</strong></label><span className={`bot-state bot-state-${status}`}>{STATES[status]}</span></div>
      <dl className="bot-card-meta"><dt>Почта</dt><dd>{mail.filter(addedMail).map(row => row.email).join(', ') || 'Нет готового адреса'}</dd><dt>Площадки</dt><dd>{accounts.map(account => account.service_name).join(', ') || 'Не подключены'}</dd><dt>Сессия</dt><dd><BotSession profile={profile} /></dd><dt>Задания</dt><dd>{jobs.length ? `${jobs.length} · ${jobs.filter(job => job.active_run).length} в очереди или выполняются` : 'Не добавлены'}</dd></dl>
      <div className="bot-card-actions"><button className="btn secondary sm" disabled={busy} onClick={() => setModal({ profile: profile.id })}><Settings2 size={14} />Управление</button><button className="btn secondary sm" disabled={busy || profile.collecting || state.server?.windows_enabled === false} onClick={() => browserAction(profile, accounts)}>{profile.open ? <X size={14} /> : <ExternalLink size={14} />}{profile.open ? 'Закрыть браузер' : 'Открыть браузер'}</button>{['running', 'queued'].includes(status) ? <button className="btn secondary sm" disabled={busy} onClick={() => action('stop', [profile.id])}><Square size={13} />Остановить</button> : <button className="btn primary sm" disabled={busy || !jobs.length || profile.open || !state.server?.worker_alive} onClick={() => action('run', [profile.id])}><Play size={13} />Запустить</button>}</div>
    </li>)}</ul> : <Empty icon={Users} title={models.length ? 'Боты не найдены' : 'Создайте первого бота'} action={<button className="btn secondary" onClick={() => models.length ? (setSearch(''), setFilter('')) : setModal({ create: true })}>{models.length ? 'Сбросить фильтры' : 'Создать ботов'}</button>}>У каждого бота своя сессия. Почта и вход на площадку подключаются в его карточке.</Empty>}
    {modal?.create && <Modal title="Создать ботов" close={() => setModal(null)}><CreateBots refresh={refresh} created={ids => setSelectedIds(previous => [...new Set([...previous, ...ids])])} /></Modal>}
    {modal?.collection && <Modal title="Настроить сбор рекламы" close={() => setModal(null)}><CollectionSetup ids={selected} state={state} refresh={refresh} /></Modal>}
    {current && <Modal title={`Бот: ${current.name}`} size="lg" close={() => setModal(null)}><BotDetails profile={current} state={state} refresh={refresh} browser={browser} action={action} jobForm={jobForm} schedule={job => setModal({ schedule: job.id })} busy={busy} go={go} feedback={feedback} /></Modal>}
    {modal?.jobForm && <Modal title={modal.job ? 'Изменить задание бота' : 'Новое задание бота'} close={() => setModal(null)}><JobForm state={state} refresh={refresh} presetProfile={modal.jobForm} job={modal.job} /></Modal>}
    {currentJob && <Modal title="Расписание задания бота" subtitle={currentJob.name} close={() => setModal(null)}><ScheduleForm job={currentJob} refresh={refresh} /></Modal>}
  </div>;
}
