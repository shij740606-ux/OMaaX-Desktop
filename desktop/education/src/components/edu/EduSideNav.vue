<!-- EduSideNav.vue —— 教育科研左侧菜单（步骤4）。
     7 项与 edu store SECTIONS 一致；顶部「← 返回问答」回 chat 视图
     （AppShell 含 TopBar 在 edu 视图整体 display:none，此为唯一显式返回入口）。
     高亮仅用现成 --pa-* 变量；无 icon 组件 → emoji。 -->
<script setup>
import { useAppStore } from '../../stores/app.js';
import { useEduStore } from '../../stores/edu.js';

const app = useAppStore();
const edu = useEduStore();

const ITEMS = [
  { key: 'journals', label: '学术期刊情报', icon: '📰' },
  { key: 'recommend', label: '智能投稿推荐', icon: '🎯' },
  { key: 'presubmit', label: 'AI 预审稿系统', icon: '🔍' },
  { key: 'report', label: '专业审稿意见报告', icon: '📝' },
  { key: 'format', label: '论文排版与规范', icon: '📐' },
  { key: 'reformat', label: '论文自动重排版', icon: '🧩' },
  // SVG 内联（U+1F4AC/U+2709 在用户 Ubuntu 浏览器均无字形 → 方框；SVG 不依赖字体）
  { key: 'rebuttal', label: '审稿回复助手', icon: '<svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor"><path d="M2 2h12v9H6l-4 3z"/></svg>' },
  { key: 'calendar', label: '会议与投稿日历', icon: '📅' },
];
</script>

<template>
  <nav class="edu-sidenav">
    <button class="back-btn" type="button" title="返回问答" @click="app.gotoChat()">
      ← 返回问答
    </button>
    <ul class="edu-menu">
      <li v-for="it in ITEMS" :key="it.key">
        <button
          class="edu-item"
          :class="{ active: edu.section === it.key }"
          type="button"
          :title="it.label"
          @click="edu.setSection(it.key)"
        >
          <!-- icon 为静态白名单字符串（本文件 ITEMS 常量），SVG 项用 v-html，emoji 项纯文本 -->
          <span v-if="it.icon.startsWith('<svg')" class="edu-icon" aria-hidden="true" v-html="it.icon" />
          <span v-else class="edu-icon" aria-hidden="true">{{ it.icon }}</span>
          <span class="edu-label">{{ it.label }}</span>
        </button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.edu-sidenav {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  padding: 14px 10px;
  background: var(--pa-surface-1);
  border-right: 1px solid var(--pa-hairline);
}
.back-btn {
  display: flex;
  align-items: center;
  padding: 6px 10px;
  border: none;
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12px;
  cursor: pointer;
  transition: color .15s var(--pa-ease), background .15s var(--pa-ease);
}
.back-btn:hover {
  color: var(--pa-ink);
  background: var(--pa-surface-2);
}
.edu-menu {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.edu-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: none;
  border-radius: var(--pa-r-md);
  background: transparent;
  color: var(--pa-ink-muted);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  transition: color .15s var(--pa-ease), background .15s var(--pa-ease);
}
.edu-item:hover {
  color: var(--pa-ink);
  background: var(--pa-surface-2);
}
.edu-item.active {
  color: var(--pa-ink);
  font-weight: 600;
  background: var(--pa-surface-2);
  box-shadow: inset 2px 0 0 var(--pa-g-from);
}
.edu-icon {
  flex: none;
  font-size: 14px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
}
.edu-label {
  min-width: 0;
  line-height: 1.35;
}
</style>
