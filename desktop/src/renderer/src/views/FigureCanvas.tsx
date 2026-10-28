import {forwardRef,useImperativeHandle,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {figureSvg,moveItems,type FigureDocument,type FigureItem} from './figure-model';
import {editVector,vectorObjects,type VectorObject} from './figure-vector';
import {useT} from '../i18n';

type Box={x:number;y:number;width:number;height:number};
type Basis={id:string;parent:DOMMatrix;own:DOMMatrix};
export type FigureCanvasHandle={nudge:(dx:number,dy:number)=>void;align:(mode:string)=>void;viewport:()=>{width:number;height:number}};
type Props={doc:FigureDocument;selected:string[];inside:string|null;nodes:string[];zoom:number;busy:boolean;tool:'select'|'pan';onSelect:(ids:string[])=>void;onNodes:(ids:string[])=>void;onEnter:(id:string|null)=>void;onChange:(doc:FigureDocument)=>void;onCommit:(doc:FigureDocument)=>void;onEnd:(original:FigureDocument)=>void;onText:(isVector?:boolean)=>void;onError:(error:unknown)=>void;onFiles:(files:File[])=>void;onKey:(event:React.KeyboardEvent<HTMLDivElement>)=>void};
const query=(id:string,attr:string)=>`[${attr}="${CSS.escape(id)}"]`;
function elementBox(root:SVGSVGElement,el:SVGGraphicsElement):Box|null{
 try{const b=el.getBBox(),r=root.getScreenCTM(),m=el.getScreenCTM();if(!r||!m)return null;const matrix=r.inverse().multiply(m),p=[[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,b.y+b.height],[b.x,b.y+b.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(matrix));const x=Math.min(...p.map(p=>p.x)),y=Math.min(...p.map(p=>p.y));return{x,y,width:Math.max(...p.map(p=>p.x))-x,height:Math.max(...p.map(p=>p.y))-y};}catch{return null;}
}
function union(boxes:Box[]):Box|null{if(!boxes.length)return null;const x=Math.min(...boxes.map(b=>b.x)),y=Math.min(...boxes.map(b=>b.y));return{x,y,width:Math.max(...boxes.map(b=>b.x+b.width))-x,height:Math.max(...boxes.map(b=>b.y+b.height))-y};}
function matrixString(m:DOMMatrix){const values=[m.a,m.b,m.c,m.d,m.e,m.f];if(!values.every(Number.isFinite))throw Error('图形变换无效');return `matrix(${values.map(n=>Number(n.toFixed(9))).join(' ')})`;}
function checkedItem(item:FigureItem):FigureItem{
 const inRange=(value:number,minimum:number,maximum:number)=>Number.isFinite(value)&&value>=minimum&&value<=maximum;
 if(!inRange(item.x,-100000,100000)||!inRange(item.y,-100000,100000)||!inRange(item.width,.01,100000)||!inRange(item.height,.01,100000)||!inRange(item.rotation,-36000,36000)||
   item.kind==='text'&&!inRange(item.fontSize??24,1,2000)||item.letterSpacing!==undefined&&!inRange(item.letterSpacing,-1000,1000))throw Error('缩放或旋转超出可保存范围，已恢复修改前的图形。');
 return item;
}
function ancestors(id:string,objects:VectorObject[]):string[]{const result:string[]=[];let object=objects.find(o=>o.id===id);while(object?.parentId){result.push(object.parentId);object=objects.find(o=>o.id===object!.parentId);}return result;}
function topNodes(ids:string[],objects:VectorObject[]):string[]{const valid=new Set(objects.map(o=>o.id));return [...new Set(ids)].filter(id=>valid.has(id)&&!ancestors(id,objects).some(parent=>ids.includes(parent)));}
function bases(root:SVGSVGElement,item:string,ids:string[]):Basis[]{
 const panel=root.querySelector(query(item,'data-figure-id')),screen=root.getScreenCTM();if(!panel||!screen)return[];
 const elements=ids.map(id=>panel.querySelector(query(id,'data-vector-id')) as SVGGraphicsElement|null).filter((el):el is SVGGraphicsElement=>!!el);
 return elements.filter(el=>!elements.some(other=>other!==el&&other.contains(el))).flatMap(el=>{const p=(el.parentElement as unknown as SVGGraphicsElement)?.getScreenCTM?.();if(!p)return[];return[{id:el.getAttribute('data-vector-id')!,parent:DOMMatrix.fromMatrix(screen).inverse().multiply(DOMMatrix.fromMatrix(p)),own:el.transform?.baseVal.consolidate()?.matrix?DOMMatrix.fromMatrix(el.transform.baseVal.consolidate()!.matrix):new DOMMatrix()}];});
}
function vectorTransform(doc:FigureDocument,itemId:string,basis:Basis[],world:DOMMatrix):FigureDocument{
 return{...doc,items:doc.items.map(item=>{if(item.id!==itemId||!item.svg||item.locked||item.hidden)return item;let svg=item.svg;for(const b of basis)svg=editVector(svg,[b.id],{transform:matrixString(b.parent.inverse().multiply(world).multiply(b.parent).multiply(b.own))});return{...item,svg};})};
}
export default forwardRef<FigureCanvasHandle,Props>(function FigureCanvas(p,ref){
 const{t}=useT(),stage=useRef<HTMLDivElement>(null),art=useRef<HTMLDivElement>(null),[box,setBox]=useState<Box|null>(null),[marquee,setMarquee]=useState<Box|null>(null);
 const gesture=useRef<{kind:'move'|'scale'|'rotate'|'marquee'|'pan';start:DOMPoint;doc:FigureDocument;ids:string[];basis:Basis[];box:Box|null;handle?:string;shift:boolean;moved:boolean;marquee?:Box;scroll?:{x:number;y:number};screen?:{x:number;y:number}}|null>(null);
 const svg=useMemo(()=>figureSvg(p.doc),[p.doc]);
 const panel=p.doc.items.find(item=>item.id===p.inside),objects=useMemo(()=>panel?.svg?vectorObjects(panel.svg):[],[panel?.svg]);
 const nodeIds=topNodes(p.nodes,objects);
 const members=(id:string)=>{const item=p.doc.items.find(value=>value.id===id);return item?.groupId?p.doc.items.filter(value=>value.groupId===item.groupId).map(value=>value.id):[id];};
 const unlockedItems=(ids:string[])=>[...new Set(ids)].filter(id=>members(id).every(member=>p.doc.items.some(item=>item.id===member&&!item.locked&&!item.hidden)));
 const root=()=>art.current?.querySelector('svg')||null;
 const point=(e:{clientX:number;clientY:number})=>{const m=root()?.getScreenCTM();return m?new DOMPoint(e.clientX,e.clientY).matrixTransform(m.inverse()):null;};
 const elements=()=>{const r=root();if(!r)return[];const scope=p.inside?r.querySelector(query(p.inside,'data-figure-id')):r;return (p.inside?nodeIds:p.selected).map(id=>scope?.querySelector(query(id,p.inside?'data-vector-id':'data-figure-id')) as SVGGraphicsElement|null).filter((e):e is SVGGraphicsElement=>!!e);};
 useLayoutEffect(()=>{const r=root();setBox(r?union(elements().map(e=>elementBox(r,e)).filter((b):b is Box=>!!b)):null);if(p.inside&&nodeIds.join('|')!==p.nodes.join('|'))p.onNodes(nodeIds);},[p.doc,p.selected.join('|'),p.nodes.join('|'),p.inside]);
 const unlockedNodes=(ids:string[])=>{if(!panel||!unlockedItems([panel.id]).length)return[];return topNodes(ids,objects).filter(id=>{const object=objects.find(o=>o.id===id);return object&&!object.locked&&!object.hidden&&!objects.some(child=>child.locked&&ancestors(child.id,objects).includes(id));});};
 const editableIds=p.inside?unlockedNodes(nodeIds):unlockedItems(p.selected),showHandles=!p.busy&&editableIds.length>0&&editableIds.length===(p.inside?nodeIds:p.selected).length;
 const move=(dx:number,dy:number)=>{if(p.busy||!editableIds.length||!Number.isFinite(dx)||!Number.isFinite(dy)||(!dx&&!dy))return;try{const r=root();if(p.inside&&r){const basis=bases(r,p.inside,editableIds);if(basis.length)p.onCommit(vectorTransform(p.doc,p.inside,basis,new DOMMatrix().translate(dx,dy)));}else if(!p.inside)p.onCommit(moveItems(p.doc,editableIds,dx,dy));}catch(error){p.onError(error);}};
 useImperativeHandle(ref,()=>({nudge:move,viewport:()=>{const el=stage.current;if(!el)return{width:800,height:600};const style=getComputedStyle(el);return{width:el.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),height:el.clientHeight-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom)};},align:mode=>{
  if(p.busy||!['left','center','right','top','middle','bottom','horizontal','vertical'].includes(mode))return;try{const r=root();if(!r||!p.inside)return;const list=elements().filter(el=>unlockedNodes([el.getAttribute('data-vector-id')!]).length).map(el=>({id:el.getAttribute('data-vector-id')!,box:elementBox(r,el)})).filter((e):e is {id:string;box:Box}=>!!e.box),all=union(list.map(e=>e.box));if(!all||list.length<2)return;
  let next=p.doc,cursor=mode==='horizontal'?all.x:all.y;const horizontal=mode==='horizontal';if(mode==='horizontal'||mode==='vertical')list.sort((a,b)=>horizontal?a.box.x-b.box.x:a.box.y-b.box.y);const gap=((horizontal?all.width:all.height)-list.reduce((n,e)=>n+(horizontal?e.box.width:e.box.height),0))/(list.length-1);
  for(const e of list){let dx=mode==='left'?all.x-e.box.x:mode==='center'?all.x+all.width/2-e.box.x-e.box.width/2:mode==='right'?all.x+all.width-e.box.x-e.box.width:0,dy=mode==='top'?all.y-e.box.y:mode==='middle'?all.y+all.height/2-e.box.y-e.box.height/2:mode==='bottom'?all.y+all.height-e.box.y-e.box.height:0;if(mode==='horizontal')dx=cursor-e.box.x;if(mode==='vertical')dy=cursor-e.box.y;if(Math.abs(dx)+Math.abs(dy)>1e-8)next=vectorTransform(next,p.inside,bases(r,p.inside,[e.id]),new DOMMatrix().translate(dx,dy));cursor+=(horizontal?e.box.width:e.box.height)+gap;}if(next!==p.doc)p.onCommit(next);}catch(error){p.onError(error);}
 }}));
 function begin(e:React.PointerEvent,kind:'scale'|'rotate',handle?:string){
  if(!showHandles||!box||e.button!==0)return;e.stopPropagation();e.preventDefault();stage.current?.focus();const start=point(e),r=root();if(!start||!r)return;const ids=editableIds;
  gesture.current={kind,start,doc:p.doc,ids,basis:p.inside?bases(r,p.inside,ids):[],box,handle,shift:e.shiftKey,moved:false};stage.current?.setPointerCapture(e.pointerId);
 }
 function down(e:React.PointerEvent<HTMLDivElement>){
  if(p.busy||e.button!==0)return;const r=root(),start=point(e);if(!r||!start)return;stage.current?.focus();e.preventDefault();
  if(p.tool==='pan'||e.ctrlKey&&e.altKey){gesture.current={kind:'pan',start,doc:p.doc,ids:[],basis:[],box:null,shift:false,moved:false,scroll:{x:stage.current!.scrollLeft,y:stage.current!.scrollTop},screen:{x:e.clientX,y:e.clientY}};e.currentTarget.setPointerCapture(e.pointerId);return;}
  let target=e.target as Element;
  if(e.altKey){const hit=document.elementsFromPoint(e.clientX,e.clientY).filter(el=>art.current?.contains(el)).map(el=>el.closest(p.inside?'[data-vector-id]':'[data-figure-id]')).filter((v,i,a):v is Element=>!!v&&a.indexOf(v)===i&&(!p.inside||v.closest('[data-figure-id]')?.getAttribute('data-figure-id')===p.inside));const selected=p.inside?nodeIds:p.selected;const index=hit.findIndex(el=>selected.includes(el.getAttribute(p.inside?'data-vector-id':'data-figure-id')||''));target=hit[(index+1)%hit.length]||target;}
  const panel=target.closest('[data-figure-id]'),panelId=panel?.getAttribute('data-figure-id'),item=p.doc.items.find(i=>i.id===panelId);
  let ids:string[]=[],hit=false;
  if(p.inside){
   const id=panelId===p.inside?target.closest('[data-vector-id]')?.getAttribute('data-vector-id'):null;
   if(id){hit=true;const next=topNodes(e.shiftKey?(nodeIds.includes(id)?nodeIds.filter(n=>n!==id):[...nodeIds,id]):nodeIds.includes(id)?nodeIds:[id],objects);p.onNodes(next);ids=unlockedNodes(next);}
  }else if(item){
   hit=true;const group=members(item.id);ids=e.shiftKey?(p.selected.includes(item.id)?p.selected.filter(id=>!group.includes(id)):[...new Set([...p.selected,...group])]):p.selected.includes(item.id)?p.selected:group;p.onSelect(ids);ids=unlockedItems(ids);
  }
  if(!hit){if(!e.shiftKey){p.inside?p.onNodes([]):p.onSelect([]);}gesture.current={kind:'marquee',start,doc:p.doc,ids:e.shiftKey?(p.inside?nodeIds:p.selected):[],basis:[],box:null,shift:e.shiftKey,moved:false};}
  else if(ids.length&&!e.shiftKey)gesture.current={kind:'move',start,doc:p.doc,ids,basis:p.inside?bases(r,p.inside,ids):[],box,shift:false,moved:false};
  // Capture starts after movement so stationary double-clicks retain their SVG target.
 }
 function motion(e:React.PointerEvent<HTMLDivElement>){
  const g=gesture.current;if(!g)return;if(p.busy){finish(true);return;}if(g.kind==='pan'){stage.current!.scrollLeft=g.scroll!.x-(e.clientX-g.screen!.x);stage.current!.scrollTop=g.scroll!.y-(e.clientY-g.screen!.y);return;}
  const q=point(e);if(!q)return;const dx=q.x-g.start.x,dy=q.y-g.start.y;if(!g.moved&&Math.abs(dx)+Math.abs(dy)<.2)return;g.moved=true;if(!e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.setPointerCapture(e.pointerId);
  if(g.kind==='marquee'){g.marquee={x:Math.min(q.x,g.start.x),y:Math.min(q.y,g.start.y),width:Math.abs(dx),height:Math.abs(dy)};setMarquee(g.marquee);return;}
  try{
   let world=new DOMMatrix().translate(dx,dy);
   if(g.kind==='rotate'&&g.box){const cx=g.box.x+g.box.width/2,cy=g.box.y+g.box.height/2;let angle=(Math.atan2(q.y-cy,q.x-cx)-Math.atan2(g.start.y-cy,g.start.x-cx))*180/Math.PI;if(e.shiftKey)angle=Math.round(angle/15)*15;world=new DOMMatrix().translate(cx,cy).rotate(angle).translate(-cx,-cy);}
   if(g.kind==='scale'&&g.box){const b=g.box,left=g.handle?.includes('w'),top=g.handle?.includes('n'),ax=left?b.x+b.width:b.x,ay=top?b.y+b.height:b.y;let sx=Math.max(.02,(q.x-ax)/(g.start.x-ax||1)),sy=Math.max(.02,(q.y-ay)/(g.start.y-ay||1));
    // Outer items cannot express shear; an oblique rotation must retain its aspect ratio.
    const proportional=!p.inside&&g.doc.items.some(item=>g.ids.includes(item.id)&&(item.kind==='text'||Math.abs(Math.sin(item.rotation*Math.PI/90))>1e-7));
     if(!e.shiftKey||g.ids.length>1||proportional){const denominator=(g.start.x-ax)**2+(g.start.y-ay)**2;let s=Math.max(.02,denominator?((q.x-ax)*(g.start.x-ax)+(q.y-ay)*(g.start.y-ay))/denominator:1);
      if(!p.inside){const text=g.doc.items.filter(item=>g.ids.includes(item.id)&&item.kind==='text');if(text.length){const minimum=Math.max(...text.map(item=>1/(item.fontSize??24))),maximum=Math.min(...text.map(item=>Math.min(2000/(item.fontSize??24),item.letterSpacing?1000/Math.abs(item.letterSpacing):Infinity)));if(minimum>maximum)throw Error('文字缩放超出可保存范围');s=Math.max(minimum,Math.min(maximum,s));}}
      sx=s;sy=s;}world=new DOMMatrix().translate(ax,ay).scale(sx,sy).translate(-ax,-ay);}
   if(p.inside)p.onChange(vectorTransform(g.doc,p.inside,g.basis,world));
   else if(g.kind==='move')p.onChange(moveItems(g.doc,g.ids,dx,dy));
    else{const sx=Math.hypot(world.a,world.b),sy=Math.hypot(world.c,world.d),angle=Math.atan2(world.b,world.a)*180/Math.PI;p.onChange({...g.doc,items:g.doc.items.map(item=>{if(!g.ids.includes(item.id)||item.locked||item.hidden)return item;const c=new DOMPoint(item.x+item.width/2,item.y+item.height/2).matrixTransform(world),quarterTurn=Math.abs(Math.sin(item.rotation*Math.PI/180))>1-1e-7,wx=g.kind==='scale'&&quarterTurn?sy:sx,hy=g.kind==='scale'&&quarterTurn?sx:sy;return checkedItem({...item,x:c.x-item.width*wx/2,y:c.y-item.height*hy/2,width:item.width*wx,height:item.height*hy,rotation:item.rotation+angle,...(g.kind==='scale'&&item.kind==='text'?{fontSize:(item.fontSize||24)*hy,letterSpacing:(item.letterSpacing||0)*wx}:{}),...(g.kind==='scale'&&Math.abs(sx-sy)>1e-8&&['svg','image'].includes(item.kind)?{preserveAspectRatio:'none' as const}:{})});})});}
  }catch(error){p.onError(error);gesture.current=null;p.onChange(g.doc);}
 }
 function finish(cancel=false){const g=gesture.current;gesture.current=null;if(!g)return;if(g.kind==='marquee'){
  const area=g.marquee;if(area&&!cancel){const r=root(),scope=p.inside?r?.querySelector(query(p.inside,'data-figure-id')):r;const matches=[...scope?.querySelectorAll(p.inside?'[data-vector-id]':'[data-figure-id]')||[]].filter(el=>{const id=el.getAttribute(p.inside?'data-vector-id':'data-figure-id')!;if(p.inside){const obj=objects.find(o=>o.id===id);if(!obj||['g','svg','a'].includes(obj.tag)||!unlockedNodes([id]).length)return false;}else if(!unlockedItems([id]).length)return false;const b=elementBox(r!,el as SVGGraphicsElement);return b&&b.x>=area.x&&b.y>=area.y&&b.x+b.width<=area.x+area.width&&b.y+b.height<=area.y+area.height;}).map(el=>el.getAttribute(p.inside?'data-vector-id':'data-figure-id')!);p.inside?p.onNodes(topNodes([...g.ids,...matches],objects)):p.onSelect([...new Set([...g.ids,...matches.flatMap(members)])]);}setMarquee(null);
 }else if(g.kind!=='pan'&&g.moved){if(cancel)p.onChange(g.doc);else p.onEnd(g.doc);}}
 return <div ref={stage} className={'figure-stage'+(p.tool==='pan'?' is-panning':'')} tabIndex={0} aria-label={t('拼图画布')} onKeyDown={p.onKey} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!p.busy)p.onFiles(Array.from(e.dataTransfer.files));}} onPointerDown={down} onPointerMove={motion} onPointerUp={()=>finish()} onPointerCancel={()=>finish(true)} onLostPointerCapture={()=>finish(true)}>
  {!p.doc.items.length&&<div className="figure-empty"><strong>{t('把图表放到这里，开始拼图')}</strong><p>{t('支持 SVG、PDF、PNG、JPG；双击矢量图进入图内编辑。')}</p></div>}
  <div className="figure-canvas" style={{width:p.doc.width*p.zoom,height:p.doc.height*p.zoom}} onDoubleClick={e=>{if(p.busy||p.tool==='pan')return;const el=e.target as Element,id=el.closest('[data-figure-id]')?.getAttribute('data-figure-id'),item=p.doc.items.find(i=>i.id===id);if(!item||!unlockedItems([item.id]).length)return;if(item.kind==='svg'){const list=item.svg?vectorObjects(item.svg):[],node=el.closest('text[data-vector-id]')||el.closest('[data-vector-id]'),nodeId=node?.getAttribute('data-vector-id'),object=list.find(o=>o.id===nodeId);if(object&&(object.locked||object.hidden||list.some(child=>child.locked&&ancestors(child.id,list).includes(object.id))))return;p.onSelect([item.id]);p.onEnter(item.id);p.onNodes(nodeId?[nodeId]:[]);if(object&&['text','tspan','textPath'].includes(object.tag))p.onText(true);}else if(item.kind==='text'){p.onSelect([item.id]);p.onEnter(null);p.onNodes([]);p.onText(false);}}}>
   <div className="figure-artwork" ref={art} dangerouslySetInnerHTML={{__html:svg}}/>
   <svg className="figure-controls" viewBox={`0 0 ${p.doc.width} ${p.doc.height}`} aria-label={t('选择控制点')}>
    {box&&<g><rect x={box.x} y={box.y} width={Math.max(1,box.width)} height={Math.max(1,box.height)} className="figure-selection-box"/>{showHandles&&<>{(['nw','ne','sw','se'] as const).map(h=><rect key={h} aria-label={t('缩放控制点')+' '+h} x={(h.includes('w')?box.x:box.x+box.width)-5/p.zoom} y={(h.includes('n')?box.y:box.y+box.height)-5/p.zoom} width={10/p.zoom} height={10/p.zoom} className="figure-handle" onPointerDown={e=>begin(e,'scale',h)}/>)}<line x1={box.x+box.width/2} x2={box.x+box.width/2} y1={box.y} y2={box.y-26/p.zoom} className="figure-selection-box"/><circle aria-label={t('旋转控制点')} cx={box.x+box.width/2} cy={box.y-26/p.zoom} r={6/p.zoom} className="figure-handle rotate" onPointerDown={e=>begin(e,'rotate')}/></>}</g>}
    {marquee&&<rect {...marquee} className="figure-marquee"/>}
   </svg>
  </div>
 </div>;
});
