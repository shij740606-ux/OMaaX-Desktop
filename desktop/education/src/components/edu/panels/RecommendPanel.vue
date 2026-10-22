<!-- RecommendPanel.vue —— ② 智能投稿推荐（步骤7，产品核心功能）。
     链路：表单草稿 → edu.submitRecommend(payload) → POST /api/review/recommend（JSON）
     → {ok, tiers{challenge,fit,safe}{label,items}, meta{vector_ok,llm_ok,...}}
     （真实响应结构已于写入前 curl 8100 核对，按真实字段渲染）。
     纯文本粘贴版（PDF 上传后续迭代）；目标期刊字段首版砍掉（后端无此入参）。
     tier chips 为纯前端结果筛选（不改 payload 语义）；结果与草稿存 edu store，
     切菜单往返不丢、mount 无请求。D2 同样只读。 -->
<script setup>
import { computed } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

const TIERS = ['challenge', 'fit', 'safe'];
const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'challenge', label: '挑战' },
  { key: 'fit', label: '适配' },
  { key: 'safe', label: '稳妥' },
];

const abstractLen = computed(() => edu.drafts.abstract.trim().length);
const abstractOk = computed(() => abstractLen.value >= 50);
const loading = computed(() => edu.recommendStatus === 'loading');
const errored = computed(() => edu.recommendStatus === 'error');
const result = computed(() =>
  edu.recommend && edu.recommend.ok && edu.recommend.tiers ? edu.recommend : null
);
const meta = computed(() => result.value?.meta || null);
const visibleTiers = computed(() => {
  if (!result.value) return [];
  const f = edu.drafts.tierFilter;
  return TIERS.filter((k) => f === 'all' || f === k);
});
const itemsOf = (key) => result.value?.tiers?.[key]?.items || [];
const labelOf = (key) => result.value?.tiers?.[key]?.label || key;

function setFilter(k) {
  edu.drafts.tierFilter = k;
}

/** prefs（同构草稿）→ preferences（接口字段）：null 数值剔除，tier_focus 恒 'all'（chips 纯前端筛选） */
function buildPayload() {
  const { max_review_weeks: w, min_impact_factor: inf, tier_focus: focus } = edu.drafts.prefs;
  const preferences = { tier_focus: focus || 'all' };
  const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const nw = num(w);
  const ninf = num(inf);
  // 本地钳制到接口 pydantic 范围（越界会触发 FastAPI 422 非信封响应）
  if (nw !== null) preferences.max_review_weeks = Math.min(104, Math.max(1, nw));
  if (ninf !== null) preferences.min_impact_factor = Math.min(200, Math.max(0, ninf));
  return { abstract: edu.drafts.abstract.trim(), preferences };
}

async function submit() {
  if (!abstractOk.value || loading.value) return;
  await edu.submitRecommend(buildPayload());
}

const fmt = (v) => (v === null || v === undefined ? '—' : v);
const riskClass = (note) => {
  if (!note || note === '无') return 'none';
  return String(note).includes('⚠') ? 'warn' : '';
};
</script>

