# RNA-Seq Differential Expression Stage Contract v1

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/rnaseq_de/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **基因计数矩阵**：来自RNA_QUAN阶段
  - 来源：从RNA_QUAN阶段的Manifest中获取
  - 格式：TSV（行=基因，列=样本）

- **样本与差异比较配置文件**：`samples.yaml`
  - 定义样本、分组、分组角色和比较方案
  - 示例：
    ```yaml
    samples:
      S1: {condition: Control, replicate: 1, library_strandness: reverse, group_role: control, include: true}
      S2: {condition: Control, replicate: 2, library_strandness: reverse, group_role: control, include: true}
      S3: {condition: salt, replicate: 1, library_strandness: reverse, group_role: compare, include: true}
      S4: {condition: salt, replicate: 2, library_strandness: reverse, group_role: compare, include: true}
    groups:
      S1: Control
      S2: Control
      S3: salt
      S4: salt
    experiment: {design: two_group, control_group: Control, treatment_groups: [salt]}
    compare: [["salt", "Control"]]
    de: {strategy: auto, design: two_group, control_group: Control, pairwise_modes: [control]}
    ```
  - `group_role` 允许值：`control`、`compare`、`ignore`。普通样本表路径中，所有 `include: true` 的样本必须填写 `group_role`；`ignore` 样本必须保留在 `samples` 中用于追溯，但不得进入 `groups` 和 DE 比较。

### 2.2 比较生成规则

- YAML 中存在显式 `compare` 时，`rnaseq_de` 必须严格按 `compare` 执行，不自动补充其他比较；同时必须校验 compare 引用的分组存在且不是 `ignore`。
- 未提供显式 `compare` 时，系统必须从 `group_role` 自动生成比较；缺少 `group_role`、没有唯一 `control` 或没有 `compare` 组时必须报错并提示用户补齐样本表。
- `time_series`：所有参与 DE 的样本必须填写 `time`，且必须有唯一 `control/baseline`，默认生成每个非基线时间点 vs baseline；不默认生成 adjacent 比较。
- `two_group`：必须有唯一 `control/baseline`，生成 compare group vs control。
- `multi_group`：必须有唯一 `control/baseline`，生成所有 compare group vs control；不再默认允许无 control 的 all-pairwise。
- `manual` 仅作为高级显式 compare 兼容入口；普通表格输入不要求用户选择 manual 或手写 compare。

### 2.3 可选输入

