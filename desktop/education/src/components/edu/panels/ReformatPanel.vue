<!-- ReformatPanel.vue —— ⑤' 论文自动重排版 P1（.tex 源码级规则变换，不编译）。
     链路：本地 .tex（≤2MB，仅 .tex——docx/pdf 只走 ⑤ 审核不重排）+ 目标期刊 →
     edu.submitReformat → POST /api/review/reformat（纯规则，同步秒回）
     → {ok, job_id, modified_tex, changes[{line,original,modified,reason,
        rule_key,action}], warnings[结构级], meta{journal_*,rules*,
        filename,original_sha256,engine,generated_at}}
     目标下拉分组：通用规则（首项默认 default，APA+通用规范）/ 精选期刊 /
     期刊目录（选 __dir_* 进搜索模式，getDirectory 300ms 防抖本地态，
     选定后 target = 目录 id，后端经 journal_directory 回链）。
      结果三 Tab：修改后源码（Blob 下载）/ Diff 报告（按 rule_key 着色、
      按 action 计数：modified=已修改、warning=建议·橙色·不计入）/
      编译 PDF（P3b）：POST /compile {job_id}（tectonic 单槽队列，约 10-30 秒，
      首次拉 bundle 1-3 分钟——store.compileFirstHint 黄条提示可切面板后台跑）；
      成功→blob URL 内嵌 iframe 预览 + 下载 + 耗时/重编；失败→错误摘要 +
      log 尾 200 行折叠 + 完整 log 下载；QUEUE_FULL→固定文案。
      P4 项目 zip（上传区模式切换，与单文件互斥）：POST /compile/inspect（≤50MB，
      1h TTL，run 前不占编译队列）→ 主文件/统计/跳过文件摘要 → POST /compile/run
      （原子消费 inspect；信封与 P3b 同构复用 compile* 字段；失败也带 log_url）。
      结果跨菜单保留（store reformatResult/compile*）；样式 rf- 前缀，零新增依赖。 -->
<script setup>
import {et, etList, educationLocale} from '../../../i18n.js';
import { computed, onMounted, ref, watch } from 'vue';
import { getDirectory, reviewErrorMessage } from '../../../api/review.js';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

const MAX_SIZE = 2 * 1024 * 1024; // 与后端 REVIEW_MAX_TEX_BYTES 一致
const RULE_LABEL = { font_pt: '字号', margin: '页边距', line_spacing: '行距', bib_style: '参考文献' };
const RULE_MOD = { font_pt: 'k1', margin: 'k2', line_spacing: 'k3', bib_style: 'k4' };
const SOURCE_LABEL = {
  default: '通用规则', official: '期刊专属规则', fetched: '官网抓取规则', cache: '缓存规则',
};

const fileInput = ref(null);
const file = ref(edu.reformatFile || null); // File 存 store（换期刊重排不丢；× 移除才清空）
const fileError = ref('');
const dragging = ref(false);
const tab = ref('source'); // 'source' | 'diff' | 'pdf'

/* —— 目标期刊（drafts.reformatTarget 持久：'default' | 精选 id | '__dir_en__'
      过渡态 | 'dir:<id>' 目录已选）—— */
const targetChoice = ref(edu.drafts.reformatTarget || 'default');

const dirMode = computed(() => {
  const t = targetChoice.value;
  if (t === '__dir_en__') return 'en';
  if (t === '__dir_zh__') return 'zh';
  return '';
});
const actualTarget = computed(() => {
  const t = String(targetChoice.value || '');
  if (t.startsWith('dir:')) return t.slice(4);
  if (t.startsWith('__dir')) return ''; // 搜索中未选定 → 不可提交
  return t;
});
const targetReady = computed(() => !!actualTarget.value);
const dirPick = computed(() => {
  const t = targetChoice.value;
  const pick = edu.drafts.reformatDirPick;
  if (t.startsWith('dir:') && pick && `dir:${pick.id}` === t) return pick;
  return null;
});

/* —— 目录搜索（本地态，不占用 store.directory ——那是 ① 目录页的数据）—— */
const dirQuery = ref('');
const dirResults = ref([]);
const dirLoading = ref(false);
const dirError = ref('');
let dirTimer = null;
let dirSeq = 0;

watch(targetChoice, (v) => {
  edu.drafts.reformatTarget = v;
  if (!v.startsWith('__dir')) {
    dirQuery.value = '';
    dirResults.value = [];
    dirError.value = '';
  }
});

async function runDirSearch() {
  const q = dirQuery.value.trim();
  if (!dirMode.value || !q) {
    dirResults.value = [];
    return;
  }
  const seq = ++dirSeq;
  dirLoading.value = true;
  dirError.value = '';
  try {
    const payload = await getDirectory({ q, lang: dirMode.value, size: 20 });
    if (seq !== dirSeq) return;
    dirResults.value = (payload && payload.items) || [];
  } catch (err) {
    if (seq !== dirSeq) return;
    dirError.value = reviewErrorMessage(err);
  } finally {
    if (seq === dirSeq) dirLoading.value = false;
  }
}
watch(dirQuery, () => {
  clearTimeout(dirTimer);
  dirTimer = setTimeout(runDirSearch, 300);
});

function pickDir(item) {
  targetChoice.value = `dir:${item.id}`;
  edu.drafts.reformatDirPick = { id: item.id, name: item.name };
  dirQuery.value = '';
  dirResults.value = [];
}

