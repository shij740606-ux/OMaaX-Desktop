# 参数清单

以下值来自冻结后端实际config示例；示例值不是适用于所有数据的保证。标为null的MT参数保持未启用，路径/参考占位符必须替换。后端未提供的参数不能通过Skill擅自添加。

## config.example.json

| 参数 | 示例/默认值 |
|---|---|
| `abundance` | `"../results/merged/abundance.tsv"` |
| `samples` | `"samples.example.csv"` |
| `outdir` | `"../results/community"` |
| `profiler` | `"Kraken2+Bracken"` |
| `database_version` | `"REPLACE_WITH_ACTUAL_DATABASE_BUILD"` |
| `taxonomic_rank` | `"species"` |
| `abundance_type` | `"bracken_estimated_reads"` |
| `top_taxa` | `20` |
| `plots` | `true` |
| `permanova` | `false` |
| `design` | `"independent_one_factor"` |
| `n_permutations` | `999` |
| `seed` | `42` |
| `taxon_names` | `"../results/merged/taxon_names.tsv"` |

## mag.params.example.json

| 参数 | 示例/默认值 |
|---|---|
| `input` | `"/data/mag_samples.csv"` |
| `outdir` | `"/data/results/mag"` |

## taxprofiler.params.example.json

| 参数 | 示例/默认值 |
|---|---|
| `input` | `"fastq_samples.example.csv"` |
| `databases` | `"databases.example.csv"` |
| `outdir` | `"/data/results/taxprofiler"` |
| `run_kraken2` | `true` |
| `run_bracken` | `true` |
| `perform_shortread_qc` | `true` |
| `perform_runmerging` | `false` |

## 参数语义与限制

samples/input/annotations/abundance/taxon_names 为对应输入路径，outdir 为新输出目录；counts_layer 为原始计数层或明确X；seed 为整数随机种子。qc.min_genes/min_counts/min_cells_per_gene 为非负整数，max_genes/max_pct_mt 可为null，比例0..100；mitochondrial_pattern为物种/ID匹配正则。doublets.enabled布尔、expected_rate介于0和1；integration为none或harmony。n_hvg/n_pcs/n_neighbors/top_taxa/n_jobs为正整数；resolution正数；paired布尔但必须反映真实设计。

空间spatial_graph.mode为grid/knn/radius；n_neighbors正整数（示例6）；radius模式还必须补radius>0及单位；H5AD grid补grid_confirmed=true及证据。n_permutations至少99，示例999；neighborhood_label是obs实际存在且非缺失的分类列；point_size>0，invert_y/plots/show_progress_bar布尔。元基因组permanova布尔，design只支持independent_one_factor；profiler/database_version/taxonomic_rank/abundance_type必须真实，不留REPLACE占位符。

上游params JSON透传固定nf-core版本：示例不是全部上游参数；读后端docs/WORKFLOW.md和安装缓存的该固定版本schema/usage。不得猜测参数名或套用最新版本文档。CLI --profile、--work-dir、--execute、--resume按contract使用；执行前检查所选分支输入及资源。

注释input/annotations/outdir；pseudobulk min_cells默认20、paired默认false。DESeq2位置参数COUNTS/META/CONTROL/TREATED/OUT/MODE；alpha0.05、过滤counts>=10且至少3单元、设计group或donor+group写在脚本内部，无CLI调节。ANCOM-BC2固定prv_cut0.30、BH、alpha0.05、seed42、n_cl1及group公式；不可承诺用户能从配置修改这些固定值。用户需更改固定实现时另立扩展版本。

富集CLI selected/universe/gmt/output必需；gene-map可选但ID体系不同则必需；min-size默认10、max-size默认500，需0<min<=max。差异后选基因的padj/log2FC阈值由用户给定或明确记录外层默认，不能声称是包内可调参数。
