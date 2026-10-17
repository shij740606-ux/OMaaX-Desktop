import React from 'react';
import { createRoot } from 'react-dom/client';
import AccountView from './views/AccountView';
import './styles.css';

const apply = (dark: boolean) => document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
const mq = window.matchMedia('(prefers-color-scheme: dark)');
apply(mq.matches);
mq.addEventListener('change', () => apply(mq.matches));

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <div className="h-full" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <AccountView />
    </div>
  </React.StrictMode>,
);
