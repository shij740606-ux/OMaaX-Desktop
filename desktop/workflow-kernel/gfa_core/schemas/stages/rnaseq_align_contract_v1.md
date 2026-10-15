# RNA-Seq Alignment Stage Contract v1

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/rnaseq_align/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **Clean FASTQ文件**：来自RNA_QC阶段的过滤后数据
  - 来源：从RNA_QC阶段的Manifest中获取
  - 格式：`*_1.clean.fastq.gz`, `*_2.clean.fastq.gz`

- **参考基因组FASTA文件**：`*.genome.fasta` 或 `*.fa`
  - 来源：用户上传
  - 验证：文件必须存在且为有效的FASTA格式

- **HISAT2索引**：`*.ht2` 系列文件
  - 来源：用户上传或预构建
  - 命名格式：`{species}.HISAT2.*.ht2`
  - 验证：索引文件必须完整

- **样本配置文件**：`samples.yaml`
  - 必须为每个样本提供 FASTQ 配对信息
  - 必须在 `rnaseq.library_strandness` 或 `samples.<sample>.library_strandness` 中显式声明链特异性
  - 允许值：`non_stranded`, `forward`, `reverse`
  - 不允许默认；缺失或非法值必须在 HISAT2 执行前失败

### 2.2 可选输入

- **参考基因组GTF/GFF注释**：`*.gtf`, `*.gff`, `*.gff3`
  - 用途：用于后续RNA_QUAN阶段的转录本组装；当 `splice_site_source=auto/annotation` 时可提取 HISAT2 known splice site

- **HISAT2 known splice site 文件**：`.ss`, `.txt`, `.tsv`
  - 用途：当 `splice_site_source=manual` 时传入 HISAT2 `--known-splicesite-infile`

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1. **`{prefix}.rnaseq_align_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：比对统计、样本覆盖率、工具版本

2. **`{prefix}.rnaseq_align.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

3. **`{prefix}.hisat2.splice_sites.ss`**（条件产出）
   - 类型：HISAT2 known splice site
   - 格式：TSV
   - 触发条件：`splice_site_source=auto/annotation` 且从 GTF/GFF 成功提取到 splice site

### 3.2 rnaseq_align/ 推荐产出（可追溯中间态）

1. **`{sample}.sam`**
   - SAM格式比对结果（中间文件）

2. **`{sample}.bam`**
   - BAM格式比对结果

3. **`{sample}.sorted.bam`**
   - 排序后的BAM文件

4. **`{sample}.sorted.bam.bai**
   - BAM索引文件

5. **`{sample}_hisat2.log`**
   - HISAT2比对日志

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式，包含以下必需产物：

- **sorted_bam**：排序后的BAM文件
  - artifact: "sorted_bam"
  - path: "rnaseq_align/{sample}.sorted.bam"
  - type: "bam"

- **bam_index**：BAM索引文件
  - artifact: "bam_index"
  - path: "rnaseq_align/{sample}.sorted.bam.bai"
  - type: "bai"

- **alignment_log**：比对日志
  - artifact: "alignment_log"
  - path: "rnaseq_align/{sample}_hisat2.log"
  - type: "text"

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.rnaseq_align_report.md"
  - type: "markdown"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.rnaseq_align.manifest.json"
  - type: "json"

### 4.2 可选 artifacts（条件触发）

- **sam_file**：SAM文件（如果需要保留中间结果）
- **known_splice_site**：HISAT2 known splice site 文件
  - artifact: "known_splice_site"
  - path: "final/{prefix}.hisat2.splice_sites.ss"
  - type: "tsv"
  - format: "hisat2_splice_site"

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **total_reads**：总reads数（整数）
- **aligned_reads**：比对上的reads数（整数）
- **unaligned_reads**：未比对上的reads数（整数）
- **alignment_rate**：比对率（浮点数）
- **unique_alignments**：唯一比对数（整数）
- **multiple_alignments**：多重比对数（整数）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀（字符串）
- **hisat2_params**：HISAT2参数（字符串）
- **samtools_params**：samtools参数（字符串）
- **splice_site_source**：known splice site 来源，允许 `auto`, `off`, `manual`, `annotation`
- **known_splice_site_status**：known splice site 状态，例如 `disabled`, `not_found`, `provided`, `generated`, `empty`
- **known_splice_site**：实际传入 HISAT2 的 splice site 文件路径；未使用时为空字符串
- **known_splice_site_input**：用户提供或自动解析到的注释/`.ss` 源文件路径；未使用时为空字符串
- **known_splice_site_count**：提取或读取到的 splice site 条目数（整数）
- **library_strandness_by_sample**：每个样本最终解析出的链特异性
- **library_strandness_signature**：当前 samples.yaml 链特异性签名，用于 status 防止复用旧结果
- **stage**：阶段名称，固定为 "rnaseq_align"（字符串）
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

1. **输入验证失败**：FASTQ/参考基因组/索引文件不存在或无效
2. **链特异性缺失或非法**：`samples.yaml` 未显式声明 `library_strandness`
3. **HISAT2失败**：比对工具返回非零状态码
4. **known splice site 准备失败**：`manual/annotation` 模式下用户提供的文件不存在或格式不支持
5. **samtools失败**：BAM处理失败
6. **文件系统错误**：无法写入输出文件

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空或包含部分成功指标
- `"artifacts"`: 可包含已成功生成的部分产物

### 6.3 部分成功

如果部分样本处理成功：
- 状态可设为 `"partial_success"`
- 包含已成功处理的样本结果

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── rnaseq_align/
│   ├── S1.sam
│   ├── S1.bam
│   ├── S1.sorted.bam
│   ├── S1.sorted.bam.bai
│   ├── S1_hisat2.log
│   ├── S2.sam
│   ├── S2.bam
│   ├── S2.sorted.bam
│   ├── S2.sorted.bam.bai
│   └── S2_hisat2.log
├── final/
│   ├── rnaseq_align_report.md
│   ├── Test.hisat2.splice_sites.ss
│   └── rnaseq_align.manifest.json
└── logs/
    └── 20250101_123500_rnaseq_align.log
```

### 7.2 关键验证点

1. **样本匹配**：输入样本与输出BAM一一对应
2. **索引完整性**：BAI文件必须与BAM文件匹配
3. **Manifest 格式**：artifacts 必须是字典，不是列表
4. **工具版本记录**：必须记录HISAT2、samtools版本

### 7.3 执行参数示例

默认执行参数：

- **HISAT2**: `hisat2 -x {hisat2_index} -1 {r1} -2 {r2} -p {cpu} --new-summary [--rna-strandness RF/FR] [--known-splicesite-infile {splice_sites.ss}] | samtools sort`
- **samtools view**: `samtools view -bS {input}.sam > {output}.bam`
- **samtools sort**: `samtools sort {input}.bam -o {output}.sorted.bam`
- **samtools index**: `samtools index {input}.sorted.bam`

用户可通过命令行参数覆盖默认参数。
