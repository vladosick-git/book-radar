import React from 'react';
import { ChevronDown, LoaderCircle, CircleCheck, CircleAlert, Clock3, Square, SlidersHorizontal } from 'lucide-react';
import { Empty, Select, Notice, Pager, Period } from './components';
import { RunPages, WorkerCard } from './Runs';
import { creativeDate } from './CatalogUI';
import { dayStart, qs, usePref, useServerList } from './api';

const LABELS = { queued: 'В очереди', running: 'Идёт', done: 'Завершён', partial: 'Частично', error: 'Ошибка', failed: 'Сбой', waiting_user: 'Нужна проверка вручную', stopped: 'Остановлен', interrupted: 'Прерван', cancelled: 'Отменён', skipped: 'Пропущен' };
export function RunStatus({ status }) {
  const tone = status === 'done' ? 'success' : ['error', 'failed'].includes(status) ? 'danger' : ['partial', 'waiting_user', 'interrupted'].includes(status) ? 'warning' : status === 'running' ? 'active' : 'neutral';
  const Icon = status === 'running' ? LoaderCircle : status === 'done' ? CircleCheck : ['error', 'failed', 'waiting_user', 'partial', 'interrupted'].includes(status) ? CircleAlert : Clock3;
  return <span className={`run-status run-${tone}`}><Icon size={18} strokeWidth={1.8} className={status === 'running' ? 'spin' : undefined} />{LABELS[status] || status}</span>;
}

export function RunTable({ items, state, act, busy, loading = false, showError = true }) {
  const [opened, setOpened] = React.useState(() => new Set());
  const toggle = id => setOpened(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const failure = showError && items.find(r => ['error', 'failed'].includes(r.status) && r.error);
  return <>
    <div className="analyst-table-wrap"><table className="analyst-table runs-table" aria-busy={loading}><thead><tr><th>Запуск</th><th>Задание</th><th>Состояние</th><th>Страницы</th><th>Начало</th><th><span className="visually-hidden">Действия</span></th></tr></thead>
      <tbody>{items.map(run => <React.Fragment key={run.id}><tr>
        <td data-label="Запуск" className="tabular">#{run.id}</td><td data-label="Задание">{run.job_name}</td><td data-label="Состояние"><RunStatus status={run.status} /></td>
        <td data-label="Страницы"><span className="run-progress-cell"><span className={`run-progress-bar status-${run.status}`}><i style={{ width: `${Math.min(100, run.pages_done / Math.max(1, run.pages_total) * 100)}%` }} /></span><span className="tabular">{run.pages_done} из {run.pages_total}</span></span></td>
        <td data-label="Начало" className="tabular">{run.status === 'queued' ? 'Ожидает' : creativeDate(run.started_at || run.queued_at)}</td>
        <td className="table-row-actions">{['queued', 'running'].includes(run.status) && <button className="icon-button" aria-label={`Остановить запуск #${run.id}`} disabled={busy || run.stop_requested} onClick={() => act(`/jobs/${run.job_id}/stop`, {}, 'Остановка запрошена.')}><Square size={13} /></button>}<button className="icon-button" aria-label={`Страницы запуска #${run.id}`} aria-expanded={opened.has(run.id)} onClick={() => toggle(run.id)}><ChevronDown size={16} /></button></td>
      </tr>{opened.has(run.id) && <tr className="run-detail-row"><td colSpan="6"><RunPages run={run} open codes={state.server?.error_codes} />{run.error && <p className="error-text run-detail-error">{run.error}</p>}</td></tr>}</React.Fragment>)}</tbody>
    </table></div>
    {failure && <div className="collection-error"><CircleAlert size={18} /><span>Запуск #{failure.id}: {failure.error.split('\n')[0]}</span><button className="btn danger-ghost sm" disabled={busy} onClick={() => act(`/jobs/${failure.job_id}/run`, {}, 'Повторный запуск поставлен в очередь.')}>Повторить</button></div>}
  </>;
}

export default function AnalystRuns({ state, act, busy, go }) {
  const [job, setJob] = usePref('runs.job', ''), [status, setStatus] = usePref('runs.status', '');
  const [since, setSince] = usePref('runs.since', ''), [until, setUntil] = usePref('runs.until', '');
  const [offset, setOffset] = React.useState(0);
  const filterKey = JSON.stringify([job, status, since, until]);
  React.useEffect(() => { setOffset(0); }, [filterKey]);
  const pulse = state.runs.map(r => `${r.id}:${r.status}:${r.pages_done}:${r.stop_requested}`).join(',');
  const list = useServerList(`/runs${qs({ job_id: job, status, since: dayStart(since), until: dayStart(until, true), offset, limit: 50 })}`, pulse);
  return <div className="analyst-runs"><header className="analyst-page-header"><div className="analyst-title-line"><h1>Запуски</h1></div></header>
    <div className="runs-controls"><Select label="Задание" value={job} onChange={setJob} options={[
      ['', 'Все задания'], ...state.jobs.map(j => [String(j.id), j.name]),
    ]} /><Select label="Состояние запуска" value={status} onChange={setStatus} options={[
      ['', 'Все статусы'], ...Object.entries(LABELS),
    ]} /><Period since={since} until={until} onChange={(a, b) => { setSince(a); setUntil(b); }} label="Период запусков" /></div>
    {list.error && <Notice tone="danger">{list.error}<button className="text-button" onClick={list.reload}>Повторить</button></Notice>}
    {list.data?.items.length ? <><RunTable items={list.data.items} state={state} act={act} busy={busy} loading={list.loading} /><Pager total={list.data.total} offset={offset} limit={50} loading={list.loading} onChange={setOffset} /></> : list.loading ? <p className="meta">Загрузка запусков…</p> : !list.error && <Empty title="Запусков пока нет" action={<button className="btn primary" onClick={() => go('jobs')}>К заданиям</button>} />}
    <details className="catalog-advanced"><summary><SlidersHorizontal size={14} />Состояние сборщика<ChevronDown size={14} /></summary><WorkerCard server={state.server} /></details>
  </div>;
}
