# Structure Stage Contract v1 (Gene Structure Visualization)

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/&lt;job_id&gt;/final/`
- 中间文件应位于 `workspace/&lt;job_id&gt;/structure/analysis/`
- 日志文件位于 `workspace/&lt;job_id&gt;/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

1. **基因ID列表文件**：`final/{prefix}.final_members.id`
   - 来源：Identify 阶段的最终产物
   - 获取方式：从 Identify 阶段的 Manifest 中获取 `artifacts.final_members_id.full_path`
   - 格式：每行一个基因ID，注释行以 `#` 开头
   - 验证：文件必须存在且包含至少一个基因ID

2. **系统发育树文件**：`final/{prefix}.tree.nwk`
   - 来源：Phylogeny 阶段的最终产物
   - 获取方式：从 Phylogeny 阶段的 Manifest 中获取 `artifacts.tree.full_path`
   - 格式：Newick 格式
   - 用途：确定基因在结构图中的排列顺序

3. **基因组注释文件**：GFF3 格式，严格命名规范 `{species_short}_final.gff3`
   - **命名规则**：从 prefix 提取物种简写 + `_final.gff3`
     - 例：`Ft.SPL` → `Ft_final.gff3`
     - 例：`At.MYB` → `At_final.gff3`
   - **来源方式一（命令行）**：`--gff3` 参数显式指定路径
   - **来源方式二（自动查找）**：脚本按以下优先级自动查找：
     1. `final/{species_short}_final.gff3`（优先）
     2. `input/{species_short}_final.gff3`（备选）
   - 格式：标准 GFF3（9列制表符分隔）
   - 验证：文件必须存在且包含有效的基因结构注释（mRNA, CDS, UTR）
   - **失败条件**：两种方式都找不到符合命名规范的文件时，阶段失败
   - Scope 模式：若传入 `scope_file`，应从 scope 中按 `selected_species`/`target_species` 读取各目标物种 GFF，可合并过滤；不再从 `prefix` 猜单一物种 GFF。

### 2.2 可选输入

- **亚家族分类文件**：自动查找仅使用 `confirmed_subfamily.tsv`
  - **来源方式一（命令行）**：`--subfamily` 参数显式指定，可用于显式查看 prediction 文件
  - **来源方式二（自动查找）**：脚本只查找 `final/` 或 `input/` 中的 `confirmed_subfamily.tsv`
  - `reference_classification.subfamily.tsv` / `geneID.subfamily` 属于 target prediction，不能被自动查找静默用于正式绘图
  - 格式：两列制表符分隔（geneID, subfamily）
  - 用途：R 绘图中按亚家族着色
  - **可选性**：找不到时不失败，使用默认着色方案


> `analysisRoute=subfamily_evidence` 用于亚家族分类证据生成，默认不消费目标物种的 prediction 或 confirmed 标签；分类后的正式重绘必须显式使用 confirmed 标签。

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

以下文件必须位于 `workspace/&lt;job_id&gt;/final/` 目录：

1. **`{prefix}.structure.png`**
   - 类型：基因结构可视化图片（PNG格式）
   - 内容：三面板组合图（进化树 + 亚家族条带 + 基因结构）
   - 要求：图片清晰可读，基因按进化树顺序排列

2. **`{prefix}.structure.pdf`**
   - 类型：基因结构可视化图片（PDF格式，矢量图）
   - 内容：与 PNG 相同内容，用于出版
   - 要求：矢量格式，支持无限放大

3. **`{prefix}.structure_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：人可读的分析摘要、统计结果、参数、输出文件列表

4. **`{prefix}.structure.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

### 3.2 structure/analysis/ 推荐产出（可追溯中间态）

以下文件推荐位于 `workspace/&lt;job_id&gt;/structure/analysis/` 目录：

1. **`{prefix}.structure.filtered.gff3`**
   - 类型：过滤后的 GFF3 文件
   - 内容：只包含目标基因的结构注释（mRNA, CDS, UTR）
   - 处理：UTR类型统一为"UTR"，移除"gene"类型行

