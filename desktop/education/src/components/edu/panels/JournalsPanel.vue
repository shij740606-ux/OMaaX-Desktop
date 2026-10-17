<!-- JournalsPanel.vue —— ① 学术期刊情报（步骤5 目录化改造，D2 严格只读）。
     数据链：onMounted → edu.fetchDirectory() → review.js getDirectory()
     → GET /api/review/journals/directory（相对路径 → vite proxy → BFF → 8100）。
     双 Tab 强制单语（🌍 en 779 本 / 🇨🇳 zh 277 本，后端 lang 过滤保证永不混排）；
     搜索/研究方向/分区筛选 + 后端分页（每页 50）+ 行展开详情 + 四态
     （loading 骨架 / error 重试 / 未导入 / 无匹配）。
     v4 改造（决策2）：主视图「来源」列移除，末列为「官网」列——official_url 有值
     → <a>短域名（去协议+去尾斜杠）</a>，null → 「—」纯文本不生成链接；
     展开行承载凭证 4 项：JIF核对状态 / 官网核对状态（可靠=绿、未逐站复核=浅绿、
     未检得·待核实=橙）/ JIF数据来源链接 / 分区数据来源链接；zh 展开行无 JCR 字段
     （jcr_release_year 恒 null，年报版本年 metric_report_year 单独展示）。
     底部固定表C长版数据源声明；顶部黄字按 Tab 切换（en/zh 两套口径）。
     三态与 journals（FormatPanel 下拉的 5 本精选）完全隔离，edu.journals 链路零改动。
     读时归一化由后端承担：zh cas_partition 恒 null + note、jif_note 两类文案、
     缺值 source 归零（前端只管展示 tooltip/链接）。 -->
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useEduStore } from '../../../stores/edu.js';

const edu = useEduStore();

/* —— panel-local 纯视图态（丢弃无妨；查询条件在 store）—— */
const searchInput = ref(''); // 输入框即时值（300ms 防抖后才进 store）
const expandedId = ref(''); // 展开详情的期刊 id（'' = 无）
const jumpInput = ref(''); // 跳页输入
let searchTimer = null;

const tab = computed(() => edu.journalsTab);
const q = computed(() => edu.directoryQuery);

/* 数据源声明：meta 到达且 lang 匹配 → 服务端文案；否则本地兜底（切 Tab 即时切换）。
   顶部黄字按 Tab 切换；底部另有表C长版统一声明（FOOT_NOTE，双 Tab 相同）。 */
const LOCAL_NOTE = {
  en: '分区：2025年中科院公开表（多表比对，2026年起停更）；JIF：2025年度指标（2026年JCR发布，两份公开汇编一致或已核对出版社官方指标）；整理日期2026-10-10；空白=来源未给值≠0',
  zh: '指标：国内自然科学期刊年报2025版（统计年2024，与JIF统计源不同、不混排）；中文期刊不按中科院分区口径列示',
};
/* 底部数据源声明（表C 长版，拍板决策3；末尾补来源口径说明） */
const FOOT_NOTE =
  'JIF 取自 2025 年度指标（2026 年 JCR 发布）并经两份公开汇编交叉一致或出版社官方指标核对，' +
  '中科院分区采用 2025 版公开表（多表比对、2026 年起停更），' +
  '中文期刊复合/综合影响因子取自国内年报 2025 版（统计年 2024，与 JIF 统计源不同、不混排），' +
  '期刊官网均附核对状态（3 本中文刊未检得可靠官网留空待核实）。' +
  '分区为高校转载版，官方入口需机构登录；JIF/中文数据为公开汇编镜像，仅供参考核对。';

