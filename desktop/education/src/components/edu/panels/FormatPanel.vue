<!-- FormatPanel.vue —— ⑤ 论文排版与规范（④ 后端规则引擎前端）。
     链路：本地 PDF/Word/LaTeX（form-data）→ edu.submitFormatCheck →
     POST /api/review/format-check（file + target_journal=期刊id，纯规则 <1s）
     → {ok, checks[{key,name,status,detail,suggestion}], summary{pass,warn,fail},
        meta{filename,file_type,pages,ruleset_source,rules_source,journal_*,
             refs_style_*,tex{class,fontsize_pt,paper,packages,cites,macros}}}
     （真实响应结构写入前已 curl 8100 实测：PDF/Word/LaTeX 三类型 + 错误信封）。
     LaTeX：detail 中含 "\n建议：\n" 标记时拆分为 正文 + <pre class="fc-code"> 代码块
     （只展示建议片段，不自动改写）。
     优先级：formatReport 存在 → 结果区（不退回上传区）；否则 loading > error > 上传区。
     rules_source="placeholder" → 结果区顶部黄字提示（诚实展示占位规则）。
     结果跨菜单保留（store formatReport）；期刊下拉草稿 drafts.formatJournalId；
     文件为面板本地 ref。样式 fc- 前缀。 -->
<script setup>
import {et, educationLocale} from '../../../i18n.js';
import { computed, onMounted, ref } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

const MAX_SIZE = 50 * 1024 * 1024;
const STATUS_LABEL = { pass: '通过', warn: '警告', fail: '不通过' };
const FILE_LABEL = { pdf: 'PDF', docx: 'Word', tex: 'LaTeX' };
const DETAIL_CODE_MARKER = '\n建议：\n'; // 后端 LaTeX 建议片段统一标记

const fileInput = ref(null);
const file = ref(null);
const fileError = ref('');
const dragging = ref(false);

const hasReport = computed(() => !!(edu.formatReport && edu.formatReport.ok));
const loading = computed(() => edu.formatStatus === 'loading');
const errored = computed(() => edu.formatStatus === 'error');
const report = computed(() => edu.formatReport || null);
const meta = computed(() => edu.formatReport?.meta || null);
const summary = computed(() => edu.formatReport?.summary || { pass: 0, warn: 0, fail: 0 });
const checks = computed(() => edu.formatReport?.checks || []);
const placeholderRules = computed(() => meta.value?.rules_source === 'placeholder');

const fmtSize = (b) => (b / 1048576).toFixed(1) + ' MB';

/** detail 按 "\n建议：\n" 拆成 正文 + LaTeX 代码片段（无标记则整段为正文） */
function splitDetail(detail) {
  const text = String(detail || '');
  const i = text.indexOf(DETAIL_CODE_MARKER);
  if (i < 0) return { text, code: '' };
  return { text: text.slice(0, i), code: text.slice(i + DETAIL_CODE_MARKER.length) };
}

onMounted(() => {
  if (edu.journalsStatus === 'idle') edu.fetchJournals(); // 目标期刊下拉数据源
});

function acceptFile(f) {
  fileError.value = '';
  if (!f) return;
  const name = String(f.name || '').toLowerCase();
  if (!name.endsWith('.pdf') && !name.endsWith('.docx') && !name.endsWith('.tex')) {
    fileError.value = '仅支持 PDF / Word（.docx）/ LaTeX（.tex）文件';
    return;
  }
  if (!f.size) {
    fileError.value = '上传文件为空';
    return;
  }
  if (f.size > MAX_SIZE) {
    fileError.value = '上传文件超过 50MB 上限';
    return;
  }
  file.value = f;
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
}

async function submit() {
  if (!file.value || loading.value) return;
  const fd = new FormData();
  fd.append('file', file.value);
  fd.append('target_journal', edu.drafts.formatJournalId || ''); // 期刊 id；空=通用规则
  await edu.submitFormatCheck(fd);
}
function reset() {
  edu.clearFormatReport();
  file.value = null;
  fileError.value = '';
}
</script>

