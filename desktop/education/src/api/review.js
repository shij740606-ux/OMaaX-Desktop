// api/review.js —— 科研审稿端点封装（BFF 8090 → review_service 8100）
// 相对路径 /api/review/* → vite proxy（vite.config.js bffTarget）→ BFF 反代（须 REVIEW_ENABLED=1）。
//
// 错误契约适配：review_service 业务错误是 **HTTP 200 + {ok:false,error:{...}}**
// （VALIDATION/PARSE_FAILED/UPSTREAM_DOWN/SEED_UNAVAILABLE 均实测 200），
// 而 http.js 只对非 2xx 抛 ApiError —— 200 信封会正常 resolve。
// unwrap() 把两条错误路径（200 信封 / 非 2xx http.js 抛出）归一成 ApiError，
// 调用方只需 catch + reviewErrorMessage(err) 一个口径。
import { get, post, patch, del, ApiError } from './http.js';

const BASE = `/${location.pathname.split('/')[1]}/api/review`;

function unwrap(promise) {
  return promise.then((payload) => {
    if (payload && payload.ok === false && payload.error) {
      const err = new ApiError(payload.error.message || '服务错误', {
        code: payload.error.code || 'INTERNAL',
        status: 200,
        degraded: !!payload.error.degraded,
      });
      if (payload.error.detail) err.detail = payload.error.detail; // 编译失败的 log 尾等
      // P4：项目编译失败信封带 compile_id + log_url（补充 3，前端"查看完整 log"用）
      if (payload.compile_id) err.compileId = payload.compile_id;
      if (payload.log_url) err.logUrl = payload.log_url;
      throw err;
    }
    return payload;
  });
}

/** ① GET /api/review/journals?field=&order=
 *  → {ok, items:[{id,name,scope_desc,impact_factor,review_time_weeks,acceptance_rate,
 *      submission_deadline,official_url,last_updated}],
 *     meta:{count,last_updated,data_source_note}} */
export function getJournals(params = {}) {
  return unwrap(get(`${BASE}/journals`, params));
}

/** ①' GET /api/review/journals/directory?lang=&domain=&cas_partition=&q=&page=&size=&field=&order=
 *  → {ok, items[{id,lang,name,research_direction,cas_partition,cas_partition_note,
 *     cas_partition_year,jif,jif_year,jif_note,jif_check_status,composite_impact_factor,
 *     comprehensive_impact_factor,metric_year,metric_report_year,metric_caliber,
 *     annual_report_page,top_flag,publisher_series,journal_mark,article_type,issn,eissn,
 *     jcr_disciplines[],supplement_note,evidence_note,official_url,partition_source,
 *     jif_source,jif_source_url,partition_source_url,data_source_label,data_source_url}],
 *     meta:{total,page,size,lang,domain,cas_partition,q,domains[],
 *           data_source_note,data_imported}}
 *  1056 本目录（779 国际 + 277 中文）；只读分页，lang 强制单语（en/zh 永不混排）；
 *  cas_partition 仅 lang=en 过滤；field 排序白名单 name/jif/composite_impact_factor/
 *  comprehensive_impact_factor/research_direction/cas_partition；缺值 source 已归零。 */
export function getDirectory(params = {}) {
  return unwrap(get(`${BASE}/journals/directory`, params));
}

/** ⑦' GET /api/review/conferences?domain=&ccf=&upcoming_days=
 *  → {ok, items:[{id,name,full_name,domain_tags,application_tags,ccf_rank,
 *      deadline_abstract,deadline_full_paper,notification_date,conference_date,
 *      location,official_url,timezone_note,deadline_source,last_verified}],
 *     meta:{count,last_verified,data_source_note,query}}
 *  只读静态种子（全 placeholder）；排序/过滤由后端做，upcoming_days=0 表示不过滤。 */
export function getConferences(params = {}) {
  return unwrap(get(`${BASE}/conferences`, params));
}

/** ③ POST /api/review/presubmit（multipart PDF）→ {ok, report, meta}
 *  FormData 自动 multipart（http.js 不设 Content-Type，浏览器带 boundary）；
 *  opts 可传 {onUploadProgress, signal, timeout}（FormData+进度走 XHR 分支）。
 *  读超时沿用默认 600s（> BFF 360s），不先于上游自断。 */
export function presubmit(formData, opts = {}) {
  return unwrap(post(`${BASE}/presubmit`, formData, opts));
}

/** ② POST /api/review/recommend → {ok, tiers:[{tier,journals:[...]}], meta} */
export function recommend(payload, opts = {}) {
  return unwrap(post(`${BASE}/recommend`, payload, opts));
}

