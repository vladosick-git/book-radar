import React from 'react';

export async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let value;
  try { value = await response.json(); } catch { throw new Error('Сервер вернул неожиданный ответ.'); }
  if (response.status === 401 && path !== '/login') window.dispatchEvent(new Event('radar:unauthorized'));
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : value.detail?.map(e => e.msg.replace(/^Value error, /, '')).join('; ') || 'Ошибка запроса.');
  return value;
}

export function stamp(value) { return value ? new Date(value).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; }
export function shortStamp(value) {
  if (!value) return '—';
  const date = new Date(value), today = new Date();
  const time = date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (date.toDateString() === today.toDateString()) return `сегодня, ${time}`;
  return `${date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}, ${time}`;
}
export function safeLink(value) { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
export function host(value) { try { return new URL(value).host; } catch { return value || '—'; } }
export function plural(n, one, few, many) { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many; }
export function evidenceUrl(file) { return /^job-\d+-[a-f0-9]{32}\.png$/.test(file || '') ? `/api/evidence/${file}` : null; }

// View preferences only. Never put tokens, passwords or form secrets here.
const memory = new Map();
const PREFIX = 'book-radar:';
export const prefs = {
  get(key, fallback) {
    try { const raw = localStorage.getItem(PREFIX + key); if (raw !== null) return JSON.parse(raw); } catch { /* storage unavailable */ }
    return memory.has(key) ? memory.get(key) : fallback;
  },
  set(key, value) {
    memory.set(key, value);
    try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* keep in memory */ }
  },
};
export function usePref(key, fallback) {
  const [value, setValue] = React.useState(() => prefs.get(key, fallback));
  const update = React.useCallback(next => setValue(previous => { const resolved = typeof next === 'function' ? next(previous) : next; prefs.set(key, resolved); return resolved; }), [key]);
  return [value, update];
}

// Marks rows that appeared or changed since the previous poll. Never fires on first render.
export function useFlash(items, keyOf, signatureOf) {
  const previous = React.useRef(null), timer = React.useRef(null);
  const [flash, setFlash] = React.useState(() => new Set());
  React.useEffect(() => {
    const next = new Map(items.map(item => [keyOf(item), signatureOf(item)]));
    if (previous.current) {
      const changed = [...next].filter(([key, signature]) => previous.current.get(key) !== signature).map(([key]) => key);
      if (changed.length) {
        setFlash(new Set(changed));
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setFlash(new Set()), 1800);
      }
    }
    previous.current = next;
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => () => clearTimeout(timer.current), []);
  return flash;
}

export const reducedMotion = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };

export function ago(value, now) {
  if (!value) return '—';
  const seconds = Math.max(0, Math.round(((now ? new Date(now) : new Date()) - new Date(value)) / 1000));
  if (seconds < 60) return `${seconds} с назад`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин назад`;
  return `${Math.round(seconds / 3600)} ч назад`;
}
export const TRIGGERS = { manual: 'вручную', schedule: 'по расписанию', retry: 'повтор' };
export function scheduleText(job) {
  if (!job.schedule_kind || job.schedule_kind === 'none') return '';
  const rule = job.schedule_kind === 'interval' ? `каждые ${job.interval_minutes} мин`
    : job.schedule_kind === 'once' ? `разово ${job.run_at ? new Date(job.run_at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}`
    : `ежедневно в ${job.daily_time} (${job.timezone})`;
  const window = job.window_start && job.schedule_kind !== 'once' ? ` · окно ${job.window_start}-${job.window_end}` : '';
  return job.paused ? `${rule}${window} · пауза` : `${rule}${window}`;
}

// 0.7.0: server-side lists (findings, journal, runs). Empty parameters are not sent.
export function qs(params) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== '' && value !== null && value !== undefined && value !== false) query.set(key, value); });
  const text = query.toString();
  return text ? `?${text}` : '';
}

// A local calendar day → the UTC moment of its start; `end` gives the start of the next day (exclusive bound).
export function dayStart(day, end = false) {
  if (!day) return '';
  const date = new Date(`${day}T00:00:00`);
  if (Number.isNaN(date.getTime())) return '';
  if (end) date.setDate(date.getDate() + 1);
  return date.toISOString();
}

export function useDebounced(value, delay = 300) {
  const [current, setCurrent] = React.useState(value);
  React.useEffect(() => { const timer = setTimeout(() => setCurrent(value), delay); return () => clearTimeout(timer); }, [value, delay]);
  return current;
}

// Fetches `path` whenever it changes; `pulse` (a value from the 2-second state poll) refreshes it quietly.
export function useServerList(path, pulse) {
  const [result, setResult] = React.useState({ data: null, loading: true, error: '', path: null });
  const [nonce, setNonce] = React.useState(0);
  const last = React.useRef(path);
  React.useEffect(() => {
    let alive = true;
    const changed = last.current !== path;
    last.current = path;
    if (changed) setResult(previous => ({ ...previous, loading: true }));
    api(path).then(data => alive && setResult({ data, loading: false, error: '', path }))
      .catch(e => alive && setResult(previous => ({ ...previous, loading: false, error: e.message, path })));
    return () => { alive = false; };
  }, [path, pulse, nonce]);
  return { ...result, loading: result.loading || result.path !== path, reload: () => setNonce(n => n + 1) };
}
