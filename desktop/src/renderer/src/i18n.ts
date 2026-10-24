// 极简国际化：界面源码里写中文，t('中文') 在英文模式下查表返回英文；查不到就原样返回（并在开发时提示）。
// 语言来自设置 language: 'system' | 'zh' | 'en'。词典在 ./i18n.en.ts。
import { createContext, createElement, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { EN } from './i18n.en';

export type Lang = 'zh' | 'en';
export type LangSetting = Lang | 'system';

export function resolveLang(setting: LangSetting | undefined): Lang {
  if (setting === 'zh' || setting === 'en') return setting;
  const nav = typeof navigator !== 'undefined' ? navigator.language.toLowerCase() : 'zh';
  return nav.startsWith('zh') ? 'zh' : 'en';
}

const missing = new Set<string>();
const templates = Object.entries(EN).filter(([key]) => /\{\w+\}/.test(key)).map(([key, value]) => {
  const names: string[] = [];
  const pattern = key.split(/(\{\w+\})/).map(part => {
    if (/^\{\w+\}$/.test(part)) { names.push(part.slice(1, -1)); return '([\\s\\S]+?)'; }
    return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }).join('');
  return { pattern: new RegExp('^' + pattern + '$'), names, value, specificity: key.replace(/\{\w+\}/g, '').length };
}).filter(entry => entry.specificity > 0).sort((a, b) => b.specificity - a.specificity);

function interpolate(text: string, vars: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (token, key: string) => Object.hasOwn(vars, key) ? String(vars[key]) : token);
}

export function translate(lang: Lang, zh: string, vars?: Record<string, string | number>): string {
  let out = zh;
  if (lang === 'en') {
    const hit = Object.hasOwn(EN, zh) ? EN[zh] : undefined;
    if (hit) out = hit;
    else if (Object.hasOwn(EN, zh.trim())) out = zh.replace(zh.trim(), () => EN[zh.trim()]);
    else if (/[\p{Script=Han}]/u.test(zh)) {
      const errorPrefix = /^(Error:\s*)([\s\S]+)$/.exec(zh);
      if (errorPrefix) return errorPrefix[1] + translate(lang, errorPrefix[2], vars);
      for (const entry of templates) {
        const match = entry.pattern.exec(zh);
        if (match) {
          // Localize nested app errors, but never reinterpret paths, names or other user values.
          const values = Object.fromEntries(entry.names.map((name, i) => {
            const value = match[i + 1];
            return [name, /^(error|e|detail|message|msg)$/.test(name) && value !== zh ? translate(lang, value) : value];
          }));
          out = interpolate(entry.value, values);
          break;
        }
      }
      if (out === zh && import.meta.env?.DEV && !missing.has(zh)) { missing.add(zh); console.warn('[i18n] missing en:', zh); }
    }
  }
  if (vars) out = interpolate(out, lang === 'en' ? Object.fromEntries(Object.entries(vars).map(([name, value]) =>
    [name, typeof value === 'string' && /^(error|e|detail|message|msg)$/.test(name) && value !== zh ? translate(lang, value) : value]
  )) : vars);
  return out;
}

export const LangContext = createContext<Lang>('zh');
export function useT() {
  const lang = useContext(LangContext);
  const t = useCallback((zh: string, vars?: Record<string, string | number>) => translate(lang, zh, vars), [lang]);
  return { lang, t };
}

const LANGUAGE_KEY = 'omaax.ui-language';
const LANGUAGE_EVENT = 'omaax-language-change';
const isSetting = (value: unknown): value is LangSetting => value === 'system' || value === 'zh' || value === 'en';
export function syncLanguage(setting: LangSetting) {
  try { localStorage.setItem(LANGUAGE_KEY, setting); } catch { /* Settings remain the source of truth. */ }
  window.dispatchEvent(new CustomEvent(LANGUAGE_EVENT, { detail: setting }));
}

// Login and global dialogs must share the same preference as the authenticated shell.
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [setting, setSetting] = useState<LangSetting>(() => {
    try { const value = localStorage.getItem(LANGUAGE_KEY); return isSetting(value) ? value : 'system'; } catch { return 'system'; }
  });
  const [systemLang, setSystemLang] = useState(() => resolveLang('system'));
  useEffect(() => {
    let alive = true, changed = false;
    const receive = (event: Event) => { const value = (event as CustomEvent).detail; if (isSetting(value)) { changed = true; setSetting(value); } };
    const system = () => setSystemLang(resolveLang('system'));
    window.addEventListener(LANGUAGE_EVENT, receive);
    window.addEventListener('languagechange', system);
    void window.biodsh.getSettings().then(value => { if (alive && !changed && isSetting(value.language)) setSetting(value.language); }).catch(() => {});
    return () => { alive = false; window.removeEventListener(LANGUAGE_EVENT, receive); window.removeEventListener('languagechange', system); };
  }, []);
  const lang = setting === 'system' ? systemLang : setting;
  useEffect(() => { document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'; }, [lang]);
  return createElement(LangContext.Provider, { value: lang }, children);
}
