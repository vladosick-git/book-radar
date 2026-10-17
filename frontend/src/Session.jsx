import React from 'react';
import { Download, Upload, KeyRound } from 'lucide-react';
import { Field, Notice, Busy, Reveal } from './components';
import { Status } from './status';
import { api, shortStamp, stamp } from './api';

const MIN = 12;
const HELP = {
  empty: 'В профиле нет сохранённой сессии: сбор идёт без входа на сайты.',
  stored: 'Сессия сохранена. Вход на каждую площадку проверяется отдельно: в её карточке или при сборе с признаком входа.',
  imported: 'Сессия импортирована из файла. Результат проверки входа на Author.Today находится во вкладке «Почта и аккаунты».',
  active: 'Признак входа найден при последнем сборе: сессия рабочая.',
  expired: 'Признак входа не найден: сессия истекла. Войдите заново и перенесите сессию.',
};

async function download(profileId, passphrase) {
  const response = await fetch(`/api/profiles/${profileId}/session/export`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passphrase }) });
  if (!response.ok) {
    let detail = 'Не удалось экспортировать сессию.';
    try { const value = await response.json(); detail = typeof value.detail === 'string' ? value.detail : detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `profile-${profileId}.radar-session.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}

export function SessionPanel({ profile, server, refresh, toast }) {
  const [mode, setMode] = React.useState(null);
  const [phrase, setPhrase] = React.useState(''), [again, setAgain] = React.useState(''), [file, setFile] = React.useState(null);
  const [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false);
  const local = server?.windows_enabled !== false;
  const state = profile.session_state || 'unknown';
  const reset = () => { setPhrase(''); setAgain(''); setFile(null); setError(''); };
  const toggle = next => { reset(); setMode(mode === next ? null : next); };
  const tooShort = phrase.length < MIN;
  async function doExport(event) {
    event.preventDefault();
    if (tooShort || phrase !== again) return;
    setBusy(true); setError('');
    try { await download(profile.id, phrase); toast('Файл сессии сохранён. Храните его как пароль и удалите после импорта.'); reset(); setMode(null); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  async function doImport(event) {
    event.preventDefault();
    if (tooShort || !file) return;
    setBusy(true); setError('');
    try {
      const text = await file.text();
      const result = await api(`/profiles/${profile.id}/session/import`, { passphrase: phrase, file_text: text });
      await refresh();
      toast(`Сессия импортирована: cookies ${result.cookies}${result.domains.length ? `, сайты: ${result.domains.slice(0, 3).join(', ')}` : ''}.`);
      reset(); setMode(null);
    } catch (err) { setError(err.message); setPhrase(''); } finally { setBusy(false); }
  }
  return <section className="session-panel">
    <h3>Снимок браузера <Status kind="session" value={state} /></h3>
    {profile.at_session && <p className="meta">Последняя проверка AT: {['signed_in', 'session_verified'].includes(profile.at_session.status) ? 'вход подтверждён' : 'вход требует проверки'} · {stamp(profile.at_session.checked_at)}. Проверка конкретного сайта показана отдельно от сохранения снимка браузера.</p>}
    <p className="meta">{HELP[state] || HELP.stored}</p>
    <dl className="detail-grid compact">
      <dt>Последнее использование</dt><dd>{shortStamp(profile.last_used_at)}</dd>
      {profile.session_checked_at && <><dt>Проверка входа</dt><dd>{stamp(profile.session_checked_at)}</dd></>}
      {profile.session_imported_at && <><dt>Импорт</dt><dd>{stamp(profile.session_imported_at)}</dd></>}
    </dl>
    <div className="row-actions start">
      <button className="btn secondary sm" disabled={state === 'empty'} title={state === 'empty' ? 'Сначала войдите в окне профиля' : undefined} onClick={() => toggle('export')}><Download size={13} />Экспорт в файл</button>
      <button className="btn secondary sm" onClick={() => toggle('import')}><Upload size={13} />Импорт из файла</button>
    </div>
    <Reveal open={mode === 'export'}>
      <form className="session-form" onSubmit={doExport} noValidate>
        <Notice tone="info">{local ? 'Войдите на сайт в окне профиля, закройте окно и экспортируйте сессию. На сервере импортируйте файл в нужный профиль.' : 'Экспорт с сервера нужен только для резервной копии или переноса на другой сервер.'} Файл содержит cookies: передавайте его как пароль.</Notice>
        <Field label="Фраза для шифрования файла" hint={`Не короче ${MIN} символов. Программа её не сохраняет.`} error={phrase && tooShort ? `Нужно не меньше ${MIN} символов.` : ''}>
          <input type="password" autoComplete="new-password" value={phrase} onChange={e => setPhrase(e.target.value)} /></Field>
        <Field label="Повторите фразу" error={again && again !== phrase ? 'Фразы не совпадают.' : ''}><input type="password" autoComplete="new-password" value={again} onChange={e => setAgain(e.target.value)} /></Field>
        {error && mode === 'export' && <Notice tone="danger">{error}</Notice>}
        <div className="row-actions"><button className="btn primary sm" disabled={busy || tooShort || phrase !== again}><Busy busy={busy} busyText="Шифрование…"><KeyRound size={13} />Скачать файл сессии</Busy></button></div>
      </form>
    </Reveal>
    <Reveal open={mode === 'import'}>
      <form className="session-form" onSubmit={doImport} noValidate>
        <Notice tone="warning">Импорт заменит сохранённую сессию этого профиля. Во время сбора или при открытом окне импорт не выполняется.</Notice>
        <Field label="Файл сессии (.radar-session.json)"><input type="file" accept=".json,application/json" onChange={e => setFile(e.target.files?.[0] || null)} /></Field>
        <Field label="Фраза, которой файл зашифрован"><input type="password" autoComplete="off" value={phrase} onChange={e => setPhrase(e.target.value)} /></Field>
        {error && mode === 'import' && <Notice tone="danger">{error}</Notice>}
        <div className="row-actions"><button className="btn primary sm" disabled={busy || tooShort || !file}><Busy busy={busy} busyText="Проверка…"><Upload size={13} />Импортировать</Busy></button></div>
      </form>
    </Reveal>
  </section>;
}
