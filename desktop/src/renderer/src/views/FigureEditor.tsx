import {useEffect,useMemo,useRef,useState} from 'react';
import {MousePointer2,Hand,Type,Square} from 'lucide-react';
import {useAuth} from '../AuthGate';
import {useT} from '../i18n';
import {blankFigure,parseFigure,importSvg,figureSvg,svgTextEntries,replaceSvgText,svgColors,replaceSvgColor,gridLayout,alignItems,fitFigure,fitTextBox,moveItems,exportFigurePng,newFigureId,type FigureDocument,type FigureItem} from './figure-model';
import FigureCanvas,{type FigureCanvasHandle} from './FigureCanvas';
import FigurePdfImport from './FigurePdfImport';
import FigureInspector from './FigureInspector';
import {loadFigureDraft,saveFigureDraft} from './figure-draft';
import {vectorObjects,editVector,deleteVectorNodes,groupVectorNodes,ungroupVectorNodes,pasteVectorNodes,reorderVectorNodes,selectedVectorSvg,type VectorPatch} from './figure-vector';
import {importSvgWithReport} from './figure-model';
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
  const [labelLetter,setLabelLetter]=useState('A');
  const [inside,setInside]=useState<string|null>(null),[nodeIds,setNodeIds]=useState<string[]>([]),[tool,setTool]=useState<'select'|'pan'>('select');
  const [fonts,setFonts]=useState<string[]>([]),[pdfQueue,setPdfQueue]=useState<File[]>([]);
  const [reports,setReports]=useState<{name:string;objects:number;images:number;warnings:string[]}[]>([]),[draftSaved,setDraftSaved]=useState(true);
  const canvasHandle=useRef<FigureCanvasHandle>(null),nodeText=useRef<HTMLTextAreaElement>(null);
  const clipboard=useRef<{items?:FigureItem[];svg?:string;ids?:string[]}|null>(null);
  const vectorList=useMemo(()=>{try{return inside?vectorObjects(doc.items.find(i=>i.id===inside)?.svg||''):[];}catch{return[];}},[inside,doc.items]);
  const vectorCurrent=vectorList.find(n=>n.id===nodeIds.at(-1));
  useEffect(()=>{let keep=true;void Promise.resolve().then(()=>window.biodsh.drawing('figure_fonts',{})).then((v:any)=>{if(keep&&Array.isArray(v?.families))setFonts(v.families.filter((s:unknown)=>typeof s==='string'));}).catch(()=>{});return()=>{keep=false;};},[]);
  useEffect(()=>{if(inside&&!doc.items.some(i=>i.id===inside)){setInside(null);setNodeIds([]);}},[doc.items,inside]);
  const fileInput=useRef<HTMLInputElement>(null),live=useRef(true),generation=useRef(0),request=useRef(''),busyRef=useRef(false);
  const textInput=useRef<HTMLTextAreaElement>(null),focusText=useRef(false);
  const api=(action:string,body:Record<string,unknown>={})=>window.biodsh.drawing(action,{...body,lang});
  const current=doc.items.find(item=>item.id===selected.at(-1));
  const editableIds=selected.filter(id=>doc.items.some(item=>item.id===id&&!item.locked));
  useEffect(()=>{if(focusText.current&&current?.kind==='text'&&!current.locked){focusText.current=false;textInput.current?.focus();textInput.current?.select();}},[current?.id]);

  useEffect(()=>{
    live.current=true;generation.current++;busyRef.current=false;setBusy('');setError('');setNotice('');setSelected([]);setPast([]);setFuture([]);setProject('');setProjects([]);setPrompt('');setHistory([]);setInside(null);setNodeIds([]);setPdfQueue([]);setReports([]);
    let keep=true;setLoaded('');busyRef.current=true;setBusy('正在打开…');setDoc(blankFigure(t('未命名拼图')));
    void loadFigureDraft(draftKey).then(raw=>{if(!keep)return;if(raw){const saved=JSON.parse(raw);setDoc(parseFigure(saved.document));setHistory(validHistory(saved.history));}setLoaded(draftKey);})
      .catch(()=>{if(keep)setError('拼图草稿无法恢复，请打开保存的项目文件。');})
      .finally(()=>{if(keep){busyRef.current=false;setBusy('');}});
    return()=>{keep=false;live.current=false;generation.current++;if(request.current)void window.biodsh.drawing('figure_cancel',{requestId:request.current}).catch(()=>{});request.current='';};
  },[draftKey]);
  useEffect(()=>{
    if(loaded!==draftKey)return;
    let keep=true;setDraftSaved(false);const timer=window.setTimeout(()=>{try{parseFigure(doc);void saveFigureDraft(draftKey,JSON.stringify({document:doc,history})).then(()=>{if(keep)setDraftSaved(true);}).catch(error=>{if(keep)setError(message(error));});}catch(e){if(keep)setError(message(e));}},400);
    return()=>{keep=false;window.clearTimeout(timer);};
  },[doc,history,draftKey,loaded]);
  useEffect(()=>{let keep=true;if(active)void window.biodsh.dshRpc('workspace.list').then((v:any)=>{if(keep)setProjects((v.items||[]).filter((p:any)=>typeof p.path==='string'));}).catch(()=>{});return()=>{keep=false;};},[active,owner]);
  const commit=(next:FigureDocument)=>{try{parseFigure(next);setPast(p=>[...p.slice(-39),doc]);setFuture([]);setDoc(next);setNotice('');}catch(error){setError(message(error));}};
  const safe=(work:()=>FigureDocument)=>{try{setError('');commit(work());}catch(e){setError(message(e));}};
  const updateItem=(id:string,patch:Partial<FigureItem>)=>safe(()=>({...doc,items:doc.items.map(item=>item.id===id&&!item.locked?(patch.text!==undefined||patch.fontSize!==undefined?fitTextBox({...item,...patch}):{...item,...patch}):item)}));
  const undo=()=>{if(!past.length)return;setFuture(f=>[doc,...f]);setDoc(past[past.length-1]);setNodeIds([]);setPast(p=>p.slice(0,-1));setSelected([]);};
  const redo=()=>{if(!future.length)return;setPast(p=>[...p,doc]);setDoc(future[0]);setNodeIds([]);setFuture(f=>f.slice(1));setSelected([]);};
  const fit=()=>safe(()=>{const next=fitFigure(doc,24);const area=canvasHandle.current?.viewport();if(area)setZoom(Math.max(.1,Math.min(2,(area.width-12)/next.width,(area.height-12)/next.height)));return next;});
  async function run(label:string,work:(valid:()=>boolean)=>Promise<void>){
    if(busyRef.current)return;busyRef.current=true;setBusy(label);setError('');setNotice('');const stamp=++generation.current;
    const valid=()=>live.current&&stamp===generation.current;
    try{await work(valid);}catch(e){if(valid())setError(message(e));}finally{if(valid()){busyRef.current=false;setBusy('');}}
  }
  function select(id:string,add=false){setInside(null);setNodeIds([]);const item=doc.items.find(v=>v.id===id);if(!item)return;const ids=item.groupId?doc.items.filter(v=>v.groupId===item.groupId).map(v=>v.id):[id];setSelected(old=>add?(old.includes(id)?old.filter(v=>!ids.includes(v)):[...new Set([...old,...ids])]):ids);}
  function add(item:FigureItem){if(busy)return;setInside(null);setNodeIds([]);const x=40+(doc.items.length%3)*80,y=40+Math.floor(doc.items.length/3)*45;focusText.current=item.kind==='text';commit({...doc,items:[...doc.items,fitTextBox({...item,x,y})]});setSelected([item.id]);}
  function reportItem(item:FigureItem,warnings:string[]=[]){const objects=item.svg?vectorObjects(item.svg):[];return{name:item.name,objects:objects.filter(o=>o.tag!=='g'&&o.tag!=='image').length,images:item.kind==='image'?1:objects.filter(o=>o.tag==='image').length,warnings};}
  async function importFiles(files:File[]){await run('正在导入…',async valid=>{
    const incoming:FigureItem[]=[],pdfs:File[]=[],results:ReturnType<typeof reportItem>[]=[];
    for(const file of files){try{
      if(file.size>50_000_000)throw Error(t('单个文件不能超过 50 MB。'));
      const header=await file.slice(0,1024).arrayBuffer(),signature=new Uint8Array(header),headerText=new TextDecoder().decode(header);
      if(new TextDecoder().decode(signature).startsWith('%PDF-')){pdfs.push(file);continue;}
      if(/\.svg$/i.test(file.name)||file.type==='image/svg+xml'||/^\s*(?:<\?xml[^>]*>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg[\s>]/i.test(headerText)){const parsed=importSvgWithReport(await file.text(),file.name);incoming.push(parsed.item);results.push(reportItem(parsed.item,parsed.report.warnings));}
      else if(signature[0]===137&&signature[1]===80||signature[0]===255&&signature[1]===216||/^image\/(png|jpeg|webp|gif)$/.test(file.type)){
        const mime=signature[0]===137&&signature[1]===80?'image/png':signature[0]===255&&signature[1]===216?'image/jpeg':file.type;
        const dataUrl=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).replace(/^data:[^;]*;/,`data:${mime};`));reader.onerror=()=>reject(Error(t('图片无法读取')));reader.readAsDataURL(file);});
        const image=new Image();image.src=dataUrl;await image.decode();const width=Math.min(440,image.naturalWidth),height=width*image.naturalHeight/image.naturalWidth;
        const item:FigureItem={id:newFigureId(),name:file.name,kind:'image',x:0,y:0,width,height,rotation:0,hidden:false,locked:false,dataUrl,source:{name:file.name,kind:'image'}};parseFigure({...blankFigure(),items:[item]});incoming.push(item);results.push(reportItem(item));
      }else throw Error(t('文件内容不是支持的 SVG、PDF 或图片格式。'));
    }catch(error){results.push({name:file.name,objects:0,images:0,warnings:[message(error)]});}}
    if(!valid())return;if(incoming.length){commit(parseFigure({...doc,items:[...doc.items,...incoming.map((item,i)=>({...item,x:40+(i%3)*100,y:40+Math.floor(i/3)*80}))]}));setSelected(incoming.map(item=>item.id));setInside(null);setNodeIds([]);}
    setReports(results);setPdfQueue(pdfs);
  });}
  function importPdf(items:Array<{name:string;page:number;svg?:string;dataUrl?:string;width:number;height:number;warnings:string[]}>){try{
    const incoming=items.map((row,i)=>{const scale=Math.min(1,440/Math.max(row.width,row.height));let item:FigureItem;let warnings=row.warnings||[];
      if(row.svg){const parsed=importSvgWithReport(row.svg,row.name);item=parsed.item;warnings=[...warnings,...parsed.report.warnings];}
      else item={id:newFigureId(),name:row.name,kind:'image',dataUrl:row.dataUrl,width:row.width*scale,height:row.height*scale,x:0,y:0,rotation:0,hidden:false,locked:false};
      item={...item,x:40+i*60,y:40+i*45,source:{name:row.name,kind:'pdf',page:row.page}};return{item,report:reportItem(item,warnings)};});
    commit(parseFigure({...doc,items:[...doc.items,...incoming.map(v=>v.item)]}));setReports(old=>[...old,...incoming.map(v=>v.report)]);setSelected(incoming.map(v=>v.item.id));setInside(null);setNodeIds([]);setPdfQueue(files=>files.slice(1));
  }catch(error){setError(message(error));throw error;}}
  async function open(){if(doc.items.length&&!confirm(t('打开其他拼图前，请确认当前修改已保存。')))return;await run('正在打开…',async valid=>{
    const value=await api('figure_open') as {document?:unknown;svg?:string;name:string}|null;if(!value||!valid())return;
    const next=value.document?parseFigure(value.document):fitFigure({...blankFigure(value.name),items:[importSvg(value.svg||'',value.name)]},24);
    commit(next);setLoaded(draftKey);setSelected([]);setHistory([]);setInside(null);setNodeIds([]);
  });}
  async function save(){await run('正在保存…',async valid=>{
    parseFigure(doc);
    let path=project;if(!path){path=await window.biodsh.pickFolder()||'';if(!path||!valid())return;setProject(path);}
    const result=await api('figure_save',{project:path,document:JSON.stringify(doc),svg:figureSvg(doc)}) as {path:string};
    if(valid())setNotice(t('已保存到：{path}',{path:result.path}));
  });}
  async function exportImage(format:'svg'|'png'){await run('正在导出…',async valid=>{
    parseFigure(doc);
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
  const mapSelected=(patch:Partial<FigureItem>)=>safe(()=>({...doc,items:doc.items.map(item=>editableIds.includes(item.id)?fitTextBox({...item,...patch}):item)}));
  const deleteSelected=()=>{if(busy)return;if(inside){if(!nodeIds.length||current?.locked||current?.hidden)return;safe(()=>({...doc,items:doc.items.map(item=>item.id===inside&&item.svg?{...item,svg:deleteVectorNodes(item.svg,nodeIds)}:item)}));setNodeIds([]);return;}if(!editableIds.length)return;safe(()=>({...doc,items:doc.items.filter(item=>item.locked||(!editableIds.includes(item.id)&&!editableIds.includes(item.labelFor||''))).map(item=>item.labelFor&&editableIds.includes(item.labelFor)?{...item,labelFor:undefined}:item)}));setSelected([]);};
  function layer(direction:number){if(busy||!current||current.locked||current.hidden)return;if(inside&&current.svg){safe(()=>({...doc,items:doc.items.map(item=>item.id===inside?{...item,svg:reorderVectorNodes(item.svg!,nodeIds,direction>0?'forward':'backward')}:item)}));return;}safe(()=>{const items=[...doc.items];const indices=items.flatMap((item,i)=>editableIds.includes(item.id)?[i]:[]);for(const i of direction>0?indices.reverse():indices){const j=i+direction;if(j>=0&&j<items.length&&!editableIds.includes(items[j].id))[items[i],items[j]]=[items[j],items[i]];}return {...doc,items};});}
  const textEntries=current?.kind==='svg'&&current.svg?svgTextEntries(current.svg):[];
  const colors=current?.kind==='svg'&&current.svg?svgColors(current.svg):[];
  function vectorPatch(patch:VectorPatch,ids=nodeIds){if(busy||!inside||!current?.svg||current.locked||current.hidden)return;safe(()=>({...doc,items:doc.items.map(item=>item.id===inside?{...item,svg:editVector(item.svg!,ids,patch)}:item)}));}
  function vectorBatch(changes:Array<{ids:string[];patch:VectorPatch}>){if(busy||!inside||!current?.svg||current.locked||current.hidden)return;safe(()=>{let svg=current.svg!;for(const change of changes)svg=editVector(svg,change.ids,change.patch);return{...doc,items:doc.items.map(item=>item.id===inside?{...item,svg}:item)};});}
  function grouping(remove=false){if(busy||current?.locked||current?.hidden)return;if(inside&&current?.svg){try{const result=remove?ungroupVectorNodes(current.svg,nodeIds):groupVectorNodes(current.svg,nodeIds);updateItem(current.id,{svg:result.svg});setNodeIds('ids' in result?result.ids:[result.id]);}catch(e){setError(message(e));}}else mapSelected({groupId:remove?undefined:newFigureId('group')});}
  function copy(){if(inside&&current?.svg&&nodeIds.length)clipboard.current={svg:current.svg,ids:[...nodeIds]};else clipboard.current={items:doc.items.filter(i=>selected.includes(i.id)).map(i=>({...i}))};}
  function paste(){if(busy)return;const clip=clipboard.current;if(!clip)return;try{if(inside&&current?.svg&&clip.svg&&clip.ids){if(current.locked||current.hidden)return;const result=pasteVectorNodes(current.svg,clip.svg,clip.ids);updateItem(current.id,{svg:result.svg});setNodeIds(result.ids);}else if(clip.items?.length){const ids=new Map(clip.items.map(item=>[item.id,newFigureId()])),groups=new Map(clip.items.filter(i=>i.groupId).map(i=>[i.groupId!,newFigureId('group')]));const items=clip.items.map(i=>({...i,id:ids.get(i.id)!,x:i.x+24,y:i.y+24,groupId:i.groupId?groups.get(i.groupId):undefined,labelFor:i.labelFor?ids.get(i.labelFor):undefined,locked:false}));commit(parseFigure({...doc,items:[...doc.items,...items]}));setSelected(items.map(i=>i.id));setInside(null);}else if(clip.svg&&clip.ids){add(importSvg(selectedVectorSvg(clip.svg,clip.ids),t('复制的图形')));}}catch(error){setError(message(error));}}
  function keyboard(e:React.KeyboardEvent<HTMLDivElement>){if(busy||e.target!==e.currentTarget)return;const mod=e.ctrlKey||e.metaKey,k=e.key.toLowerCase();
    if(mod&&k==='a'){e.preventDefault();inside?setNodeIds(vectorList.filter(n=>!n.locked&&!n.hidden&&n.tag!=='g').map(n=>n.id)):setSelected(doc.items.filter(i=>!i.locked&&!i.hidden).map(i=>i.id));}
    else if(mod&&k==='z'){e.preventDefault();e.shiftKey?redo():undo();}else if(mod&&k==='y'){e.preventDefault();redo();}
    else if(mod&&k==='c'){e.preventDefault();copy();}else if(mod&&k==='v'){e.preventDefault();paste();}else if(mod&&k==='x'){e.preventDefault();copy();deleteSelected();}
    else if(mod&&k==='g'){e.preventDefault();grouping(e.shiftKey);}else if(e.key==='Escape'){setInside(null);setNodeIds([]);setSelected([]);}
    else if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();deleteSelected();}
    else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const n=e.shiftKey?10:1;canvasHandle.current?.nudge(e.key==='ArrowLeft'?-n:e.key==='ArrowRight'?n:0,e.key==='ArrowUp'?-n:e.key==='ArrowDown'?n:0);}
  }
  const count=inside?nodeIds.length:selected.length;
  return <section className="figure-editor" aria-label={t('论文拼图与精修')}>
    <header className="figure-header"><div><h1>{t('论文拼图与精修')}</h1><p>{t('导入图表，统一排版，精修文字与颜色。')}</p></div><div className="figure-actions">
      <button className="btn" disabled={!!busy} onClick={()=>{if(!doc.items.length||confirm(t('新建拼图前，请确认当前修改已保存。'))){commit(blankFigure(t('未命名拼图')));setLoaded(draftKey);setSelected([]);setHistory([]);setInside(null);setNodeIds([]);}}}>{t('新建')}</button>
      <button className="btn" disabled={!!busy} onClick={()=>void open()}>{t('打开拼图')}</button>
      <button className="btn" disabled={!!busy} onClick={()=>fileInput.current?.click()}>{t('导入图表或图片')}</button>
      <input hidden ref={fileInput} aria-label={t('导入图表或图片')} type="file" multiple accept=".svg,.pdf,.png,.jpg,.jpeg,.webp,.gif" onChange={e=>{const files=Array.from(e.target.files||[]);e.target.value='';if(files.length)void importFiles(files);}}/>
      <button className="btn" disabled={!!busy||!doc.items.length} onClick={()=>void exportImage('svg')}>{t('导出 SVG')}</button>
      <button className="btn" disabled={!!busy||!doc.items.length} onClick={()=>void exportImage('png')}>{t('导出 PNG')}</button>
      <button className="btn btn-primary" disabled={!!busy||!doc.items.length} onClick={()=>void save()}>{t('保存到项目')}</button>
    </div></header>
    <div className="figure-project"><label>{t('保存项目')}<select value={project} disabled={!!busy} onChange={e=>setProject(e.target.value)}><option value="">{t('选择本地项目')}</option>{project&&!projects.some(p=>p.path===project)&&<option value={project}>{project}</option>}{projects.map(p=><option key={p.path} value={p.path}>{p.title||p.path}</option>)}</select></label><button className="btn btn-ghost" disabled={!!busy} onClick={()=>void run('正在打开…',async valid=>{const p=await window.biodsh.pickFolder();if(p&&valid())setProject(p);})}>{t('选择文件夹')}</button><small>{t('每次保存生成新版本，保留原图。')}</small></div>
    {error&&<p role="alert" className="drawing-error">{t(error)}</p>}{notice&&<p role="status" className="drawing-notice selectable">{notice}</p>}
    <div className="figure-layout"><div className="figure-main"><fieldset className="figure-toolbar" disabled={!!busy}>
      <button className="btn" disabled={!past.length} onClick={undo}>{t('撤销')}</button><button className="btn" disabled={!future.length} onClick={redo}>{t('重做')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('文字'),kind:'text',text:t('文字'),fontSize:32,color:'#222222',x:0,y:0,width:180,height:48,rotation:0,hidden:false,locked:false})}>{t('添加文字')}</button>
      <label>{t('标签字母')}<select aria-label={t('标签字母')} value={labelLetter} onChange={e=>setLabelLetter(e.target.value)}>{Array.from({length:26},(_,i)=>String.fromCharCode(65+i)).map(letter=><option key={letter} value={letter}>{letter}</option>)}</select></label><button className="btn" onClick={()=>add({id:newFigureId('label'),name:`${t('标签')} ${labelLetter}`,kind:'text',text:labelLetter,fontSize:24,color:'#222222',x:0,y:0,width:50,height:32,rotation:0,hidden:false,locked:false})}>{t('添加标签')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('矩形'),kind:'rect',fill:'none',stroke:'#64748b',x:0,y:0,width:240,height:160,rotation:0,hidden:false,locked:false})}>{t('添加矩形')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('椭圆'),kind:'ellipse',fill:'none',stroke:'#64748b',x:0,y:0,width:180,height:120,rotation:0,hidden:false,locked:false})}>{t('添加椭圆')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('箭头'),kind:'arrow',stroke:'#334155',strokeWidth:2,x:0,y:0,width:180,height:40,rotation:0,hidden:false,locked:false})}>{t('添加箭头')}</button>
      <button className="btn" onClick={()=>add({id:newFigureId(),name:t('线条'),kind:'line',stroke:'#334155',strokeWidth:2,x:0,y:0,width:180,height:1,rotation:0,hidden:false,locked:false})}>{t('添加线条')}</button>
      <button className="btn" onClick={fit}>{t('画布适应内容')}</button>
      <label>{t('缩放')}<input type="range" min=".1" max="2" step=".05" value={zoom} onChange={e=>setZoom(Number(e.target.value))}/><span>{Math.round(zoom*100)}%</span></label>
    </fieldset>
    <fieldset className="figure-toolbar" disabled={!!busy||!editableIds.length}>
      <label>{t('列数')}<input aria-label={t('拼图列数')} type="number" min="1" max="20" value={columns} onChange={e=>setColumns(Math.max(1,Math.min(20,Number(e.target.value)||1)))}/></label>
      <label>{t('间距')}<input aria-label={t('拼图间距')} type="number" min="0" max="500" value={gap} onChange={e=>setGap(Math.max(0,Math.min(500,Number(e.target.value))))}/></label>
      <button className="btn" disabled={!!inside} onClick={()=>safe(()=>gridLayout(doc,editableIds,columns,gap))}>{t('网格拼图')}</button>
      <select aria-label={t('对齐与分布')} defaultValue="" onChange={e=>{if(e.target.value){if(inside)canvasHandle.current?.align(e.target.value);else safe(()=>alignItems(doc,editableIds,e.target.value as Parameters<typeof alignItems>[2]));}e.target.value='';}}><option value="">{t('对齐与分布')}</option>{(['left','center','right','top','middle','bottom','horizontal','vertical'] as const).map((key,i)=><option key={key} value={key}>{t(['左对齐','水平居中','右对齐','顶部对齐','垂直居中','底部对齐','水平分布','垂直分布'][i])}</option>)}</select>
      <button className="btn" disabled={count<2} onClick={()=>grouping()}>{t('分组')}</button><button className="btn" onClick={()=>grouping(true)}>{t('取消分组')}</button>
    </fieldset>
    <fieldset className="figure-selection" disabled={!!busy}>
      <div className="figure-selection-actions"><span>{count?t('已选中 {n} 个元素',{n:count}):t('选择元素可编辑或删除')}</span><button type="button" className="btn figure-delete" disabled={inside?!nodeIds.length||current?.locked||current?.hidden:!editableIds.length} title={current?.locked?t('请先解锁图层再编辑或删除'):undefined} onClick={deleteSelected}>{t('删除所选元素')}</button></div>
      {!inside&&current?.kind==='text'&&<label>{t(current.labelFor||current.id.startsWith('label-')?'标签内容':'文字内容')}<textarea ref={textInput} aria-label={t(current.labelFor||current.id.startsWith('label-')?'标签内容':'文字内容')} rows={2} value={current.text||''} disabled={current.locked} maxLength={4000} onChange={e=>updateItem(current.id,{text:e.target.value})}/></label>}
      {current?.locked&&<small>{t('请先解锁图层再编辑或删除')}</small>}
    </fieldset>
    <div className="figure-modebar"><div className="figure-mode-buttons"><button className="btn" disabled={!count} onClick={copy}>{t('复制')}</button><button className="btn" disabled={!!busy} onClick={paste}>{t('粘贴')}</button></div>
      {inside?<><span>{current?.name} / {t('图内编辑')}</span><button className="btn" onClick={()=>{setInside(null);setNodeIds([]);}}>{t('返回整图布局')}</button></>:current?.kind==='svg'&&<button className="btn btn-tint" disabled={current.locked} onClick={()=>{setInside(current.id);setNodeIds([]);}}>{t('编辑图内元素')}</button>}
      <small>{t('拖框多选 · Shift 加选 · Alt 循环选择 · 拖动角点缩放')}</small></div>
    {inside&&vectorCurrent&&(vectorCurrent.tag==='text'||vectorCurrent.tag==='tspan')&&<label className="figure-inline-text">{t('图内文字内容')}<textarea ref={nodeText} aria-label={t('图内文字内容')} rows={2} value={vectorCurrent.text||''} disabled={vectorCurrent.locked} onChange={e=>vectorPatch({text:e.target.value})}/></label>}
    <div className="figure-workspace"><nav className="figure-toolrail" aria-label={t('编辑工具')}><button className={tool==='select'?'is-active':''} aria-label={t('选择工具')} title={t('选择工具')} onClick={()=>setTool('select')}><MousePointer2 size={19}/></button><button className={tool==='pan'?'is-active':''} aria-label={t('平移工具')} title={t('平移工具')} onClick={()=>setTool('pan')}><Hand size={19}/></button><button aria-label={t('文字工具')} title={t('文字工具')} onClick={()=>add({id:newFigureId(),name:t('文字'),kind:'text',text:t('文字'),fontSize:32,color:'#222222',x:0,y:0,width:180,height:48,rotation:0,hidden:false,locked:false})}><Type size={19}/></button><button aria-label={t('形状工具')} title={t('形状工具')} onClick={()=>add({id:newFigureId(),name:t('矩形'),kind:'rect',fill:'none',stroke:'#64748b',x:0,y:0,width:240,height:160,rotation:0,hidden:false,locked:false})}><Square size={19}/></button></nav><FigureCanvas ref={canvasHandle} doc={doc} selected={selected} inside={inside} nodes={nodeIds} zoom={zoom} busy={!!busy} tool={tool} onSelect={setSelected} onNodes={setNodeIds} onEnter={setInside} onChange={setDoc} onCommit={next=>safe(()=>next)} onEnd={original=>{try{parseFigure(doc);setPast(p=>[...p.slice(-39),original]);setFuture([]);}catch(error){setDoc(original);setError(message(error));}}} onText={(isVector?:boolean)=>{setTimeout(()=>{(isVector?nodeText.current:textInput.current)?.focus();},0);}} onError={e=>setError(message(e))} onFiles={files=>void importFiles(files)} onKey={keyboard}/></div>
    <footer className="figure-status"><span>{Math.round(zoom*100)}%</span><span>{t('已选中 {n} 个元素',{n:count})}</span><span>{doc.widthMm.toFixed(1)} × {(doc.widthMm*doc.height/doc.width).toFixed(1)} mm</span><span>PNG {Math.ceil(doc.widthMm/25.4*dpi)} × {Math.ceil(Math.ceil(doc.widthMm/25.4*dpi)*doc.height/doc.width)} px · {dpi} dpi</span><span role="status">{t(loaded!==draftKey?(busy||'拼图草稿无法恢复，请打开保存的项目文件。'):draftSaved?'草稿已保存':'正在保存草稿…')}</span></footer>
    </div>
    <aside className="figure-inspector"><FigureInspector doc={doc} current={current} inside={inside} nodes={nodeIds} objects={vectorList} fonts={fonts} busy={!!busy} onDocument={next=>safe(()=>next)} onStyle={patch=>mapSelected(patch)} onVector={vectorPatch} onVectorBatch={vectorBatch} onItem={patch=>current&&updateItem(current.id,patch)} onNodes={setNodeIds}/><details open><summary>{t('画布与导出')}</summary><fieldset disabled={!!busy}>
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
    {!inside&&current&&<details open><summary>{t('所选图形')}</summary><fieldset disabled={!!busy||current.locked}>
      <label>{t('名称')}<input value={current.name} maxLength={150} onChange={e=>updateItem(current.id,{name:e.target.value})}/></label><div className="figure-fields">
      {(['x','y','width','height','rotation'] as const).map((key,i)=><label key={key}>{t(['横向位置','纵向位置','宽度','高度','旋转'][i])}<input type="number" value={Math.round(current[key]*100)/100} min={key==='width'||key==='height'?1:undefined} onChange={e=>{const n=Number(e.target.value);if(Number.isFinite(n)&&Math.abs(n)<=100000&&(!(key==='width'||key==='height')||n>0)){if(key==='x'||key==='y')safe(()=>moveItems(doc,[current.id],key==='x'?n-current.x:0,key==='y'?n-current.y:0));else updateItem(current.id,{[key]:n});}}}/></label>)}
      </div>
      {current.kind==='text'&&<><label>{t('字号')}<input type="number" min="4" max="400" value={current.fontSize||32} onChange={e=>updateItem(current.id,{fontSize:Math.max(4,Math.min(400,Number(e.target.value)))})}/></label><label>{t('文字颜色')}<input type="color" value={current.color||'#222222'} onChange={e=>updateItem(current.id,{color:e.target.value})}/></label></>}
      {current.kind==='rect'&&<><label className="figure-no-fill"><input type="checkbox" checked={!current.fill||current.fill==='none'} onChange={e=>updateItem(current.id,{fill:e.target.checked?'none':'#e5f3ee'})}/>{t('无填充')}</label><label>{t('填充颜色')}<input type="color" disabled={!current.fill||current.fill==='none'} value={/^#[a-f\d]{6}$/i.test(current.fill||'')?current.fill:'#e5f3ee'} onChange={e=>updateItem(current.id,{fill:e.target.value})}/></label><label>{t('边框颜色')}<input type="color" value={current.stroke||'#64748b'} onChange={e=>updateItem(current.id,{stroke:e.target.value})}/></label></>}
      {!inside&&!!textEntries.length&&<details open><summary>{t('图内文字')}</summary>{textEntries.map(entry=><label key={entry.index}>{t('文字 {n}',{n:entry.index+1})}<input value={entry.text} maxLength={4000} onChange={e=>{try{updateItem(current.id,{svg:replaceSvgText(current.svg!,entry.index,e.target.value)});}catch(err){setError(message(err));}}}/></label>)}</details>}
      {!inside&&!!colors.length&&<details><summary>{t('图内颜色')}</summary>{colors.map(color=><label className="figure-color" key={color}><span>{color}</span><input aria-label={t('替换颜色 {color}',{color})} type="color" value={/^#[a-f\d]{6}$/i.test(color)?color:'#000000'} onChange={e=>{try{updateItem(current.id,{svg:replaceSvgColor(current.svg!,color,e.target.value)});}catch(err){setError(message(err));}}}/></label>)}</details>}
    </fieldset></details>}
    {!!reports.length&&<details open className="figure-import-report"><summary>{t('导入结果')}</summary>{reports.map((report,i)=><div key={i}><strong>{report.name}</strong><p>{t('{objects} 个可编辑对象 · {images} 张嵌入图片',{objects:report.objects,images:report.images})}</p>{report.warnings.map((w,i)=><p className="figure-import-warning" key={i}>{t(w)}</p>)}</div>)}</details>}
    <details><summary>{t('对话修改')}</summary><div className="figure-chat">{history.map((turn,i)=><p key={i} className={turn.role==='user'?'is-user':''}>{turn.role==='assistant'?t(turn.content):turn.content}</p>)}</div><form onSubmit={e=>{e.preventDefault();void generate();}}><textarea aria-label={t('拼图修改要求')} value={prompt} maxLength={4000} disabled={!!busy} onChange={e=>setPrompt(e.target.value)} placeholder={t('例如：统一字体、调整配色，保留数据和图表内容。')}/><div className="figure-actions"><button className="btn btn-primary" disabled={!!busy||!prompt.trim()}>{busy==='正在修改图形…'?t('正在修改图形…'):t('修改图形')}</button>{busy==='正在修改图形…'&&<button type="button" className="btn" onClick={stop}>{t('停止')}</button>}</div></form><p className="figure-hint">{t('选择单个图层进行对话精修；其他图层保持原样。修改可撤销，图中数据与结论需要自行核对。')}</p></details>
    </aside></div>
    {pdfQueue[0]&&<FigurePdfImport key={pdfQueue[0].name+pdfQueue[0].lastModified} file={pdfQueue[0]} api={api} onImported={importPdf} onClose={()=>setPdfQueue(files=>files.slice(1))}/>}
  </section>;
}
function validHistory(raw:unknown):Turn[]{return Array.isArray(raw)?raw.filter(v=>v&&['user','assistant'].includes(v.role)&&typeof v.content==='string').slice(-30):[];}
function message(error:unknown){return String(error).replace(/^Error:\s*/,'');}
