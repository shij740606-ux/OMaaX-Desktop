import {useEffect,useState} from 'react';

type ProtectedPath={path:string;resolved:string;directory:boolean};

export default function ProtectedPaths({server}:{server:string}) {
  const [rows,setRows]=useState<ProtectedPath[]>([]);
  const [path,setPath]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const call=async(action:string,body={})=>await window.biodsh.personalCompute(server,action,body) as ProtectedPath[];
  useEffect(()=>{let active=true;setBusy(true);void call('protected_paths').then(value=>{if(active)setRows(value);}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setBusy(false);});return()=>{active=false;};},[server]);
  const update=async(action:string,value:string)=>{
    setBusy(true);setError('');
    try{setRows(await call(action,{path:value}));if(action==='protected_path_add')setPath('');}
    catch(e){setError(String(e));}finally{setBusy(false);}
  };
  return <section className="card p-5 flex flex-col gap-3">
    <h2 className="t-headline">保护原始数据</h2>
    <p>添加需要保护的原始文件或文件夹，防止在文件操作中被修改、移动或删除。分析时仍可读取原始数据，并将副本和结果保存到其他位置。</p>
    <form className="flex flex-wrap gap-2" onSubmit={e=>{e.preventDefault();void update('protected_path_add',path.trim());}}>
      <label className="flex-1 min-w-60">原始数据路径<input className="field w-full" placeholder="例如 /home/user/data/raw" value={path} onChange={e=>setPath(e.target.value)} disabled={busy}/></label>
      <button className="btn btn-primary self-end" disabled={busy||!path.trim()}>添加保护</button>
    </form>
    {error&&<p role="alert" className="break-all" style={{color:'var(--red)'}}>{error}</p>}
    <details open><summary className="cursor-pointer">已保护路径（{rows.length}）</summary>
      {!rows.length&&<p className="t-caption mt-3">尚未登记保护路径。</p>}
      {rows.map(row=><div key={row.path} className="flex items-center gap-3 py-3 hairline-b"><div className="flex-1 min-w-0 break-all">{row.path}<div className="t-caption">{row.directory?'目录及全部内容':'文件'}{row.resolved!==row.path&&` · 实际路径：${row.resolved}`}</div></div><button className="btn btn-ghost" disabled={busy} onClick={()=>void update('protected_path_remove',row.path)}>解除保护</button></div>)}
    </details>
    <p className="t-caption">保护范围由你管理，文件操作会保留记录。自行运行的程序仍可能改动原始文件，请为重要数据另行备份。</p>
  </section>;
}