/** ⑤ POST /api/review/format-check（multipart PDF/Word）
 *  → {ok, checks:[{key,name,status,detail,suggestion}], summary:{pass,warn,fail},
 *     meta:{filename,file_type,pages,ruleset_source,rules_source,journal_*,
 *           refs_style_expected,refs_style_detected}}
 *  纯规则引擎，无 LLM，同步秒回（无需 onUploadProgress）。 */
export function formatCheck(formData, opts = {}) {
  return unwrap(post(`${BASE}/format-check`, formData, opts));
}

/** ⑤ format-check 专用错误文案：解档/校验类 PARSE_FAILED/VALIDATION 用后端原话
 * （与 presubmit 的"扫描件"提示是不同语境，不能复用 CODE_HINTS），其余走统一口径。 */
export function formatErrorMessage(err) {
  if (err instanceof ApiError && (err.code === 'PARSE_FAILED' || err.code === 'VALIDATION') && err.message) {
    return err.message;
  }
  return reviewErrorMessage(err);
}

/** 后端 error.code → 用户可读提示。
 *  null = 不覆盖、直接用后端 message（VALIDATION 入参问题后端原话更具体）。 */
const CODE_HINTS = {
  VALIDATION: null,
  PARSE_FAILED: '论文解析失败：可能是扫描件/图片型 PDF，请换文本型 PDF。',
  UPSTREAM_DOWN: '审稿模型暂时不可用，请稍后重试。',
  AUTH_EXPIRED: '登录已失效，请重新登录后重试。',
  MODEL_AUTH: '模型服务拒绝了临时凭据，请联系管理员。',
  MODEL_FORBIDDEN: '当前账号暂不能使用模型服务，请联系管理员。',
  MODEL_NOT_CONNECTED: '模型服务尚未连接，请重新打开教育科研页面。',
  MODEL_REQUEST_INVALID: '模型服务未接受审稿请求，请稍后重试或联系管理员。',
  MODEL_INVALID_RESPONSE: '模型返回的结果格式异常，请重试。',
  INPUT_TOO_LONG: '论文内容超出模型可处理范围，请缩小审稿范围后重试。',
  OUTPUT_TRUNCATED: '审稿输出达到模型上限，报告不完整。请缩小审稿范围后重试。',
  RATE_LIMIT: '模型服务请求过于频繁，请稍后重试。',
  SEED_UNAVAILABLE: '期刊库就绪中（种子数据缺失），请稍后再试。',
  TIMEOUT: '请求超时，请稍后重试。',
  NETWORK: '网络异常，请检查连接后重试。',
};

/** ApiError（或任意错误）→ 用户可读文案。degraded 由调用方读 err.degraded 走降级横幅样式。 */
export function reviewErrorMessage(err) {
  if (err instanceof ApiError) {
    const hint = CODE_HINTS[err.code];
    if (hint) return hint;
    if (err.message) return err.message; // VALIDATION 等：后端原话
  }
  return '服务异常，请稍后重试';
}

/* —— Rebuttal Phase 1（审稿回复助手，10 端点）—— */
const RB = `${BASE}/rebuttal`;

/** POST /rebuttal/jobs {text} → {ok, job, meta:{reviewers,comments}} 文本粘贴主入口 */
export function rebuttalCreateJob(text, opts = {}) {
  return unwrap(post(`${RB}/jobs`, { text }, opts));
}

/** POST /rebuttal/jobs/pdf（multipart）→ 同上；PDF 次要入口（批注版审稿 PDF） */
export function rebuttalCreateFromPdf(formData, opts = {}) {
  return unwrap(post(`${RB}/jobs/pdf`, formData, opts));
}

/** GET /rebuttal/jobs/{id} → {ok, job} 全量还原 */
export function rebuttalGetJob(id, opts = {}) {
  return unwrap(get(`${RB}/jobs/${id}`, {}, opts));
}

/** PATCH /rebuttal/jobs/{id}/comments/{cid}/response {parts} → {ok, job} */
export function rebuttalSaveResponse(id, cid, parts, opts = {}) {
  return unwrap(patch(`${RB}/jobs/${id}/comments/${cid}/response`, { parts }, opts));
}

/** POST /rebuttal/jobs/{id}/comments/{cid}/draft → {ok, job} 或校验失败
 *  {ok:false, error:{code:'VALIDATION_FAILED'}, job, meta:{validation}}（校验C前置）。
 *  不走 unwrap：校验失败信封里 job/meta 也是有效载荷，需原样返回给面板。 */
