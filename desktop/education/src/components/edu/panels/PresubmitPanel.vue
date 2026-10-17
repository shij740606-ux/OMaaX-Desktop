<!-- PresubmitPanel.vue —— ③ AI 预审稿系统（步骤8，产品核心功能）。
     链路：本地 PDF（form-data）→ edu.submitPresubmit(fd, {onUploadProgress})
     → POST /api/review/presubmit（file + target_journal；≤50MB，vstore 解析 + 六维 LLM）
     → {ok, report{summary, major/minor_weaknesses[], presentation_format[],
        questions_for_authors[], recommendation(规则引擎计算), recommendation_rationale},
        meta{...,tier,tier_label,tier_source}}
     判定链（2026-10-12）：LLM 只列 weakness（major 带 hard_rule/fatal 标签），
     recommendation 由 recommend_engine 规则引擎计算并附 rationale（可解释）；
     fatal → reject 红标 + "建议人工复核"（克制展示，须人工核对）。
     优先级：edu.report 存在 → 结果区（不退回上传区）；否则 loading > error > 上传区。
     结果/targetJournal 草稿/已选 PDF（edu.presubmitFile，仅内存）均存 store 跨菜单保留，
     切菜单回来：有报告显示报告，无报告保留已选文件可直接提交。
     http.js FormData+onUploadProgress → XHR 分支，上传阶段显示真实百分比。 -->
<script setup>
import { computed, ref } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

const MAX_PDF = 50 * 1024 * 1024;
const REC = {
  accept: { label: '接收', cls: 'ok' },
  minor_revision: { label: '小修', cls: 'minor' },
  major_revision: { label: '大修', cls: 'major' },
  reject: { label: '拒稿', cls: 'reject' },
};
const HARD_RULE_LABELS = {
  busco_missing: 'BUSCO 完整度缺失',
  hic_missing: 'Hi-C 挂载率缺失',
  accession_missing: '数据 Accession 缺失',
  voucher_missing: '凭证标本缺失',
  abs_missing: 'ABS/名古屋合规声明缺失',
};

const fileInput = ref(null);
const file = ref(edu.presubmitFile || null); // File 存 store（切菜单/面板卸载不丢）；本地 ref 为渲染镜像
const fileError = ref('');
const dragging = ref(false);
const uploadPct = ref(0);

const hasReport = computed(() => !!(edu.report && edu.report.report));
const loading = computed(() => edu.reportStatus === 'loading');
const errored = computed(() => edu.reportStatus === 'error');
const report = computed(() => edu.report?.report || null);
const meta = computed(() => edu.report?.meta || null);
const rec = computed(() => REC[report.value?.recommendation] || REC.major_revision);
const rationale = computed(() => report.value?.recommendation_rationale || null);
const isReject = computed(() => report.value?.recommendation === 'reject');
/** 判定档位提示：同一论文投不同 tier 结果可能不同，必须显式告知 */
const tierText = computed(() => {
  const r = rationale.value;
  const m = meta.value;
  const tier = r?.tier ?? m?.tier;
  if (!tier) return '';
  const label = r?.tier_label || m?.tier_label || '';
  const journal = (m?.target_journal || '').trim();
  const name = journal || (m?.tier_source && m.tier_source !== '默认' ? m.tier_source : '未指定期刊');
  return `判定档位：${name}（tier ${tier} ${label}）`;
});
const hardHits = computed(() =>
  (rationale.value?.hard_hits || []).map((t) => HARD_RULE_LABELS[t] || t)
);
const scannerConflicts = computed(() => rationale.value?.scanner_conflicts || []);
const droppedSections = computed(() => {
  const d = meta.value?.dropped_sections || [];
  const pretty = {
    preamble: '文首', abstract: '摘要', introduction: '引言', results: '结果',
    discussion: '讨论', methods: '方法', data_availability: '数据可用性',
    references: '参考文献', acknowledgements: '致谢',
  };
  return d.filter((s) => !s.startsWith('[')).map((s) => pretty[s] || s);
});

/** 上传阶段（pct<1）显示真实百分比；上传完成后是解析 + LLM 长等待 */
const phaseText = computed(() =>
  uploadPct.value < 1
    ? `上传中 ${Math.round(uploadPct.value * 100)}%`
    : '正在阅读论文并生成审稿意见，请稍候…'
);

