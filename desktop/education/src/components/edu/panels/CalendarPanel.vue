<!-- CalendarPanel.vue —— ⑦' 会议与投稿日历（会议时间线；步骤 3.1）。
     数据链：onMounted → edu.fetchConferences({domain, ccf, upcoming_days}) →
     review.js getConferences() → GET /api/review/conferences
     （过滤/排序在后端：domain 对 domain_tags 精确匹配、ccf ∈ A/B/C、
      upcoming_days=180 窗口 / 0=不过滤；按 deadline_full_paper 升序）。
     数据诚实性铁律：deadline_source=placeholder 的会议挂「⚠ 日期待核验」黄徽章，
      并附 deadline_confidence 置信度（high=官方已公布/历年极稳定，medium=按历年
      规律推算，low=待核验）；领域标签按族着色：AI/ML 蓝 / 生物信息学 绿 /
      植物·农学 黄 / 交叉·健康医疗 紫。面板不生成任何日期，倒计时仅由种子 ISO
      时间与本机时钟相减得出。
      倒计时色阶：≤0 灰「已结束」/ ≤7 红 / 8–30 橙 / >30 绿。
      筛选为 panel-local refs（不进 store/drafts）；每次进面板按当前筛选重查
      （27 条静态种子，成本可忽略，保证筛选 UI 与列表一致）。
     空态文案：「暂无可投会议，或数据待核验」。样式 cl- 前缀。 -->
<script setup>
import {et, educationLocale} from '../../../i18n.js';
import { computed, onMounted, ref } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

/* —— 筛选（panel-local）—— */
const domain = ref('');
const ccf = ref('');
const upcomingOnly = ref(true); // 默认开 → upcoming_days=180；关 → 0（含已过期）

const CCF_OPTIONS = ['A', 'B', 'C'];
/* 领域下拉从已加载条目累积（切筛选不清空，避免选项随过滤缩水） */
const domainOptions = ref([]);

function noteDomains(items) {
  for (const c of items || []) {
    for (const t of (c.domain_tags || [])) {
      if (!domainOptions.value.includes(t)) domainOptions.value.push(t);
    }
  }
}

function reload() {
  edu
    .fetchConferences({
      domain: domain.value,
      ccf: ccf.value,
      upcoming_days: upcomingOnly.value ? 180 : 0,
    })
    .then(payload => noteDomains(payload?.items));
}

onMounted(() => {
  // loading = 切菜单期间请求在飞（store 持有，不重发）；其余状态按当前筛选重查
  if (edu.conferencesStatus !== 'loading') reload();
  else noteDomains(edu.conferences);
});

/* —— 倒计时（本机时钟计算；后端只负责窗口过滤）—— */
function countdown(iso) {
  if (!iso) return { cls: 'ended', text: '日期缺失' };
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { cls: 'ended', text: '日期缺失' };
  const d = Math.ceil((t - Date.now()) / 86400000);
  if (d <= 0) return { cls: 'ended', text: '已结束' };
  if (d <= 7) return { cls: 'red', text: `还有 ${d} 天` };
  if (d <= 30) return { cls: 'orange', text: `还有 ${d} 天` };
  return { cls: 'green', text: `还有 ${d} 天` };
}

const day = (iso) => (iso ? String(iso).slice(0, 10) : '—');
const isPlaceholder = (c) => c.deadline_source === 'placeholder';

/* —— 置信度：deadline_confidence → 中文（徽章文案/悬浮提示）—— */
const CONF_LABEL = { high: '高', medium: '中', low: '低' };
const confLabel = (v) => CONF_LABEL[String(v || '').toLowerCase()] || '—';
const confTitle = (v) =>
  et(`日期未经官方核验；置信度：${et(confLabel(v))}`) +
  et('（请以会议官网公布的日期为准）');

/* —— 领域标签族着色：ai=蓝 / bio=绿 / plant=黄 / cross=紫 —— */
const DOMAIN_FAMILY = {
  'AI/ML': 'ai', '深度学习': 'ai', '计算机视觉': 'ai', '自然语言处理': 'ai',
  '数据挖掘': 'ai', '信息检索': 'ai',
  '生物信息学': 'bio', '医学影像': 'bio',
  '植物/农学': 'plant', '基因组学': 'plant', '分子生物学': 'plant',
  '健康医疗': 'cross',
};
const famClass = (t) => `cl-chip--${DOMAIN_FAMILY[t] || 'ai'}`;
const appClass = (t) => (t === '交叉学科' ? 'cl-chip--cross' : 'cl-chip--app');

