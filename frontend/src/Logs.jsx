import React from 'react';
import { ScrollText, ArrowRight, Bell, BellOff } from 'lucide-react';
import { Empty, PageHeader, SearchInput, Select, Segmented, LogMessage, Pager, Period, Notice, Modal } from './components';
import { Status } from './status';
import { dayStart, qs, shortStamp, stamp, usePref, useFlash, useDebounced, useServerList } from './api';
import { hintFor } from './Jobs';
import Actions from './Actions';
import Notifications from './Notifications';

const LIMIT = 100;
const SOURCES = { job: 'Задания', mail: 'Почта', profile: 'Профили', system: 'Система' };
const MAIL_HINTS = [[/неизвестен|Нужно проверить|мог создаться/i, 'Проверьте адрес в панели администратора Zoho. Повтор из программы заблокирован, пока результат неизвестен.'], [/Zoho завершён/i, 'Итог по каждому адресу — в разделе «Почта».']];
const hintOf = log => (log.source === 'mail' ? MAIL_HINTS.find(([p]) => p.test(log.message))?.[1] : null) || hintFor(log.message);

function Events({ state, go }) {
  const [level, setLevel] = usePref('logs.level', '');
  const [source, setSource] = usePref('logs.source', '');
  const [profile, setProfile] = usePref('logs.profile', '');
  const [code, setCode] = usePref('logs.code', '');
  const [search, setSearch] = usePref('logs.search', '');
  const [since, setSince] = usePref('logs.since', '');
  const [until, setUntil] = usePref('logs.until', '');
  const [offset, setOffset] = React.useState(0);
  const q = useDebounced(search.trim());
  const query = { q, level, source, profile_id: profile, error_code: code, since: dayStart(since), until: dayStart(until, true) };
  const filterKey = JSON.stringify(query);
  React.useEffect(() => { setOffset(0); }, [filterKey]);
  const { data, loading, error } = useServerList(`/logs${qs({ ...query, limit: LIMIT, offset })}`, offset ? 0 : state.journal?.last_id);
  const jobs = React.useMemo(() => new Map(state.jobs.map(job => [job.id, job])), [state.jobs]);
  const items = data?.items || [];
  const flash = useFlash(items.slice(0, 50), log => log.id, () => 1);
  const levels = data?.levels || {};
  const codes = state.server?.error_codes || {};
  const reset = () => { setLevel(''); setSource(''); setProfile(''); setCode(''); setSearch(''); setSince(''); setUntil(''); };
  if (data && !data.total && !Object.values(query).some(Boolean)) return <Empty icon={ScrollText} title="Журнал пока пуст">Здесь появятся события программы.</Empty>;
  return <>
    <div className="toolbar">
      <Segmented label="Уровень" value={level} onChange={setLevel} options={[['', 'Все'], ['error', `Ошибки · ${levels.error || 0}`], ['warning', `Внимание · ${levels.warning || 0}`], ['info', 'Инфо']]} />
      <SearchInput label="Поиск по журналу" value={search} onChange={setSearch} placeholder="Текст события или задание" />
    </div>
    <div className="toolbar">
      <Select label="Контекст" value={source} onChange={setSource} options={[['', 'Любой контекст'], ...Object.entries(SOURCES)]} />
      <Select label="Профиль" value={profile} onChange={setProfile} options={[['', 'Все профили'], ...state.profiles.map(p => [String(p.id), p.name])]} />
      <Select label="Код" value={code} onChange={setCode} options={[['', 'Любой код'], ['*', 'Только с кодом'], ...Object.entries(codes).map(([key, label]) => [key, `${key} · ${label}`])]} />
      <Period since={since} until={until} onChange={(a, b) => { setSince(a); setUntil(b); }} label="Период событий" />
    </div>
    {error && <Notice tone="danger">{error}</Notice>}
    {items.length ? <ol className={`log-list${loading ? ' is-loading' : ''}`}>{items.map(log => { const job = jobs.get(log.job_id), hint = log.level !== 'info' && hintOf(log); return <li key={log.id} className={`log level-${log.level}${flash.has(log.id) ? ' flash' : ''}`}>
      <time dateTime={log.created_at} title={stamp(log.created_at)}>{shortStamp(log.created_at)}</time>
      <Status kind="log" value={log.level} />
      <div className="log-body">
        <span className="log-context">{SOURCES[log.source] || 'Система'}{job && <> · <button className="text-button" onClick={() => go('jobs', { 'jobs.search': job.name, 'jobs.status': '', 'jobs.profile': '' })}>{job.name}<ArrowRight size={12} /></button></>}
          {!job && log.job_id && ` · задание #${log.job_id}${log.job_name ? ` «${log.job_name}»` : ''}`}{log.run_id && ` · запуск #${log.run_id}`}
          {log.error_code && <button className="code-tag as-button" title={codes[log.error_code] || ''} onClick={() => setCode(log.error_code)}>{log.error_code}</button>}</span>
        <p><LogMessage message={log.message} /></p>
        {hint && <p className="log-hint">Что сделать: {hint}</p>}
      </div></li>; })}</ol>
      : data && <Empty compact title="Нет событий с такими условиями" action={<button className="btn secondary" onClick={reset}>Сбросить фильтры</button>} />}
    <Pager total={data?.total || 0} offset={offset} limit={LIMIT} loading={loading} onChange={value => { setOffset(value); window.scrollTo({ top: 0 }); }} />
    {offset > 0 && <p className="footnote">Новые события не подгружаются на дальних страницах — вернитесь на первую.</p>}
  </>;
}

export default function Logs({ state, go }) {
  const [tab, setTab] = usePref('logs.tab', 'events');
  const [settings, setSettings] = React.useState(false);
  const enabled = state.notifications?.enabled;
  const actions = <button className={`btn ${enabled ? 'secondary' : 'ghost'}`} onClick={() => setSettings(true)}>{enabled ? <Bell size={15} /> : <BellOff size={15} />}Уведомления{enabled ? ': вкл.' : ''}</button>;
  return <>
    <PageHeader title="Журнал" actions={actions}>События сбора и действия пользователей за любой период</PageHeader>
    <div className="tabs-line"><Segmented label="Раздел журнала" value={tab} onChange={setTab} options={[['events', 'События программы'], ['actions', 'Действия пользователей']]} /></div>
    <div key={tab} className="catalog">{tab === 'actions' ? <Actions state={state} /> : <Events state={state} go={go} />}</div>
    {settings && <Modal title="Уведомления в Telegram" subtitle="О сбоях, «Нужен пользователь» и восстановлении" close={() => setSettings(false)}><Notifications /></Modal>}
  </>;
}
