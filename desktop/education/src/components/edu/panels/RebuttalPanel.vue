<!-- RebuttalPanel.vue —— 审稿回复助手（Rebuttal Phase 1，UI v2）。
     独立工作，不依赖 tracker（tracker 已解除映射，2026-10-12）。
     步骤引导：① 粘贴审稿意见 → ② 智能拆条 → ③ 逐条回复 → ④ 导出 Response Letter。
     布局（大屏三栏，<1100px 两栏降级，letter 折叠）：
       左 220px：Reviewer 折叠分组 + comment 列表（编号/severity 徽标/状态点）
       中 flex-1：原话（灰底只读）+ 问题要点（LLM 归纳）+ 5 类表单
                  + draft 预览 + 校验红条 + 逐条确认
       右 40% 可折叠：Letter 预览（仅已填 draft 条目）+ X/Y 已确认 + 下载
     状态机：无 job→粘贴区；loading→拆条动画；error→重试；NOT_FOUND→任务过期。
     样例文本与 docs/samples/rebuttal_sample_reviewer.txt 同源（前端内置副本）。 -->
<script setup>
import { computed, ref, watch } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();
const rb = computed(() => edu.rebuttal);
const job = computed(() => rb.value.job);

const SAMPLE_TEXT = `Reviewer #1:

1. The GWAS analysis lacks multiple-testing correction. Please re-run with FDR control and report q-values for all significant loci. In addition, the sample size justification is missing — please add a power analysis or at least discuss the detectable effect size.

2. Figure 3 is blurry and the axis labels are unreadable. As noted in Comment 3, the same issue applies to Figure S2. Please replace all figures with high-resolution versions.

3. Minor: please add the standard error estimates to Supplementary Table 2.

Reviewer #2:

1. The claim of novelty overstates the advance relative to Zhang et al. 2023. The introduction should more clearly delineate what is new.

2. How were the batch effects between the two sequencing centers corrected? This is essential to support the conclusions.`;

const textInput = ref('');
const showPdf = ref(false);
const partErr = ref('');

const PART_TYPES = [
  { key: 'new_data', label: '补充新数据', hint: '新增实验/数据（图/表编号、关键数值）' },
  { key: 'reanalysis', label: '重新分析', hint: '重跑的分析、方法/参数变化与新结论' },
  { key: 'location', label: '定位/指出位置', hint: '修改或证据在稿件中的位置（Section/Fig/Methods）' },
  { key: 'clarification', label: '澄清说明', hint: '针对误解的准确解释' },
  { key: 'rebuttal', label: '礼貌反驳', hint: '礼貌但明确的不同立场与理由' },
];

const collapsed = ref({}); // reviewerId → bool

const allComments = computed(() => {
  const j = job.value;
  if (!j) return [];
  return (j.reviewers || []).flatMap((r) =>
    (r.comments || []).map((c, i) => ({ ...c, reviewer: r.label, ord: i + 1 })));
});
const activeComment = computed(() => allComments.value.find((c) => c.id === rb.value.activeCid) || null);
const unconfirmedCount = computed(() => allComments.value.filter((c) => !c.human_confirmed).length);
const confirmedCount = computed(() => allComments.value.length - unconfirmedCount.value);
const allConfirmed = computed(() => allComments.value.length > 0 && unconfirmedCount.value === 0);
const letterComments = computed(() => allComments.value.filter((c) => String(c.draft || '').trim()));

const step = computed(() => {
  if (rb.value.status === 'loading') return 2;
  if (!job.value) return 1;
  if (job.value.letter_md && allConfirmed.value) return 4;
  return 3;
});
const isExpired = computed(() => rb.value.expired === true);

