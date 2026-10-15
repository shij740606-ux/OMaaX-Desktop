# RNA-seq fgsea Stage Contract v1

## 1. Workspace 目录

- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件必须位于 `workspace/<job_id>/rnaseq_fgsea/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入

### 必需输入

- DESeq2 全量差异表达结果：
  - `final/{prefix}.rnaseq_de.{comparison}.DE_results.txt`
  - 必需列：`gene_id`, `log2FoldChange`
- KO 背景注释：
  - `final/{prefix}.gene2ko.txt` 或 `final/gene2ko.txt`
  - 格式：两列 TSV，基因 ID 与 KO ID
- 本地 KEGG KO pathway 缓存：
  - `data/kegg_pathway_cache/link_pathway_ko.tsv`
  - `data/kegg_pathway_cache/list_pathway_ko.tsv`

### 可选输入

- `comparison`：指定单个比较组；不指定时处理所有 `{prefix}.rnaseq_de.*.DE_results.txt`
- `species_name`：物种拉丁名或常用名，用于推断 KEGG organism code
- `kegg_organism`：KEGG organism code，例如水稻 `osa`、拟南芥 `ath`
- `kegg_species_cache_dir`：物种级 KEGG pathway 缓存目录，默认 `data/kegg_species_cache/`
- `kegg_mode`：`auto`/`species`/`ko`，默认 `auto`
- `kegg_filter_scope`：`plant`/`all`，默认 `plant`
- `min_size`：默认 15
- `max_size`：默认 500
- `nperm`：默认 10000
- `seed`：默认 1

## 3. 输出

### final/ 必须产出

- `{prefix}.rnaseq_fgsea.manifest.json`
- `{prefix}.rnaseq_fgsea_report.md`
- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_kegg.tsv`
- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_summary.tsv`

### final/ 条件产出

如果存在可绘制的结果：

- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_kegg_dotplot.png`
- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_kegg_dotplot.pdf`
- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_kegg_top_enrichment.png`
- `{prefix}.rnaseq_fgsea.{comparison}.fgsea_kegg_top_enrichment.pdf`

### rnaseq_fgsea/ 可复现文件

- `run_fgsea_kegg.R`
- `commands.sh`
- `environment.txt`

## 4. Manifest 最小字段

```json
{
  "manifest_version": "1.0",
  "stage": "rnaseq_fgsea",
  "status": "success",
  "prefix": "Os",
  "job_id": "job_xxx",
  "parameters": {
    "collection": "kegg",
    "min_size": 15,
    "max_size": 500,
    "nperm": 10000,
    "seed": 1,
    "species_name": "Oryza sativa",
    "kegg_organism": "osa",
    "kegg_mode": "auto",
    "kegg_filter_scope": "plant"
  },
  "metrics": {
    "comparisons": 1,
    "ranked_genes": 39049,
    "genes_with_pathway_in_ranks": 5346,
    "pathways_tested": 100,
    "significant_pathways_padj_0_05": 3
  },
  "artifacts": {}
}
```

## 5. 失败语义

失败时仍必须写出 `{prefix}.rnaseq_fgsea.manifest.json`，并设置：

- `"status": "failed"`
- `"error": "<具体错误>"`

常见失败条件：

- 缺少 `DE_results.txt`
- 缺少 `gene2ko.txt`
- 缺少 KEGG pathway 缓存
- R 环境中缺少 `fgsea`, `data.table` 或 `ggplot2`
- `kegg_mode=species` 但指定物种的 KEGG REST 缓存不可用

## 6. 方法说明

本阶段不使用人类 MSigDB 或 `org.Hs.eg.db`。植物数据的 KEGG fgsea gene sets 按以下优先级构建：

1. 如果提供 `kegg_organism` 或可由 `species_name` 推断 organism code，则从 KEGG REST 缓存该物种的 pathway 列表与 gene-pathway 关系。
2. 如果物种级 KEGG gene ID 与本地 DE/gene2ko 基因 ID 可直接匹配，则使用物种级 gene set。
3. 如果无法直接匹配（例如水稻 KEGG 使用数字 gene ID，而本地数据使用 `LOC_Os...`），则使用该物种 pathway 列表作为白名单，再结合 `gene2ko.txt` 与 KO-to-pathway 缓存构建 gene set。
4. 如果没有可用物种信息，则使用 KO 全局 pathway，但在 fgsea 前过滤明显人类疾病/动物系统 pathway。
5. 用 DESeq2 的 `log2FoldChange` 对所有基因排序。
6. 对过滤后的 pathway-to-gene 和排序向量运行 `fgsea`，因此 `padj` 基于实际测试的 pathway universe 计算。
