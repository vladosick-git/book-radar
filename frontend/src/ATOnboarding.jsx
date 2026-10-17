import React from 'react';
import { Field, Notice, Busy, useToast } from './components';
import { api, stamp } from './api';
import './ATOnboarding.css';

const labels = { not_started: 'Не начато', form_ready: 'Форма заполнена', awaiting_owner: 'Ожидает подтверждения на сайте',
  needs_owner: 'Требуется проверка владельца', email_unconfirmed: 'Ожидает подтверждения почты', signed_out: 'Вход не подтверждён', signed_in: 'Вход проверен', session_verified: 'Вход сохранился после перезапуска' };

export const isAT = account => {
  try { const url = new URL(account.registration_url); return account.service_kind === 'site' && url.protocol === 'https:' && url.hostname === 'author.today' && !url.username && !url.password; }
  catch { return false; }
};

function RemoteBrowser({ accountId, close, disabled, onBusy }) {
  const [revision, setRevision] = React.useState(0), [text, setText] = React.useState(''), [url, setUrl] = React.useState('');
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState(''), [zoom, setZoom] = React.useState(false);
  async function control(body) {
    if (busy || disabled) return;
    setBusy(true); onBusy(true); setError('');
    try { await api(`/onboarding/at/${accountId}/control`, body); setRevision(n => n + 1); }
    catch (err) { setError(err.message); } finally { setBusy(false); onBusy(false); setText(''); }
  }
  function click(event) {
    const box = event.currentTarget.getBoundingClientRect();
    control({ action: 'click', x: (event.clientX - box.left) * 1440 / box.width, y: (event.clientY - box.top) * 1000 / box.height });
  }
  return <section className="at-remote" aria-label="Браузер AT на сервере">
    <h3>Браузер AT на сервере</h3><p className="meta">Нажмите нужное поле на снимке, затем введите текст ниже. Проверки сайта выполняйте вручную. Снимок обновляется после действия.</p>
    <label className="check"><input type="checkbox" checked={zoom} onChange={e => setZoom(e.target.checked)} />Увеличить снимок до исходного размера</label>
    <div className="at-remote-screen"><img style={zoom ? { width: 1440, maxWidth: 'none' } : undefined} src={`/api/onboarding/at/${accountId}/screen?v=${revision}`} alt="Текущее окно Author.Today на сервере" onClick={click} /></div>
    <Field label="Текст в выбранное поле сайта"><input type="password" value={text} onChange={e => setText(e.target.value)} autoComplete="off" /></Field>
    <div className="bot-inline-actions"><button type="button" className="btn secondary" disabled={busy || disabled || !text} onClick={() => control({ action: 'type', text })}>Ввести текст</button>
      {['Tab', 'Enter', 'Backspace'].map(key => <button type="button" className="btn secondary" disabled={busy || disabled} key={key} onClick={() => control({ action: 'key', key })}>{key}</button>)}
      <button type="button" className="btn secondary" disabled={busy || disabled} onClick={() => control({ action: 'scroll', delta: 600 })}>Прокрутить вниз</button>
      <button type="button" className="btn secondary" disabled={busy || disabled} onClick={() => control({ action: 'scroll', delta: -600 })}>Вверх</button>
      <button type="button" className="btn secondary" disabled={busy || disabled} onClick={() => setRevision(n => n + 1)}>Обновить снимок</button></div>
    <Field label="Ссылка подтверждения из письма" optional hint="Только https://author.today/. Ссылка откроется в этом серверном профиле."><input type="url" value={url} onChange={e => setUrl(e.target.value)} autoComplete="off" /></Field>
    <div className="bot-inline-actions"><button type="button" className="btn secondary" disabled={busy || disabled || !url} onClick={() => control({ action: 'navigate', url })}>Открыть подтверждение</button><button type="button" className="btn ghost" disabled={busy || disabled} onClick={close}>Закрыть серверный браузер</button></div>
    {error && <Notice tone="danger">{error}</Notice>}
  </section>;
}

