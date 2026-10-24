import {ref} from 'vue';
import {EDUCATION_EN} from './i18n.en.js';
import {EDUCATION_BACKEND_EN} from './i18n.backend.en.js';

export const educationLocale = ref('zh');
const normalize = value => value.replace(/\s+/g, ' ').trim();
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const dictionary = {...EDUCATION_EN, ...EDUCATION_BACKEND_EN};
const patterns = Object.entries(dictionary).filter(([key]) => /\{\d+\}/.test(key)).sort((a,b)=>b[0].replace(/\{\d+\}/g,'').length-a[0].replace(/\{\d+\}/g,'').length).map(([key, value]) => {
  const captures = [];
  const source = key.split(/(\{\d+\})/).map(part => {
    if (/^\{\d+\}$/.test(part)) { captures.push(part); return '([\\s\\S]*?)'; }
    return escape(part);
  }).join('');
  return {key, regex:new RegExp(`^${source}$`), value, captures};
});

function translateLabel(key, depth = 0) {
  if (Object.hasOwn(dictionary, key)) return dictionary[key];
  if (depth > 5) return key;
  const list = key.split('、');
  if (list.length > 1 && list.every(v => Object.hasOwn(dictionary, v) || /^(?:[^:：;；,，()]+\(\d+字\)|[上右下左]-?\d+(?:\.\d+)?)$/.test(v))) {
    return list.map(v => translateLabel(v, depth + 1)).join(', ');
  }
  for (const pattern of patterns) {
    const match = pattern.regex.exec(key);
    if (!match) continue;
    return pattern.value.replace(/\{\d+\}/g, token => {
      const index = pattern.captures.indexOf(token);
      const value = match[index + 1] ?? token;
      // Quotes, journal names, file names and diagnostic paths are content.
      // Their surrounding labels are localized, but the captured data is retained.
      if (/样例|文件|期刊|证据|建议：|引文|失败：|路径|条目/.test(pattern.key)) return value;
      return translateLabel(value, depth + 1);
    });
  }
  if (key.includes('、')) return key.split('、').map(v => translateLabel(v, depth + 1)).join(', ');
  const side = /^(上|右|下|左)(-?\d+(?:\.\d+)?)$/.exec(key);
  if (side) return `${dictionary[side[1]]} ${side[2]}`;
  return key;
}

/** Translate authored interface labels at render time. Never apply to manuscript,
 * model answers, file paths, source code, or text entered by the user. */
export function et(value) {
  // Reading the ref here makes Vue templates and computed labels react immediately.
  if (educationLocale.value !== 'en' || typeof value !== 'string') return value;
  const key = normalize(value);
  const translated = translateLabel(key);
  if (translated === key) return value;
  return `${value.match(/^\s*/)?.[0] || ''}${translated}${value.match(/\s*$/)?.[0] || ''}`;
}

/** Lists of interface labels, not prose from uploaded documents or the model. */
export function etList(value, separator = ' · ') {
  return typeof value === 'string' ? value.split(separator).map(et).join(separator) : value;
}

export function setEducationLocale(value) {
  if (value !== 'zh' && value !== 'en') return false;
  educationLocale.value = value;
  if (typeof document !== 'undefined') {
    document.documentElement.lang = value === 'en' ? 'en' : 'zh-CN';
    document.title = value === 'en' ? 'OMaaX Education & Research' : 'OMaaX 教育科研';
  }
  return true;
}

export function isTrustedHostMessage(event, host = globalThis.parent, referrer = globalThis.document?.referrer || '') {
  if (event.source !== host || host === globalThis.window) return false;
  try {
    if (referrer) {
      const source = new URL(referrer);
      return event.origin === source.origin || (source.protocol === 'tauri:' && source.hostname === 'localhost' && event.origin === 'tauri://localhost');
    }
    // Tauri may omit the referrer. Only the embedding window at a local app origin
    // can configure this workspace; unrelated frames and remote sites are rejected.
    const url = new URL(event.origin);
    return ['http:', 'https:', 'tauri:'].includes(url.protocol) && ['localhost', '127.0.0.1', '[::1]', 'tauri.localhost'].includes(url.hostname);
  } catch { return false; }
}

export function applyHostPreferences(event) {
  if (!isTrustedHostMessage(event)) return false;
  if (event.data?.type === 'omaax-locale') return setEducationLocale(event.data.language);
  if (event.data?.type === 'omaax-theme' && ['light','dark'].includes(event.data.theme)) {
    document.documentElement.dataset.theme = event.data.theme;
    return true;
  }
  return false;
}
