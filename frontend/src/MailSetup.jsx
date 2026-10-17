import React from 'react';
import { Field, Notice, Busy, useToast } from './components';
import { api } from './api';
import ATOnboarding from './ATOnboarding';

export default function MailSetup({ state, refresh }) {
  const toast = useToast();
  const [profile, setProfile] = React.useState(String(state.profiles[0]?.id || ''));
  const [email, setEmail] = React.useState(''), [stage, setStage] = React.useState(null);
  const [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false);
  const [at, setAT] = React.useState(null);
  const mailbox = state.mailboxes.find(m => String(m.profile_id) === profile && m.provider === 'Yandex');
  const account = at && state.accounts.find(a => a.id === at);
  const selected = state.profiles.find(p => String(p.id) === profile);

  async function run(operation) {
    setBusy(true); setError('');
    try { await operation(); await refresh(); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  function openRegistration() {
    run(async () => {
      const prepared = await api('/mailboxes/setup', { profile_id: Number(profile) });
      const result = await api(`/accounts/${prepared.account_id}/open`, {});
      setStage(result.loaded ? 'opened' : 'load_failed');
    });
  }
  function attach(event) {
    event.preventDefault();
    run(async () => {
      await api('/mailboxes/attach', { profile_id: Number(profile), provider: 'Yandex', email });
      setStage('attached'); toast('Адрес привязан вручную. Доставка писем ещё не проверена.');
    });
  }
  function prepareAT() {
    run(async () => {
      const result = await api(`/mailboxes/${mailbox.id}/at-preparation`, {});
      setAT(result.account_id);
    });
  }
  return <div className="form">
    <Notice tone="info">Почта владельца → подключение адреса → подготовка Author.Today. Если ящик уже есть, начните с шага 2. Новый домен и платный тариф для этого сценария не нужны.</Notice>
    <Field label="Профиль"><select aria-label="Профиль для почты" value={profile} disabled={busy} onChange={e => { setProfile(e.target.value); setEmail(''); setStage(null); setAT(null); setError(''); }}>{state.profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>
    <section>
      <h3>1. Создать почту Яндекса вручную</h3>
      <p className="meta">Откроется официальная регистрация в отдельном окне профиля. Имя, пароль, согласие и подтверждения вводит владелец на сайте. Яндекс может потребовать телефон; автоматического создания серии ящиков здесь нет.</p>
      <button className="btn secondary" type="button" disabled={busy || !profile || selected?.collecting || state.server?.windows_enabled === false} onClick={openRegistration}><Busy busy={busy} busyText="Открытие…">Открыть регистрацию Яндекса</Busy></button>
      {stage === 'opened' && <p role="status">Окно открыто. Завершите регистрацию и проверьте вход в почту. Открытая страница не подтверждает создание ящика.</p>}
      {stage === 'load_failed' && <Notice tone="warning">Окно открыто, но страница не загрузилась. Проверьте его и продолжите вручную.</Notice>}
    </section>
    <section>
      <h3>2. Подключить готовый адрес</h3>
      {mailbox ? <Notice tone="info"><strong>{mailbox.email}</strong> — подключён владельцем. Пароль и доставка писем программой не проверялись.</Notice> : <form className="form" onSubmit={attach}>
        <Field label="Готовый адрес Яндекс Почты" hint="Укажите существующий адрес, к которому у вас есть доступ. Пароль почты в этой форме не нужен."><input type="email" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} autoComplete="off" /></Field>
        <button className="btn primary" disabled={busy || !profile || !email.trim()}><Busy busy={busy} busyText="Подключение…">Привязать готовый адрес</Busy></button>
      </form>}
    </section>
    <section>
      <h3>3. Подготовить вход на Author.Today</h3>
      <p className="meta">Адрес подставится в запись этого профиля. Далее регистрация или вход и проверка сохранённой сессии выполняются отдельно.</p>
      <button className="btn secondary" type="button" disabled={busy || !mailbox || state.server?.windows_enabled === false} onClick={prepareAT}>Перейти к подготовке AT</button>
      {account && <ATOnboarding key={account.id} account={account} refresh={refresh} />}
    </section>
    {error && <Notice tone="danger">{error}</Notice>}
  </div>;
}