<template>
  <section class="fc">
    <header class="fc-head">
      <h1 class="fc-title">{{ et("论文排版与规范") }}</h1>
      <span class="fc-sub">{{ et("PDF / Word / LaTeX · 页数·字体·边距·行距·图表·参考文献") }}</span>
    </header>

    <!-- ========== ① 有结果 → 结果区（最高优先级） ========== -->
    <template v-if="hasReport">
      <p v-if="placeholderRules" class="fc-placeholder" role="note">{{ et(" ⚠ 格式规则基于占位数据，建议核对期刊官网投稿指南 ") }}</p>

      <div class="fc-meta">
        📄 {{ meta?.filename || '—' }} · {{ FILE_LABEL[meta?.file_type] || meta?.file_type }} ·
        {{ meta?.ruleset_source === 'journal' ? meta?.journal_name : et('通用规则') }} ·
        {{ et(meta?.pages != null ? `共 ${meta?.pages} 页` : '页数未记录') }}
        <span class="fc-meta-sub">{{ et(" · 参考文献要求 ") }}{{ meta?.refs_style_expected }}{{ et(" / 检出 ") }}{{ meta?.refs_style_detected }}
        </span>
      </div>

      <!-- 汇总条 -->
      <div class="fc-summary">
        <span class="fc-count pass">{{ summary.pass }}{{ et(" 项通过") }}</span>
        <span class="fc-count warn">{{ summary.warn }}{{ et(" 项警告") }}</span>
        <span class="fc-count fail">{{ summary.fail }}{{ et(" 项不通过") }}</span>
        <button class="fc-again" type="button" @click="reset">{{ et("重新检查") }}</button>
      </div>

      <!-- 逐项检查卡 -->
      <article v-for="c in checks" :key="c.key" class="fc-card" :class="c.status">
        <span class="fc-badge" :class="c.status">{{ et(STATUS_LABEL[c.status] || c.status) }}</span>
        <div class="fc-card-body">
          <div class="fc-card-name">{{ et(c.name) }}</div>
          <p class="fc-card-detail">{{ et(splitDetail(c.detail).text) }}</p>
          <pre v-if="splitDetail(c.detail).code" class="fc-code">{{ splitDetail(c.detail).code }}</pre>
          <p v-if="c.suggestion" class="fc-card-sug">{{ et("建议：") }}{{ et(c.suggestion) }}</p>
        </div>
      </article>
    </template>

    <!-- ========== ② loading ========== -->
    <div v-else-if="loading" class="fc-state" aria-busy="true">
      <div class="fc-skel"></div>
      <div class="fc-skel fc-skel-mid"></div>
      <p class="fc-state-msg">{{ et("版式检查中…（纯规则，通常 <1 秒）") }}</p>
    </div>

    <!-- ========== ③ error ========== -->
    <div v-else-if="errored" class="fc-state" role="alert">
      <p class="fc-state-msg fc-state-error">{{ et(edu.formatError) }}</p>
      <div class="fc-err-btns">
        <button v-if="file" class="fc-retry" type="button" @click="submit">{{ et("重试") }}</button>
        <button class="fc-retry" type="button" @click="reset">{{ et("重新选择文件") }}</button>
      </div>
    </div>

    <!-- ========== ④ 上传区 ========== -->
    <div v-else class="fc-upload">
      <div
        class="fc-drop"
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
          class="fc-file-input"
          type="file"
          accept=".pdf,.docx,.tex,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          @click.stop
          @change="onPick"
        />
        <template v-if="file">
          <span class="fc-file-name">📄 {{ file.name }} · {{ fmtSize(file.size) }}</span>
          <button class="fc-remove" type="button" :title="et(&quot;移除文件&quot;)" @click.stop="removeFile">×</button>
        </template>
        <template v-else>
          <span class="fc-drop-main">{{ et("拖拽 PDF / Word(.docx) / LaTeX(.tex) 到此处，或点击选择") }}</span>
          <span class="fc-hint">{{ et("仅支持 PDF / Word(.docx) / LaTeX(.tex) · ≤ 50MB · 纯本地规则检查") }}</span>
        </template>
      </div>
      <p v-if="fileError" class="fc-file-error">{{ et(fileError) }}</p>

      <label class="fc-label" for="fc-journal">{{ et("目标期刊（选填，决定规则集）") }}</label>
      <select
        id="fc-journal"
        v-model="edu.drafts.formatJournalId"
        class="fc-input fc-select"
        :disabled="loading"
      >
        <option value="">{{ et("不指定（通用规则）") }}</option>
        <option v-for="j in edu.journals" :key="j.id" :value="j.id">{{ j.name }}</option>
      </select>

      <button class="fc-submit" type="button" :disabled="!file || loading" @click="submit">{{ et(" 开始版式检查 ") }}</button>
    </div>
  </section>
