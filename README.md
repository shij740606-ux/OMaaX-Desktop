# OMaaX Desktop

OMaaX 0.4.14 的独立开发项目。包括桌面界面、Rust/Tauri 主程序、对话插件、服务器执行代码、工具目录和九类一体化分析流程源码。

本项目从该版本安装包提取运行资源，并使用本机对应版本的原始开发源码补齐 React/TypeScript 和 Rust 文件。**不是把二进制反编译成原始源码。** 发布源清单中的 81 个文件在整理前已逐一校验；原安装包与备份保存在仓库外，不提交 Git 历史。

不包含用户设置、密钥、SSH 凭据、聊天记录、分析数据或原仓库 Git 历史。社区工具保留目录和来源信息，用户选择添加时才下载；不会安装所有分析软件。

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

测试只使用临时模拟文件；真实 SSH 端到端测试需要自行配置测试服务器。具体本次开发副本验证结果见 `provenance/VALIDATION.md`。

## 许可证

根目录 `LICENSE` 保持原文。DeepSeek Harness、nf-core 等第三方代码继续按各自许可证使用；原出处与提交信息保留在流程源码、`SOURCE.md` 和上游 manifest 中。本仓库的整理不变更第三方代码权属。
