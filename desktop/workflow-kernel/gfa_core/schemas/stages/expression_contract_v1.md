# Expression Stage Contract v1 (Differential Expression Analysis)

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/expression/analysis/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

1. **表达量矩阵文件**：`*.fpkm.txt` 或 `*.tpm.txt`
   - 来源：用户上传或从NCBI GEO下载
   - 格式：TSV格式，第一列为基因ID，其余列为样本表达量
   - 验证：文件必须存在且不为空

2. **基因ID列表**：`{prefix}.geneID` 或 `{prefix}.geneID_new`
   - 来源：Identify阶段的成员列表
   - 格式：每行一个基因ID
   - 注意：可能需要去除`.T01`后缀以匹配表达量矩阵中的ID
   - Scope模式：Expression 是单物种表达矩阵分析，必须只选择一个 target 物种，并优先使用 `identify/{Species}/final/{Species}.final_gfmembers.id`，不能使用多物种合并的 `final/{prefix}.final_members.id`

3. **样本信息文件**：`sample.txt`
   - 来源：用户从NCBI样本页面提取
   - 格式：两列，制表符分隔
   - 列1：样本名称（如X2_1），列2：分组名称（如X2）
   - 示例：
     ```
     X2_1	X2
     X2_2	X2
     X3_1	X3
     ```

### 2.2 可选输入

- **参考基因组基因ID文件**：用于ID格式转换（如添加/去除.T01后缀）
- **scope_file**：target/reference/comparative scope JSON；Expression 只读取单个 target 物种，reference/comparative 不参与表达矩阵筛选
- **analysis_route / member_set_id**：scope 模式的阶段身份字段；standalone FPKM/TPM Expression 在单 target scope 下必须写入 `single_species` 与对应 member set，RNA-seq 自动表达图不使用 gene-family analysis_route

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

以下文件必须位于 `workspace/<job_id>/final/` 目录：

1. **`{prefix}.gene-exp.heatmap.pdf`**
   - 类型：基因表达量热图
   - 格式：PDF
   - 内容：log2变换后的表达量热图

2. **`{prefix}.gene-cor.heatmap.pdf`**
   - 类型：基因表达相关性热图
   - 格式：PDF
   - 内容：Pearson相关系数热图

3. **`{prefix}.gene-exp.hist.pdf`**
   - 类型：基因表达量直方图
   - 格式：PDF
   - 内容：按分组展示的基因表达量条形图（带误差线）

4. **`{prefix}.gene-exp.txt`**
   - 类型：表达量数据表
   - 格式：TSV
   - 内容：提取的基因表达量数据

5. **`{prefix}.gene-cor.txt`**
   - 类型：相关性数据表
   - 格式：TSV
   - 内容：基因间Pearson相关系数矩阵

6. **`{prefix}.expression_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：人可读的分析摘要、参数、统计结果

7. **`{prefix}.expression.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

### 3.2 expression/analysis/ 推荐产出（可追溯中间态）

以下文件推荐位于 `workspace/<job_id>/expression/analysis/` 目录：

1. **`{prefix}.expression.log`**
   - R脚本执行日志

2. **原始数据备份**
   - 上传的原始表达量矩阵（如有）

## 4. Artifacts 约定

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式（不是列表），包含以下必需产物：

- **gene_exp_heatmap**：基因表达量热图
  - artifact: "gene_exp_heatmap"
  - path: "final/{prefix}.gene-exp.heatmap.pdf"
  - type: "document"
  - format: "pdf"
  - size: 文件大小（字节）

- **gene_cor_heatmap**：基因相关性热图
  - artifact: "gene_cor_heatmap"
  - path: "final/{prefix}.gene-cor.heatmap.pdf"
  - type: "document"
  - format: "pdf"
  - size: 文件大小（字节）

- **gene_exp_hist**：基因表达直方图
  - artifact: "gene_exp_hist"
  - path: "final/{prefix}.gene-exp.hist.pdf"
  - type: "document"
  - format: "pdf"
  - size: 文件大小（字节）

- **gene_exp_data**：表达量数据表
  - artifact: "gene_exp_data"
  - path: "final/{prefix}.gene-exp.txt"
  - type: "table"
  - format: "tsv"
  - size: 文件大小（字节）

- **gene_cor_data**：相关性数据表
  - artifact: "gene_cor_data"
  - path: "final/{prefix}.gene-cor.txt"
  - type: "table"
  - format: "tsv"
  - size: 文件大小（字节）

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.expression_report.md"
  - type: "markdown"
  - size: 文件大小（字节）

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.expression.manifest.json"
  - type: "json"
  - size: 文件大小（字节）

### 4.2 可选 artifacts（条件触发）

