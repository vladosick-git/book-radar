import React from 'react';
import { version as UI_VERSION } from '../package.json';
import { Search, Play, Users, ClipboardList, Mail, ScrollText, Square, LoaderCircle, LogOut, BookOpen, Upload, ChevronDown, UserRound } from 'lucide-react';
import Jobs from './AnalystJobs';
import Profiles from './Profiles';
import Findings from './Findings';
import Preparation from './Preparation';
import Mailboxes from './Mailboxes';
import RealMail from './RealMail';
import Logs from './Logs';
import Runs from './AnalystRuns';
import Export from './Export';
import { CatalogSkeleton } from './CatalogUI';
import Login from './Login';
import Purchases from './Purchases';
import Simulation from './Simulation';
import Bots from './Bots';
import { Notice, Skeleton, Toasts, ToastContext } from './components';
import { api, ago, prefs, shortStamp, usePref } from './api';

const NAV = [
  ['bots', 'Боты', Users],
  ['findings', 'Находки', Search], ['jobs', 'Задания', ClipboardList], ['runs', 'Запуски', Play], ['profiles', 'Профили браузера', UserRound], ['logs', 'Журнал', ScrollText], ['export', 'Экспорт', Upload],
];
const EXTRA_NAV = [
  ['preparation', 'Подготовка', ClipboardList], ['mail', 'Почта', Mail], ['mail-legacy', 'Прежний реестр почты', Mail],
  ['purchases', 'Сверка покупок', ClipboardList],
];
const PAGES = { bots: Bots, jobs: Jobs, runs: Runs, findings: Findings, profiles: Profiles, preparation: Preparation, mail: RealMail, 'mail-legacy': Mailboxes, logs: Logs, purchases: Purchases, simulation: Simulation, export: Export };
const EMPTY = { jobs: [], runs: [], profiles: [], catalog: { findings: 0, ads: 0 }, journal: { last_id: 0 }, notifications: { enabled: false }, services: [], accounts: [], mailboxes: [], mail_running: false, server: null };

function Connection({ link }) {
  if (link.ok === null) return <span className="conn"><LoaderCircle size={12} className="spin" />Подключение…</span>;
  return link.ok
    ? <span className="conn ok" title={`Ответ API получен ${shortStamp(link.at)}`}><i /><span className="conn-text">Программа работает</span></span>
    : <span className="conn lost" title="Нет связи с программой"><i /><span className="conn-text">Нет связи</span></span>;
}

function WorkerLine({ server }) {
  if (!server) return null;
  if (server.worker_enabled === false && !server.worker_alive) return <span className="conn"><i /><span className="conn-text">Сборщик выключен</span></span>;
  const latest = server.workers?.[0];
  return server.worker_alive
    ? <span className="conn ok" title={`Последний сигнал ${ago(latest?.heartbeat_at, server.time)}`}><i /><span className="conn-text">Сборщик отвечает</span></span>
    : <span className="conn lost" title={latest ? `Последний сигнал ${ago(latest.heartbeat_at, server.time)}` : 'Сигналов не было'}><i /><span className="conn-text">Сборщик не отвечает</span></span>;
}

function Activity({ state, act, busy }) {
  const running = state.runs.filter(run => run.status === 'running');
  const queued = state.runs.filter(run => run.status === 'queued').length;
  if (!running.length && !state.mail_running) return null;
  return <div className="activity" role="status">
    {running.map(run => <div className="activity-item" key={run.id}>
      <LoaderCircle size={15} className="spin" aria-hidden="true" />
      <span className="activity-text"><strong>Идёт сбор: {run.job_name}</strong><small>Запуск #{run.id} · страниц {run.pages_done} из {run.pages_total} · начат {shortStamp(run.started_at)}{queued ? ` · в очереди ещё ${queued}` : ''}</small></span>
      <button className="btn danger-ghost sm" disabled={busy || run.stop_requested} onClick={() => act(`/jobs/${run.job_id}/stop`, {}, 'Остановка запрошена. Сохранённые находки остаются в базе.')}><Square size={13} />{run.stop_requested ? 'Останавливается…' : 'Остановить'}</button>
    </div>)}
    {state.mail_running && <div className="activity-item">
      <LoaderCircle size={15} className="spin" aria-hidden="true" />
      <span className="activity-text"><strong>{state.mail_operation === 'reconcile' ? 'Идёт сверка ящика с Zoho' : 'Идёт почтовая операция'}</strong><small>{state.mail_operation === 'reconcile' ? 'Чтение без создания аккаунтов' : 'Запросы отправляются по одному'}</small></span>
      <button className="btn danger-ghost sm" disabled={busy || state.mail_operation !== 'create'} onClick={() => act('/mailboxes/stop', {}, 'Запуск Zoho остановлен. Прерванный адрес помечен «Нужно проверить».')}><Square size={13} />Остановить</button>
    </div>}
  </div>;
}

