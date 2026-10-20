# 服务器执行与三档模式实施进度

日期：2026-10-20。状态：开发与验收中，未发布、未替换桌面。

## 基线与备份

- 本次开始时桌面实际安装 0.4.37；桌面已有 0.4.38 安装包，两者不能混同。
- 源码版本字段仍为 0.4.38，不能据此推断本次改动已打包。
- 修改前完整源码备份：`validation-local/before-engine-modes-20261020-150159`，4359 个文件，附清单与 SHA256。
- 保留此前未提交修改；本次未提交或推送 GitHub，未修改用户配置、账号数据和对话记录。

## 已实现的源码改动

### Conda

- 识别用户环境、CONDA_EXE、当前 Python 邻接 Conda，以及常见共享安装目录。
- 优先复用已有 Conda，不再要求先下载 Micromamba。
- 新环境始终使用独立用户目录，不修改公共基础环境。
- 先尝试已有环境的精确依赖闭包和已校验缓存包，再尝试普通离线求解，最后尝试联网安装。
- 精确依赖匹配使用 Conda 自身 MatchSpec；校验缓存包哈希，失败时不发布 ready.json。
- Nextflow 子环境使用已选 Conda，保留共享缓存，不再执行全局缓存清理。

### Slurm 与日志

- 普通分析任务及流程环境准备可通过 sbatch 提交，不因调度失败回落到登录节点执行。
- 保存提交摘要、作业编号和状态；重复请求复用原作业。
- 丢失提交响应时，通过唯一作业名核对 squeue/sacct；不直接重复投递。
- 在计算节点校验作业账号、状态和节点分配；继承连接入口的控制目录标识。
- 排队交给 Slurm，执行超时与排队时间分离。
- 恢复前核实原作业已终止；保留旧调度记录、日志和结果。
- 日志由原先 10 MiB 后丢弃后续内容，改成完整分段保留；支持按分段和偏移读取。

### 模式与交互

- 公司模型代理支持新 models 响应（enabled 可以省略），按 modelEffort 的真实能力开放档位。
- 界面低、中、高分别传输 effort=low/high/max；真实模型标识只在代理内部使用。
- 模式跟随会话的 SDK 模型选择，不使用跨会话共享的单一模式变量。
- 对话插件记录实际 source.kind=user 的任务指令；显式不执行及仅讨论方案的指令阻止自动提交。
- 模型准备固定计划后，原生端根据该会话真实指令提交，不向模型开放确认接口。
- 原生与远端保存指令来源及执行记录。该链路仍需完整真实 SDK→固定流程验收；语义判断还依赖模型理解用户要求，关键词保护只是附加防线。

### 删除

- 对话工具生成的新删除请求使用展示回执：列明路径、范围和精确字节数后才能移入回收站。
- 界面不在可见范围内时不自动处理；可见提醒展示 5 秒后提交回执，允许取消。
- 只有所属对话可自动提交展示回执；服务器管理查看其他对话的请求不会自动执行。
- 原生拒绝模型调用回执、确认和拒绝接口；远端复核请求摘要及实际文件快照。
- 保留回收站、跨盘副本校验、正在使用的文件保护和原数据默认另存规则。
- 手动文件管理及历史请求沿用原有明确操作方式；已有文件覆盖仍采用原有确认，尚未完成方案中统一的覆盖通知改造。
- 普通 Conda 执行继承 SSH 账号权限；上述保护不是 OS 沙箱，不承诺任意脚本都不能删改文件。

## 验收证据

### 实际集群

目标为用户授权的 `10.1.218.163:22`，普通账号；所有测试限于
`~/omaax/diagnostics/engine-modes-20261020`。

1. 作业 893204：node17，确认共享 HOME、系统 Python 3.6.8、软件源 DNS 不可用。
2. 作业 893205：公共 `/jinxianstor/apps/Anaconda3` 的 Python 3.12.3、Conda 24.11.3 可在计算节点运行。
3. 作业 893208：缓存普通求解失败，联网 DNS 也失败；保留此失败证据。
4. 作业 893209：改为精确依赖缓存后，离线创建独立 Python/PyYAML 环境并执行成功，无 sudo/Podman。
5. 作业 893211：通过修改后的任务引擎提交；断开 SSH 后在 node17 完成，重连取得 succeeded、日志和 `SLURM_RUNNER_OK` 结果。
   测试使用实际任务引擎，只有控制目录重定向到诊断目录；不是正式用户目录发布。
