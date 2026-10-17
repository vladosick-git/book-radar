import React from 'react';
import { ImageOff, ChevronDown, ChevronLeft, ChevronRight, SlidersHorizontal } from 'lucide-react';
import { SearchInput, Select, Segmented, Period } from './components';
import { dayStart, evidenceUrl, qs, useDebounced, usePref, useServerList } from './api';

export const BLOCK_LABELS = { ad: 'Реклама', recommendation: 'Рекомендация', unknown: 'Неясно' };
const defaultFilters = { search: '', source: '', advertiser: '', block_type: '', period: '7', since: '', until: '', profile_id: '', kind: '', sort: 'last', mode: 'ads', data_scope: 'real', include_duplicates: false };
export function useCatalogFilters(pulse) {
  const [saved, save] = usePref('catalog.filters.v1', defaultFilters);
  const filters = { ...defaultFilters, ...saved };
  const set = (key, value) => save(old => ({ ...defaultFilters, ...old, [key]: value }));
  const search = useDebounced(filters.search.trim());
  const date = new Date(), today = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  let since = '', until = '';
  if (filters.period === 'custom') { since = dayStart(filters.since); until = dayStart(filters.until, true); }
  else if (filters.period !== 'all') {
    const start = new Date(today); start.setDate(start.getDate() - (Number(filters.period) - 1));
    const end = new Date(today); end.setDate(end.getDate() + 1);
    since = start.toISOString(); until = end.toISOString();
  }
  const query = { q: search, source: filters.source, advertiser: filters.advertiser, block_type: filters.block_type, since, until, profile_id: filters.profile_id, kind: filters.kind, data_scope: filters.data_scope, include_duplicates: filters.include_duplicates ? 'true' : '' };
  Object.keys(query).forEach(key => { if (!query[key]) delete query[key]; });
  const facets = useServerList(`/findings/facets${qs({ new_since: today.toISOString(), data_scope: filters.data_scope, include_duplicates: filters.include_duplicates ? 'true' : '' })}`, pulse);
  return { filters, set, query, facets, reset: () => save({ ...defaultFilters, period: 'all' }) };
}

export function CatalogControls({ model, view, setView }) {
  const { filters: f, set, facets } = model;
  return <div className="catalog-controls">
    <div className="catalog-search-row"><SearchInput label="Поиск объявлений" value={f.search} onChange={v => set('search', v)} placeholder="Заголовок, текст или ссылка" />
      {setView && <Segmented label="Вид каталога" value={view} onChange={setView} options={[["cards", "Карточки"], ["list", "Список"]]} />}</div>
    <div className="catalog-filters">
      <Select label="Сайт показа" value={f.source} onChange={v => set('source', v)} options={[
        ['', 'Все сайты'], ...(facets.data?.sources || []).map(s => [s.host, s.host]),
      ]} />
      <Select label="Рекламодатель" value={f.advertiser} onChange={v => set('advertiser', v)} options={[
        ['', 'Все рекламодатели'], ...(facets.data?.advertisers || []).map(s => [s.domain, s.domain]),
      ]} />
      <Select label="Тип блока" value={f.block_type} onChange={v => set('block_type', v)} options={[
        ['', 'Все типы'], ...Object.entries(BLOCK_LABELS),
      ]} />
      <Select label="Период" value={f.period} onChange={v => set('period', v)} options={[
        ['all', 'За всё время'], ['1', 'Сегодня'], ['7', 'За 7 дней'], ['30', 'За 30 дней'], ['custom', 'Выбрать даты'],
      ]} />
      <Select label="Происхождение данных" value={f.data_scope} onChange={v => set('data_scope', v)} options={[
        ['real', 'Настоящие страницы'], ['test', 'Только тестовые'], ['all', 'Все, включая тестовые'],
      ]} />
      <label className="check"><input type="checkbox" checked={f.include_duplicates} onChange={e => set('include_duplicates', e.target.checked)} />Показать точные дубли</label>
    </div>
    {f.period === 'custom' && <Period since={f.since} until={f.until} onChange={(a, b) => { set('since', a); set('until', b); }} label="Период обнаружения" />}
  </div>;
}