- **expression_matrix**：处理后的表达量矩阵
- **sample_info**：样本分组信息

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **genes_analyzed**：分析的基因数量（整数）
- **samples_count**：样本数量（整数）
- **groups_count**：分组数量（整数）
- **expression_min**：最小表达量（浮点数）
- **expression_max**：最大表达量（浮点数）
- **expression_mean**：平均表达量（浮点数）
- **execution_time_sec**：执行耗时（浮点数，秒）

### 5.2 parameters（至少包含）

- **prefix**：分析前缀，格式：物种.家族（字符串）
- **expression_file**：表达量矩阵文件名（字符串）
- **gene_id_file**：基因ID列表文件名（字符串）
- **sample_file**：样本信息文件名（字符串）
- **id_transform**：ID转换方式（如"T01_remove"表示去除.T01后缀）
- **stage**：阶段名称，固定为 "expression"（字符串）
- **job_id**：任务ID（字符串）
- **run_id**：运行ID（字符串）
- **manifest_version**：Manifest版本，固定为 "1.0"（字符串）
- **status**：状态，"success" 或 "failed"（字符串）
- **scope_id / scope_mode / target_species / reference_species / comparative_species / selected_species / family**：scope模式下必须写入，且 `target_species` 长度必须为1
- **analysis_route / member_set_id / stage_context / result_group**：scope模式下必须写入，用于严格状态恢复、结果分组和复现追踪；单目标 Expression 默认 `analysis_route=single_species`
- **timestamp**：时间戳，ISO格式（字符串）
- **start_time**：开始时间，ISO格式（字符串）
- **end_time**：结束时间，ISO格式（字符串）
- **elapsed_sec**：耗时，秒（浮点数）
- **cmd**：执行命令列表（数组）
- **workspace_root**：工作空间根目录路径（字符串）
- **workspace**：当前阶段工作目录路径（字符串）
- **input_dir**：输入目录路径（字符串）

## 5.3 模式边界

- **legacy / standalone 模式**：用户上传或 GEO 获取 FPKM/TPM 矩阵，消费 gene-family target-only scope，并写入 gene-family identity。
- **rnaseq_auto 模式**：由 `rnaseq_de` 自动触发表达图后处理，写入 `rnaseq_scope_id` / `foreground_mode` 等 RNA-seq context；该模式结果归属 `rnaseq_de`，不得冒充 standalone gene-family Expression 完成状态。

## 6. 失败语义（Failure Semantics）

### 6.1 失败条件

以下情况视为阶段失败：

1. **输入验证失败**：必需输入文件不存在或无效
2. **R脚本失败**：R脚本返回非零状态码
3. **数据提取失败**：无法从表达量矩阵中提取指定基因
4. **文件系统错误**：无法写入输出文件

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空或包含部分成功指标
- `"artifacts"`: 可包含已成功生成的部分产物
- 报告应说明失败原因和关键日志位置

### 6.3 部分成功

如果部分图片/表格生成成功：
- 状态可设为 `"partial_success"`
- 包含已生成的文件
- 记录详细的失败原因

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── expression/
│   └── analysis/
│       └── Ft.SPL.expression.log
├── final/
│   ├── Ft.SPL.gene-exp.heatmap.pdf
│   ├── Ft.SPL.gene-cor.heatmap.pdf
│   ├── Ft.SPL.gene-exp.hist.pdf
│   ├── Ft.SPL.gene-exp.txt
│   ├── Ft.SPL.gene-cor.txt
│   ├── Ft.SPL.expression_report.md
│   └── Ft.SPL.expression.manifest.json
└── logs/
    └── 20250101_123500_expression.log
```

### 7.2 关键验证点

1. **文件名精确匹配**：必须使用 `{prefix}.gene-exp.heatmap.pdf` 等指定文件名
2. **Manifest 格式**：artifacts 必须是字典，不是列表
3. **输入文件验证**：必须验证表达量矩阵、基因ID列表、样本信息文件都存在
4. **必需字段完整**：Manifest 必须包含所有 schema 要求的字段

### 7.3 执行参数示例

默认执行参数：
- **R脚本**: `Rscript draw_expression.R <expression_file> <gene_id_file> <sample_file> <output_prefix>`

用户可通过命令行参数覆盖默认参数。

## 8. 数据来源说明

### 8.1 NCBI GEO数据获取流程

1. 访问 NCBI Taxonomy，搜索物种拉丁名
2. 选择物种，进入 GEO DataSets 页面
3. 查找相关 Series（如 GSE111937）
4. 进入样本页面，下载 Supplementary file 中的 FPKM 数据
5. 从样本信息中提取样本名称和分组，编辑为 sample.txt

### 8.2 基因ID匹配

表达量矩阵中的基因ID可能与基因家族鉴定结果略有差异：
- 表达量矩阵：`Ftunia...` 格式
- 基因ID列表：`Ftunia...T01` 格式

解决方案：
- 使用 `sed 's/\.T01//'` 去除后缀
- 或使用 `sed 's/$/.T01/'` 添加后缀
