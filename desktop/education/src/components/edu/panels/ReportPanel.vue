<!-- ReportPanel.vue —— ④ 专业审稿意见报告（读者视角，正式报告版式）。
     与 PresubmitPanel（作者视角·修改工作台）共用同一份 edu.report
     （{ok, report{summary, major/minor_weaknesses[], presentation_format[],
      questions_for_authors[], recommendation}, meta{filename,target_journal,text_chars,truncated,model}}），
     生成时间取 edu.reportCreatedAt（store 盖章，payload 无时间字段）。
     无报告 → 引导去 section='presubmit'。
     工具栏：导出 PDF = window.print()（打印时 :global 隐藏 .edu-sidenav/.top-bar/.rp-toolbar）；
     复制 Markdown = clipboard API（安全上下文）→ execCommand 兜底（LAN http:// 非安全上下文）。
     不动 presubmit 后端 / self_review.py / 六维 Prompt，纯前端只读展示。 -->
<script setup>
import { computed, onUnmounted, ref } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

const REC = {
  accept: { label: '接收', cls: 'ok' },
  minor_revision: { label: '小修', cls: 'minor' },
  major_revision: { label: '大修', cls: 'major' },
  reject: { label: '拒稿', cls: 'reject' },
};

const toast = ref(false);
let toastTimer = null;

const report = computed(() => edu.report?.report || null);
const meta = computed(() => edu.report?.meta || null);
const rec = computed(() => REC[report.value?.recommendation] || REC.major_revision);
const createdAtText = computed(() => {
  const s = edu.reportCreatedAt;
  // ISO → "10-12 15:25" 短格式（不裸露完整时间戳）
  return s ? String(s).slice(5, 16).replace('T', ' ') : '—';
});

/* 五节结构：1 总体评价（summary 单列）+ 2..5 列表节（questions 映射为 {title} 条目） */
const sections = computed(() => {
  const r = report.value;
  if (!r) return [];
  return [
    { title: '主要问题', items: r.major_weaknesses || [] },
    { title: '次要问题', items: r.minor_weaknesses || [] },
    { title: '排版与规范', items: r.presentation_format || [] },
    {
      title: '给作者的问题',
      items: (r.questions_for_authors || []).map((q) => ({ title: q, detail: '', evidence: '', suggestion: '' })),
    },
  ];
});

function exportPdf() {
  window.print();
}

function buildMarkdown() {
  const r = report.value;
  if (!r) return '';
  const m = meta.value || {};
  const lines = [
    '# AI 预审稿报告',
    '',
    `- 文件：${m.filename || '—'}`,
    `- 目标期刊：${m.target_journal || '未指定'}`,
    `- 生成时间：${createdAtText.value}`,
    `- 推荐决定：${rec.value.label}（${r.recommendation}）`,
    '',
    '## 总体评价',
    '',
    r.summary || '无',
    '',
  ];
  const weakSec = (title, items) => {
    lines.push(`## ${title}`, '');
    if (!items || !items.length) {
      lines.push('无', '');
      return;
    }
    items.forEach((it, i) => {
      lines.push(`${i + 1}. ${it.title}`, '');
      if (it.detail) lines.push(it.detail, '');
      if (it.evidence) lines.push(`   - 证据：${it.evidence}`);
      if (it.suggestion) lines.push(`   - 建议：${it.suggestion}`);
      lines.push('');
    });
  };
  weakSec('主要问题', r.major_weaknesses);
  weakSec('次要问题', r.minor_weaknesses);
  weakSec('排版与规范', r.presentation_format);
  lines.push('## 给作者的问题', '');
  const qs = r.questions_for_authors || [];
  if (!qs.length) lines.push('无', '');
  else qs.forEach((q, i) => lines.push(`${i + 1}. ${q}`));
  lines.push('', '---', `*正文 ${m.text_chars ?? '—'} 字符${m.truncated ? ' · 已截断' : ''}*`);
  return lines.join('\n');
}

function showToast() {
  toast.value = true;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = false), 1600);
}

