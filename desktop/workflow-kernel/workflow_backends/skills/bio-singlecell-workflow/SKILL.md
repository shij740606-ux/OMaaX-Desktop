---
name: bio-singlecell-workflow
description: 基于冻结且已验证的流程包执行单细胞/单核转录组分析，支持现有数据入口选择、缺失信息收集、用户参数配置、条件分支与结果验收。用户要求运行或封装这类组学分析时使用。
---

# 单细胞/单核转录组流程

本Skill固定主要步骤，用户提供数据与必要配置并可覆盖公开参数。后端归档随Skill附带，必须保持原字节不变；不修改已测试流程包、源码、固定公式或原结果。只在任务独立目录生成配置、执行副本和验收材料。

1. 读取[运行规则](references/common.md)与[输入输出和分支](references/contract.md)，检查当前数据选择入口；不要要求用户补无关上游信息。
2. 用[参数清单](references/parameters.md)生成配置副本；集中询问当前阻塞项，其余合法用户参数直接采用，默认值记录来源。
3. 运行 `python SKILL_DIR/scripts/prepare_backend.py --destination NEW_BACKEND_DIR`。脚本校验assets/backend.json的SHA256并解包未修改后端；打印B路径。读取B/README.md；上游或高级分支再读B/docs/WORKFLOW.md。环境依赖在B requirements及环境说明中，不能只安装Skill就宣称分析环境已就绪。
4. 按contract的真实命令执行，输出在RUN_ROOT，不修改Skill。已有授权内继续，资源/分支检查适用当前任务；无输入或不满足统计前提只阻塞相应分支。
5. 按common及contract逐阶段核验实际输出，写run_manifest.json和acceptance.json，交付结果索引、运行记录和未覆盖项。实际通过不扩展为所有物种/平台/设计适用。

封装层只验证归档一致性、参数约定和入口；后端历史真实数据验收是版本证据，不代表这次用户数据运行成功。当前用户输入仍需执行后验收。

历史测试范围见[后端版本证据](references/backend-validation.md)，讨论稳定性或已测范围时读取；不能用历史通过替代本次运行验收。