2. **`{prefix}.structure.tsv`**
   - 类型：结构数据表格
   - 格式：TSV（7列：gene_id, feature, start, end, strand, chrom, y_pos）
   - 用途：R 绘图脚本的输入数据

## 4. Manifest Artifacts Schema（产物清单）

Scope 模式下 manifest 必须记录 `scope_id`、`scope_mode`、`scope_file`、`target_species`、`reference_species`、`comparative_species`、`selected_species`，并在 parameters 中记录实际使用的 `gff3_sources`。Structure 不应要求蛋白 FASTA 作为输入。

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式（不是列表），包含以下必需产物：

- **structure_plot_png**：PNG 格式结构图
  - artifact: "structure_plot"
  - path: "final/{prefix}.structure.png"
  - full_path: 绝对路径
  - type: "image"
  - format: "png"
  - size: 文件大小（字节）
  - sha256: 文件哈希
  - description: "Gene structure visualization (PNG)"

- **structure_plot_pdf**：PDF 格式结构图
  - artifact: "structure_plot"
  - path: "final/{prefix}.structure.pdf"
  - full_path: 绝对路径
  - type: "image"
  - format: "pdf"
  - size: 文件大小（字节）
  - sha256: 文件哈希
  - description: "Gene structure visualization (PDF)"

- **structure_report**：分析报告
  - artifact: "structure_report"
  - path: "final/{prefix}.structure_report.md"
  - full_path: 绝对路径
  - type: "report"
  - format: "markdown"
  - size: 文件大小（字节）
  - sha256: 文件哈希
  - description: "Human-readable analysis report"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.structure.manifest.json"
  - full_path: 绝对路径
  - type: "json"
  - size: 文件大小（字节）
  - description: "Machine-readable manifest"

### 4.2 可选 artifacts（条件触发）

- **structure_gff3_filtered**：过滤后的 GFF3（中间文件）
  - path: "structure/analysis/{prefix}.structure.filtered.gff3"
  - type: "gff3"
  - description: "Filtered GFF3 with only target genes (intermediate)"

- **structure_tsv**：结构数据表格（中间文件）
  - path: "structure/analysis/{prefix}.structure.tsv"
  - type: "tsv"
  - description: "Gene structure data in TSV format (intermediate)"

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **genes_total**：输入基因总数（整数）
- **genes_with_structure**：成功解析结构的基因数（整数，不超过 genes_total）
- **structure_coverage_rate**：结构覆盖率（浮点数，0.0-1.0）
- **total_cds_features**：CDS 特征总数（整数）
- **total_utr_features**：UTR 特征总数（整数）
- **avg_exon_count_per_gene**：平均每基因外显子数（浮点数）
- **avg_gene_length_bp**：平均基因长度（碱基对，浮点数）
- **avg_cds_length_bp**：平均 CDS 长度（估算，浮点数）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀，格式：物种.家族（字符串）
- **stage**：阶段名称，固定为 "structure"（字符串）
- **job_id**：任务ID（字符串）
- **run_id**：运行ID（字符串）
- **gff3_source**：输入 GFF3 文件路径（字符串）
- **gff3_naming_convention**：GFF3 命名规范（字符串，如 "Ft_final.gff3"）
- **gff3_source_sha256**：GFF3 文件 SHA256 哈希（字符串）
- **subfamily_file**：亚家族文件路径（字符串，可选，可为 null）
- **tree_file**：输入树文件路径（字符串）
- **gene_id_file**：输入基因ID文件路径（字符串）
- **gene_count**：输入基因数量（整数）
- **tree_tip_count**：树文件叶节点数量（整数）
- **Rscript_path**：R 脚本路径（字符串）
- **tool_versions**：工具版本字典（对象）
  - R: R 版本信息
  - biopython: Biopython 版本信息
