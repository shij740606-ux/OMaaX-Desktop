# OMaaX Desktop

OMaaX 0.4.25 开发项目，基于 0.4.20 的已核对源码继续开发。包括桌面界面、Rust/Tauri 主程序、对话插件、服务器执行代码、工具目录和九类一体化分析流程源码。

本项目从该版本安装包提取运行资源，并使用本机对应版本的原始开发源码补齐 React/TypeScript 和 Rust 文件。**不是把二进制反编译成原始源码。** 0.4.20 安装包的 89 个资源及流程包内 2,709 个可编辑文件已逐一核对；提取出的主程序与安装版一致。安装包、编译缓存和个人文件不提交 Git 历史。来源记录见 [provenance/releases/0.4.20](provenance/releases/0.4.20)。

不包含用户设置、密钥、SSH 凭据、聊天记录、分析数据或原仓库 Git 历史。社区工具保留目录和来源信息，用户选择添加时才下载；不会安装所有分析软件。

0.4.20 基线包含服务器文件浏览、环境管理、独立连接状态、中文错误说明、传输恢复及数据保护。与安装版相比，额外包含测试基础设施，以及一处原生认证测试夹具修正；生产功能实现保持对应版本。

## 0.4.25 续跑、调度与账号边界

- 固定流程每完成一个步骤，保存带哈希的独立输出快照；失败、中断、取消后可在流程记录中确认续跑。原失败输出保留，输入、计划、镜像或执行版本变化时拒绝续跑。旧任务没有快照时不能追溯恢复。
- 排队时间不消耗执行超时；同一系统账号最多同时运行 4 个任务，按 CPU 和内存声明预留资源。不同系统账号之间仍由操作系统/集群调度器协调。
- 登录账号必须由管理员绑定独立服务器系统账号；执行、文件浏览、流程上传、传输均检查 root 管理的访问绑定。没有绑定时拒绝业务访问，不自动归属历史文件。部署要求见 [账号隔离部署](provenance/releases/0.4.25/ACCOUNT-DEPLOYMENT.md)。
- 对话中连续 3 次修复失败暂停新增修复操作，状态轮询不算失败、同一失败记录不重复计数。每段达到 80 次模型步骤或累计 200,000 个已报告 tokens 后暂停下一次请求（单次响应可能跨过 token 阈值），用户确认可继续。额度不是金额保证，服务商账单为准；正在执行的服务器任务不会被该额度取消。
- 对话页展示本段调用次数和已报告用量。删除、覆盖、初始计划和续跑确认不向模型开放。

## 0.4.24 用户体验与运行环境

- 登录页使用“服务地址”和操作说明，移除早期升级弹窗；诊断日志折叠显示。工作空间加载失败提供重试，退出时忽略迟到的状态响应。
- 隔离任务可以联网下载依赖、调用在线分析服务，使用独立可写运行层及磁盘临时目录；HOME 和缓存位于任务内的可写目录，取消原 256 MiB 临时盘限制。仍保留只读输入、专属输出目录、非特权账号、能力限制与禁止主机回环访问，不挂载宿主根目录或容器管理接口。
- 删除、覆盖与固定分析计划确认不变；模型能准备环境、读取失败日志并修复重试。软件不再仅因联网限制禁用 NCBI 等步骤，实际服务可用性和账号仍需满足分析要求。
- 移除账号模式已不可用的自定义助手、外接工具和历史数据导入表单，图像及外部应用连接明确显示暂不可用，不再承诺保存后可使用。
- 已验证真实模型缺依赖修复、实际服务器运行时依赖安装、小数据分析、输入保护与跨服务器传输；生产账号登录及完整生物学流程仍单独验收。

详见 [0.4.24 验证记录](provenance/releases/0.4.24/VALIDATION.md)。

## 0.4.23 账号登录

- 使用 PlantAI 中枢用户名/密码强制登录；管理员在中枢 `/admin/` 建号，不提供注册。中枢未开启鉴权、断线或会话无效时禁止进入业务功能。
- 凭据保存在系统安全存储；对话、设置、SSH 列表和 WebView 缓存按中枢与用户 UUID 分目录。旧版目录保留且不自动迁移；切换账号重启本地智能体与界面。
- 原生调用与模型工具检查权限。`qa` 提供问答，`bioflow` 提供分析及流程对话，管理员仍受删除/覆盖保护。普通界面授权缓存最多 5 秒，后台每 15 秒检查，网络请求最多等待 12 秒；模型工具每次独立验证。
- 账号模式只开放原生受控服务器工具，旧平台全局凭据入口、任意本地终端/文件工具、外接 MCP 和未隔离远程智能体停用。受控环境配置、日志读取与修复重试继续可用。知识库入口继续隐藏。
- 现有 SSH/操作系统账号的权限不由中枢重写；同一 Windows 用户能直接读取本地文件，同一 SSH 账号能访问该账号的数据。如需操作系统级隔离，需要独立系统账号。

验证与尚未完成的真实中枢验收见 [0.4.23 验证记录](provenance/releases/0.4.23/VALIDATION.md)。

## 0.4.22 修补

- 已绑定一体化流程的对话不再收到普通问答的步数/时间收尾指令；模型工具白名单、删除确认与原始数据保护保持独立生效。
- 环境安装进程启动失败会记录失败状态和错误日志，模型可以重试，不再卡在排队状态。
- 构建时同步更新步数护栏插件，避免发布旧资源。

验证说明见 [0.4.22 验证记录](provenance/releases/0.4.22/VALIDATION.md)。