/* —— 结果视图 —— */
const hasResult = computed(() => !!(edu.reformatResult && edu.reformatResult.ok));
const loading = computed(() => edu.reformatStatus === 'loading');
const errored = computed(() => edu.reformatStatus === 'error');
const result = computed(() => edu.reformatResult || null);
const meta = computed(() => result.value?.meta || null);
const changes = computed(() => result.value?.changes || []);
const structWarnings = computed(() => result.value?.warnings || []);
const modifiedCount = computed(() => changes.value.filter((c) => c.action === 'modified').length);
const suggestCount = computed(() => changes.value.filter((c) => c.action === 'warning').length);
const sourceLabel = computed(() => SOURCE_LABEL[meta.value?.rules_source] || meta.value?.rules_source || '—');
// 目标期刊规则摘要：只显示有值字段（null 不显示）；全 null → 明确说明无强制约束。
// 目的：diff=0 时（如 Frontiers / GB↔BMC 互切）也能看到"该刊要求什么"，消除"没生效"错觉。
const _REFS_LABEL = { vancouver: 'Vancouver 引用', apa: 'APA 引用', author_year: '作者-年份引用' };
const rulesSummary = computed(() => {
  const r = meta.value?.rules;
  if (!r || typeof r !== 'object') return null;
  const parts = [];
  const ls = r.min_line_spacing;
  if (typeof ls === 'number' && ls > 0) {
    if (ls <= 1.05) parts.push('单倍行距');
    else if (Math.abs(ls - 1.5) < 0.01) parts.push('1.5 倍行距');
    else if (ls >= 1.95) parts.push('双倍行距');
    else parts.push(`行距 ≥${ls} 倍`);
  }
  if (r.refs_style && _REFS_LABEL[r.refs_style]) parts.push(_REFS_LABEL[r.refs_style]);
  if (typeof r.min_font_pt === 'number') parts.push(`字号 ≥${r.min_font_pt}pt`);
  if (typeof r.min_margin_mm === 'number') parts.push(`边距 ≥${r.min_margin_mm}mm`);
  if (!parts.length) return '该刊官网未规定明确格式要求，无强制约束';
  return parts.join(' · ');
});
// 与 diff 的关系：有修改 → "已修改 N 处"；0 修改 → "样本已满足该刊要求，无需修改"
const rulesStatusText = computed(() => {
  if (modifiedCount.value > 0) return `已修改 ${modifiedCount.value} 处`;
  return '样本已满足该刊要求，无需修改';
});
// ISO 时间戳 → "10-12 15:25"（内部字段不裸露；helper 规则见 UI 注释）
const generatedShort = computed(() => {
  const s = meta.value?.generated_at;
  if (!s) return '';
  const str = String(s);
  return str.length >= 16 ? str.slice(5, 16).replace('T', ' ') : '';
});
function copyJobId() {
  const id = result.value?.job_id;
  if (id && navigator.clipboard) navigator.clipboard.writeText(String(id)).catch(() => {});
}