const fmtSize = (b) => (b / 1048576).toFixed(1) + ' MB';

function acceptFile(f) {
  fileError.value = '';
  if (!f) return;
  if (!String(f.name || '').toLowerCase().endsWith('.pdf')) {
    fileError.value = '仅支持 PDF 文件';
    return;
  }
  if (!f.size) {
    fileError.value = '上传文件为空';
    return;
  }
  if (f.size > MAX_PDF) {
    fileError.value = 'PDF 超过 50MB 上限';
    return;
  }
  file.value = f;
  edu.presubmitFile = f; // 进 store：切菜单/面板卸载不丢
}
function onPick(e) {
  acceptFile(e.target.files?.[0]);
  e.target.value = ''; // 允许重复选择同名文件
}
function onDrop(e) {
  dragging.value = false;
  acceptFile(e.dataTransfer?.files?.[0]);
}
function removeFile() {
  file.value = null;
  edu.presubmitFile = null;
}

async function submit() {
  if (!file.value || loading.value) return;
  const fd = new FormData();
  fd.append('file', file.value); // 浏览器自动 multipart boundary（http.js 不设 Content-Type）
  fd.append('target_journal', (edu.drafts.targetJournal || '').trim());
  uploadPct.value = 0;
  await edu.submitPresubmit(fd, {
    onUploadProgress: (e) => {
      uploadPct.value = e.pct;
    },
  });
}

/** 重新上传（结果区）/ 重新选择（错误区）：清结果回上传区，保留 targetJournal 草稿 */
function reset() {
  edu.clearReport();
  file.value = null;
  uploadPct.value = 0;
  fileError.value = '';
}
</script>

