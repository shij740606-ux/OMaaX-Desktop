import {useEffect,useId,useRef,useState,type KeyboardEvent} from 'react';
import {X} from 'lucide-react';
import {useT} from '../i18n';
import './FigurePdfImport.css';

export type FigurePdfImportedItem={name:string;page:number;svg?:string;dataUrl?:string;width:number;height:number;warnings:string[]};
type Props={file:File;api:(action:string,body?:Record<string,unknown>)=>Promise<unknown>;onImported:(items:FigurePdfImportedItem[])=>void;onClose:()=>void};
type PdfPage={page:number;width:number;height:number;preview:string};
type Inspection={token:string;pages:PdfPage[]};
type Phase='preparing'|'inspecting'|'ready'|'importing'|'failed';
type Mode='editable'|'page';

const record=(value:unknown):value is Record<string,unknown>=>typeof value==='object'&&value!==null&&!Array.isArray(value);
const dimension=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>0;
const imageData=(value:unknown):value is string=>typeof value==='string'&&/^data:image\/(?:png|jpe?g|webp);base64,[a-z\d+/=\s]+$/i.test(value);

function inspectResult(value:unknown):Inspection|null {
  if(!record(value)||typeof value.token!=='string'||!value.token.trim()||!Array.isArray(value.pages)||!value.pages.length)return null;
  const pages:PdfPage[]=[],seen=new Set<number>();
  for(const page of value.pages){
    if(!record(page)||typeof page.page!=='number'||!Number.isInteger(page.page)||page.page<1||seen.has(page.page)||!dimension(page.width)||!dimension(page.height)||!imageData(page.preview))return null;
    seen.add(page.page);pages.push({page:page.page,width:page.width,height:page.height,preview:page.preview});
  }
  return {token:value.token,pages};
}

function importResult(value:unknown,pages:number[]):FigurePdfImportedItem[]|null {
  if(!record(value)||!Array.isArray(value.items)||!value.items.length)return null;
  const items:FigurePdfImportedItem[]=[];
  for(const item of value.items){
    if(!record(item)||typeof item.name!=='string'||typeof item.page!=='number'||!pages.includes(item.page)||!dimension(item.width)||!dimension(item.height)||!Array.isArray(item.warnings)||!item.warnings.every(warning=>typeof warning==='string'))return null;
    const svg=typeof item.svg==='string'&&item.svg.trim()?item.svg:undefined;
    const dataUrl=imageData(item.dataUrl)?item.dataUrl:undefined;
    if(!svg&&!dataUrl)return null;
    items.push({name:item.name,page:item.page,width:item.width,height:item.height,warnings:item.warnings as string[],...(svg?{svg}:{}),...(dataUrl?{dataUrl}:{})});
  }
  return items;
}