/* —— 凭证状态配色：可靠=绿 / 未逐站复核=浅绿 / 未检得·待核实=橙 —— */
const JIF_OK = new Set(['两份公开汇编一致', '已核对出版社2025指标']);
const URL_OK = new Set([
  '出版社/期刊主页匹配',
  '期刊/主办方官方入口匹配',
  '期刊/出版平台页面检索核对',
]);
const URL_SOFT = 'ISSN匹配公开元数据；未逐站复核';
function checkCls(v, okSet) {
  if (!v) return '';
  if (okSet.has(v)) return 'jp-ok';
  if (v === URL_SOFT) return 'jp-ok-soft';
  if (v.includes('未检得') || v.includes('待核实')) return 'jp-warn';
  return '';
}
const sourceNote = computed(() =>
  edu.directoryMeta && edu.directoryMeta.lang === tab.value
    ? edu.directoryMeta.data_source_note
    : LOCAL_NOTE[tab.value]
);
const countText = computed(() =>
  edu.directoryMeta && edu.directoryMeta.lang === tab.value
    ? edu.directoryMeta.total
    : '—'
);
/* 研究方向 options（只认当前 lang 的 meta，防切 Tab 后旧词表残留） */
const domains = computed(() =>
  edu.directoryMeta && edu.directoryMeta.lang === tab.value ? edu.directoryMeta.domains : []
);

const total = computed(() => edu.directoryMeta?.total ?? 0);
const totalPages = computed(() => Math.ceil(total.value / q.value.size) || 1);
const hasFilter = computed(
  () => !!(q.value.q || q.value.domain || q.value.cas_partition)
);

/* 排序游标：后端默认（en=jif desc / zh=复合IF desc）在 field='' 时生效，箭头如实回显 */
const activeSort = computed(() => ({
  field: q.value.field || (tab.value === 'en' ? 'jif' : 'composite_impact_factor'),
  order: q.value.order || 'desc',
}));

const CAS_OPTIONS = ['1', '2', '3', '4'];

/* —— 交互 —— */
function onTab(next) {
  if (next === tab.value) return;
  clearTimeout(searchTimer);
  searchInput.value = '';
  jumpInput.value = '';
  expandedId.value = '';
  edu.switchDirectoryTab(next);
}
function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    edu.fetchDirectory({ q: searchInput.value.trim(), page: 1 });
    expandedId.value = '';
  }, 300);
}
function onDomainChange(e) {
  edu.fetchDirectory({ domain: e.target.value, page: 1 });
  expandedId.value = '';
}
function onCasChange(e) {
  edu.fetchDirectory({ cas_partition: e.target.value, page: 1 });
  expandedId.value = '';
}
function clearFilters() {
  clearTimeout(searchTimer);
  searchInput.value = '';
  edu.fetchDirectory({ q: '', domain: '', cas_partition: '', page: 1 });
  expandedId.value = '';
}
function gotoPage(p) {
  const n = Math.min(Math.max(1, p), totalPages.value);
  if (n === q.value.page) return;
  expandedId.value = '';
  edu.fetchDirectory({ page: n });
}
function jumpPage() {
  const n = parseInt(jumpInput.value, 10);
  jumpInput.value = '';
  if (Number.isNaN(n)) return;
  gotoPage(n);
}
function retry() {
  edu.fetchDirectory();
}
function toggleExpand(id) {
  expandedId.value = expandedId.value === id ? '' : id;
}
function toggleSort(field) {
  const cur = activeSort.value;
  if (cur.field === field) {
    // 同列切换升/降（不重置页码：total 不变，当前页仍有效）
    edu.fetchDirectory({ field, order: cur.order === 'asc' ? 'desc' : 'asc' });
  } else {
    edu.fetchDirectory({ field, order: field === 'name' ? 'asc' : 'desc' });
  }
}
const arrow = (f) => (activeSort.value.field === f ? (activeSort.value.order === 'asc' ? '▲' : '▼') : '');

