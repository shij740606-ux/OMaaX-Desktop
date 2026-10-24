import {esc} from './drawing-model';
import {translate,type Lang} from '../i18n';
export type StructureFile={name:string;format:'pdb'|'sdf'|'mol2'|'cif'|'pdbqt';text:string};
export type InteractionRow={ligand:string;residue:string;type:string;distance:number|null;atoms?:unknown};
export type InteractionResult={format:'omaax-interactions';version:number;engine:string;engineVersion:string;proteinSha256:string;ligandSha256:string;note:string;poses:{index:number;name:string;rows:InteractionRow[];ligandSvg:string}[]};
export function structureFile(name:string,text:string):StructureFile{
 const format=name.split('.').at(-1)?.toLowerCase();
 if(!['pdb','sdf','mol2','cif','pdbqt'].includes(format||''))throw Error('请导入 PDB、SDF、MOL2、CIF 或 PDBQT 文件');
 if(!text.trim()||new TextEncoder().encode(text).length>5_000_000)throw Error('结构文件为空或超过 5 MB');
 return {name:name.slice(0,160),format:format as StructureFile['format'],text};
}
export function parseInteractions(v:any):InteractionResult{
 if(!v||v.format!=='omaax-interactions'||!Array.isArray(v.poses)||!v.poses.length||v.poses.length>50||typeof v.engine!=='string'||typeof v.engineVersion!=='string'||typeof v.note!=='string')throw Error('相互作用结果无效');
 for(const p of v.poses){
  if(typeof p.name!=='string'||!Array.isArray(p.rows)||p.rows.length>10000||typeof p.ligandSvg!=='string'||p.ligandSvg.length>1_000_000)throw Error('相互作用结果过大或不完整');
  for(const r of p.rows)if(typeof r.residue!=='string'||typeof r.type!=='string'||(r.distance!==null&&!Number.isFinite(r.distance)))throw Error('相互作用记录无效');
 }
 return v;
}
// Project files are untrusted: allow only vector geometry and text, never active SVG content.
export function cleanVector(raw:string){
 const doc=new DOMParser().parseFromString(raw,'image/svg+xml');
 if(doc.querySelector('parsererror')||doc.documentElement.localName!=='svg')throw Error('二维图格式无效');
 const allowed=new Set('svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop text tspan title desc'.split(' '));
 function clean(el:Element){
  for(const child of Array.from(el.children)){if(!allowed.has(child.localName)){child.remove();}else clean(child);}
  for(const attr of Array.from(el.attributes))if(attr.name.toLowerCase().startsWith('on')||['href','xlink:href','class'].includes(attr.name)||(/(https?:|javascript:|data:|@import|expression\s*\()/i.test(attr.value)&&attr.name!=='xmlns')||/url\s*\(/i.test(attr.value.replace(/url\(#[\w-]+\)/g,'')))el.removeAttribute(attr.name);
 }
 clean(doc.documentElement);return new XMLSerializer().serializeToString(doc.documentElement);
}
export function interactionSvg(result:InteractionResult,pose:number,compare=-1,lang:Lang='zh'){
 const t=(value:string)=>translate(lang,value);
 const p=result.poses[pose];if(!p)throw Error('请选择有效姿势');
 const residues=[...new Set(p.rows.map(r=>r.residue))];
 const height=Math.max(440,Math.ceil(residues.length/2)*88+140),width=1040;
 const other=new Set((result.poses[compare]?.rows||[]).map(r=>r.residue+'|'+r.type));
 const ligand=cleanVector(p.ligandSvg).replace(/<svg\b/,`<svg x="310" y="${height/2-150}"`);
 const cards=residues.map((res,i)=>{
  const x=i%2?770:20,y=85+Math.floor(i/2)*88;
  const types=[...new Set(p.rows.filter(r=>r.residue===res).map(r=>r.type))];
  const unique=compare>=0&&types.some(t=>!other.has(res+'|'+t));
  return `<path d="M ${i%2?770:270} ${y+30} L ${i%2?730:310} ${height/2}" stroke="${unique?'#e08b35':'#94a3b8'}" stroke-width="1.5" fill="none"/><rect x="${x}" y="${y}" width="250" height="65" rx="9" fill="${unique?'#fff3df':'#edf6f0'}"/><text x="${x+12}" y="${y+22}" font-size="16">${esc(res)}</text><text x="${x+12}" y="${y+43}" font-size="11">${esc(types.join(', '))}</text>`;
 }).join('');
 return {width,height,svg:`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="Arial,Microsoft YaHei,sans-serif"><rect width="${width}" height="${height}" fill="white"/><text x="20" y="30" font-size="22">${esc(t(p.name))} · ${esc(t('残基相互作用概览'))}</text><text x="20" y="55" font-size="13">${esc(t('连线表示残基级相互作用，不表示二维原子键位'))}${compare>=0?esc(t('；橙色表示相较参考姿势新增的类型')):''}</text>${cards}${ligand}<text x="20" y="${height-22}" font-size="12">${esc(result.engine+' '+result.engineVersion)} · ${esc(t('基于输入结构的几何识别'))}</text></svg>`};
}