- **GTF注释文件**：`*.gtf`
  - 用途：基因ID到基因名的映射

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1. **`{prefix}.rnaseq_de_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：差异分析摘要、统计、工具版本

2. **`{prefix}.rnaseq_de.manifest.json`**
   - 类型：机器清单
   - 格式：JSON
   - 内容：完整的分析元数据

3. **`{prefix}.rnaseq_de.{comparison}.DE_results.txt`**
   - 差异表达结果
   - 格式：TSV（包含gene, log2FC, pvalue, padj等列）
   - 示例：`Os.rnaseq_de.salt_vs_Control.DE_results.txt`

4. **`{prefix}.rnaseq_de.{comparison}.DE_genes.txt`**
   - 显著差异表达基因列表
   - 默认筛选条件：|log2FC| >= 1, padj < 0.05

### 3.2 rnaseq_de/ 推荐产出（可追溯中间态）

1. **`{comparison}_deseq2.rds`**
   - DESeq2 R对象（用于后续分析）

2. **`{comparison}_MA_plot.png`**
   - MA Plot可视化

3. **`{comparison}_volcano_plot.png`**
   - 火山图可视化

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

- **de_results**：差异表达结果
  - artifact: "de_results"
  - path: "final/{prefix}.rnaseq_de.{comparison}.DE_results.txt"
  - type: "tsv"

- **de_genes**：显著差异基因列表
  - artifact: "de_genes"
  - path: "final/{prefix}.rnaseq_de.{comparison}.DE_genes.txt"
  - type: "tsv"

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.rnaseq_de_report.md"
  - type: "markdown"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.rnaseq_de.manifest.json"
  - type: "json"

### 4.2 可选 artifacts（条件触发）

- **rds_object**：DESeq2 R对象
- **ma_plot**：MA Plot图片
- **volcano_plot**：火山图图片

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **total_genes_analyzed**：分析的基因总数（整数）
- **de_genes_up**：上调基因数（整数）
- **de_genes_down**：下调基因数（整数）
- **de_genes_total**：显著差异基因总数（整数）
- **comparisons**：比较组数（整数）
- **planned_comparisons**：实际计划执行的比较列表（数组，例如 `[["salt", "Control"]]`）
- **actual_comparisons**：成功产出的比较名称列表（数组，例如 `["salt_vs_Control"]`）
- **comparison_mode**：比较生成来源（字符串，例如 `configured`、`group_role_control`）
- **effective_control_group**：本次 DE 计划的有效 reference/control 分组；普通样本表路径必须非空

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀（字符串）
- **padj_threshold**：显著性阈值（浮点数，默认0.05）
- **log2fc_threshold**：log2FC阈值（浮点数，默认1.0）
- **deseq2_params**：DESeq2参数（字符串）
- **stage**：阶段名称，固定为 "rnaseq_de"（字符串）
- **job_id**：任务ID（字符串）
- **run_id**：运行ID（字符串）
- **manifest_version**：Manifest版本，固定为 "1.0"（字符串）
- **status**：状态，"success" 或 "failed"（字符串）
- **timestamp**：时间戳，ISO格式（字符串）
- **start_time**：开始时间，ISO格式（字符串）
- **end_time**：结束时间，ISO格式（字符串）
- **elapsed_sec**：耗时，秒（浮点数）
- **cmd**：执行命令列表（数组）
- **workspace_root**：工作空间根目录路径（字符串）
- **workspace**：当前阶段工作目录路径（字符串）
- **input_dir**：输入目录路径（字符串）

## 6. 失败语义（Failure Semantics）

### 6.1 失败条件

1. **输入验证失败**：计数矩阵/分组文件不存在或格式无效
2. **DESeq2失败**：R脚本执行失败
3. **比较组无效**：指定的比较组不存在，或设计要求 control/baseline 但未提供
4. **矩阵维度不匹配**：样本数与分组不匹配

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空
- `"artifacts"`: 可包含部分产物

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── rnaseq_de/
│   ├── salt_vs_Control_deseq2.rds
│   ├── salt_vs_Control_MA_plot.png
│   └── salt_vs_Control_volcano_plot.png
├── final/
│   ├── {prefix}.rnaseq_de.salt_vs_Control.DE_results.txt
│   ├── {prefix}.rnaseq_de.salt_vs_Control.DE_genes.txt
│   ├── {prefix}.rnaseq_de_report.md
│   └── {prefix}.rnaseq_de.manifest.json
└── logs/
    └── 20250101_123500_rnaseq_de.log
```

### 7.2 DE_results.txt 格式示例

```
gene	log2FC	pvalue	padj
GeneA	2.5	0.001	0.005
GeneB	-1.8	0.003	0.010
GeneC	0.5	0.100	0.250
```

### 7.3 DE_genes.txt 格式示例

```
gene	log2FC	pvalue	padj
GeneA	2.5	0.001	0.005
GeneB	-1.8	0.003	0.010
```

### 7.4 执行参数示例

默认执行参数：

- **DESeq2**: 使用R脚本执行差异分析
  - 输入：计数矩阵 + 分组文件
  - 输出：差异基因列表 + 可视化

- **筛选条件**：
  - padj < 0.05
  - |log2FC| >= 1

用户可通过命令行参数覆盖默认参数。

### 7.5 R脚本示例

```R
library(DESeq2)
countMatrix <- read.table("count_matrix.txt", header=TRUE, row.names=1)
colData <- data.frame(condition=factor(c("Control","Control","salt","salt")))
dds <- DESeqDataSetFromMatrix(countMatrix, colData, design=~condition)
dds <- DESeq(dds)
results <- results(dds)
write.table(as.data.frame(results), file="DE_results.txt", sep="\t")
```
