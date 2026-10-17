import React from 'react';
import { X, Search, Info, TriangleAlert, CircleX, CircleCheck, LoaderCircle, ChevronDown, ChevronLeft, ChevronRight, Inbox, CalendarRange } from 'lucide-react';
import { reducedMotion, evidenceUrl } from './api';

export function LogMessage({ message }) {
  const file = message.match(/job-\d+-[a-f0-9]{32}\.png/)?.[0];
  return <>{file ? message.replace(/\s*(Скриншот страницы|Скриншот):\s*job-\d+-[a-f0-9]{32}\.png/, '') : message}
    {file && <a className="inline-link" href={evidenceUrl(file)} target="_blank" rel="noreferrer">Открыть скриншот</a>}</>;
}

export function Empty({ title, children, action, icon: Icon = Inbox, compact = false }) {
  return <div className={`empty${compact ? ' compact' : ''}`}><span className="empty-icon"><Icon size={22} strokeWidth={1.7} /></span><h3>{title}</h3>{children && <p>{children}</p>}{action}</div>;
}

export function PageHeader({ title, children, actions }) {
  return <header className="page-header"><div><h1>{title}</h1>{children && <p>{children}</p>}</div>{actions && <div className="header-actions">{actions}</div>}</header>;
}

export function Notice({ tone = 'info', children, onClose, role }) {
  const Icon = { info: Info, warning: TriangleAlert, danger: CircleX, success: CircleCheck }[tone];
  return <div className={`notice tone-${tone}`} role={role || (tone === 'danger' ? 'alert' : 'status')}>
    <Icon size={17} className="notice-icon" aria-hidden="true" /><div className="notice-body">{children}</div>
    {onClose && <button className="icon-button" aria-label="Скрыть сообщение" onClick={onClose}><X size={16} /></button>}
  </div>;
}

export function SearchInput({ value, onChange, placeholder, label }) {
  return <label className="search"><Search size={16} aria-hidden="true" /><input aria-label={label} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} />
    {value && <button type="button" className="icon-button" aria-label="Очистить поиск" onClick={() => onChange('')}><X size={14} /></button>}</label>;
}

export function Select({ label, value, onChange, options }) {
  return <label className="select"><span className="visually-hidden">{label}</span>
    <select aria-label={label} value={value} onChange={e => onChange(e.target.value)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select>
    <ChevronDown size={15} aria-hidden="true" /></label>;
}

export function Segmented({ label, value, onChange, options }) {
  return <div className="segmented" role="radiogroup" aria-label={label}>{options.map(([key, text, Icon]) =>
    <button type="button" key={key} role="radio" aria-checked={value === key} className={value === key ? 'on' : ''} onClick={() => onChange(key)} title={typeof text === 'string' ? text : undefined}>
      {Icon && <Icon size={15} aria-hidden="true" />}<span>{text}</span></button>)}</div>;
}

export function Spinner({ label = 'Выполняется…' }) {
  return <span className="spinner" role="status"><LoaderCircle size={15} className="spin" aria-hidden="true" /><span>{label}</span></span>;
}

export function Busy({ busy, children, busyText }) {
  return busy ? <><LoaderCircle size={15} className="spin" aria-hidden="true" />{busyText}</> : children;
}

// Smooth height reveal without measuring: grid rows 0fr → 1fr.
export function Reveal({ open, children, id }) {
  return <div className={`reveal${open ? ' open' : ''}`} id={id} aria-hidden={!open}><div className="reveal-inner" inert={!open}>{children}</div></div>;
}

export function Modal({ title, subtitle, children, close, size = 'md' }) {
  const ref = React.useRef(null), [closing, setClosing] = React.useState(false), closeRef = React.useRef(close);
  closeRef.current = close;
  const requestClose = React.useCallback(() => {
    if (reducedMotion()) { closeRef.current(); return; }
    setClosing(true);
    setTimeout(() => closeRef.current(), 140);
  }, []);
  React.useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    const onCancel = e => { e.preventDefault(); requestClose(); };
    dialog.addEventListener('cancel', onCancel);
    return () => dialog.removeEventListener('cancel', onCancel);
  }, [requestClose]);
  return <dialog ref={ref} aria-label={title} className={`modal size-${size}${closing ? ' closing' : ''}`}>
    <div className="dialog-header"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button close" aria-label="Закрыть" onClick={requestClose}><X size={18} /></button></div>
    <ModalClose.Provider value={requestClose}>{children}</ModalClose.Provider>
  </dialog>;
}
export const ModalClose = React.createContext(() => {});
export const useModalClose = () => React.useContext(ModalClose);

export const ToastContext = React.createContext(() => {});
export const useToast = () => React.useContext(ToastContext);
export function Toasts({ items, dismiss }) {
  return <div className="toasts" aria-live="polite">{items.map(toast => <div className={`toast tone-${toast.tone}`} key={toast.id} role="status">
    {toast.tone === 'danger' ? <CircleX size={16} /> : toast.tone === 'warning' ? <TriangleAlert size={16} /> : <CircleCheck size={16} />}
    <span>{toast.text}</span><button className="icon-button" aria-label="Скрыть уведомление" onClick={() => dismiss(toast.id)}><X size={14} /></button></div>)}</div>;
}

export function Field({ label, hint, error, children, optional }) {
  return <div className={`field${error ? ' has-error' : ''}`}><label><span className="field-label-text">{label}{optional && <span className="optional">необязательно</span>}</span>{children}</label>
    {error ? <p className="field-error" role="alert">{error}</p> : hint && <p className="field-hint">{hint}</p>}</div>;
}

export function Skeleton({ rows = 4 }) {
  return <div className="skeleton" aria-hidden="true">{Array.from({ length: rows }, (_, i) => <div className="skeleton-row" key={i}><span /><span /><span /></div>)}</div>;
}

// 0.7.0: pages of a server-side list.
export function Pager({ total, offset, limit, onChange, loading }) {
  if (!total) return null;
  const from = Math.min(total, offset + 1), to = Math.min(total, offset + limit);
  return <nav className="pager" aria-label="Страницы списка">
    <span className="meta">{loading ? <LoaderCircle size={12} className="spin" aria-hidden="true" /> : null}Показаны {from}–{to} из {total}</span>
    <span className="pager-buttons">
      <button className="btn secondary sm" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))} aria-label="Предыдущая страница"><ChevronLeft size={14} />Назад</button>
      <button className="btn secondary sm" disabled={to >= total} onClick={() => onChange(offset + limit)} aria-label="Следующая страница">Дальше<ChevronRight size={14} /></button>
    </span>
  </nav>;
}

// Calendar period in the viewer's local days; empty fields mean «без ограничения».
export function Period({ since, until, onChange, label = 'Период' }) {
  return <div className="period" role="group" aria-label={label}>
    <CalendarRange size={15} aria-hidden="true" />
    <input type="date" aria-label="С даты" value={since} max={until || undefined} onChange={e => onChange(e.target.value, until)} />
    <span aria-hidden="true">–</span>
    <input type="date" aria-label="По дату включительно" value={until} min={since || undefined} onChange={e => onChange(since, e.target.value)} />
    {(since || until) && <button type="button" className="icon-button" aria-label="Сбросить период" onClick={() => onChange('', '')}><X size={14} /></button>}
  </div>;
}