## 0.4.21 变更

- 一体化流程环境配置交给对话模型，开放依赖检查、安装方案、状态日志及已有环境登记工具。模型可按步骤调整 Conda 软件/来源及隔离安装脚本，失败后修改方案重试；登记仅在全部所选步骤检查成功后更新。
- 删除、覆盖已有文件、运行输入保护与服务器选择授权保持后端强制执行。没有开放能绕过这些保护的任意主机终端；初始分析计划仍由用户确认。基础系统组件需要的管理员权限不自动授予。
- “分析”页展示服务器最近的流程记录与结果，支持失败任务、分页、下载，以及文本、HTML、图片与 PDF 预览。HTML 预览禁止脚本和外部资源；预览上限为文本 64 KiB、图片/PDF 8 MiB，超限请下载。结果仍核对清单和哈希。
- 暂时关闭知识库导航、页面路由及自动同步；保留用户已有知识库配置和资料。

验证说明见 [0.4.21 验证记录](provenance/releases/0.4.21/VALIDATION.md)。下述 0.4.20 安装资源来源记录仅描述基线，不能作为 0.4.21 与旧安装包完全一致的声明。

## 开发与构建

需要 Node.js 24（包含 npm）、Python 3.12+、Rust stable。Windows 还需要 Visual Studio C++ Build Tools、Windows SDK 和 WebView2。Linux 需要 Tauri 的 WebKitGTK 4.1、GTK3、OpenSSL、librsvg 等系统开发依赖。

```sh
cd desktop
npm ci --ignore-scripts
npm run setup
npm run typecheck
npm run tauri:dev
```

`setup` 仅准备当前平台的 Node/uv、对话运行时和内置基础工具定义，不下载社区工具源文件、不创建用户分析环境、不安装生信软件或数据库。准备运行时需要联网。Python 可通过 `PYTHON` 环境变量指定。

Windows 生产安装包：

```sh
cd desktop
npm run release:win
```

生成路径：`desktop/src-tauri/target/release/bundle/nsis/`。Linux x86_64 使用 `npm run release:linux`，生成 deb；Linux 打包必须在 Linux 系统完成。不要用裸 `cargo build` 代替 Tauri 生产构建，否则可能缺少内嵌页面。

## 目录

| 路径 | 内容 |
| --- | --- |
| `desktop/src/renderer` | React 界面、参数编辑、对话和流程入口 |
| `desktop/src-tauri` | Rust 主程序、SSH 调用、窗口和安装配置 |
| `desktop/dsh-plugins` | 模型路由、执行约束、一体化流程对话插件 |
| `desktop/resources/ssh` | 服务器文件管理、环境配置和流程执行引擎 |
| `desktop/store` | 工具元数据、社区来源、中文分类 |
| `biodsh-core/skills` | 内置基础工具定义与脚本 |
| `desktop/workflow-kernel` | 从安装版 kernel.zip 展开的可编辑流程源码及上游许可证 |
| `desktop/workflow-extensions` | 单细胞、空间、宏基因组及表观遗传扩展 |
| `desktop/resources/workflows` | 发布用目录、源码包、校验清单 |
| `desktop/qa/server-management` | 独立浏览器测试、模拟服务与复测说明 |
| `source-inputs` | 原五组学源码输入包，供完整再生成使用 |
| `provenance` | 安装包来源、恢复方法、源文件与资源核对记录 |

部分内部包名和兼容目录沿用历史标识，以保证现有用户数据兼容；界面品牌为 OMaaX。仓库不连接旧项目远程地址。

## 修改流程

直接修改 `desktop/workflow-kernel/` 中的运行源码后，执行：

```sh
cd desktop
npm run workflow:pack
```

此命令更新 kernel.zip、catalog 中的包哈希和 bundle-manifest。修改步骤结构与参数定义应同时维护 `resources/workflows/catalog.json`。重新导入原始五流程及扩展则运行 `python scripts/build-workflow-release.py`；该操作会重新生成发布源码包，覆盖直接编辑后的包，因此请先提交修改。

## 本地测试

```sh
cd desktop
npm run typecheck
node --test scripts/workflow-navigation.test.mjs scripts/workflow-presentation.test.mjs scripts/workflow-parameter-help.test.mjs scripts/workflow-plan-request.test.mjs scripts/workflow-environment-state.test.mjs scripts/workflow-source-transport.test.mjs
python -m unittest discover -s resources/ssh -p "*_test.py"
```

服务器连接、文件、环境、任务、传输与保护的本机回归：

```sh
cd desktop
node --test scripts/server-management-ui.test.mjs scripts/server-requests.test.mjs scripts/product-help-ui.test.mjs resources/ssh/client.test.cjs resources/ssh/browse.test.cjs resources/ssh/relay.test.cjs resources/ssh/relay-recovery.test.cjs
cd src-tauri
cargo test --lib -- --test-threads=1
```

测试使用独立配置和临时文件；浏览器测试入口与依赖见 [desktop/qa/server-management/README.md](desktop/qa/server-management/README.md)。真实 SSH 测试需要专用服务器和明确配置，普通测试不会自动连接服务器。开发副本验证及未覆盖范围见 [provenance/VALIDATION.md](provenance/VALIDATION.md)。

## 许可证

根目录 `LICENSE` 保持原文。DeepSeek Harness、nf-core 等第三方代码继续按各自许可证使用；原出处与提交信息保留在流程源码、`SOURCE.md` 和上游 manifest 中。本仓库的整理不变更第三方代码权属。
