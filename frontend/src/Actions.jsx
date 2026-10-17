import React from 'react';
import { UserRound } from 'lucide-react';
import { Empty, SearchInput, Select, Pager, Period, Notice } from './components';
import { dayStart, qs, shortStamp, stamp, usePref, useDebounced, useServerList } from './api';

const LIMIT = 50;
// Stage 17: what a person did in the panel (the audit table). Names of actions come from the server, details never contain secrets.
const TARGETS = { job: 'задание', profile: 'профиль', findings: 'находки', notifications: 'уведомления' };
const FIELDS = {
  name: 'название', pages: 'страниц', changed: 'изменено', kind: 'правило', interval_minutes: 'интервал, мин', daily_time: 'время', run_at: 'дата',
  timezone: 'пояс', missed_policy: 'пропуски', overlap_policy: 'наложения', window_start: 'окно с', window_end: 'окно до', paused: 'пауза', run_id: 'запуск',
  result: 'итог', source_job: 'из задания', url: 'адрес', cookies: 'cookies', domains: 'домены', client: 'адрес клиента', findings: 'находок',
  observations: 'с историей', enabled: 'включено', chat_id: 'получатель', events: 'события', bot_changed: 'новый бот', bot_removed: 'бот удалён', delivered: 'доставлено',
};

const EVENTS = { failure: 'сбой', user: 'нужен пользователь', done: 'завершение', recovered: 'восстановление' };

function value(v, key) {
  if (key === 'events' && Array.isArray(v)) return v.map(e => EVENTS[e] || e).join(', ') || '—';
  if (v === true) return 'да';
  if (v === false) return 'нет';
  if (Array.isArray(v)) return v.join(', ') || '—';
  return v === null || v === undefined || v === '' ? '—' : String(v);
}

export default function Actions() {
  const [search, setSearch] = usePref('audit.search', '');
  const [action, setAction] = usePref('audit.action', '');
  const [since, setSince] = usePref('audit.since', '');
  const [until, setUntil] = usePref('audit.until', '');
  const [offset, setOffset] = React.useState(0);
  const q = useDebounced(search.trim());
  const query = { q, action, since: dayStart(since), until: dayStart(until, true) };
  const filterKey = JSON.stringify(query);
  React.useEffect(() => { setOffset(0); }, [filterKey]);
  const { data, loading, error } = useServerList(`/audit${qs({ ...query, limit: LIMIT, offset })}`, 0);
  const items = data?.items || [];
  const known = data?.actions || [], labels = data?.labels || {};
  return <>
    <div className="toolbar">
      <SearchInput label="Поиск по действиям" value={search} onChange={setSearch} placeholder="Действие, исполнитель или подробности" />
      <Select label="Действие" value={action} onChange={setAction} options={[['', 'Любое действие'], ...known.map(key => [key, labels[key] || key])]} />
      <Period since={since} until={until} onChange={(a, b) => { setSince(a); setUntil(b); }} label="Период действий" />
    </div>
    {error && <Notice tone="danger">{error}</Notice>}
    {items.length ? <ol className={`log-list${loading ? ' is-loading' : ''}`}>{items.map(entry => <li key={entry.id} className="log action-row">
      <time dateTime={entry.at} title={stamp(entry.at)}>{shortStamp(entry.at)}</time>
      <span className="actor"><UserRound size={13} aria-hidden="true" /><span className="truncate" title={entry.actor}>{entry.actor}</span></span>
      <div className="log-body">
        <span className="log-context">{labels[entry.action] || entry.action}{entry.target_type && ` · ${TARGETS[entry.target_type] || entry.target_type}${entry.target_id ? ` #${entry.target_id}` : ''}`}</span>
        {Object.keys(entry.details || {}).length > 0 && <p className="action-details">{Object.entries(entry.details).map(([key, v]) => <span key={key}><span className="meta">{FIELDS[key] || key}:</span> {value(v, key)}</span>)}</p>}
      </div></li>)}</ol>
      : data && <Empty compact title={data.total || q || action || since || until ? 'Нет действий с такими условиями' : 'Действий пока нет'}>Сюда записываются вход, изменения заданий и расписаний, запуски, остановки, перенос сессий и экспорт.</Empty>}
    <Pager total={data?.total || 0} offset={offset} limit={LIMIT} loading={loading} onChange={value => { setOffset(value); window.scrollTo({ top: 0 }); }} />
  </>;
}
