import React from 'react';
import { ChevronDown, Plus } from 'lucide-react';
import { Field, Notice, Busy, Empty, SearchInput, Select } from './components';
import { Disclosure, ProfileStatus } from './SimulationUI';

export default function SimulationProfiles({ data, chosen, setChosen, busy, operation, perform, openReading }) {
  const [name, setName] = React.useState(''), [city, setCity] = React.useState('tver'), [device, setDevice] = React.useState('android');
  const [query, setQuery] = React.useState(''), [filter, setFilter] = React.useState('all'), [limit, setLimit] = React.useState(20);
  const [expanded, setExpanded] = React.useState(() => new Set());
  const profiles = data?.profiles || [];
  const needle = query.trim().toLocaleLowerCase();
  const visible = profiles.filter(p => (filter === 'all' || (filter === 'selected' ? chosen.includes(p.id) : p.status === filter)) &&
    `${p.name} ${p.email || ''} ${data?.cities[p.city]?.name || ''} ${data?.devices[p.device]?.name || ''}`.toLocaleLowerCase().includes(needle));
  const toggle = id => setExpanded(old => { const next = new Set(old); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const canRead = chosen.length > 0 && chosen.every(id => profiles.find(p => p.id === id)?.status === 'registered');
  return <div className="profiles-workspace">
    <Disclosure title="Добавить бота вручную" caption="Самостоятельно выбрать город и устройство">
      <form className="form compact-form" onSubmit={async e => { e.preventDefault(); const r = await perform('profiles', { profiles: [{ name, city, device }] }); if (r) setName(''); }}>
        <Field label="Название бота"><input value={name} required maxLength={80} onChange={e => setName(e.target.value)} placeholder="Например: Иван · Москва" /></Field>
        <div className="form-pair"><Field label="Город"><select aria-label="Город" value={city} onChange={e => setCity(e.target.value)}>{Object.entries(data?.cities || {}).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></Field>
          <Field label="Устройство"><select aria-label="Устройство" value={device} onChange={e => setDevice(e.target.value)}>{Object.entries(data?.devices || {}).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></Field></div>
        <div className="toolbar"><button className="btn primary" disabled={busy || !name.trim()}><Plus size={15} aria-hidden="true" />Добавить бота</button></div>
      </form>
    </Disclosure>
    <Disclosure title="Список ботов" caption={`Всего ${profiles.length} · вход подтверждён у ${data?.registered || 0}`} defaultOpen>
      <div className="toolbar profile-search">
        <SearchInput value={query} onChange={value => { setQuery(value); setLimit(20); }} label="Поиск ботов" placeholder="Имя, почта, город или устройство" />
        <Select label="Состояние ботов" value={filter} onChange={value => { setFilter(value); setLimit(20); }} options={[
          ['all', 'Все боты'], ['registered', 'Вход подтверждён'], ['planned', 'Подготовлены'], ['failed', 'Нужна проверка'], ['selected', 'Выбранные'],
        ]} />
        {expanded.size > 0 && <button className="btn ghost sm" onClick={() => setExpanded(new Set())}>Свернуть подробности</button>}
      </div>
      {visible.length ? <>
        <div className="profile-table-head" aria-hidden="true"><span>Бот и устройство</span><span>Почта</span><span>Состояние</span></div>
        <ul className="list bot-list">{visible.slice(0, limit).map(p => {
          const open = expanded.has(p.id), observed = p.observed || {};
          return <li key={p.id} className={`bot-row${chosen.includes(p.id) ? ' selected' : ''}`}>
            <div className="bot-row-summary">
              <label className="bot-check"><input type="checkbox" aria-label={`Выбрать ${p.name}`} disabled={busy || (!chosen.includes(p.id) && chosen.length >= 10)} checked={chosen.includes(p.id)} onChange={e => setChosen(old => e.target.checked ? [...old, p.id] : old.filter(id => id !== p.id))} /></label>
              <button className="bot-identity" aria-expanded={open} aria-controls={`bot-detail-${p.id}`} onClick={() => toggle(p.id)}><strong>{p.name}</strong><span>{data?.cities[p.city]?.name} · {data?.devices[p.device]?.name}</span></button>
              <span className="bot-email">{p.email || 'Почта не назначена'}</span>
              <ProfileStatus status={p.status} />
              <button className="icon-button bot-toggle" aria-label={`${open ? 'Свернуть' : 'Подробности'}: ${p.name}`} aria-expanded={open} aria-controls={`bot-detail-${p.id}`} onClick={() => toggle(p.id)}><ChevronDown size={17} aria-hidden="true" /></button>
            </div>
            <div id={`bot-detail-${p.id}`} className="bot-detail" hidden={!open}>
              <dl className="bot-specs"><div><dt>Почта</dt><dd>{p.email || 'Не назначена'}</dd></div><div><dt>Среда браузера</dt><dd>{data?.devices[p.device]?.name} · эмуляция</dd></div>
                <div><dt>Размер экрана</dt><dd>{observed.width ? `${observed.width} × ${observed.height}` : 'После первого входа'}</dd></div><div><dt>Часовой пояс</dt><dd>{observed.timezone || data?.cities[p.city]?.timezone || 'По городу'}</dd></div>
                <div><dt>Координаты</dt><dd>{observed.latitude != null ? `${observed.latitude}, ${observed.longitude}` : 'По городу'}</dd></div><div><dt>Сенсорный экран</dt><dd>{observed.touch != null ? observed.touch ? 'Да' : 'Нет' : 'После первого входа'}</dd></div></dl>
              {p.status === 'failed' && p.message && <Notice tone="danger">{p.message}</Notice>}
              <p className="meta">Профиль #{p.id}. Вход относится к сайту, указанному в статусе подключения.</p>
            </div>
          </li>;
        })}</ul>
        {visible.length > limit && <button className="btn secondary more-profiles" onClick={() => setLimit(n => n + 20)}>Показать ещё · осталось {visible.length - limit}</button>}
      </> : <Empty compact title={profiles.length ? 'Боты не найдены' : 'Ботов пока нет'}>{profiles.length ? 'Попробуйте другой запрос или состояние.' : 'Создайте нужное количество на вкладке «Запуск» или добавьте бота вручную.'}</Empty>}
      <div className="profile-selection">
        <div className="selection-title"><strong>Выбрано {chosen.length} из 10</strong>{chosen.length > 0 && <button type="button" className="text-button" disabled={busy} onClick={() => setChosen([])}>Снять выбор</button>}</div>
        <div className="toolbar"><button className="btn primary" disabled={busy || !chosen.length} onClick={() => perform('register', { profile_ids: chosen })}><Busy busy={busy && operation === 'register'} busyText="Регистрация…">Зарегистрировать выбранных</Busy></button>
          <button className="btn secondary" disabled={busy || !chosen.length} onClick={() => perform('verify', { profile_ids: chosen })}><Busy busy={busy && operation === 'verify'} busyText="Проверка…">Проверить вход</Busy></button>
          <button className="btn ghost" disabled={busy || !canRead} onClick={openReading}>Перейти к чтению</button></div>
        <p className="meta">Здесь можно выполнить действие для 1–10 ботов. Для большой партии используйте вкладку «Запуск».</p>
      </div>
    </Disclosure>
  </div>;
}
