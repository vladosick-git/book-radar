import React from 'react';
import { ChevronDown } from 'lucide-react';

// Native disclosures keep keyboard support and their open state across polling.
export function Disclosure({ title, caption, children, defaultOpen = false, className = '' }) {
  return <details className={`work-disclosure ${className}`} open={defaultOpen || undefined}>
    <summary><span className="disclosure-title">{title}{caption && <small>{caption}</small>}</span><ChevronDown size={18} aria-hidden="true" /></summary>
    <div className="disclosure-content">{children}</div>
  </details>;
}

export function ProfileStatus({ status }) {
  const labels = { planned: 'Подготовлен', registering: 'Регистрация', verifying: 'Проверка входа', reading: 'Читает', registered: 'Вход подтверждён', failed: 'Нужна проверка' };
  const tone = status === 'registered' ? 'success' : status === 'failed' ? 'danger' : ['registering', 'verifying', 'reading'].includes(status) ? 'active' : 'neutral';
  return <span className={`profile-state ${tone}`}><i aria-hidden="true" />{labels[status] || status}</span>;
}
