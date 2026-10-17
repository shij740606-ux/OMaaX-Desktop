// api/http.js —— B1 归属，B2/B3 复用。fetch 封装 + 统一错误信封解析。
// 契约（API.md §1）：非 2xx 与降级响应返回 {"ok":false,"error":{"code","message","degraded"}}。
// 网络错误与信封错误一律归一为结构化 ApiError。
//
// 超时策略（BUG-R1-07 修复，API.md 全表读超时：qa chat 300s / qa search 180s /
// extraction upload 600s / kg overview 60s / 邻域 30s）：
//  - 默认读超时 600s = API.md 全表最大值。前端兜底永不先于 BFF 超时自断——
//    真实超时时由 BFF 返回 TIMEOUT 信封（504），前端展示契约文案「检索超时」，
//    而不是自己 fabricate 一个 NETWORK 错误。
//  - 调用方可传 opts.timeout（毫秒）做更紧的超时（如已知快路径）；
//    timeout: 0 表示不设超时。
//  - SSE 流式（raw:true）不走读超时——上游最长 240s，由调用方 AbortSignal 控制生命周期。
//
// 查询参数（BUG-R1-01 修复）：get(path, params, opts) 的 params 经 URLSearchParams
// 序列化拼到 query string（undefined/null 跳过），匹配 B3 kg.js / extraction.js 的调用形态：
//   get('/api/kg/entities', { q, limit })          → /api/kg/entities?q=…&limit=20
//   get('/api/kg/entities/{id}/neighborhood', { depth, limit }) → …/neighborhood?depth=1&limit=50

export class ApiError extends Error {
  constructor(message, { code = 'NETWORK', status = 0, degraded = false, cause } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;         // 信封 error.code，网络错误为 'NETWORK'
    this.status = status;     // HTTP 状态码，网络错误为 0
    this.degraded = degraded; // true = 上游不可用（UI 走降级提示而非错误弹窗）
    if (cause) this.cause = cause;
  }
}

/** 默认读超时：API.md 全表最大值（extraction upload 600s）——前端兜底不得先于 BFF 超时 */
const DEFAULT_TIMEOUT_MS = 600000;

function buildQuery(params) {
  if (!params) return '';
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    sp.set(k, String(v));
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : '';
}

function _parseXhrPayload(xhr) {
  const ct = xhr.getResponseHeader('content-type') || '';
  if (!ct.includes('application/json')) return null;
  try {
    return JSON.parse(xhr.responseText);
  } catch {
    return null;
  }
}

/** FormData + onUploadProgress → XHR（fetch 无上传进度事件）。
 *  契约与 fetch 分支完全一致：2xx 返回 JSON payload；非 2xx 走统一错误信封抛 ApiError。 */
function xhrUpload(url, method, body, { timeout, signal, onUploadProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, url);
    // 不设 Content-Type：浏览器自动带 multipart boundary（与 fetch 分支一致）
    if (body instanceof FormData) {
      if (body.has('code_zip')) {
        const f = body.get('code_zip');
        if (f instanceof Blob && (f.size || 0) > 0) xhr._paSizeHint = f.size;
      } else if (body.has('code_files')) {
        // 目录形态:全部文件大小求和(多值字段)
        let sum = 0;
        for (const f of body.getAll('code_files')) {
          if (f instanceof Blob) sum += f.size || 0;
        }
        if (sum > 0) xhr._paSizeHint = sum;
      } else if (body.has('file')) {
        const f = body.get('file');
        if (f instanceof Blob && (f.size || 0) > 0) xhr._paSizeHint = f.size;
      }
    }
    xhr.responseType = 'text';
    let timedOut = false;
    let abortedBySignal = false;
    const t = timeout === 0 ? null : setTimeout(() => {
      timedOut = true;
      xhr.abort();
    }, timeout);
    const onAbort = () => {
      if (signal && signal.aborted) {
        abortedBySignal = true;
        xhr.abort();
      }
    };
    if (signal) {
      if (signal.aborted) abortedBySignal = true;
      else signal.addEventListener('abort', onAbort);
    }
    if (onUploadProgress && xhr.upload) {
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const total = e.total || xhr._paSizeHint || 0;
        onUploadProgress({ loaded: e.loaded, total, pct: total ? Math.min(1, e.loaded / total) : 0 });
      };
    }
    xhr.onload = () => {
      if (t) clearTimeout(t);
      if (signal) signal.removeEventListener('abort', onAbort);
      const payload = _parseXhrPayload(xhr);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      if (payload && payload.ok === false && payload.error) {
        reject(new ApiError(payload.error.message || '服务错误', {
          code: payload.error.code || 'INTERNAL',
          status: xhr.status,
          degraded: !!payload.error.degraded,
        }));
        return;
      }
      reject(new ApiError(`请求失败（HTTP ${xhr.status}）`, { code: 'INTERNAL', status: xhr.status }));
    };
    xhr.onerror = () => {
      if (t) clearTimeout(t);
      if (signal) signal.removeEventListener('abort', onAbort);
      if (abortedBySignal) {
        reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
        return;
      }
      reject(new ApiError('网络异常，请检查连接', {
        code: 'NETWORK',
        degraded: false,
        cause: timedOut ? new Error('timeout') : undefined,
      }));
    };
    xhr.onabort = () => {
      if (t) clearTimeout(t);
      if (signal) signal.removeEventListener('abort', onAbort);
      reject(abortedBySignal ? Object.assign(new Error('Aborted'), {name:'AbortError'}) : new ApiError('请求超时，请重试', {code:'TIMEOUT'}));
    };
    xhr.ontimeout = () => { timedOut=true; xhr.abort(); };
    if (abortedBySignal) { xhr.onabort(); return; }
    xhr.send(body);
  });
}

