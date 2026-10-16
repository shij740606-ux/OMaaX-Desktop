# 服务器管理测试

在独立、无头 Edge 中运行真实 React 管理界面，服务接口使用隔离的测试夹具；不操作用户已打开的 OMaaX。

已配置 Anthropic 官方 [webapp-testing](https://github.com/anthropics/skills/tree/main/skills/webapp-testing) 技能。技能需在下一轮会话才能被自动发现；本测试入口直接使用其 `with_server.py`。

## 本机复测

在项目根目录执行：

```powershell
powershell -File desktop/qa/server-management/run.ps1 -Soak
```

依次检查类型、组件与 SSH 协议、Windows 原生后端、浏览器交互，再进行 90 秒有界稳定性测试。测试服务仅绑定 `127.0.0.1:41739`；端口被占用时停止，不复用或结束未知服务。两轮浏览器测试必须串行运行，过程中不要编辑测试界面文件，以免 Vite 重载干扰结果。

所需依赖及固定版本见 `testing-config.json`：已有 Node/Cargo 开发环境、Codex Python、Playwright 1.62.0、现有 Edge，以及已安装的 webapp-testing。Playwright 装入 `validation-local/qa-runtime/python`，不修改全局 Python，也不下载新的浏览器。

Python 测试脚本也可使用正常 Python 环境中的 Playwright。浏览器优先使用 `OMAAX_QA_BROWSER` 指定的路径，其次查找 Windows Edge，均没有时使用 Playwright 已安装的 Chromium；不会自动下载浏览器。

## 真实服务器复测

本机保留了 `validation-local/server-full-*.py` 诊断脚本及其本机辅助模块，默认不会运行。当前机器可显式使用：

```powershell
powershell -File desktop/qa/server-management/run.ps1 -Soak -LiveServers
```

这些脚本针对本机已登记的 175、176、spark 和现有凭据，仅在唯一 `~/omaax/diagnostics/` 子目录创建小文件与临时任务。它们不是随安装包发布的通用服务器测试工具；更换机器或服务器前需要核对本机配置及辅助脚本。不会进行 sudo 安装、修改已有环境、重新下载基础镜像或运行完整生物学流程。

## 结果判读

- `coverage.json` 是覆盖清单，不代表每项都经过真实服务器点击验收。
- 浏览器断言、截图和回放位于 `validation-local/server-full-qa/browser`，接口为模拟，React 组件和浏览器真实。
- Windows 原生、Linux 后端、真实 SSH、实际安装程序模型入口分别保留证据，不能与浏览器模拟测试混称完整桌面端到端验收。
- 无权限、不存在、认证失败、网络超时、镜像下载失败等应呈现对应中文解释；预期失败通过是指保护或错误处理符合断言。
- 大于 10 GB 删除使用大小元数据边界夹具验证，不创建真实大文件。续传使用预置正确的部分内容；连接中断另由协议层测试覆盖。
- 90 秒压力结果不等于长期无泄漏或所有发行版、架构、全新安装都可用。

本轮报告入口：`validation-local/server-full-qa/report.html`。初始失败、测试夹具修正及外部阻塞均保留记录；不要只读取通过计数。
