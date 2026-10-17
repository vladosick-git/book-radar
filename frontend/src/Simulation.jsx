import React from 'react';
import { Play, Users, Mail, BookOpen, History, Link } from 'lucide-react';
import { Notice, PageHeader, Empty } from './components';
import { api, usePref } from './api';
import SimulationReading from './SimulationReading';
import SimulationBatch, { FleetStatus } from './SimulationBatch';
import SimulationProfiles from './SimulationProfiles';
import PermanentMail from './PermanentMail';
import { Disclosure } from './SimulationUI';

const tabs = [['launch', 'Запуск', Play], ['bots', 'Боты', Users], ['mail', 'Почта', Mail], ['reading', 'Чтение', BookOpen], ['history', 'История', History]];
const events = { profile_created: 'Добавлен бот', site_register: 'Создан аккаунт', site_login: 'Повторный вход', session_verified: 'Вход подтверждён', page_read: 'Прочитана страница', fleet_started: 'Запущена партия ботов' };

export default function Simulation({ state }) {
  const [data, setData] = React.useState(null), [error, setError] = React.useState('');
  const [mail, setMail] = React.useState(null), [fleet, setFleet] = React.useState(null);
  const [requestBusy, setBusy] = React.useState(false), [chosen, setChosen] = React.useState([]), [operation, setOperation] = React.useState('');
  const [savedTab, setTab] = usePref('simulation-section', 'launch'), [fixtureOnly, setFixtureOnly] = usePref('simulation-fixture-only', false);
  const tab = tabs.some(([id]) => id === savedTab) ? savedTab : 'launch';
  const nav = React.useRef(null);
  const enabled = state.server?.simulation_enabled;
  const profiles = data?.profiles || [], busy = requestBusy || profiles.some(p => p.active);
  async function refreshMail() { const r = await api('/simulation/mail/state'); setMail(r); }
  async function refreshFleet() { const r = await api('/simulation/fleet'); setFleet(r.run); }
  async function refresh() {
    const [s, m, f] = await Promise.all([api('/simulation/state'), api('/simulation/mail/state'), api('/simulation/fleet')]);
    setData(s); setMail(m); setFleet(f.run);
  }
  React.useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    const update = () => Promise.all([api('/simulation/state'), api('/simulation/mail/state'), api('/simulation/fleet')]).then(([s, m, f]) => {
      if (active) { setData(s); setMail(m); setFleet(f.run); }
    }).catch(e => { if (active) setError(e.message); });
    update(); const timer = setInterval(update, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [enabled]);
  async function run(path, body) {
    setBusy(true); setOperation(path); setError('');
    try {
      const result = await api(`/simulation/${path}`, body);
      if (result.profile_ids) setChosen(result.profile_ids.slice(0, 10));
      const failed = result.results?.filter(r => !['registered', 'done', 'already_finished'].includes(r.status)) || [];
      if (failed.length) setError(`Не все боты завершили операцию: ${failed.map(r => `#${r.profile_id}: ${r.message || r.status}`).join('; ')}. Успешные результаты сохранены.`);
      await refresh(); return result;
    } catch (err) { setError(err.message); }
    finally { setBusy(false); setOperation(''); }
  }
  function selectTab(id, focus = false) {
    setTab(id);
    if (focus) nav.current?.querySelector(`#work-tab-${id}`)?.focus();
  }
  function navigateTabs(event) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = tabs.findIndex(([id]) => id === tab);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectTab(tabs[next][0], true);
  }
  if (!enabled) return <><PageHeader title="Боты и чтение">Управление аккаунтами и браузерами</PageHeader><Notice tone="info">Для этой панели запустите Simulation.cmd. Она использует отдельную базу и встроенный локальный сайт.</Notice></>;
  return <div className="bot-workbench">
    <PageHeader title="Боты и чтение">Аккаунты, почта и сценарии чтения в одной панели</PageHeader>
    <div className="workbench-overview" aria-label="Обзор"><div><strong>{profiles.length}</strong><span>ботов в базе</span></div><div><strong>{data?.registered || 0}</strong><span>с подтверждённым входом</span></div><div><strong>{mail?.accounts.filter(m => m.ready).length || 0}</strong><span>готовых почтовых ящиков</span></div><div><strong>{Object.keys(data?.cities || {}).length} / {Object.keys(data?.devices || {}).length}</strong><span>городов / устройств</span></div></div>
    <Disclosure className="connection-disclosure" title={<><Link size={15} aria-hidden="true" />Сайт: встроенный локальный</>} caption="Сайт клиента ещё не подключён">
      <div className="connection-grid"><div><strong>Книжный сайт</strong><p>Регистрация, вход и чтение работают во встроенной локальной копии. Подключение сайта клиента ещё предстоит.</p></div>
        <div><strong>Почта</strong><p>{mail?.configured ? `Подключён домен ${mail.settings.domain}. Готовность каждого ящика проверяется отдельно.` : 'Сервер создания почты не подключён. Можно подключить Mailcow или добавить уже готовые ящики.'}</p><button className="text-button" onClick={() => selectTab('mail', true)}>Открыть почту</button></div>
        <div><strong>Города и устройства</strong><p>Эмулируются экран, координаты и часовой пояс. Выбор города не меняет IP, а профиль iPhone не запускает настоящий iOS.</p></div></div>
    </Disclosure>
    {error && <Notice tone="danger" onClose={() => setError('')}>{error}</Notice>}
    <FleetStatus fleet={fleet} busy={requestBusy} stop={() => run(`fleet/${fleet.id}/stop`, {})} />
    <div className="workbench-tabs" role="tablist" aria-label="Управление ботами" ref={nav} onKeyDown={navigateTabs}>{tabs.map(([id, label, Icon]) => <button key={id} id={`work-tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls={`work-panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => selectTab(id)}><Icon size={16} aria-hidden="true" /><span>{label}</span>{id === 'bots' && profiles.length > 0 && <small>{profiles.length}</small>}{id === 'mail' && mail?.accounts.length > 0 && <small>{mail.accounts.length}</small>}</button>)}</div>
    <div id="work-panel-launch" role="tabpanel" aria-labelledby="work-tab-launch" hidden={tab !== 'launch'}>
      <SimulationBatch pool={data?.mail_pool} profiles={profiles} mail={mail} fleet={fleet} busy={requestBusy} operation={operation} perform={run} refreshMail={refreshMail} refreshFleet={refreshFleet} fixtureOnly={fixtureOnly} setFixtureOnly={setFixtureOnly} openMail={() => selectTab('mail', true)} />
    </div>
    <div id="work-panel-bots" role="tabpanel" aria-labelledby="work-tab-bots" hidden={tab !== 'bots'}>
      <SimulationProfiles data={data} chosen={chosen} setChosen={setChosen} busy={busy} operation={operation} perform={run} openReading={() => selectTab('reading', true)} />
    </div>
    <div id="work-panel-mail" role="tabpanel" aria-labelledby="work-tab-mail" hidden={tab !== 'mail'}>
      <PermanentMail data={mail} profiles={profiles} busy={requestBusy} operation={operation} perform={run} refresh={refreshMail} active={tab === 'mail'} />
    </div>
    <div id="work-panel-reading" role="tabpanel" aria-labelledby="work-tab-reading" hidden={tab !== 'reading'}>
      <SimulationReading profiles={profiles} chosen={chosen} busy={busy} operation={operation} perform={run} openBots={() => selectTab('bots', true)} />
    </div>
    <div id="work-panel-history" role="tabpanel" aria-labelledby="work-tab-history" hidden={tab !== 'history'}>
      <Disclosure title="Последние действия" caption="События регистрации, входа и чтения" defaultOpen>{data?.events.length ? <ul className="workbench-events">{data.events.slice(0, 20).map(e => <li key={e.id}><span>{e.profile_id ? profiles.find(p => p.id === e.profile_id)?.name || `Бот #${e.profile_id}` : 'Все боты'}</span><strong>{events[e.action] || e.action}</strong></li>)}</ul> : <Empty compact title="Действий пока нет">После первого запуска здесь появится история.</Empty>}</Disclosure>
    </div>
  </div>;
}
