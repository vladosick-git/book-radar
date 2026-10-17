import React from 'react';
import { api } from './api';
import { Field, Notice, Busy, Modal } from './components';
import { Disclosure } from './SimulationUI';

const labels = { queued: 'Ожидает создания', creating: 'Создаётся на сервере', created: 'Сервер подтвердил создание', unknown: 'Нужна сверка результата', failed: 'Не создан', attached: 'Готовый ящик добавлен' };

export default function PermanentMail({ data, profiles, busy, operation, perform, refresh, active = true, endpoint = '/simulation/mail', realProfiles = false }) {
  const [origin, setOrigin] = React.useState(''), [domain, setDomain] = React.useState(''), [key, setKey] = React.useState('');
  const [quota, setQuota] = React.useState(256), [error, setError] = React.useState(''), [revealed, setRevealed] = React.useState(null);
  const [email, setEmail] = React.useState(''), [name, setName] = React.useState(''), [provider, setProvider] = React.useState('Yandex');
  const [password, setPassword] = React.useState(''), [appPassword, setAppPassword] = React.useState(''), [recovery, setRecovery] = React.useState(''), [imap, setImap] = React.useState('imap.yandex.ru');
  const [query, setQuery] = React.useState(''), [limit, setLimit] = React.useState(20);
  const [profileId, setProfileId] = React.useState(String(profiles[0]?.id || ''));
  React.useEffect(() => { if (data?.settings) { setOrigin(data.settings.origin); setDomain(data.settings.domain); setQuota(data.settings.quota); } }, [data?.settings?.origin, data?.settings?.domain]);
  React.useEffect(() => { if (!revealed) return undefined; const t = setTimeout(() => setRevealed(null), 60000); return () => clearTimeout(t); }, [revealed]);
  React.useEffect(() => { if (!active) { setRevealed(null); setKey(''); setPassword(''); setAppPassword(''); } }, [active]);
  async function connect(event) {
    event.preventDefault(); setError('');
    try { await perform('mail/connect', { origin, domain, api_key: key, quota: Number(quota) }); await refresh(); }
    finally { setKey(''); }
  }
  async function attach(event) {
    event.preventDefault(); setError('');
    try {
      const result = await perform('mail/attach', { email, display_name: name, provider, password, app_password: appPassword || null, recovery_email: recovery, imap_host: imap, ...(realProfiles ? { profile_id: Number(profileId) } : {}) });
      if (result) { setEmail(''); setName(''); setRecovery(''); await refresh(); }
    } finally { setPassword(''); setAppPassword(''); }
  }
  async function verify(row, reconcile = false) {
    setError('');
    const result = await perform(`mail/${row.id}/${reconcile ? 'reconcile' : 'verify'}`, {});
    await refresh();
    if (result && !(result.verified || result.confirmed)) setError('Вход пока не подтверждён. Смотрите сообщение ящика; его сохранённый пароль не менялся.');
  }
  async function reveal(row) {
    setError('');
    try { setRevealed(await api(`${endpoint}/${row.id}/credentials`, {})); }
    catch (err) { setError(err.message); }
  }
  const counts = (status) => data?.accounts.filter(m => m.status === status).length || 0;
  const accounts = (data?.accounts || []).filter(m => `${m.email} ${m.display_name} ${m.provider}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="mail-workspace">
    <div className="section-intro"><div><h2>Почта для ботов</h2><p>Свои ящики и готовые адреса в одной защищённой базе</p></div></div>
    <Notice tone="warning">Внешняя почта: подключение Mailcow и создание ящиков обращаются к настоящему серверу. Эти операции не являются симуляцией. Нужны сервер, домен и API-ключ; бесплатное ПО не обеспечивает бесплатную инфраструктуру.</Notice>
    <p className="mail-connection-status">{data?.configured ? `Подключён домен ${data.settings.domain}.` : 'Сервер создания почты ещё не подключён.'} Готовых ящиков: <strong>{data?.accounts.filter(m => m.ready).length || 0}</strong>.</p>
    {error && <Notice tone="danger">{error}</Notice>}
    {data?.creating_task && <Notice tone="info">{data.stopping ? 'Остановка после текущего запроса. Новые запросы создания не отправляются.' : 'Постоянные ящики создаются последовательно. Можно остановить оставшуюся очередь.'}
      <div className="toolbar"><button type="button" className="btn secondary" disabled={busy || data.stopping} onClick={async () => { await perform('mail/stop', {}); await refresh(); }}>Остановить создание почты</button></div>
    </Notice>}
    <Disclosure title={data?.configured ? 'Подключение своего домена' : 'Подключить почту на своём домене'} caption={data?.configured ? data.settings.domain : 'Mailcow · нужны работающий сервер, домен и API-ключ'} defaultOpen={!data?.configured}>
      <p className="meta">Укажите уже работающий сервер Mailcow и домен. Ключ хранится зашифрованно. Эта форма проверяет подключение и не устанавливает почтовый сервер.</p>
      <form className="form compact-form" onSubmit={connect}>
        <div className="form-pair"><Field label="Адрес сервера Mailcow"><input type="url" value={origin} onChange={e => setOrigin(e.target.value)} placeholder="https://mail.example.org" required /></Field>
          <Field label="Домен постоянной почты"><input value={domain} onChange={e => setDomain(e.target.value)} placeholder="example.org" required /></Field></div>
        <div className="form-pair"><Field label="Ключ API Mailcow"><input type="password" value={key} onChange={e => setKey(e.target.value)} required minLength="8" maxLength="500" autoComplete="off" /></Field>
          <Field label="Размер нового ящика, МБ"><input type="number" value={quota} onChange={e => setQuota(e.target.value)} min="1" max="10240" required /></Field></div>
        <div className="toolbar"><button className="btn secondary" disabled={busy || data?.running}><Busy busy={busy && operation === 'mail/connect'} busyText="Проверка подключения…">Проверить и подключить Mailcow</Busy></button></div>
      </form>
    </Disclosure>
    <Disclosure title="Добавить готовый ящик" caption="Яндекс, Mail.ru и другие сервисы">
      <p className="meta">Добавьте существующий адрес. Данные сохранятся зашифрованно. Для проверки входа в Яндекс/Mail.ru может потребоваться пароль приложения.</p>
      <form className="form compact-form" onSubmit={attach}>
        {realProfiles && <Field label="Бот для готовой почты"><select value={profileId} onChange={e => setProfileId(e.target.value)} required><option value="">Выберите бота</option>{profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>}
        <div className="form-pair"><Field label="Провайдер готовой почты"><select aria-label="Провайдер готовой почты" value={provider} onChange={e => { setProvider(e.target.value); setImap(({ Yandex: 'imap.yandex.ru', 'Mail.ru': 'imap.mail.ru' })[e.target.value] || ''); }}>{['Yandex', 'Mail.ru', 'Tuta', 'Proton', 'Zoho', 'Other'].map(p => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Имя владельца ящика"><input value={name} onChange={e => setName(e.target.value)} maxLength="100" /></Field></div>
        <Field label="Адрес готовой постоянной почты"><input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoComplete="off" /></Field>
        <div className="form-pair"><Field label="Пароль постоянной почты"><input type="password" value={password} onChange={e => setPassword(e.target.value)} required maxLength="500" autoComplete="off" /></Field>
          <Field label="Пароль приложения для IMAP" optional><input aria-label="Пароль приложения для IMAP" type="password" value={appPassword} onChange={e => setAppPassword(e.target.value)} maxLength="500" autoComplete="off" /></Field></div>
        <div className="form-pair"><Field label="Резервная почта" optional><input aria-label="Резервная почта" type="email" value={recovery} onChange={e => setRecovery(e.target.value)} maxLength="254" autoComplete="off" /></Field>
          <Field label="Сервер IMAP для проверки" optional><input aria-label="Сервер IMAP для проверки" value={imap} onChange={e => setImap(e.target.value)} placeholder="imap.example.org" /></Field></div>
        <div className="toolbar"><button className="btn secondary" disabled={busy || data?.running}>Сохранить готовый ящик</button></div>
      </form>
    </Disclosure>
    {data?.accounts.length > 0 && <Disclosure title="Почтовые ящики" caption={`${data.accounts.length} в базе · пароли скрыты`} defaultOpen>
      <p className="meta">Ожидают {counts('queued')} · создаются {counts('creating')} · готовы {data.accounts.filter(m => m.ready).length} · нужна сверка {counts('unknown')} · ошибки {counts('failed')}.</p>
      <Field label="Поиск почтового ящика"><input type="search" value={query} onChange={e => { setQuery(e.target.value); setLimit(20); }} placeholder="Адрес, имя или провайдер" /></Field>
      <ul className="mail-account-list">{accounts.slice(0, limit).map(m => <li key={m.id}><Disclosure className="mail-account" title={m.display_name || m.email} caption={`${m.email} · ${m.ready ? 'Вход подтверждён' : labels[m.status] || m.status}`}>
        <p>{m.email} · {m.provider}</p><p className="meta">Логин: {m.login} · {m.profile_id ? (profiles.find(p => p.id === m.profile_id)?.name || `Бот #${m.profile_id}`) : 'Ещё не закреплён за ботом'}</p>
        <p role="status">{labels[m.status] || m.status} · {m.ready ? 'Вход проверен' : 'Вход ещё не подтверждён'}</p>
        {m.message && <p className="meta">{m.message}</p>}
        <div className="toolbar"><button type="button" className="btn ghost sm" onClick={() => reveal(m)}>Показать данные входа</button>
          {m.status === 'unknown' ? <button type="button" className="btn secondary sm" disabled={busy || data.running} onClick={() => verify(m, true)}>Сверить результат</button>
            : <button type="button" className="btn secondary sm" disabled={busy || data.running || !m.imap_host || !['created', 'attached'].includes(m.status)} onClick={() => verify(m)}>Проверить вход IMAP</button>}
          {realProfiles && m.ready && m.mailbox_id && <button type="button" className="btn secondary sm" disabled={busy} onClick={async () => { await perform(`mail/${m.id}/at-preparation`, {}); await refresh(); }}>Подготовить AT этому боту</button>}</div>
      </Disclosure></li>)}</ul>
      {accounts.length === 0 && <p className="meta">По этому запросу ящики не найдены.</p>}
      {accounts.length > limit && <button type="button" className="btn secondary" onClick={() => setLimit(n => n + 20)}>Показать ещё ящики · всего {accounts.length}</button>}
    </Disclosure>}
    {revealed && <Modal title="Данные входа в почту" subtitle="Автоматически скроются через минуту" close={() => setRevealed(null)}>
      <div className="form"><Field label="Почта / логин"><input value={revealed.email} readOnly /></Field>
        <Field label="Пароль ящика"><input value={revealed.password} readOnly autoComplete="off" /></Field>
        {revealed.app_password && <Field label="Пароль приложения"><input value={revealed.app_password} readOnly autoComplete="off" /></Field>}
        {revealed.recovery_email && <Field label="Резервная почта"><input value={revealed.recovery_email} readOnly /></Field>}
        <div className="toolbar"><button className="btn secondary" onClick={() => setRevealed(null)}>Скрыть данные</button></div>
      </div>
    </Modal>}
  </div>;
}
