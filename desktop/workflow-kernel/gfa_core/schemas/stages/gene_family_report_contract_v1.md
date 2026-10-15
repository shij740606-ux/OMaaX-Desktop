# Gene Family Report Stage Contract v1.0

## 阶段信息

- **阶段名称**: `gene_family_report`
- **脚本**: `apps/cli/run_gene_family_report.py`
- **职责**: 按用户确认的计划阶段，生成或打开可持久化的基因家族分析结果报告。

## 输入契约

| 字段 | 类型 | 必需 | 描述 |
|---|---|---|---|
| workspace | directory | 是 | 当前 job 工作目录 |
| prefix | string | 是 | 结果前缀 |
| expected_stages | non-empty stage list | 生成时是 | 用户确认的计划阶段，保持原顺序；不得从目录中已有 Manifest 反推计划 |
| stage_statuses | JSON object | 是 | 每个计划阶段的终态或当前状态 |
| job_id | string | 否 | job 身份 |
| scope_id / analysis_route / member_set_id | string | 否 | 严格证据身份 |
| rnaseq_scope_id | string | 否 | 可选 RNA-seq 证据身份 |
| action | enum | 否 | `generate` 生成；`open` 有现成报告时复用，否则生成 |

阶段不启动任何上游生物分析。至少一个同身份、同计划范围的成功 Manifest 才能生成报告。

`action=generate`，以及 `action=open` 但不存在可复用报告时，`expected_stages` 必须为非空；否则明确拒绝生成。`action=open` 且 Markdown 与报告 Manifest 已存在时可以直接复用，不要求再次提供计划。

## 输出契约

| artifact key | 类型 | 文件 |
|---|---|---|
| gene_family_report | markdown | `*.gene_family_report.md` |
| gene_family_report_manifest | manifest | `*.gene_family_report.manifest.json` |

Manifest 记录 `final|partial` 报告状态、`api|fallback_script` 生成模式和当前分析身份。`included_stages` 只包含模板支持且成功的计划阶段；成功但不在模板 13 个阶段中的计划项写入 `unsupported_stages`，在公共完成表标记“不支持”，不生成推断章节，并使报告状态保持 `partial`。API 不可用或编排越界时，`model.reason` 固定为 `api_unavailable_or_boundary_violation`。

## 确定性数据接口

`build_report_data(final_dir, prefix, planned, statuses, manifests) -> ReportData` 是模板渲染和 API 编排共用的唯一结果模型。每节把脚本确定的内容分为 `facts`、`observations`、`interpretations`、`limitations`、`recommendations`，并汇总为带稳定 `sentence_id`、原文、类型和证据编号的不可变句子。

指标优先读取同身份 Manifest 的嵌套 `metrics`。仅当模板所需指标缺失时，集中提取器才读取该指标声明的命名 artifact；不得从文件名、其他阶段或相似字段猜值。真缺失使用 `MissingMetric(label, checked_sources)`，渲染为“源结果未提供此指标（已检查：…）”。声明的数据源存在但解析失败时必须报错，不得静默写成“未解析”、`N/A` 或 `None`。

当前真实 schema 映射包括：Identify 的 `member_count` / `identified_members_count` / `final_members`（按此前顺序取首个已提供值），Phylogeny 的 `sequences_count` 与 `msa_length`，PEP 的 `protein_count`、`tasks_success` 与 `tasks_total`。Interspecies Synteny 在没有直接语义指标时只读取 Manifest 声明的 `gene_to_species` 与 `*_collinearity` 命名 artifact：以 `## Alignment` 计共线性区块，并按 TSV 的 `group=target|ref` 名单统计目标—参考家族直接配对。

## API 语言编排接口

API 输入字段仅限：

```json
{
  "sections": [
    {
      "section_id": "stage.identify",
      "sentences": [{"sentence_id": "stage.identify.status", "text": "不可改写原句"}]
    }
  ],
  "allowed_connectors": ["also", "however", "therefore"]
}
```

API 输出字段仅限：

```json
{
  "sections": [
    {
      "section_id": "stage.identify",
      "paragraphs": [
        {"sentence_ids": ["stage.identify.status"], "connector_ids": []}
      ]
    }
  ]
}
```

输出必须保持完整章节集合；每个不可变句子必须在原章节中恰好出现一次；连接语必须来自白名单，数量必须与段内句间位置一致。未知、遗漏或重复 `sentence_id`，未知连接语，额外字段，自由文本字段，章节遗漏、重复或改变，均使整份 API 编排作废并回退 `fallback_script`，不能局部采用。

## 报告生成原则

1. Manifest、结果表格和结果文件是报告事实的唯一来源。成员数、统计值、比较结果、证据等级、限制、可下结论范围和后续建议均由确定性脚本提取或生成。
2. 模型 API 默认启用，但只负责排列脚本已生成的 `sentence_id`、组织段落并选择受控 `connector_id`；API 不直接返回报告文本。不得补充、推断或修改任何数值、结果判断、生物学解释、因果关系、候选基因、证据等级、限制或建议。
3. API 输出必须通过上述严格结构与集合校验；任一边界错误都使整份 API 编排作废，并自动使用同一 `ReportData` 的默认顺序。
4. API 不可用、超时或校验失败时，不影响报告事实和章节完整性；报告明确标注 `fallback_script`，但仍使用同一模板和同一份确定性结果数据。
5. 只纳入用户计划中的阶段。源结果确实没有提供某项指标时，明确写“源结果未提供此指标”并指出所检查的来源；不得用“未解析”、`N/A` 或模型猜测代替可提取的数据。
