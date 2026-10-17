import React from 'react';
import { X, ExternalLink, Check, Info, ImageOff } from 'lucide-react';
import { Notice, Spinner } from './components';
import { evidenceUrl, host, plural, safeLink, stamp, useServerList } from './api';
import { AdvertiserBadge, BlockTag, CreativeImage, creativeDate } from './CatalogUI';

function Drawer({ children, close, type }) {
  const ref = React.useRef(null), closer = React.useRef(close), heading = React.useId();
  closer.current = close;
  React.useEffect(() => {
    const node = ref.current, previous = document.activeElement;
    node.showModal();
    const cancel = event => { event.preventDefault(); closer.current(); };
    node.addEventListener('cancel', cancel);
    return () => { node.removeEventListener('cancel', cancel); if (node.open) node.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} className="ad-drawer" aria-labelledby={heading} onClick={e => { if (e.target === ref.current && e.clientX < ref.current.getBoundingClientRect().left) close(); }}>
    <div className="ad-drawer-header"><h2 id={heading}>Объявление</h2><BlockTag type={type} /><button className="icon-button" aria-label="Закрыть объявление" autoFocus onClick={close}><X size={21} /></button></div>
    <div className="ad-drawer-scroll">{children}</div>
  </dialog>;
}

function EvidenceShot({ finding }) {
  const file = evidenceUrl(finding.screenshot), [failed, setFailed] = React.useState(false);
  const rect = finding.block_evidence?.page_rect;
  const valid = rect && ['x', 'y', 'width', 'height', 'page_width', 'page_height'].every(k => Number.isFinite(rect[k])) && rect.x >= 0 && rect.y >= 0 && rect.width > 0 && rect.height > 0 && rect.page_width >= rect.x + rect.width && rect.page_height >= rect.y + rect.height;
  return <div className="drawer-screenshot">{file && !failed ? <a href={file} target="_blank" rel="noreferrer" className="evidence-capture" aria-label="Открыть снимок страницы">
    <img src={file} alt="Сохранённый снимок страницы" loading="lazy" onError={() => setFailed(true)} />
    {valid && <span className="evidence-highlight" style={{ left: `${rect.x / rect.page_width * 100}%`, top: `${rect.y / rect.page_height * 100}%`, width: `${rect.width / rect.page_width * 100}%`, height: `${rect.height / rect.page_height * 100}%` }} />}
  </a> : <div className="screenshot-missing"><ImageOff size={21} /><span>Снимок недоступен</span></div>}
    {!valid && file && !failed && <p className="meta">Координаты блока не сохранены.</p>}
  </div>;
}

export default function AdDrawer({ selected, mode, close, onSelect }) {
  const path = `/${mode === 'ads' ? 'ads' : 'findings'}/${selected.id}`;
  const { data, error, reload, loading } = useServerList(path, selected.id);
  const [historyLimit, setHistoryLimit] = React.useState(3);
  const placement = mode === 'ads' ? data?.placements.find(p => p.id === selected.finding_id) || data?.placements[0] : data;
  const finding = placement || selected;
  const observations = data?.observations?.items || [];
  const text = data?.text || finding.clean_text || finding.text;
  const destination = safeLink(data?.destination || finding.destination);
  const type = finding.block_type || selected.block_type || 'unknown';
  const evidence = finding.block_evidence || {};
  const classification = type === 'ad' ? 'Почему это реклама' : type === 'recommendation' ? 'Почему это рекомендация' : 'Почему тип неясен';
  const reason = finding.block_reason || (type === 'unknown' ? 'Недостаточно признаков для уверенной классификации.' : 'Причина не сохранена в этой записи.');
  return <Drawer close={close} type={type}>
    {error ? <Notice tone="danger">{error}<button className="text-button" onClick={reload}>Повторить</button></Notice> : !data || loading ? <Spinner label="Загрузка объявления…" /> : <>
      <CreativeImage item={finding} className="drawer-creative" />
      <h3 className="drawer-ad-title">{finding.title || data.title}</h3>
      {finding.is_test && <Notice tone="warning">Тестовая находка с локальной страницы.</Notice>}
      {finding.duplicate_of && <p className="meta">Точный дубль записи #{finding.duplicate_of}. Обе исходные записи сохранены.</p>}
      <div className="drawer-ad-meta"><AdvertiserBadge item={finding} /><span>{host(finding.source_url)}</span><span>Видели {data.sightings} {plural(data.sightings, 'раз', 'раза', 'раз')}</span>{typeof evidence.age_mark === 'string' && <span>{evidence.age_mark}</span>}</div>
      <section className="drawer-section"><h3>Текст объявления</h3><p className="drawer-full-text">{text || 'У объявления нет доступного текста.'}</p></section>
      <section className="drawer-ocr"><h3>Текст с картинки</h3><p>{finding.ocr_text || (finding.ocr_status === 'empty' ? 'На изображении не найден текст.' : 'Распознанный текст пока отсутствует.')}</p></section>
      <section className="drawer-link"><h3>Исходная ссылка</h3>{destination ? <a href={destination} target="_blank" rel="noreferrer">{destination.replace(/^https?:\/\//, '')}<ExternalLink size={16} aria-hidden="true" /></a> : <p className="meta">Ссылка не обнаружена в доступном блоке.</p>}{finding.destination_status === 'tracking_link' && <p className="meta">Это счётчик рекламной сети. Конечный адрес не подтверждён; программа не переходила по рекламе.</p>}</section>
      <div className="drawer-evidence-grid">
        <section className="drawer-history"><h3>История показов</h3>{observations.length ? <><ol>{observations.slice(0, historyLimit).map(o => <li key={o.id}><i aria-hidden="true" /><div><time title={stamp(o.seen_at)}>{creativeDate(o.seen_at)}</time><span> · {host(o.source_url)}</span></div></li>)}</ol>{observations.length > historyLimit && <button className="text-button" onClick={() => setHistoryLimit(n => n + 10)}>Показать ещё</button>}</> : <p className="meta">История пока пуста.</p>}</section>
        <section><h3>Снимок страницы</h3><EvidenceShot finding={finding} /></section>
      </div>
      <section className="drawer-reason"><h3>{classification}</h3><p><span className={`reason-icon ${type === 'ad' ? 'confirmed' : ''}`}>{type === 'ad' ? <Check size={13} /> : <Info size={13} />}</span>{reason}</p>
        {finding.advertisers?.length > 0 && <p><span className="reason-icon confirmed"><Check size={13} /></span>Рекламодатель: {finding.advertisers.join(', ')}</p>}
      </section>
      <details className="drawer-technical"><summary>Данные и варианты</summary><dl><dt>Впервые найдено</dt><dd>{stamp(data.first_seen)}</dd><dt>Последнее обнаружение</dt><dd>{stamp(data.last_seen)}</dd><dt>Размещение</dt><dd>{finding.profile_name || '—'}</dd><dt>Источник</dt><dd>{safeLink(finding.source_url) ? <a href={safeLink(finding.source_url)} target="_blank" rel="noreferrer">{finding.source_url}</a> : '—'}</dd></dl>
        <p className="meta">Счётчик показывает обнаружения сборщиком, а не статистику рекламной сети.</p>
        {data.variants?.length > 1 && <div className="drawer-variants">{data.variants.map(v => <button key={v.id} className="text-button" onClick={() => onSelect({ ...v }, 'ads')}>Вариант {v.variant_no}: {v.title}</button>)}</div>}
      </details>
    </>}
  </Drawer>;
}