export default function ATOnboarding({ account, refresh, initialAction = 'register', initialEmail = '' }) {
  const toast = useToast();
  const [result, setResult] = React.useState(null), [action, setAction] = React.useState(initialAction);
  const [email, setEmail] = React.useState(account.identifier || initialEmail), [name, setName] = React.useState('');
  const [password, setPassword] = React.useState(''), [submit, setSubmit] = React.useState(false), [terms, setTerms] = React.useState(false);
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
  React.useEffect(() => {
    let active = true;
    api(`/onboarding/at/${account.id}`).then(value => { if (active) setResult(value); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [account.id]);
  async function run(path, body) {
    setBusy(true); setError('');
    try { const value = await api(`/onboarding/at/${account.id}/${path}`, body); setResult(value); await refresh(); toast(labels[value.status] || 'Операция завершена.'); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); setPassword(''); }
  }
  function send(event) {
    event.preventDefault();
    run('form', { action, email, display_name: name, password, submit, accept_terms: terms });
  }
  return <div className="form at-onboarding">
    <Notice tone="info">Один профиль Author.Today: регистрация или вход → подтверждение на сайте → проверка входа после перезапуска. Используйте свою действующую почту.</Notice>
    <form className="form" onSubmit={send}>
      <Field label="Действие"><select aria-label="Действие" value={action} disabled={busy} onChange={e => { setAction(e.target.value); setPassword(''); setSubmit(false); }}><option value="register">Регистрация нового аккаунта</option><option value="login">Вход в существующий аккаунт</option></select></Field>
      {action === 'register' && <Field label="Имя на сайте"><input value={name} onChange={e => setName(e.target.value)} required maxLength={80} autoComplete="off" /></Field>}
      <Field label="Электронная почта"><input type="email" value={email} onChange={e => setEmail(e.target.value)} required maxLength={160} autoComplete="off" /></Field>
      <Field label="Пароль" hint="Используется только для заполнения формы. Поле очищается после операции; пароль не записывается в журнал или базу программы."><input type="password" value={password} onChange={e => setPassword(e.target.value)} required maxLength={200} autoComplete={action === 'register' ? 'new-password' : 'current-password'} /></Field>
      <label className="check"><input type="checkbox" checked={submit} onChange={e => setSubmit(e.target.checked)} />Отправить заполненную форму на сайт</label>
      {action === 'register' && <label className="check"><input type="checkbox" checked={terms} onChange={e => setTerms(e.target.checked)} required={submit} />Я принимаю <a href="https://author.today/pages/terms" target="_blank" rel="noreferrer">условия Author.Today</a> и <a href="https://author.today/pages/confidental" target="_blank" rel="noreferrer">политику конфиденциальности</a></label>}
      <p className="meta">Письмо подтверждения, CAPTCHA и коды проверяются владельцем в окне сайта. После отправки программа не считает регистрацию завершённой.</p>
      <div className="form-actions"><button className="btn primary" disabled={busy || !email.trim() || !password || (action === 'register' && (!name.trim() || (submit && !terms)))}><Busy busy={busy} busyText="Открытие формы…">{submit ? 'Заполнить и отправить' : 'Открыть и заполнить'}</Busy></button></div>
    </form>
    {result && <Notice tone={['signed_in', 'session_verified'].includes(result.status) ? 'success' : 'warning'}><strong>{labels[result.status] || result.status}.</strong> {result.message}{result.checked_at && <p className="meta">Последняя проверка: {stamp(result.checked_at)}</p>}</Notice>}
    {error && <Notice tone="danger">{error}</Notice>}
    {result?.remote_open && <RemoteBrowser accountId={account.id} disabled={busy} onBusy={setBusy} close={() => run('remote-close', {})} />}
    <div className="form-actions">
      {result?.remote_available && !result.remote_open && <button type="button" className="btn secondary" disabled={busy} onClick={() => run('remote-open', {})}>Открыть браузер AT на сервере</button>}
      <button type="button" className="btn secondary" disabled={busy} onClick={() => run('check', { reopen: false })}>Проверить текущий вход</button>
      <button type="button" className="btn secondary" disabled={busy} onClick={() => run('check', { reopen: true })}>Закрыть и проверить сохранённый вход</button>
    </div>
    <p className="meta">Проверка текущего входа открывает настройки AT в отдельной вкладке. Проверка сохранённого входа сначала закрывает окно этого бота и сохраняет его сессию, затем запускает отдельный браузер без окна. Кнопки на сайте при проверке не нажимаются. Заполнение формы также использует отдельную вкладку.</p>
  </div>;
}
