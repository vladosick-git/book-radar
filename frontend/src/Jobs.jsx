import React from 'react';
import { Plus, Play, Square, Pencil, Copy, ChevronDown, ExternalLink, FlaskConical, ListChecks, CalendarClock } from 'lucide-react';
import { Empty, Modal, PageHeader, SearchInput, Select, Field, Reveal, Notice, Busy, useModalClose, useToast } from './components';
import { Status, STATUS } from './status';
import { api, host, plural, prefs, safeLink, shortStamp, stamp, usePref, useFlash, scheduleText, TRIGGERS } from './api';

const HINTS = [
  [/Блоки не найдены|не найдены/i, 'Откройте скриншот в журнале и задайте CSS-селектор контейнера рекламы.'],
  [/Доступ ограничен|проверка/i, 'Откройте браузер профиля, пройдите проверку вручную и закройте окно перед повтором.'],
  [/селектор/i, 'Исправьте CSS-селектор в параметрах задания.'],
  [/HTTP \d+|подключиться|найти сайт|сертификат/i, 'Проверьте адрес страницы и доступ к сайту, затем запустите снова.'],
  [/не ответили вовремя|Превышено время/i, 'Увеличьте ожидание или уменьшите число страниц в задании.'],
  [/завершилась во время сбора/i, 'Программа перезапускалась. Найденное сохранено — можно запустить заново.'],
];
export const hintFor = message => HINTS.find(([pattern]) => pattern.test(message || ''))?.[1];

function parseUrls(text) {
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  const bad = lines.map((line, index) => { try { const url = new URL(line); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? null : index + 1; } catch { return index + 1; } }).filter(Boolean);
  return { urls: [...new Set(lines)], bad, count: lines.length };
}

