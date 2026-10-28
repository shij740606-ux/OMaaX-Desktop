// Keep large drafts out of localStorage; operations on one account key finish in order.
const DATABASE = 'omaax-figure-drafts';
const STORE = 'drafts';
type Draft = { format:'omaax-figure-draft'; version:1; modified:number; json:string|null };
let database:Promise<IDBDatabase|null>|undefined;
const pending = new Map<string,Promise<unknown>>();
let lastModified = 0;

function failure(error:unknown):Error {
  const name = error && typeof error==='object' && 'name' in error ? String(error.name) : '';
  return new Error(name==='QuotaExceededError'
    ? '拼图草稿存储空间不足，请保存到项目后重试。'
    : '拼图草稿读写失败，请保存到项目并检查浏览器存储权限。');
}

function ordered<T>(key:string,work:()=>Promise<T>):Promise<T> {
  if(typeof key!=='string'||!key||key.length>1000)return Promise.reject(new Error('拼图草稿标识无效。'));
  const result=(pending.get(key)??Promise.resolve()).catch(()=>{}).then(work);
  pending.set(key,result);
  const clear=()=>{if(pending.get(key)===result)pending.delete(key);};
  result.then(clear,clear);
  return result;
}

function openDatabase():Promise<IDBDatabase|null> {
  if(database)return database;
  const attempt=new Promise<IDBDatabase|null>((resolve,reject)=>{
    if(typeof indexedDB==='undefined'){resolve(null);return;}
    let request:IDBOpenDBRequest,settled=false;
    const unavailable=(error:DOMException|null)=>['SecurityError','InvalidStateError','NotSupportedError'].includes(error?.name||'');
    try {request=indexedDB.open(DATABASE,1);} catch(error){if(unavailable(error as DOMException))resolve(null);else reject(failure(error));return;}
    const timer=setTimeout(()=>{settled=true;reject(new Error('拼图草稿存储暂时被占用，请关闭其他窗口后重试。'));},5000);
    request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains(STORE))request.result.createObjectStore(STORE);};
    request.onerror=()=>{clearTimeout(timer);if(settled)return;settled=true;if(unavailable(request.error))resolve(null);else reject(failure(request.error));};
    request.onsuccess=()=>{
      clearTimeout(timer);
      if(settled){request.result.close();return;}
      settled=true;const db=request.result;
      db.onversionchange=()=>{db.close();database=undefined;};
      db.onclose=()=>{database=undefined;};
      resolve(db);
    };
  });
  database=attempt.catch(error=>{database=undefined;throw error;});
  return database;
}

function transaction<T>(db:IDBDatabase,mode:IDBTransactionMode,operation:(store:IDBObjectStore)=>IDBRequest<T>):Promise<T> {
  return new Promise((resolve,reject)=>{
    try {
      const tx=db.transaction(STORE,mode),request=operation(tx.objectStore(STORE));
      let reason:unknown;
      request.onerror=()=>{reason=request.error;};
      tx.onerror=()=>{reason=tx.error||reason;};
      tx.onabort=()=>reject(failure(tx.error||reason));
      tx.oncomplete=()=>resolve(request.result);
    } catch(error){reject(failure(error));}
  });
}

function decode(value:unknown,legacy=false):Draft|null {
  if(value===null||value===undefined)return null;
  let record=value;
  if(legacy&&typeof value==='string'){
    try {record=JSON.parse(value);} catch {return {format:'omaax-figure-draft',version:1,modified:0,json:value};}
    if(!record||typeof record!=='object'||(record as Draft).format!=='omaax-figure-draft')return {format:'omaax-figure-draft',version:1,modified:0,json:value};
  }
  const draft=record as Draft;
  if(!draft||draft.format!=='omaax-figure-draft'||draft.version!==1||!Number.isFinite(draft.modified)||draft.modified<0||(typeof draft.json!=='string'&&draft.json!==null))throw new Error('拼图草稿存储格式无效，请打开保存的项目文件。');
  return draft;
}

function local(key:string):Draft|null {
  try {return decode(localStorage.getItem(key),true);}catch(error){if(error instanceof Error&&error.message.startsWith('拼图草稿存储格式'))throw error;throw failure(error);}
}
function removeLocal(key:string){try{localStorage.removeItem(key);}catch(error){throw failure(error);}}
function putLocal(key:string,value:Draft){try{localStorage.setItem(key,JSON.stringify(value));}catch(error){throw failure(error);}}

async function stored(db:IDBDatabase|null,key:string):Promise<Draft|null> {
  return db?decode(await transaction(db,'readonly',store=>store.get(key))):null;
}
function latest(a:Draft|null,b:Draft|null):Draft|null{return !a||b&&b.modified>=a.modified?b:a;}

export function loadFigureDraft(key:string):Promise<string|null> {
  return ordered(key,async()=>{
    const db=await openDatabase(),persisted=await stored(db,key),legacy=local(key),draft=latest(persisted,legacy);
    if(!draft)return null;
    if(db&&draft!==persisted)await transaction(db,'readwrite',store=>store.put(draft,key));
    // Remove the old copy only after the IndexedDB transaction has committed.
    if(db&&legacy)removeLocal(key);
    return draft.json;
  });
}

function write(key:string,json:string|null):Promise<void> {
  return ordered(key,async()=>{
    const db=await openDatabase(),previous=latest(await stored(db,key),local(key));
    const modified=Math.max(Date.now(),lastModified+1,(previous?.modified||0)+1);lastModified=modified;
    const draft:Draft={format:'omaax-figure-draft',version:1,modified,json};
    if(db){await transaction(db,'readwrite',store=>store.put(draft,key));removeLocal(key);}
    else putLocal(key,draft);
  });
}

export function saveFigureDraft(key:string,json:string):Promise<void> {
  if(typeof json!=='string')return Promise.reject(new Error('拼图草稿内容无效。'));
  return write(key,json);
}

export function deleteFigureDraft(key:string):Promise<void> {
  // A dated tombstone prevents an older IndexedDB copy resurfacing after fallback deletion.
  return write(key,null);
}
