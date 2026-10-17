import React from 'react';
import { Send, Save, KeyRound } from 'lucide-react';
import { Field, Notice, Busy, Spinner, useModalClose, useToast } from './components';
import { api, shortStamp, stamp } from './api';

const EVENT_HINTS = {
  failure: 'запуск завершился ошибкой и автоматических повторов не осталось',
  user: 'сайт просит проверку или вход — без человека сбор не продолжится',
  recovered: 'первый успешный запуск после отправленной проблемы',
  done: 'каждый завершённый запуск; при частом расписании будет много сообщений',
};
const DELIVERY = { sent: 'доставлено', failed: 'не доставлено', suppressed: 'повтор не отправлен', skipped: 'пропущено' };

// Stage 17: Telegram channel. The bot token is write-only: the panel never receives it back.
export default function Notifications() {
  const requestClose = useModalClose(), toast = useToast();
  const [state, setState] = React.useState(null), [error, setError] = React.useState(''), [busy, setBusy] = React.useState('');
  const [form, setForm] = React.useState({ enabled: false, chat_id: '', events: [], token: '', clear_token: false });
  const load = React.useCallback(() => api('/notifications').then(data => {
    setState(data);
    setForm(previous => ({ ...previous, enabled: data.settings.enabled, chat_id: data.settings.chat_id, events: data.settings.events, token: '', clear_token: false }));
  }).catch(e => setError(e.message)), []);
  React.useEffect(() => { load(); }, [load]);
  if (!state) return <div className="form">{error ? <Notice tone="danger">{error}</Notice> : <Spinner label="Загрузка настроек…" />}</div>;
  const set = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const toggleEvent = key => set('events', form.events.includes(key) ? form.events.filter(e => e !== key) : [...form.events, key]);
  async function save() {
    setBusy('save'); setError('');
    try {
      const body = { enabled: form.enabled, chat_id: form.chat_id.trim(), events: form.events, clear_token: form.clear_token };
      if (form.token.trim()) body.token = form.token.trim();
      await api('/notifications/settings', body);
      await load();
      toast(form.enabled ? 'Уведомления включены.' : 'Настройки уведомлений сохранены.');
    } catch (e) { setError(e.message); } finally { setBusy(''); }
  }
  async function test() {
    setBusy('test'); setError('');
    try { await api('/notifications/test', {}); toast('Тестовое сообщение отправлено в Telegram.'); await load(); }
    catch (e) { setError(e.message); await load(); } finally { setBusy(''); }
  }
  const tokenSet = state.settings.token_set && !form.clear_token;
  return <div className="form notify-form">
    <Notice tone="info">Нужен бот Telegram: создайте его у @BotFather и скопируйте токен. Chat id — ваш числовой id (его покажет, например, @userinfobot) или id группы, куда добавлен бот. Токен хранится зашифрованным и в панель больше не возвращается.</Notice>
    {error && <Notice tone="danger" onClose={() => setError('')}>{error}</Notice>}
    <label className="check"><input type="checkbox" checked={form.enabled} onChange={e => set('enabled', e.target.checked)} />Отправлять уведомления</label>
    <Field label="Токен бота" hint={tokenSet ? 'Токен сохранён. Оставьте поле пустым, чтобы не менять его.' : 'Вид: 123456789:AA… — целиком из @BotFather.'} optional={tokenSet}>
      <input type="password" autoComplete="off" spellCheck={false} value={form.token} placeholder={tokenSet ? '•••••••• сохранён' : '123456789:AA…'} onChange={e => set('token', e.target.value)} />
    </Field>
    {state.settings.token_set && <label className="check"><input type="checkbox" checked={form.clear_token} onChange={e => set('clear_token', e.target.checked)} />Удалить сохранённый токен<small>уведомления выключатся</small></label>}
    <Field label="Chat id получателя" hint="Например -1001234567890 для группы или @channel для канала.">
      <input value={form.chat_id} spellCheck={false} onChange={e => set('chat_id', e.target.value)} placeholder="-1001234567890" />
    </Field>
    <fieldset className="checklist"><legend>О чём сообщать</legend>
      {Object.entries(state.events).map(([key, label]) => <label key={key} className="check"><input type="checkbox" checked={form.events.includes(key)} onChange={() => toggleEvent(key)} />{label}<small>{EVENT_HINTS[key]}</small></label>)}
    </fieldset>
    <p className="meta">Одна и та же проблема задания отправляется один раз: повторы не шлются, пока задание не завершится успешно.</p>
    {state.recent.length > 0 && <section className="detail-section"><h3>Последние отправки</h3>
      <ul className="mini-list">{state.recent.slice(0, 8).map(row => <li key={row.id}><span><span className="truncate">{state.events[row.event] || 'Проверка'}{row.job_name ? ` · ${row.job_name}` : ''}{row.run_id ? ` · запуск #${row.run_id}` : ''}</span>
        <small title={row.detail}>{row.detail}</small></span><span className={`delivery ${row.status}`} title={stamp(row.at)}>{DELIVERY[row.status] || row.status} · {shortStamp(row.at)}</span></li>)}</ul></section>}
    <div className="form-actions">
      <button className="btn ghost" onClick={requestClose}>Закрыть</button>
      <button className="btn secondary" disabled={!!busy || !state.settings.token_set} onClick={test} title={state.settings.token_set ? '' : 'Сначала сохраните токен и chat id'}><Busy busy={busy === 'test'} busyText="Отправка…"><Send size={15} />Проверить</Busy></button>
      <button className="btn primary" disabled={!!busy} onClick={save}><Busy busy={busy === 'save'} busyText="Сохранение…">{form.token ? <KeyRound size={15} /> : <Save size={15} />}Сохранить</Busy></button>
    </div>
  </div>;
}
