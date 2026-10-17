import React from 'react';
import { History, Server, Square, ArrowRight, ChevronDown, ExternalLink } from 'lucide-react';
import { Empty, PageHeader, Select, Notice, Reveal, Pager, Period } from './components';
import { Status, STATUS } from './status';
import { TRIGGERS, ago, api, dayStart, evidenceUrl, host, plural, qs, shortStamp, stamp, usePref, useFlash, useServerList } from './api';

export function RunPages({ run, open, codes }) {
  const [pages, setPages] = React.useState(null), [error, setError] = React.useState('');
  React.useEffect(() => {
    if (!open) return;
    let alive = true;
    api(`/runs/${run.id}/pages`).then(r => alive && setPages(r.pages)).catch(e => alive && setError(e.message));
    return () => { alive = false; };
  }, [open, run.id, run.pages_done, run.status]);
  const label = code => codes?.[code] || code;
  return <Reveal open={open}><div className="run-pages">
    {error ? <p className="error-text">{error}</p> : !pages ? <p className="meta">Загрузка…</p> : !pages.length ? <p className="meta">Постраничных результатов нет: запуск не дошёл до первой страницы или выполнен до версии 0.6.2.</p>
      : <ol className="page-list">{pages.map(page => <li key={page.idx}>
        <span className="page-n">{page.idx + 1}</span>
        <span className="page-url"><span className="truncate" title={page.url}>{host(page.url)}{(() => { try { return new URL(page.url).pathname; } catch { return ''; } })()}</span>
          {page.error_code && <small className="error-text">{label(page.error_code)} · {page.error_code}</small>}
          {page.login !== null && page.login !== undefined && <small>{page.login ? 'вход подтверждён' : 'признак входа не найден'}</small>}</span>
        <Status kind="page" value={page.status} />
        <span className="meta">{page.found} {plural(page.found, 'блок', 'блока', 'блоков')}</span>
        {evidenceUrl(page.screenshot) ? <a className="inline-link" href={evidenceUrl(page.screenshot)} target="_blank" rel="noreferrer">Снимок<ExternalLink size={12} /></a> : <span />}
      </li>)}</ol>}
  </div></Reveal>;
}

export function WorkerCard({ server }) {
  const workers = server?.workers || [];
  const alive = workers.filter(w => w.alive);
  const latest = workers[0];
  return <section className="card worker-card">
    <div className="worker-head"><Server size={18} aria-hidden="true" /><strong>Worker</strong>
      <span className={`dot ${alive.length ? 'ok' : 'lost'}`} />
      <span>{alive.length ? `отвечает · сигнал ${ago(latest.heartbeat_at, server.time)}` : latest ? `не отвечает · последний сигнал ${ago(latest.heartbeat_at, server.time)}` : 'не запущен'}</span></div>
    <dl className="worker-facts">
      <dt>Режим</dt><dd>{server?.mode === 'server' ? 'сервер: worker отдельным процессом' : 'локальный: worker внутри программы'}</dd>
      <dt>Лимит браузеров</dt><dd>{server?.max_browsers}</dd>
      <dt>Очередь</dt><dd>{server?.queue.running} выполняется · {server?.queue.queued} ждёт</dd>
      {latest && <><dt>Запущен</dt><dd>{stamp(latest.started_at)}</dd></>}
    </dl>
    {!alive.length && <Notice tone="warning">{server?.mode === 'server'
      ? 'Задания стоят в очереди, но их некому выполнить. Проверьте сервис worker (docker compose ps / journalctl).'
      : 'Worker не отвечает. Перезапустите программу; незавершённые запуски продолжатся с готового места.'}</Notice>}
  </section>;
}

const LIMIT = 50;

