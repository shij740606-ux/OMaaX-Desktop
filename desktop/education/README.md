# OMaaX 教育科研本地模块

2026-10-22 按 EDU_MODULE_PORTING_GUIDE 迁移。九个面板继续使用现有 Vue 前端；业务代码及静态期刊/样式数据位于 `resources/education-service`，来源文件摘要记录在该目录 SOURCE.json。

- 不再查找 175 SSH 配置，不连接远端 review_service、vstore、Ollama 或云端项目。
- 本机运行独立 Python 服务，首次打开通过随软件提供的 uv 自动准备 Python 3.12 和锁定依赖；之后复用本机缓存。准备失败可重试，日志保存在本机教育科研目录。
- 原生层读取已保存的直连模型凭据，管道传给后台。默认 DeepSeek，支持已有 offlineBaseUrl/offlineModel 配置。密钥不写入前端、命令行或源码，不调用公司模型授权接口。
- PDF 由 PyMuPDF 本地解析；期刊候选来自本地目录，由模型按范围说明排序，不依赖向量服务器。
- 1056 本目录、5 本精选规则、27 个会议条目、14 套样式随源码携带。日期和指标沿用源数据，不声称实时更新。
- SQLite、论文修改稿、编译产物和 PPT 保存在当前本机资料目录的 education 子目录。不会读取、覆盖或迁移 175 上的用户任务数据库。生成产物不按旧服务的七天期限自动删除。
- 本地端口固定复用以保留投稿台账 localStorage；每次启动重置访问令牌。后台另有独立令牌，普通 HTTP DELETE 仍被拒绝，任务删除继续由软件原生确认。
- LaTeX 编译按需下载官方 Tectonic 0.17 并校验 SHA256。首次编译还可能下载 TeX 宏包，需要网络；不需要服务器或管理员权限。EPS 外部转换工具仍是可选项，缺失时给出错误，不伪装编译成功。

测试：`python desktop/scripts/education-local-test.py`（使用本地测试环境）；Node 教育科研和预算回归测试；原生 cargo check 和前端类型检查。构建前端资源仍使用 `vite.education.config.mjs`。本次不制作安装包或替换桌面软件。
