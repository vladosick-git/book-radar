import React from 'react';
import { api } from './api';
import { Field, Notice, Busy, useModalClose, useToast } from './components';

export default function CollectionSetup({ ids, state, refresh }) {
  const close = useModalClose(), toast = useToast();
  const [name, setName] = React.useState('Книжная реклама');
  const [urls, setUrls] = React.useState(''), [wait, setWait] = React.useState(15);
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState('');
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await api('/collection/setup', { profile_ids: ids, name,
        urls: urls.split('\n').map(value => value.trim()).filter(Boolean), wait_seconds: Number(wait) });
      await refresh(); toast(`Создано заданий: ${result.created.length}; уже были настроены: ${result.existing.length}.`); close();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  return <form className="form" onSubmit={save}>
    <p>Боты: <strong>{state.profiles.filter(p => ids.includes(p.id)).map(p => p.name).join(', ')}</strong></p>
    <Field label="Название сбора"><input value={name} onChange={e => setName(e.target.value)} maxLength={60} required /></Field>
    <Field label="Страницы с рекламой" hint="Один адрес на строку, до 20. Укажите страницы, где видны нужные объявления."><textarea value={urls} onChange={e => setUrls(e.target.value)} rows={4} placeholder="https://…" required /></Field>
    <Field label="Ожидание загрузки рекламы, с" hint="От 2 до 30 секунд. Это ожидание рекламных блоков, не время чтения главы."><input type="number" value={wait} onChange={e => setWait(e.target.value)} min={2} max={30} required /></Field>
    <Notice tone="info">У каждого выбранного бота будет своё задание. Совпадающая настройка используется повторно. Сбор запускается кнопкой «Запустить выбранных»; расписание настраивается в задании.</Notice>
    {error && <Notice tone="danger">{error}</Notice>}
    <div className="form-actions"><button type="button" className="btn ghost" onClick={close}>Отмена</button><button className="btn primary" disabled={busy}><Busy busy={busy} busyText="Сохранение…">Настроить сбор</Busy></button></div>
  </form>;
}