export default function Runs({ state, act, busy, go }) {
  const [job, setJob] = usePref('runs.job', '');
  const [status, setStatus] = usePref('runs.status', '');
  const [since, setSince] = usePref('runs.since', '');
  const [until, setUntil] = usePref('runs.until', '');
  const [offset, setOffset] = React.useState(0);
  const query = { job_id: job, status, since: dayStart(since), until: dayStart(until, true) };
  const filterKey = JSON.stringify(query);
  React.useEffect(() => { setOffset(0); }, [filterKey]);
  // The 2-second state poll carries the latest runs: any change there refreshes the first page of the history.
  const pulse = offset ? 0 : state.runs.slice(0, 20).map(r => `${r.id}:${r.status}:${r.pages_done}:${r.stop_requested}`).join(',');
  const { data, loading, error } = useServerList(`/runs${qs({ ...query, limit: LIMIT, offset })}`, pulse);
  const list = data?.items || [];
  const flash = useFlash(list, r => r.id, r => `${r.status}|${r.pages_done}`);
  const [opened, setOpened] = React.useState(() => new Set());
  const toggle = id => setOpened(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
  const reset = () => { setJob(''); setStatus(''); setSince(''); setUntil(''); };
  const filtered = Object.values(query).some(Boolean);
  return <>
    <PageHeader title="Запуски">Очередь, выполнение и история запусков заданий за любой период</PageHeader>
    <WorkerCard server={state.server} />
    {data && !data.total && !filtered
      ? <Empty icon={History} title="Запусков пока не было" action={<button className="btn primary" onClick={() => go('jobs')}>К заданиям</button>}>Запуск вручную или по расписанию появится здесь. История сохраняется после перезапуска программы.</Empty>
      : <>
        <div className="toolbar">
          <Select label="Задание" value={job} onChange={setJob} options={[['', 'Все задания'], ...state.jobs.map(j => [String(j.id), j.name])]} />
          <Select label="Статус" value={status} onChange={setStatus} options={[['', 'Все статусы'], ...Object.entries(STATUS.run).map(([k, [l]]) => [k, l])]} />
          <Period since={since} until={until} onChange={(a, b) => { setSince(a); setUntil(b); }} label="Период постановки в очередь" />
        </div>
        {error && <Notice tone="danger">{error}</Notice>}
        {list.length ? <ul className={`list${loading ? ' is-loading' : ''}`}>{list.map(run => <li key={run.id} className={`row run${flash.has(run.id) ? ' flash' : ''}`}><div className="row-main">
          <button className="row-toggle" aria-expanded={opened.has(run.id)} aria-label={`Страницы запуска #${run.id}`} onClick={() => toggle(run.id)}><ChevronDown size={16} /></button>
          <div className="row-title"><strong>#{run.id} · {run.job_name}</strong>
            <span className="meta">{TRIGGERS[run.trigger]}{run.attempt > 1 ? ` · попытка ${run.attempt}` : ''}{run.retry_of ? ` · продолжение #${run.retry_of}` : ''}{run.scheduled_for ? ` · на ${shortStamp(run.scheduled_for)}` : ''}</span></div>
          <div className="row-status"><Status kind="run" value={run.status} flash={flash.has(run.id)} /></div>
          <div className="row-result"><span><strong className="num">{run.pages_done}</strong> из {run.pages_total} стр. · <strong className="num">{run.found}</strong> {plural(run.found, 'блок', 'блока', 'блоков')}</span>
            <span className="meta">{run.status === 'queued' ? `в очереди с ${shortStamp(run.queued_at)}${run.not_before > state.server?.time ? ` · не раньше ${shortStamp(run.not_before)}` : ''}`
              : run.finished_at ? `${shortStamp(run.started_at || run.queued_at)} → ${shortStamp(run.finished_at)}` : `начат ${shortStamp(run.started_at)} · сигнал ${ago(run.heartbeat_at, state.server?.time)}`}</span></div>
          <div className="row-actions">{['queued', 'running'].includes(run.status) && <button className="btn danger-ghost sm" disabled={busy || run.stop_requested} onClick={() => act(`/jobs/${run.job_id}/stop`, {}, r => r.status === 'cancelled' ? 'Запуск отменён.' : 'Остановка запрошена: worker завершит текущую страницу.')}><Square size={12} />{run.stop_requested ? 'Останавливается' : run.status === 'queued' ? 'Отменить' : 'Стоп'}</button>}</div>
        </div>{run.error && !['done'].includes(run.status) && <p className={`row-error${['waiting_user', 'interrupted', 'skipped', 'cancelled', 'stopped'].includes(run.status) ? ' warn' : ''}`}>{run.error}{run.error_code && <span className="code-tag">{run.error_code}</span>}</p>}
          <RunPages run={run} open={opened.has(run.id)} codes={state.server?.error_codes} /></li>)}</ul>
          : data && <Empty compact title="Нет запусков с такими условиями" action={<button className="btn secondary" onClick={reset}>Сбросить фильтры</button>} />}
        <Pager total={data?.total || 0} offset={offset} limit={LIMIT} loading={loading} onChange={value => { setOffset(value); window.scrollTo({ top: 0 }); }} />
        <p className="footnote">Страницы считаются только завершёнными; после сбоя повтор продолжает со следующей незавершённой страницы.</p>
      </>}
  </>;
}
