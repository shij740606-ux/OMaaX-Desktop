# RNA-seq WGCNA Stage Contract v1

## 1. Workspace 目录

- 最终产物位于 `workspace/<job_id>/final/`
- 中间文件位于 `workspace/<job_id>/rnaseq_wgcna/`
- 日志位于 `workspace/<job_id>/logs/`

## 2. 输入

### 必需输入（二选一）

- 已标准化表达矩阵：
  - 默认优先：`final/{prefix}.rnaseq_sample_qc.vst_matrix.tsv`
  - 行为 gene，列为 sample
- raw count matrix：
  - `final/{prefix}.count_matrix.txt`
  - 行为 gene，列为 sample
  - R 层使用 DESeq2 `vst(dds, blind=TRUE)`

### 可选输入

- YAML 分组配置，支持：
  - `groups: {sample: condition}`
  - `samples: {sample: {condition: condition}}`
- sample table：
  - 两列：`sample`, `condition`

## 3. 参数

- `min_mean`: 表达均值过滤阈值，默认 `1.0`
- `max_genes`: 按方差保留 top genes，默认 `8000`；设为 `0` 表示不限制
- `min_module_size`: 动态剪枝最小模块大小，默认 `30`
- `merge_cut_height`: 合并相似模块阈值，默认 `0.25`
- `powers`: soft threshold 候选 power 列表

## 4. 输出

### final/ 必须产出

- `{prefix}.rnaseq_wgcna.manifest.json`
- `{prefix}.rnaseq_wgcna_report.md`
- `{prefix}.rnaseq_wgcna.soft_threshold.tsv`
- `{prefix}.rnaseq_wgcna.gene_modules.tsv`
- `{prefix}.rnaseq_wgcna.module_sizes.tsv`
- `{prefix}.rnaseq_wgcna.module_eigengenes.tsv`
- `{prefix}.rnaseq_wgcna.module_trait_correlation.tsv`
- `{prefix}.rnaseq_wgcna.module_trait_pvalue.tsv`
- `{prefix}.rnaseq_wgcna.module_trait_padj.tsv`
- `{prefix}.rnaseq_wgcna.wgcna_summary.tsv`

### final/ 图形产物

- `{prefix}.rnaseq_wgcna.soft_threshold.png`
- `{prefix}.rnaseq_wgcna.sample_tree.png`
- `{prefix}.rnaseq_wgcna.gene_dendrogram_modules.png`
- `{prefix}.rnaseq_wgcna.module_trait_heatmap.png`

### 模块基因列表

- `{prefix}.rnaseq_wgcna.modules/{color}_module_genes.txt`

### rnaseq_wgcna/ 可复现文件

- `run_wgcna.R`
- `sample_table.tsv`
- `commands.sh`
- `environment.txt`

## 5. Manifest 最小字段

```json
{
  "manifest_version": "1.0",
  "stage": "rnaseq_wgcna",
  "status": "success",
  "prefix": "Os",
  "metrics": {
    "samples": 6,
    "input_genes": 29441,
    "genes_used": 8000,
    "soft_power": 6,
    "modules": 10
  },
  "artifacts": {}
}
```

## 6. 方法

核心 R 流程：

```r
gsg <- goodSamplesGenes(datExpr, verbose = 3)
sft <- pickSoftThreshold(datExpr, powerVector = powers, verbose = 5)
adjacency <- adjacency(datExpr, power = softPower)
TOM <- TOMsimilarity(adjacency)
dissTOM <- 1 - TOM
geneTree <- hclust(as.dist(dissTOM), method = "average")
dynamicMods <- cutreeDynamic(dendro = geneTree, distM = dissTOM,
                             deepSplit = 2, pamRespectsDendro = FALSE,
                             minClusterSize = minModuleSize)
moduleColors <- labels2colors(dynamicMods)
merge <- mergeCloseModules(datExpr, moduleColors, cutHeight = mergeCutHeight)
MEs <- merge$newMEs
moduleTraitCor <- cor(MEs, traits, use = "p")
moduleTraitPvalue <- corPvalueStudent(moduleTraitCor, nSamples = nrow(datExpr))
moduleTraitPadj <- matrix(
  p.adjust(as.vector(moduleTraitPvalue), method = "BH"),
  nrow = nrow(moduleTraitPvalue), ncol = ncol(moduleTraitPvalue),
  dimnames = dimnames(moduleTraitPvalue)
)
```

模块-性状热图单元格显示相关系数与 `moduleTraitPadj`。校正范围为当前热图中的全部模块×性状检验，方法为 Benjamini-Hochberg；原始 P 值继续写入 `module_trait_pvalue.tsv` 供追溯和兼容旧下游。

## 7. 解释限制

WGCNA 对样本数量敏感。少样本数据可以完成计算，但模块稳定性和模块-性状相关性需要谨慎解释，建议后续进行模块稳定性或重采样验证。