6. 作业 893225：48 项 Linux 专项回归全部通过。
7. 作业 893229：184 项 Python 回归全部通过，无跳过。此前固定 UID=1000 的测试假设导致失败，已修正为当前测试账号 UID，未修改生产权限。

对应本地原始记录：

- `validation-local/cluster-placement-20261020.json`
- `validation-local/cluster-environment-20261020.json`
- `validation-local/cluster-conda-cache-result.json`
- `validation-local/cluster-conda-explicit-result.json`
- `validation-local/cluster-runner-result.json`
- `validation-local/cluster-all-r2-result.json`

### 本地及云端

- 最新 Windows Python：185 项，164 通过、21 项 Linux 专属检查跳过；`engine-regression-windows-r5.log`。
- 原生后端：42 通过、2 项既有忽略；`engine-native-tests-r2.log`。首次沙箱运行受系统凭据库权限影响，终止该测试进程后在正常权限下通过。
- 前端与流程守卫：36 项通过；`engine-ui-guard-final.log`。
- 前端类型检查通过。
- 删除提醒真实浏览器：屏幕外不执行、可见范围回执、仅执行一次、取消阻止执行，4 项通过；测试无真实删除。
- SDK 模拟公司接口：三档传输、回答、工具协议、用量返回通过。
- 公司真实 `/api/llm/models` 返回 low/high/max，默认 high。
- 三档各一次限长真实模型请求均返回 HTTP 200、非空回答及用量；合计报告 164 tokens。
- 云端记录：`company-mode-capabilities-live.json`、`company-three-modes-live.json`。不记录账号密码或令牌。

注意：Linux 184 项及原生 42 项之后仍有小范围修改（调度查询回退、引擎哈希、删除自动展示作用域等），发布前需要对最终源码重跑相关检查，不能把较早一轮测试当成最终包校验。

## 2026-10-20 最终工程验收更新

原先未完成的预处理放置、完整自动提交和分步骤恢复已完成小数据验收：

- 新增持久化预处理作业，Slurm 输入扫描、校验、计划与恢复校验都在计算节点执行。客户端返回操作编号，后台准备完成后继续提交，未增加重复审批。
- 完整 SDK → 可信用户事件 → 原生连接 → Slurm 准备 → 两步固定流程 → 结果读取通过。原生链路作业预处理编号 893252，任务 95f95669-6734-45a5-941a-11ee0bf961a5，9 个结果文件。模型回复采用确定性测试服务；三档真实公司模型调用独立验证。
- 第一检查点后强制终止监督进程，重连识别中断。发现并修复快照内部绝对链接误判；恢复预处理作业 893246 在 node17 执行，任务完成，首步骤日志和输入哈希均不变，保留失败输出。
- 集群无法访问外部软件源时，公共 Conda 缓存创建 Python/PyYAML/Pandas 环境成功；没有 sudo 或 Podman。
- 最终 Linux 192/192；Windows 167 通过、25 Linux 专属跳过；原生 42 通过、3 默认忽略；JavaScript 109/109；界面 78/78；删除通知界面 4/4；类型检查通过。
- 发布版本 0.4.39。安装和资源校验以 `provenance/releases/0.4.39/installation-verification.json` 为准；更新日志见同目录。

最终证据：

- `validation-local/native-chain-r4/acceptance.json`
- `validation-local/cluster-fault-acceptance-r2.json`
- `validation-local/cluster-final-regression-result.json`
- `validation-local/engine-regression-windows-release.log`
- `validation-local/engine-native-tests-final.log`
- `validation-local/engine-js-full.log`
- `validation-local/browser-release-r3.log`
- `validation-local/delete-notice-final.log`

仍明确保留的边界：没有对集群断电或重启做破坏性测试；小数据不代表全规模科学验收；缓存缺少软件时仍需可达软件源或预先准备依赖，未实现任意跨机器离线搬运。手动文件管理与覆盖继续使用既有确认机制。Conda 普通权限执行不等同操作系统级文件隔离。

## 后续操作约束

- 用户已经授权继续实现、真实集群小任务测试及最终打包替换，不需要重新询问一般权限。
- 不使用 sudo，不强制容器隔离，不恢复软件账号与 SSH 账号绑定。
- 不输出或写入测试报告中的 SSH 密码、云端令牌、内测账号密码。
- 不在测试中删除真实用户输入；保留失败证据。
- 替换桌面时保留配置、对话；0.4.37 仅保留最近一份回退安装包，安装证据单独记录。