export default function FigurePdfImport({file,api,onImported,onClose}:Props){
  const {t}=useT(),titleId=useId(),modeId=useId();
  const [phase,setPhase]=useState<Phase>('preparing'),[inspection,setInspection]=useState<Inspection|null>(null);
  const [selected,setSelected]=useState<number[]>([]),[mode,setMode]=useState<Mode>('editable'),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  const callbacks=useRef({api,onImported,onClose});callbacks.current={api,onImported,onClose};
  const generation=useRef(0),closed=useRef(false),busy=useRef(true),reader=useRef<FileReader|null>(null),closeButton=useRef<HTMLButtonElement>(null);
  const isBusy=phase==='preparing'||phase==='inspecting'||phase==='importing';

  function close(){
    if(closed.current)return;
    closed.current=true;generation.current++;reader.current?.abort();reader.current=null;
    callbacks.current.onClose();
  }

  useEffect(()=>{
    const previous=document.activeElement;
    closeButton.current?.focus();
    return()=>{if(previous instanceof HTMLElement&&previous.isConnected)previous.focus();};
  },[]);

  useEffect(()=>{
    closed.current=false;busy.current=true;
    const stamp=++generation.current,valid=()=>!closed.current&&generation.current===stamp;
    setPhase('preparing');setError('');setInspection(null);setSelected([]);setMode('editable');
    void (async()=>{
      try{
        await callbacks.current.api('figure_pdf_prepare',{});
        if(!valid())return;
        setPhase('inspecting');
        const dataUrl=await new Promise<string>((resolve,reject)=>{
          const next=new FileReader();reader.current=next;
          next.onload=()=>typeof next.result==='string'?resolve(next.result.replace(/^data:[^;]*;/,'data:application/pdf;')):reject(new Error(t('PDF 文件无法读取')));
          next.onerror=()=>reject(new Error(t('PDF 文件无法读取')));
          next.onabort=()=>reject(new Error(t('读取已取消')));
          next.onloadend=()=>{if(reader.current===next)reader.current=null;};
          next.readAsDataURL(file);
        });
        if(!valid())return;
        const result=await callbacks.current.api('figure_pdf_inspect',{name:file.name,dataUrl});
        if(!valid())return;
        const parsed=inspectResult(result);
        if(!parsed)throw new Error(t('无法读取 PDF 页面预览，请重试。'));
        setInspection(parsed);setSelected([parsed.pages[0].page]);setPhase('ready');busy.current=false;
      }catch(reason){
        if(!valid())return;
        setError(reason instanceof Error?reason.message:String(reason));setPhase('failed');busy.current=false;
      }
    })();
    return()=>{generation.current++;reader.current?.abort();reader.current=null;};
  },[file,attempt]);

  async function importPages(){
    if(busy.current||closed.current||!inspection||!selected.length||selected.length>20)return;
    busy.current=true;setPhase('importing');setError('');
    const stamp=generation.current,pages=inspection.pages.filter(page=>selected.includes(page.page)).map(page=>page.page);
    const valid=()=>!closed.current&&generation.current===stamp;
    try{
      const result=await callbacks.current.api('figure_pdf_import',{token:inspection.token,pages,mode});
      if(!valid())return;
      const items=importResult(result,pages);
      if(!items)throw new Error(t('PDF 导入结果不完整，请重试。'));
      busy.current=false;setPhase('ready');callbacks.current.onImported(items);
    }catch(reason){
      if(!valid())return;
      busy.current=false;setPhase('ready');setError(reason instanceof Error?reason.message:String(reason));
    }
  }

  function keyboard(event:KeyboardEvent<HTMLDivElement>){
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();return;}
    if(event.key!=='Tab')return;
    const focusable=Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]'));
    const first=focusable[0],last=focusable.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
  }

  return <div className="figure-pdf-overlay">
    <div className="figure-pdf-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={keyboard}>
      <header className="figure-pdf-header">
        <div><h2 id={titleId}>{t('导入 PDF')}</h2><p title={file.name}>{file.name}</p></div>
        <button ref={closeButton} type="button" className="figure-pdf-close" aria-label={t('关闭 PDF 导入')} onClick={close}><X size={18}/></button>
      </header>
      <div className="figure-pdf-body" aria-busy={isBusy}>
        {(phase==='preparing'||phase==='inspecting')&&<div className="figure-pdf-status" role="status">
          <span className="figure-pdf-spinner" aria-hidden="true"/>
          <strong>{phase==='preparing'?t('正在准备 PDF 导入…'):t('正在读取页面预览…')}</strong>
          <p>{phase==='preparing'?t('首次使用可能需要联网下载组件，请稍候。'):t('正在读取文件并生成页面缩略图。')}</p>
        </div>}
        {error&&<div className="figure-pdf-error" role="alert"><strong>{phase==='failed'?t('暂时无法打开 PDF'):t('导入未完成')}</strong><p>{error}</p></div>}
        {phase==='failed'&&<div className="figure-pdf-retry"><button type="button" className="btn btn-primary" onClick={()=>setAttempt(value=>value+1)}>{t('重试')}</button></div>}
        {inspection&&<>
          <fieldset className="figure-pdf-modes" disabled={isBusy}>
            <legend>{t('导入方式')}</legend>
            <label className={mode==='editable'?'selected':''}><input type="radio" name={modeId} value="editable" checked={mode==='editable'} onChange={()=>setMode('editable')}/><span><strong>{t('保留可编辑内容')}</strong><small>{t('尽可能保留文字与矢量内容；位图保留原始外观。')}</small></span></label>
            <label className={mode==='page'?'selected':''}><input type="radio" name={modeId} value="page" checked={mode==='page'} onChange={()=>setMode('page')}/><span><strong>{t('整页作为图片')}</strong><small>{t('保持整页排版，作为一张图片导入。')}</small></span></label>
          </fieldset>
          <div className="figure-pdf-page-toolbar"><div><strong>{t('选择页面')}</strong><span>{t('已选 {selected} / {total} 页',{selected:selected.length,total:inspection.pages.length})}</span></div><div><button type="button" className="btn btn-ghost" disabled={isBusy} onClick={()=>setSelected(inspection.pages.map(page=>page.page))}>{t('全选')}</button><button type="button" className="btn btn-ghost" disabled={isBusy||!selected.length} onClick={()=>setSelected([])}>{t('清空')}</button></div></div>
          {inspection.pages.length>20&&<p className={`figure-pdf-limit${selected.length>20?' exceeded':''}`} role={selected.length>20?'status':undefined}>{t('每次最多导入 20 页，请分批选择。')}</p>}
          <div className="figure-pdf-pages">
            {inspection.pages.map(page=><label key={page.page} className={`figure-pdf-page${selected.includes(page.page)?' selected':''}`}>
              <span className="figure-pdf-thumbnail"><img src={page.preview} alt={t('第 {page} 页预览',{page:page.page})}/></span>
              <span className="figure-pdf-page-caption"><input type="checkbox" checked={selected.includes(page.page)} disabled={isBusy} onChange={event=>setSelected(current=>event.target.checked?[...current,page.page]:current.filter(value=>value!==page.page))}/><span>{t('第 {page} 页',{page:page.page})}</span></span>
            </label>)}
          </div>
        </>}
      </div>
      <footer className="figure-pdf-footer">
        <div role="status">{phase==='importing'&&<><span className="figure-pdf-spinner" aria-hidden="true"/>{t('正在导入所选页面…')}</>}</div>
        <button type="button" className="btn btn-ghost" onClick={close}>{t('取消')}</button>
        <button type="button" className="btn btn-primary" disabled={isBusy||!inspection||!selected.length||selected.length>20} onClick={()=>void importPages()}>{t('导入 {count} 页',{count:selected.length})}</button>
      </footer>
    </div>
  </div>;
}
