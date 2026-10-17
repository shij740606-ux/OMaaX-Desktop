// stores/app.js —— 全局视图状态（chat ⇄ edu，无 vue-router；覆盖层语义 D1）
// 切到 'edu' 时 App.vue 用 v-show 隐藏 AppShell 而不卸载：RAG 会话、
// 上传文件、滚动位置零丢失（严禁改用 v-if）。
// 仅内存态：刷新页面必回 'chat'，避免用户被卡在 edu 视图。
import { defineStore } from 'pinia';

const VIEWS = ['chat', 'edu'];

export const useAppStore = defineStore('app', {
  state: () => ({
    currentView: 'chat', // 'chat' | 'edu'，默认 chat
  }),
  getters: {
    isChat: (s) => s.currentView === 'chat',
    isEdu: (s) => s.currentView === 'edu',
  },
  actions: {
    setView(v) {
      if (VIEWS.includes(v)) this.currentView = v; // 非法值静默忽略
    },
    toggleView() {
      this.currentView = this.currentView === 'edu' ? 'chat' : 'edu';
    },
    gotoEdu() {
      this.setView('edu');
    },
    gotoChat() {
      parent.postMessage({type:'omaax-back'},'*');
    },
  },
});
