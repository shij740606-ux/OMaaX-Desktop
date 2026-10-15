# RNA-Seq Enrichment Analysis Stage Contract v1

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/rnaseq_enrich/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **差异基因列表**：来自RNA_DE阶段
  - 来源：从RNA_DE阶段的Manifest中获取
  - 格式：TSV（gene, log2FC, pvalue, padj）
  - 文件：`*.DE_genes.txt`

- **物种注释数据库**
  - GO注释：org.*.eg.db (如 org.At.tair.db for Arabidopsis)
  - KEGG注释：kegg数据库路径

### 2.2 可选输入

- **物种选择**
  - 支持：ath (拟南芥), hsa (人), mmu (小鼠), rno (大鼠), etc.
  - 默认：从GTF文件自动检测或用户指定

- **背景基因列表**
  - 用途：富集分析的背景集
  - 默认：使用所有表达基因

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1. **`{prefix}.rnaseq_enrich_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：富集分析摘要、统计、工具版本

2. **`{prefix}.rnaseq_enrich.manifest.json`**
   - 类型：机器清单
   - 格式：JSON
   - 内容：完整的分析元数据

3. **`{comparison}.GO_enrich.txt`**
   - GO富集结果
   - 格式：TSV（包含ID, Description, GeneRatio, BgRatio, pvalue, padj等）

4. **`{comparison}.KEGG_enrich.txt`**
   - KEGG通路富集结果
   - 格式：TSV

### 3.2 rnaseq_enrich/ 推荐产出（可追溯中间态）

1. **`{comparison}_GO_BP_enrich.png`**
   - GO BP富集条形图

2. **`{comparison}_GO_CC_enrich.png`**
   - GO CC富集条形图

3. **`{comparison}_GO_MF_enrich.png`**
   - GO MF富集条形图

4. **`{comparison}_KEGG_pathway.png`**
   - KEGG通路富集条形图

5. **kegg_pathway/**
   - KEGG通路图目录（*.png）

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

- **go_enrich**：GO富集结果
  - artifact: "go_enrich"
  - path: "final/{comparison}.GO_enrich.txt"
  - type: "tsv"

- **kegg_enrich**：KEGG富集结果
  - artifact: "kegg_enrich"
  - path: "final/{comparison}.KEGG_enrich.txt"
  - type: "tsv"

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.rnaseq_enrich_report.md"
  - type: "markdown"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.rnaseq_enrich.manifest.json"
  - type: "json"

### 4.2 可选 artifacts（条件触发）

- **go_barplot_bp**：GO BP条形图
- **go_barplot_cc**：GO CC条形图
- **go_barplot_mf**：GO MF条形图
- **kegg_barplot**：KEGG条形图
- **kegg_pathway_images**：KEGG通路图

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **de_genes_input**：输入的差异基因数（整数）
- **go_enriched_terms**：GO富集条目数（整数）
- **kegg_enriched_pathways**：KEGG富集通路数（整数）
- **comparisons**：处理的比较组数（整数）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀（字符串）
- **species**：物种代码（如ath, hsa, mmu）
- **pvalue_cutoff**：P值阈值（浮点数，默认0.05）
- **qvalue_cutoff**：Q值阈值（浮点数，默认0.05）
- **clusterProfiler_params**：clusterProfiler参数（字符串）
- **stage**：阶段名称，固定为 "rnaseq_enrich"（字符串）
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

1. **输入验证失败**：差异基因文件不存在或格式无效
2. **物种注释缺失**：指定的物种数据库不可用
3. **clusterProfiler失败**：R包执行失败
4. **网络问题**：无法下载KEGG通路图（可选）

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空
- `"artifacts"`: 可包含部分产物

### 6.3 部分成功

- GO富集成功但KEGG失败：状态设为 "partial_success"
- 保留已成功的结果

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── rnaseq_enrich/
│   ├── Control_vs_Treatment_GO_BP_enrich.png
│   ├── Control_vs_Treatment_GO_CC_enrich.png
│   ├── Control_vs_Treatment_GO_MF_enrich.png
│   ├── Control_vs_Treatment_KEGG_pathway.png
│   └── kegg_pathway/
│       ├── ath03010.png  # 蛋白酶体通路图
│       └── ath04120.png  # 泛素介导的蛋白降解
├── final/
│   ├── Control_vs_Treatment.GO_enrich.txt
│   ├── Control_vs_Treatment.KEGG_enrich.txt
│   ├── rnaseq_enrich_report.md
│   └── rnaseq_enrich.manifest.json
└── logs/
    └── 20250101_123500_rnaseq_enrich.log
```

### 7.2 GO_enrich.txt 格式示例

```
ID	Description	GeneRatio	BgRatio	pvalue	p.adjust	qvalue	geneID
GO:0006950	response to stress	15/100	200/20000	1.2e-5	0.001	0.001	GeneA;GeneB;GeneC
GO:0009628	response to abiotic stimulus	10/100	150/20000	0.0005	0.02	0.015	GeneA;GeneD
```

### 7.3 KEGG_enrich.txt 格式示例

```
ID	Description	GeneRatio	BgRatio	pvalue	p.adjust	geneID
ath03010	Proteasome	5/100	80/20000	0.001	0.02	GeneA;GeneB
ath04120	Ubiquitin mediated proteolysis	8/100	120/20000	0.0001	0.005	GeneA;GeneC;GeneD
```

### 7.4 执行参数示例

默认执行参数：

- **GO富集** (clusterProfiler):
  ```R
  ego <- enrichGO(
    gene = de_genes,
    OrgDb = org.At.tair.db,
    ont = "ALL",
    pvalueCutoff = 0.05,
    pAdjustMethod = "BH"
  )
  ```

- **KEGG富集**:
  ```R
  kk <- enrichKEGG(
    gene = entrez_ids,
    organism = "ath",
    pvalueCutoff = 0.05
  )
  ```

用户可通过命令行参数覆盖默认参数。

### 7.5 物种代码参考

| 代码 | 物种 |
|------|------|
| ath | 拟南芥 (Arabidopsis thaliana) |
| hsa | 人 (Homo sapiens) |
| mmu | 小鼠 (Mus musculus) |
| rno | 大鼠 (Rattus norvegicus) |
| dme | 果蝇 (Drosophila melanogaster) |
| sce | 酵母 (Saccharomyces cerevisiae) |
