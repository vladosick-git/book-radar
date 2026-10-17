import React from 'react';
import { Lock } from 'lucide-react';
import { Notice, Busy } from './components';
import { api } from './api';

export default function Login({ onDone }) {
  const [password, setPassword] = React.useState(''), [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false);
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    try { await api('/login', { password }); setPassword(''); onDone(); }
    catch (err) { setError(err.message); setPassword(''); }
    finally { setBusy(false); }
  }
  return <div className="login-screen">
    <form className="card login-card view" onSubmit={submit}>
      <div className="brand"><span className="brand-mark" aria-hidden="true" /><span>Книжный радар</span></div>
      <h1><Lock size={18} aria-hidden="true" />Вход в панель</h1>
      <p className="meta">Сервер продолжает работу и без открытой панели. Вход нужен, чтобы видеть результаты и управлять заданиями.</p>
      <div className="field"><label><span className="field-label-text">Пароль администратора</span>
        <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} autoFocus required /></label></div>
      {error && <Notice tone="danger">{error}</Notice>}
      <button className="btn primary" disabled={busy || !password}><Busy busy={busy} busyText="Проверка…">Войти</Busy></button>
    </form>
  </div>;
}