async function copyMarkdown() {
  const text = buildMarkdown();
  if (!text) return;
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch {
    /* fallthrough → execCommand 兜底 */
  }
  if (!ok) {
    // LAN http:// 非安全上下文 navigator.clipboard 不可用 → 隐藏 textarea + execCommand
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      ta.remove();
    } catch {
      ok = false;
    }
  }
  if (ok) showToast();
}

onUnmounted(() => clearTimeout(toastTimer));
</script>

<template>
  <section class="rp">
    <!-- ========== ① 无报告 → 引导 ========== -->
    <div v-if="!edu.hasReport" class="rp-guide">
      <div class="rp-guide-icon" aria-hidden="true">📝</div>
      <p class="rp-guide-text">尚无审稿报告。先上传 PDF 运行 AI 预审稿，生成后在此查看正式报告。</p>
      <button class="rp-guide-btn" type="button" @click="edu.setSection('presubmit')">
        去预审稿 →
      </button>
    </div>

    <!-- ========== ② 有报告 → 正式报告（读者视角） ========== -->
    <template v-else>
      <div class="rp-toolbar">
        <button class="rp-tool" type="button" @click="exportPdf">📥 导出 PDF</button>
        <button class="rp-tool" type="button" @click="copyMarkdown">📋 复制为 Markdown</button>
        <transition name="rp-fade">
          <span v-if="toast" class="rp-toast" role="status">已复制</span>
        </transition>
      </div>

      <article class="rp-sheet">
        <header class="rp-head">
          <h1 class="rp-title">AI 预审稿报告</h1>
          <div class="rp-headmeta">
            <span class="rp-meta-line">
              📄 {{ meta?.filename || '—' }}
              <template v-if="meta?.target_journal"> · 目标期刊 {{ meta.target_journal }}</template>
              · 生成于 {{ createdAtText }}
            </span>
            <span class="rp-rec" :class="rec.cls">{{ rec.label }} · {{ report.recommendation }}</span>
          </div>
        </header>

        <!-- 1. 总体评价 -->
        <section class="rp-sec">
          <h2 class="rp-h2">1. 总体评价</h2>
          <p class="rp-summary">{{ report.summary || '无' }}</p>
        </section>

        <!-- 2..5 列表节 -->
        <section v-for="(s, si) in sections" :key="s.title" class="rp-sec">
          <h2 class="rp-h2">{{ si + 2 }}. {{ s.title }}（{{ s.items.length }}）</h2>
          <div v-for="(it, i) in s.items" :key="i" class="rp-item">
            <p class="rp-item-title">{{ i + 1 }}. {{ it.title }}</p>
            <p v-if="it.detail" class="rp-item-detail">{{ it.detail }}</p>
            <p v-if="it.evidence" class="rp-item-evi"><b>证据：</b>{{ it.evidence }}</p>
            <p v-if="it.suggestion" class="rp-item-sug"><b>建议：</b>{{ it.suggestion }}</p>
          </div>
          <p v-if="!s.items.length" class="rp-none">无</p>
        </section>

        <footer class="rp-foot">
          正文 {{ meta?.text_chars ?? '—' }} 字符<template
            v-if="meta?.truncated"
            > · 已截断至 6 万字符</template
          > · 报告生成于 {{ createdAtText }}
        </footer>
      </article>
    </template>
  </section>
</template>

<style scoped>
.rp {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ---------- 引导空态 ---------- */
.rp-guide {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 56px 24px;
  text-align: center;
  border: 1px dashed var(--pa-hairline);
  border-radius: var(--pa-r-lg);
}
.rp-guide-icon {
  font-size: 30px;
}
.rp-guide-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pa-ink-muted);
  max-width: 420px;
}
.rp-guide-btn {
  padding: 8px 22px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-gradient);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.rp-guide-btn:hover {
  filter: brightness(1.08);
}

