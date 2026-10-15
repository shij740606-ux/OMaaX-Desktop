# Synteny Stage Contract v1 (Gene Family Synteny Analysis)

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/synteny/analysis/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **目标物种全蛋白组 FASTA**：`{species}.pep.fa` / `{species}.pep.fasta`
  - 来源优先级：标准化/最长转录本代表集（如 `input/{job_id}/{species}.pep.fasta`、`input/{job_id}/{species}/{species}.pep.fasta`、`genome_prepare/{species}_extracted.pep.fa`） > `scope_file` 中目标物种的原始 `pep` 文件
  - 验证：文件必须存在且不为空
  - 注意：种内 MCScanX 需要全蛋白组做自比对，不能用 Identify 阶段的家族成员 FASTA 代替；同一基因座应优先只有一个代表转录本，避免 MCScanX 选择 `.m2/.m4` 等非家族成员表 ID 后导致 SPL 共线性高亮漏识别。仅在 legacy 无全蛋白组时才允许回退到 identify 产物，并应在报告中谨慎解释。

- **GFF3注释文件**：`*.gff3`
  - 来源优先级：标准化/最长转录本代表 GFF（如 `input/{job_id}/{species}_final.gff3`、`input/{job_id}/{species}/{species}.gff3`、`genome_prepare/{species}.gff3.tmp`） > `scope_file` 中目标物种的原始 GFF/GFF3
  - 自动查找规则：`input/{job_id}/{species}_final.gff3`、`input/{job_id}/{species}.gff3`、`input/{job_id}/*.gff3` 或 `input/{job_id}/*.gff`
  - Scope 模式：从 `scope_file` 的单个 `target_species` 确认物种，但执行时必须优先使用该物种的代表转录本 GFF；找不到代表文件时才回退到 scope 原始 GFF/GFF3，并在 parameters/report 中记录回退来源
  - 验证：文件必须存在且包含有效的GFF3格式

- **基因组FASTA文件**：`*.genome.fasta`
  - 来源：用户上传或自动查找
  - 自动查找规则：`input/{job_id}/*.genome.fasta` 或 `input/{job_id}/*.fasta`
  - Scope 模式：从同一目标物种读取 genome FASTA
  - 验证：文件必须存在

> Synteny 是种内分析，一次只能分析一个目标物种。若 `scope_file.target_species` 包含多个物种，应要求用户选择单个 target 或分别运行每个目标物种；不能用 `Multi.<family>` prefix 猜测物种。

### 2.2 可选输入

- **基因ID列表文件**：`*.geneID*`
  - 用途：Circos绘图的基因筛选
  - 自动查找规则：`final/{prefix}.final_members.id` > `input/{job_id}/*.geneID*`

- `--scope-file`：target/reference/comparative scope JSON；Synteny 只使用单个 target 的 pep/GFF/genome，并记录 scope 元数据

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

以下文件必须位于 `workspace/<job_id>/final/` 目录：

1. **`{prefix}.synteny_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：人可读的分析摘要、参数、统计结果、工具版本

2. **`{prefix}.synteny.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

### 3.2 synteny/analysis/ 推荐产出（可追溯中间态）

以下文件推荐位于 `workspace/<job_id>/synteny/analysis/` 目录：

1. **`{prefix}.blast`**
   - BLAST/Diamond 比对结果
   - 格式：Tab分隔的BLAST输出格式

2. **`{prefix}.gff`**
   - 过滤后的GFF文件
   - 格式：4列（染色体、基因ID、起始、终止）

3. **`{prefix}.collinearity`**
   - MCScanX 共线性分析结果
   - 格式：MCScanX标准输出格式

4. **`{prefix}.dup_class.log`**
   - 复制基因分类日志

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式（不是列表），包含以下必需产物：

- **blast**：BLAST比对文件
  - artifact: "blast"
  - path: "synteny/analysis/{prefix}.blast"
  - type: "blast"
  - size: 文件大小（字节）