export default function App() {
  const [tab, setTabRaw] = usePref('workspace-page.v2', 'findings');
  React.useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get('page');
    if (PAGES[requested] && requested !== 'simulation') setTabRaw(requested);
  }, [setTabRaw]);
  const [state, setState] = React.useState(EMPTY);
  const [loading, setLoading] = React.useState(true);
  const [link, setLink] = React.useState({ ok: null, at: null });
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [toasts, setToasts] = React.useState([]);
  const busyRef = React.useRef(false);
  const [needLogin, setNeedLogin] = React.useState(false);
  React.useEffect(() => {
    api('/session').then(s => setNeedLogin(s.auth_required && !s.authenticated)).catch(() => {});
    const onUnauthorized = () => setNeedLogin(true);
    window.addEventListener('radar:unauthorized', onUnauthorized);
    return () => window.removeEventListener('radar:unauthorized', onUnauthorized);
  }, []);

  const toast = React.useCallback((text, tone = 'success') => {
    const id = Math.random().toString(36).slice(2);
    setToasts(list => [...list.slice(-2), { id, text, tone }]);
    setTimeout(() => setToasts(list => list.filter(item => item.id !== id)), tone === 'success' ? 4000 : 7000);
  }, []);
  const dismiss = React.useCallback(id => setToasts(list => list.filter(item => item.id !== id)), []);

  const refresh = React.useCallback(async () => {
    const data = await api('/state');
    setState({ ...EMPTY, ...data });
    setLink({ ok: true, at: data.server?.time || new Date().toISOString() });
    setLoading(false);
  }, []);
  React.useEffect(() => {
    if (needLogin) return undefined;
    let alive = true;
    const update = () => refresh().catch(e => { if (alive && !/вход/i.test(e.message)) { setLink(previous => ({ ok: false, at: previous.at })); setLoading(false); } });
    update();
    const timer = setInterval(update, 2000);
    return () => { alive = false; clearInterval(timer); };
  }, [refresh, needLogin]);

  const act = React.useCallback(async (path, body = {}, success) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError('');
    try {
      const result = await api(path, body);
      await refresh().catch(() => {});
      if (success) toast(typeof success === 'function' ? success(result) : success);
      return result;
    } catch (e) { setError(e.message); }
    finally { busyRef.current = false; setBusy(false); }
  }, [refresh, toast]);

  const setTab = React.useCallback(key => { setError(''); setTabRaw(key); }, [setTabRaw]);
  const go = React.useCallback((key, presets = {}) => { Object.entries(presets).forEach(([name, value]) => prefs.set(name, value)); setTab(key); }, [setTab]);

  const navRef = React.useRef(null);
  React.useEffect(() => { navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }, [tab]);
  const simulationOnly = state.server?.simulation_enabled && !state.server?.preview_fixture;
  const activeTab = simulationOnly ? 'simulation' : PAGES[tab] && tab !== 'simulation' ? tab : 'findings';
  const navigation = simulationOnly ? [['simulation', 'Боты и чтение', Users]] : NAV;
  const Page = PAGES[activeTab];
  const marks = {
    jobs: state.runs.some(run => run.status === 'running') ? 'active' : null,
    runs: state.server && !state.server.worker_alive && state.runs.some(run => run.status === 'queued') ? 'warning' : null,
    mail: state.mail_running ? 'active' : state.mailboxes.some(row => row.status === 'unknown') ? 'warning' : null,
  };
  async function logout() { await api('/logout', {}).catch(() => {}); setNeedLogin(true); }
  if (needLogin) return <Login onDone={() => { setNeedLogin(false); setLoading(true); }} />;
  return <ToastContext.Provider value={toast}>
    <div className="app analyst-app">
      <aside className="sidebar">
        <div className="brand"><BookOpen size={25} strokeWidth={1.6} aria-hidden="true" /><span>Книжный радар</span></div>
        <nav aria-label="Разделы" ref={navRef}>{navigation.map(([key, label, Icon]) =>
          <button key={key} className={activeTab === key ? 'active' : ''} aria-current={activeTab === key ? 'page' : undefined} onClick={() => setTab(key)}>
            <Icon size={18} strokeWidth={1.9} aria-hidden="true" /><span>{label}</span>{marks[key] && <i className={`nav-mark ${marks[key]}`} aria-label={marks[key] === 'active' ? 'идёт процесс' : 'требует проверки'} />}
          </button>)}</nav>
        {!simulationOnly && <details className="extra-navigation" open={EXTRA_NAV.some(([key]) => key === activeTab) || undefined}><summary>Дополнительно<ChevronDown size={15} /></summary><div>{EXTRA_NAV.map(([key, label, Icon]) => <button key={key} className={activeTab === key ? 'active' : ''} aria-current={activeTab === key ? 'page' : undefined} onClick={() => setTab(key)}><Icon size={17} strokeWidth={1.8} /><span>{label}</span></button>)}</div></details>}
        <div className="sidebar-foot"><Connection link={link} /><WorkerLine server={state.server} />
          {state.server && <span className="version">{simulationOnly ? 'Симуляция' : state.server.preview_fixture ? 'Просмотр дизайна' : 'Обычный режим'}</span>}
          <span className="version">{state.server?.version || ''}</span>
          {state.server?.preview_fixture && <span className="preview-fixture-label">Демонстрационные данные</span>}
          {state.server?.auth && <button className="text-button logout" onClick={logout} aria-label="Выйти" title="Выйти"><LogOut size={13} /><span className="conn-text">Выйти</span></button>}</div>
      </aside>
      <div className="workspace">
        {!['findings', 'jobs', 'runs', 'export'].includes(activeTab) && <Activity state={state} act={act} busy={busy} />}
        <main>
          {state.server?.preview_fixture && <Notice tone="warning"><strong>ПРОСМОТР ДИЗАЙНА — ВЫМЫШЛЕННЫЕ ДАННЫЕ.</strong> Эти объявления, аккаунты и результаты не подтверждают работу с настоящими сайтами.</Notice>}
          {simulationOnly && <Notice tone="warning">Боты и чтение используют локальную симуляцию. Отдельный модуль постоянной почты обращается к настоящему Mailcow только после явного подключения сервера. Для настоящего сбора откройте обычный режим программы.</Notice>}
          {state.server?.version && state.server.version !== UI_VERSION && <Notice tone="warning">Открыта версия интерфейса {UI_VERSION}, а программа ещё работает на версии {state.server.version}. Закройте прежнее окно запуска программы через Ctrl+C и запустите его заново, чтобы применить обновление.</Notice>}
          {link.ok === false && <Notice tone="warning">Нет связи с программой. На экране данные {link.at ? `на ${shortStamp(link.at)}` : 'не загружены'}; повторяем запрос каждые 2 секунды. Проверьте, что сервер запущен.</Notice>}
          {error && <Notice tone="danger" onClose={() => setError('')}>{error}</Notice>}
          {loading
            ? <div className="view"><div className="page-header"><h1>{activeTab === 'findings' ? 'Находки' : 'Загрузка…'}</h1></div>{activeTab === 'findings' ? <CatalogSkeleton /> : <Skeleton rows={5} />}</div>
            : <div className="view" key={activeTab}><Page state={state} refresh={refresh} act={act} busy={busy} go={go} /></div>}
        </main>
      </div>
      <Toasts items={toasts} dismiss={dismiss} />
    </div>
  </ToastContext.Provider>;
}