/* —— P3b 编译 PDF（状态全在 store → 切菜单/切 Tab 编译继续跑）—— */
const compiling = computed(() => edu.compileStatus === 'loading');
const compileReady = computed(() => edu.compileStatus === 'ready' && !!edu.compileResult);
const compileErrored = computed(() => edu.compileStatus === 'error');
const compileMeta = computed(() => edu.compileResult?.meta || null);
const pdfSrc = computed(() => edu.compilePdfUrl || edu.compileResult?.pdf_url || '');
const pdfSizeText = computed(() => {
  const b = compileMeta.value?.pdf_bytes;
  if (b == null) return '';
  return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.ceil(b / 1024)} KB`;
});
const compileLog = computed(() => edu.compileLogTail || '');
const logCollapsed = ref(true); // log 折叠区（默认收起）
const firstHintDismissed = ref(false);
const showFirstHint = computed(() =>
  edu.compileFirstHint && compiling.value && !firstHintDismissed.value,
);

function startCompile() {
  const jid = result.value?.job_id;
  if (!jid || compiling.value) return;
  logCollapsed.value = true;
  edu.submitCompile(jid); // 不 await：按钮/进度由 store 状态驱动，切面板不中断
}
function downloadPdf() {
  const href = pdfSrc.value;
  if (!href) return;
  const a = document.createElement('a');
  a.href = href;
  a.download = `${result.value?.job_id || 'paper'}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function downloadFullLog() {
  // 成功：信封带 log_url；失败信封不带——job_id 即 anchor_id，log 同目录可拼路径
  const jid = result.value?.job_id;
  const href = edu.compileResult?.log_url || (jid ? `/${location.pathname.split("/")[1]}/api/review/compile/${jid}/log` : '');
  if (!href) return;
  const a = document.createElement('a');
  a.href = href;
  a.download = `${jid || 'compile'}.log`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* —— P4 项目 zip（两段式；与单文件模式互斥，切换即清对侧状态）—— */
const uploadMode = ref('tex'); // 'tex' | 'zip'
const zipInput = ref(null);
const zipInspecting = computed(() => edu.zipInspectStatus === 'loading');
const zipInspected = computed(() => edu.zipInspectStatus === 'ready' && !!edu.zipInspect);
const zipError = computed(() => edu.zipInspectError || '');
const MAX_ZIP_SIZE = 50 * 1024 * 1024; // 与后端 REVIEW_MAX_ZIP_BYTES 一致

function switchMode(mode) {
  if (uploadMode.value === mode) return;
  uploadMode.value = mode;
  fileError.value = '';
  if (mode === 'zip') {
    edu.clearReformat(); // 互斥：zip 编译结果与单文件编译共用 compile* 字段
  } else {
    edu.clearZip();
  }
}

function acceptZip(f) {
  if (!f) return;
  if (!String(f.name || '').toLowerCase().endsWith('.zip')) {
    edu.zipInspectStatus = 'error';
    edu.zipInspectError = '仅支持 .zip 项目包';
    return;
  }
  if (!f.size) {
    edu.zipInspectStatus = 'error';
    edu.zipInspectError = '上传文件为空';
    return;
  }
  if (f.size > MAX_ZIP_SIZE) {
    edu.zipInspectStatus = 'error';
    edu.zipInspectError = 'zip 超过 50MB 上限';
    return;
  }
  edu.zipFile = f;
  edu.zipInspectStatus = 'idle'; // 换文件 → 旧 inspect 失效
  edu.zipInspect = null;
  edu.zipInspectError = null;
  edu.clearCompile();
}
function onZipPick(e) {
  acceptZip(e.target.files?.[0]);
  e.target.value = '';
}
function onZipDrop(e) {
  dragging.value = false;
  acceptZip(e.dataTransfer?.files?.[0]);
}
function removeZip() {
  edu.clearZip(); // 唯一清空入口："×" 移除
}
async function startZipInspect() {
  if (!edu.zipFile || zipInspecting.value) return;
  await edu.submitZipInspect(); // 不 await 也可，但保持与其他面板一致写法
}
function startZipCompile() {
  logCollapsed.value = true;
  edu.submitZipCompile(); // 不 await：状态由 store 驱动，切面板不中断
}
function downloadZipPdf() {
  const href = pdfSrc.value;
  if (!href) return;
  const cid = edu.compileResult?.compile_id || 'project';
  const a = document.createElement('a');
  a.href = href;
  a.download = `${cid}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function downloadZipLog() {
  const cid = edu.compileResult?.compile_id;
  const href = edu.compileResult?.log_url || (cid ? `/${location.pathname.split("/")[1]}/api/review/compile/zip/${cid}/log` : '');
  if (!href) return;
  const a = document.createElement('a');
  a.href = href;
  a.download = `${cid || 'compile'}.log`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

onMounted(() => {
  if (edu.journalsStatus === 'idle') edu.fetchJournals(); // 精选期刊下拉数据源
  // 恢复的 target 是目录刊但无选中记录（历史草稿）→ 回落通用规则
  if (targetChoice.value.startsWith('dir:') && !dirPick.value) {
    targetChoice.value = 'default';
  }
});

/* —— 文件（仅 .tex，拍板 5）—— */
function acceptFile(f) {
  fileError.value = '';
  if (!f) return;
  if (!String(f.name || '').toLowerCase().endsWith('.tex')) {
    fileError.value = '仅支持 LaTeX 源码（.tex）；docx/pdf 请使用「论文排版与规范」审核';
    return;
  }
  if (!f.size) {
    fileError.value = '上传文件为空';
    return;
  }
  if (f.size > MAX_SIZE) {
    fileError.value = '源码超过 2MB 上限';
    return;
  }
  file.value = f;
  edu.reformatFile = f; // 进 store：换期刊/切菜单重排不丢
}
function onPick(e) {
  acceptFile(e.target.files?.[0]);
  e.target.value = '';
}
function onDrop(e) {
  dragging.value = false;
  acceptFile(e.dataTransfer?.files?.[0]);
}
function removeFile() {
  file.value = null;
  edu.reformatFile = null; // 唯一清空入口："×" 移除
}

async function submit() {
  if (!file.value || loading.value || !targetReady.value) return;
  const fd = new FormData();
  fd.append('file', file.value);
  fd.append('target_journal', actualTarget.value);
  tab.value = 'source';
  await edu.submitReformat(fd);
}
function reset() {
  edu.clearReformat();
  // 不清 file——换期刊重排时保留已上传文件（仅"×"移除才清）
  fileError.value = '';
  tab.value = 'source';
}
function download() {
  const tex = result.value?.modified_tex;
  if (tex == null) return;
  const base = String(meta.value?.filename || 'paper').replace(/\.tex$/i, '');
  const url = URL.createObjectURL(new Blob([tex], { type: 'application/x-tex;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${base}.reformat.tex`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <section class="rf">
    <header class="rf-head">
      <h1 class="rf-title">{{ et("论文自动重排版") }}</h1>
      <span class="rf-sub">{{ et("LaTeX (.tex) · 字号 · 页边距 · 行距 · 参考文献 · 纯规则不编译") }}</span>
    </header>

    <!-- ========== ① 有结果 → 结果区 ========== -->
    <template v-if="hasResult">
      <div class="rf-meta">
        📄 {{ meta?.filename || '—' }}{{ et(" · 目标：") }}{{ meta?.journal_name || '—' }}（{{ et(sourceLabel) }}）
        <template v-if="generatedShort"> · {{ generatedShort }}</template>
        <!-- 内部 job id 默认不展示；报错/反馈时可点复制 -->
        <button
          v-if="result.job_id" class="rf-copy-id" type="button"
          :title="et(&quot;复制任务 ID（报错反馈时使用）&quot;)" @click="copyJobId"
        >{{ et(" 复制任务 ID ") }}</button>
      </div>
      <!-- 目标期刊规则摘要：diff=0 时也能看到"该刊要求什么"（次级文字，不抢 diff 主视觉） -->
      <p v-if="rulesSummary" class="rf-rules" data-testid="rf-rules-summary">{{ et(" 📋 该刊规则：") }}{{ etList(rulesSummary) }}
        <span class="rf-rules-status">（{{ et(rulesStatusText) }}）</span>
      </p>
      <p v-if="meta?.rules_note" class="rf-note" role="note">{{ et(meta.rules_note) }}</p>

      <!-- 汇总：已修改 / 建议（橙，不计入修改）/ 结构提示 -->
      <div class="rf-summary">
        <span class="rf-count mod">{{ modifiedCount }}{{ et(" 处已修改") }}</span>
        <span class="rf-count sug">{{ suggestCount }}{{ et(" 条建议") }}</span>
        <span class="rf-count warn" v-if="structWarnings.length">{{ structWarnings.length }}{{ et(" 条结构提示") }}</span>
        <button class="rf-again" type="button" @click="reset">{{ et("重新排版") }}</button>
        <button class="rf-dl" type="button" @click="download">{{ et("下载修改后源码") }}</button>
      </div>

      <article v-for="(w, i) in structWarnings" :key="'w' + i" class="rf-struct">
        ⚠ {{ et(w.message) }}
      </article>

      <!-- Tab：修改后源码 | Diff 报告 | 编译 PDF（P3a） -->
      <div class="rf-tabs" role="tablist">
        <button class="rf-tab" :class="{ active: tab === 'source' }" type="button"
          role="tab" @click="tab = 'source'">{{ et("修改后源码") }}</button>
        <button class="rf-tab" :class="{ active: tab === 'diff' }" type="button"
          role="tab" @click="tab = 'diff'">{{ et("Diff 报告") }}</button>
        <button class="rf-tab" :class="{ active: tab === 'pdf' }" type="button"
          role="tab" @click="tab = 'pdf'">{{ et("编译 PDF") }}</button>
      </div>

      <pre v-if="tab === 'source'" class="rf-src">{{ result.modified_tex }}</pre>

      <div v-else-if="tab === 'diff'" class="rf-diff">
        <p v-if="!changes.length" class="rf-empty">{{ et(" ✅ 未发现违反目标规则的可修改项——源码未被触碰（规则：") }}{{ et(sourceLabel) }}）。
        </p>
        <article
          v-for="(c, i) in changes"
          :key="i"
          class="rf-row"
          :class="c.action"
        >
          <header class="rf-row-head">
            <span class="rf-line">L{{ c.line }}</span>
            <span class="rf-rule" :class="RULE_MOD[c.rule_key]">{{ et(RULE_LABEL[c.rule_key] || c.rule_key) }}</span>
            <span class="rf-act" :class="c.action">
              {{ et(c.action === 'modified' ? '已修改' : '建议') }}
            </span>
          </header>
          <div class="rf-pair">
            <pre class="rf-old">{{ c.original }}</pre>
            <pre v-if="c.action === 'modified'" class="rf-new">{{ c.modified }}</pre>
            <pre v-else class="rf-skip">{{ et("（未修改 — 缺少可安全修改的插入点）") }}</pre>
          </div>
          <p class="rf-reason" :class="{ sug: c.action === 'warning' }">{{ et(c.reason) }}</p>
        </article>
      </div>

      <!-- ========== 编译 PDF（P3b） ========== -->
      <div v-else class="rf-compile">
        <!-- 无 job_id（未完成重排版）→ 引导 -->
        <div v-if="!result?.job_id" class="rf-wip" role="note" data-testid="rf-compile-guide">
          <p class="rf-wip-main">{{ et("请先完成重排版再编译。") }}</p>
          <p class="rf-wip-sub">{{ et("编译基于重排版后的 .tex 源码，重排版成功后此页会出现「编译 PDF」按钮。") }}</p>
        </div>

        <template v-else>
          <!-- 首次编译慢提示（loading 期间显示，可手动关闭） -->
          <div v-if="showFirstHint" class="rf-first-hint" role="note" data-testid="rf-first-hint">
            <span>{{ et(" 首次编译需要 1-3 分钟（下载 TeX 环境），后续编译秒级。 可以切到其他面板，编译会在后台继续。 ") }}</span>
            <button class="rf-hint-x" type="button" :aria-label="et(&quot;关闭提示&quot;)"
              @click="firstHintDismissed = true">×</button>
          </div>

          <!-- 操作行：编译按钮 / 下载 / 重新编译 -->
          <div class="rf-compile-bar">
            <button
              v-if="!compileReady && !compileErrored"
              class="rf-compile-btn" type="button"
              data-testid="rf-compile-btn"
              :disabled="compiling"
              @click="startCompile"
            >
              {{ et(compiling ? '编译中…（首次约 1-3 分钟）' : '编译 PDF') }}
            </button>
            <template v-if="compileReady">
              <button class="rf-compile-btn" type="button" data-testid="rf-download-pdf"
                @click="downloadPdf">{{ et("下载 PDF") }}</button>
              <button class="rf-compile-btn rf-compile-ghost" type="button"
                data-testid="rf-recompile"
                :disabled="compiling" @click="startCompile">
                {{ et(compiling ? '编译中…' : '重新编译') }}
              </button>
            </template>
            <template v-if="compileErrored">
              <button class="rf-compile-btn" type="button" data-testid="rf-recompile"
                :disabled="compiling" @click="startCompile">
                {{ et(compiling ? '编译中…' : '重新编译') }}
              </button>
              <button class="rf-compile-btn rf-compile-ghost" type="button"
                data-testid="rf-download-log" @click="downloadFullLog">{{ et("查看详细记录") }}</button>
            </template>
            <span v-if="compileReady && pdfSizeText" class="rf-compile-size"
              data-testid="rf-pdf-size">{{ pdfSizeText }}</span>
          </div>

          <!-- loading：动画进度（后端无 stage，不确定时长） -->
          <div v-if="compiling" class="rf-compile-loading" role="status"
            data-testid="rf-compile-loading">
            <span class="rf-compile-spin" aria-hidden="true" />
            <p class="rf-state-msg">{{ et(" 正在生成 PDF，请稍候… 可切换到其他面板，编译在后台继续。 ") }}</p>
          </div>

          <!-- 成功：内嵌 PDF 预览（blob URL） -->
          <div v-else-if="compileReady" class="rf-compile-ready">
            <iframe
              class="rf-compile-frame" :src="pdfSrc" :title="et(&quot;PDF 预览&quot;)"
              data-testid="rf-pdf-frame"
            />
            <div class="rf-compile-foot">
              <span class="rf-compile-secs" data-testid="rf-compile-secs">{{ et(" 编译耗时 ") }}{{ et(compileMeta?.seconds != null ? `${Number(compileMeta.seconds).toFixed(1)} 秒` : '—') }}
              </span>
            </div>
          </div>

          <!-- 失败：错误摘要 + log 尾折叠 + 完整 log 下载 -->
          <div v-else-if="compileErrored" class="rf-compile-error" role="alert"
            data-testid="rf-compile-error">
            <p class="rf-compile-msg">{{ et(edu.compileError) }}</p>
            <details
              v-if="compileLog" class="rf-log" :open="!logCollapsed"
              data-testid="rf-log-details"
            >
              <summary class="rf-log-sum" @click="logCollapsed = !logCollapsed">{{ et(" 编译日志（尾 200 行） ") }}</summary>
              <pre class="rf-log-body" data-testid="rf-log-body">{{ compileLog }}</pre>
            </details>
          </div>
        </template>
      </div>
    </template>

    <!-- ========== ② loading ========== -->
    <div v-else-if="loading" class="rf-state" aria-busy="true">
      <div class="rf-skel"></div>
      <p class="rf-state-msg">{{ et("重排版中…（纯规则引擎，通常 <1 秒）") }}</p>
    </div>

    <!-- ========== ③ error ========== -->
    <div v-else-if="errored" class="rf-state" role="alert">
      <p class="rf-state-msg rf-state-error">{{ et(edu.reformatError) }}</p>
      <div class="rf-err-btns">
        <button v-if="file" class="rf-retry" type="button" @click="submit">{{ et("重试") }}</button>
        <button class="rf-retry" type="button" @click="reset">{{ et("重新开始") }}</button>
      </div>
    </div>

    <!-- ========== ④ 上传区 ========== -->
    <div v-else class="rf-upload">
      <!-- P4 模式切换：单文件 .tex（P3 重排版链路）| 项目 .zip（两段式编译，不经重排版） -->
      <div class="rf-modes" role="tablist" :aria-label="et(&quot;上传模式&quot;)">
        <button
          class="rf-mode" :class="{ active: uploadMode === 'tex' }" type="button"
          role="tab" :aria-selected="uploadMode === 'tex'" data-testid="rf-mode-tex"
          @click="switchMode('tex')"
        >{{ et("单文件 .tex") }}</button>
        <button
          class="rf-mode" :class="{ active: uploadMode === 'zip' }" type="button"
          role="tab" :aria-selected="uploadMode === 'zip'" data-testid="rf-mode-zip"
          @click="switchMode('zip')"
        >{{ et("项目 .zip") }}</button>
      </div>

      <!-- ========== ④-zip 项目上传（P4 两段式） ========== -->
      <template v-if="uploadMode === 'zip'">
        <div
          class="rf-drop"
          :class="{ dragging, filled: !!edu.zipFile }"
          role="button"
          tabindex="0"
          @click="zipInput.click()"
          @keydown.enter="zipInput.click()"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onZipDrop"
        >
          <input
            ref="zipInput"
            class="rf-file-input"
            type="file"
            accept=".zip"
            @click.stop
            @change="onZipPick"
          />
          <template v-if="edu.zipFile">
            <span class="rf-file-name">🗜 {{ edu.zipFile.name }} · {{ (edu.zipFile.size / 1024 / 1024).toFixed(1) }} MB</span>
            <button class="rf-remove" type="button" :title="et(&quot;移除文件&quot;)" @click.stop="removeZip">×</button>
          </template>
          <template v-else>
            <span class="rf-drop-main">{{ et("拖拽 LaTeX 项目（.zip）到此处，或点击选择") }}</span>
            <span class="rf-hint">{{ et("打包建议：main.tex 放 zip 根目录，图片用相对路径引用，不要包含 .git / node_modules / 无关大文件。") }}</span>
          </template>
        </div>
        <p v-if="zipError" class="rf-file-error" data-testid="rf-zip-error">{{ et(zipError) }}</p>

        <button
          class="rf-submit"
          type="button"
          :disabled="!edu.zipFile || zipInspecting"
          data-testid="rf-zip-inspect-btn"
          @click="startZipInspect"
        >
          {{ et(zipInspecting ? '检查中…' : '上传并检查') }}
        </button>

        <!-- inspect 通过：项目摘要 + 编译入口 + 编译结果（复用 P3b compile* 状态） -->
        <template v-if="zipInspected">
          <div class="rf-zip-meta" data-testid="rf-zip-meta">{{ et(" 📦 主文件：") }}<code>{{ edu.zipInspect?.main }}</code>{{ et(" · 文件 ") }}{{ edu.zipInspect?.stats?.files }}{{ et(" 个 · .tex ") }}{{ edu.zipInspect?.stats?.tex }}
            <template v-if="edu.zipInspect?.stats?.images">{{ et(" · 图片 ") }}{{ edu.zipInspect.stats.images }}</template>
            <template v-if="edu.zipInspect?.stats?.cls"> · .cls {{ edu.zipInspect.stats.cls }}</template>
            <template v-if="edu.zipInspect?.stats?.bib"> · .bib {{ edu.zipInspect.stats.bib }}</template>
            <template v-if="edu.zipInspect?.skipped_count">
              · <span class="rf-zip-skipped">{{ edu.zipInspect.skipped_count }}{{ et(" 个文件已跳过") }}</span>
            </template>
          </div>
          <p v-if="edu.zipInspect?.skipped_count" class="rf-zip-skipped-list" data-testid="rf-zip-skipped">{{ et(" 已跳过（不参与编译）：") }}{{ (edu.zipInspect?.skipped || []).map((s) => s.name).join('、') }}
          </p>

          <div class="rf-compile-bar">
            <button
              v-if="!compileReady && !compileErrored"
              class="rf-compile-btn" type="button"
              data-testid="rf-zip-compile-btn"
              :disabled="compiling"
              @click="startZipCompile"
            >
              {{ et(compiling ? '编译中…' : '编译 PDF') }}
            </button>
            <template v-if="compileReady">
              <button class="rf-compile-btn" type="button" data-testid="rf-download-pdf"
                @click="downloadZipPdf">{{ et("下载 PDF") }}</button>
              <button class="rf-compile-btn rf-compile-ghost" type="button"
                data-testid="rf-recompile" :disabled="compiling" @click="startZipCompile">
                {{ et(compiling ? '编译中…' : '重新编译') }}
              </button>
            </template>
            <template v-if="compileErrored">
              <button class="rf-compile-btn" type="button" data-testid="rf-recompile"
                :disabled="compiling" @click="startZipCompile">
                {{ et(compiling ? '编译中…' : '重新编译') }}
              </button>
              <button class="rf-compile-btn rf-compile-ghost" type="button"
                data-testid="rf-download-log" @click="downloadZipLog">{{ et("查看详细记录") }}</button>
            </template>
            <span v-if="compileReady && pdfSizeText" class="rf-compile-size"
              data-testid="rf-pdf-size">{{ pdfSizeText }}</span>
          </div>

          <div v-if="compiling" class="rf-compile-loading" role="status"
            data-testid="rf-compile-loading">
            <span class="rf-compile-spin" aria-hidden="true" />
            <p class="rf-state-msg">{{ et("编译中…（tectonic 单槽队列，常规 10-30 秒；项目首次编译需下载宏包可能更慢）") }}</p>
          </div>

          <div v-else-if="compileReady" class="rf-compile-ready">
            <iframe
              class="rf-compile-frame" :src="pdfSrc" :title="et(&quot;PDF 预览&quot;)"
              data-testid="rf-pdf-frame"
            />
            <div class="rf-compile-foot">
              <span class="rf-compile-secs" data-testid="rf-compile-secs">{{ et(" 编译耗时 ") }}{{ et(compileMeta?.seconds != null ? `${Number(compileMeta.seconds).toFixed(1)} 秒` : '—') }}
              </span>
            </div>
          </div>

          <div v-else-if="compileErrored" class="rf-compile-error" role="alert"
            data-testid="rf-compile-error">
            <p class="rf-compile-msg">{{ et(edu.compileError) }}</p>
            <details
              v-if="compileLog" class="rf-log" :open="!logCollapsed"
              data-testid="rf-log-details"
            >
              <summary class="rf-log-sum" @click="logCollapsed = !logCollapsed">{{ et(" 编译日志（尾 200 行） ") }}</summary>
              <pre class="rf-log-body" data-testid="rf-log-body">{{ compileLog }}</pre>
            </details>
          </div>
        </template>
      </template>

      <!-- ========== ④-tex 单文件上传（P3 链路，零改动） ========== -->
      <template v-else>
      <div
        class="rf-drop"
        :class="{ dragging, filled: !!file }"
        role="button"
        tabindex="0"
        @click="fileInput.click()"
        @keydown.enter="fileInput.click()"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input
          ref="fileInput"
          class="rf-file-input"
          type="file"
          accept=".tex"
          @click.stop
          @change="onPick"
        />
        <template v-if="file">
          <span class="rf-file-name">📄 {{ file.name }} · {{ (file.size / 1024).toFixed(1) }} KB</span>
          <button class="rf-remove" type="button" :title="et(&quot;移除文件&quot;)" @click.stop="removeFile">×</button>
        </template>
        <template v-else>
          <span class="rf-drop-main">{{ et("拖拽 LaTeX 源码（.tex）到此处，或点击选择") }}</span>
          <span class="rf-hint">{{ et("仅 .tex 单文件（多文件 \\input 工程请先合并）· ≤ 2MB · UTF-8") }}</span>
        </template>
      </div>
      <p v-if="fileError" class="rf-file-error">{{ et(fileError) }}</p>

      <label class="rf-label" for="rf-target">{{ et("目标期刊（必选，决定规则集）") }}</label>
      <select id="rf-target" v-model="targetChoice" class="rf-input rf-select" :disabled="loading">
        <optgroup :label="et(&quot;通用规则&quot;)">
          <option value="default">{{ et("通用规则（APA + 通用论文规范）") }}</option>
        </optgroup>
        <optgroup v-if="edu.journals.length" :label="et(&quot;精选期刊&quot;)">
          <option v-for="j in edu.journals" :key="j.id" :value="j.id">{{ j.name }}</option>
        </optgroup>
        <optgroup :label="et(&quot;期刊目录（1056 本）&quot;)">
          <option value="__dir_en__">{{ et("国际目录 · 搜索选择…") }}</option>
          <option value="__dir_zh__">{{ et("中文目录 · 搜索选择…") }}</option>
          <option v-if="dirPick" :value="`dir:${dirPick.id}`">{{ dirPick.name }}{{ et("（已选定）") }}</option>
        </optgroup>
      </select>

      <!-- 目录搜索模式：输入 → 300ms 防抖 → 列表点选 -->
      <div v-if="dirMode" class="rf-dirsearch">
        <input
          v-model="dirQuery"
          class="rf-input"
          type="search"
          :placeholder="et(dirMode === 'en' ? '搜索国际目录期刊名 / ISSN…' : '搜索中文目录期刊名 / ISSN…')"
          :disabled="loading"
        />
        <p v-if="dirError" class="rf-file-error">{{ et(dirError) }}</p>
        <p v-if="dirLoading" class="rf-dirhint">{{ et("搜索中…") }}</p>
        <p v-else-if="dirQuery.trim() && !dirResults.length && !dirError" class="rf-dirhint">{{ et("无匹配期刊") }}</p>
        <ul v-if="dirResults.length" class="rf-dirlist">
          <li v-for="it in dirResults" :key="it.id">
            <button class="rf-diritem" type="button" @click="pickDir(it)">
              <span class="rf-dirname">{{ it.name }}</span>
              <span class="rf-dirmeta">
                {{ it.issn ? 'ISSN ' + it.issn : '' }}
                <template v-if="it.cas_partition"> · {{ it.cas_partition }}</template>
                <template v-if="it.jif"> · JIF {{ it.jif }}</template>
              </span>
            </button>
          </li>
        </ul>
      </div>
      <p v-else-if="targetChoice.startsWith('dir:') && !dirPick" class="rf-dirhint">{{ et(" ⚠ 目录选中记录缺失，请重新搜索选择期刊 ") }}</p>

      <button
        class="rf-submit"
        type="button"
        :disabled="!file || !targetReady || loading"
        @click="submit"
      >{{ et(" 开始自动重排版 ") }}</button>
      <p v-if="dirMode && !actualTarget" class="rf-dirhint">{{ et("请从搜索结果中选择期刊后提交") }}</p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.rf {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rf-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.rf-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.rf-sub {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.rf-meta {
  font-size: 12.5px;
  color: var(--pa-ink-muted);
}
/* 规则摘要：次级文字（同 rf-meta 色阶），与汇总条的计数视觉区分 */
.rf-rules {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--pa-ink-muted);
}
.rf-rules-status {
  color: var(--pa-ink-subtle);
}
.rf-copy-id {
  margin-left: 8px;
  padding: 1px 8px;
  font-size: 11px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-sm);
  background: transparent;
  color: var(--pa-ink-subtle);
  cursor: pointer;
}
.rf-copy-id:hover { color: var(--pa-ink); border-color: var(--pa-hairline-strong); }
.rf-note {
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--pa-warning);
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-warning);
  font-size: 12.5px;
}
.rf-struct {
  padding: 8px 12px;
  border: 1px solid var(--pa-warning);
  border-radius: var(--pa-r-md);
  color: var(--pa-warning);
  font-size: 12.5px;
}

/* 汇总条 */
.rf-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.rf-count {
  padding: 3px 12px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 12.5px;
  font-weight: 600;
}
.rf-count.mod { color: var(--pa-success); }
.rf-count.sug { color: var(--pa-warning); }
.rf-count.warn { color: var(--pa-warning); }
.rf-again,
.rf-dl {
  padding: 5px 14px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
}
.rf-dl { margin-left: auto; }
.rf-again:hover,
.rf-dl:hover {
  color: var(--pa-ink);
  background: var(--pa-surface-2);
}

/* Tab */
.rf-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--pa-hairline);
}
.rf-tab {
  padding: 7px 14px;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 13px;
  cursor: pointer;
}
.rf-tab.active {
  color: var(--pa-ink);
  font-weight: 600;
  border-bottom-color: var(--pa-g-from);
}
.rf-tab:disabled {
  opacity: .45;
  cursor: not-allowed;
}
.rf-src {
  margin: 0;
  padding: 14px 16px;
  max-height: 60vh;
  overflow: auto;
  background: var(--pa-code-bg);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--pa-ink);
  white-space: pre;
}
.rf-empty {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}