export function JobForm({ job, state, refresh, close, presetProfile }) {
  const requestClose = useModalClose(), toast = useToast();
  const [name, setName] = React.useState(job?.name || '');
  const [profile, setProfile] = React.useState(String(job?.profile_id || presetProfile || state.profiles[0]?.id || ''));
  const [urls, setUrls] = React.useState(job ? job.urls.join('\n') : '');
  const [selector, setSelector] = React.useState(job?.selector || '');
  const [loginSelector, setLoginSelector] = React.useState(job?.login_selector || '');
  const [wait, setWait] = React.useState(job?.wait_seconds ?? 5);
  const [headed, setHeaded] = React.useState(Boolean(job?.headed));
  const [touched, setTouched] = React.useState(false);
  const [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const live = job && state.jobs.find(item => item.id === job.id);
  const locked = Boolean(live?.active_run) || live?.status === 'running';
  const parsed = parseUrls(urls);
  const urlError = !parsed.count ? 'Добавьте хотя бы один адрес.' : parsed.bad.length ? `Строка ${parsed.bad.join(', ')}: нужен полный адрес http:// или https:// без логина и пароля.` : parsed.urls.length > 20 ? `Адресов ${parsed.urls.length}, допустимо до 20.` : '';
  const waitError = !(Number(wait) >= 2 && Number(wait) <= 30) ? 'От 2 до 30 секунд.' : '';
  const nameError = !name.trim() ? 'Укажите название.' : '';
  async function submit(event) {
    event.preventDefault(); setTouched(true); setError('');
    if (urlError || waitError || nameError || locked) return;
    setSaving(true);
    const body = { name, urls: parsed.urls, selector, wait_seconds: Number(wait), headed, login_selector: loginSelector };
    try {
      if (job) await api(`/jobs/${job.id}`, body); else await api('/jobs', { ...body, profile_id: Number(profile) });
      await refresh();
      toast(job ? 'Параметры сохранены. Они применятся со следующего запуска.' : 'Задание создано. Запустите его, когда профиль свободен.');
      requestClose();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  const sample = () => { setUrls(`${location.origin}/sample`); if (!name.trim()) setName('Тестовый сбор'); };
  return <form className="form" onSubmit={submit} noValidate>
    {locked && <Notice tone="warning">Запуск этого задания в очереди или выполняется. Остановите его или дождитесь завершения — до этого изменения не принимаются.</Notice>}
    <Field label="Название" error={touched && nameError}><input value={name} onChange={e => setName(e.target.value)} maxLength={80} placeholder="Например: Главная Author.Today" autoFocus /></Field>
    {job
      ? <Field label="Профиль" hint="Находки привязаны к профилю, поэтому он не меняется. Для другого профиля создайте новое задание."><input value={job.profile_name} readOnly disabled /></Field>
      : <Field label="Профиль" hint="Сбор использует cookies и вход этого профиля."><select value={profile} onChange={e => setProfile(e.target.value)}>{state.profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>}
    <Field label="Страницы" error={touched && urlError} hint={`Один адрес на строку, до 20. Сейчас: ${parsed.urls.length}.`}>
      <textarea value={urls} onChange={e => setUrls(e.target.value)} rows={4} placeholder="https://author.today/" spellCheck={false} />
    </Field>
    {state.server?.test_collection_enabled && <button type="button" className="text-button" onClick={sample}><FlaskConical size={14} />Подставить локальную тестовую страницу</button>}
    <Field label="CSS-селектор рекламного блока" optional hint="Пусто — автоматический поиск типовых контейнеров. Задайте селектор, если блоки не находятся.">
      <input value={selector} onChange={e => setSelector(e.target.value)} maxLength={500} placeholder="#atRecs .f-book-container" spellCheck={false} />
    </Field>
    <Field label="Признак входа (CSS-селектор)" optional hint="Элемент, который виден только вошедшему пользователю, например меню профиля. По нему программа отличает рабочую сессию от истёкшей.">
      <input value={loginSelector} onChange={e => setLoginSelector(e.target.value)} maxLength={500} placeholder=".user-menu" spellCheck={false} />
    </Field>
    <div className="form-pair">
      <Field label="Ожидание после загрузки, с" error={touched && waitError}><input type="number" min={2} max={30} value={wait} onChange={e => setWait(e.target.value)} /></Field>
      <div className="field"><span className="field-label">Окно браузера</span><label className="check"><input type="checkbox" checked={headed} onChange={e => setHeaded(e.target.checked)} /><span>Показывать при сборе</span></label></div>
    </div>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button>
      <button className="btn primary" disabled={saving || locked}><Busy busy={saving} busyText="Сохранение…">{job ? 'Сохранить изменения' : 'Создать задание'}</Busy></button></div>
  </form>;
}

const ZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Europe/Samara', 'Asia/Yekaterinburg', 'Asia/Omsk', 'Asia/Novosibirsk', 'Asia/Krasnoyarsk', 'Asia/Irkutsk', 'Asia/Yakutsk', 'Asia/Vladivostok', 'UTC'];

const MISSED = { one: 'Один догоняющий запуск', skip: 'Пропустить опоздавшие', all: 'Все пропущенные (до 20)' };
const OVERLAP = { skip: 'Пропустить новый', queue: 'Встать в очередь' };

function toLocalInput(iso, zone) {
  if (!iso) return '';
  try {
    const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
    return parts.replace(' ', 'T');
  } catch { return ''; }
}

export function ScheduleForm({ job, refresh }) {
  const requestClose = useModalClose(), toast = useToast();
  const local = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { return 'UTC'; } })();
  const initialZone = job.timezone && job.timezone !== 'UTC' ? job.timezone : (ZONES.includes(local) ? local : 'Europe/Moscow');
  const [kind, setKind] = React.useState(job.schedule_kind || 'none');
  const [minutes, setMinutes] = React.useState(job.interval_minutes || 60);
  const [time, setTime] = React.useState(job.daily_time || '03:00');
  const [zone, setZone] = React.useState(initialZone);
  const [runAt, setRunAt] = React.useState(toLocalInput(job.run_at, initialZone));
  const [missed, setMissed] = React.useState(job.missed_policy || 'one');
  const [overlap, setOverlap] = React.useState(job.overlap_policy || 'skip');
  const [useWindow, setUseWindow] = React.useState(Boolean(job.window_start));
  const [winStart, setWinStart] = React.useState(job.window_start || '01:00');
  const [winEnd, setWinEnd] = React.useState(job.window_end || '06:00');
  const [paused, setPaused] = React.useState(Boolean(job.paused));
  const [error, setError] = React.useState(''), [saving, setSaving] = React.useState(false);
  const zones = [...new Set([zone, local, ...ZONES])];
  const hhmm = value => /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
  const invalid = kind === 'interval' && !(Number(minutes) >= 1 && Number(minutes) <= 10080) ? 'Интервал — от 1 до 10080 минут.'
    : kind === 'daily' && !hhmm(time) ? 'Время — в формате ЧЧ:ММ.'
    : kind === 'once' && !runAt ? 'Укажите дату и время разового запуска.'
    : useWindow && kind !== 'once' && (!hhmm(winStart) || !hhmm(winEnd)) ? 'Окно работы — время начала и конца в формате ЧЧ:ММ.' : '';
  async function submit(event) {
    event.preventDefault(); if (invalid) return; setSaving(true); setError('');
    try {
      const windowed = useWindow && (kind === 'interval' || kind === 'daily');
      const result = await api(`/jobs/${job.id}/schedule`, {
        kind, interval_minutes: kind === 'interval' ? Number(minutes) : null, daily_time: kind === 'daily' ? time : null,
        run_at: kind === 'once' ? runAt : null, timezone: zone, missed_policy: missed, overlap_policy: overlap,
        window_start: windowed ? winStart : null, window_end: windowed ? winEnd : null, paused });
      await refresh();
      toast(result.next_run_at ? `Расписание сохранено. Следующий запуск: ${stamp(result.next_run_at)}.` : kind === 'none' ? 'Расписание отключено.' : 'Расписание сохранено, ближайших запусков нет.');
      requestClose();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  }
  return <form className="form" onSubmit={submit} noValidate>
    <Field label="Когда запускать"><select value={kind} onChange={e => setKind(e.target.value)}>
      <option value="none">Только вручную</option><option value="once">Один раз в заданное время</option>
      <option value="interval">Через равные интервалы</option><option value="daily">Каждый день в заданное время</option></select></Field>
    {kind === 'interval' && <Field label="Интервал, минут" hint="Отсчёт — от момента сохранения."><input type="number" min={1} max={10080} value={minutes} onChange={e => setMinutes(e.target.value)} /></Field>}
    {kind === 'once' && <Field label="Дата и время" hint="Время — в выбранном часовом поясе."><input type="datetime-local" value={runAt} onChange={e => setRunAt(e.target.value)} /></Field>}
    {kind === 'daily' && <Field label="Время"><input type="time" value={time} onChange={e => setTime(e.target.value)} /></Field>}
    {kind !== 'none' && <Field label="Часовой пояс"><select value={zone} onChange={e => setZone(e.target.value)}>{zones.map(z => <option key={z} value={z}>{z}</option>)}</select></Field>}
    {(kind === 'interval' || kind === 'daily') && <>
      <label className="check"><input type="checkbox" checked={useWindow} onChange={e => setUseWindow(e.target.checked)} /><span>Запускать только в окне работы</span></label>
      {useWindow && <div className="form-pair">
        <Field label="С"><input type="time" value={winStart} onChange={e => setWinStart(e.target.value)} /></Field>
        <Field label="До"><input type="time" value={winEnd} onChange={e => setWinEnd(e.target.value)} /></Field>
      </div>}
      {useWindow && <p className="field-hint">Окно может переходить через полночь, например 22:00-06:00. Запуски вне окна не создаются.</p>}
    </>}
    {kind !== 'none' && <div className="form-pair">
      <Field label="Если программа была выключена" hint={missed === 'all' ? 'Работает вместе с «Поставить в очередь».' : undefined}><select value={missed} onChange={e => setMissed(e.target.value)}>{Object.entries(MISSED).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
      <Field label="Если предыдущий запуск ещё идёт"><select value={overlap} onChange={e => setOverlap(e.target.value)}>{Object.entries(OVERLAP).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
    </div>}
    {kind !== 'none' && <label className="check"><input type="checkbox" checked={paused} onChange={e => setPaused(e.target.checked)} /><span>Пауза — не ставить новые запуски</span></label>}
    <Notice tone="info">Запуски выполняет worker на сервере: панель и браузер клиента для этого не нужны. Изменение расписания не затрагивает уже идущий запуск.{job.schedule_updated_by ? ` Последнее изменение: ${job.schedule_updated_by}, ${stamp(job.schedule_updated_at)}.` : ''}</Notice>
    {invalid && <p className="field-error" role="alert">{invalid}</p>}
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={requestClose}>Отмена</button><button className="btn primary" disabled={saving}><Busy busy={saving} busyText="Сохранение…">Сохранить расписание</Busy></button></div>
  </form>;
}

function JobRow({ job, runs, busy, act, edit, schedule, flash, open, toggle }) {
  const active = job.active_run;
  const running = active?.status === 'running' || job.status === 'running';
  const queued = active?.status === 'queued';
  const plan = scheduleText(job);
  const hint = hintFor(job.last_error);
  return <li className={`row job${flash ? ' flash' : ''}${open ? ' expanded' : ''}`}>
    <div className="row-main">
      <button className="row-toggle" aria-expanded={open} aria-controls={`job-${job.id}`} onClick={toggle} aria-label={`Подробнее: ${job.name}`}><ChevronDown size={16} /></button>
      <div className="row-title"><strong>{job.name}</strong>
        <span className="meta">{job.profile_name} · {job.urls.length} {plural(job.urls.length, 'страница', 'страницы', 'страниц')} · {job.headed ? 'видимый браузер' : 'фоновый режим'}</span>
        {plan && <span className="meta schedule-line"><CalendarClock size={12} aria-hidden="true" />{plan}{job.next_run_at ? ` · следующий ${shortStamp(job.next_run_at)}` : ''}</span>}</div>
      <div className="row-status"><Status kind="job" value={queued ? 'queued' : running ? 'running' : job.status} flash={flash} />{running && active && <span className="meta">стр. {active.pages_done} из {active.pages_total}</span>}</div>
      <div className="row-result">{job.last_run ? <><strong className="num">{job.last_count}</strong> <span className="meta">{plural(job.last_count, 'блок', 'блока', 'блоков')} · {shortStamp(job.last_run)}</span></> : <span className="meta">Ещё не запускалось</span>}</div>
      <div className="row-actions">
        {running || queued
          ? <button className="btn danger-ghost sm" disabled={busy || active?.stop_requested} onClick={() => act(`/jobs/${job.id}/stop`, {}, r => r.status === 'cancelled' ? 'Запуск отменён.' : 'Остановка запрошена.')}><Square size={13} />{queued ? 'Отменить' : active?.stop_requested ? 'Стоп…' : 'Стоп'}</button>
          : <button className="btn secondary sm" disabled={busy} onClick={() => act(`/jobs/${job.id}/run`, {}, `«${job.name}»: запуск поставлен в очередь.`)}><Play size={13} />Запустить</button>}
        <button className="icon-button" aria-label={`Расписание задания ${job.name}`} title="Расписание" onClick={schedule}><CalendarClock size={15} /></button>
        <button className="icon-button" aria-label={`Изменить задание ${job.name}`} title={running || queued ? 'Нельзя менять, пока запуск в очереди или идёт' : 'Изменить'} disabled={running || queued} onClick={edit}><Pencil size={15} /></button>
        <button className="icon-button" aria-label={`Дублировать задание ${job.name}`} title="Дублировать" disabled={busy} onClick={() => act(`/jobs/${job.id}/duplicate`, {}, 'Создана копия задания. Находки исходного задания не переносятся.')}><Copy size={15} /></button>
      </div>
    </div>
    {job.last_error && !open && <p className="row-error">{job.last_error.split('\n')[0]}{hint && <span className="hint-inline"> {hint}</span>}</p>}
    <Reveal open={open} id={`job-${job.id}`}>
      <dl className="detail-grid">
        <dt>Страницы</dt><dd><ul className="url-list">{job.urls.map(url => <li key={url}>{safeLink(url) ? <a href={safeLink(url)} target="_blank" rel="noreferrer">{url}<ExternalLink size={12} /></a> : url}</li>)}</ul></dd>
        <dt>Селектор</dt><dd><code>{job.selector || 'автоматический поиск'}</code></dd>
        <dt>Ожидание</dt><dd>{job.wait_seconds} с после загрузки</dd>
        <dt>Последний запуск</dt><dd>{stamp(job.last_run)}</dd>
        <dt>Создано</dt><dd>{stamp(job.created_at)}{job.updated_at && ` · изменено ${stamp(job.updated_at)}`}</dd>
        <dt>Расписание</dt><dd>{plan || 'только вручную'}{job.next_run_at ? ` · следующий запуск ${stamp(job.next_run_at)}` : ''}{job.schedule_updated_by && <span className="hint-block">Изменил: {job.schedule_updated_by}, {stamp(job.schedule_updated_at)}</span>}</dd>
        {job.login_selector && <><dt>Признак входа</dt><dd><code>{job.login_selector}</code></dd></>}
        {job.last_error && <><dt>Ошибки</dt><dd className="error-text">{job.last_error}{hint && <span className="hint-block">Что сделать: {hint}</span>}</dd></>}
        {runs.length > 0 && <><dt>Последние запуски</dt><dd><ul className="mini-list">{runs.slice(0, 5).map(r => <li key={r.id}><span>#{r.id} · {TRIGGERS[r.trigger]} · {shortStamp(r.started_at || r.queued_at)}<small>стр. {r.pages_done} из {r.pages_total} · блоков {r.found}</small></span><Status kind="run" value={r.status} /></li>)}</ul></dd></>}
      </dl>
    </Reveal>
  </li>;
}

export default function Jobs({ state, refresh, act, busy, go }) {
  const [modal, setModal] = React.useState(() => { const preset = prefs.get('jobs.createFor', null); prefs.set('jobs.createFor', null); return preset ? { create: preset } : null; });
  const [search, setSearch] = usePref('jobs.search', '');
  const [status, setStatus] = usePref('jobs.status', '');
  const [profile, setProfile] = usePref('jobs.profile', '');
  const [open, setOpen] = React.useState(() => new Set());
  const flash = useFlash(state.jobs, job => job.id, job => `${job.status}|${job.last_count}|${job.last_run}`);
  const q = search.trim().toLowerCase();
  const jobs = state.jobs.filter(job => (!status || job.status === status) && (!profile || String(job.profile_id) === String(profile))
    && (!q || `${job.name} ${job.profile_name} ${job.urls.join(' ')}`.toLowerCase().includes(q)));
  const filtered = Boolean(q || status || profile);
  const create = <button className="btn primary" disabled={!state.profiles.length} onClick={() => setModal({ create: true })}><Plus size={16} />Новое задание</button>;
  const toggle = id => setOpen(previous => { const next = new Set(previous); next.has(id) ? next.delete(id) : next.add(id); return next; });
  return <>
    <PageHeader title="Задания" actions={create}>Сбор рекламных блоков со страниц от имени выбранного профиля</PageHeader>
    {!state.profiles.length
      ? <Empty icon={ListChecks} title="Сначала нужен профиль" action={<button className="btn primary" onClick={() => go('profiles')}>Перейти к профилям</button>}>Задание собирает рекламу от имени профиля браузера с его cookies.</Empty>
      : !state.jobs.length
        ? <section className="card guide"><h2>Первый сбор за три шага</h2><ol className="steps">
            <li><strong>Профиль</strong><span>У каждого профиля отдельные cookies и вход.</span></li>
            <li><strong>Задание</strong><span>Адреса страниц и, при необходимости, CSS-селектор.</span></li>
            <li><strong>Запуск</strong><span>Блоки, ссылки и скриншот сохраняются в «Находках».</span></li></ol>
            <div className="guide-actions">{create}<span className="meta">Для проверки подставьте локальную тестовую страницу в форме.</span></div></section>
        : <>
          <div className="toolbar">
            <SearchInput label="Поиск заданий" value={search} onChange={setSearch} placeholder="Название, профиль или адрес" />
            <Select label="Статус" value={status} onChange={setStatus} options={[['', 'Все статусы'], ...Object.entries(STATUS.job).map(([key, [label]]) => [key, label])]} />
            <Select label="Профиль" value={profile} onChange={setProfile} options={[['', 'Все профили'], ...state.profiles.map(p => [String(p.id), p.name])]} />
          </div>
          <div className="list-head jobs-cols" aria-hidden="true"><span /><span>Задание</span><span>Статус</span><span>Последний результат</span><span /></div>
          {jobs.length
            ? <ul className="list">{jobs.map(job => <JobRow key={job.id} job={job} runs={state.runs.filter(r => r.job_id === job.id)} busy={busy} act={act}
                flash={flash.has(job.id)} open={open.has(job.id)} toggle={() => toggle(job.id)} edit={() => setModal({ edit: job })} schedule={() => setModal({ schedule: job.id })} />)}</ul>
            : <Empty compact title="Нет заданий с такими условиями" action={filtered && <button className="btn secondary" onClick={() => { setSearch(''); setStatus(''); setProfile(''); }}>Сбросить фильтры</button>} />}
          <p className="footnote">Запуски выполняет worker по очереди, одновременно — не больше {state.server?.max_browsers || 1} {plural(state.server?.max_browsers || 1, 'браузера', 'браузеров', 'браузеров')}. Профиль с открытым окном браузера в очередь не ставится.</p>
        </>}
    {modal?.create && <Modal title="Новое задание" close={() => setModal(null)}><JobForm state={state} refresh={refresh} presetProfile={modal.create === true ? null : modal.create} /></Modal>}
    {modal?.schedule && state.jobs.find(j => j.id === modal.schedule) && <Modal title="Расписание" subtitle={state.jobs.find(j => j.id === modal.schedule).name} close={() => setModal(null)}><ScheduleForm job={state.jobs.find(j => j.id === modal.schedule)} refresh={refresh} /></Modal>}
    {modal?.edit && <Modal title="Изменить задание" subtitle={`#${modal.edit.id} · история и находки сохраняются`} close={() => setModal(null)}><JobForm job={modal.edit} state={state} refresh={refresh} /></Modal>}
  </>;
}
