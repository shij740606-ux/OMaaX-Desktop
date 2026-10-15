# Summary Stage Contract v1.0

## 阶段信息

- **阶段名称**: summary
- **阶段描述**: 先汇总整个 job 的 manifest、artifact、核心指标和一致性检查，再通过 DeepSeek API 将本地证据写成通俗、可追溯的结果解读报告。
- **脚本**: `apps/cli/run_summary.py`

## 输入契约

| 字段 | 类型 | 描述 |
|------|------|------|
| workspace | directory | `workspace/<job_id>` |
| manifest | json | 任一阶段 manifest；可选，用于推断 workspace/prefix |
| scope_file | json | 可选；用于额外生成指定 scope 的 summary |

Summary 必须扫描 `workspace/<job_id>/final/*.manifest.json`，而不是只依赖 identify manifest。

## 覆盖范围

Summary 应囊括所有已运行阶段，包括但不限于：

- gene family: identify, phylogeny, reference_classification, motif, domain, structure, combo
- downstream: promoter, synteny, interspecies_synteny, karyotype, pep, ortholog, ppi, kaks
- RNA-seq: rnaseq_qc, rnaseq_align, rnaseq_quant, rnaseq_de, rnaseq_enrich/fgsea, wgcna
- annotation/enrichment and utility stages when manifest exists

## 输出契约

| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.summary_report.md` | markdown | 人可读汇总报告 |
| `{prefix}.summary_evidence_index.md` | markdown | 确定性结果索引与完整性检查；不经过 LLM |
| `{prefix}.summary.evidence.json` | json | 提交给 DeepSeek 的本地事实包 |
| `{prefix}.summary.deepseek.json` | json | 模型、用量、图表覆盖率和报告校验记录 |
| `{prefix}.summary.files.tsv` | tsv | artifact 文件索引 |
| `{prefix}.summary.manifest.json` | json | 机器可读总汇总 |
| `{prefix}.{scope_id}.summary_report.md` | markdown | 指定 scope 的报告 |
| `{prefix}.{scope_id}.summary.files.tsv` | tsv | 指定 scope 的文件索引 |
| `{prefix}.{scope_id}.summary.manifest.json` | json | 指定 scope 的机器汇总 |

## Summary 的确定性层与 DeepSeek 层

确定性层做：

- 阶段状态汇总
- 输出文件索引
- 核心 metrics 抽取
- scope/family/species 一致性检查
- 缺失依赖或 artifact 检查

DeepSeek 层做：

- 读取确定性层生成的证据包，不直接扫描和猜测未知结果
- 按分析阶段逐项解释结果和每一个逻辑图
- 用短句和通俗中文说明“看到了什么、可能说明什么、不能说明什么”
- 汇总候选成员证据、限制和下一步验证建议

DeepSeek 层禁止：

- 补造本地证据中没有的数字、成员、分组、功能或文献
- 把预测、相关或候选线索写成已经证明的功能
- 在高风险或缺失阶段给出肯定结论
- 把全基因组背景数量误写成家族成员数

默认运行必须成功调用 DeepSeek API 并通过报告完整性检查；`--no-deepseek` 只用于离线测试或管理员排障，生成物必须标记为确定性回退报告。