const forms = ref({});
function formOf(cid) {
  if (!forms.value[cid]) {
    const c = allComments.value.find((x) => x.id === cid);
    const parts = (c?.response?.parts || []).map((p) => ({ ...p }));
    forms.value[cid] = { parts: parts.length ? parts : [{ type: 'clarification', content: '', location_hint: '' }] };
  }
  return forms.value[cid];
}
const canDraft = computed(() => {
  const f = activeComment.value && forms.value[activeComment.value.id];
  if (!f) return false;
  return f.parts.length > 0 && f.parts.every((p) => String(p.content || '').trim());
});
const validationOf = computed(() => activeComment.value?.validation || null);
const validationFailed = computed(() => validationOf.value && validationOf.value.passed === false);

// 前端立场词预检（与后端 _STANCE 同词表）：只提示不阻止保存/编辑
const STANCE_RE = /\b(disagree|maintain|stand\s+by|reject|respectfully|not\s+persuaded|cannot\s+agree|dispute)\b/i;
const STANCE_HINT = '礼貌反驳需要包含 maintain/disagree/respectfully 等立场词，'
  + '例如：We respectfully maintain that... 或 We disagree with the reviewer\'s point that...';
function stanceMissing(p) {
  const text = String(p.content || '').trim();
  return p.type === 'rebuttal' && !!text && !STANCE_RE.test(text);
}
const stanceHintParts = computed(() => {
  const f = activeComment.value && forms.value[activeComment.value.id];
  if (!f) return [];
  return f.parts.map((p, i) => (stanceMissing(p) ? i : -1)).filter((i) => i >= 0);
});

function statusDot(c) {
  if (c.human_confirmed) return 'done';
  const f = forms.value[c.id] || c.response;
  const parts = f?.parts || [];
  const filled = c.draft || parts.some((p) => String(p.content || '').trim());
  return filled ? 'filled' : 'empty';
}

function fillSample() {
  textInput.value = SAMPLE_TEXT;
}
async function submitText() {
  partErr.value = '';
  const t = textInput.value.trim();
  if (!t) { partErr.value = '请粘贴审稿意见文本'; return; }
  const jobNew = await edu.rebuttalCreate(t);
  if (jobNew?.reviewers?.[0]?.comments?.[0]) {
    edu.rebuttalSetActive(jobNew.reviewers[0].comments[0].id);
  }
}
async function onPdf(ev) {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  partErr.value = '';
  if (!f.name.toLowerCase().endsWith('.pdf')) {
    partErr.value = '仅支持 PDF（推荐直接粘贴文本）';
    ev.target.value = '';
    return;
  }
  const fd = new FormData();
  fd.append('file', f);
  const jobNew = await edu.rebuttalCreatePdf(fd);
  ev.target.value = '';
  if (jobNew?.reviewers?.[0]?.comments?.[0]) {
    edu.rebuttalSetActive(jobNew.reviewers[0].comments[0].id);
  }
}

function partsPayload(cid) {
  return formOf(cid).parts.map((p) => ({
    type: p.type, content: String(p.content || ''), location_hint: p.location_hint || null,
  }));
}
function addPart() {
  formOf(activeComment.value.id).parts.push({ type: 'clarification', content: '', location_hint: '' });
}
function removePart(i) {
  const f = formOf(activeComment.value.id);
  if (f.parts.length > 1) f.parts.splice(i, 1);
}

const draftMsg = ref('');
async function genDraft() {
  const c = activeComment.value;
  if (!c || !canDraft.value) return;
  draftMsg.value = '';
  // 无独立"保存"按钮：生成前自动保存当前要点（保存+生成一步完成）
  const res = await edu.rebuttalSaveResponse(c.id, partsPayload(c.id));
  if (!res.ok) { draftMsg.value = res.error; return; }
  const d = await edu.rebuttalDraft(c.id);
  if (!d.ok) {
    // 校验失败：按钮已由 store finally 解锁；这里补"如何改"的具体建议
    const noStance = d.validation?.findings?.some((f) => f.code === 'REBUTTAL_NO_STANCE');
    draftMsg.value = noStance
      ? `${d.error || '草稿生成失败'}。${STANCE_HINT}`
      : (d.error || '草稿生成失败');
  }
}