<template>
  <section class="rp">
    <header class="rp-head">
      <h1 class="rp-title">智能投稿推荐</h1>
      <span class="rp-sub">根据论文摘要推荐适合的投稿期刊</span>
    </header>

    <!-- ========== 表单区（草稿存 edu.drafts，切菜单不丢） ========== -->
    <div class="rp-form">
      <label class="rp-label" for="rp-abstract">
        论文摘要 <span class="rp-req">必填</span>
      </label>
      <textarea
        id="rp-abstract"
        v-model="edu.drafts.abstract"
        class="rp-textarea"
        rows="7"
        maxlength="20000"
        placeholder="粘贴论文摘要（≥50 字符），或粘贴全文前 500 字"
      ></textarea>
      <div class="rp-count" :class="{ ok: abstractOk }">
        {{ edu.drafts.abstract.trim().length }} / 20000 字符
        <span v-if="!abstractOk">· 至少需要 50 字符</span>
      </div>

      <div class="rp-prefs">
        <div class="rp-field">
          <label class="rp-label" for="rp-weeks">最大审稿周期（周，选填）</label>
          <input
            id="rp-weeks"
            v-model.number="edu.drafts.prefs.max_review_weeks"
            class="rp-input"
            type="number"
            min="1"
            max="104"
            placeholder="如 8"
          />
        </div>
        <div class="rp-field">
          <label class="rp-label" for="rp-if">最低影响因子（选填）</label>
          <input
            id="rp-if"
            v-model.number="edu.drafts.prefs.min_impact_factor"
            class="rp-input"
            type="number"
            min="0"
            max="200"
            step="0.1"
            placeholder="如 3"
          />
        </div>
      </div>

      <button
        class="rp-submit"
        type="button"
        :disabled="!abstractOk || loading"
        @click="submit"
      >
        {{ loading ? '推荐生成中…（约 10–30 秒）' : '开始推荐' }}
      </button>
    </div>

    <!-- ========== 状态区 ========== -->
    <div v-if="loading" class="rp-state" aria-busy="true">
      <div class="rp-skel"></div>
      <div class="rp-skel rp-skel-mid"></div>
      <div class="rp-skel"></div>
      <p class="rp-state-msg">正在匹配适合的期刊，请稍候…</p>
    </div>

    <div v-else-if="errored" class="rp-state" role="alert">
      <p class="rp-state-msg rp-state-error">{{ edu.recommendError }}</p>
      <button class="rp-retry" type="button" @click="submit">重试</button>
    </div>

    <div v-else-if="!result" class="rp-state">
      <p class="rp-state-msg">粘贴摘要后点击「开始推荐」：从期刊库语义匹配候选，按挑战/适配/稳妥三档给出匹配理由与风险提示。</p>
    </div>

    <!-- ========== 结果区 ========== -->
    <template v-else>
      <!-- 降级横幅（meta 标志，非报错红） -->
      <div v-if="meta && !meta.llm_ok" class="rp-degraded">
        {{ !meta.llm_ok ? '智能推荐暂不可用，以下按影响因子和审稿周期排序' : '已扩大期刊范围进行匹配' }}
      </div>

      <!-- 结果元信息 -->
      <div v-if="meta" class="rp-meta">
        已匹配 {{ meta.candidate_count }} 本候选期刊
      </div>

      <!-- 档位 chips（纯前端筛选，不重查） -->
      <div class="rp-chips" role="tablist" aria-label="档位筛选">
        <button
          v-for="f in FILTERS"
          :key="f.key"
          class="rp-chip"
          :class="{ on: edu.drafts.tierFilter === f.key }"
          type="button"
          role="tab"
          :aria-selected="edu.drafts.tierFilter === f.key"
          @click="setFilter(f.key)"
        >
          {{ f.label }}
        </button>
      </div>

      <section v-for="key in visibleTiers" :key="key" class="rp-tier">
        <h2 class="rp-tier-head">
          <span class="rp-dot" :class="key" aria-hidden="true"></span>
          {{ labelOf(key) }}
          <span class="rp-tier-count">{{ itemsOf(key).length }} 本</span>
        </h2>

        <p v-if="!itemsOf(key).length" class="rp-empty">本档暂无期刊</p>

        <article v-for="it in itemsOf(key)" :key="it.id" class="rp-card">
          <header class="rp-card-head">
            <span class="rp-card-name">{{ it.name }}</span>
            <span class="rp-card-metrics">
              <span class="rp-metric">IF {{ fmt(it.impact_factor) }}</span>
              <span class="rp-metric">约 {{ fmt(it.review_time_weeks) }} 周</span>
              <a
                v-if="it.official_url"
                class="rp-link"
                :href="it.official_url"
                target="_blank"
                rel="noopener"
              >官网 ↗</a>
            </span>
          </header>
          <p v-if="it.similarity !== null && it.similarity !== undefined" class="rp-sim">
            语义匹配 {{ Number(it.similarity).toFixed(2) }}
          </p>
          <p class="rp-reason">
            <span class="rp-k">匹配理由</span>{{ it.match_reason }}
          </p>
          <p class="rp-risk" :class="riskClass(it.risk_note)">
            <span class="rp-k">风险</span>{{ it.risk_note }}
          </p>
        </article>
      </section>
    </template>
  </section>
