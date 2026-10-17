<!-- PptPanel.vue —— 科研汇报助手（P5，/api/review/ppt job 模式）。
     流程：① 选 PDF/粘贴文本 + 场景/时长/风格 → 生成大纲（同步长调用）
           → ② 逐页编辑（bullets/讲稿；evidence 徽标只读展示）→ 保存（重验）
           → ③ 生成 PPTX（python-pptx 本地生成，多风格版式 + 逐页缩略图预览）
           → 下载；可删除/重新生成。
     状态机：edu.ppt.status = idle|loading|editing|building|done|error；
     本地 localPages 副本 + dirty 标记，保存后以后端返回为权威（evidence 重验）。
     预览：build 返回 meta.previews → 缩略图条 + 大图（preview/{page}.png 直链）；
     旧响应无 previews / 编辑后 pptx 失效 → 区块隐藏。 -->
<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useEduStore } from '../../../stores/edu.js';
import { pptDownloadUrl, pptPreviewUrl } from '../../../api/review.js';
import './ppt-styles.css';

const edu = useEduStore();
const warningText = text => String(text).replace(/\bsection\b/g, '章节分隔').replace(/\bevidence\b/g, '证据').replace(/\bquote\b/g, '原文引文').replace(/\bpara_id\b/g, '段落编号');
const ppt = computed(() => edu.ppt);
const job = computed(() => edu.ppt.job);

const SCENARIOS = [
  { key: 'group_meeting', label: '组会汇报' },
  { key: 'proposal', label: '开题报告' },
  { key: 'conclusion_report', label: '结题汇报' },
  { key: 'defense', label: '学位答辩' },
  { key: 'conference_talk', label: '学术会议' },
];
const MINUTES = [
  { v: 10, pages: '8-10 页' },
  { v: 15, pages: '11-14 页' },
  { v: 20, pages: '15-18 页' },
  { v: 30, pages: '22-26 页' },
];
const STYLES = [
  { id: 'academic-minimal', label: '学术极简', desc: '白底蓝黑 · 投屏通用' },
  { id: 'botanic-forest', label: '植研绿林', desc: '植物绿系 · 所内汇报' },
  { id: 'classic-defense', label: '经典答辩', desc: '深蓝衬线 · 正式答辩' },
  { id: 'academic-blue', label: '学术蓝', desc: '经典蓝白 · 答辩常用' },
  { id: 'nature-green', label: '自然绿', desc: '自然生态绿 · 植物/生态领域' },
  { id: 'tech-dark', label: '深色科技', desc: '深色投影 · AI/工程领域' },
  { id: 'monochrome', label: '极简黑白', desc: '黑白灰 · 正式严肃' },
  { id: 'warm-earth', label: '暖色土系', desc: '暖土色系 · 农业领域' },
  { id: 'domestic', label: '国内组会', desc: '深蓝红强调 · 国内组会 / 学术报告' },
  { id: 'international', label: '国际会议', desc: '白底红点 · 海外组会 / 国际会议' },
  { id: 'systems-talk', label: '系统顶会', desc: '深蓝橙标 · 系统顶会报告' },
  { id: 'theory-beamer', label: '理论报告', desc: '灰绿橙点 · Metropolis 理论风' },
  { id: 'jp-gothic', label: '日韩发表', desc: '白底蓝黄贴纸 · 日韩技术发表' },
  { id: 'editorial', label: '杂志插画', desc: '米底紫衬线 · 插画杂志风' },
];
const TYPE_LABELS = {
  title: '封面', outline: '目录', section: '分隔', background: '背景', method: '方法',
  result: '结果', conclusion: '结论', reference: '参考文献',
};

/** 风格卡封面（assets/ppt-style-cards/*.png：真实 build+PIL 渲染的各风格封面） */
function styleCover(id) {
  return new URL(`../../../assets/ppt-style-cards/${id}.png`, import.meta.url).href;
}

/* —— 改进 1：副标题风格数动态（后端 data/ppt_styles 实际数量，不再写死 8/14） —— */
onMounted(() => {
  edu.pptLoadStyles();
});

/** 副标题括号文案：有清单 →「{n} 风格·图文混排」；接口未取到 →「多风格·图文混排」 */
const styleTag = computed(() => {
  const items = edu.pptStyles;
  const n = Array.isArray(items) ? items.length : 0;
  return n > 0 ? `${n} 风格·图文混排` : '多风格·图文混排';
});

