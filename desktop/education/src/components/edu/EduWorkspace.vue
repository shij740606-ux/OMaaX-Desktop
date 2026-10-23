<!-- EduWorkspace.vue —— 教育科研全屏工作台（步骤4；覆盖层语义 D1）。
     App.vue 中用 v-show 挂载（始终存活，仅 display 切换）：
     左 200px EduSideNav + 右侧 <component :is> 按 edu.section 动态切面板。
     面板首次打开后保留，切菜单不重复加载，也不丢失文件选择和草稿。 -->
<script setup>
import { computed } from 'vue';
import { useEduStore } from '../../stores/edu.js';
import EduSideNav from './EduSideNav.vue';
import JournalsPanel from './panels/JournalsPanel.vue';
import RecommendPanel from './panels/RecommendPanel.vue';
import PresubmitPanel from './panels/PresubmitPanel.vue';
import ReportPanel from './panels/ReportPanel.vue';
import FormatPanel from './panels/FormatPanel.vue';
import ReformatPanel from './panels/ReformatPanel.vue';
import CalendarPanel from './panels/CalendarPanel.vue';
import RebuttalPanel from './panels/RebuttalPanel.vue';
import PptPanel from './panels/PptPanel.vue';

const edu = useEduStore();

/** section → 面板组件映射（与 edu store SECTIONS 一致；
 *  training → calendar、tracker 已解除映射（面板文件保留在 panels/ 下未删除） */
const panelMap = {
  journals: JournalsPanel,
  recommend: RecommendPanel,
  presubmit: PresubmitPanel,
  report: ReportPanel,
  format: FormatPanel,
  reformat: ReformatPanel,
  calendar: CalendarPanel,
  rebuttal: RebuttalPanel,
  ppt: PptPanel,
};

const currentPanel = computed(() => panelMap[edu.section] || JournalsPanel);
</script>

<template>
  <div class="edu-workspace">
    <EduSideNav class="edu-nav" />
    <main class="edu-panel">
      <KeepAlive><component :is="currentPanel" /></KeepAlive>
    </main>
  </div>
</template>

<style scoped>
.edu-workspace {
  display: flex;
  height: 100vh;
  background: var(--pa-canvas);
  color: var(--pa-ink);
}
.edu-nav {
  width: 200px;
  flex: none;
}
.edu-panel {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 24px 28px;
}
</style>