export function AdvancedCatalog({ model, profiles, children }) {
  const { filters: f, set } = model;
  return <details className="catalog-advanced"><summary><SlidersHorizontal size={14} aria-hidden="true" />Дополнительные параметры<ChevronDown size={14} aria-hidden="true" /></summary>
    <div className="advanced-filter-content">
      <Select label="Профиль браузера" value={f.profile_id} onChange={v => set('profile_id', v)} options={[
        ['', 'Все профили'], ...profiles.map(p => [String(p.id), p.name]),
      ]} />
      <Select label="Сортировка" value={f.sort} onChange={v => set('sort', v)} options={[
        ['last', 'Сначала недавние'], ['first', 'Сначала ранние'], ['seen', 'Чаще встречались'],
      ]} />
      <Select label="Группировка каталога" value={f.mode} onChange={v => set('mode', v)} options={[
        ['ads', 'Объявления'], ['findings', 'Отдельные размещения'],
      ]} />
      <Select label="Содержимое блока" value={f.kind} onChange={v => set('kind', v)} options={[
        ['', 'Любое содержимое'], ['banner', 'Баннер без текста'], ['text', 'Текстовый блок'], ['mixed', 'Картинка и текст'], ['link', 'Только ссылка'], ['iframe', 'Закрытый фрейм'],
      ]} />
      {children}
    </div>
  </details>;
}

export function AdvertiserBadge({ item }) {
  const domains = item.advertisers || item.block_evidence?.target_domains || [];
  const safe = Array.isArray(domains) ? domains.filter(d => typeof d === 'string') : [];
  const at = safe.find(d => /^(www\.)?author\.today$/i.test(d));
  const domain = at || safe[0];
  return <span className={`advertiser-badge${at ? ' is-author' : ''}${!domain ? ' is-unknown' : ''}`} title={safe.join(', ')}>{domain || 'Не указан'}{safe.length > 1 ? ` +${safe.length - 1}` : ''}</span>;
}

export function BlockTag({ type = 'unknown', reason }) {
  return <span className={`block-tag block-${type || 'unknown'}`} title={reason}>{BLOCK_LABELS[type] || 'Неясно'}</span>;
}

export function CreativeImage({ item, className = '' }) {
  const [failed, setFailed] = React.useState('');
  const url = evidenceUrl(item.crop) || evidenceUrl(item.screenshot);
  return <div className={`creative-image ${className}`}>{url && failed !== url
    ? <img src={url} alt="Сохранённый креатив объявления" loading="lazy" decoding="async" onError={() => setFailed(url)} />
    : <div className="creative-missing"><ImageOff size={22} strokeWidth={1.5} aria-hidden="true" /><span>Изображение недоступно</span></div>}</div>;
}

export function creativeDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).replace(' г.', '').replace(',', '');
}

export function CatalogSkeleton() {
  return <div className="creative-grid skeleton-grid" role="status" aria-label="Загрузка объявлений">{Array.from({ length: 6 }, (_, i) => <div className="creative-skeleton" key={i}><div /><span /><span /><span /></div>)}</div>;
}

export function CatalogPager({ total, offset, limit, onChange, loading }) {
  if (!total) return null;
  const count = Math.ceil(total / limit), current = Math.floor(offset / limit) + 1;
  const start = Math.max(1, Math.min(current - 2, count - 4));
  const numbers = Array.from({ length: Math.min(5, count) }, (_, i) => start + i);
  return <div className="catalog-pager"><span>Показано {Math.min(limit, total - offset)} из {total}</span>
    <div role="group" aria-label="Страницы каталога">
      {current > 1 && <button className="page-number" aria-label="Предыдущая страница" disabled={loading} onClick={() => onChange(offset - limit)}><ChevronLeft size={15} /></button>}
      {numbers.map(n => <button key={n} className={`page-number${n === current ? ' current' : ''}`} aria-current={n === current ? 'page' : undefined} aria-label={`Страница ${n}`} disabled={loading} onClick={() => onChange((n - 1) * limit)}>{n}</button>)}
      {current < count && <button className="page-number" aria-label="Следующая страница" disabled={loading} onClick={() => onChange(offset + limit)}><ChevronRight size={15} /></button>}
    </div>
  </div>;
}