async function request(path, { method = 'GET', body, headers = {}, timeout = DEFAULT_TIMEOUT_MS, signal, raw = false, params, onUploadProgress } = {}) {
  // GET 的 params 序列化为 query string（BUG-R1-01）
  if(method === 'DELETE') {
    const requestId=crypto.randomUUID();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{window.removeEventListener('message',reply);reject(new ApiError('删除确认已超时'));},120000);
      function reply(event){if(event.source!==parent||event.data?.type!=='omaax-delete-result'||event.data.requestId!==requestId)return;clearTimeout(timer);window.removeEventListener('message',reply);event.data.error?reject(new ApiError(event.data.error)):resolve(event.data.value);}
      window.addEventListener('message',reply);parent.postMessage({type:'omaax-delete',requestId,path:path.slice(path.indexOf('/api/review/'))},'*');
    });
  }
  let url = path;
  if (method === 'GET' && params) url += buildQuery(params);

  // 上传进度（2026-09-04）：FormData + onUploadProgress → XHR 分支（fetch 无 upload 事件）
  if (onUploadProgress && typeof FormData !== 'undefined' && body instanceof FormData) {
    return xhrUpload(url, method, body, { timeout, signal, onUploadProgress });
  }

  const opts = { method, headers: { ...headers }, signal };
  if (body !== undefined && body !== null) {
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      // multipart（FR-EXTRACT-004 上传）：不设 Content-Type（浏览器自动带 boundary）
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }

  let res;
  const ctrl = new AbortController();let t;
  const onAbort=()=>ctrl.abort();
  if(signal){if(signal.aborted)ctrl.abort();else signal.addEventListener('abort',onAbort,{once:true});}
  try {
    // raw（SSE 流式）不设读超时；timeout:0 显式关闭
    t = raw || timeout === 0 ? null : setTimeout(() => ctrl.abort(), timeout);
    res = await fetch(url, {
      ...opts,
      signal: ctrl.signal,
    });
    if (t) clearTimeout(t);
  } catch (e) {
    // AbortError 也可能是调用方主动中断流式请求——继续抛原始错误，由调用方处理
    if (e.name === 'AbortError' && signal && signal.aborted) throw e;
    throw new ApiError('网络异常，请检查连接', {
      code: 'NETWORK',
      degraded: false,
      cause: e,
    });
  } finally {if(t)clearTimeout(t);if(signal)signal.removeEventListener('abort',onAbort);}

  // raw:true —— 返回原始 Response 供 SSE 流式消费（B2 consumeSseStream）；
  // 非 2xx 仍按统一错误信封抛 ApiError（HTTP 错误在流开始前就被拒绝）
  if (raw) {
    if (!res.ok) {
      let payload = null;
      if ((res.headers.get('content-type') || '').includes('application/json')) {
        try {
          payload = await res.json();
        } catch {
          payload = null;
        }
      }
      if (payload && payload.ok === false && payload.error) {
        throw new ApiError(payload.error.message || '服务错误', {
          code: payload.error.code || 'INTERNAL',
          status: res.status,
          degraded: !!payload.error.degraded,
        });
      }
      throw new ApiError(`请求失败（HTTP ${res.status}）`, { code: 'INTERNAL', status: res.status });
    }
    return res;
  }

  let payload = null;
  const isJson = (res.headers.get('content-type') || '').includes('application/json');
  if (isJson) {
    try {
      payload = await res.json();
    } catch {
      payload = null;
    }
  }

  if (!res.ok) {
    // 统一错误信封（API.md §1）
    if (payload && payload.ok === false && payload.error) {
      throw new ApiError(payload.error.message || '服务错误', {
        code: payload.error.code || 'INTERNAL',
        status: res.status,
        degraded: !!payload.error.degraded,
      });
    }
    throw new ApiError(`请求失败（HTTP ${res.status}）`, { code: 'INTERNAL', status: res.status });
  }

  return payload;
}

export const api = {
  /** params 序列化为 query string（BUG-R1-01）；opts 可传 { timeout, signal, raw } */
  get: (path, params = {}, opts = {}) => request(path, { ...opts, method: 'GET', params }),
  /** body 为普通对象 → JSON；FormData → multipart。
   *  opts 可传 { timeout, signal, raw, onUploadProgress }（onUploadProgress 仅 FormData 生效） */
  post: (path, body, opts = {}) => request(path, { ...opts, method: 'POST', body }),
  patch: (path, body, opts = {}) => request(path, { ...opts, method: 'PATCH', body }),
  del: (path, opts = {}) => request(path, { ...opts, method: 'DELETE' }),
  /** 原始 fetch（无封装），供 SSE 流式调用（B2 用 ReadableStream 消费 POST /api/qa/chat/stream） */
  rawFetch: fetch,
};

// 命名导出（B2/B3 api/* 层按此 import；与 api 对象同函数，两种取法等价）
export const get = api.get;
export const post = api.post;
export const patch = api.patch;
export const del = api.del;

// 别名：B2 代码注释中的 HttpError 即 ApiError（统一错误信封，契约 API.md §1）
export const HttpError = ApiError;