- **gff**：过滤后的GFF文件
  - artifact: "gff"
  - path: "synteny/analysis/{prefix}.gff"
  - type: "gff"
  - size: 文件大小（字节）

- **collinearity**：共线性分析结果
  - artifact: "collinearity"
  - path: "synteny/analysis/{prefix}.collinearity"
  - type: "text"
  - size: 文件大小（字节）

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.synteny_report.md"
  - type: "markdown"
  - size: 文件大小（字节）

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.synteny.manifest.json"
  - type: "json"
  - size: 文件大小（字节）

### 4.2 可选 artifacts（条件触发）

- **dup_class_log**：复制基因分类日志（如果生成）
- **circos_data**：Circos绘图数据文件（如果生成）

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **gene_count**：过滤后的基因数量（整数）
- **total_alignments**：BLAST比对总数（整数）
- **unique_queries**：唯一查询基因数（整数）
- **unique_subjects**：唯一目标基因数（整数）
- **synteny_blocks**：共线性块数量（整数）
- **gene_pairs**：共线性基因对数量（整数）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀，格式：物种.家族（字符串）
- **evalue**：BLAST E-value阈值（浮点数）
- **max_target_seqs**：最大目标序列数（整数）
- **stage**：阶段名称，固定为 "synteny"（字符串）
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

以下情况视为阶段失败：

1. **输入验证失败**：蛋白序列/GFF3/基因组文件不存在或无效
2. **BLAST失败**：比对工具返回非零状态码
3. **GFF过滤失败**：无法正确过滤GFF3文件
4. **MCScanX失败**：共线性分析工具返回非零状态码
5. **文件系统错误**：无法写入输出文件

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空或包含部分成功指标
- `"artifacts"`: 可包含已成功生成的部分产物
- 报告应说明失败原因和关键日志位置

### 6.3 部分成功

如果 BLAST 成功但 MCScanX 失败：
- 状态可设为 `"partial_success"`
- 包含已生成的 BLAST 和 GFF 文件
- 记录详细的失败原因

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── synteny/
│   └── analysis/
│       ├── Ft.SPL.blast
│       ├── Ft.SPL.gff
│       ├── Ft.SPL.collinearity
│       ├── Ft.SPL.dup_class.log
│       └── Ft.SPL.circos.*
├── final/
│   ├── Ft.SPL.synteny_report.md
│   └── Ft.SPL.synteny.manifest.json
└── logs/
    └── 20250101_123500_synteny.log
```

### 7.2 关键验证点

1. **文件名精确匹配**：必须使用 `{prefix}.blast`, `{prefix}.gff`, `{prefix}.collinearity`
2. **Manifest 格式**：artifacts 必须是字典，不是列表
3. **输入依赖**：必须从 Identify 的 Manifest 获取输入文件路径
4. **工具版本记录**：必须记录 Diamond/Blastp、MCScanX 版本
5. **必需字段完整**：Manifest 必须包含所有 schema 要求的字段

### 7.3 执行参数示例

默认执行参数：
- **Diamond**: `diamond makedb --in {protein} --db {protein}` + `diamond blastp --query {protein} --db {protein} --out {prefix}.blast --outfmt 6 --max-target-seqs 5 --evalue 1e-10`
- **Blastp (备选)**: `makeblastdb -in {protein} -dbtype prot` + `blastp -query {protein} -db {protein} -outfmt 6 -out {prefix}.blast -num_alignments 5 -evalue 1e-10`
- **GFF过滤**: `awk -F "\t" '$3=="mRNA" {split($9,a,";"); print $1"\t"a[1]"\t"$4"\t"$5}' {gff3} | sed 's/ID=//' > {prefix}.gff`
- **复制基因分类**: `duplicate_gene_classifier ./`
- **MCScanX**: `MCScanX ./`

用户可通过命令行参数覆盖默认参数。
