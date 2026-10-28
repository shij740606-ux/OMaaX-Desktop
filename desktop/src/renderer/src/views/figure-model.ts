export type FigureCrop = {x:number;y:number;width:number;height:number};
export type FigureItem = {
  id: string; name: string; kind: 'svg' | 'image' | 'text' | 'rect' | 'ellipse' | 'line' | 'arrow';
  x: number; y: number; width: number; height: number; rotation: number; hidden: boolean; locked: boolean;
  groupId?: string; labelFor?: string; svg?: string; dataUrl?: string; text?: string;
  fontSize?: number; color?: string; fill?: string; stroke?: string;
  fontFamily?:string; fontWeight?:string|number; fontStyle?:'normal'|'italic'|'oblique'; textAnchor?:'start'|'middle'|'end';
  lineHeight?:number; letterSpacing?:number; opacity?:number; strokeWidth?:number; crop?:FigureCrop;
  preserveAspectRatio?:'xMidYMid meet'|'xMidYMid slice'|'none';
  source?:{name:string;kind:'svg'|'pdf'|'image';page?:number};
};
export type FigureDocument = { format: 'omaax-figure'; version: 1; title: string; width: number; height: number; widthMm: number; items: FigureItem[]; background?:string; unit?:'mm'|'in'|'px' };
export type SvgImportReport = {removedElements:string[];removedAttributes:string[];unsupportedStyles:string[];warnings:string[]};
export type FigureAlignment = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom' | 'horizontal' | 'vertical';
const NS = 'http://www.w3.org/2000/svg';
const MAX_SOURCE = 16_000_000, MAX_DOCUMENT = 64_000_000;
const tags = new Set('svg g a defs path rect circle ellipse line polyline polygon text tspan textPath title desc linearGradient radialGradient stop clipPath mask pattern marker use image symbol filter feBlend feColorMatrix feComponentTransfer feComposite feConvolveMatrix feDiffuseLighting feDisplacementMap feDistantLight feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur feMerge feMergeNode feMorphology feOffset fePointLight feSpecularLighting feSpotLight feTile feTurbulence'.split(' '));
const attributes = new Set('id x y x1 x2 y1 y2 cx cy r rx ry width height d points transform viewBox preserveAspectRatio fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset opacity font-family font-size font-weight font-style text-anchor dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing text-decoration clip-path clip-rule clipPathUnits mask maskUnits maskContentUnits mask-type filter filterUnits primitiveUnits color-interpolation color-interpolation-filters offset stop-color stop-opacity gradientUnits gradientTransform spreadMethod patternUnits patternContentUnits patternTransform markerWidth markerHeight markerUnits refX refY orient marker-start marker-mid marker-end pathLength lengthAdjust textLength dx dy rotate vector-effect overflow visibility display color style href xlink:href startOffset method spacing in in2 result type values operator k1 k2 k3 k4 mode stdDeviation flood-color flood-opacity dx dy edgeMode order kernelMatrix divisor bias targetX targetY preserveAlpha surfaceScale diffuseConstant specularConstant specularExponent scale xChannelSelector yChannelSelector azimuth elevation limitingConeAngle pointsAtX pointsAtY pointsAtZ z lighting-color baseFrequency numOctaves seed stitchTiles intercept slope amplitude exponent tableValues radius data-vector-id data-vector-locked xml:space'.split(' '));
const styleProperties = new Set('fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset opacity font-family font-size font-weight font-style text-anchor dominant-baseline alignment-baseline baseline-shift letter-spacing word-spacing text-decoration clip-path clip-rule mask mask-type filter flood-color flood-opacity lighting-color color-interpolation color-interpolation-filters stop-color stop-opacity vector-effect visibility display color overflow'.split(' '));
const vectorTags=new Set('svg g a path rect circle ellipse line polyline polygon text tspan textPath use image'.split(' '));
const definitionTags=new Set('defs clipPath mask pattern marker symbol filter linearGradient radialGradient'.split(' '));
attributes.add('data-vector-wrapper');
export function isVectorElement(el:Element):boolean {
  if(!vectorTags.has(el.localName)||el.getAttribute('data-vector-wrapper')==='true')return false;
  for(let parent=el.parentElement;parent;parent=parent.parentElement)if(definitionTags.has(parent.localName))return false;
  return true;
}
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
function fontFamily(value:unknown):string {
  if(typeof value!=='string'||!value.trim()||value.length>300||/[<>;{}\\\u0000-\u001f]/.test(value)||!safeValue(value))throw Error('字体名称无效');
  return value;
}
function fontWeight(value:unknown):string|number {
  if(typeof value==='number')return number(value,400,1,1000);
  if(typeof value==='string'&&/^(normal|bold|bolder|lighter|[1-9]\d{0,2}|1000)$/.test(value))return value;
  throw Error('字体粗细无效');
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
function declarations(value: string, report?:SvgImportReport) {
  return value.split(';').map(s => s.split(/:(.*)/s).slice(0, 2)).filter(([key, v]) => {
    const keep=v !== undefined && styleProperties.has(key.trim().toLowerCase()) && safeValue(v);
    if(!keep&&key.trim())report?.unsupportedStyles.push(key.trim());
    return keep;
  }).map(([key, v]) => `${key.trim().toLowerCase()}:${v.trim()}`).join(';');
}
// Imported markup is inert, self-contained vector data. No scripts, external resources or font downloads.
export function prepareSvg(text:string,prefix='figure-svg'):{svg:string;report:SvgImportReport} {
  const report:SvgImportReport={removedElements:[],removedAttributes:[],unsupportedStyles:[],warnings:[]};
  const doc = vectorDocument(text), root = doc.documentElement;
  const styled = new Map<Element, Map<string, string>>();
  for (const style of Array.from(root.querySelectorAll('style'))) {
    if(!report.warnings.includes('SVG 样式表已转换为行内样式；复杂 CSS 层叠请核对导入效果。'))report.warnings.push('SVG 样式表已转换为行内样式；复杂 CSS 层叠请核对导入效果。');
    if(/@import/i.test(style.textContent||''))report.unsupportedStyles.push('@import');
    const css = (style.textContent || '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/@import[^;{}]*;/gi, '');
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (match[1].includes('@') || match[1].includes('\\')) {report.unsupportedStyles.push(match[1].trim());continue;}
      const rules = declarations(match[2],report);
      if (rules) try { for (const el of Array.from(root.querySelectorAll(match[1].trim()))) { const map = styled.get(el) || new Map<string, string>(); for (const rule of rules.split(';')) { const [key, value] = rule.split(/:(.*)/s); map.set(key, value); } styled.set(el, map); } } catch {report.unsupportedStyles.push(match[1].trim());}
    }
    style.remove();
  }
  for (const [el, map] of styled) el.setAttribute('style', `${[...map].map(([key, value]) => `${key}:${value}`).join(';')};${el.getAttribute('style') || ''}`);
  for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
    if (el !== root && (!tags.has(el.localName) || el.namespaceURI !== NS)) {report.removedElements.push(el.localName);el.remove();continue;}
    for (const attr of Array.from(el.attributes)) {
      if ((attr.name === 'xmlns'||attr.name==='xmlns:xlink') && el === root) continue;
      if (!attributes.has(attr.name)) {report.removedAttributes.push(el.localName+'.'+attr.name);el.removeAttributeNode(attr);continue;}
      if(attr.name==='data-vector-id'&&!/^[\w:.-]{1,160}$/.test(attr.value)){report.removedAttributes.push(el.localName+'.'+attr.name);el.removeAttributeNode(attr);continue;}
      if(attr.name==='data-vector-locked'&&attr.value!=='true'){el.removeAttributeNode(attr);continue;}
      if (attr.name === 'style') { const clean = declarations(attr.value,report); if (clean) el.setAttribute('style', clean); else el.removeAttribute('style'); continue; }
      if (attr.localName === 'href') {
        let keep = /^#[\w:.-]+$/.test(attr.value);
        if (el.localName === 'image') { try { raster(attr.value); keep = true; } catch { keep = false; } }
        if (!keep) {report.removedAttributes.push(el.localName+'.'+attr.name);el.removeAttributeNode(attr);}
      } else if (!safeValue(attr.value)) {report.removedAttributes.push(el.localName+'.'+attr.name);el.removeAttributeNode(attr);}
    }
    if (el.localName === 'image' && !el.hasAttribute('href') && !el.hasAttribute('xlink:href')) {report.removedElements.push('image');el.remove();}
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
  const vectorIds=new Set<string>(),reservedVectorIds=new Set(Array.from(root.querySelectorAll('[data-vector-id]'),el=>el.getAttribute('data-vector-id')!));let vectorIndex=0;
  root.removeAttribute('data-vector-id');
  for(const el of Array.from(root.querySelectorAll('*'))){
    if(!isVectorElement(el)){el.removeAttribute('data-vector-id');continue;}
    let id=el.getAttribute('data-vector-id');
    if(!id||vectorIds.has(id)){do{id=idPrefix+'-vector-'+vectorIndex++;}while(reservedVectorIds.has(id)||vectorIds.has(id));el.setAttribute('data-vector-id',id);}
    vectorIds.add(id);
  }
  if(report.removedElements.length)report.warnings.push('已移除不安全或不支持的元素：'+[...new Set(report.removedElements)].join('、')+'。这些元素及其子内容不会出现在导入结果中，请核对原图。');
  if(report.removedAttributes.length||report.unsupportedStyles.length)report.warnings.push('已移除不安全或不支持的属性或样式，请核对导入图形。');
  return {svg:new XMLSerializer().serializeToString(root),report};
}
export function sanitizeSvg(text:string,prefix='figure-svg'):string{return prepareSvg(text,prefix).svg;}
export function importSvg(text: string, name = 'SVG panel'): FigureItem {
  return importSvgWithReport(text,name).item;
}
export function importSvgWithReport(text:string,name='SVG panel'):{item:FigureItem;report:SvgImportReport}{
  const id=newFigureId(),{svg,report}=prepareSvg(text,id),box=vectorDocument(svg).documentElement.getAttribute('viewBox')!.split(/[ ,]+/).map(Number);
  const scale = Math.min(1, 440 / Math.max(box[2], box[3]));
  return {item:{ id, name, kind: 'svg', svg, x: 30, y: 30, width: box[2] * scale, height: box[3] * scale, rotation: 0, hidden: false, locked: false, source:{name:name.replace(/\0/g,'').trim().slice(0,500)||'SVG panel',kind:'svg'} },report};
}
export function parseFigure(raw: unknown): FigureDocument {
  if (typeof raw === 'string' && raw.length > MAX_DOCUMENT) throw Error('拼图项目超过 64 MB');
  const v: any = typeof raw === 'string' ? JSON.parse(raw) : raw;
  if (!v || v.format !== 'omaax-figure' || v.version !== 1 || !Array.isArray(v.items) || v.items.length > 2000) throw Error('拼图项目格式无效或超过 2000 个图层');
  const ids = new Set<string>(); let size = 0;
  const items: FigureItem[] = v.items.map((n: any) => {
    if (!n || typeof n.id !== 'string' || !n.id || n.id.length > 160 || ids.has(n.id) || !['svg', 'image', 'text', 'rect','ellipse','line','arrow'].includes(n.kind)) throw Error('图层标识重复或无效');
    ids.add(n.id);
    const item: FigureItem = { id: n.id, name: String(n.name || n.kind).slice(0, 240), kind: n.kind, x: number(n.x, 0), y: number(n.y, 0), width: number(n.width, 200, 0.01), height: number(n.height, 150, 0.01), rotation: number(n.rotation, 0, -36000, 36000), hidden: !!n.hidden, locked: !!n.locked };
    for (const key of ['groupId', 'labelFor'] as const) if (typeof n[key] === 'string' && n[key].length <= 160) item[key] = n[key];
    if (n.kind === 'svg') item.svg = sanitizeSvg(n.svg, n.id);
    if (n.kind === 'image') item.dataUrl = raster(n.dataUrl);
    if (n.kind === 'text') { item.text = String(n.text || '').slice(0, 100_000); item.fontSize = number(n.fontSize, 24, 1, 2000); item.color = paint(n.color, '#17202a'); }
    if (['rect','ellipse','line','arrow'].includes(n.kind)) { item.fill = paint(n.fill, 'none'); item.stroke = paint(n.stroke, '#17202a'); }
    if(n.fontFamily!==undefined)item.fontFamily=fontFamily(n.fontFamily);
    if(n.fontWeight!==undefined)item.fontWeight=fontWeight(n.fontWeight);
    if(n.fontStyle!==undefined){if(!['normal','italic','oblique'].includes(n.fontStyle))throw Error('字体样式无效');item.fontStyle=n.fontStyle;}
    if(n.textAnchor!==undefined){if(!['start','middle','end'].includes(n.textAnchor))throw Error('文字对齐无效');item.textAnchor=n.textAnchor;}
    if(n.lineHeight!==undefined)item.lineHeight=number(n.lineHeight,1.2,.5,10);
    if(n.letterSpacing!==undefined)item.letterSpacing=number(n.letterSpacing,0,-1000,1000);
    if(n.opacity!==undefined)item.opacity=number(n.opacity,1,0,1);
    if(n.strokeWidth!==undefined)item.strokeWidth=number(n.strokeWidth,1.5,0,1000);
    if(n.preserveAspectRatio!==undefined){if(!['xMidYMid meet','xMidYMid slice','none'].includes(n.preserveAspectRatio))throw Error('图形缩放方式无效');item.preserveAspectRatio=n.preserveAspectRatio;}
    if(n.crop!==undefined){
      if(!n.crop||typeof n.crop!=='object')throw Error('裁剪范围无效');
      const crop={x:number(n.crop.x,0,0,100),y:number(n.crop.y,0,0,100),width:number(n.crop.width,100,.01,100),height:number(n.crop.height,100,.01,100)};
      if(crop.x+crop.width>100.000001||crop.y+crop.height>100.000001)throw Error('裁剪范围不能超出原图');
      item.crop=crop;
    }
    if(n.source!==undefined){
      if(!n.source||typeof n.source!=='object'||typeof n.source.name!=='string'||!n.source.name.trim()||n.source.name.length>500||n.source.name.includes('\0')||!['svg','pdf','image'].includes(n.source.kind))throw Error('图形来源信息无效');
      item.source={name:n.source.name,kind:n.source.kind};
      if(n.source.page!==undefined){if(!Number.isInteger(n.source.page)||n.source.page<1||n.source.page>100000)throw Error('图形来源页码无效');item.source.page=n.source.page;}
    }
    size += (item.svg?.length || 0) + (item.dataUrl?.length || 0) + (item.text?.length || 0);
    if (size > MAX_DOCUMENT) throw Error('拼图项目超过 64 MB');
    return item;
  });
  for (const n of items) if (n.labelFor && !ids.has(n.labelFor)) delete n.labelFor;
  const result:FigureDocument={ format: 'omaax-figure', version: 1, title: String(v.title || 'Untitled figure').slice(0, 240), width: number(v.width, 1200, 1), height: number(v.height, 850, 1), widthMm: number(v.widthMm, 180, 1, 2000), items };
  if(v.background!==undefined){if(v.background!=='transparent'&&!paint(v.background,''))throw Error('画布背景无效');result.background=v.background;}
  if(v.unit!==undefined){if(!['mm','in','px'].includes(v.unit))throw Error('尺寸单位无效');result.unit=v.unit;}
  return result;
}
export function figureSvg(doc: FigureDocument, selectedIds: string[] = []) {
  const items = doc.items.filter(n => !n.hidden).map(n => {
    let body = '';
    if (n.kind === 'svg') {
      const root = vectorDocument(sanitizeSvg(n.svg || '', n.id)).documentElement;
      root.setAttribute('width', String(n.width)); root.setAttribute('height', String(n.height));
      if(n.preserveAspectRatio)root.setAttribute('preserveAspectRatio',n.preserveAspectRatio);
      if(n.crop){const [x,y,w,h]=root.getAttribute('viewBox')!.split(/[ ,]+/).map(Number),c=n.crop;root.setAttribute('viewBox',`${x+w*c.x/100} ${y+h*c.y/100} ${w*c.width/100} ${h*c.height/100}`);root.setAttribute('overflow','hidden');}
      body = new XMLSerializer().serializeToString(root);
    } else if (n.kind === 'image'){
      body=`<image width="${n.width}" height="${n.height}" href="${raster(n.dataUrl)}" preserveAspectRatio="${n.preserveAspectRatio||'xMidYMid meet'}"/>`;
      if(n.crop){const c=n.crop;body=`<svg width="${n.width}" height="${n.height}" viewBox="${n.width*c.x/100} ${n.height*c.y/100} ${n.width*c.width/100} ${n.height*c.height/100}" preserveAspectRatio="${n.preserveAspectRatio||'xMidYMid meet'}" overflow="hidden">${body}</svg>`;}
    }else if(n.kind==='text'){
      const x=n.textAnchor==='middle'?n.width/2:n.textAnchor==='end'?n.width:0;
      body=`<text font-family="${esc(n.fontFamily||'Arial,Helvetica,sans-serif')}" font-size="${n.fontSize||24}" font-weight="${esc(n.fontWeight??'normal')}" font-style="${n.fontStyle||'normal'}" text-anchor="${n.textAnchor||'start'}" letter-spacing="${n.letterSpacing??0}" fill="${esc(paint(n.color,'#17202a'))}">${(n.text||'').split('\n').map((line,i)=>`<tspan x="${x}" y="${(n.fontSize||24)*(1+i*(n.lineHeight||1.2))}">${esc(line)}</tspan>`).join('')}</text>`;
    }else{
      const style=`fill="${esc(paint(n.fill,'none'))}" stroke="${esc(paint(n.stroke,'#17202a'))}" stroke-width="${n.strokeWidth??1.5}"`;
      if(n.kind==='ellipse')body=`<ellipse cx="${n.width/2}" cy="${n.height/2}" rx="${n.width/2}" ry="${n.height/2}" ${style}/>`;
      else if(n.kind==='line'||n.kind==='arrow'){
        const marker='arrow-'+Array.from(n.id).map(c=>c.codePointAt(0)!.toString(16)).join('-');
        body=(n.kind==='arrow'?`<defs><marker id="${marker}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="${esc(paint(n.stroke,'#17202a'))}"/></marker></defs>`:'')+`<line x1="0" y1="${n.height/2}" x2="${n.width}" y2="${n.height/2}" ${style}${n.kind==='arrow'?` marker-end="url(#${marker})"`:''}/>`;
      }else body=`<rect width="${n.width}" height="${n.height}" ${style}/>`;
    }
    return `<g data-figure-id="${esc(n.id)}" transform="translate(${n.x} ${n.y}) rotate(${n.rotation} ${n.width / 2} ${n.height / 2})" opacity="${n.opacity??1}"><rect width="${n.width}" height="${n.height}" fill="transparent"/>${body}${selectedIds.includes(n.id) ? `<rect width="${n.width}" height="${n.height}" fill="none" stroke="#147d92" stroke-width="2" stroke-dasharray="7 4" pointer-events="none"/>` : ''}</g>`;
  }).join('');
  const background=doc.background==='transparent'?'':`<rect width="${doc.width}" height="${doc.height}" fill="${esc(paint(doc.background,'white'))}"/>`;
  return `<svg xmlns="${NS}" width="${doc.widthMm}mm" height="${doc.widthMm * doc.height / doc.width}mm" viewBox="0 0 ${doc.width} ${doc.height}"><title>${esc(doc.title)}</title><metadata>OMaaX figure v1</metadata>${background}${items}</svg>`;
}
const textContentTags=new Set(['text','tspan','textPath','a']);
function svgTextNodes(element:Element):Text[]{
  const nodes:Text[]=[];
  const visit=(parent:Element)=>{for(const child of Array.from(parent.childNodes)){
    if(child.nodeType===Node.TEXT_NODE)nodes.push(child as Text);
    else if(child.nodeType===Node.ELEMENT_NODE&&textContentTags.has((child as Element).localName))visit(child as Element);
  }};
  visit(element);return nodes;
}
export function svgTextContent(element:Element):string{
  const text=svgTextNodes(element).map(node=>node.data).join('');
  for(let parent:Element|null=element;parent;parent=parent.parentElement){const space=parent.getAttribute('xml:space');if(space)return space==='preserve'?text:text.trim();}
  return text.trim();
}
export function replaceSvgElementText(element:Element,value:string){
  const nodes=svgTextNodes(element);
  // Metadata and indentation are not the label. Keep the first visible run's anchor and styling.
  let first=nodes.find(node=>node.data.trim());
  if(!first){
    const positioned=Array.from(element.querySelectorAll('tspan,textPath')).find(candidate=>{
      for(let parent:Element|null=candidate;parent&&parent!==element;parent=parent.parentElement)if(!textContentTags.has(parent.localName))return false;
      return true;
    });
    if(positioned){first=nodes.find(node=>node.parentElement===positioned);if(!first){first=element.ownerDocument.createTextNode('');positioned.appendChild(first);}}
    else first=nodes[0];
  }
  if(!first){first=element.ownerDocument.createTextNode('');element.appendChild(first);}
  for(const node of nodes)node.data='';first.data=value;
}
export function svgTextEntries(svg: string) { return Array.from(vectorDocument(sanitizeSvg(svg)).querySelectorAll('text')).map((el, index) => ({ index, text: svgTextContent(el) })); }
export function replaceSvgText(svg: string, index: number, value: string) {
  const doc = vectorDocument(sanitizeSvg(svg)), target = doc.querySelectorAll('text')[index];
  if (!target) throw Error('请选择有效的文字元素');
  replaceSvgElementText(target,value.slice(0,100_000));
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
   if(context){context.font=`${item.fontStyle||'normal'} ${item.fontWeight||'normal'} ${size}px ${item.fontFamily||'Arial,Helvetica,sans-serif'}`;context.textAlign='left';}
  (item.text||'').split('\n').forEach((line,index)=>{
     const metrics=context?.measureText(line),baseline=size*(1+index*(item.lineHeight||1.2)),spacing=Math.max(0,Array.from(line).length-1)*(item.letterSpacing||0),advance=(metrics?.width??line.length*size)+spacing;
     const origin=item.textAnchor==='middle'?(item.width-advance)/2:item.textAnchor==='end'?item.width-advance:0;
     box.left=Math.min(box.left,origin-(metrics?.actualBoundingBoxLeft||0));
    box.top=Math.min(box.top,baseline-(metrics?.actualBoundingBoxAscent||size));
     box.right=Math.max(box.right,origin+(metrics?Math.max(metrics.width,metrics.actualBoundingBoxRight||0):line.length*size)+spacing+2);
    box.bottom=Math.max(box.bottom,baseline+(metrics?.actualBoundingBoxDescent||size*.25)+2);
  });
  return box;
}
// Match the font used by figureSvg; old projects may have a text box smaller than its glyphs.
export function fitTextBox(item:FigureItem):FigureItem {
  if(item.kind!=='text')return item;
  const box=textBounds(item),width=Math.ceil(box.right-box.left),height=Math.ceil(box.bottom-box.top);
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