async function toggleConfirm(c, ev) {
  const res = await edu.rebuttalConfirm(c.id, ev.target.checked);
  if (!res.ok) draftMsg.value = res.error;
}

const letterAssembling = ref(false);
const letterError = ref('');
const letterCollapsed = ref(false);
async function assembleLetter() {
  letterAssembling.value = true; letterError.value = '';
  const res = await edu.rebuttalAssembleLetter();
  letterAssembling.value = false;
  if (!res.ok) letterError.value = res.error;
}
async function downloadLetter() {
  if (!job.value?.human_ack) return;
  letterError.value = '';
  try {
    const { rebuttalDownloadLetterMd } = await import('../../../api/review.js');
    const md = await rebuttalDownloadLetterMd(job.value.id);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `response-letter-${job.value.id}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    letterError.value = err?.message || '下载失败';
  }
}

function retryParse() {
  edu.clearRebuttal();
  if (!textInput.value.trim() && job.value === null) return; // 保留已输入文本
}

watch(() => rb.value.activeCid, () => {
  partErr.value = ''; draftMsg.value = '';
});
</script>

<template>
  <div class="rb-root">
    <header class="rb-head">
      <h2 class="rb-title">审稿回复助手</h2>
      <p class="rb-sub">Phase 1：粘贴审稿意见 → 智能拆条 → 逐条回复 → 导出 Response Letter（.md）</p>
    </header>

    <!-- 步骤引导 -->
    <ol class="rb-steps" data-testid="rb-steps">
      <li v-for="(s, i) in ['粘贴审稿意见', '智能拆条', '逐条回复', '导出 Response Letter']"
          :key="i" class="rb-step" :class="{ on: step === i + 1, past: step > i + 1 }">
        <span class="rb-step-num"><span class="rb-step-digit">{{ i + 1 }}</span></span>{{ s }}
      </li>
    </ol>

    <!-- 状态：拆条中 -->
    <section v-if="rb.status === 'loading'" class="rb-parsing" data-testid="rb-parsing">
      <div class="rb-spinner" aria-hidden="true" />
      <p>正在拆条…（约 5-15 秒）</p>
      <p class="rb-hint">正在识别 Reviewer 分节与编号条目，并归纳问题要点</p>
    </section>

    <!-- 状态：任务过期 -->
    <section v-else-if="isExpired" class="rb-expired">
      <p>任务已过期，请重新创建。</p>
      <button class="rb-btn rb-btn-primary" type="button" @click="edu.clearRebuttal()">重新开始</button>
    </section>

    <!-- 状态：解析失败 -->
    <section v-else-if="rb.status === 'error'" class="rb-expired">
      <p class="rb-err">{{ rb.error || '拆条失败' }}</p>
      <button class="rb-btn rb-btn-primary" type="button" @click="retryParse">重试</button>
    </section>

    <!-- 状态：无 job → 粘贴区 -->
    <section v-else-if="!job" class="rb-input">
      <div class="rb-input-head">
        <label class="rb-label" for="rb-text">审稿意见文本</label>
        <button class="rb-link" type="button" data-testid="rb-sample" @click="fillSample">填充示例</button>
      </div>
      <textarea
        id="rb-text" v-model="textInput" class="rb-textarea"
        placeholder="直接粘贴审稿意见（支持 Reviewer 1/2 分节或编号条目）..."
      />
      <div class="rb-input-actions">
        <button class="rb-btn rb-btn-primary" type="button" data-testid="rb-parse"
                :disabled="!textInput.trim()" @click="submitText">
          解析并拆条
        </button>
      </div>
      <div class="rb-pdf-card">
        <button class="rb-btn" type="button" data-testid="rb-pdf-toggle" @click="showPdf = !showPdf">
          或上传 PDF
        </button>
        <p class="rb-hint">如果是审稿人批注版 PDF，通常提取不到批注，建议用文本粘贴。</p>
        <div v-if="showPdf" class="rb-pdf">
          <input type="file" accept="application/pdf,.pdf" @change="onPdf" />
        </div>
      </div>
      <p v-if="partErr" class="rb-err">{{ partErr }}</p>
    </section>

    <!-- 主区：三栏 -->
    <section v-else class="rb-main">
      <div class="rb-toolbar">
        <span />
        <button class="rb-link rb-danger" type="button" @click="edu.rebuttalDelete()">删除任务</button>
      </div>

      <div class="rb-banner" :class="allConfirmed ? 'rb-banner-ok' : 'rb-banner-warn'">
        <template v-if="allConfirmed">
          全部已确认 —— 可下载 回复信
        </template>
        <template v-else>{{ unconfirmedCount }} 条未确认 —— 全部勾选确认后才能下载 回复信</template>
      </div>

      <div class="rb-cols" :class="{ 'rb-cols-2': letterCollapsed }">
        <!-- 左：Reviewer → Comment 树 -->
        <aside class="rb-tree">
          <div v-for="r in job.reviewers" :key="r.id" class="rb-rev">
            <button class="rb-rev-label" type="button" @click="collapsed[r.id] = !collapsed[r.id]">
              <span class="rb-rev-caret" :class="{ open: !collapsed[r.id] }">▸</span>
              {{ r.label }}（{{ (r.comments || []).length }}）
            </button>
            <template v-if="!collapsed[r.id]">
              <button
                v-for="(c, i) in r.comments" :key="c.id"
                class="rb-c-item" :class="{ active: c.id === rb.activeCid }"
                type="button" @click="edu.rebuttalSetActive(c.id)"
              >
                <span class="rb-c-ord">{{ i + 1 }}</span>
                <span class="rb-c-title">{{ c.title || c.id }}</span>
                <span class="rb-c-sev" :class="c.severity">{{ c.severity }}</span>
                <span class="rb-c-dot" :class="statusDot(c)" :title="statusDot(c)" />
              </button>
            </template>
          </div>
        </aside>

        <!-- 中：编辑区 -->
        <div v-if="activeComment" class="rb-form">
          <div class="rb-form-head">
            <h3 class="rb-form-title">{{ activeComment.title }}</h3>
            <label class="rb-confirm">
              <input
                type="checkbox" :checked="activeComment.human_confirmed"
                data-testid="rb-confirm" @change="toggleConfirm(activeComment, $event)"
              />
              已确认本条
            </label>
          </div>

          <div class="rb-original">
            <div class="rb-original-label">审稿人原话（{{ activeComment.reviewer }} · #{{ activeComment.ord }}）</div>
            <p class="rb-original-body">{{ activeComment.body }}</p>
          </div>

          <!-- 问题要点（Phase 1 砍手工合并/拆分：多子条由 LLM 拆分，纠错留 Phase 2） -->
          <div class="rb-point">
            <div class="rb-point-body">
              <div class="rb-original-label">问题要点</div>
              <p class="rb-point-text">{{ activeComment.title }}</p>
            </div>
          </div>

          <div v-if="activeComment.cross_refs?.length" class="rb-xref">
            交叉引用：<span v-for="(x, i) in activeComment.cross_refs" :key="i">「{{ x.raw }}」{{ x.target_id ? `→ ${x.target_id}` : '（未解析，保留原文）' }} </span>
          </div>

          <div class="rb-parts">
            <div class="rb-parts-label">回复要点（user_response）</div>
            <div v-for="(p, i) in formOf(activeComment.id).parts" :key="i" class="rb-part">
              <select v-model="p.type" class="rb-select" :data-testid="`rb-type-${i}`">
                <option v-for="t in PART_TYPES" :key="t.key" :value="t.key">{{ t.label }}</option>
              </select>
              <textarea
                v-model="p.content" class="rb-part-content" rows="3"
                :placeholder="PART_TYPES.find((t) => t.key === p.type)?.hint"
                :data-testid="`rb-content-${i}`"
              />
              <!-- 立场词预检：只提示不阻止继续编辑/生成 -->
              <p
                v-if="stanceMissing(p)" class="rb-err rb-stance"
                :data-testid="`rb-stance-hint-${i}`"
              >{{ STANCE_HINT }}</p>
              <input
                v-if="p.type === 'location'"
                v-model="p.location_hint" class="rb-part-hint" data-testid="rb-location-hint"
                placeholder="位置提示：如 Section 3 / Fig. S2 / Methods 第 2 段"
              />
            </div>
            <div class="rb-part-actions">
              <button class="rb-link" type="button" @click="addPart">+ 添加要点</button>
              <button
                v-if="formOf(activeComment.id).parts.length > 1"
                class="rb-link" type="button"
                @click="removePart(formOf(activeComment.id).parts.length - 1)"
              >移除最后一条</button>
            </div>
          </div>

          <div class="rb-actions">
            <button
              class="rb-btn rb-btn-primary" type="button" data-testid="rb-draft"
              :disabled="!canDraft || rb.draftCid === activeComment.id"
              @click="genDraft"
            >
              {{ rb.draftCid === activeComment.id ? '生成中…' : '生成 draft' }}
            </button>
            <span class="rb-hint">填写要点后点击生成；内容会自动保存</span>
          </div>
          <p v-if="draftMsg" class="rb-err">{{ draftMsg }}</p>

          <div v-if="validationFailed" class="rb-banner rb-banner-err" data-testid="rb-validation">
            反幻觉校验未通过（{{ validationOf.findings?.length || 0 }} 项）：
            <ul>
              <li v-for="(f, i) in validationOf.findings" :key="i">[{{ f.code }}] {{ f.message }}</li>
            </ul>
          </div>

          <div v-if="activeComment.draft" class="rb-draft" data-testid="rb-draft-preview">
            <div class="rb-original-label">draft 预览</div>
            <pre class="rb-draft-md">{{ activeComment.draft }}</pre>
          </div>
        </div>
        <div v-else class="rb-empty">← 选择一条审稿意见开始回复</div>

        <!-- 右：Letter 预览（可折叠） -->
        <aside v-if="!letterCollapsed" class="rb-letter">
          <div class="rb-letter-head">
            <span class="rb-letter-count" data-testid="rb-letter-count">
              {{ confirmedCount }}/{{ allComments.length }} 条已确认
            </span>
            <button class="rb-iconbtn" type="button" title="折叠" @click="letterCollapsed = true">»</button>
          </div>
          <div v-if="!letterComments.length" class="rb-empty rb-letter-empty">
            尚无 draft —— 填写回复要点并生成后将在此预览
          </div>
          <div v-for="c in letterComments" :key="c.id" class="rb-letter-item">
            <div class="rb-letter-title">{{ c.reviewer }} · {{ c.title }}</div>
            <pre class="rb-letter-md">{{ c.draft }}</pre>
          </div>
          <div class="rb-letter-actions">
            <button class="rb-btn" type="button" :disabled="letterAssembling" @click="assembleLetter">
              {{ letterAssembling ? '拼装中…' : '拼装 letter' }}
            </button>
            <button
              class="rb-btn rb-btn-primary" type="button" data-testid="rb-download"
              :disabled="!job.human_ack || !job.letter_md" @click="downloadLetter"
            >
              下载 .md
            </button>
          </div>
          <span v-if="!allConfirmed" class="rb-letter-gate" data-testid="rb-gate">
            {{ unconfirmedCount }} 条未确认，下载暂不可用
          </span>
          <p v-if="letterError" class="rb-err">{{ letterError }}</p>
        </aside>
        <button v-else class="rb-letter-expand" type="button" title="展开 Letter 预览" @click="letterCollapsed = false">
          « Letter
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 2026-10-12 深浅双主题适配：全部走 tokens.css 真实变量
   （--pa-hairline/--pa-surface-N/--pa-raised/--pa-ink-N/--pa-g-*）；
   步骤徽章纯 CSS 圆形（无 emoji 数字圈，深色下对比度可控）。 */
.rb-root { display: flex; flex-direction: column; gap: 14px; padding: 18px 22px; max-width: 1280px; color: var(--pa-ink); }
.rb-title { font-size: 20px; margin: 0; color: var(--pa-ink); }
.rb-sub { color: var(--pa-ink-muted); font-size: 13px; margin: 4px 0 0; }

/* 步骤条：纯 CSS 圆形徽章 */
.rb-steps { display: flex; gap: 8px; list-style: none; margin: 0; padding: 0; flex-wrap: wrap; }
.rb-step {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--pa-ink-subtle);
  padding: 6px 14px 6px 8px; border-radius: var(--pa-r-pill);
  background: var(--pa-surface-2); border: 1px solid var(--pa-hairline);
}
.rb-step-num {
  width: 20px; height: 20px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 600; flex-shrink: 0;
  border: 1px solid var(--pa-ink-subtle); color: var(--pa-ink-subtle);
  background: transparent;
}
/* 当前步骤：品牌渐变填充 + 白字 */
.rb-step.on {
  background: var(--pa-surface-1); color: var(--pa-ink);
  border-color: transparent; font-weight: 600;
}
.rb-step.on .rb-step-num {
  background: var(--pa-gradient); border-color: transparent; color: #fff;
}
/* 已完成步骤：绿勾 + 提亮文字 */
.rb-step.past { color: var(--pa-success); border-color: var(--pa-hairline); }
.rb-step.past .rb-step-num {
  border-color: var(--pa-success); color: var(--pa-success); background: transparent;
}
.rb-step.past .rb-step-num::before { content: '✓'; font-size: 11px; }
.rb-step.past .rb-step-num > .rb-step-digit { display: none; }

/* 输入区：surface 底，不用纯白 */
.rb-label, .rb-parts-label, .rb-original-label { font-size: 13px; font-weight: 600; color: var(--pa-ink); }
.rb-input-head { display: flex; justify-content: space-between; align-items: baseline; }
.rb-textarea {
  width: 100%; box-sizing: border-box; font: inherit; padding: 10px;
  background: var(--pa-surface-2); color: var(--pa-ink);
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md);
  min-height: 240px; max-height: 45vh; overflow-y: auto; resize: vertical;
}
.rb-textarea:focus { outline: none; border-color: var(--pa-g-from); }
.rb-textarea::placeholder { color: var(--pa-ink-subtle); }
.rb-input-actions { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
/* PDF 次要卡片：虚线 = hairline 50% 透明；说明文字 = muted */
.rb-pdf-card {
  margin-top: 12px; padding: 12px 14px;
  border: 1px dashed color-mix(in srgb, var(--pa-hairline) 50%, transparent);
  border-radius: var(--pa-r-lg);
  display: flex; flex-direction: column; gap: 6px; align-items: flex-start;
  background: var(--pa-surface-1);
}
.rb-pdf { margin-top: 4px; }
.rb-pdf input { font: inherit; color: var(--pa-ink); accent-color: var(--pa-g-from); }
.rb-btn {
  padding: 6px 14px; border: 1px solid var(--pa-hairline-strong); border-radius: var(--pa-r-md);
  background: var(--pa-raised); color: var(--pa-ink); cursor: pointer; font: inherit;
}
.rb-btn:hover:not(:disabled) { border-color: var(--pa-g-from); }
.rb-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.rb-btn-primary { background: var(--pa-gradient); color: #fff; border-color: transparent; }
.rb-link { background: none; border: none; color: var(--pa-info); cursor: pointer; font: inherit; text-decoration: underline; }
.rb-danger { color: var(--pa-error); }
.rb-err { color: var(--pa-error); font-size: 13px; }
.rb-stance { margin: 4px 0 0; font-size: 12px; }
.rb-hint { color: var(--pa-ink-muted); font-size: 12px; margin: 0; }

/* 解析中 / 过期 / 失败 */
.rb-parsing, .rb-expired {
  display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 48px 20px;
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-lg); background: var(--pa-surface-1);
  color: var(--pa-ink);
}
.rb-spinner {
  width: 28px; height: 28px; border-radius: 50%;
  border: 3px solid var(--pa-hairline-strong); border-top-color: var(--pa-g-to);
  animation: rb-spin 0.8s linear infinite;
}
@keyframes rb-spin { to { transform: rotate(360deg); } }

/* 主区三栏：左/右 surface-2，中 surface-1 区分层级 */
.rb-toolbar { display: flex; justify-content: space-between; align-items: center; }
.rb-banner { padding: 8px 12px; border-radius: var(--pa-r-md); font-size: 13px; }
.rb-banner-warn { background: color-mix(in srgb, var(--pa-warning) 14%, transparent); color: var(--pa-warning); }
.rb-banner-ok { background: color-mix(in srgb, var(--pa-success) 14%, transparent); color: var(--pa-success); }
.rb-banner-err { background: color-mix(in srgb, var(--pa-error) 14%, transparent); color: var(--pa-error); margin-top: 8px; }
.rb-banner-err ul { margin: 4px 0 0 16px; padding: 0; }
.rb-cols { display: grid; grid-template-columns: 220px minmax(0, 1fr) 40%; gap: 14px; align-items: start; }
.rb-cols-2 { grid-template-columns: 220px minmax(0, 1fr) auto; }

.rb-tree {
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-lg);
  padding: 6px; max-height: 560px; overflow: auto;
  background: var(--pa-surface-2);
}
.rb-rev-label {
  display: flex; align-items: center; gap: 4px; width: 100%; text-align: left;
  border: none; background: none; font-weight: 600; font-size: 13px; cursor: pointer;
  padding: 6px 4px; color: var(--pa-ink);
}
.rb-rev-caret { display: inline-block; transition: transform 0.15s; font-size: 11px; color: var(--pa-ink-muted); }
.rb-rev-caret.open { transform: rotate(90deg); }
.rb-c-item {
  display: flex; gap: 6px; align-items: center; width: 100%; text-align: left;
  padding: 5px 6px; border: none; background: none; cursor: pointer;
  border-radius: var(--pa-r-sm); font: inherit; color: var(--pa-ink);
}
.rb-c-item.active { background: var(--pa-surface-3); box-shadow: inset 2px 0 0 var(--pa-g-from); }
.rb-c-ord {
  width: 16px; height: 16px; border-radius: var(--pa-r-xs); background: var(--pa-surface-3); color: var(--pa-ink-muted);
  font-size: 10px; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.rb-c-title { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--pa-ink); }
.rb-c-sev { font-size: 10px; padding: 1px 5px; border-radius: var(--pa-r-pill); flex-shrink: 0; }
.rb-c-sev.major { background: color-mix(in srgb, var(--pa-error) 18%, transparent); color: var(--pa-error); }
.rb-c-sev.minor { background: color-mix(in srgb, var(--pa-warning) 18%, transparent); color: var(--pa-warning); }
.rb-c-sev.question { background: color-mix(in srgb, var(--pa-info) 18%, transparent); color: var(--pa-info); }
.rb-c-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.rb-c-dot.empty { background: var(--pa-ink-subtle); }
.rb-c-dot.filled { background: var(--pa-info); }
.rb-c-dot.done { background: var(--pa-success); }

.rb-form {
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-lg); padding: 14px;
  display: flex; flex-direction: column; gap: 10px; background: var(--pa-surface-1);
}
.rb-form-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.rb-form-title { margin: 0; font-size: 15px; color: var(--pa-ink); }
.rb-confirm { font-size: 13px; display: flex; gap: 6px; align-items: center; white-space: nowrap; color: var(--pa-ink); }
.rb-original { background: var(--pa-surface-2); border-radius: var(--pa-r-md); padding: 8px 10px; }
.rb-original-body { margin: 4px 0 0; font-size: 13px; white-space: pre-wrap; color: var(--pa-ink); }
.rb-point {
  display: flex; gap: 8px; align-items: flex-start;
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md); padding: 8px 10px;
  background: var(--pa-surface-2);
}
.rb-point-body { flex: 1; }
.rb-point-text { margin: 2px 0 0; font-size: 13px; font-weight: 600; color: var(--pa-ink); }
.rb-iconbtn {
  border: 1px solid var(--pa-hairline); background: var(--pa-raised); border-radius: var(--pa-r-sm);
  cursor: pointer; font-size: 13px; padding: 2px 8px; color: var(--pa-ink-muted);
}
.rb-iconbtn:hover { color: var(--pa-ink); border-color: var(--pa-g-from); }
.rb-xref { font-size: 12px; color: var(--pa-ink-muted); }
.rb-part { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.rb-select, .rb-part-hint {
  font: inherit; padding: 6px 8px; border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md);
  max-width: 320px; background: var(--pa-surface-2); color: var(--pa-ink);
}
.rb-part-content {
  font: inherit; padding: 8px; border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md);
  background: var(--pa-surface-2); color: var(--pa-ink);
}
.rb-part-content::placeholder, .rb-part-hint::placeholder { color: var(--pa-ink-subtle); }
.rb-part-actions { display: flex; gap: 12px; margin-top: 6px; }
.rb-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.rb-draft { border-top: 1px solid var(--pa-hairline); padding-top: 8px; }
.rb-draft-md {
  white-space: pre-wrap; font-size: 13px; background: var(--pa-code-bg);
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md); padding: 10px; margin: 6px 0 0;
  color: var(--pa-ink);
}
.rb-empty { color: var(--pa-ink-subtle); padding: 30px; text-align: center; }

/* 右栏 letter：surface-2 与中栏区分 */
.rb-letter {
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-lg); padding: 10px;
  max-height: 640px; overflow: auto; display: flex; flex-direction: column; gap: 8px;
  background: var(--pa-surface-2);
}
.rb-letter-head { display: flex; justify-content: space-between; align-items: center; }
.rb-letter-count { font-size: 12px; font-weight: 600; color: var(--pa-success); }
.rb-letter-empty { padding: 16px; font-size: 12px; }
.rb-letter-item { border-top: 1px solid var(--pa-hairline); padding-top: 6px; }
.rb-letter-title { font-size: 12px; font-weight: 600; margin-bottom: 4px; color: var(--pa-ink); }
.rb-letter-md {
  white-space: pre-wrap; font-size: 12px; background: var(--pa-code-bg);
  border: 1px solid var(--pa-hairline); border-radius: var(--pa-r-md); padding: 8px; margin: 0;
  color: var(--pa-ink);
}
.rb-letter-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.rb-letter-gate { font-size: 12px; color: var(--pa-warning); }
.rb-letter-expand {
  writing-mode: vertical-rl; border: 1px solid var(--pa-hairline); background: var(--pa-surface-2);
  border-radius: var(--pa-r-lg); cursor: pointer; font: inherit; font-size: 12px; padding: 12px 4px;
  color: var(--pa-ink-muted);
}
.rb-letter-expand:hover { color: var(--pa-ink); border-color: var(--pa-g-from); }

@media (max-width: 1100px) {
  .rb-cols, .rb-cols-2 { grid-template-columns: 200px minmax(0, 1fr); }
  .rb-letter { grid-column: 1 / -1; max-height: none; }
  .rb-letter-expand { display: none; }
}
</style>