<template>
  <section class="pp">
    <header class="pp-head">
      <h1 class="pp-title">AI 预审稿系统</h1>
      <span class="pp-sub">植物基因组学六维清单 · PDF ≤ 50MB</span>
    </header>

    <!-- ========== ① 有结果 → 结果区（最高优先级，不退回上传区） ========== -->
    <template v-if="hasReport">
      <div v-if="meta" class="pp-meta">
        📄 {{ meta.filename }}
        <template v-if="meta.target_journal"> · 目标期刊 {{ meta.target_journal }}</template>
        · {{ meta.text_chars }} 字符
        <span v-if="meta.truncated" class="pp-truncated">· 正文超限已截断（保摘要/数据可用性/方法/结果/讨论）</span>
      </div>

      <p class="pp-ai-note">本判定由 AI 生成，仅供投稿前参考；fatal 级判定请务必人工核对后再决定。</p>

      <div class="pp-topline">
        <span class="pp-rec" :class="rec.cls">{{ rec.label }}</span>
        <span v-if="isReject" class="pp-rec-warn">建议人工复核</span>
        <span v-if="tierText" class="pp-tier">{{ tierText }}</span>
        <button class="pp-again" type="button" @click="reset">重新上传</button>
      </div>

      <!-- 判定依据（规则引擎 rationale：可解释、可复现） -->
      <div v-if="rationale" class="pp-rationale">
        <div class="pp-rationale-head">判定依据</div>
        <p class="pp-rationale-text">{{ rationale.explanation }}</p>
        <div v-if="hardHits.length" class="pp-hard-chips">
          <span v-for="(h, i) in hardHits" :key="'hh-' + i" class="pp-hard-chip">{{ h }}</span>
        </div>
        <!-- 扫描器冲突（黄标：证伪了硬性缺项，请人工核对） -->
        <div v-if="scannerConflicts.length" class="pp-conflicts">
          <div class="pp-conflict-title">关键词扫描器冲突（已降级为非硬性，请人工核对）</div>
          <div v-for="(c, i) in scannerConflicts" :key="'sc-' + i" class="pp-conflict-item">
            <span class="pp-conflict-tag">{{ HARD_RULE_LABELS[c.hard_rule] || c.hard_rule }}</span>
            <span class="pp-conflict-reason">{{ c.reason }}</span>
          </div>
        </div>
        <!-- 截断提示：被丢弃的低优先级节（判定价值低，不代表论文缺失） -->
        <div v-if="droppedSections.length" class="pp-dropped">
          未送审部分：{{ droppedSections.join('、') }}（截断所致，非论文缺失）
        </div>
      </div>

      <p class="pp-summary">{{ report.summary }}</p>

      <!-- 主要问题（重视觉） -->
      <h2 class="pp-h2">主要问题（{{ report.major_weaknesses.length }}）</h2>
      <article
        v-for="(it, i) in report.major_weaknesses"
        :key="'maj-' + i"
        class="pp-card major"
      >
        <div class="pp-card-title">
          <span class="pp-idx">{{ i + 1 }}</span>{{ it.title }}
          <span v-if="it.hard_rule" class="pp-hard-badge" title="硬性规则命中，不可协商">硬性</span>
          <span v-if="it.fatal" class="pp-fatal-badge" title="fatal 级：请人工核对证据">fatal</span>
        </div>
        <p class="pp-detail">{{ it.detail }}</p>
        <div v-if="it.evidence" class="pp-evidence">
          <span class="pp-tag">证据</span><code class="pp-evi-text">{{ it.evidence }}</code>
        </div>
        <div v-if="it.suggestion" class="pp-suggestion">
          <span class="pp-tag">建议</span>{{ it.suggestion }}
        </div>
      </article>
      <p v-if="!report.major_weaknesses.length" class="pp-empty">无主要问题</p>

      <!-- 次要问题（轻视觉） -->
      <h2 class="pp-h2">次要问题（{{ report.minor_weaknesses.length }}）</h2>
      <article
        v-for="(it, i) in report.minor_weaknesses"
        :key="'min-' + i"
        class="pp-card minor"
      >
        <div class="pp-card-title">
          <span class="pp-idx light">{{ i + 1 }}</span>{{ it.title }}
          <span
            v-if="it.scanner_downgraded"
            class="pp-down-badge"
            title="扫描器在正文检出该缺项证据，已从大修降级为小修；请人工核对"
          >冲突降级</span>
        </div>
        <p class="pp-detail light">{{ it.detail }}</p>
        <div v-if="it.evidence" class="pp-evidence light">
          <span class="pp-tag">证据</span><code class="pp-evi-text">{{ it.evidence }}</code>
        </div>
        <div v-if="it.suggestion" class="pp-suggestion light">
          <span class="pp-tag">建议</span>{{ it.suggestion }}
        </div>
      </article>
      <p v-if="!report.minor_weaknesses.length" class="pp-empty">无次要问题</p>

      <!-- 排版与规范（默认折叠） -->
      <details class="pp-details">
        <summary>排版与规范（{{ report.presentation_format.length }}）</summary>
        <article
          v-for="(it, i) in report.presentation_format"
          :key="'pre-' + i"
          class="pp-card minor"
        >
          <div class="pp-card-title">
            <span class="pp-idx light">{{ i + 1 }}</span>{{ it.title }}
          </div>
          <p class="pp-detail light">{{ it.detail }}</p>
          <div v-if="it.evidence" class="pp-evidence light">
            <span class="pp-tag">证据</span><code class="pp-evi-text">{{ it.evidence }}</code>
          </div>
          <div v-if="it.suggestion" class="pp-suggestion light">
            <span class="pp-tag">建议</span>{{ it.suggestion }}
          </div>
        </article>
        <p v-if="!report.presentation_format.length" class="pp-empty">无排版类意见</p>
      </details>

      <!-- 给作者的问题 -->
      <h2 class="pp-h2">给作者的问题（{{ report.questions_for_authors.length }}）</h2>
      <ol class="pp-questions">
        <li v-for="(q, i) in report.questions_for_authors" :key="'q-' + i">{{ q }}</li>
      </ol>
      <p v-if="!report.questions_for_authors.length" class="pp-empty">无</p>
    </template>

    <!-- ========== ② loading：上传进度 / 审稿长等待 ========== -->
    <div v-else-if="loading" class="pp-state" aria-busy="true">
      <div class="pp-bar">
        <div class="pp-bar-fill" :style="{ width: Math.round(uploadPct * 100) + '%' }"></div>
      </div>
      <div class="pp-skel"></div>
      <div class="pp-skel pp-skel-mid"></div>
      <p class="pp-state-msg">{{ phaseText }}</p>
    </div>

    <!-- ========== ③ error ========== -->
    <div v-else-if="errored" class="pp-state" role="alert">
      <p class="pp-state-msg pp-state-error">{{ edu.reportError }}</p>
      <div class="pp-err-btns">
        <button v-if="file" class="pp-retry" type="button" @click="submit">重试</button>
        <button class="pp-retry" type="button" @click="reset">重新选择文件</button>
      </div>
    </div>

    <!-- ========== ④ 上传区（report 为 null 时才显示） ========== -->
    <div v-else class="pp-upload">
      <div
        class="pp-drop"
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
          class="pp-file-input"
          type="file"
          accept=".pdf,application/pdf"
          @click.stop
          @change="onPick"
        />
        <template v-if="file">
          <span class="pp-file-name">📄 {{ file.name }} · {{ fmtSize(file.size) }}</span>
          <button class="pp-remove" type="button" title="移除文件" @click.stop="removeFile">×</button>
        </template>
        <template v-else>
          <span class="pp-drop-main">拖拽 PDF 到此处，或点击选择</span>
          <span class="pp-hint">仅支持 PDF · ≤ 50MB</span>
        </template>
      </div>
      <p v-if="fileError" class="pp-file-error">{{ fileError }}</p>

      <label class="pp-label" for="pp-tj">目标期刊（选填，影响格式类意见）</label>
      <input
        id="pp-tj"
        v-model="edu.drafts.targetJournal"
        class="pp-input"
        type="text"
        placeholder="如 The Plant Cell（留空则不限定）"
      />

      <button class="pp-submit" type="button" :disabled="!file || loading" @click="submit">
        {{ loading ? phaseText : '开始预审稿' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.pp {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.pp-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.pp-sub {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.pp-meta {
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.pp-truncated {
  color: var(--pa-warning);
}
.pp-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.pp-rec {
  display: inline-flex;
  align-items: center;
  padding: 3px 14px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 13px;
  font-weight: 700;
}
.pp-rec.ok { color: var(--pa-success); }
.pp-rec.minor { color: var(--pa-info); }
.pp-rec.major { color: var(--pa-warning); }
.pp-rec.reject { color: var(--pa-error); }
.pp-ai-note {
  margin: 0;
  padding: 6px 10px;
  border: 1px dashed var(--pa-hairline);
  border-radius: var(--pa-r-sm);
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.pp-rec-warn {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--pa-r-sm);
  background: var(--pa-error);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}
.pp-tier {
  flex: 1;
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.pp-rationale {
  padding: 10px 12px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
}
.pp-rationale-head {
  font-size: 12px;
  font-weight: 600;
  color: var(--pa-ink-muted);
  margin-bottom: 4px;
}
.pp-rationale-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--pa-ink);
}
.pp-hard-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.pp-hard-chip {
  padding: 1px 8px;
  border: 1px solid var(--pa-error);
  border-radius: var(--pa-r-pill);
  font-size: 11.5px;
  color: var(--pa-error);
}
.pp-conflicts {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--pa-warn, #d4a017);
  border-radius: var(--pa-r-sm);
  background: color-mix(in srgb, var(--pa-warn, #d4a017) 8%, transparent);
}
.pp-conflict-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--pa-warn, #9a6b00);
  margin-bottom: 4px;
}
.pp-conflict-item {
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 12px;
  line-height: 1.6;
  color: var(--pa-ink);
}
.pp-conflict-tag {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--pa-warn, #d4a017);
  border-radius: var(--pa-r-pill);
  font-size: 10.5px;
  color: var(--pa-warn, #9a6b00);
}
.pp-conflict-reason {
  color: var(--pa-muted, #666);
}
.pp-dropped {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--pa-muted, #777);
}
.pp-hard-badge {
  flex: none;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--pa-r-sm);
  border: 1px solid var(--pa-error);
  font-size: 10.5px;
  font-weight: 600;
  color: var(--pa-error);
  line-height: 1.6;
}
.pp-down-badge {
  flex: none;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--pa-r-sm);
  border: 1px solid var(--pa-warn, #d4a017);
  font-size: 10.5px;
  font-weight: 600;
  color: var(--pa-warn, #9a6b00);
  line-height: 1.6;
}
.pp-fatal-badge {
  flex: none;
  margin-left: 4px;
  padding: 0 6px;
  border-radius: var(--pa-r-sm);
  background: var(--pa-error);
  font-size: 10.5px;
  font-weight: 700;
  color: #fff;
  line-height: 1.6;
}
.pp-again {
  padding: 5px 14px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
}
.pp-again:hover {
  color: var(--pa-ink);
  background: var(--pa-surface-2);
}
.pp-summary {
  margin: 0;
  padding: 12px 14px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pa-ink);
}
.pp-h2 {
  margin: 8px 0 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-ink);
}
.pp-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 12px 14px;
  border-radius: var(--pa-r-md);
}
.pp-card.major {
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline-strong);
}
.pp-card.minor {
  background: transparent;
  border: 1px solid var(--pa-hairline);
}
.pp-card-title {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--pa-ink);
  line-height: 1.5;
}
.pp-idx {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--pa-surface-3);
  color: var(--pa-ink);
  font-family: var(--pa-font-mono);
  font-size: 11px;
  margin-top: 1px;
}
.pp-idx.light {
  background: transparent;
  border: 1px solid var(--pa-hairline);
  color: var(--pa-ink-muted);
}
.pp-detail {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--pa-ink);
}
.pp-detail.light {
  color: var(--pa-ink-muted);
}
.pp-evidence {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  background: var(--pa-code-bg);
  border-radius: var(--pa-r-sm);
}
.pp-evidence.light {
  background: transparent;
  border: 1px dashed var(--pa-hairline);
}
.pp-evi-text {
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--pa-ink);
  white-space: normal;
}
.pp-evidence.light .pp-evi-text {
  color: var(--pa-ink-muted);
}
.pp-suggestion {
  font-size: 13px;
  line-height: 1.6;
  color: var(--pa-ink);
}
.pp-suggestion.light {
  color: var(--pa-ink-muted);
}
.pp-tag {
  flex: none;
  display: inline-block;
  margin-right: 8px;
  padding: 1px 7px;
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-3);
  font-size: 11px;
  color: var(--pa-ink-muted);
  vertical-align: 1px;
}
.pp-empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-ink-subtle);
}
.pp-details {
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  padding: 8px 14px;
}
.pp-details summary {
  cursor: pointer;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--pa-ink-muted);
}
.pp-details summary:hover {
  color: var(--pa-ink);
}
.pp-details[open] summary {
  margin-bottom: 10px;
  color: var(--pa-ink);
}
.pp-questions {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  line-height: 1.65;
  color: var(--pa-ink);
}
.pp-state {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 6px 0;
}
.pp-bar {
  height: 6px;
  border-radius: var(--pa-r-pill);
  background: var(--pa-surface-2);
  overflow: hidden;
}
.pp-bar-fill {
  height: 100%;
  background: var(--pa-g-from);
  transition: width .2s var(--pa-ease);
}
.pp-skel {
  height: 56px;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  animation: pp-pulse 1.2s var(--pa-ease) infinite alternate;
}
.pp-skel-mid {
  height: 88px;
}
@keyframes pp-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
.pp-state-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}
.pp-state-error {
  color: var(--pa-error);
}
.pp-err-btns {
  display: flex;
  gap: 10px;
}
.pp-retry {
  padding: 5px 14px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
}
.pp-retry:hover {
  background: var(--pa-surface-3);
}
.pp-upload {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pp-drop {
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
.pp-drop:hover,
.pp-drop.dragging {
  border-color: var(--pa-g-from);
  background: var(--pa-surface-2);
}
.pp-drop.filled {
  border-style: solid;
}
.pp-file-input {
  display: none;
}
.pp-drop-main {
  font-size: 13.5px;
  color: var(--pa-ink);
}
.pp-hint {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.pp-file-name {
  font-size: 13.5px;
  color: var(--pa-ink);
  word-break: break-all;
}
.pp-remove {
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
.pp-remove:hover {
  color: var(--pa-ink);
}
.pp-file-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-error);
}
.pp-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-ink-muted);
}
.pp-input {
  width: 320px;
  max-width: 100%;
  box-sizing: border-box;
  padding: 7px 11px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 13px;
}
.pp-input:focus {
  outline: none;
  border-color: var(--pa-g-from);
}
.pp-submit {
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
.pp-submit:disabled {
  opacity: .45;
  cursor: not-allowed;
}
</style>
