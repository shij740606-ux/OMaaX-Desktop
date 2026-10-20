# 一体化流程辅助工具与环境自主配置修复

日期：2026-10-20。源码修改，未打包、未替换已安装的 0.4.39。

## 问题与修改

流程守卫和账号守卫叠加后，原生 read/grep/glob 等读取工具被整体拦截，阻碍模型阅读资料与定位配置。两层守卫现共同放行 read、grep、glob、read_image、web_fetch、web_search、skill、ask_user_question；仍检查软件登录状态。

流程说明明确：固定流程和所需分析软件保持约束；模型自行选择依赖安装方式，通过 workflow_prepare 的 packages/channels/script 配置普通用户 Conda 环境，读取 workflow_prepare_status 日志并调整后重试。远程文件仍用 server_files；本机读取工具不被误当作远端工具。

任意终端、直接写文件/编辑、绕过流程的子智能体执行、模型自行确认删除/覆盖仍不放行。服务端删除提醒、覆盖确认、固定流程源码、后台依赖校验、预算和失败重试上限没有修改。Conda 普通用户运行并不构成操作系统级隔离。

## 验证

- 守卫单元测试：20/20。
- 实际 SDK：选定流程后 read/grep/glob 读取本地测试文件成功；过期账号读取被拒绝；环境准备允许；删除自确认与任意执行仍被拦截；可信对话标识及预算上限检查通过。
- 环境模块：14/14，覆盖自定义依赖/安装脚本、配置失败后调整重试、已有环境保留、流程依赖校验。
- 本轮未调用真实模型或在远端安装依赖。未更改已安装程序，未处理另行约定下次更新的推理模式位置和系统提示词展示。

证据：validation-local/workflow-support-tools-all.log、workflow-support-tools-sdk.log、workflow-support-tools-environments.log。
备份：validation-local/before-workflow-read-tools-20261020-173030。
