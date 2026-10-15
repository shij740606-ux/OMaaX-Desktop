# RNA-Seq Quantification Stage Contract v1

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/rnaseq_quant/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **排序后的BAM文件**：来自RNA_ALN阶段
  - 来源：从RNA_ALIGN阶段的Manifest中获取
  - 格式：`.sorted.bam`
  - 验证：文件必须存在且已建立索引

- **参考基因组GTF注释文件**：`*.gtf`
  - 来源：用户上传
  - 验证：文件必须存在且为有效的GTF格式

- **样本配置文件**：`samples.yaml`
  - 必须在 `rnaseq.library_strandness` 或 `samples.<sample>.library_strandness` 中显式声明链特异性
  - 允许值：`non_stranded`, `forward`, `reverse`
  - 当样本存在混合链特异性时，必须按链特异性分组运行 featureCounts，并合并回统一的 gene count matrix

### 2.2 可选输入

- **参考基因组FASTA文件**：`*.genome.fasta`
  - 用途：StringTie转录本组装

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1. **`{prefix}.rnaseq_quant_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：定量统计、基因覆盖度、工具版本

2. **`{prefix}.rnaseq_quant.manifest.json`**
   - 类型：机器清单
   - 格式：JSON
   - 内容：完整的分析元数据

3. **`{prefix}.count_matrix.txt`**
   - 基因计数矩阵
   - 格式：TSV（行=基因，列=样本）

4. **`{prefix}.fpkm_matrix.txt`**
   - FPKM表达矩阵
   - 格式：TSV

5. **`{prefix}.tpm_matrix.txt`**
   - featureCounts-derived TPM表达矩阵
   - 用途：当 transcript-aware TPM 不可用或桥接 QC 未通过时作为表达展示 fallback
   - 格式：TSV

6. **`{prefix}.featurecounts.txt.summary`**
   - featureCounts 汇总统计
   - 格式：TSV

7. **`{prefix}.featurecounts.command.txt`**
   - featureCounts 命令记录
   - 格式：Text

8. **`{prefix}.rnaseq_quant.matrix_bridge_qc.tsv`**
   - featureCounts count matrix 与 Salmon/Kallisto tximport TPM 的桥接 QC
   - 检查：样本列完全一致、gene ID overlap、TPM 列和是否约等于 1e6、tx2gene fallback 比例、Salmon duplicate transcript removal
   - 作用：决定 expression 阶段默认 TPM 来源
   - 格式：TSV（metric/value）

### 3.2 rnaseq_quant/ 推荐产出（可追溯中间态）

1. **`{sample}_stringtie.gtf`**
   - StringTie组装结果

2. **`{sample}_coverage.txt`**
   - 覆盖度文件

3. **`{sample}_ballgown/`**
   - ballgown数据目录（可选）

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

- **count_matrix**：基因计数矩阵
  - artifact: "count_matrix"
  - path: "final/{prefix}.count_matrix.txt"
  - type: "tsv"

- **fpkm_matrix**：FPKM表达矩阵
  - artifact: "fpkm_matrix"
  - path: "final/{prefix}.fpkm_matrix.txt"
  - type: "tsv"

- **tpm_matrix**：TPM表达矩阵
  - artifact: "tpm_matrix"
  - path: "final/{prefix}.tpm_matrix.txt"
  - type: "tsv"
  - status: "computed_fpkm_tpm"

- **matrix_bridge_qc**：featureCounts raw counts 与 tximport TPM 的一致性检查
  - artifact: "matrix_bridge_qc"
  - path: "final/{prefix}.rnaseq_quant.matrix_bridge_qc.tsv"
  - type: "tsv"
  - 通过条件：样本列完全一致，gene overlap vs featureCounts ≥95%，TPM 每列和与 1e6 的偏差 ≤1，tx2gene fallback ≤5%

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.rnaseq_quant_report.md"
  - type: "markdown"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.rnaseq_quant.manifest.json"
  - type: "json"

- **featurecounts_summary**：featureCounts 汇总统计
  - artifact: "featurecounts_summary"
  - path: "final/{prefix}.featurecounts.txt.summary"
  - type: "tsv"

- **featurecounts_command**：featureCounts 命令记录
  - artifact: "featurecounts_command"
  - path: "final/{prefix}.featurecounts.command.txt"
  - type: "text"

### 4.2 可选 artifacts（条件触发）

- **stringtie_gtf**：StringTie GTF文件
- **ballgown_data**：ballgown数据目录
- **featurecounts_group_{strandness}**：混合链特异性时的分组 featureCounts 原始输出
  - path: "final/{prefix}.featurecounts.{strandness}.txt"
  - type: "tsv"
- **featurecounts_group_{strandness}_summary**：混合链特异性时的分组 featureCounts summary
  - path: "final/{prefix}.featurecounts.{strandness}.txt.summary"
  - type: "tsv"
- **featurecounts_group_{strandness}_command**：混合链特异性时的分组 featureCounts command log
  - path: "final/{prefix}.featurecounts.{strandness}.command.txt"
  - type: "text"
- **salmon_tpm_matrix / kallisto_tpm_matrix**：tximport 汇总后的 gene-level TPM
  - path: "final/{prefix}.{salmon|kallisto}.tpm_matrix.txt"
  - type: "tsv"
  - 用途：当 `matrix_bridge_qc.status=pass` 时作为 expression 展示默认 TPM
- **salmon_tx2gene / kallisto_tx2gene**：最终用于 tximport 的 transcript-to-gene 映射
  - path: "final/{prefix}.{salmon|kallisto}.tx2gene.tsv"
  - type: "tsv"

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **total_genes**：检测到的基因总数（整数）
- **total_transcripts**：检测到的转录本总数（整数）
- **expressed_genes**：表达的基因数（整数）
- **samples_processed**：处理的样本数（整数）
- **matrix_bridge_qc**：桥接 QC 结果对象，至少包含 `status`、`sample_columns_match`、`gene_overlap_vs_featurecounts_pct`、`tpm_column_sums_within_tolerance`、`recommended_expression_tpm_source`

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀（字符串）
- **stringtie_params**：StringTie参数（字符串）
- **featurecounts_params**：featureCounts参数（字符串）
  - paired-end 数据必须包含 `-p --countReadPairs -B -C`
- **featurecounts_strandness**：映射到 featureCounts `-s` 的链特异性
- **featurecounts_grouped**：是否按链特异性分组运行 featureCounts（布尔值）
- **featurecounts_groups**：混合链特异性时每个 featureCounts 分组的 `strandness`、`featurecounts_s`、`samples`、`output`、`summary`、`command_log`
- **library_strandness_by_sample**：每个样本最终解析出的链特异性
- **library_strandness_signature**：当前 samples.yaml 链特异性签名，用于 status 防止复用旧结果
- **stage**：阶段名称，固定为 "rnaseq_quant"（字符串）
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

## 6. 默认证据链与矩阵政策

### 6.1 默认下游统计链

`HISAT2 BAM -> featureCounts raw counts -> DESeq2 -> DE genes -> GO/KEGG/FGSEA/WGCNA/candidate`

- 差异表达和所有基于显著 DE gene 的下游统计默认使用 `{prefix}.count_matrix.txt`
- tximport counts 默认不替代 DESeq2 输入，除非用户显式覆盖分析策略

### 6.2 默认表达展示链

`Salmon/Kallisto quant -> tximport gene-level TPM -> expression plots`

- expression 阶段只有在 `{prefix}.rnaseq_quant.matrix_bridge_qc.tsv` 的 `status=pass` 时，才默认使用 `{prefix}.{engine}.tpm_matrix.txt`
- 若桥接 QC 不通过、abundance track 不存在或 tx2gene fallback >5%，expression 阶段必须回退到 `{prefix}.tpm_matrix.txt`
- FPKM 保持 featureCounts-derived，不由 Salmon 产生

### 6.3 tx2gene 解析优先级

1. 用户提供的 `--tx2gene`
2. FASTA header 中的 `gene:`, `gene=`, `gene_id:`, `gene_id=`, `Parent=`, `locus_tag=`, `Name=`
3. GFF3/GTF annotation 中的 `ID/transcript_id` 与 `Parent/gene_id/gene/locus_tag/Name`
4. transcript ID 后缀规则 fallback

fallback 比例超过 5% 时不得默认使用 tximport TPM 作为 expression 展示矩阵。

## 7. 失败语义（Failure Semantics）

### 7.1 失败条件

1. **输入验证失败**：BAM/GTF文件不存在或无效
2. **链特异性缺失或非法**：`samples.yaml` 未显式声明 `library_strandness`，或链特异性值不在允许范围内
3. **StringTie失败**：组装工具返回非零状态码
4. **featureCounts失败**：计数工具返回非零状态码
5. **矩阵生成失败**：无法生成表达矩阵

### 7.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空
- `"artifacts"`: 可包含部分产物

## 8. 示例（运行结果形态）

### 8.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── rnaseq_quant/
│   ├── S1_stringtie.gtf
│   ├── S1_coverage.txt
│   ├── S2_stringtie.gtf
│   ├── S2_coverage.txt
│   └── ...
├── final/
│   ├── Ft.count_matrix.txt
│   ├── Ft.fpkm_matrix.txt
│   ├── Ft.tpm_matrix.txt
│   ├── Ft.salmon.tpm_matrix.txt
│   ├── Ft.salmon.tximport_counts.txt
│   ├── Ft.salmon.length_matrix.txt
│   ├── Ft.salmon.tx2gene.tsv
│   ├── Ft.rnaseq_quant.matrix_bridge_qc.tsv
│   ├── Ft.featurecounts.txt.summary
│   ├── Ft.featurecounts.command.txt
│   ├── Ft.featurecounts.forward.txt
│   ├── Ft.featurecounts.forward.command.txt
│   ├── Ft.featurecounts.reverse.txt
│   ├── Ft.featurecounts.reverse.command.txt
│   ├── rnaseq_quant_report.md
│   └── rnaseq_quant.manifest.json
└── logs/
    └── 20250101_123500_rnaseq_quant.log
```

### 8.2 计数矩阵格式示例

```
gene_id	S1	S2	S3
GeneA	10.5	12.3	8.7
GeneB	5.2	6.1	4.8
GeneC	100.0	95.2	110.5
```

### 8.3 执行参数示例

默认执行参数：

- **StringTie**: `stringtie -G {gtf} -o {output}.gtf -A {coverage}.txt {input}.bam`
- **featureCounts**: `featureCounts -t exon -g gene_id -a {gtf} -o {output}.txt -p --countReadPairs -B -C -s {0|1|2} {bam1} {bam2} ...`
- **混合链特异性**: 按 `non_stranded/forward/reverse` 分组分别运行 featureCounts，再合并为统一 `count_matrix`
- **矩阵生成**: 使用Python脚本将featureCounts输出转换为 count/FPKM/featureCounts-derived TPM；使用 Salmon/Kallisto + tximport 生成 gene-level TPM 展示矩阵；使用 matrix_bridge_qc 决定 expression 默认 TPM 来源

用户可通过命令行参数覆盖默认参数。
