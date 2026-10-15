# RNA-seq Sample Quality Assessment Stage Contract v1

## 1. Workspace 目录

- 最终产物位于 `workspace/<job_id>/final/`
- 中间文件位于 `workspace/<job_id>/rnaseq_sample_qc/`
- 日志位于 `workspace/<job_id>/logs/`

## 2. 输入

### 必需输入

- Count matrix：
  - 默认：`final/{prefix}.count_matrix.txt`
  - 格式：第一列为 gene ID，后续列为样本 raw counts

### 可选输入

- YAML 分组配置：
  - `groups: {sample: condition}`
  - 或 `samples: {sample: {condition: condition, replicate: replicate}}`
  - 若自动填充把重复编号写进 condition（如 `Peel.1/Peel.2/Peel.3` 且 replicate 为 `1/2/3`），执行时应规范为同一生物条件 `Peel`，replicate 仅作重复信息。
- 若未提供配置，阶段从样本名推断 condition：
  - `Control_1`, `Control_2` -> `Control`
  - `Drought_1`, `Drought_2` -> `Drought`

## 3. 输出

### final/ 必须产出

- `{prefix}.rnaseq_sample_qc.manifest.json`
- `{prefix}.rnaseq_sample_qc_report.md`
- `{prefix}.rnaseq_sample_qc.vst_matrix.tsv`
- `{prefix}.rnaseq_sample_qc.pca_data.tsv`
- `{prefix}.rnaseq_sample_qc.pca_variance.tsv`
- `{prefix}.rnaseq_sample_qc.sample_correlation.tsv`
- `{prefix}.rnaseq_sample_qc.sample_distance.tsv`

### final/ 图形产物

- `{prefix}.rnaseq_sample_qc.pca.png`
- `{prefix}.rnaseq_sample_qc.pca.pdf`
- `{prefix}.rnaseq_sample_qc.sample_correlation_heatmap.png`
- `{prefix}.rnaseq_sample_qc.sample_correlation_heatmap.pdf`
- `{prefix}.rnaseq_sample_qc.sample_distance_heatmap.png`
- `{prefix}.rnaseq_sample_qc.sample_distance_heatmap.pdf`
- `{prefix}.rnaseq_sample_qc.sample_clustering.png`
- `{prefix}.rnaseq_sample_qc.sample_clustering.pdf`

### rnaseq_sample_qc/ 可复现文件

- `run_sample_qc.R`
- `sample_table.tsv`
- `commands.sh`
- `environment.txt`

## 4. Manifest 最小字段

```json
{
  "manifest_version": "1.0",
  "stage": "rnaseq_sample_qc",
  "status": "success",
  "prefix": "Os",
  "metrics": {
    "input_genes": 39049,
    "expressed_genes": 26574,
    "samples": 6,
    "conditions": 2,
    "pc1_percent": 55.1,
    "pc2_percent": 21.3
  },
  "artifacts": {}
}
```

## 5. 方法

R 层执行以下通用流程：

```r
dds <- DESeqDataSetFromMatrix(countData, coldata, design = ~ 1)
vsd <- vst(dds, blind = TRUE)
pcaData <- plotPCA(vsd, intgroup = "condition", returnData = TRUE)
sampleCor <- cor(assay(vsd))
sampleDists <- dist(t(assay(vsd)))
hc <- hclust(sampleDists)
```

样本质量评估属于探索性 QC，VST 固定使用 `blind = TRUE` 和 `design = ~ 1`；condition 仅用于 PCA/热图注释，避免把差异分析设计或误拆的重复编号带入 QC 变换。

## 6. 失败语义

失败时仍写出 `{prefix}.rnaseq_sample_qc.manifest.json`：

- `"status": "failed"`
- `"error": "<具体错误>"`

常见失败：

- count matrix 不存在
- 样本数少于 2
- count matrix 含非数值列
- R 包 `DESeq2`, `ggplot2`, `pheatmap` 缺失
