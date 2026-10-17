import React from 'react';
import { Megaphone, BookOpen, CircleHelp, Circle, LoaderCircle, CircleCheck, TriangleAlert, CircleX, CircleSlash, Clock, HardDrive, Send, UserCheck, CircleDashed, Info, PlugZap, Hourglass, Hand, SkipForward, Ban, KeyRound, ShieldCheck, ShieldAlert, CircleOff } from 'lucide-react';

// Every status has its own text, icon and tone; similar outcomes never share a badge.
export const STATUS = {
  job: {
    ready: ['Готово к запуску', Circle, 'neutral'],
    running: ['Идёт сбор', LoaderCircle, 'active'],
    done: ['Завершено', CircleCheck, 'success'],
    partial: ['Частично', TriangleAlert, 'warning'],
    error: ['Ошибка', CircleX, 'danger'],
    stopped: ['Остановлено', CircleSlash, 'muted'],
    interrupted: ['Прервано', PlugZap, 'warning'],
    queued: ['В очереди', Hourglass, 'info'],
    waiting_user: ['Нужен пользователь', Hand, 'warning'],
  },
  run: {
    queued: ['В очереди', Hourglass, 'info'],
    running: ['Выполняется', LoaderCircle, 'active'],
    done: ['Завершён', CircleCheck, 'success'],
    partial: ['Частично', TriangleAlert, 'warning'],
    error: ['Ошибки страниц', CircleX, 'danger'],
    failed: ['Сбой', CircleX, 'danger'],
    waiting_user: ['Нужен пользователь', Hand, 'warning'],
    stopped: ['Остановлен', CircleSlash, 'muted'],
    interrupted: ['Прерван', PlugZap, 'warning'],
    cancelled: ['Отменён', Ban, 'muted'],
    skipped: ['Пропущен', SkipForward, 'muted'],
  },
  mailbox: {
    attached: ['Добавлено владельцем · не проверено', UserCheck, 'manual'],
    prepared: ['Подготовлено локально', HardDrive, 'local'],
    creating: ['Запрос в Zoho', LoaderCircle, 'active'],
    created: ['Создано в Zoho', CircleCheck, 'success'],
    failed: ['Запрос отклонён', CircleX, 'danger'],
    unknown: ['Нужно проверить в Zoho', TriangleAlert, 'warning'],
  },
  account: {
    planned: ['Не начато', CircleDashed, 'neutral'],
    in_progress: ['Начато вручную', Clock, 'info'],
    confirmed: ['Подтверждено', UserCheck, 'success'],
  },
  session: {
    empty: ['Нет сессии', CircleOff, 'neutral'],
    stored: ['Сохранена', KeyRound, 'local'],
    imported: ['Импортирована', KeyRound, 'info'],
    active: ['Вход подтверждён', ShieldCheck, 'success'],
    expired: ['Сессия истекла', ShieldAlert, 'warning'],
    unknown: ['Не проверена', CircleDashed, 'neutral'],
  },
  page: {
    ok: ['Найдено', CircleCheck, 'success'],
    empty: ['Блоков нет', CircleDashed, 'neutral'],
    error: ['Ошибка', CircleX, 'danger'],
    user_action: ['Нужен пользователь', Hand, 'warning'],
  },
  profile: {
    ready: ['Свободен', Circle, 'neutral'],
    open: ['Окно открыто', Send, 'info'],
    collecting: ['Идёт сбор', LoaderCircle, 'active'],
  },
  block: {
    ad: ['Реклама', Megaphone, 'success'],
    recommendation: ['Рекомендация', BookOpen, 'neutral'],
    unknown: ['Неясно', CircleHelp, 'muted'],
  },
  log: {
    info: ['Инфо', Info, 'info'],
    warning: ['Внимание', TriangleAlert, 'warning'],
    error: ['Ошибка', CircleX, 'danger'],
  },
};

export function Status({ kind, value, flash = false, title }) {
  const [label, Icon, tone] = STATUS[kind][value] || [value, Circle, 'neutral'];
  return <span className={`badge tone-${tone}${flash ? ' flash' : ''}`} title={title}>
    <Icon size={13} strokeWidth={2.2} className={tone === 'active' ? 'spin' : undefined} aria-hidden="true" />{label}
  </span>;
}
export const statusLabel = (kind, value) => STATUS[kind][value]?.[0] || value;
