import React from 'react';
import { Plus, Play, Square, MoreHorizontal, CalendarClock, Pencil, Copy } from 'lucide-react';
import { Empty, Select, Modal } from './components';
import { JobForm, ScheduleForm } from './Jobs';
import { RunTable } from './AnalystRuns';
import { creativeDate } from './CatalogUI';
import { host, prefs, scheduleText } from './api';

export default function AnalystJobs({ state, refresh, act, busy, go }) {
  const [modal, setModal] = React.useState(() => { const id = prefs.get('jobs.createFor', null); prefs.set('jobs.createFor', null); return id ? { create: id } : null; });
  const [status, setStatus] = React.useState('');
  const runs = state.runs.filter(r => !status || r.status === status).slice(0, 5);
  const schedule = modal?.schedule && state.jobs.find(j => j.id === modal.schedule);
  return <div className="analyst-jobs">
    <header className="analyst-page-header"><div className="analyst-title-line"><h1>Задания и запуски</h1></div><div className="analyst-header-actions"><span className={`collector-state${state.server?.worker_alive ? ' ok' : ''}`}><i />{state.server?.worker_alive ? 'Сборщик отвечает' : 'Сборщик выключен'}</span><button className="btn primary" disabled={!state.profiles.length} onClick={() => setModal({ create: true })}><Plus size={19} />Новое задание</button></div></header>
    <section className="analyst-section"><div className="analyst-section-heading"><h2>Задания</h2></div>
      {!state.profiles.length ? <Empty compact title="Нужен профиль браузера" action={<button className="btn primary" onClick={() => go('profiles')}>Создать профиль</button>} /> : !state.jobs.length ? <Empty compact title="Заданий пока нет" action={<button className="btn primary" onClick={() => setModal({ create: true })}>Новое задание</button>} />
        : <div className="analyst-table-wrap"><table className="analyst-table jobs-table"><thead><tr><th>Название</th><th>Сайты</th><th>Расписание</th><th>Последний результат</th><th><span className="visually-hidden">Действия</span></th></tr></thead>
          <tbody>{state.jobs.map(job => {
            const active = job.active_run, working = ['queued', 'running'].includes(active?.status) || job.status === 'running';
            const domains = [...new Set(job.urls.map(host))];
            return <tr key={job.id}><td data-label="Название"><button className="job-name-button" onClick={() => setModal({ edit: job })} disabled={working}>{job.name}</button></td>
              <td data-label="Сайты"><span className="job-domains" title={job.urls.join('\n')}>{domains.join(' · ')}</span></td><td data-label="Расписание"><button className="schedule-text" onClick={() => setModal({ schedule: job.id })}>{scheduleText(job) || 'Только вручную'}</button></td>
              <td data-label="Последний результат"><span className={['error', 'partial', 'interrupted'].includes(job.status) ? 'job-result warning' : 'job-result'}>{job.last_run ? `${job.last_count} найдено · ${creativeDate(job.last_run)}` : 'Ещё не запускалось'}</span></td>
              <td className="job-table-actions">{working ? <button className="btn secondary sm" disabled={busy || active?.stop_requested} onClick={() => act(`/jobs/${job.id}/stop`, {}, 'Остановка запрошена.')}><Square size={12} />{active?.status === 'queued' ? 'Отменить' : 'Остановить'}</button> : <button className="btn primary sm" disabled={busy} onClick={() => act(`/jobs/${job.id}/run`, {}, 'Задание поставлено в очередь.')}><Play size={12} />Запустить</button>}
                <details className="job-context-menu"><summary aria-label={`Действия задания ${job.name}`}><MoreHorizontal size={18} /></summary><div>
                  <button onClick={() => setModal({ schedule: job.id })}><CalendarClock size={14} />Расписание</button><button disabled={working} onClick={() => setModal({ edit: job })}><Pencil size={14} />Изменить</button><button disabled={busy} onClick={() => act(`/jobs/${job.id}/duplicate`, {}, 'Копия задания создана.')}><Copy size={14} />Дублировать</button>
                </div></details>
              </td></tr>;
          })}</tbody></table></div>}
    </section>
    <section className="analyst-section runs-section"><div className="analyst-section-heading"><h2>Очередь и история запусков</h2><Select label="Состояние в истории" value={status} onChange={setStatus} options={[
      ['', 'Все статусы'], ['queued', 'В очереди'], ['running', 'Идёт'], ['done', 'Завершён'], ['error', 'Ошибка'], ['waiting_user', 'Нужна проверка вручную'],
    ]} /></div>
      {runs.length ? <RunTable items={runs} state={state} act={act} busy={busy} /> : <Empty compact title={state.runs.length ? 'Нет запусков с таким состоянием' : 'Запусков пока нет'} />}
    </section>
    <div className="jobs-footer"><span>Показано {runs.length} запусков</span>{state.runs.length > 5 && <button className="text-button" onClick={() => go('runs')}>Вся история</button>}</div>
    {modal?.create && <Modal title="Новое задание" close={() => setModal(null)}><JobForm state={state} refresh={refresh} presetProfile={modal.create === true ? null : modal.create} /></Modal>}
    {schedule && <Modal title="Расписание" subtitle={schedule.name} close={() => setModal(null)}><ScheduleForm job={schedule} refresh={refresh} /></Modal>}
    {modal?.edit && <Modal title="Изменить задание" close={() => setModal(null)}><JobForm job={modal.edit} state={state} refresh={refresh} /></Modal>}
  </div>;
}