/** 风格卡数据：优先后端清单（与副标题数量同源），未取到回落本地 STYLES 常量 */
const styleCards = computed(() => {
  const items = Array.isArray(edu.pptStyles) ? edu.pptStyles : [];
  if (!items.length) return STYLES;
  return items.map((s) => ({
    id: s.id,
    label: s.label || s.display_name || s.id,
    desc: s.description || '',
  }));
});

/* —— 本地编辑副本（后端 job 为权威；生成/保存/构建后重新同步） —— */
const localPages = ref([]);
const dirty = ref(false);

watch(
  job,
  (j) => {
    const pages = j ? JSON.parse(JSON.stringify(j.pages || [])) : [];
    pages.forEach((p) => {
      p.bullets = p.bullets || [];
      p.evidence = p.evidence || [];
      while (p.evidence.length < p.bullets.length) p.evidence.push(null);
      p.evidence.length = p.bullets.length;
      p.suggested_figures = p.suggested_figures || [];
    });
    localPages.value = pages;
    dirty.value = false;
  },
  { immediate: true },
);

function touch() {
  dirty.value = true;
}

function evOf(page, i) {
  return (page.evidence && page.evidence[i]) || null;
}
function badgeClass(page, i) {
  const ev = evOf(page, i);
  if (!ev) return 'ev-none';
  return ev.status === 'verified' ? 'ev-ok' : 'ev-bad';
}
function badgeTitle(page, i) {
  const ev = evOf(page, i);
  if (!ev) return '无来源';
  const q = String(ev.quote || '').slice(0, 80);
  return `${ev.status === 'verified' ? '已核实' : '未核实'} ${ev.para_id || ''}：${q}`;
}
function removeBullet(pi, bi) {
  const p = localPages.value[pi];
  p.bullets.splice(bi, 1);
  if (p.evidence) p.evidence.splice(bi, 1);
  touch();
}
function addBullet(pi) {
  const p = localPages.value[pi];
  p.bullets.push('');
  p.evidence = p.evidence || [];
  p.evidence.push(null);
  touch();
}
// 分隔页副标题：后端 coerce 以 bullets[0] 优先、subtitle 兜底，
// 编辑器同写两字段保持一致；清空 → 回落该章第一页标题
function onSectionSubtitle(pi, e) {
  const p = localPages.value[pi];
  const v = e.target.value;
  p.bullets = v ? [v] : [];
  p.subtitle = v;
  touch();
}

/* —— 动作 —— */
async function onGenerate() {
  await edu.pptGenerate();
}
async function onSave() {
  const saved = await edu.pptSave(JSON.parse(JSON.stringify(localPages.value)));
  if (saved) dirty.value = false;
  return !!saved;
}
async function onBuild() {
  if (dirty.value) {
    const saved = await onSave();
    if (!saved) return;
  }
  await edu.pptBuild();
}
function onFileChange(e) {
  edu.ppt.form.file = (e.target.files && e.target.files[0]) || null;
}
function onReset() {
  if (dirty.value && !window.confirm('有未保存的修改，放弃并重新生成？')) return;
  edu.clearPpt();
}
async function onDelete() {
  await edu.pptDelete();
}

const isBusy = computed(() => ['loading', 'building'].includes(ppt.value.status));
const statusLabel = computed(() => ({
  idle: '待生成', loading: '生成大纲中…', editing: '编辑中',
  building: '生成 PPT 中…', done: '已生成', error: '出错',
}[ppt.value.status] || ppt.value.status));
const pageTarget = computed(() => {
  const m = MINUTES.find((x) => x.v === ppt.value.form.minutes);
  return m ? m.pages : '';
});