const count = computed(() => edu.conferences.length);
</script>

<template>
  <section class="cl">
    <!-- 头部 -->
    <header class="cl-head">
      <h1 class="cl-title">{{ et("会议与投稿日历") }}</h1>
      <span v-if="edu.conferencesStatus === 'ready'" class="cl-count">{{ et("共 ") }}{{ count }}{{ et(" 个会议") }}</span>
    </header>

    <!-- 时区说明（固定一行）+ 诚实性提示 -->
    <p class="cl-tz">{{ et("所有时间按 AoE (UTC-12) 时区 · 未经核验的日期以 ⚠ 标注并附置信度") }}</p>

    <!-- 筛选栏 -->
    <div class="cl-filters">
      <label class="cl-field">
        <span class="cl-field-label">{{ et("领域") }}</span>
        <select v-model="domain" class="cl-select" @change="reload">
          <option value="">{{ et("全部领域") }}</option>
          <option v-for="d in domainOptions" :key="d" :value="d">{{ et(d) }}</option>
        </select>
      </label>
      <label class="cl-field">
        <span class="cl-field-label">CCF</span>
        <select v-model="ccf" class="cl-select" @change="reload">
          <option value="">{{ et("全部等级") }}</option>
          <option v-for="r in CCF_OPTIONS" :key="r" :value="r">CCF-{{ r }}</option>
        </select>
      </label>
      <label class="cl-check">
        <input v-model="upcomingOnly" type="checkbox" @change="reload" />{{ et(" 仅显示最近可投 ") }}</label>
    </div>

    <!-- ERROR -->
    <div v-if="edu.conferencesStatus === 'error'" class="cl-state" role="alert">
      <p class="cl-state-msg">{{ et(edu.conferencesError) }}</p>
      <button class="cl-retry" type="button" @click="reload">{{ et("重试") }}</button>
    </div>

    <!-- LOADING -->
    <div v-else-if="edu.conferencesStatus === 'loading'" class="cl-state" aria-busy="true">
      <div v-for="n in 3" :key="n" class="cl-skel"></div>
      <p class="cl-state-msg">{{ et("会议数据加载中…") }}</p>
    </div>

    <!-- READY + 空态 -->
    <div v-else-if="!edu.conferences.length" class="cl-state">
      <p class="cl-state-msg">{{ et("暂无可投会议，或数据待核验") }}</p>
    </div>

    <!-- READY + 列表（后端已按 deadline_full_paper 升序） -->
    <ul v-else class="cl-list">
      <li v-for="c in edu.conferences" :key="c.id" class="cl-card">
        <div class="cl-row1">
          <span class="cl-name">{{ c.name }}</span>
          <span v-if="c.ccf_rank" class="cl-chip cl-chip--ccf">CCF-{{ c.ccf_rank }}</span>
          <span class="cl-cd" :class="`cl-cd--${countdown(c.deadline_full_paper).cls}`">
            {{ et(countdown(c.deadline_full_paper).text) }}
          </span>
          <span v-if="isPlaceholder(c)" class="cl-chip cl-chip--warn" :title="et(confTitle(c.deadline_confidence))">{{ et(" ⚠ 日期待核验（置信度：") }}{{ et(confLabel(c.deadline_confidence)) }}）
          </span>
        </div>

        <p class="cl-full">{{ c.full_name }}</p>

        <div class="cl-tags">
          <span v-for="t in c.domain_tags" :key="`d-${t}`" class="cl-chip cl-chip--tag" :class="famClass(t)">{{ et(t) }}</span>
          <span v-for="t in c.application_tags" :key="`a-${t}`" class="cl-chip cl-chip--tag" :class="appClass(t)">{{ et(t) }}</span>
        </div>

        <dl class="cl-meta">
          <div class="cl-meta-item">
            <dt>{{ et("截稿（摘要）") }}</dt>
            <dd>{{ day(c.deadline_abstract) }}</dd>
          </div>
          <div class="cl-meta-item">
            <dt>{{ et("截稿（全文）") }}</dt>
            <dd class="cl-deadline">{{ day(c.deadline_full_paper) }}</dd>
          </div>
          <div class="cl-meta-item">
            <dt>{{ et("通知日") }}</dt>
            <dd>{{ day(c.notification_date) }}</dd>
          </div>
          <div class="cl-meta-item">
            <dt>{{ et("会期") }}</dt>
            <dd>{{ c.conference_date || '—' }}</dd>
          </div>
          <div class="cl-meta-item">
            <dt>{{ et("地点") }}</dt>
            <dd>{{ c.location || '—' }}</dd>
          </div>
          <div class="cl-meta-item">
            <dt>{{ et("官网") }}</dt>
            <dd>
              <a v-if="c.official_url" class="cl-link" :href="c.official_url" target="_blank" rel="noopener">
                {{ String(c.official_url).replace(/^https?:\/\//, '').replace(/\/$/, '') }}
              </a>
              <span v-else>—</span>
            </dd>
          </div>
        </dl>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.cl {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cl-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.cl-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.cl-count {
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.cl-tz {
  margin: 0;
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
/* 筛选栏 */
.cl-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  padding: 10px 12px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
}
.cl-field {
  display: flex;
  align-items: center;
  gap: 6px;
}
.cl-field-label {
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.cl-select {
  padding: 4px 8px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
}
.cl-check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--pa-ink);
  cursor: pointer;
  user-select: none;
}
/* 状态区 */
.cl-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 0;
}
.cl-state-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}
.cl-retry {
  align-self: flex-start;
  padding: 5px 14px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
  transition: background .15s var(--pa-ease);
}
.cl-retry:hover {
  background: var(--pa-surface-3);
}
.cl-skel {
  height: 72px;
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-2);
  animation: cl-pulse 1.2s var(--pa-ease) infinite alternate;
}
@keyframes cl-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
/* 卡片列表 */
.cl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cl-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
}
.cl-row1 {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cl-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-ink);
}
.cl-full {
  margin: 0;
  font-size: 12px;
  color: var(--pa-ink-muted);
}
/* 徽章 */
.cl-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--pa-r-pill);
  font-size: 11px;
  line-height: 1.5;
}
.cl-chip--ccf {
  background: var(--pa-surface-3);
  color: var(--pa-ink);
  border: 1px solid var(--pa-hairline-strong);
}
.cl-chip--warn {
  background: color-mix(in srgb, var(--pa-warning) 14%, transparent);
  color: var(--pa-warning);
  border: 1px solid color-mix(in srgb, var(--pa-warning) 45%, transparent);
  font-weight: 500;
}
.cl-chip--tag {
  background: var(--pa-surface-2);
  color: var(--pa-ink-muted);
  border: 1px solid var(--pa-hairline);
}
.cl-chip--app {
  background: color-mix(in srgb, var(--pa-info) 10%, transparent);
  color: var(--pa-info);
  border-color: color-mix(in srgb, var(--pa-info) 35%, transparent);
}
/* 领域标签族色：ai 蓝 / bio 绿 / plant 黄 / cross 紫（置于 app 之后，交叉优先） */
.cl-chip--ai {
  background: color-mix(in srgb, var(--pa-info) 12%, transparent);
  color: var(--pa-info);
  border-color: color-mix(in srgb, var(--pa-info) 35%, transparent);
}
.cl-chip--bio {
  background: color-mix(in srgb, var(--pa-success) 14%, transparent);
  color: var(--pa-success);
  border-color: color-mix(in srgb, var(--pa-success) 40%, transparent);
}
.cl-chip--plant {
  background: color-mix(in srgb, var(--pa-warning) 14%, transparent);
  color: var(--pa-warning);
  border-color: color-mix(in srgb, var(--pa-warning) 40%, transparent);
}
.cl-chip--cross {
  background: color-mix(in srgb, #7c5cbf 14%, transparent);
  color: #7c5cbf;
  border-color: color-mix(in srgb, #7c5cbf 40%, transparent);
}
/* 倒计时色阶：≤0 灰 / ≤7 红 / 8–30 橙 / >30 绿 */
.cl-cd {
  font-family: var(--pa-font-mono);
  font-size: 12.5px;
  font-weight: 600;
}
.cl-cd--red { color: var(--pa-error); }
.cl-cd--orange { color: var(--pa-warning); }
.cl-cd--green { color: var(--pa-success); }
.cl-cd--ended { color: var(--pa-ink-muted); }
/* 元信息网格 */
.cl-meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 6px 14px;
}
.cl-meta-item dt {
  font-size: 11px;
  color: var(--pa-ink-subtle);
}
.cl-meta-item dd {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-ink);
}
.cl-deadline {
  font-family: var(--pa-font-mono);
  font-weight: 600;
}
.cl-link {
  color: var(--pa-g-from);
  text-decoration: none;
}
.cl-link:hover {
  text-decoration: underline;
}
</style>