/* Diff 行 */
.rf-diff {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rf-row {
  padding: 10px 14px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-left-width: 3px;
  border-radius: var(--pa-r-md);
}
.rf-row.modified { border-left-color: var(--pa-success); }
.rf-row.warning { border-left-color: var(--pa-warning); }
.rf-row-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.rf-line {
  font-family: var(--pa-font-mono);
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.rf-rule {
  padding: 1px 8px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 11.5px;
  font-weight: 600;
}
.rf-rule.k1 { color: var(--pa-g-from); }   /* 字号 */
.rf-rule.k2 { color: var(--pa-success); }  /* 页边距 */
.rf-rule.k3 { color: var(--pa-info, #5b8def); } /* 行距 */
.rf-rule.k4 { color: var(--pa-error); }    /* 参考文献 */
.rf-act {
  margin-left: auto;
  padding: 1px 8px;
  border-radius: var(--pa-r-pill);
  font-size: 11.5px;
  font-weight: 700;
}
.rf-act.modified {
  color: var(--pa-success);
  border: 1px solid var(--pa-success);
}
.rf-act.warning {
  color: var(--pa-warning);
  border: 1px solid var(--pa-warning);
}
.rf-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.rf-pair pre {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-sm);
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
}
.rf-old {
  background: var(--pa-surface-2);
  color: var(--pa-ink-muted);
}
.rf-old::before {
  content: '− ';
  color: var(--pa-error);
}
.rf-new {
  background: var(--pa-surface-2);
  color: var(--pa-ink);
}
.rf-new::before {
  content: '+ ';
  color: var(--pa-success);
}
.rf-skip {
  background: transparent;
  color: var(--pa-warning);
  font-style: italic;
}
.rf-reason {
  margin: 6px 0 0;
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--pa-ink-muted);
  white-space: pre-wrap;
  word-break: break-word;
}
.rf-reason.sug {
  color: var(--pa-warning);
}

.rf-wip {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
  padding: 16px 18px;
  border: 1px dashed var(--pa-hairline-strong);
  border-radius: var(--pa-r-lg);
  background: var(--pa-surface-1);
}
.rf-wip-main {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pa-ink);
}
.rf-wip-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-ink-subtle);
}