/* —— 可感知化（3.x）：meta 露出 + 逐页缩略图预览 —— */
/** createMeta.figures_extracted（PDF 原图提取数；text 模式无此键 → 0） */
const figsExtracted = computed(() => {
  const n = ppt.value.createMeta && ppt.value.createMeta.figures_extracted;
  return typeof n === 'number' ? n : 0;
});
/** evidence 核实比例（本地副本实时算，保存/重验后自动刷新） */
const evStats = computed(() => {
  let ok = 0;
  let total = 0;
  for (const p of localPages.value) {
    for (const ev of p.evidence || []) {
      if (!ev) continue;
      total += 1;
      if (ev.status === 'verified') ok += 1;
    }
  }
  return { ok, total };
});
/** 缩略图条：仅最近一次 build 返回了 previews 且 pptx 就绪才渲染 */
const previewCount = computed(() => {
  const m = ppt.value.buildMeta;
  return job.value && job.value.pptx_ready && m && m.previews > 0 ? m.previews : 0;
});
const activePreview = ref(1);
const failedPreviews = ref(new Set());
const previewPages = computed(() => {
  const out = [];
  for (let i = 1; i <= previewCount.value; i += 1) {
    if (!failedPreviews.value.has(i)) out.push(i);
  }
  return out;
});
function onPreviewError(page) {
  failedPreviews.value = new Set(failedPreviews.value).add(page);
  if (activePreview.value === page) {
    activePreview.value = previewPages.value[0] || 1;
  }
}
function previewSrc(page) {
  return pptPreviewUrl(job.value.id, page);
}
// job/重建变化 → 回到第 1 页、清失败缓存（新 build 会覆盖同名 PNG）
watch(
  () => [job.value && job.value.id, ppt.value.buildMeta],
  () => {
    activePreview.value = 1;
    failedPreviews.value = new Set();
  },
  { deep: false },
);
</script>