</template>

<style scoped>
.fc {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fc-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.fc-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.fc-sub {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}

/* 占位规则黄字提示（仅 rules_source=placeholder 时渲染） */
.fc-placeholder {
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--pa-warning);
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-warning);
  font-size: 12.5px;
}
.fc-meta {
  font-size: 12.5px;
  color: var(--pa-ink-muted);
}
.fc-meta-sub {
  color: var(--pa-ink-subtle);
}

/* 汇总条 */
.fc-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.fc-count {
  padding: 3px 12px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 12.5px;
  font-weight: 600;
}
.fc-count.pass { color: var(--pa-success); }
.fc-count.warn { color: var(--pa-warning); }
.fc-count.fail { color: var(--pa-error); }
.fc-again {
  margin-left: auto;
  padding: 5px 14px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
}
.fc-again:hover {
  color: var(--pa-ink);
  background: var(--pa-surface-2);
}

/* 检查卡 */
.fc-card {
  display: flex;
  gap: 12px;
  padding: 12px 14px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-left-width: 3px;
  border-radius: var(--pa-r-md);
}
.fc-card.pass { border-left-color: var(--pa-success); }
.fc-card.warn { border-left-color: var(--pa-warning); }
.fc-card.fail { border-left-color: var(--pa-error); }
.fc-badge {
  flex: none;
  align-self: flex-start;
  min-width: 52px;
  text-align: center;
  padding: 2px 9px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 11.5px;
  font-weight: 700;
}
.fc-badge.pass { color: var(--pa-success); }
.fc-badge.warn { color: var(--pa-warning); }
.fc-badge.fail { color: var(--pa-error); }
.fc-card-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.fc-card-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--pa-ink);
}
.fc-card-detail {
  margin: 0;
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--pa-ink-muted);
  word-break: break-word;
}
.fc-card-sug {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--pa-ink);
}
/* LaTeX 建议代码片段（detail 中 "建议：" 标记后的部分）：等宽 + 灰底块 */
.fc-code {
  margin: 0;
  padding: 8px 12px;
  background: var(--pa-code-bg);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-sm);
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--pa-ink);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}

/* 状态区 */
.fc-state {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fc-skel {
  height: 64px;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  animation: fc-pulse 1.2s var(--pa-ease) infinite alternate;
}
.fc-skel-mid {
  height: 96px;
}
@keyframes fc-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
.fc-state-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}
.fc-state-error {
  color: var(--pa-error);
}
.fc-err-btns {
  display: flex;
  gap: 10px;
}
.fc-retry {
  padding: 5px 14px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
}
.fc-retry:hover {
  background: var(--pa-surface-3);
}

/* 上传区 */
.fc-upload {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fc-drop {
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
.fc-drop:hover,
.fc-drop.dragging {
  border-color: var(--pa-g-from);
  background: var(--pa-surface-2);
}
.fc-drop.filled {
  border-style: solid;
}
.fc-file-input {
  display: none;
}
.fc-drop-main {
  font-size: 13.5px;
  color: var(--pa-ink);
}
.fc-hint {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.fc-file-name {
  font-size: 13.5px;
  color: var(--pa-ink);
  word-break: break-all;
}
.fc-remove {
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
.fc-remove:hover {
  color: var(--pa-ink);
}
.fc-file-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-error);
}
.fc-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-ink-muted);
}
.fc-input {
  width: 320px;
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
.fc-input:focus {
  outline: none;
  border-color: var(--pa-g-from);
}
.fc-select:disabled {
  opacity: .6;
}
.fc-submit {
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
.fc-submit:disabled {
  opacity: .45;
  cursor: not-allowed;
}
</style>