</template>

<style scoped>
.rp {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.rp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.rp-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.rp-sub {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
.rp-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-lg);
}
.rp-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-ink-muted);
}
.rp-req {
  margin-left: 4px;
  font-size: 11px;
  color: var(--pa-warning);
}
.rp-textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 120px;
  padding: 10px 12px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-family: var(--pa-font-ui);
  font-size: 13px;
  line-height: 1.55;
}
.rp-textarea:focus {
  outline: none;
  border-color: var(--pa-g-from);
}
.rp-count {
  font-size: 11.5px;
  color: var(--pa-ink-subtle);
  text-align: right;
}
.rp-count.ok {
  color: var(--pa-success);
}
.rp-prefs {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.rp-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.rp-input {
  width: 150px;
  box-sizing: border-box;
  padding: 6px 10px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-family: var(--pa-font-mono);
  font-size: 13px;
}
.rp-input:focus {
  outline: none;
  border-color: var(--pa-g-from);
}
.rp-submit {
  align-self: flex-start;
  padding: 8px 22px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-gradient);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity .15s var(--pa-ease);
}
.rp-submit:disabled {
  opacity: .45;
  cursor: not-allowed;
}
.rp-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 6px 0;
}
.rp-skel {
  height: 56px;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  animation: rp-pulse 1.2s var(--pa-ease) infinite alternate;
}
.rp-skel-mid {
  height: 88px;
}
@keyframes rp-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
.rp-state-msg {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pa-ink-muted);
}
.rp-state-error {
  color: var(--pa-error);
}
.rp-retry {
  align-self: flex-start;
  padding: 5px 14px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
}
.rp-retry:hover {
  background: var(--pa-surface-3);
}
.rp-degraded {
  padding: 8px 12px;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-2);
  border-left: 3px solid var(--pa-warning);
  font-size: 12.5px;
  color: var(--pa-warning);
}
.rp-meta {
  font-size: 11.5px;
  color: var(--pa-ink-subtle);
}
.rp-chips {
  display: flex;
  gap: 8px;
}
.rp-chip {
  padding: 4px 14px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-pill);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
  transition: color .15s var(--pa-ease), background .15s var(--pa-ease);
}
.rp-chip:hover {
  color: var(--pa-ink);
}
.rp-chip.on {
  color: #fff;
  background: var(--pa-g-from);
  border-color: transparent;
  font-weight: 600;
}
.rp-tier {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rp-tier-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-ink);
}
.rp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.rp-dot.challenge { background: var(--pa-warning); }
.rp-dot.fit { background: var(--pa-info); }
.rp-dot.safe { background: var(--pa-success); }
.rp-tier-count {
  font-size: 11.5px;
  font-weight: 400;
  color: var(--pa-ink-subtle);
}
.rp-empty {
  margin: 0;
  font-size: 12.5px;
  color: var(--pa-ink-subtle);
}
.rp-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  transition: background .15s var(--pa-ease);
}
.rp-card:hover {
  background: var(--pa-surface-2);
}
.rp-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.rp-card-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-ink);
}
.rp-card-metrics {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rp-metric {
  font-family: var(--pa-font-mono);
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.rp-link {
  font-size: 12px;
  color: var(--pa-g-from);
  text-decoration: none;
}
.rp-link:hover {
  text-decoration: underline;
}
.rp-sim {
  margin: 0;
  font-size: 11.5px;
  color: var(--pa-ink-subtle);
}
.rp-reason,
.rp-risk {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}
.rp-reason {
  color: var(--pa-ink);
}
.rp-risk {
  color: var(--pa-ink-muted);
}
.rp-risk.warn {
  color: var(--pa-warning);
}
.rp-risk.none {
  color: var(--pa-ink-subtle);
}
.rp-k {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 7px;
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-3);
  font-size: 11px;
  color: var(--pa-ink-muted);
  vertical-align: 1px;
}
</style>