<template>
  <section class="ppt-root">
    <header class="ppt-head">
      <div>
        <h2 class="ppt-title">科研汇报助手</h2>
        <p class="ppt-sub">论文 PDF → 证据可溯的大纲 → 逐页编辑 → PPTX 初稿 + 逐页页面预览（{{ styleTag }}）</p>
      </div>
      <span v-if="job" class="ppt-status" :class="`st-${ppt.status}`" data-test="status-chip">
        {{ statusLabel }}
      </span>
    </header>

    <!-- ============ ① 生成表单（无 job） ============ -->
    <div v-if="!job" class="ppt-form" data-test="ppt-form">
      <div class="ppt-field">
        <label class="ppt-label">输入方式</label>
        <div class="ppt-seg">
          <button type="button" :class="{ on: ppt.form.mode === 'pdf' }"
                  data-test="mode-pdf" @click="ppt.form.mode = 'pdf'">上传 PDF</button>
          <button type="button" :class="{ on: ppt.form.mode === 'text' }"
                  data-test="mode-text" @click="ppt.form.mode = 'text'">粘贴文本</button>
        </div>
      </div>

      <div v-if="ppt.form.mode === 'pdf'" class="ppt-field">
        <label class="ppt-label">论文 PDF（不超过 50 MB）</label>
        <input type="file" accept=".pdf,application/pdf" data-test="file-input" @change="onFileChange" />
        <span v-if="ppt.form.file" class="ppt-file">{{ ppt.form.file.name }}</span>
      </div>
      <div v-else class="ppt-field">
        <label class="ppt-label">论文正文（≥300 字）</label>
        <textarea v-model="ppt.form.text" rows="6" data-test="text-input"
                  placeholder="粘贴论文纯文本（标题/摘要/正文/参考文献）" />
      </div>

      <div class="ppt-field">
        <label class="ppt-label">汇报场景</label>
        <select v-model="ppt.form.scenario" data-test="scenario-select">
          <option v-for="s in SCENARIOS" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
      </div>

      <div class="ppt-field">
        <label class="ppt-label">汇报时长</label>
        <div class="ppt-seg">
          <button v-for="m in MINUTES" :key="m.v" type="button"
                  :class="{ on: ppt.form.minutes === m.v }"
                  :data-test="`minutes-${m.v}`"
                  @click="ppt.form.minutes = m.v">
            {{ m.v }} 分钟<span class="min-pages">{{ m.pages }}</span>
          </button>
        </div>
      </div>

      <div class="ppt-field">
        <label class="ppt-label">风格模板（卡面为该风格真实版式封面）</label>
        <div class="ppt-styles">
          <button v-for="s in styleCards" :key="s.id" type="button"
                  class="ppt-style-card" :class="[s.id, { on: ppt.form.styleId === s.id }]"
                  :data-test="`style-${s.id}`" @click="ppt.form.styleId = s.id">
            <img class="ppt-style-cover" :src="styleCover(s.id)" :alt="`${s.label} 版式封面`"
                 :data-test="`cover-${s.id}`" />
            <span class="ppt-style-meta">
              <span class="ppt-style-name">{{ s.label }}</span>
              <span class="ppt-style-desc">{{ s.desc }}</span>
            </span>
          </button>
        </div>
      </div>

      <p v-if="ppt.status === 'error' && ppt.error" class="ppt-error" data-test="form-error">
        {{ ppt.error }}
      </p>

      <div class="ppt-actions">
        <button type="button" class="ppt-primary" data-test="generate"
                :disabled="isBusy" @click="onGenerate">
          {{ ppt.status === 'loading' ? '大纲生成中（约 1-3 分钟）…' : '生成大纲' }}
        </button>
        <span class="ppt-hint">目标 {{ pageTarget }}（超上限自动裁剪，不足不凑页）</span>
      </div>
    </div>

    <!-- ============ ② 编辑器（有 job） ============ -->
    <div v-else class="ppt-editor" data-test="ppt-editor">
      <div class="ppt-toolbar">
        <span class="ppt-chip">{{ SCENARIOS.find(s => s.key === job.scenario)?.label || '科研汇报' }} · {{ job.minutes }} 分钟</span>
        <span class="ppt-chip">{{ styleCards.find(s => s.id === job.style_id)?.label || '汇报样式' }}</span>
        <span class="ppt-chip" data-test="page-count">{{ localPages.length }} 页</span>
        <span v-if="ppt.createMeta && ppt.createMeta.target_pages" class="ppt-chip">
          目标 {{ ppt.createMeta.target_pages[0] }}-{{ ppt.createMeta.target_pages[1] }} 页
        </span>
        <span v-if="figsExtracted" class="ppt-chip" data-test="figs-chip">
          提取原图 {{ figsExtracted }} 张
        </span>
        <span v-if="evStats.total" class="ppt-chip" data-test="ev-stats">
          证据 {{ evStats.ok }}/{{ evStats.total }} 已核实
        </span>
        <span class="ppt-spacer" />
        <button type="button" data-test="save" :disabled="!dirty || ppt.saving"
                :title="dirty ? '保存编辑' : '无修改'" @click="onSave">
          {{ ppt.saving ? '保存中…' : dirty ? '保存' : '已保存' }}
        </button>
        <button type="button" class="ppt-primary" data-test="build"
                :disabled="isBusy" @click="onBuild">
          {{ ppt.status === 'building' ? '生成中…' : '生成 PPT' }}
        </button>
        <a v-if="job.pptx_ready" class="ppt-dl" data-test="download"
           :href="pptDownloadUrl(job.id)" download>下载 PPTX</a>
        <button type="button" data-test="regenerate" :disabled="isBusy" @click="onReset">重新生成</button>
        <button type="button" class="ppt-danger" data-test="delete" :disabled="isBusy" @click="onDelete">删除</button>
      </div>

      <p v-if="ppt.error" class="ppt-error" data-test="editor-error">
        {{ ppt.error }}
      </p>
      <p v-if="dirty" class="ppt-dirty" data-test="dirty-hint">有未保存修改（生成 PPT 前会自动保存）</p>

      <details v-if="job.warnings && job.warnings.length" class="ppt-warnings" data-test="warnings" open>
        <summary>生成提示（{{ job.warnings.length }}）</summary>
        <ul>
          <li v-for="(w, i) in job.warnings" :key="i">{{ warningText(w) }}</li>
        </ul>
      </details>

      <!-- 3.x 页面预览：build 后逐页缩略图（preview/{page}.png 直链，失败单张隐藏） -->
      <div v-if="previewCount" class="ppt-previews" data-test="preview-wrap">
        <div class="ppt-preview-main-wrap">
          <img class="ppt-preview-main" :src="previewSrc(activePreview)"
               :data-test="`preview-main-${activePreview}`" :alt="`第 ${activePreview} 页预览`"
               @error="onPreviewError(activePreview)" />
        </div>
        <div class="ppt-preview-thumbs" data-test="preview-strip">
          <button v-for="p in previewPages" :key="p" type="button"
                  class="ppt-preview-thumb" :class="{ on: p === activePreview }"
                  :data-test="`thumb-${p}`" :title="`第 ${p} 页`"
                  @click="activePreview = p">
            <img :src="previewSrc(p)" :alt="`第 ${p} 页`" loading="lazy" @error.stop="onPreviewError(p)" />
          </button>
        </div>
      </div>

      <ol class="ppt-pages">
        <li v-for="(page, pi) in localPages" :key="pi" class="ppt-page" :class="`tp-${page.type}`"
            data-test="page-card">
          <div class="ppt-page-head">
            <span class="ppt-page-no">{{ page.slide_number }}</span>
            <span class="ppt-type" data-test="page-type">{{ TYPE_LABELS[page.type] || page.type }}</span>
            <input v-model="page.title" class="ppt-page-title" data-test="page-title"
                   placeholder="页标题" @input="touch" />
          </div>

          <!-- 分隔页只暴露副标题（渲染端 40pt 主标题 + 20pt 副标题，见 ppt_build._add_section_page） -->
          <div v-if="page.type === 'section'" class="ppt-sub-row">
            <input :value="page.bullets[0] || page.subtitle || ''" class="ppt-page-subtitle"
                   data-test="page-subtitle" placeholder="副标题（默认取该章第一页标题）"
                   @input="onSectionSubtitle(pi, $event)" />
          </div>
          <template v-else>
            <ul class="ppt-bullets">
              <li v-for="(b, bi) in page.bullets" :key="bi" class="ppt-bullet-row">
                <span class="ppt-ev" :class="badgeClass(page, bi)" data-test="ev-badge"
                      :title="badgeTitle(page, bi)" />
                <input v-model="page.bullets[bi]" class="ppt-bullet-input" :data-test="`bullet-${pi}-${bi}`"
                       placeholder="要点" @input="touch" />
                <button type="button" class="ppt-x" :data-test="`rm-${pi}-${bi}`"
                        title="删除要点" @click="removeBullet(pi, bi)">×</button>
              </li>
            </ul>
            <button type="button" class="ppt-add" :data-test="`add-${pi}`" @click="addBullet(pi)">+ 要点</button>
          </template>

          <div v-if="page.suggested_figures && page.suggested_figures.length" class="ppt-figs">
            <span v-for="(f, fi) in page.suggested_figures" :key="fi" class="ppt-fig"
                  :class="f.status === 'verified' ? 'ev-ok' : 'ev-bad'"
                  :data-test="`fig-${pi}-${fi}`"
                  :title="`论文真实图引用 ${f.para_id}（${f.status === 'verified' ? '已核实' : '未核实'}）`">
              {{ f.label || '图' }}（论文 {{ f.para_id }}）
            </span>
          </div>

          <textarea v-model="page.speaker_notes" class="ppt-notes" rows="2"
                    :data-test="`notes-${pi}`" placeholder="讲稿备注（不进正文；证据来源自动生成在备注尾部）"
                    @input="touch" />
        </li>
      </ol>

      <p class="ppt-foot">
        e 徽标：<span class="ppt-ev ev-ok" /> 已核实 · <span class="ppt-ev ev-bad" /> 未核实
        （保存后按原文重新校验） · <span class="ppt-ev ev-none" /> 无来源
        <template v-if="ppt.buildMeta"> · 最近生成 {{ ppt.buildMeta.slides }} 页 / {{ Math.round(ppt.buildMeta.bytes / 1024) }}KB</template>
      </p>
    </div>
  </section>