const fmt = (v) => (v === null || v === undefined ? '—' : v);
const fmtIf = (v, year) => (v === null || v === undefined ? '—' : year ? `${v} (${year})` : `${v}`);
const shortUrl = (u) => String(u || '').replace(/^https?:\/\//, '').replace(/\/$/, '') || '—';

onMounted(() => {
  // idle 才查：ready 复用 store 缓存（切菜单回来筛选仍在）；error 不自动重试防循环
  if (edu.directoryStatus === 'idle') edu.fetchDirectory();
});
onUnmounted(() => clearTimeout(searchTimer));
</script>

<template>
  <section class="jp">
    <!-- 头部：标题 + 共 N 本（meta 与当前 Tab 匹配才回显，防切 Tab 瞬间旧计数） -->
    <header class="jp-head">
      <h1 class="jp-title">学术期刊情报</h1>
      <span class="jp-count">共 {{ countText }} 本</span>
    </header>

    <!-- 诚实性声明（黄字；两套口径按 Tab 切换） -->
    <p class="jp-note jp-note--warn">{{ sourceNote }}</p>

    <!-- Tab：🌍 国际 / 🇨🇳 中文（切换全清筛选） -->
    <nav class="jp-tabs" role="tablist">
      <button
        class="jp-tab"
        :class="{ on: tab === 'en' }"
        type="button"
        role="tab"
        :aria-selected="tab === 'en'"
        @click="onTab('en')"
      >🌍 国际期刊</button>
      <button
        class="jp-tab"
        :class="{ on: tab === 'zh' }"
        type="button"
        role="tab"
        :aria-selected="tab === 'zh'"
        @click="onTab('zh')"
      >🇨🇳 中文期刊</button>
    </nav>

    <!-- 搜索（防抖 300ms → q + page=1） -->
    <div class="jp-tools">
      <input
        v-model="searchInput"
        class="jp-search"
        type="search"
        placeholder="搜索期刊名（如 Nature、Plant Cell）"
        @input="onSearchInput"
      >
    </div>

    <!-- 筛选栏（domains 来自当前 lang 的 meta；分区下拉仅 en） -->
    <div v-if="domains.length" class="jp-filters">
      <div class="jp-fgroup">
        <span class="jp-flabel">研究方向</span>
        <select class="jp-select" :value="q.domain" @change="onDomainChange">
          <option value="">全部</option>
          <option v-for="d in domains" :key="d" :value="d">{{ d }}</option>
        </select>
      </div>
      <div v-if="tab === 'en'" class="jp-fgroup">
        <span class="jp-flabel">分区</span>
        <select class="jp-select" :value="q.cas_partition" @change="onCasChange">
          <option value="">全部</option>
          <option v-for="p in CAS_OPTIONS" :key="p" :value="p">{{ p }}区</option>
        </select>
      </div>
      <button v-if="hasFilter" class="jp-clear" type="button" @click="clearFilters">清除筛选</button>
    </div>

    <!-- ERROR：directory 三态独立于 journals；reviewErrorMessage 已映射 -->
    <div v-if="edu.directoryStatus === 'error'" class="jp-state" role="alert">
      <p class="jp-state-msg">{{ edu.directoryError }}</p>
      <button class="jp-retry" type="button" @click="retry">重试</button>
    </div>

    <!-- LOADING：骨架行 -->
    <div v-else-if="edu.directoryStatus === 'loading'" class="jp-state" aria-busy="true">
      <div class="jp-skel-head"></div>
      <div v-for="n in 6" :key="n" class="jp-skel-row"></div>
      <p class="jp-state-msg">期刊目录加载中…</p>
    </div>

    <!-- 空态 A：未导入（data_imported=false，HTTP 200 优雅降级路径） -->
    <div v-else-if="edu.directoryMeta && edu.directoryMeta.data_imported === false" class="jp-state">
      <p class="jp-state-msg">目录数据未导入（需先运行 review_service/scripts/import_journals.py）</p>
    </div>

    <!-- 空态 B：有筛选 → 无匹配（≠ 无数据） -->
    <div v-else-if="!edu.directory.length && hasFilter" class="jp-state">
      <p class="jp-state-msg">无匹配结果，可清除筛选后重试</p>
      <button class="jp-retry" type="button" @click="clearFilters">清除筛选</button>
    </div>

    <!-- 空态 C：兜底（已导入但整表为空） -->
    <div v-else-if="!edu.directory.length" class="jp-state">
      <p class="jp-state-msg">暂无期刊数据</p>
    </div>

    <!-- READY + 表格（写交互 = 筛选/排序重查 + 分页 + 行展开；en/zh 各 5 列，末列官网） -->
    <table v-else class="jp-table">
      <thead>
        <tr v-if="tab === 'en'">
          <th class="jp-th-sort jp-c-name" :class="{ on: activeSort.field === 'name' }" @click="toggleSort('name')">
            期刊名 <span class="jp-arrow">{{ arrow('name') }}</span>
          </th>
          <th class="jp-th-sort jp-c-dir" :class="{ on: activeSort.field === 'research_direction' }" @click="toggleSort('research_direction')">
            研究方向 <span class="jp-arrow">{{ arrow('research_direction') }}</span>
          </th>
          <th class="jp-th-sort jp-c-part" :class="{ on: activeSort.field === 'cas_partition' }" @click="toggleSort('cas_partition')">
            分区 <span class="jp-arrow">{{ arrow('cas_partition') }}</span>
          </th>
          <th class="jp-th-sort jp-c-jif" :class="{ on: activeSort.field === 'jif' }" @click="toggleSort('jif')">
            JIF <span class="jp-arrow">{{ arrow('jif') }}</span>
          </th>
          <th class="jp-c-url">官网</th>
        </tr>
        <tr v-else>
          <th class="jp-th-sort jp-c-name" :class="{ on: activeSort.field === 'name' }" @click="toggleSort('name')">
            期刊名 <span class="jp-arrow">{{ arrow('name') }}</span>
          </th>
          <th class="jp-th-sort jp-c-dir" :class="{ on: activeSort.field === 'research_direction' }" @click="toggleSort('research_direction')">
            研究方向 <span class="jp-arrow">{{ arrow('research_direction') }}</span>
          </th>
          <th class="jp-th-sort jp-c-comp" :class="{ on: activeSort.field === 'composite_impact_factor' }" @click="toggleSort('composite_impact_factor')">
            复合IF <span class="jp-arrow">{{ arrow('composite_impact_factor') }}</span>
          </th>
          <th class="jp-th-sort jp-c-compr" :class="{ on: activeSort.field === 'comprehensive_impact_factor' }" @click="toggleSort('comprehensive_impact_factor')">
            综合IF <span class="jp-arrow">{{ arrow('comprehensive_impact_factor') }}</span>
          </th>
          <th class="jp-c-url">官网</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="j in edu.directory" :key="j.id">
          <tr class="jp-row" :class="{ open: expandedId === j.id }" @click="toggleExpand(j.id)">
            <!-- en：期刊名 / 研究方向 / 分区 / JIF(年) / 官网 -->
            <template v-if="tab === 'en'">
              <td class="jp-name jp-c-name">{{ j.name }}</td>
              <td class="jp-c-dir">{{ j.research_direction || '—' }}</td>
              <td class="jp-part jp-c-part">{{ j.cas_partition || '—' }}</td>
              <td class="jp-num jp-c-jif" :title="j.jif_note || ''">
                {{ fmtIf(j.jif, j.jif_year) }}
              </td>
              <td class="jp-c-url">
                <a v-if="j.official_url" class="jp-link" :href="j.official_url" target="_blank" rel="noopener" @click.stop>{{ shortUrl(j.official_url) }}</a>
                <span v-else class="jp-dash">—</span>
              </td>
            </template>
            <!-- zh：期刊名 / 研究方向 / 复合IF(统计年) / 综合IF / 官网 -->
            <template v-else>
              <td class="jp-name jp-c-name">{{ j.name }}</td>
              <td class="jp-c-dir">{{ j.research_direction || '—' }}</td>
              <td class="jp-num jp-c-comp" :title="j.jif_note || ''">{{ fmtIf(j.composite_impact_factor, j.metric_year) }}</td>
              <td class="jp-num jp-c-compr">{{ fmt(j.comprehensive_impact_factor) }}</td>
              <td class="jp-c-url">
                <a v-if="j.official_url" class="jp-link" :href="j.official_url" target="_blank" rel="noopener" @click.stop>{{ shortUrl(j.official_url) }}</a>
                <span v-else class="jp-dash">—</span>
              </td>
            </template>
          </tr>
          <!-- 展开行（两 Tab 均 5 列） -->
          <tr v-if="expandedId === j.id" class="jp-xp-row">
            <td colspan="5">
              <!-- en 详情：年份/状态/凭证链接 -->
              <div v-if="tab === 'en'" class="jp-xp">
                <div class="jp-xp-group">
                  <span class="jp-xp-label">分区年</span><span class="jp-xp-val">{{ fmt(j.cas_partition_year) }}</span>
                  <span class="jp-xp-label">JIF年</span><span class="jp-xp-val">{{ fmt(j.jif_year) }}</span>
                  <span class="jp-xp-label">JIF核对状态</span><span class="jp-xp-val" :class="checkCls(j.jif_check_status, JIF_OK)">{{ j.jif_check_status || '—' }}</span>
                  <span class="jp-xp-label">Top</span><span class="jp-xp-val">{{ j.top_flag || '—' }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">官网核对</span><span class="jp-xp-val" :class="checkCls(j.official_check_status, URL_OK)">{{ j.official_check_status || '—' }}</span>
                  <span class="jp-xp-label">JCR发布年</span><span class="jp-xp-val">{{ fmt(j.jcr_release_year) }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">出版系列</span><span class="jp-xp-val">{{ j.publisher_series || '—' }}</span>
                  <span class="jp-xp-label">期刊标记</span><span class="jp-xp-val">{{ j.journal_mark || '—' }}</span>
                  <span class="jp-xp-label">文章类型</span><span class="jp-xp-val">{{ j.article_type || '—' }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">ISSN</span>
                  <span class="jp-xp-val jp-mono">{{ j.issn || '—' }}<template v-if="j.eissn"> · eISSN {{ j.eissn }}</template></span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">JCR学科</span>
                  <span v-for="d in j.jcr_disciplines || []" :key="d" class="jp-chip on">{{ d }}</span>
                  <span v-if="!(j.jcr_disciplines || []).length" class="jp-dash">—</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">来源</span>
                  <template v-if="j.jif_source_url">
                    <a class="jp-link" :href="j.jif_source_url" target="_blank" rel="noopener" @click.stop>
                      JIF来源：{{ shortUrl(j.jif_source_url) }}
                    </a>
                    <span class="jp-src-note">（汇编镜像，官方入口需机构订阅）</span>
                  </template>
                  <span v-else class="jp-dash">JIF来源：无</span>
                  <span class="jp-src-note">分区来源：{{ j.partition_source_note }}</span>
                </div>
              </div>
              <!-- zh 详情：年报口径指标 + 不可混排黄字 -->
              <div v-else class="jp-xp">
                <div class="jp-xp-group">
                  <span class="jp-xp-label">复合IF</span><span class="jp-xp-val jp-mono">{{ fmt(j.composite_impact_factor) }}</span>
                  <span class="jp-xp-label">综合IF</span><span class="jp-xp-val jp-mono">{{ fmt(j.comprehensive_impact_factor) }}</span>
                  <span class="jp-xp-label">统计年</span><span class="jp-xp-val">{{ fmt(j.metric_year) }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">年报版本</span><span class="jp-xp-val">{{ j.metric_report_year ? `${j.metric_report_year}版` : '—' }}</span>
                  <span class="jp-xp-label">年报页码</span><span class="jp-xp-val">{{ j.annual_report_page ? `第${j.annual_report_page}页` : '—' }}</span>
                  <span class="jp-xp-label">核对状态</span><span class="jp-xp-val" :class="checkCls(j.jif_check_status, JIF_OK)">{{ j.jif_check_status || '—' }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">官网核对</span><span class="jp-xp-val" :class="checkCls(j.official_check_status, URL_OK)">{{ j.official_check_status || '—' }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">指标口径</span><span class="jp-xp-val">{{ j.metric_caliber || '—' }}</span>
                </div>
                <div class="jp-xp-group">
                  <span class="jp-xp-label">来源</span>
                  <template v-if="j.data_source_url">
                    <a class="jp-link" :href="j.data_source_url" target="_blank" rel="noopener" @click.stop>
                      年报原页：{{ shortUrl(j.data_source_url) }}
                    </a>
                    <span class="jp-src-note">（国内自然科学期刊年报 2025 版）</span>
                  </template>
                  <span v-else class="jp-dash">年报原页：无</span>
                  <template v-if="j.jif_source_url">
                    <a class="jp-link" :href="j.jif_source_url" target="_blank" rel="noopener" @click.stop>
                      JIF来源：{{ shortUrl(j.jif_source_url) }}
                    </a>
                    <span class="jp-src-note">（汇编镜像，官方入口需机构订阅）</span>
                  </template>
                  <span v-else class="jp-dash">JIF来源：无</span>
                  <span class="jp-src-note">分区来源：{{ j.partition_source_note }}</span>
                </div>
                <p class="jp-xp-note">⚠ 国内IF与JIF统计源不同，不可混排</p>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <!-- 分页（后端分页；total 超过 size 才显示） -->
    <footer v-if="edu.directoryStatus === 'ready' && totalPages > 1" class="jp-pager">
      <button class="jp-page-btn" type="button" :disabled="q.page <= 1" @click="gotoPage(q.page - 1)">‹ 上一页</button>
      <span class="jp-page-info">第 {{ q.page }} / {{ totalPages }} 页</span>
      <button class="jp-page-btn" type="button" :disabled="q.page >= totalPages" @click="gotoPage(q.page + 1)">下一页 ›</button>
      <label class="jp-page-jump">
        跳至
        <input v-model="jumpInput" class="jp-page-input" type="number" min="1" :max="totalPages" @change="jumpPage">
        页
      </label>
      <span class="jp-page-size">每页 {{ q.size }} 条</span>
    </footer>

    <!-- 底部数据源声明（表C 长版，拍板决策3；双 Tab 统一） -->
    <p class="jp-foot">{{ FOOT_NOTE }}</p>
  </section>
</template>

<style scoped>
.jp {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.jp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.jp-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--pa-ink);
}
.jp-count {
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.jp-note {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--pa-ink-subtle);
}
.jp-note--warn {
  color: var(--pa-warning);
}
/* —— Tab —— */
.jp-tabs {
  display: flex;
  gap: 6px;
}
.jp-tab {
  padding: 6px 14px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-pill);
  background: var(--pa-surface-1);
  color: var(--pa-ink-muted);
  font-size: 12.5px;
  cursor: pointer;
  transition: background .15s var(--pa-ease), color .15s var(--pa-ease), border-color .15s var(--pa-ease);
}
.jp-tab:hover {
  background: var(--pa-surface-2);
  color: var(--pa-ink);
}
.jp-tab.on {
  background: color-mix(in srgb, var(--pa-g-from) 14%, transparent);
  border-color: color-mix(in srgb, var(--pa-g-from) 50%, transparent);
  color: var(--pa-ink);
  font-weight: 600;
}
/* —— 搜索 —— */
.jp-tools {
  display: flex;
}
.jp-search {
  width: min(360px, 100%);
  padding: 6px 10px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-1);
  color: var(--pa-ink);
  font-size: 13px;
}
.jp-search:focus {
  outline: none;
  border-color: color-mix(in srgb, var(--pa-g-from) 55%, transparent);
}
.jp-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 0;
}
.jp-state-msg {
  margin: 0;
  font-size: 13px;
  color: var(--pa-ink-muted);
}
.jp-retry {
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
.jp-retry:hover {
  background: var(--pa-surface-3);
}
.jp-skel-head,
.jp-skel-row {
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-2);
  animation: jp-pulse 1.2s var(--pa-ease) infinite alternate;
}
.jp-skel-head {
  height: 34px;
  margin-bottom: 2px;
}
.jp-skel-row {
  height: 30px;
  margin-bottom: 6px;
}
@keyframes jp-pulse {
  from { opacity: .55; }
  to { opacity: 1; }
}
.jp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
  overflow: hidden;
}
.jp-table thead th {
  padding: 9px 12px;
  text-align: left;
  font-size: 12px;
  font-weight: 500;
  color: var(--pa-ink-muted);
  background: var(--pa-surface-1);
  border-bottom: 1px solid var(--pa-hairline);
  white-space: nowrap;
}
.jp-th-sort {
  cursor: pointer;
  user-select: none;
  transition: color .15s var(--pa-ease);
}
.jp-th-sort:hover {
  color: var(--pa-ink);
}
.jp-th-sort.on {
  color: var(--pa-ink);
  font-weight: 600;
}
.jp-arrow {
  font-size: 10px;
  color: var(--pa-g-from);
}
.jp-table tbody td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--pa-hairline);
  color: var(--pa-ink);
}
.jp-table tbody tr:last-child td {
  border-bottom: none;
}
.jp-table tbody tr:hover {
  background: var(--pa-surface-2);
}
.jp-name {
  font-weight: 500;
}
.jp-num {
  text-align: center;
  font-family: var(--pa-font-mono);
  color: var(--pa-ink-muted);
}
.jp-link {
  color: var(--pa-g-from);
  text-decoration: none;
}
.jp-link:hover {
  text-decoration: underline;
}
.jp-dash {
  color: var(--pa-ink-subtle);
}
/* 来源口径说明（灰色小字）：分区常量文案 + 链接后括号补充 */
.jp-src-note {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
/* —— 列宽白名单（table 自适应 + min-width，不硬截断）—— */
.jp-c-name { min-width: 200px; }
.jp-c-dir { min-width: 140px; }
.jp-c-part { min-width: 64px; text-align: center; }
.jp-c-jif { min-width: 110px; }
.jp-c-url { min-width: 180px; }
.jp-c-comp { min-width: 110px; }
.jp-c-compr { min-width: 110px; }
/* —— 凭证状态配色：可靠=绿 / 未逐站复核=浅绿 / 未检得·待核实=橙 —— */
.jp-ok {
  color: var(--pa-success);
  font-weight: 500;
}
.jp-ok-soft {
  color: color-mix(in srgb, var(--pa-success) 60%, var(--pa-ink));
}
.jp-warn {
  color: var(--pa-warning);
  font-weight: 500;
}
/* —— 筛选栏 —— */
.jp-filters {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: var(--pa-surface-1);
  border: 1px solid var(--pa-hairline);
  border-radius: var(--pa-r-md);
}
.jp-fgroup {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.jp-flabel {
  flex: none;
  width: 56px;
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.jp-select {
  padding: 4px 8px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-2);
  color: var(--pa-ink);
  font-size: 12px;
}
.jp-clear {
  align-self: flex-start;
  padding: 4px 12px;
  border: none;
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-3);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
  transition: background .15s var(--pa-ease);
}
.jp-clear:hover {
  background: var(--pa-surface-2);
}
/* —— 行展开 —— */
.jp-part {
  text-align: center;
  font-weight: 500;
}
.jp-row {
  cursor: pointer;
}
.jp-row.open {
  background: var(--pa-surface-2);
}
.jp-xp-row td {
  background: var(--pa-surface-2);
  border-bottom: 1px solid var(--pa-hairline);
}
.jp-xp {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px;
}
.jp-xp-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.jp-xp-label {
  flex: none;
  width: 56px;
  font-size: 11px;
  color: var(--pa-ink-subtle);
}
.jp-xp-val {
  font-size: 12.5px;
  color: var(--pa-ink);
}
.jp-mono {
  font-family: var(--pa-font-mono);
}
.jp-xp-note {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--pa-warning);
}
.jp-chip {
  padding: 3px 10px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-pill);
  background: var(--pa-surface-2);
  color: var(--pa-ink-muted);
  font-size: 12px;
  line-height: 1.5;
}
.jp-chip.on {
  background: color-mix(in srgb, var(--pa-g-from) 16%, transparent);
  border-color: color-mix(in srgb, var(--pa-g-from) 50%, transparent);
  color: var(--pa-ink);
  font-weight: 500;
}
/* —— 分页 —— */
.jp-pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.jp-page-btn {
  padding: 5px 12px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-md);
  background: var(--pa-surface-1);
  color: var(--pa-ink);
  font-size: 12px;
  cursor: pointer;
  transition: background .15s var(--pa-ease);
}
.jp-page-btn:hover:not(:disabled) {
  background: var(--pa-surface-2);
}
.jp-page-btn:disabled {
  opacity: .4;
  cursor: default;
}
.jp-page-info {
  font-family: var(--pa-font-mono);
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.jp-page-jump {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--pa-ink-muted);
}
.jp-page-input {
  width: 56px;
  padding: 3px 6px;
  border: 1px solid var(--pa-hairline-strong);
  border-radius: var(--pa-r-sm);
  background: var(--pa-surface-1);
  font-size: 12px;
}
.jp-page-size {
  font-size: 12px;
  color: var(--pa-ink-subtle);
}
/* —— 底部数据源声明（表C 长版） —— */
.jp-foot {
  margin: 0;
  padding-top: 4px;
  border-top: 1px solid var(--pa-hairline);
  font-size: 12px;
  line-height: 1.6;
  color: var(--pa-ink-subtle);
}
</style>
