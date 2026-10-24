import assetData from './drawing-assets.json';
// Original upstream names are retained separately from the Chinese catalog labels.
export const assets=assetData.map(a=>({...a,name_en:decodeURIComponent(a.source.split('/').at(-1)||a.id).replace(/\.svg$/i,'').replace(/[_-]+/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2')}));
export type DiagramNode={id:string;text:string;x:number;y:number;color:string;shape:'rectangle'|'ellipse'|'diamond';width?:number;height?:number;rotation?:number;asset?:string;group?:string};
export type Diagram={title:string;nodes:DiagramNode[];edges:{from:string;to:string;label:string}[]};
export const blank:Diagram={title:'未命名绘图',nodes:[],edges:[]};
const bounded=(v:unknown,fallback:number,min:number,max:number)=>typeof v==='number'&&Number.isFinite(v)?Math.max(min,Math.min(max,v)):fallback;
export function parseDiagram(raw:unknown):Diagram{
 let value:any=raw;if(typeof raw==='string')value=JSON.parse(raw.trim().replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,''));
 if(!value||!Array.isArray(value.nodes)||!Array.isArray(value.edges)||value.nodes.length>40||value.edges.length>80)throw Error('图结构无效，最多支持40个节点和80条连线');
 const ids=new Set<string>();
 const nodes:DiagramNode[]=value.nodes.map((n:any)=>{
  if(!n||typeof n.id!=='string'||!n.id||n.id.length>80||ids.has(n.id))throw Error('节点标识重复或无效');ids.add(n.id);
  if(!Number.isFinite(n.x)||!Number.isFinite(n.y))throw Error('节点位置无效');
  if(n.asset&&!assets.some(a=>a.id===n.asset))throw Error('素材不在当前素材库中，请重新选择');
  return {id:n.id,text:String(n.text||'').slice(0,80),x:bounded(n.x,0,0,4000),y:bounded(n.y,0,0,4000),color:/^#[a-f0-9]{6}$/i.test(n.color)?n.color:'#e5f3ee',shape:['rectangle','ellipse','diamond'].includes(n.shape)?n.shape:'rectangle',width:bounded(n.width,240,40,1000),height:bounded(n.height,152,40,1000),rotation:bounded(n.rotation,0,-180,180),asset:n.asset||undefined,group:typeof n.group==='string'?n.group.slice(0,80):undefined};
 });
 const edges=value.edges.map((e:any)=>{if(!e||!ids.has(e.from)||!ids.has(e.to))throw Error('连线指向不存在的节点');return {from:e.from,to:e.to,label:String(e.label||'').slice(0,40)};});
 return {title:String(value.title||'科研绘图').slice(0,100),nodes,edges};
}
export const esc=(s:string)=>s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g,'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]!));
export function dimensions(d:Diagram){
 const boxes=d.nodes.map(n=>{const w=n.width||240,h=n.height||152,r=(n.rotation||0)*Math.PI/180;
  const rx=(Math.abs(w*Math.cos(r))+Math.abs((h+60)*Math.sin(r)))/2,ry=(Math.abs(w*Math.sin(r))+Math.abs((h+60)*Math.cos(r)))/2;
  return {left:n.x+w/2-rx,right:n.x+w/2+rx,top:n.y+60+h/2-ry,bottom:n.y+60+h/2+ry};});
 const left=Math.min(-30,...boxes.map(b=>b.left-30)),top=Math.min(0,...boxes.map(b=>b.top-60));
 return {left,top,width:Math.max(800,...boxes.map(b=>b.right-left+30)),height:Math.max(500,...boxes.map(b=>b.bottom-top+50))};
}
export function moveNodes(d:Diagram,selected:string[],dx:number,dy:number):Diagram{
 const moving=d.nodes.filter(n=>selected.includes(n.id));if(!moving.length)return d;
 dx=Math.max(-Math.min(...moving.map(n=>n.x)),Math.min(4000-Math.max(...moving.map(n=>n.x)),dx));
 dy=Math.max(-Math.min(...moving.map(n=>n.y)),Math.min(4000-Math.max(...moving.map(n=>n.y)),dy));
 return {...d,nodes:d.nodes.map(n=>selected.includes(n.id)?{...n,x:n.x+dx,y:n.y+dy}:n)};
}
export function assetSvg(id:string,prefix:string,width:number,height:number){
 const a=assets.find(a=>a.id===id);if(!a)return '';
 const ids=[...a.svg.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);let svg=a.svg;
 for(const old of ids){const next=prefix+'-'+old;svg=svg.split('id="'+old+'"').join('id="'+next+'"').split('url(#'+old+')').join('url(#'+next+')');}
 return svg.replace('<svg ',`<svg width="${width}" height="${height}" `);
}
function lines(text:string){const rows:string[]=[];let row='',units=0;for(const c of text){const u=c.charCodeAt(0)>255?2:1;if(c==='\n'||units+u>26){rows.push(row);row='';units=0;if(c==='\n')continue;}row+=c;units+=u;}rows.push(row);return rows;}
export function diagramSvg(d:Diagram,selected:string|string[]=''){
 const selectedIds=Array.isArray(selected)?selected:[selected];const {width,height,left,top}=dimensions(d);const map=new Map(d.nodes.map(n=>[n.id,n]));
 const edges=d.edges.map(e=>{const a=map.get(e.from)!,b=map.get(e.to)!;const forward=b.x>=a.x;const x1=a.x+(forward?(a.width||240):0),x2=b.x+(forward?0:(b.width||240)),y1=a.y+60+(a.height||152)/2,y2=b.y+60+(b.height||152)/2;return `<path d="M ${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}" fill="none" stroke="#64748b" stroke-width="2" marker-end="url(#arrow)"/><text x="${(x1+x2)/2}" y="${(y1+y2)/2-8}" text-anchor="middle" font-size="13" fill="#475569">${esc(e.label)}</text>`;}).join('');
 const nodes=d.nodes.map((n,index)=>{const w=n.width||240,h=n.height||152,chosen=selectedIds.includes(n.id);const shape=n.asset?'<g fill="#000" stroke="none">'+assetSvg(n.asset,'asset'+index,w,h)+'</g>':n.shape==='ellipse'?`<ellipse cx="${w/2}" cy="${h/2}" rx="${w/2}" ry="${h/2}"/>`:n.shape==='diamond'?`<path d="M ${w/2} 0 L ${w} ${h/2} L ${w/2} ${h} L 0 ${h/2} Z"/>`:`<rect width="${w}" height="${h}" rx="14"/>`;const rows=lines(n.text);return `<g data-node="${esc(n.id)}" transform="translate(${n.x} ${n.y+60}) rotate(${n.rotation||0} ${w/2} ${h/2})" fill="${esc(n.color)}" stroke="#aac1b7" stroke-width="1.5" style="cursor:move"><rect width="${w}" height="${h}" fill="transparent" stroke="none"/>${shape}${chosen?`<rect width="${w}" height="${h}" fill="none" stroke="#087f5b" stroke-width="3" stroke-dasharray="6 3"/>`:''}<text fill="#24352e" stroke="none" text-anchor="middle" font-size="16">${rows.map((r,i)=>`<tspan x="${w/2}" y="${(n.asset?h+20:h/2-(rows.length-1)*10)+i*20}">${esc(r)}</tspan>`).join('')}</text></g>`;}).join('');
 const credits=[...new Set(d.nodes.map(n=>n.asset).filter(Boolean))].map(id=>{const a=assets.find(a=>a.id===id)!;return `${a.name}: ${a.author}, ${a.license}, ${a.source}`;}).join('\n');
 return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${left} ${top} ${width} ${height}" width="${width}" height="${height}" font-family="Microsoft YaHei,Arial,sans-serif"><metadata>${esc(credits)}</metadata><rect x="${left}" y="${top}" width="${width}" height="${height}" fill="white"/><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs><text x="${left+width/2}" y="${top+32}" text-anchor="middle" font-size="24" fill="#24352e">${esc(d.title)}</text>${edges}${nodes}</svg>`;
}
export async function svgPng(svg:string,width:number,height:number){const url=URL.createObjectURL(new Blob([svg],{type:'image/svg+xml'}));try{const image=new Image();image.src=url;await image.decode();const scale=Math.min(2,4096/Math.max(width,height));const canvas=document.createElement('canvas');canvas.width=Math.ceil(width*scale);canvas.height=Math.ceil(height*scale);const ctx=canvas.getContext('2d');if(!ctx)throw Error('无法导出图片');ctx.drawImage(image,0,0,canvas.width,canvas.height);return canvas.toDataURL('image/png');}finally{URL.revokeObjectURL(url);}}
export async function diagramPng(d:Diagram){const {width,height}=dimensions(d);return svgPng(diagramSvg(d),width,height);}