/* ---------- 工具栏 ---------- */
.rp-toolbar {
  position: relative;
  display: flex;
  gap: 10px;
}
.rp-tool {
  padding: 7px 16px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-1);
  color: var(--pa-ink);
  font-size: 12.5px;
  cursor: pointer;
}
.rp-tool:hover {
  background: var(--pa-surface-2);
  border-color: var(--pa-hairline-strong);
}
.rp-toast {
  position: absolute;
  left: 50%;
  top: -8px;
  transform: translate(-50%, -100%);
  padding: 5px 14px;
  border-radius: var(--pa-r-pill);
  background: var(--pa-raised);
  border: 1px solid var(--pa-success);
  color: var(--pa-success);
  font-size: 12px;
  pointer-events: none;
}
.rp-fade-enter-active,
.rp-fade-leave-active {
  transition: opacity .2s var(--pa-ease);
}
.rp-fade-enter-from,
.rp-fade-leave-to {
  opacity: 0;
}

/* ---------- 纸面版式：等宽正式报告，左右留白宽、行距松 ---------- */
.rp-sheet {
  max-width: 760px;
  width: 100%;
  margin: 0 auto;
  padding: 40px 56px;
  box-sizing: border-box;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-lg);
  font-family: var(--pa-font-mono);
  color: var(--pa-ink);
  line-height: 1.85;
}
.rp-head {
  padding-bottom: 16px;
  border-bottom: 2px solid var(--pa-hairline-strong);
}
.rp-title {
  margin: 0 0 10px;
  font-family: var(--pa-font-mono);
  font-size: 19px;
  font-weight: 700;
  letter-spacing: .04em;
}
.rp-headmeta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.rp-meta-line {
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.rp-rec {
  display: inline-flex;
  padding: 3px 14px;
  border: 1px solid currentColor;
  border-radius: var(--pa-r-pill);
  font-size: 12.5px;
  font-weight: 700;
  white-space: nowrap;
}
.rp-rec.ok { color: var(--pa-success); }
.rp-rec.minor { color: var(--pa-info); }
.rp-rec.major { color: var(--pa-warning); }
.rp-rec.reject { color: var(--pa-error); }

.rp-sec {
  margin-top: 26px;
}
.rp-h2 {
  margin: 0 0 12px;
  font-family: var(--pa-font-mono);
  font-size: 14.5px;
  font-weight: 700;
  color: var(--pa-ink);
  letter-spacing: .03em;
}
.rp-summary {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.95;
  color: var(--pa-ink);
}
.rp-item {
  padding: 13px 0;
  border-bottom: 1px solid var(--pa-hairline);
}
.rp-item:last-child {
  border-bottom: none;
}
.rp-item-title {
  margin: 0 0 6px;
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.7;
  color: var(--pa-ink);
}
.rp-item-detail {
  margin: 0;
  font-size: 13px;
  line-height: 1.9;
  color: var(--pa-ink);
}
.rp-item-evi {
  margin: 9px 0 0;
  padding-left: 13px;
  border-left: 3px solid var(--pa-hairline-strong);
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--pa-ink-muted);
}
.rp-item-sug {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--pa-ink-muted);
}
.rp-item-evi b,
.rp-item-sug b {
  color: var(--pa-ink);
  font-weight: 700;
}
.rp-none {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-subtle);
}
.rp-foot {
  margin-top: 30px;
  padding-top: 14px;
  border-top: 1px dashed var(--pa-hairline);
  font-family: var(--pa-font-mono);
  font-size: 11.5px;
  color: var(--pa-ink-subtle);
}

/* ---------- 打印（导出 PDF = window.print）：只留报告纸面 ----------
   注：打印态下 #fff/#000/#bbb 为硬编码（仅 @media print 生效）——
   屏幕用暗色主题变量直接打印会输出大面积黑底，故打印强制白底黑字。 */
@media print {
  :global(.edu-sidenav),
  :global(.top-bar),
  .rp-toolbar {
    display: none !important;
  }
  /* 2026-10-12 修"导出只导一页"：祖先链 height:100vh + overflow:auto 会把打印
     内容裁在视口第一页——打印态整链展开为正常文档流，让浏览器自然分页。 */
  :global(.edu-workspace) {
    height: auto !important;
    display: block !important;
  }
  :global(.edu-panel) {
    height: auto !important;
    overflow: visible !important;
    display: block !important;
    padding: 0 !important;
  }
  .rp-sheet {
    max-width: none;
    padding: 0;
    border: none;
    background: #fff;
  }
  .rp-sheet * {
    color: #000 !important;
    background: transparent !important;
    border-color: #bbb !important;
  }
}
</style>
