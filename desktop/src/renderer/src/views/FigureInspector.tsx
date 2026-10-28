import {useLayoutEffect,useState} from 'react';
import {useT} from '../i18n';
import type {FigureDocument,FigureItem} from './figure-model';
import type {VectorObject,VectorPatch} from './figure-vector';
type Props={doc:FigureDocument;current?:FigureItem;inside:string|null;nodes:string[];objects:VectorObject[];fonts:string[];busy:boolean;onDocument:(doc:FigureDocument)=>void;onStyle:(patch:Partial<FigureItem>)=>void;onVector:(patch:VectorPatch,ids?:string[])=>void;onVectorBatch?:(changes:Array<{ids:string[];patch:VectorPatch}>)=>void;onItem:(patch:Partial<FigureItem>)=>void;onNodes:(ids:string[])=>void};
function vectorTextScale(panelId:string,id:string):number|null{
 const panel=document.querySelector(`.figure-artwork [data-figure-id="${CSS.escape(panelId)}"]`),el=panel?.querySelector(`[data-vector-id="${CSS.escape(id)}"]`) as SVGGraphicsElement|null,root=panel?.closest('.figure-artwork')?.querySelector('svg'),m=el?.getScreenCTM(),base=root?.getScreenCTM();
 if(!m||!base)return null;
 const relative=base.inverse().multiply(m),scale=Math.hypot(relative.c,relative.d);
 return Number.isFinite(scale)&&scale>0?scale:null;
}
function ColorField({label,value,onChange}:{label:string;value:string;onChange:(value:string)=>void}){
 const[draft,setDraft]=useState(value);useLayoutEffect(()=>setDraft(value),[value]);
 return <label>{label}<div className="figure-paint-field"><input aria-label={label} value={draft} onChange={e=>setDraft(e.target.value)} onBlur={()=>onChange(draft)} onKeyDown={e=>{if(e.key==='Enter')onChange(draft);}} placeholder="#2563eb / rgba(0,0,0,0.5) / none"/><input type="color" aria-label={label+' HEX'} value={/^#[\da-f]{6}$/i.test(value)?value:'#000000'} onChange={e=>onChange(e.target.value)}/></div></label>;
}
export default function FigureInspector(p:Props){
 const{t}=useT(),[scope,setScope]=useState<'selection'|'text'>('selection'),[textScale,setTextScale]=useState(1),[sizeError,setSizeError]=useState('');
 const current=p.current,node=p.objects.find(n=>n.id===p.nodes.at(-1)),unit=p.doc.unit||'px';
 const objectsById=new Map(p.objects.map(object=>[object.id,object])),selected=new Set(p.nodes),containerBlocked=p.busy||!!current?.locked||!!current?.hidden;
 const protectedAncestors=new Set<string>();for(const object of p.objects)if(object.locked||object.hidden){let parent=object.parentId?objectsById.get(object.parentId):undefined;while(parent){protectedAncestors.add(parent.id);parent=parent.parentId?objectsById.get(parent.parentId):undefined;}}
 const unitScale=unit==='mm'?p.doc.widthMm/p.doc.width:unit==='in'?p.doc.widthMm/p.doc.width/25.4:1;
 useLayoutEffect(()=>{setTextScale(p.inside&&node?vectorTextScale(p.inside,node.id)||1:1);setSizeError('');},[p.doc,p.inside,node?.id]);
 const value=(key:string,fallback:string|number):string|number=>p.inside?(node as any)?.[key]??fallback:(current as any)?.[key]??fallback;
 const text=p.inside?['text','tspan','textPath','g'].includes(node?.tag||''):current?.kind==='text';
 function ancestorBlocked(object:VectorObject){let parent=object.parentId?objectsById.get(object.parentId):undefined;while(parent){if(parent.locked||parent.hidden)return true;parent=parent.parentId?objectsById.get(parent.parentId):undefined;}return false;}
 function targets(key:string){return p.objects.filter(object=>{
  if(object.locked||object.hidden||ancestorBlocked(object))return false;
  if(scope!=='text'&&!/^(font|textAnchor|letterSpacing|lineHeight)/.test(key))return selected.has(object.id);
  if(!['text','tspan','textPath'].includes(object.tag)||protectedAncestors.has(object.id))return false;
  if(scope==='text')return true;
  let entry:VectorObject|undefined=object;while(entry){if(selected.has(entry.id))return true;entry=entry.parentId?objectsById.get(entry.parentId):undefined;}return false;
 }).map(object=>object.id);}
 function patch(key:string,v:unknown){if(containerBlocked||node?.locked||node?.hidden)return;if(p.inside){const ids=targets(key);if(ids.length)p.onVector({[key]:v},ids);}else p.onStyle({[key]:v});}
 function printFontSize(points:number){
  if(containerBlocked||node?.locked||node?.hidden||!Number.isFinite(points)||points<=0)return;
  setSizeError('');const canvasSize=points*25.4/72*p.doc.width/p.doc.widthMm;
  if(!p.inside){p.onStyle({fontSize:canvasSize});return;}
  try{
   const changes=targets('fontSize').map(id=>{const scale=vectorTextScale(p.inside!,id);if(!scale)throw Error('无法读取文字缩放，请显示图层后重试。');const fontSize=canvasSize/scale;if(!Number.isFinite(fontSize)||fontSize<.1||fontSize>10000)throw Error('SVG 属性数值超出范围');return{ids:[id],patch:{fontSize}};});
   if(!changes.length)return;
   if(p.onVectorBatch)p.onVectorBatch(changes);
   else if(changes.every(change=>Math.abs(change.patch.fontSize-changes[0].patch.fontSize)<1e-9))p.onVector(changes[0].patch,changes.flatMap(change=>change.ids));
   else throw Error('批量字号更新暂不可用，请重新打开编辑器。');
  }catch(error){setSizeError(error instanceof Error?error.message:String(error));}
 }
 function preset(v:string){const presets:Record<string,[number,number]>={a4:[210,297],a3:[297,420],single:[85,65],double:[180,120],square:[180,180]};if(presets[v]){const[w,h]=presets[v];p.onDocument({...p.doc,width:w*96/25.4,height:h*96/25.4,widthMm:w,unit:'mm'});}}
 return <>
 <details open={!current} className="figure-paper"><summary>{t('论文版式')}</summary><fieldset disabled={p.busy}>
  <label>{t('版式预设')}<select aria-label={t('论文版式')} defaultValue="" onChange={e=>{preset(e.target.value);e.target.value='';}}><option value="">{t('自定义')}</option><option value="a4">A4 · 210 × 297 mm</option><option value="a3">A3 · 297 × 420 mm</option><option value="single">{t('期刊单栏')} · 85 mm</option><option value="double">{t('期刊双栏')} · 180 mm</option><option value="square">{t('正方形')} · 180 mm</option></select></label>
  <label>{t('尺寸单位')}<select aria-label={t('尺寸单位')} value={unit} onChange={e=>p.onDocument({...p.doc,unit:e.target.value as 'mm'|'in'|'px'})}><option value="mm">mm</option><option value="in">in</option><option value="px">px</option></select></label>
  <div className="figure-fields">{(['width','height'] as const).map((key,i)=><label key={key}>{t(i?'页面高度':'页面宽度')} ({unit})<input aria-label={t(i?'页面高度':'页面宽度')} type="number" min="0.1" step="any" value={Number((p.doc[key]*unitScale).toFixed(2))} onChange={e=>{const n=Number(e.target.value)/unitScale;if(n>0&&n<=16000)p.onDocument({...p.doc,[key]:n,...(key==='width'&&unit!=='px'?{widthMm:unit==='mm'?Number(e.target.value):Number(e.target.value)*25.4}:{})});}}/></label>)}</div>
  <label className="figure-checkbox"><input type="checkbox" aria-label={t('透明背景')} checked={p.doc.background==='transparent'} onChange={e=>p.onDocument({...p.doc,background:e.target.checked?'transparent':'#ffffff'})}/>{t('透明背景')}</label>
  {p.doc.background!=='transparent'&&<label>{t('画布背景')}<input type="color" value={p.doc.background||'#ffffff'} onChange={e=>p.onDocument({...p.doc,background:e.target.value})}/></label>}
 </fieldset></details>
 {current&&(!p.inside||node||scope==='text')&&<details open className="figure-format"><summary>{t('格式')}</summary><fieldset disabled={containerBlocked||!!node?.locked||!!node?.hidden}>
  {p.inside&&<label>{t('修改范围')}<select aria-label={t('修改范围')} value={scope} onChange={e=>setScope(e.target.value as 'selection'|'text')}><option value="selection">{t('所选对象')}</option><option value="text">{t('当前图全部文字')}</option></select></label>}
  {(text||p.inside&&scope==='text')&&<>
   <label>{t('字体')}<select aria-label={t('字体')} value={String(value('fontFamily','Arial'))} onChange={e=>patch('fontFamily',e.target.value)}>{!p.fonts.includes(String(value('fontFamily','Arial')))&&<option value={String(value('fontFamily','Arial'))}>{String(value('fontFamily','Arial'))} · {t('尚未确认可用')}</option>}{p.fonts.map(f=><option key={f} value={f}>{f}</option>)}</select></label>
   <div className="figure-fields"><label>{t('印刷字号（pt）')}<input aria-label={t('印刷字号（pt）')} type="number" min="1" step="0.5" value={Number((Number(value('fontSize',24))*textScale*p.doc.widthMm/p.doc.width/25.4*72).toFixed(2))} onChange={e=>printFontSize(Number(e.target.value))}/></label><label>{t('字间距')}<input aria-label={t('字间距')} type="number" step="0.1" value={Number(value('letterSpacing',0))} onChange={e=>patch('letterSpacing',Number(e.target.value))}/></label></div>
   {sizeError&&<p role="alert">{t(sizeError)}</p>}
   {!p.inside&&<label>{t('行距倍数')}<input type="number" min="0.5" max="5" step="0.1" value={Number(value('lineHeight',1.2))} onChange={e=>patch('lineHeight',Number(e.target.value))}/></label>}
   <div className="figure-actions"><button className="btn" aria-pressed={['700','bold'].includes(String(value('fontWeight','400')))} onClick={()=>patch('fontWeight',['700','bold'].includes(String(value('fontWeight','400')))?'400':'700')}>{t('粗体')}</button><button className="btn" aria-pressed={value('fontStyle','normal')==='italic'} onClick={()=>patch('fontStyle',value('fontStyle','normal')==='italic'?'normal':'italic')}>{t('斜体')}</button></div>
   <label>{t('文字对齐')}<select aria-label={t('文字对齐')} value={String(value('textAnchor','start'))} onChange={e=>patch('textAnchor',e.target.value)}><option value="start">{t('左对齐')}</option><option value="middle">{t('居中')}</option><option value="end">{t('右对齐')}</option></select></label>
  </>}
  <ColorField label={t('填充 / 文字颜色')} value={String(value(p.inside?'fill':current.kind==='text'?'color':'fill','none'))} onChange={v=>patch(p.inside?'fill':current.kind==='text'?'color':'fill',v)}/>
  <ColorField label={t('描边颜色')} value={String(value('stroke','none'))} onChange={v=>patch('stroke',v)}/>
  <div className="figure-fields"><label>{t('线条粗细')}<input aria-label={t('线条粗细')} type="number" min="0" step="0.1" value={Number(value('strokeWidth',1.5))} onChange={e=>{const n=Number(e.target.value);if(n>=0)patch('strokeWidth',n);}}/></label><label>{t('透明度')}<input aria-label={t('透明度')} type="number" min="0" max="1" step="0.05" value={Number(value('opacity',1))} onChange={e=>{const n=Number(e.target.value);if(n>=0&&n<=1)patch('opacity',n);}}/></label></div>
  {!p.inside&&current.kind==='image'&&<><label>{t('图片适配')}<select aria-label={t('图片适配')} value={current.preserveAspectRatio||'xMidYMid meet'} onChange={e=>p.onItem({preserveAspectRatio:e.target.value as FigureItem['preserveAspectRatio']})}><option value="xMidYMid meet">{t('完整显示')}</option><option value="xMidYMid slice">{t('填满边框')}</option><option value="none">{t('拉伸')}</option></select></label><p className="figure-hint">{t('裁剪范围使用原图百分比，原始图片保持不变。')}</p><div className="figure-fields">{(['x','y','width','height'] as const).map((key,i)=><label key={key}>{t(['裁剪左边','裁剪顶部','裁剪宽度','裁剪高度'][i])}<input aria-label={t(['裁剪左边','裁剪顶部','裁剪宽度','裁剪高度'][i])} type="number" min={i<2?0:1} max="100" value={current.crop?.[key]??(i<2?0:100)} onChange={e=>{const crop={x:0,y:0,width:100,height:100,...current.crop,[key]:Number(e.target.value)};crop.x=Math.max(0,Math.min(99,crop.x));crop.y=Math.max(0,Math.min(99,crop.y));crop.width=Math.max(1,Math.min(100-crop.x,crop.width));crop.height=Math.max(1,Math.min(100-crop.y,crop.height));p.onItem({crop});}}/></label>)}</div><button className="btn" onClick={()=>p.onItem({crop:undefined})}>{t('重置裁剪')}</button></>}
 </fieldset></details>}
 {p.inside&&<details open><summary>{t('图内对象')} · {p.objects.length}</summary><div className="figure-object-tree">{p.objects.map(object=>{const protectedParent=containerBlocked||ancestorBlocked(object);return <div className={p.nodes.includes(object.id)?'is-selected':''} key={object.id} style={{paddingLeft:Math.min(object.depth,8)*10}}><button className="figure-object-name" disabled={protectedParent||object.hidden} onClick={e=>p.onNodes(e.shiftKey?(p.nodes.includes(object.id)?p.nodes.filter(id=>id!==object.id):[...p.nodes,object.id]):[object.id])} title={object.name}>{object.tag==='g'?'▱':object.tag==='text'?'T':'◇'} {object.name}</button><button disabled={protectedParent||object.locked} aria-label={t(object.hidden?'显示对象':'隐藏对象')} onClick={()=>p.onVector({hidden:!object.hidden},[object.id])}>{object.hidden?'○':'●'}</button><button disabled={protectedParent||object.hidden} aria-label={t(object.locked?'解锁对象':'锁定对象')} onClick={()=>p.onVector({locked:!object.locked},[object.id])}>{object.locked?'🔒':'♧'}</button></div>;})}</div></details>}
 </>;
}