/* 编译 PDF（P3b） */
.rf-compile {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rf-first-hint {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid color-mix(in srgb, var(--pa-warning) 45%, transparent);
  border-radius: var(--pa-r-md);
  background: color-mix(in srgb, var(--pa-warning) 12%, transparent);
  color: var(--pa-ink);
  font-size: 12.5px;
  line-height: 1.6;
}
.rf-hint-x {
  border: none;
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.rf-hint-x:hover { color: var(--pa-ink); }
.rf-compile-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.rf-compile-btn {
  padding: 7px 18px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-gradient);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.rf-compile-btn:disabled { opacity: .6; cursor: not-allowed; }
.rf-compile-btn:not(:disabled):hover { filter: brightness(1.08); }
.rf-compile-ghost {
  background: transparent;
  border: 1px solid var(--pa-hairline-strong);
  color: var(--pa-ink);
}
.rf-compile-size {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.rf-compile-loading {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px 16px;
  background: var(--pa-surface-2);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-lg);
}
.rf-compile-spin {
  flex: none;
  width: 18px;
  height: 18px;
  border: 2px solid var(--pa-hairline-strong);
  border-top-color: var(--pa-g-from);
  border-radius: 50%;
  animation: rf-spin .8s linear infinite;
}
@keyframes rf-spin { to { transform: rotate(360deg); } }
.rf-compile-ready { display: flex; flex-direction: column; gap: 8px; }
.rf-compile-frame {
  width: 100%;
  height: 560px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-lg);
  background: var(--pa-surface-2);
}
.rf-compile-foot { display: flex; gap: 12px; align-items: center; }
.rf-compile-secs { font-size: 12px; color: var(--pa-ink-subtle); }
.rf-compile-error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  background: color-mix(in srgb, var(--pa-error) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--pa-error) 40%, transparent);
  border-radius: var(--pa-r-lg);
}
.rf-compile-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-error);
}
.rf-log { border-top: 1px dashed var(--pa-hairline-strong); padding-top: 8px; }
.rf-log-sum {
  font-size: 12.5px;
  color: var(--pa-ink-muted);
  cursor: pointer;
  user-select: none;
}
.rf-log-body {
  margin: 8px 0 0;
  max-height: 300px;
  overflow: auto;
  padding: 10px 12px;
  background: var(--pa-code-bg);
  border-radius: var(--pa-r-md);
  font-size: 12px;
  line-height: 1.55;
  color: var(--pa-ink-muted);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 状态区 */
.rf-state {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rf-skel {
  height: 96px;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  animation: rf-pulse 1.2s var(--pa-ease) infinite alternate;
}
@keyframes rf-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
.rf-state-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}
.rf-state-error { color: var(--pa-error); }
.rf-err-btns { display: flex; gap: 10px; }
.rf-retry {
  padding: 5px 14px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
}
.rf-retry:hover { background: var(--pa-surface-3); }

/* 上传区 */
.rf-upload {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rf-drop {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 130px;
  padding: 18px;
  border: 1.5px dashed var(--pa-hairline-strong);
  border-radius: var(--pa-r-lg);
  background: var(--pa-surface-1);
  cursor: pointer;
  transition: border-color .15s var(--pa-ease), background .15s var(--pa-ease);
}
.rf-drop:hover,
.rf-drop.dragging {
  border-color: var(--pa-g-from);
  background: var(--pa-surface-2);
}
.rf-drop.filled { border-style: solid; }
.rf-file-input { display: none; }
.rf-drop-main {
  font-size: 13.5px;
  color: var(--pa-ink);
}
.rf-hint {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.rf-file-name {
  font-size: 13.5px;
  color: var(--pa-ink);
  word-break: break-all;
}
.rf-remove {
  position: absolute;
  top: 8px;
  right: 10px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: var(--pa-surface-3);
  color: var(--pa-ink-muted);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
}
.rf-remove:hover { color: var(--pa-ink); }
.rf-file-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-error);
}
/* P4 模式切换（单文件 .tex | 项目 .zip） */
.rf-modes {
  display: flex;
  gap: 6px;
}
.rf-mode {
  padding: 5px 14px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-pill);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
}
.rf-mode.active {
  border-color: var(--pa-g-from);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-weight: 600;
}
/* P4 zip 项目摘要（次级文字，与 rf-meta 同色阶） */
.rf-zip-meta {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--pa-ink-muted);
}
.rf-zip-meta code {
  padding: 1px 6px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-sm);
  font-size: 12px;
}
.rf-zip-skipped { color: var(--pa-warning); }
.rf-zip-skipped-list {
  margin: 0;
  font-size: 12px;
  color: var(--pa-ink-subtle);
  word-break: break-all;
}
.rf-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-ink-muted);
}
.rf-input {
  width: 360px;
  max-width: 100%;
  box-sizing: border-box;
  padding: 7px 11px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 13px;
  font-family: inherit;
}
.rf-input:focus {
  outline: none;
  border-color: var(--pa-g-from);
}
.rf-select:disabled { opacity: .6; }
.rf-submit {
  align-self: flex-start;
  padding: 8px 22px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-gradient);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.rf-submit:disabled {
  opacity: .45;
  cursor: not-allowed;
}

/* 目录搜索 */
.rf-dirsearch {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rf-dirhint {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-ink-subtle);
}
.rf-dirlist {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow: auto;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-1);
}
.rf-diritem {
  width: 100%;
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.rf-diritem:hover { background: var(--pa-surface-2); }
.rf-dirname {
  font-size: 13px;
  color: var(--pa-ink);
}
.rf-dirmeta {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
</style>
