import {useEffect,useRef,useState} from 'react';
import {useAuth} from '../AuthGate';
import {useT} from '../i18n';
import {blankFigure,parseFigure,importSvg,figureSvg,svgTextEntries,replaceSvgText,svgColors,replaceSvgColor,gridLayout,alignItems,fitFigure,addPanelLabels,moveItems,exportFigurePng,newFigureId,type FigureDocument,type FigureItem} from './figure-model';
import './FigureEditor.css';

type Turn={role:'user'|'assistant';content:string};
export default function FigureEditor({active}:{active:boolean}) {
  const {t,lang}=useT(), {user}=useAuth();
  const owner=user?.id||'local', draftKey='omaax.figure.'+owner;
  const [doc,setDoc]=useState<FigureDocument>(()=>blankFigure(t('未命名拼图')));
  const [selected,setSelected]=useState<string[]>([]),[past,setPast]=useState<FigureDocument[]>([]),[future,setFuture]=useState<FigureDocument[]>([]);
  const [project,setProject]=useState(''),[projects,setProjects]=useState<{path:string;title:string}[]>([]);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(''),[prompt,setPrompt]=useState(''),[history,setHistory]=useState<Turn[]>([]);
  const [loaded,setLoaded]=useState(''),[zoom,setZoom]=useState(.65),[columns,setColumns]=useState(2),[gap,setGap]=useState(24),[dpi,setDpi]=useState(300);
  const fileInput=useRef<HTMLInputElement>(null),canvas=useRef<HTMLDivElement>(null),live=useRef(true),generation=useRef(0),request=useRef(''),busyRef=useRef(false);
  const drag=useRef<{document:FigureDocument;ids:string[];x:number;y:number;moved:boolean}|null>(null);
  const api=(action:string,body:Record<string,unknown>={})=>window.biodsh.drawing(action,{...body,lang});
  const current=doc.items.find(item=>item.id===selected.at(-1));
  const editableIds=selected.filter(id=>doc.items.some(item=>item.id===id&&!item.locked));

  useEffect(()=>{
    live.current=true;generation.current++;drag.current=null;busyRef.current=false;setBusy('');setError('');setNotice('');setSelected([]);setPast([]);setFuture([]);setProject('');setProjects([]);setPrompt('');setHistory([]);
    let initial=blankFigure(t('未命名拼图'));
    try{const raw=localStorage.getItem(draftKey);if(raw){const saved=JSON.parse(raw);initial=parseFigure(saved.document);setHistory(validHistory(saved.history));}}
    catch{setError('拼图草稿无法恢复，请打开保存的项目文件。');}
    setDoc(initial);setLoaded(draftKey);
    return()=>{live.current=false;generation.current++;if(request.current)void window.biodsh.drawing('figure_cancel',{requestId:request.current}).catch(()=>{});request.current='';};
  },[draftKey]);
  useEffect(()=>{
    if(loaded!==draftKey)return;
    const timer=window.setTimeout(()=>{try{localStorage.setItem(draftKey,JSON.stringify({document:doc,history}));}catch{setError('拼图草稿空间不足，请保存到项目。');}},400);
    return()=>window.clearTimeout(timer);
  },[doc,history,draftKey,loaded]);
  useEffect(()=>{let keep=true;if(active)void window.biodsh.dshRpc('workspace.list').then((v:any)=>{if(keep)setProjects((v.items||[]).filter((p:any)=>typeof p.path==='string'));}).catch(()=>{});return()=>{keep=false;};},[active,owner]);
  const commit=(next:FigureDocument)=>{setPast(p=>[...p.slice(-39),doc]);setFuture([]);setDoc(next);setNotice('');};
  const safe=(work:()=>FigureDocument)=>{try{setError('');commit(work());}catch(e){setError(message(e));}};
  const updateItem=(id:string,patch:Partial<FigureItem>)=>safe(()=>({...doc,items:doc.items.map(item=>item.id===id&&!item.locked?{...item,...patch}:item)}));
  const undo=()=>{if(!past.length)return;setFuture(f=>[doc,...f]);setDoc(past[past.length-1]);setPast(p=>p.slice(0,-1));setSelected([]);};
  const redo=()=>{if(!future.length)return;setPast(p=>[...p,doc]);setDoc(future[0]);setFuture(f=>f.slice(1));setSelected([]);};
  const fit=()=>safe(()=>{const next=fitFigure(doc,24);const area=canvas.current?.parentElement;if(area)setZoom(Math.max(.1,Math.min(2,(area.clientWidth-48)/next.width,(area.clientHeight-48)/next.height)));return next;});
  async function run(label:string,work:(valid:()=>boolean)=>Promise<void>){
    if(busyRef.current)return;busyRef.current=true;setBusy(label);setError('');setNotice('');const stamp=++generation.current;
    const valid=()=>live.current&&stamp===generation.current;
    try{await work(valid);}catch(e){if(valid())setError(message(e));}finally{if(valid()){busyRef.current=false;setBusy('');}}
  }
  function select(id:string,add=false){const item=doc.items.find(v=>v.id===id);if(!item)return;const ids=item.groupId?doc.items.filter(v=>v.groupId===item.groupId).map(v=>v.id):[id];setSelected(old=>add?(old.includes(id)?old.filter(v=>!ids.includes(v)):[...new Set([...old,...ids])]):ids);}
  function add(item:FigureItem){const x=40+(doc.items.length%3)*80,y=40+Math.floor(doc.items.length/3)*45;commit({...doc,items:[...doc.items,{...item,x,y}]});setSelected([item.id]);}
  async function importFiles(files:File[]){await run('正在导入…',async valid=>{
    const incoming:FigureItem[]=[];
    for(const file of files){
      if(file.size>15_000_000)throw Error(t('单个导入文件不能超过 15 MB。'));
      if(/\.svg$/i.test(file.name)||file.type==='image/svg+xml')incoming.push(importSvg(await file.text(),file.name));
      else if(/^image\/(png|jpeg|webp|gif)$/.test(file.type)){
        const dataUrl=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error(t('图片无法读取')));reader.readAsDataURL(file);});
        const image=new Image();image.src=dataUrl;await image.decode();const width=Math.min(440,image.naturalWidth),height=width*image.naturalHeight/image.naturalWidth;
        incoming.push({id:newFigureId(),name:file.name,kind:'image',x:0,y:0,width,height,rotation:0,hidden:false,locked:false,dataUrl});
      }else throw Error(t('请导入 SVG、PNG、JPG、WebP 或 GIF 文件。'));
    }
    if(!valid())return;
    const next={...doc,items:[...doc.items,...incoming.map((item,i)=>({...item,x:40+(i%3)*100,y:40+Math.floor(i/3)*80}))]};
    commit(parseFigure(next));setSelected(incoming.map(item=>item.id));
  });}
  async function open(){if(doc.items.length&&!confirm(t('打开其他拼图前，请确认当前修改已保存。')))return;await run('正在打开…',async valid=>{
    const value=await api('figure_open') as {document?:unknown;svg?:string;name:string}|null;if(!value||!valid())return;
    const next=value.document?parseFigure(value.document):fitFigure({...blankFigure(value.name),items:[importSvg(value.svg||'',value.name)]},24);
    commit(next);setSelected([]);setHistory([]);
  });}
  async function save(){await run('正在保存…',async valid=>{
    let path=project;if(!path){path=await window.biodsh.pickFolder()||'';if(!path||!valid())return;setProject(path);}
    const result=await api('figure_save',{project:path,document:JSON.stringify(doc),svg:figureSvg(doc)}) as {path:string};
    if(valid())setNotice(t('已保存到：{path}',{path:result.path}));
  });}
  async function exportImage(format:'svg'|'png'){await run('正在导出…',async valid=>{
    const body=format==='svg'?{svg:figureSvg(doc)}:{dataUrl:await exportFigurePng(doc,dpi)};if(!valid())return;
    const result=await api('figure_export',{format,...body}) as {path:string}|null;
    if(valid()&&result)setNotice(t('已导出：{path}',{path:result.path}));
  });}
  async function generate(){const text=prompt.trim();if(!text)return;await run('正在修改图形…',async valid=>{
    const target=doc.items.length?doc.items.find(item=>selected.length===1&&item.id===selected[0]&&!item.locked&&!item.hidden):undefined;
    if(doc.items.length&&!target)throw Error('请先选择一个未锁定的可见图层进行精修。');
    const source=target?{...doc,width:target.width,height:target.height,items:[{...target,x:0,y:0,rotation:0}]}:doc;
    const requestId=crypto.randomUUID();request.current=requestId;
    try{
      const result=await api('figure_generate',{requestId,prompt:text,svg:figureSvg(source),history:history.slice(-10)}) as {svg:string;text:string};
      if(!valid()||request.current!==requestId)return;
      const generated=importSvg(result.svg,target?.name||doc.title);
      const item:FigureItem=target?{...generated,id:target.id,name:target.name,x:target.x,y:target.y,width:target.width,height:target.height,rotation:target.rotation,groupId:target.groupId,labelFor:target.labelFor}: {...generated,x:0,y:0,width:doc.width,height:doc.height};
      commit({...doc,items:target?doc.items.map(previous=>previous.id===target.id?item:previous):[item]});setSelected([item.id]);setHistory(old=>[...old,{role:'user',content:text},{role:'assistant',content:'图已修改，可继续编辑或撤销。'}].slice(-30) as Turn[]);setPrompt('');
    }finally{if(request.current===requestId)request.current='';}
  });}
  function stop(){const id=request.current;if(!id)return;request.current='';generation.current++;const stamp=generation.current;busyRef.current=false;setBusy('');setNotice(t('已停止，保留修改前的图。'));void api('figure_cancel',{requestId:id}).catch(e=>{if(live.current&&stamp===generation.current)setError(message(e));});}
  const mapSelected=(patch:Partial<FigureItem>)=>safe(()=>({...doc,items:doc.items.map(item=>editableIds.includes(item.id)?{...item,...patch}:item)}));
  const deleteSelected=()=>{safe(()=>({...doc,items:doc.items.filter(item=>!editableIds.includes(item.id)&&!editableIds.includes(item.labelFor||''))}));setSelected([]);};
  function layer(direction:number){if(!current||current.locked)return;safe(()=>{const items=[...doc.items],i=items.findIndex(v=>v.id===current.id),j=Math.max(0,Math.min(items.length-1,i+direction));[items[i],items[j]]=[items[j],items[i]];return {...doc,items};});}
  const svgPoint=(e:{clientX:number;clientY:number})=>{const matrix=canvas.current?.querySelector('svg')?.getScreenCTM();return matrix?new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse()):null;};
  const textEntries=current?.kind==='svg'&&current.svg?svgTextEntries(current.svg):[];
  const colors=current?.kind==='svg'&&current.svg?svgColors(current.svg):[];
  return <section className="figure-editor" aria-label={t('论文拼图与精修')}>
    <header className="figure-header"><div><h1>{t('论文拼图与精修')}</h1><p>{t('导入图表，统一排版，精修文字与颜色。')}</p></div><div className="figure-actions">
      <button className="btn" disabled={!!busy} onClick={()=>{if(!doc.items.length||confirm(t('新建拼图前，请确认当前修改已保存。'))){commit(blankFigure(t('未命名拼图')));setSelected([]);setHistory([]);}}}>{t('新建')}</button>
      <button className="btn" disabled={!!busy} onClick={()=>void open()}>{t('打开拼图')}</button>
      <button className="btn" disabled={!!busy} onClick={()=>fileInput.current?.click()}>{t('导入图表或图片')}</button>
      <input hidden ref={fileInput} aria-label={t('导入图表或图片')} type="file" multiple accept=".svg,.png,.jpg,.jpeg,.webp,.gif" onChange={e=>{const files=Array.from(e.target.files||[]);e.target.value='';if(files.length)void importFiles(files);}}/>
      <button className="btn" disabled={!!busy||!doc.items.length} onClick={()=>void exportImage('svg')}>{t('导出 SVG')}</button>
      <button className="btn" disabled={!!busy||!doc.items.length} onClick={()=>void exportImage('png')}>{t('导出 PNG')}</button>
      <button className="btn btn-primary" disabled={!!busy||!doc.items.length} onClick={()=>void save()}>{t('保存到项目')}</button>
    </div></header>
    <div className="figure-project"><label>{t('保存项目')}<select value={project} disabled={!!busy} onChange={e=>setProject(e.target.value)}><option value="">{t('选择本地项目')}</option>{project&&!projects.some(p=>p.path===project)&&<option value={project}>{project}</option>}{projects.map(p=><option key={p.path} value={p.path}>{p.title||p.path}</option>)}</select></label><button className="btn btn-ghost" disabled={!!busy} onClick={()=>void run('正在打开…',async valid=>{const p=await window.biodsh.pickFolder();if(p&&valid())setProject(p);})}>{t('选择文件夹')}</button><small>{t('每次保存生成新版本，保留原图。')}</small></div>
    {error&&<p role="alert" className="drawing-error">{t(error)}</p>}{notice&&<p role="status" className="drawing-notice selectable">{notice}</p>}
    <div className="figure-layout"><div className="figure-main"><fieldset className="figure-toolbar" disabled={!!busy}>
      <button className="btn" disabled={!past.length} onClick={undo}>{t('撤销')}</button><button className="btn" disabled={!future.length} onClick={redo}>{t('重做')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('文字'),kind:'text',text:t('文字'),fontSize:32,color:'#222222',x:0,y:0,width:180,height:48,rotation:0,hidden:false,locked:false})}>{t('添加文字')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('矩形'),kind:'rect',fill:'#e5f3ee',stroke:'#64748b',x:0,y:0,width:240,height:160,rotation:0,hidden:false,locked:false})}>{t('添加矩形')}</button>
      <button className="btn" onClick={fit}>{t('画布适应内容')}</button>
      <label>{t('缩放')}<input type="range" min=".1" max="2" step=".05" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/><span>{Math.round(zoom*100)}%</span></label>
    </fieldset>
    <fieldset className="figure-toolbar" disabled={!!busy||!editableIds.length}>
      <label>{t('列数')}<input aria-label={t('拼图列数')} type="number" min="1" max="20" value={columns} onChange={e=>setColumns(Math.max(1,Math.min(20,Number(e.target.value)||1)))}/></label>
      <label>{t('间距')}<input aria-label={t('拼图间距')} type="number" min="0" max="500" value={gap} onChange={e=>setGap(Math.max(0,Math.min(500,Number(e.target.value))))}/></label>
      <button className="btn" onClick={()=>safe(()=>gridLayout(doc,editableIds,columns,gap))}>{t('网格拼图')}</button>
      <select aria-label={t('对齐与分布')} defaultValue="" onChange={e=>{if(e.target.value)safe(()=>alignItems(doc,editableIds,e.target.value as Parameters<typeof alignItems>[2]));e.target.value='';}}><option value="">{t('对齐与分布')}</option>{(['left','center','right','top','middle','bottom','horizontal','vertical'] as const).map((key,i)=><option key={key} value={key}>{t(['左对齐','水平居中','右对齐','顶部对齐','垂直居中','底部对齐','水平分布','垂直分布'][i])}</option>)}</select>
      <button className="btn" onClick={()=>safe(()=>addPanelLabels(doc,editableIds))}>{t('添加 A/B 标签')}</button>
      <button className="btn" disabled={editableIds.length<2} onClick={()=>mapSelected({groupId:newFigureId('group')})}>{t('分组')}</button><button className="btn" onClick={()=>mapSelected({groupId:undefined})}>{t('取消分组')}</button>
    </fieldset>
    <div className="figure-stage" tabIndex={0} aria-label={t('拼图画布')} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();if(!busy)void importFiles(Array.from(e.dataTransfer.files));}} onKeyDown={e=>{
      if(busy||e.target!==e.currentTarget)return;
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();setSelected(doc.items.filter(i=>!i.locked&&!i.hidden).map(i=>i.id));}
      else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}
      else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y'){e.preventDefault();redo();}
      else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();deleteSelected();}
      else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const n=e.shiftKey?10:1;safe(()=>moveItems(doc,editableIds,e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0));}
    }}>
      {!doc.items.length&&<div className="figure-empty"><strong>{t('把图表放到这里，开始拼图')}</strong><p>{t('支持 SVG、PNG、JPG、WebP、GIF；SVG 中的文字和颜色可以继续修改。')}</p></div>}
      <div className="figure-canvas" ref={canvas} style={{width:doc.width*zoom}} onPointerDown={e=>{
        if(busy)return;e.currentTarget.parentElement?.focus();const id=(e.target as Element).closest('[data-figure-id]')?.getAttribute('data-figure-id');if(!id){setSelected([]);return;}if(e.shiftKey||!selected.includes(id))select(id,e.shiftKey);const item=doc.items.find(i=>i.id===id),point=svgPoint(e);if(!item||item.locked||!point||e.shiftKey)return;
        const ids=item.groupId?doc.items.filter(i=>i.groupId===item.groupId&&!i.locked).map(i=>i.id):selected.includes(id)?editableIds:[id];drag.current={document:doc,ids,x:point.x,y:point.y,moved:false};e.currentTarget.setPointerCapture(e.pointerId);
      }} onPointerMove={e=>{const state=drag.current,p=svgPoint(e);if(!state||!p)return;if(Math.abs(p.x-state.x)+Math.abs(p.y-state.y)<1)return;state.moved=true;setDoc(moveItems(state.document,state.ids,p.x-state.x,p.y-state.y));}}
      onPointerUp={()=>{const state=drag.current;drag.current=null;if(state?.moved){setPast(p=>[...p.slice(-39),state.document]);setFuture([]);}}}
      onPointerCancel={()=>{if(drag.current)setDoc(drag.current.document);drag.current=null;}} dangerouslySetInnerHTML={{__html:figureSvg(doc,selected)}}/>
    </div><p className="figure-hint">{t('Shift 多选，方向键微调。导出前可使用“画布适应内容”避免裁切。')}</p></div>
    <aside className="figure-inspector"><details open><summary>{t('画布与导出')}</summary><fieldset disabled={!!busy}>
      <label>{t('图标题')}<input value={doc.title} maxLength={150} onChange={e=>commit({...doc,title:e.target.value})}/></label>
      <div className="figure-fields">{(['width','height'] as const).map((key,i)=><label key={key}>{t(i?'画布高度':'画布宽度')}<input type="number" min="100" max="16000" value={doc[key]} onChange={e=>{const n=Number(e.target.value);if(n>=100&&n<=16000)commit({...doc,[key]:n});}}/></label>)}</div>
      <label>{t('印刷宽度（毫米）')}<input type="number" min="10" max="1000" value={doc.widthMm} onChange={e=>{const n=Number(e.target.value);if(n>=10&&n<=1000)commit({...doc,widthMm:n});}}/></label>
      <label>{t('PNG 分辨率')}<select value={dpi} onChange={e=>setDpi(Number(e.target.value))}><option value="150">150 dpi</option><option value="300">300 dpi</option><option value="600">600 dpi</option></select></label>
    </fieldset></details>
    <details open><summary>{t('图层')} · {doc.items.length}</summary><div className="figure-layers">{[...doc.items].reverse().map(item=><div key={item.id} className={selected.includes(item.id)?'is-selected':''}>
      <button className="figure-layer-name" title={item.name} onClick={e=>select(item.id,e.shiftKey)}>{item.name||t('未命名图层')}</button>
      <button disabled={!!busy} aria-label={t(item.hidden?'显示图层':'隐藏图层')} title={t(item.hidden?'显示图层':'隐藏图层')} onClick={()=>commit({...doc,items:doc.items.map(i=>i.id===item.id?{...i,hidden:!i.hidden}:i)})}>{item.hidden?'○':'●'}</button>
      <button disabled={!!busy} aria-label={t(item.locked?'解锁图层':'锁定图层')} title={t(item.locked?'解锁图层':'锁定图层')} onClick={()=>commit({...doc,items:doc.items.map(i=>i.id===item.id?{...i,locked:!i.locked}:i)})}>{item.locked?'🔒':'🔓'}</button>
    </div>)}</div><div className="figure-actions"><button className="btn" disabled={!!busy||!current||current.locked} onClick={()=>layer(1)}>{t('上移一层')}</button><button className="btn" disabled={!!busy||!current||current.locked} onClick={()=>layer(-1)}>{t('下移一层')}</button><button className="btn" disabled={!!busy||!editableIds.length} onClick={deleteSelected}>{t('移除')}</button></div></details>
    {current&&<details open><summary>{t('所选图形')}</summary><fieldset disabled={!!busy||current.locked}>
      <label>{t('名称')}<input value={current.name} maxLength={150} onChange={e=>updateItem(current.id,{name:e.target.value})}/></label><div className="figure-fields">
      {(['x','y','width','height','rotation'] as const).map((key,i)=><label key={key}>{t(['横向位置','纵向位置','宽度','高度','旋转'][i])}<input type="number" value={Math.round(current[key]*100)/100} min={key==='width'||key==='height'?1:undefined} onChange={e=>{const n=Number(e.target.value);if(Number.isFinite(n)&&Math.abs(n)<=100000&&(!(key==='width'||key==='height')||n>0)){if(key==='x'||key==='y')safe(()=>moveItems(doc,[current.id],key==='x'?n-current.x:0,key==='y'?n-current.y:0));else updateItem(current.id,{[key]:n});}}}/></label>)}
      </div>
      {current.kind==='text'&&<><label>{t('文字')}<textarea aria-label={t('文字')} value={current.text||''} maxLength={4000} onChange={e=>updateItem(current.id,{text:e.target.value})}/></label><label>{t('字号')}<input type="number" min="4" max="400" value={current.fontSize||32} onChange={e=>updateItem(current.id,{fontSize:Math.max(4,Math.min(400,Number(e.target.value)))})}/></label><label>{t('文字颜色')}<input type="color" value={current.color||'#222222'} onChange={e=>updateItem(current.id,{color:e.target.value})}/></label></>}
      {current.kind==='rect'&&<><label>{t('填充颜色')}<input type="color" value={current.fill||'#e5f3ee'} onChange={e=>updateItem(current.id,{fill:e.target.value})}/></label><label>{t('边框颜色')}<input type="color" value={current.stroke||'#64748b'} onChange={e=>updateItem(current.id,{stroke:e.target.value})}/></label></>}
      {!!textEntries.length&&<details open><summary>{t('图内文字')}</summary>{textEntries.map(entry=><label key={entry.index}>{t('文字 {n}',{n:entry.index+1})}<input value={entry.text} maxLength={4000} onChange={e=>{try{updateItem(current.id,{svg:replaceSvgText(current.svg!,entry.index,e.target.value)});}catch(err){setError(message(err));}}}/></label>)}</details>}
      {!!colors.length&&<details><summary>{t('图内颜色')}</summary>{colors.map(color=><label className="figure-color" key={color}><span>{color}</span><input aria-label={t('替换颜色 {color}',{color})} type="color" value={/^#[a-f\d]{6}$/i.test(color)?color:'#000000'} onChange={e=>{try{updateItem(current.id,{svg:replaceSvgColor(current.svg!,color,e.target.value)});}catch(err){setError(message(err));}}}/></label>)}</details>}
    </fieldset></details>}
    <details open><summary>{t('对话修改')}</summary><div className="figure-chat">{history.map((turn,i)=><p key={i} className={turn.role==='user'?'is-user':''}>{turn.role==='assistant'?t(turn.content):turn.content}</p>)}</div><form onSubmit={e=>{e.preventDefault();void generate();}}><textarea aria-label={t('拼图修改要求')} value={prompt} maxLength={4000} disabled={!!busy} onChange={e=>setPrompt(e.target.value)} placeholder={t('例如：统一字体、调整配色，保留数据和图表内容。')}/><div className="figure-actions"><button className="btn btn-primary" disabled={!!busy||!prompt.trim()}>{busy==='正在修改图形…'?t('正在修改图形…'):t('修改图形')}</button>{busy==='正在修改图形…'&&<button type="button" className="btn" onClick={stop}>{t('停止')}</button>}</div></form><p className="figure-hint">{t('选择单个图层进行对话精修；其他图层保持原样。修改可撤销，图中数据与结论需要自行核对。')}</p></details>
    </aside></div>
  </section>;
}
function validHistory(raw:unknown):Turn[]{return Array.isArray(raw)?raw.filter(v=>v&&['user','assistant'].includes(v.role)&&typeof v.content==='string').slice(-30):[];}
function message(error:unknown){return String(error).replace(/^Error:\s*/,'');}
