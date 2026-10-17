import React from 'react';
import { Download } from 'lucide-react';
import { Empty, Notice, Segmented } from './components';
import { host, plural, qs, usePref, useServerList } from './api';
import { AdvertiserBadge, BlockTag, CatalogControls, creativeDate, useCatalogFilters } from './CatalogUI';

export default function Export({ state }) {
  const pulse = `${state.catalog?.findings}|${state.catalog?.last_seen}|${state.catalog?.sightings}`;
  const model = useCatalogFilters(pulse);
  const [format, setFormat] = usePref('catalog.export-format', 'csv'), [observations, setObservations] = usePref('catalog.export-observations', false);
  const preview = useServerList(`/export/preview${qs(model.query)}`, pulse);
  const count = preview.data?.total;
  const ready = !preview.loading && !preview.error && count > 0;
  const href = `/api/export.${format === 'json' ? 'json' : 'csv'}${qs({ ...model.query, observations: format === 'json' && observations ? 'true' : '' })}`;
  const filename = `book-radar.${format}`;
  return <div className="analyst-export">
    <header className="analyst-page-header"><div className="analyst-title-line"><h1>Экспорт</h1><span className="catalog-total">{count == null ? '—' : count} {plural(count || 0, 'запись', 'записи', 'записей')}</span></div></header>
    <div className="export-format"><span>Формат</span><Segmented label="Формат экспорта" value={format} onChange={setFormat} options={[["csv", "CSV"], ["json", "JSON"]]} /></div>
    <CatalogControls model={model} />
    <p className="meta">Выгрузка учитывает те же фильтры, что каталог. Исходный заголовок сохраняется в original_title; тестовые записи и дубли помечены отдельно.</p>
    {model.filters.data_scope !== 'real' && <Notice tone="warning">В эту выгрузку могут попасть тестовые записи. Проверьте выбранное происхождение данных.</Notice>}
    {format === 'json' && <label className="check export-observations"><input type="checkbox" checked={observations} onChange={e => setObservations(e.target.checked)} />Добавить историю обнаружений</label>}
    <div className="preview-heading"><h2>Предпросмотр</h2><span>Первые 5 строк</span></div>
    {preview.error && <Notice tone="danger">{preview.error}<button className="text-button" onClick={preview.reload}>Повторить</button></Notice>}
    {preview.loading && !preview.data ? <div className="export-skeleton" role="status" aria-label="Загрузка предпросмотра">{Array.from({ length: 6 }, (_, i) => <div key={i} />)}</div>
      : preview.data?.items.length ? <div className="analyst-table-wrap"><table className="analyst-table export-table" aria-busy={preview.loading}><thead><tr><th>Заголовок</th><th>Рекламодатель</th><th>Сайт показа</th><th>Тип</th><th>Видели</th><th>Последний показ</th></tr></thead>
        <tbody>{preview.data.items.map(item => <tr key={item.id}><td data-label="Заголовок">{item.title}</td><td data-label="Рекламодатель"><AdvertiserBadge item={item} /></td><td data-label="Сайт показа">{host(item.source_url)}</td><td data-label="Тип"><BlockTag type={item.block_type} /></td><td data-label="Видели" className="tabular">{item.sightings}</td><td data-label="Последний показ" className="tabular">{creativeDate(item.last_seen)}</td></tr>)}</tbody></table></div>
        : !preview.error && !preview.loading && <Empty compact title="Нет записей для выгрузки" action={<button className="btn secondary" onClick={model.reset}>Сбросить фильтры</button>} />}
    <div className="export-download-row"><div><strong>{preview.loading ? 'Подсчитываем записи…' : `В файл попадёт ${count ?? '—'} ${plural(count || 0, 'запись', 'записи', 'записей')}`}</strong><span>{filename}</span></div>
      <div><a className={`btn primary${!ready ? ' disabled' : ''}`} aria-disabled={!ready} tabIndex={ready ? 0 : -1} href={ready ? href : undefined} download={filename} onClick={e => { if (!ready) e.preventDefault(); }}><Download size={19} strokeWidth={1.8} />Скачать {format.toUpperCase()}</a><button className="text-button" onClick={model.reset}>Сбросить фильтры</button></div>
    </div>
  </div>;
}
