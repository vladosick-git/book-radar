import React from 'react';
import { BookOpen, Users } from 'lucide-react';
import { Field, Notice, Busy, Empty } from './components';
import { api, plural } from './api';
import { Disclosure } from './SimulationUI';

const statuses = { running: 'Чтение идёт', done: 'Завершено', failed: 'Прервано' };

export default function SimulationReading({ profiles, chosen, busy, operation, perform, openBots }) {
  const [data, setData] = React.useState(null), [error, setError] = React.useState('');
  const [book, setBook] = React.useState('cloud-library'), [pages, setPages] = React.useState(3);
  const [minSeconds, setMinSeconds] = React.useState(60), [maxSeconds, setMaxSeconds] = React.useState(120);
  const [resume, setResume] = React.useState(true), [showBrowser, setShowBrowser] = React.useState(false);
  React.useEffect(() => {
    let active = true;
    const update = () => api('/simulation/reading').then(r => { if (active) setData(r); }).catch(e => { if (active) setError(e.message); });
    update(); const timer = setInterval(update, 2000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const ready = chosen.length > 0 && chosen.every(id => profiles.find(p => p.id === id)?.status === 'registered');
  async function start(event) {
    event.preventDefault();
    await perform('read', { profile_ids: chosen, book, pages: Number(pages), min_seconds_per_page: Number(minSeconds), max_seconds_per_page: Number(maxSeconds), resume, show_browser: showBrowser });
    try { setData(await api('/simulation/reading')); } catch (err) { setError(err.message); }
  }
  return <div className="reading-workspace">
    <div className="section-intro"><div><h2>Чтение книг</h2><p>Прокрутка страниц, закладки и сбор случайных строк</p></div><button className="btn secondary sm" onClick={openBots}><Users size={15} aria-hidden="true" />Выбрать ботов · {chosen.length}</button></div>
    {error && <Notice tone="danger">{error}</Notice>}
    <form className="form reading-form" onSubmit={start}>
      <div className="form-pair"><Field label="Книга"><select aria-label="Книга" value={book} onChange={e => setBook(e.target.value)}>{Object.entries(data?.books || {}).map(([key, value]) => <option key={key} value={key}>{value.title} · {value.pages} страниц</option>)}</select></Field>
        <Field label="Страниц за запуск"><input type="number" min="1" max="6" value={pages} onChange={e => setPages(e.target.value)} required /></Field></div>
      <div className="reading-time"><BookOpen size={19} aria-hidden="true" /><div><strong>{minSeconds}–{maxSeconds} секунд на странице</strong><p>Время выбирается заново для каждой страницы. Прокрутка распределена по всему времени.</p></div></div>
      <Disclosure title="Настроить чтение" caption="Время на странице и окно браузера">
        <div className="form-pair"><Field label="От, секунд на страницу"><input type="number" min="60" max="600" value={minSeconds} onChange={e => setMinSeconds(e.target.value)} required /></Field>
          <Field label="До, секунд на страницу"><input type="number" min={Math.max(60, Number(minSeconds))} max="600" value={maxSeconds} onChange={e => setMaxSeconds(e.target.value)} required /></Field></div>
        <label className="check"><input type="checkbox" checked={showBrowser} onChange={e => setShowBrowser(e.target.checked)} />Показывать окно чтения</label>
        <p className="meta">Выбранные боты читают последовательно. Несколько страниц и ботов займут несколько минут.</p>
      </Disclosure>
      <label className="check"><input type="checkbox" checked={resume} onChange={e => setResume(e.target.checked)} />Продолжать с сохранённой закладки</label>
      <div className="toolbar"><button className="btn primary" disabled={busy || !ready || !data}><Busy busy={busy && operation === 'read'} busyText="Чтение страниц…"><BookOpen size={15} aria-hidden="true" />Начать чтение · {chosen.length} {plural(chosen.length, 'бот', 'бота', 'ботов')}</Busy></button></div>
      {!ready && <p className="meta">На вкладке «Боты» выберите аккаунты с подтверждённым входом.</p>}
    </form>
    <Disclosure title="Сохранённые закладки" caption={`${data?.bookmarks.length || 0}`}>
      {data?.bookmarks.length ? <ul className="workbench-events">{data.bookmarks.map(b => <li key={`${b.profile_id}-${b.book}`}><span>{profiles.find(p => p.id === b.profile_id)?.name || `Бот #${b.profile_id}`}</span><strong>{data.books[b.book]?.title} · стр. {b.last_page}</strong></li>)}</ul> : <p className="meta">Закладки появятся после чтения.</p>}
    </Disclosure>
    <Disclosure title="Результаты чтения" caption={`${data?.runs.length || 0} сеансов · страницы и собранные строки`}>
      {data?.runs.length ? <div className="reading-results">{data.runs.slice(0, 6).map(r => <Disclosure key={r.id} title={r.profile_name} caption={`${statuses[r.status] || r.status} · ${r.completed} из ${r.pages_requested} стр.`}>
        <p><strong>{data.books[r.book]?.title}</strong> · начало со страницы {r.start_page}</p>
        <p className="meta">Время на страницу {r.seconds_per_page}–{r.max_seconds_per_page ?? r.seconds_per_page} сек.</p>
        {r.message && <p className="meta">{r.message}</p>}
        <Disclosure title="Страницы и прокрутка" caption={`${r.pages.length}`}><ol className="reading-pages">{r.pages.map(p => <li key={p.page}>
          <p>Страница {p.page}: {p.completed_at ? `${(p.elapsed_ms / 1000).toFixed(1)} сек. · прокруток ${p.scroll_events} · ${Math.round(p.scroll_fraction * 100)}%` : 'открыта, ещё не завершена'}{p.target_seconds != null && ` · выбрано ${p.target_seconds.toFixed(1)} сек.`}</p>
          {p.sample_text && <blockquote>{p.sample_text}</blockquote>}
          {p.screenshot && <a href={`/api/simulation/reading/${r.id}/screenshot/${p.page}`} target="_blank" rel="noreferrer">Снимок страницы</a>}
        </li>)}</ol></Disclosure>
        {r.pages.some(p => p.sample_text) && <Disclosure title="Собранный текст"><blockquote className="reading-collage">{r.pages.map(p => p.sample_text).filter(Boolean).join(' ')}</blockquote></Disclosure>}
      </Disclosure>)}</div> : <Empty compact title="Чтение ещё не запускалось">Здесь появятся пройденные страницы и выбранные строки.</Empty>}
    </Disclosure>
  </div>;
}
