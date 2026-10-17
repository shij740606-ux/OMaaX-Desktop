# 教育科研桌面模块

移植自用户指定的 `192.168.0.175:8090` 前端项目
`/home/xzq/5090/teamWorkspace/zh/PlantAI-rag-new/ui/src`。
来源 Git HEAD 与提取时原始文件 SHA256 记录在 SOURCE.json；实际读取的是当时工作树，不能假定与 HEAD 内容完全相同。

保留原网页已开放的八个模块：期刊情报、投稿推荐、预审稿、审稿报告、版式检查、论文重排版、审稿回复和会议日历。
独立 Vue 构建保留组件行为及样式，通过 React 侧栏中的本地 iframe 展示；切换侧栏不卸载科研工作台。

原网页的 `/api/review/*` 返回 404。现有科研服务实际在
`/home/xzq/5090/teamWorkspace/wjq/PlantAI-RAG-new-latest/PlantAI-rag-new/review_service` 的回环端口 8100 运行。
桌面使用已选用的 175 SSH 连接和保存的主机指纹访问该端口；未改动任何服务端项目、配置或监听端口。
源码中的科研汇报和 ZIP 工程编译未在此服务完整上线，未加入桌面菜单；重排版支持单文件 .tex，并可编译和下载 PDF。

`resources/ssh/education.cjs` 仅在本机回环地址监听，页面路径使用随机访问令牌。
API 只转发固定科研路由；密码保留在原生进程/SSH 子进程内，不进入页面。
普通 HTTP DELETE 一律拒绝。删除请求必须经过父页面来源校验、原生确认对话框和独立控制凭据，且仅允许指定科研任务路径。
此科研服务尚无应用用户隔离，所以账户强制登录构建暂不开放此入口，不能作为多租户服务部署。

构建：`node node_modules/vite/bin/vite.js build --config vite.education.config.mjs`（在 desktop 下）。
输出 `resources/education` 为生成文件，不提交。Windows 发布流程会自动构建并打包。

移植时修复：会议数据回包结构、取消删除时保留本地任务、请求超时清理、SSH 通道与 HTTP 超时兼容、窄窗口菜单、面向用户的提示。
来源组件中的科学判断及期刊/会议资料按原服务返回显示，不代表另行完成了科学准确性审查。