</template>

<style scoped>
/* tokens 全走 tokens.css（--pa-ink/--pa-surface-N/--pa-hairline/--pa-*）；
   风格选择卡的三色板见 ppt-styles.css（与 data/ppt_styles/*.json 同步）。 */
.ppt-root { display: flex; flex-direction: column; gap: 14px; padding: 18px 22px; max-width: 1280px; color: var(--pa-ink); }
.ppt-title { font-size: 20px; margin: 0; color: var(--pa-ink); }
.ppt-sub { color: var(--pa-ink-muted); font-size: 13px; margin: 4px 0 0; }
.ppt-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }

.ppt-status {
  font-size: 12px; padding: 4px 12px; border-radius: var(--pa-r-pill);
  border: 1px solid var(--pa-hairline); background: var(--pa-surface-2); color: var(--pa-ink-muted);
}
.ppt-status.st-error { color: var(--pa-error, #c2410c); border-color: currentColor; }
.ppt-status.st-done { color: var(--pa-success, #16a34a); border-color: currentColor; }

.ppt-form, .ppt-editor {
  background: var(--pa-surface-1); border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md, 12px); padding: 16px 18px;
  display: flex; flex-direction: column; gap: 14px;
}
.ppt-field { display: flex; flex-direction: column; gap: 6px; }
.ppt-label { font-size: 13px; font-weight: 600; color: var(--pa-ink); }
.ppt-seg { display: flex; gap: 8px; flex-wrap: wrap; }
.ppt-seg button {
  padding: 6px 14px; border-radius: var(--pa-r-pill, 999px); font-size: 13px;
  border: 1px solid var(--pa-hairline); background: var(--pa-surface-2); color: var(--pa-ink); cursor: pointer;
}
.ppt-seg button.on { background: var(--pa-gradient, linear-gradient(135deg, #2563eb, #7c3aed)); color: #fff; border-color: transparent; font-weight: 600; }
.min-pages { font-size: 11px; opacity: 0.85; margin-left: 6px; }
.ppt-styles { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
.ppt-file { font-size: 12px; color: var(--pa-ink-muted); }
.ppt-hint { font-size: 12px; color: var(--pa-ink-subtle); align-self: center; }
.ppt-actions { display: flex; gap: 10px; align-items: center; }

.ppt-primary {
  padding: 8px 18px; border-radius: var(--pa-r-pill, 999px); border: none; cursor: pointer;
  background: var(--pa-gradient, linear-gradient(135deg, #2563eb, #7c3aed)); color: #fff; font-weight: 600; font-size: 14px;
}
.ppt-primary:disabled { opacity: 0.55; cursor: not-allowed; }
.ppt-danger { color: var(--pa-error, #c2410c); }
.ppt-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ppt-toolbar button, .ppt-dl {
  font-size: 13px; padding: 6px 12px; border-radius: var(--pa-r-pill, 999px);
  border: 1px solid var(--pa-hairline); background: var(--pa-surface-2); color: var(--pa-ink);
  cursor: pointer; text-decoration: none;
}
.ppt-toolbar button:disabled { opacity: 0.5; cursor: not-allowed; }
.ppt-spacer { flex: 1; }
.ppt-chip {
  font-size: 12px; padding: 3px 10px; border-radius: var(--pa-r-pill, 999px);
  background: var(--pa-surface-2); border: 1px solid var(--pa-hairline); color: var(--pa-ink-muted);
}

.ppt-error { color: var(--pa-error, #c2410c); font-size: 13px; margin: 0; }
.ppt-dirty { color: #b45309; font-size: 12px; margin: 0; }
.ppt-warnings { font-size: 13px; color: #92400e; background: #fef3c722; border: 1px solid #f59e0b66; border-radius: 8px; padding: 6px 12px; }
.ppt-warnings summary { cursor: pointer; font-weight: 600; }
.ppt-warnings ul { margin: 6px 0 2px; padding-left: 18px; }
.ppt-warnings li { margin: 2px 0; }

/* 页面预览（3.x）：大图 + 缩略图条 */
.ppt-previews {
  display: flex; flex-direction: column; gap: 8px;
  border: 1px solid var(--pa-hairline); border-radius: 10px;
  background: var(--pa-surface-2); padding: 10px 12px;
}
.ppt-preview-main-wrap {
  display: flex; justify-content: center;
  background: #0000000d; border-radius: 8px; padding: 8px;
}
.ppt-preview-main {
  max-width: 100%; width: 720px; max-height: 420px; object-fit: contain;
  border: 1px solid var(--pa-hairline); border-radius: 4px; background: #fff;
}
.ppt-preview-thumbs { display: flex; gap: 6px; flex-wrap: wrap; }
.ppt-preview-thumb {
  padding: 0; border: 2px solid var(--pa-hairline); border-radius: 6px;
  background: var(--pa-surface-1); cursor: pointer; overflow: hidden;
  width: 96px; height: 54px; line-height: 0;
}
.ppt-preview-thumb.on { border-color: var(--pa-accent, #2563eb); box-shadow: 0 0 0 2px #2563eb33; }
.ppt-preview-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }

.ppt-pages { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.ppt-page {
  border: 1px solid var(--pa-hairline); border-left: 4px solid var(--pa-accent, #2563eb);
  border-radius: 10px; background: var(--pa-surface-2); padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.ppt-page.tp-title { border-left-color: #7c3aed; }
.ppt-page.tp-outline { border-left-color: #0891b2; }
.ppt-page.tp-method { border-left-color: #2563eb; }
.ppt-page.tp-result { border-left-color: #16a34a; }
.ppt-page.tp-conclusion { border-left-color: #b45309; }
.ppt-page.tp-reference { border-left-color: #6b7280; }
.ppt-page-head { display: flex; align-items: center; gap: 8px; }
.ppt-page-no {
  width: 22px; height: 22px; border-radius: 50%; font-size: 11px; font-weight: 700;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--pa-surface-1); border: 1px solid var(--pa-hairline); color: var(--pa-ink-muted); flex-shrink: 0;
}
.ppt-type { font-size: 12px; color: var(--pa-ink-muted); min-width: 42px; }
.ppt-page-title { flex: 1; font-size: 14px; font-weight: 600; padding: 4px 8px; border-radius: 6px;
  border: 1px solid transparent; background: transparent; color: var(--pa-ink); }
.ppt-page-title:hover, .ppt-page-title:focus { border-color: var(--pa-hairline); background: var(--pa-surface-1); }

.ppt-sub-row { display: flex; }
.ppt-page-subtitle {
  flex: 1; font-size: 13px; padding: 4px 8px; border-radius: 6px;
  border: 1px dashed var(--pa-hairline); background: transparent; color: var(--pa-ink-muted);
}
.ppt-page-subtitle:hover, .ppt-page-subtitle:focus {
  border-style: solid; border-color: var(--pa-hairline); background: var(--pa-surface-1); color: var(--pa-ink);
}

.ppt-bullets { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.ppt-bullet-row { display: flex; align-items: center; gap: 6px; }
.ppt-ev {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; display: inline-block;
  border: 1px solid var(--pa-hairline);
}
.ppt-ev.ev-ok { background: var(--pa-success, #16a34a); border-color: transparent; }
.ppt-ev.ev-bad { background: #f59e0b; border-color: transparent; }
.ppt-ev.ev-none { background: transparent; border-color: var(--pa-ink-subtle, #9ca3af); }
.ppt-bullet-input {
  flex: 1; font-size: 13px; padding: 4px 8px; border-radius: 6px;
  border: 1px solid transparent; background: transparent; color: var(--pa-ink);
}
.ppt-bullet-input:hover, .ppt-bullet-input:focus { border-color: var(--pa-hairline); background: var(--pa-surface-1); }
.ppt-x { border: none; background: transparent; color: var(--pa-ink-subtle); cursor: pointer; font-size: 14px; padding: 0 4px; }
.ppt-x:hover { color: var(--pa-error, #c2410c); }
.ppt-add {
  align-self: flex-start; font-size: 12px; color: var(--pa-ink-muted); cursor: pointer;
  border: 1px dashed var(--pa-hairline); background: transparent; border-radius: 6px; padding: 2px 10px;
}
.ppt-figs { display: flex; gap: 6px; flex-wrap: wrap; }
.ppt-fig {
  font-size: 11px; padding: 2px 8px; border-radius: var(--pa-r-pill, 999px);
  background: var(--pa-surface-1); border: 1px solid var(--pa-hairline); color: var(--pa-ink-muted);
}
.ppt-notes {
  font-size: 12px; padding: 6px 8px; border-radius: 6px; resize: vertical;
  border: 1px solid var(--pa-hairline); background: var(--pa-surface-1); color: var(--pa-ink-muted);
}
.ppt-foot { font-size: 12px; color: var(--pa-ink-subtle); margin: 0; }
input, textarea, select { font-family: inherit; color-scheme: light dark; }
select { padding: 5px 8px; border-radius: 6px; border: 1px solid var(--pa-hairline); background: var(--pa-surface-2); color: var(--pa-ink); max-width: 320px; }
</style>