- **timestamp_start**：开始时间，ISO格式（字符串）
- **timestamp_end**：结束时间，ISO格式（字符串）
- **manifest_version**：Manifest版本，固定为 "1.0"（字符串）
- **status**：状态，"success"/"partial_success"/"failed"（字符串）
- **start_time**：开始时间，ISO格式（字符串）
- **end_time**：结束时间，ISO格式（字符串）
- **elapsed_sec**：耗时，秒（浮点数）
- **cmd**：执行命令列表（数组）
- **workspace_root**：工作空间根目录路径（字符串）
- **workspace**：当前阶段工作目录路径（字符串）
- **input_dir**：输入目录路径（字符串）
- **structure_analysis_dir**：structure/analysis 目录路径（字符串）
- **final_dir**：final 目录路径（字符串）

## 6. 失败语义（Failure Semantics）

### 6.1 失败条件

以下情况视为阶段失败：

1. **输入验证失败**：
   - 找不到符合命名规范的 GFF3 文件（`{species_short}_final.gff3`）
   - gene_id 文件或 tree 文件不存在（来自上游阶段）
   
2. **GFF3 过滤失败**：无法从 GFF3 中提取目标基因结构

3. **TSV 转换失败**：没有有效的基因结构特征（CDS/UTR）

4. **R 绘图失败**：R 脚本执行错误或输出文件未生成

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 包含 `error_type: "execution_failed"`
- `"artifacts"`: 可为空或包含已成功生成的部分产物
- `"parameters"`: 包含 `error` 字段描述失败原因

### 6.3 部分成功

如果 GFF3 过滤和 TSV 转换成功，但 R 绘图失败：
- 状态设为 `"partial_success"`
- 包含已生成的中间文件（filtered GFF3, TSV）
- 记录详细的失败原因和 R 错误输出

## 7. 示例（运行结果形态）

### 7.1 目录结构示例
workspace/20260212_113122_1869424_93ee4f/
├── structure/
│   └── analysis/
│       ├── Ft.SPL.structure.filtered.gff3
│       └── Ft.SPL.structure.tsv
├── final/
│   ├── Ft.SPL.structure.png
│   ├── Ft.SPL.structure.pdf
│   ├── Ft.SPL.structure_report.md
│   └── Ft.SPL.structure.manifest.json
└── logs/
└── 20260212_113500_structure.log


### 7.2 关键验证点

1. **文件名精确匹配**：必须使用 `{prefix}.structure.png/pdf/report.md/manifest.json`
2. **GFF3 命名规范**：严格遵循 `{species_short}_final.gff3` 格式
3. **查找优先级**：GFF3 和 subfamily 文件都是 `final/` > `input/`
4. **Manifest 格式**：artifacts 必须是字典，不是列表
5. **输入依赖**：必须从 Identify 获取 gene_id，从 Phylogeny 获取 tree
6. **工具版本记录**：必须记录 R 和 Biopython 版本
7. **必需字段完整**：Manifest 必须包含所有 schema 要求的 17 个字段
8. **metrics 合理性**：`genes_with_structure` 不应超过 `genes_total`

### 7.3 执行参数示例

默认执行流程：
1. **GFF3 查找**：`final/{species_short}_final.gff3` → `input/{species_short}_final.gff3`
2. **GFF3 过滤**：`sed 's/\t\S\+UTR\t/\tUTR\t/i' | awk '$3!="gene"'` → 提取目标基因
3. **TSV 转换**：解析 GFF3 为表格，计算 Y 轴位置，按树顺序排序
4. **R 绘图**：`Rscript plot_structure.R --tree {tree} --structure {tsv} --output {prefix} [--subfamily {subfamily}]`

用户可通过命令行参数指定：
- `--gff3`：显式指定 GFF3 文件路径（覆盖自动查找）
- `--subfamily`：显式指定亚家族文件路径（覆盖自动查找）
- `--cpu`：CPU 核心数（默认 4）
- `--run-id`：运行 ID（可选，自动生成）
