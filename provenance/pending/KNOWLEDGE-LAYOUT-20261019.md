# Knowledge question and answer layout — 2026-10-19

Source only; do not package or replace the installed desktop until a later user request.

Both actions are functional: native chat maps to /api/qa/chat with use_llm=true; literature-only search maps to /api/qa/search with use_llm=false. The supplied screenshot has an empty question, which intentionally disables both submissions. Typing a question enables both; blank and duplicate submissions remain blocked.

The page now uses a compact header/session toolbar, independently scrolling answer history above, and a bottom composer. Questions and answers have separate styling, citations remain expandable, and source/image behavior is unchanged. Example questions fill the composer without calling the backend. Enter submits, Shift+Enter inserts a newline, and composing IME input is not submitted. Focus returns to the composer after completion.

Per the user's correction, no new input-help row or processing-status block was added. Existing busy-button behavior, failure reporting and unsaved-answer recovery remain.

Validation:
- TypeScript noEmit passed.
- Existing cloud browser suite: 18 groups passed (chat, search, source links, sessions, delete cancellation, save retry/reconciliation, upstream failures, image Markdown/queue and logout).
- New layout browser suite: 4 groups passed (activation, independent routes, keyboard/IME, independent scroll and fixed composer at 1440x1000, 1024x768 and 760x600 in light and dark themes).
- Visually inspected light empty state and dark history screenshots.

Tests use real React/Edge with synthetic cloud replies. No production QA traffic, paid model calls, packaging or desktop changes.

## 2026-10-19：0.4.35 发布后的紧凑排版调整（未打包）

- 收紧知识库标题栏、会话栏及底部输入区；输入框固定为两行高度，长输入在框内滚动。
- 推荐问题宽窗口三列、窄窗口一列；较矮窗口减少装饰，避免欢迎区被输入区遮挡。
- TypeScript 检查及知识库布局/交互 5 组测试通过。新增 1440×900、1024×704、760×600 空状态可视范围检查；原长回答、问答/检索、浅色/深色检查继续通过。
- 已人工查看 1024×704 和 760×600 截图。仅修改源码，未打包、未替换已安装 0.4.35，未访问生产知识库。
- 预览：validation-local/knowledge-layout/compact-empty-1024.png。

## 后续发布

用户随后允许本次打包；上述紧凑排版调整已随 0.4.36 打包并覆盖桌面版本，见 provenance/releases/0.4.36。
