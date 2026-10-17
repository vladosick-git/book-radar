import React from 'react';
import { Upload, Trash2 } from 'lucide-react';
import { Empty, Notice, Modal, Busy, useModalClose } from './components';
import { host, plural, qs, usePref, useServerList } from './api';
import { AdvancedCatalog, AdvertiserBadge, BlockTag, CatalogControls, CatalogPager, CatalogSkeleton, CreativeImage, creativeDate, useCatalogFilters } from './CatalogUI';
import AdDrawer from './AdDrawer';

const LIMIT = 6;
function Cleanup({ act }) {
  const close = useModalClose(), [busy, setBusy] = React.useState(false);
  async function run() { setBusy(true); const result = await act('/evidence/cleanup', {}, r => `Удалено старых снимков: ${r.removed}.`); setBusy(false); if (result) close(); }
  return <div className="form"><p>Будут удалены снимки старше 7 дней, на которые больше не ссылаются находки и последние события журнала. Первый и последний снимок каждой находки сохранятся.</p><p className="meta">Снимки отдельных обнаружений в истории могут стать недоступны.</p><div className="form-actions"><button className="btn ghost" onClick={close}>Отмена</button><button className="btn danger" disabled={busy} onClick={run}><Busy busy={busy} busyText="Очистка…">Удалить старые снимки</Busy></button></div></div>;
}

function AdCard({ item, open, list }) {
  return <li className={`creative-card${list ? ' as-list' : ''}`}><button className="creative-card-button" onClick={open} aria-label={`Открыть объявление: ${item.title}`}>
    <CreativeImage item={item} />
    <div className="creative-card-body"><h2 className="creative-card-title" title={item.title}>{item.title}</h2>
      {item.is_test && <span className="meta">ТЕСТОВАЯ ЗАПИСЬ</span>}{item.duplicate_of && <span className="meta">Дубль #{item.duplicate_of}</span>}
      <div className="creative-card-source"><AdvertiserBadge item={item} /><span>{host(item.source_url)}</span></div>
      <div className="creative-card-footer"><BlockTag type={item.block_type} reason={item.block_reason} /><span>Видели {item.matched_sightings ?? item.sightings} {plural(item.matched_sightings ?? item.sightings, 'раз', 'раза', 'раз')}</span><time dateTime={item.matched_last_seen || item.last_seen}>{creativeDate(item.matched_last_seen || item.last_seen)}</time></div>
    </div>
  </button></li>;
}

export default function Findings({ state, act, go }) {
  const catalog = state.catalog || {};
  const pulse = `${catalog.findings}|${catalog.last_seen}|${catalog.sightings}`;
  const model = useCatalogFilters(pulse);
  const [view, setView] = usePref('catalog.view.v1', 'cards');
  const [offset, setOffset] = React.useState(0), [selected, setSelected] = React.useState(null), [cleanup, setCleanup] = React.useState(false);
  const mode = model.filters.mode;
  const filterKey = JSON.stringify([model.query, model.filters.sort, mode]);
  React.useEffect(() => { setOffset(0); }, [filterKey]);
  const list = useServerList(`/${mode === 'ads' ? 'ads' : 'findings'}${qs({ ...model.query, sort: model.filters.sort, offset, limit: LIMIT })}`, pulse);
  const items = list.data?.items || [];
  const running = state.runs.some(r => r.status === 'running');
  const total = list.data?.total;
  // Facet freshness is global; do not show it as freshness of a narrowed advertiser/source list.
  const narrowed = ['q', 'source', 'advertiser', 'block_type', 'profile_id', 'kind'].some(key => model.query[key]);
  const newToday = narrowed ? 0 : mode === 'ads' ? model.facets.data?.new_ads_today : model.facets.data?.new_today;
  const open = (item, selectedMode = mode) => setSelected({ item, mode: selectedMode });
  return <div className="analyst-findings">
    <header className="analyst-page-header"><div className="analyst-title-line"><h1>Находки</h1><span className="catalog-total">{total == null ? '—' : total} {mode === 'ads' ? plural(total || 0, 'объявление', 'объявления', 'объявлений') : plural(total || 0, 'размещение', 'размещения', 'размещений')}</span>{newToday > 0 && <span className="new-findings" title="Новые записи за сегодня во всей базе">{newToday} {plural(newToday, 'новое', 'новых', 'новых')} сегодня</span>}</div>
      <div className="analyst-header-actions"><span className={`collector-state${running || state.server?.worker_alive ? ' ok' : ''}`}><i />{running ? 'Сбор идёт' : state.server?.worker_alive ? 'Сборщик отвечает' : 'Сбор не идёт'}</span><button className="btn primary" onClick={() => go('export')}><Upload size={17} strokeWidth={1.8} />Экспорт</button></div>
    </header>
    <CatalogControls model={model} view={view} setView={setView} />
    <p className="meta">Каталог содержит рекламу разных тематик. Для книжных объявлений AT выберите рекламодателя author.today. Тестовые записи и точные дубли по умолчанию скрыты, исходные данные сохранены.</p>
    {model.filters.data_scope !== 'real' && <Notice tone="warning">Включены тестовые данные. Они помечены в выгрузке и не подтверждают сбор с настоящих сайтов.</Notice>}
    {list.error && <Notice tone="danger">{list.error}<button className="text-button" onClick={list.reload}>Повторить</button></Notice>}
    {list.loading && !list.data ? <CatalogSkeleton /> : items.length ? <ul className={view === 'list' ? 'creative-list' : 'creative-grid'} aria-busy={list.loading}>{items.map(item => <AdCard key={item.id} item={item} list={view === 'list'} open={() => open(item)} />)}</ul>
      : !list.error && !list.loading && <Empty title={catalog.findings ? 'Ничего не найдено' : 'Находок пока нет'} action={<button className="btn primary" onClick={catalog.findings ? model.reset : () => go('jobs')}>{catalog.findings ? 'Сбросить фильтры' : 'Запустить сбор'}</button>} />}
    <CatalogPager total={total || 0} offset={offset} limit={LIMIT} loading={list.loading} onChange={value => { setOffset(value); window.scrollTo({ top: 0, behavior: 'instant' }); }} />
    <AdvancedCatalog model={model} profiles={state.profiles}><button className="text-button" disabled={!catalog.findings} onClick={() => setCleanup(true)}><Trash2 size={14} />Очистить старые снимки</button></AdvancedCatalog>
    {selected && <AdDrawer selected={selected.item} mode={selected.mode} close={() => setSelected(null)} onSelect={open} />}
    {cleanup && <Modal size="sm" title="Очистить старые снимки?" close={() => setCleanup(false)}><Cleanup act={act} /></Modal>}
  </div>;
}