export function rebuttalDraft(id, cid, opts = {}) {
  return post(`${RB}/jobs/${id}/comments/${cid}/draft`, {}, opts).then((payload) => {
    if (payload && payload.ok === false && payload.error) {
      const err = new ApiError(payload.error.message || '草稿生成失败', {
        code: payload.error.code || 'INTERNAL',
        status: 200,
        degraded: !!payload.error.degraded,
      });
      err.payload = payload; // 信封全量（含 job + meta.validation）
      throw err;
    }
    return payload;
  });
}

/** PATCH /rebuttal/jobs/{id}/comments/{cid}/confirm {confirmed} → {ok, job, meta.human_ack} */
export function rebuttalConfirm(id, cid, confirmed, opts = {}) {
  return unwrap(patch(`${RB}/jobs/${id}/comments/${cid}/confirm`, { confirmed }, opts));
}

/** POST /rebuttal/jobs/{id}/letter → {ok, job}（job.letter_md 已拼装） */
export function rebuttalLetter(id, opts = {}) {
  return unwrap(post(`${RB}/jobs/${id}/letter`, {}, opts));
}

/** GET /rebuttal/jobs/{id}/letter.md —— 纯文本下载（human_ack 门槛，非信封）。
 *  返回 Promise<string>；400 时抛 ApiError（code 取自响应文本前缀）。 */
export async function rebuttalDownloadLetterMd(id) {
  const resp = await fetch(`${RB}/jobs/${id}/letter.md`);
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    const code = text.startsWith('HUMAN_ACK_REQUIRED') ? 'HUMAN_ACK_REQUIRED' : 'DOWNLOAD_FAILED';
    throw new ApiError(text || `下载失败（HTTP ${resp.status}）`, { code, status: resp.status });
  }
  return resp.text();
}

/** DELETE /rebuttal/jobs/{id} → {ok:true, meta:{deleted}} 手动清（无 TTL） */
export function rebuttalDeleteJob(id, opts = {}) {
  return unwrap(del(`${RB}/jobs/${id}`, opts));
}


/* ==================== P5 科研汇报助手（/api/review/ppt，SQLite job 模式） ==================== */
const PPT = `${BASE}/ppt`;

/** GET /ppt/styles → {ok, items:[{id,label,display_name,description,bg,accent}], meta:{count}}
 *  风格清单来自 review_service/data/ppt_styles/*.json（数量动态，面板副标题不再写死 8/14） */
export function pptStyles(opts = {}) {
  return unwrap(get(`${PPT}/styles`, {}, opts));
}

/** POST /ppt/jobs {text,scenario,minutes,style_id} → {ok, job, meta}
 *  文本粘贴主入口（map-reduce 大纲生成为长调用：BFF 360s / http.js 默认 600s） */
export function pptCreateJob(body, opts = {}) {
  return unwrap(post(`${PPT}/jobs`, body, opts));
}

/** POST /ppt/jobs/pdf（multipart：file + scenario/minutes/style_id form 字段）→ 同上 */
export function pptCreateFromPdf(formData, opts = {}) {
  return unwrap(post(`${PPT}/jobs/pdf`, formData, opts));
}

/** GET /ppt/jobs/{id} → {ok, job} 全量还原（raw_text 不回传，source.text_chars 代之） */
export function pptGetJob(id, opts = {}) {
  return unwrap(get(`${PPT}/jobs/${id}`, {}, opts));
}

/** PATCH /ppt/jobs/{id} {pages} → {ok, job}（结构清洗 + evidence 状态按原文重验） */
export function pptSavePages(id, pages, opts = {}) {
  return unwrap(patch(`${PPT}/jobs/${id}`, { pages }, opts));
}

/** POST /ppt/jobs/{id}/build → {ok, job, meta:{slides,bytes,previews}}（python-pptx 本地生成 + 缩略图） */
export function pptBuildJob(id, opts = {}) {
  return unwrap(post(`${PPT}/jobs/${id}/build`, {}, opts));
}

/** DELETE /ppt/jobs/{id} → {ok, meta:{deleted}} 手动清行 + 产物目录（无 TTL） */
export function pptDeleteJob(id, opts = {}) {
  return unwrap(del(`${PPT}/jobs/${id}`, opts));
}

/** pptx 下载直链（<a href>；FileResponse 附件下载，非信封，缺失时回信封 JSON） */
export function pptDownloadUrl(id) {
  return `${PPT}/jobs/${id}/pptx`;
}

/** 第 page 页缩略图直链（<img src>；PNG。缺失时回信封 JSON → img onerror 隐藏） */
export function pptPreviewUrl(id, page) {
  return `${PPT}/jobs/${id}/preview/${page}`;
}
