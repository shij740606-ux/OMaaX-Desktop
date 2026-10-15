# Karyotype Stage Contract v1 (Gene Distribution on Chromosomes)

## 1. Workspace 目录归属

- 最终产物：`workspace/<job_id>/final/`
- 中间文件：`workspace/<job_id>/karyotype/analysis/`
- R脚本输出：`workspace/<job_id>/karyotype/analysis/`

## 2. 输入（Input Contract）

### 2.1 必需输入（来自上游阶段）

1. **GFF基因位置文件**：`synteny/analysis/{species_prefix}.gff`
   - 来源：Synteny 阶段（任务1）
   - 格式：`chr\tgene_id\tstart\tend`（制表符分隔，无表头）
   - Scope 模式：优先从 `scope_file.target_species[*].gff` 读取目标物种 GFF/GFF3，并在本阶段生成简化 GFF；不要从 `Multi.<family>` prefix 推断物种

2. **基因ID列表**：`final/{prefix}.final_members.id`
   - 来源：Identify 阶段
   - 格式：一行一个 gene_id（无表头）

3. **基因组FASTA**：`input/<job_id>/{species}.genome.fasta`
   - 用于计算染色体长度
   - Scope 模式：优先从 `scope_file.target_species[*].genome` 读取目标物种 genome FASTA

> Karyotype 是 target-side 阶段。多 target scope 应按每个 target 物种分别输出 `{Species}.{Family}.karyotype.pdf/png/report`，并在总 manifest 中记录每个物种的成功/失败；不能把多个物种染色体合并成一张图。

### 2.2 可选输入

- **串联重复基因文件**：`synteny/analysis/{species_prefix}.tandem`
  - 来源：MCScanX dup_class.log 提取
  - 格式：每行 `gene1,gene2`（逗号分隔）
  - 用于标记串联重复基因（红色点）

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（四件套）

| 文件 | 说明 |
|------|------|
| `{prefix}.karyotype.pdf` | 核型图（PDF，下载用） |
| `{prefix}.karyotype.png` | 核型图（PNG，前端展示） |
| `{prefix}.karyotype_report.md` | 分析报告 |
| `{prefix}.karyotype.manifest.json` | 机器清单 |

### 3.2 karyotype/analysis/ 中间文件

- `{species_prefix}.gff` - 基因位置文件（软链接或复制）
- `{prefix}.geneID` - 家族基因ID列表
- `genome.len` - 染色体长度文件
- `{species_prefix}.tandem` - 串联重复基因文件（可选）

## 4. 执行流程

```
[Stage 1] 数据准备
  Input:  synteny/analysis/{species_prefix}.gff
          final/{prefix}.final_members.id
          input/{species}.genome.fasta
  Output: karyotype/analysis/{species_prefix}.gff
          karyotype/analysis/{prefix}.geneID
          karyotype/analysis/genome.len
          karyotype/analysis/{species_prefix}.tandem (optional)
  
[Stage 2] 绘图
  Input:  karyotype/analysis/*
  Output: karyotype/analysis/{prefix}.karyotype.pdf
          karyotype/analysis/{prefix}.karyotype.png
  Script: Rscript scripts/karyogram_gene_family_ggplot.R

[Stage 3] 报告生成
  Input:  绘图结果
  Output: final/{prefix}.karyotype_report.md
          final/{prefix}.karyotype.manifest.json
```

## 5. R脚本调用规范

```bash
Rscript scripts/karyogram_gene_family_ggplot.R \
  <gff_file> \
  <gene_id_file> \
  <genome_len_file> \
  <tandem_file> \
  <output_pdf> \
  [label_gap_mb]
```

参数说明：
- `gff_file`: 基因位置文件路径
- `gene_id_file`: 家族基因ID列表路径
- `genome_len_file`: 染色体长度文件路径
- `tandem_file`: 串联重复基因文件路径（可传空文件）
- `output_pdf`: 输出PDF文件路径
- `label_gap_mb`: 标签最小间距（单位Mb，默认2）

## 6. Manifest Schema

### 6.1 必选 artifacts

- `karyotype_pdf` - 核型图PDF
- `karyotype_png` - 核型图PNG
- `karyotype_report` - 分析报告
- `manifest` - 清单自身

### 6.2 metrics

- `chromosome_count` - 染色体数量
- `gene_count` - 家族基因数量
- `tandem_count` - 串联重复基因数量（如有）
- `chromosomes` - 染色体列表及基因分布

### 6.3 parameters

- `label_gap_mb` - 标签间距参数
- `species_prefix` - 物种前缀
- `scope_id` / `scope_mode` / `scope_file` - Scope 模式下必须记录
- `target_species` / `reference_species` / `comparative_species` / `selected_species` - Scope 模式下必须记录

Scope 模式下 `metrics.species` 必须列出每个 target 物种的绘图状态。

## 7. 失败语义

- **failed**: 输入文件缺失或R脚本执行失败
- **partial_success**: 缺少串联重复数据但仍生成图
- **success**: 完整成功

## 8. 依赖关系

```
Identify ──► Synteny ──► Karyotype
    │                       ▲
    └───────────────────────┘
    (final_members.id)
```

Karyotype 阶段**依赖** Synteny 阶段的输出（GFF文件）和 Identify 阶段的输出（基因ID列表）。
