export type FigureItem = {
  id: string; name: string; kind: 'svg' | 'image' | 'text' | 'rect';
  x: number; y: number; width: number; height: number; rotation: number; hidden: boolean; locked: boolean;
  groupId?: string; labelFor?: string; svg?: string; dataUrl?: string; text?: string;
  fontSize?: number; color?: string; fill?: string; stroke?: string;
};
export type FigureDocument = { format: 'omaax-figure'; version: 1; title: string; width: number; height: number; widthMm: number; items: FigureItem[] };
export type FigureAlignment = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom' | 'horizontal' | 'vertical';
const NS = 'http://www.w3.org/2000/svg';
const MAX_SOURCE = 16_000_000, MAX_DOCUMENT = 64_000_000;
const tags = new Set('svg g a defs path rect circle ellipse line polyline polygon text tspan textPath title desc linearGradient radialGradient stop clipPath mask pattern marker use image symbol'.split(' '));
const attributes = new Set('id x y x1 x2 y1 y2 cx cy r rx ry width height d points transform viewBox preserveAspectRatio fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset opacity font-family font-size font-weight font-style text-anchor dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing text-decoration clip-path clip-rule mask filter offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod patternUnits patternContentUnits patternTransform markerWidth markerHeight markerUnits refX refY orient marker-start marker-mid marker-end pathLength lengthAdjust textLength dx dy rotate vector-effect overflow visibility display color style href xlink:href startOffset method spacing'.split(' '));
const styleProperties = new Set('fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset opacity font-family font-size font-weight font-style text-anchor dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing text-decoration clip-path clip-rule mask stop-color stop-opacity vector-effect visibility display color overflow'.split(' '));
const esc = (value: unknown) => String(value).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]!));
export const newFigureId = (prefix = 'item') => `${prefix}-${Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('')}`;
export const blankFigure = (title = 'Untitled figure'): FigureDocument => ({ format: 'omaax-figure', version: 1, title, width: 1200, height: 850, widthMm: 180, items: [] });
function number(value: unknown, fallback: number, minimum = -100_000, maximum = 100_000) {
  if (value === undefined) return fallback;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) throw Error('图形尺寸或坐标无效');
  return value;
}
function safeValue(value: string) {
  return !/[\\\u0000-\u0008\u000b\u000c\u000e-\u001f]|(?:javascript|vbscript|https?|file|data):|expression\s*\(|@import/i.test(value) &&
    !/url\s*\(/i.test(value.replace(/url\(\s*['"]?#[\w:.-]+['"]?\s*\)/gi, ''));
}
function paint(value: unknown, fallback: string) {
  return typeof value === 'string' && /^(?:#[\da-f]{3,8}|[a-z]+|(?:rgb|hsl)a?\([\d.% ,+/-]+\))$/i.test(value) ? value : fallback;
}
function raster(value: unknown): string {
  if (typeof value !== 'string' || value.length > MAX_SOURCE || !/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(value)) throw Error('请使用小于 12 MB 的本地 PNG、JPG、WebP 或 GIF 图片');
  const header = atob(value.slice(value.indexOf(',') + 1, value.indexOf(',') + 33));
  if (!header.startsWith('\x89PNG\r\n\x1a\n') && !header.startsWith('\xff\xd8\xff') && !header.startsWith('GIF8') && !(header.startsWith('RIFF') && header.slice(8, 12) === 'WEBP')) throw Error('图片内容与支持的格式不匹配');
  return value;
}
function vectorDocument(text: string) {
  if (typeof text !== 'string' || !text.trim() || text.length > MAX_SOURCE) throw Error('SVG 为空或超过 16 MB');
  if (/<!ENTITY/i.test(text)) throw Error('SVG 包含不支持的实体声明');
  // Plotting packages commonly include a public SVG DTD. Strip it without fetching it; never allow an internal subset.
  text = text.replace(/<!DOCTYPE\s+svg(?:\s+PUBLIC\s+(?:"[^"]*"|'[^']*')\s+(?:"[^"]*"|'[^']*')|\s+SYSTEM\s+(?:"[^"]*"|'[^']*'))?\s*>/gi, '');
  if (/<!DOCTYPE/i.test(text)) throw Error('SVG 包含不支持的实体声明');
  let doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  if (doc.querySelector('parsererror') || doc.documentElement.localName !== 'svg') throw Error('SVG 文件格式无效');
  if (!doc.documentElement.namespaceURI) { doc.documentElement.setAttribute('xmlns', NS); doc = new DOMParser().parseFromString(new XMLSerializer().serializeToString(doc.documentElement), 'image/svg+xml'); }
  if (doc.documentElement.namespaceURI !== NS) throw Error('SVG 命名空间无效');
  if (doc.querySelectorAll('*').length > 100_000) throw Error('SVG 元素过多，请拆分后导入');
  return doc;
}
function declarations(value: string) {
  return value.split(';').map(s => s.split(/:(.*)/s).slice(0, 2)).filter(([key, v]) => v !== undefined && styleProperties.has(key.trim().toLowerCase()) && safeValue(v)).map(([key, v]) => `${key.trim().toLowerCase()}:${v.trim()}`).join(';');
}
// Imported markup is inert, self-contained vector data. No scripts, external resources or font downloads.
export function sanitizeSvg(text: string, prefix = 'figure-svg'): string {
  const doc = vectorDocument(text), root = doc.documentElement;
  const styled = new Map<Element, Map<string, string>>();
  for (const style of Array.from(root.querySelectorAll('style'))) {
    const css = (style.textContent || '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/@import[^;{}]*;/gi, '');
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (match[1].includes('@') || match[1].includes('\\')) continue;
      const rules = declarations(match[2]);
      if (rules) try { for (const el of Array.from(root.querySelectorAll(match[1].trim()))) { const map = styled.get(el) || new Map<string, string>(); for (const rule of rules.split(';')) { const [key, value] = rule.split(/:(.*)/s); map.set(key, value); } styled.set(el, map); } } catch { /* Unsupported selectors do not affect other vector content. */ }
    }
    style.remove();
  }
  for (const [el, map] of styled) el.setAttribute('style', `${[...map].map(([key, value]) => `${key}:${value}`).join(';')};${el.getAttribute('style') || ''}`);
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    if (el !== root && (!tags.has(el.localName) || el.namespaceURI !== NS)) { el.remove(); continue; }
    for (const attr of Array.from(el.attributes)) {
      if (attr.name === 'xmlns' && el === root) continue;
      if (!attributes.has(attr.name)) { el.removeAttributeNode(attr); continue; }
      if (attr.name === 'style') { const clean = declarations(attr.value); if (clean) el.setAttribute('style', clean); else el.removeAttribute('style'); continue; }
      if (attr.localName === 'href') {
        let keep = /^#[\w:.-]+$/.test(attr.value);
        if (el.localName === 'image') { try { raster(attr.value); keep = true; } catch { keep = false; } }
        if (!keep) el.removeAttributeNode(attr);
      } else if (!safeValue(attr.value)) el.removeAttributeNode(attr);
    }
    if (el.localName === 'image' && !el.hasAttribute('href') && !el.hasAttribute('xlink:href')) el.remove();
  }
  const ids = new Map<string, string>(); let index = 0;
  const hash = Array.from(prefix).reduce((h, c) => Math.imul(h ^ c.codePointAt(0)!, 16777619) >>> 0, 2166136261).toString(16);
  const idPrefix = `${prefix.replace(/[^\w-]/g, '').slice(0, 80) || 'figure-svg'}-${hash}`;
  for (const el of [root, ...Array.from(root.querySelectorAll('[id]'))]) {
    const old = el.getAttribute('id'); if (!old) continue;
    const next = `${idPrefix}-${index++}`; if (!ids.has(old)) ids.set(old, next); el.setAttribute('id', next);
  }
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) for (const attr of Array.from(el.attributes)) {
    if (attr.localName === 'href' && attr.value.startsWith('#')) {
      const id = ids.get(attr.value.slice(1)); if (id) el.setAttribute(attr.name, `#${id}`); else el.removeAttributeNode(attr);
    } else if (/url\(/i.test(attr.value)) {
      el.setAttribute(attr.name, attr.value.replace(/url\(\s*['"]?#([\w:.-]+)['"]?\s*\)/gi, (_, id) => ids.has(id) ? `url(#${ids.get(id)})` : 'none'));
    }
  }
  root.setAttribute('xmlns', NS);
  const box = root.getAttribute('viewBox')?.trim().split(/[ ,]+/).map(Number);
  if (!box || box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) {
    const size = (value: string | null, fallback: number) => { const m = value?.match(/^([\d.]+)(px|pt|mm|cm|in)?$/); return m ? Number(m[1]) * ({ px: 1, pt: 96 / 72, mm: 96 / 25.4, cm: 96 / 2.54, in: 96 }[m[2] || 'px'] || 1) : fallback; };
    root.setAttribute('viewBox', `0 0 ${number(size(root.getAttribute('width'), 600), 600, 0.01)} ${number(size(root.getAttribute('height'), 400), 400, 0.01)}`);
  } else box.forEach((v, i) => number(v, 0, i < 2 ? -100_000 : 0.01));
  root.removeAttribute('width'); root.removeAttribute('height');
  return new XMLSerializer().serializeToString(root);
}
export function importSvg(text: string, name = 'SVG panel'): FigureItem {
  const id = newFigureId(), svg = sanitizeSvg(text, id), box = vectorDocument(svg).documentElement.getAttribute('viewBox')!.split(/[ ,]+/).map(Number);
  const scale = Math.min(1, 440 / Math.max(box[2], box[3]));
  return { id, name, kind: 'svg', svg, x: 30, y: 30, width: box[2] * scale, height: box[3] * scale, rotation: 0, hidden: false, locked: false };
}
export function parseFigure(raw: unknown): FigureDocument {
  if (typeof raw === 'string' && raw.length > MAX_DOCUMENT) throw Error('拼图项目超过 64 MB');
  const v: any = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (!v || v.format !== 'omaax-figure' || v.version !== 1 || !Array.isArray(v.items) || v.items.length > 2000) throw Error('拼图项目格式无效或超过 2000 个图层');
  const ids = new Set<string>(); let size = 0;
  const items: FigureItem[] = v.items.map((n: any) => {
    if (!n || typeof n.id !== 'string' || !n.id || n.id.length > 160 || ids.has(n.id) || !['svg', 'image', 'text', 'rect'].includes(n.kind)) throw Error('图层标识重复或无效');
    ids.add(n.id);
    const item: FigureItem = { id: n.id, name: String(n.name || n.kind).slice(0, 240), kind: n.kind, x: number(n.x, 0), y: number(n.y, 0), width: number(n.width, 200, 0.01), height: number(n.height, 150, 0.01), rotation: number(n.rotation, 0, -36000, 36000), hidden: !!n.hidden, locked: !!n.locked };
    for (const key of ['groupId', 'labelFor'] as const) if (typeof n[key] === 'string' && n[key].length <= 160) item[key] = n[key];
    if (n.kind === 'svg') item.svg = sanitizeSvg(n.svg, n.id);
    if (n.kind === 'image') item.dataUrl = raster(n.dataUrl);
    if (n.kind === 'text') { item.text = String(n.text || '').slice(0, 100_000); item.fontSize = number(n.fontSize, 24, 1, 2000); item.color = paint(n.color, '#17202a'); }
    if (n.kind === 'rect') { item.fill = paint(n.fill, 'none'); item.stroke = paint(n.stroke, '#17202a'); }
    size += (item.svg?.length || 0) + (item.dataUrl?.length || 0) + (item.text?.length || 0);
    if (size > MAX_DOCUMENT) throw Error('拼图项目超过 64 MB');
    return item;
  });
  for (const n of items) if (n.labelFor && !ids.has(n.labelFor)) delete n.labelFor;
  return { format: 'omaax-figure', version: 1, title: String(v.title || 'Untitled figure').slice(0, 240), width: number(v.width, 1200, 1), height: number(v.height, 850, 1), widthMm: number(v.widthMm, 180, 1, 2000), items };
}
export function figureSvg(doc: FigureDocument, selectedIds: string[] = []) {
  const items = doc.items.filter(n => !n.hidden).map(n => {
    let body = '';
    if (n.kind === 'svg') {
      const root = vectorDocument(sanitizeSvg(n.svg || '', n.id)).documentElement;
      root.setAttribute('width', String(n.width)); root.setAttribute('height', String(n.height));
      body = new XMLSerializer().serializeToString(root);
    } else if (n.kind === 'image') body = `<image width="${n.width}" height="${n.height}" href="${raster(n.dataUrl)}" preserveAspectRatio="xMidYMid meet"/>`;
    else if (n.kind === 'text') body = `<text font-family="Arial,Helvetica,sans-serif" font-size="${n.fontSize || 24}" fill="${esc(paint(n.color, '#17202a'))}">${(n.text || '').split('\n').map((line, i) => `<tspan x="0" y="${(n.fontSize || 24) * (1 + i * 1.2)}">${esc(line)}</tspan>`).join('')}</text>`;
    else body = `<rect width="${n.width}" height="${n.height}" fill="${esc(paint(n.fill, 'none'))}" stroke="${esc(paint(n.stroke, '#17202a'))}" stroke-width="1.5"/>`;
    return `<g data-figure-id="${esc(n.id)}" transform="translate(${n.x} ${n.y}) rotate(${n.rotation} ${n.width / 2} ${n.height / 2})"><rect width="${n.width}" height="${n.height}" fill="transparent"/>${body}${selectedIds.includes(n.id) ? `<rect width="${n.width}" height="${n.height}" fill="none" stroke="#147d92" stroke-width="2" stroke-dasharray="7 4" pointer-events="none"/>` : ''}</g>`;
  }).join('');
  return `<svg xmlns="${NS}" width="${doc.widthMm}mm" height="${doc.widthMm * doc.height / doc.width}mm" viewBox="0 0 ${doc.width} ${doc.height}"><title>${esc(doc.title)}</title><metadata>OMaaX figure v1</metadata><rect width="${doc.width}" height="${doc.height}" fill="white"/>${items}</svg>`;
}
export function svgTextEntries(svg: string) { return Array.from(vectorDocument(sanitizeSvg(svg)).querySelectorAll('text')).map((el, index) => ({ index, text: el.textContent || '' })); }
export function replaceSvgText(svg: string, index: number, value: string) {
  const doc = vectorDocument(sanitizeSvg(svg)), target = doc.querySelectorAll('text')[index];
  if (!target) throw Error('请选择有效的文字元素');
  const walker = doc.createTreeWalker(target, NodeFilter.SHOW_TEXT), nodes: Node[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const first = nodes.find(node => node.textContent?.trim());
  if (first) for (const node of nodes) node.textContent = node === first ? value.slice(0, 100_000) : '';
  else target.textContent = value.slice(0, 100_000);
  return new XMLSerializer().serializeToString(doc.documentElement);
}
const colorAttrs = new Set(['fill', 'stroke', 'color', 'stop-color']);
export function svgColors(svg: string) {
  const result = new Set<string>(), root = vectorDocument(sanitizeSvg(svg)).documentElement;
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    for (const attr of colorAttrs) { const v = el.getAttribute(attr); if (v && paint(v, '') && !['none', 'currentColor', 'inherit'].includes(v)) result.add(v); }
    for (const part of (el.getAttribute('style') || '').split(';')) { const [key, value] = part.split(/:(.*)/s); if (colorAttrs.has(key) && value && paint(value, '') && value !== 'none') result.add(value); }
  }
  return [...result];
}
export function replaceSvgColor(svg: string, from: string, to: string) {
  if (!paint(to, '')) throw Error('颜色格式无效');
  const doc = vectorDocument(sanitizeSvg(svg)), root = doc.documentElement;
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    for (const attr of colorAttrs) if (el.getAttribute(attr) === from) el.setAttribute(attr, to);
    if (el.hasAttribute('style')) el.setAttribute('style', (el.getAttribute('style') || '').split(';').map(part => { const [key, value] = part.split(/:(.*)/s); return colorAttrs.has(key) && value === from ? `${key}:${to}` : part; }).join(';'));
  }
  return new XMLSerializer().serializeToString(root);
}
let textContext: CanvasRenderingContext2D | null | undefined;
function textBounds(item: FigureItem) {
  const box={left:0,top:0,right:item.width,bottom:item.height};
  if(item.kind!=='text')return box;
  const context=textContext??=document.createElement('canvas').getContext('2d'),size=item.fontSize||24;
  if(context)context.font=`${size}px Arial,Helvetica,sans-serif`;
  (item.text||'').split('\n').forEach((line,index)=>{
    const metrics=context?.measureText(line),baseline=size*(1+index*1.2);
    box.left=Math.min(box.left,-(metrics?.actualBoundingBoxLeft||0));
    box.top=Math.min(box.top,baseline-(metrics?.actualBoundingBoxAscent||size));
    box.right=Math.max(box.right,(metrics?Math.max(metrics.width,metrics.actualBoundingBoxRight||0):line.length*size)+2);
    box.bottom=Math.max(box.bottom,baseline+(metrics?.actualBoundingBoxDescent||size*.25)+2);
  });
  return box;
}
// Match the font used by figureSvg; old projects may have a text box smaller than its glyphs.
export function fitTextBox(item:FigureItem):FigureItem {
  if(item.kind!=='text')return item;
  const box=textBounds(item),width=Math.ceil(box.right),height=Math.ceil(box.bottom);
  if(width>100_000||height>100_000)throw Error('文字内容过长，请缩短文字或减小字号。');
  return width===item.width&&height===item.height?item:{...item,width,height};
}
function bounds(items: FigureItem[], measureText=false) {
  const boxes = items.map(n => {
    const box=measureText?textBounds(n):{left:0,top:0,right:n.width,bottom:n.height},a=n.rotation*Math.PI/180,c=Math.cos(a),s=Math.sin(a),cx=n.width/2,cy=n.height/2;
    const corners=[[box.left,box.top],[box.right,box.top],[box.right,box.bottom],[box.left,box.bottom]].map(([x,y])=>({x:n.x+cx+(x-cx)*c-(y-cy)*s,y:n.y+cy+(x-cx)*s+(y-cy)*c}));
    return {left:Math.min(...corners.map(p=>p.x)),top:Math.min(...corners.map(p=>p.y)),right:Math.max(...corners.map(p=>p.x)),bottom:Math.max(...corners.map(p=>p.y))};
  });
  const left = Math.min(...boxes.map(b => b.left)), top = Math.min(...boxes.map(b => b.top)), right = Math.max(...boxes.map(b => b.right)), bottom = Math.max(...boxes.map(b => b.bottom));
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}
function units(doc: FigureDocument, ids: string[]) {
  const groups = new Set(doc.items.filter(n => ids.includes(n.id) && n.groupId).map(n => n.groupId));
  const chosen = new Set(doc.items.filter(n => ids.includes(n.id) || (n.groupId && groups.has(n.groupId))).map(n => n.id));
  const map = new Map<string, FigureItem[]>();
  for (const n of doc.items) {
    if (n.labelFor || !chosen.has(n.id) || n.hidden) continue;
    const key = n.groupId || n.id; map.set(key, [...(map.get(key) || []), n]);
  }
  for (const members of map.values()) for (const label of doc.items) if (label.labelFor && members.some(n => n.id === label.labelFor) && !label.hidden) members.push(label);
  for (const n of doc.items) if (n.labelFor && chosen.has(n.id) && !n.hidden && ![...map.values()].some(m => m.includes(n))) map.set(n.id, [n]);
  return [...map.values()].filter(m => !m.some(n => n.locked));
}
function moved(doc: FigureDocument, positions: Map<string, { x: number; y: number }>): FigureDocument { return { ...doc, items: doc.items.map(n => positions.has(n.id) ? { ...n, ...positions.get(n.id)! } : n) }; }
export function moveItems(doc: FigureDocument, ids: string[], dx: number, dy: number) {
  number(dx, 0); number(dy, 0); const positions = new Map<string, { x: number; y: number }>();
  for (const group of units(doc, ids)) for (const n of group) positions.set(n.id, { x: number(n.x + dx, 0), y: number(n.y + dy, 0) });
  return moved(doc, positions);
}
export function gridLayout(doc: FigureDocument, ids: string[], columns = 2, gap = 24) {
  const parts = units(doc, ids); if (!parts.length) return doc;
  columns = Math.max(1, Math.min(parts.length, Math.round(number(columns, 2, 1, 2000)))); gap = number(gap, 24, 0, 10000);
  const boxes = parts.map(members => bounds(members)), left = Math.min(...boxes.map(b => b.left)), top = Math.min(...boxes.map(b => b.top)), w = Math.max(...boxes.map(b => b.width)), h = Math.max(...boxes.map(b => b.height));
  const positions = new Map<string, { x: number; y: number }>();
  parts.forEach((members, i) => { const dx = left + (i % columns) * (w + gap) - boxes[i].left, dy = top + Math.floor(i / columns) * (h + gap) - boxes[i].top; members.forEach(n => positions.set(n.id, { x: n.x + dx, y: n.y + dy })); });
  return moved(doc, positions);
}
export function alignItems(doc: FigureDocument, ids: string[], mode: FigureAlignment) {
  const parts = units(doc, ids); if (parts.length < 2) return doc;
  const total = bounds(parts.flat()), entries = parts.map(members => ({ members, box: bounds(members) }));
  const positions = new Map<string, { x: number; y: number }>();
  if (mode === 'horizontal' || mode === 'vertical') {
    const horizontal = mode === 'horizontal'; entries.sort((a, b) => horizontal ? a.box.left - b.box.left : a.box.top - b.box.top);
    const gap = ((horizontal ? total.width : total.height) - entries.reduce((s, e) => s + (horizontal ? e.box.width : e.box.height), 0)) / (entries.length - 1);
    let cursor = horizontal ? total.left : total.top;
    for (const { members, box } of entries) { const dx = horizontal ? cursor - box.left : 0, dy = horizontal ? 0 : cursor - box.top; members.forEach(n => positions.set(n.id, { x: n.x + dx, y: n.y + dy })); cursor += (horizontal ? box.width : box.height) + gap; }
  } else for (const { members, box } of entries) {
    const dx = mode === 'left' ? total.left - box.left : mode === 'right' ? total.right - box.right : mode === 'center' ? (total.left + total.right - box.left - box.right) / 2 : 0;
    const dy = mode === 'top' ? total.top - box.top : mode === 'bottom' ? total.bottom - box.bottom : mode === 'middle' ? (total.top + total.bottom - box.top - box.bottom) / 2 : 0;
    members.forEach(n => positions.set(n.id, { x: n.x + dx, y: n.y + dy }));
  }
  return moved(doc, positions);
}
export function fitFigure(doc: FigureDocument, padding = 28): FigureDocument {
  const visible = doc.items.filter(n => !n.hidden); if (!visible.length) return doc;
  padding = number(padding, 28, 0, 10000); const b = bounds(visible,true), dx = padding - b.left, dy = padding - b.top;
  return { ...doc, width: number(b.width + 2 * padding, 1, 1), height: number(b.height + 2 * padding, 1, 1), items: doc.items.map(n => ({ ...n, x: n.x + dx, y: n.y + dy })) };
}
export function addPanelLabels(doc: FigureDocument, ids: string[]) {
  const panels = doc.items.filter(n => ids.includes(n.id) && !n.labelFor && !n.hidden && !n.locked), labelled = new Set(doc.items.filter(n => n.labelFor).map(n => n.labelFor));
  function letter(index: number): string { return index >= 26 ? letter(Math.floor(index / 26) - 1) + String.fromCharCode(65 + index % 26) : String.fromCharCode(65 + index); }
  const labels: FigureItem[] = panels.flatMap((n, i) => labelled.has(n.id) ? [] : [{ id: newFigureId('label'), name: `Panel ${letter(i)}`, kind: 'text', labelFor: n.id, x: n.x, y: n.y - 32, width: 50, height: 28, rotation: 0, hidden: false, locked: false, text: letter(i), fontSize: 24, color: '#17202a' }]);
  return labels.length ? { ...doc, items: [...doc.items, ...labels] } : doc;
}
function pngDpi(dataUrl: string, dpi: number) {
  const raw = Uint8Array.from(atob(dataUrl.split(',')[1]), c => c.charCodeAt(0)), chunk = new Uint8Array(21), view = new DataView(chunk.buffer);
  view.setUint32(0, 9); chunk.set([112, 72, 89, 115], 4); view.setUint32(8, Math.round(dpi / 0.0254)); view.setUint32(12, Math.round(dpi / 0.0254)); chunk[16] = 1;
  let crc = 0xffffffff; for (const b of chunk.slice(4, 17)) { crc ^= b; for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); } view.setUint32(17, (crc ^ 0xffffffff) >>> 0);
  const chunks: Uint8Array[] = [raw.slice(0, 8), chunk];
  for (let offset = 8; offset < raw.length;) { const size = new DataView(raw.buffer, raw.byteOffset + offset, 4).getUint32(0) + 12; if (String.fromCharCode(...raw.slice(offset + 4, offset + 8)) !== 'pHYs') chunks.push(raw.slice(offset, offset + size)); offset += size; }
  // Place pHYs after IHDR, as required by PNG readers.
  [chunks[1], chunks[2]] = [chunks[2], chunks[1]];
  const result = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0)); let offset = 0; for (const c of chunks) { result.set(c, offset); offset += c.length; }
  let encoded = ''; for (let i = 0; i < result.length; i += 0x8000) encoded += String.fromCharCode(...result.subarray(i, i + 0x8000));
  return `data:image/png;base64,${btoa(encoded)}`;
}
export async function exportFigurePng(doc: FigureDocument, dpi = 300) {
  number(dpi, 300, 72, 2400);
  const width = Math.ceil(doc.widthMm / 25.4 * dpi), height = Math.ceil(width * doc.height / doc.width);
  if (!Number.isFinite(width * height) || width > 16000 || height > 16000 || width * height > 64_000_000) throw Error('PNG 超过单边 16000 像素或 6400 万像素，请减小印刷宽度或分辨率');
  const url = URL.createObjectURL(new Blob([figureSvg(doc)], { type: 'image/svg+xml' }));
  try {
    const image = new Image(); image.src = url; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d'); if (!ctx) throw Error('无法创建导出图片');
    ctx.drawImage(image, 0, 0, width, height); return pngDpi(canvas.toDataURL('image/png'), dpi);
  } finally { URL.revokeObjectURL(url); }
}
