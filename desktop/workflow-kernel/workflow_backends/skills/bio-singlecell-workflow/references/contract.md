# 单细胞/单核执行契约

固定框架：可选 upstream → analyze → 可选人工 annotate → pseudobulk → DESeq2 → 富集。可以从任一满足条件的入口开始；analyze 内部 QC/标准化/降维/聚类为一个固定命令，不能声称支持任意内部步骤断点。

| 入口 | 输入约定 | 命令（B=解包后singlecell，PY=选定环境Python） | 输出/验收 |
|---|---|---|---|
| FASTQ | 按B/config/fastq_samples.example.csv；平台/化学版本、匹配FASTA/GTF，upstream params | PY B/run.py upstream --params ABS --profile docker --work-dir ABS --execute [--resume] | trace所有任务及计数产物；需逐样本检查矩阵再创建下游表；nf-core输出count层需显式配置 |
| counts | CSV sample_id,path,donor,group,batch；路径为10x MTX目录、10x .h5、H5AD。H5AD需counts_layer或明确X为原始整数；cell/gene唯一 | PY B/run.py analyze --config ABS | processed.h5ad、sample_qc.tsv、*.qc.tsv、cells.tsv、cluster_markers_exploratory.tsv、cluster_counts/fractions.tsv、umap.png、design_audit.json、report.html、provenance.json。读取H5AD，counts非负整数、QC细胞为输入子集、UMAP有限、元数据一致 |
| annotate | processed.h5ad；CSV cluster,cell_type,evidence，覆盖所有cluster且唯一，无可信类型可用unknown+说明 | PY B/run.py annotate --config ABS | annotated.h5ad、annotations.csv、cell_type_counts.tsv；未改变counts/细胞ID，注释覆盖，无自动伪造证据 |
| pseudobulk | annotated.h5ad含counts、donor/group/cell_type；跨组donor必须显式paired=true | PY B/run.py pseudobulk --config ABS | counts.tsv（gene_id×pb_id）、samples.tsv、gene_mapping.tsv；独立检查每列等于保留细胞按donor/group/cell_type求和，不能把细胞当重复 |
| DESeq2 | counts.tsv+samples.tsv；control/treated、paired或unpaired | Rscript B/scripts/pseudobulk_deseq2.R COUNTS META CONTROL TREATED OUT paired或unpaired | manifest.tsv逐类型status、celltype_*.tsv、sessionInfo.txt；unpaired每组≥3独立donor，paired≥3完整donor对，设计满秩；不足明确跳过。脚本公式固定group或donor+group，不支持任意协变量 |
| 富集 | selected/universe每行一个同体系gene ID、无表头；GMT物种匹配；必要gene_mapping | PY B/scripts/enrichment.py --selected ABS --universe ABS --gmt ABS --output ABS [--gene-map ABS] [--min-size 10 --max-size 500] | 可读结果，背景为实际检验基因；检查映射命中/可用基因集及p/q范围；仅ORA，不称GSEA |

条件：doublets仅用户选择且assay为scRNA/snRNA；Harmony仅存在可解释批次并检查混杂，单批次默认none，不用Harmony解决完全混杂。差异需用户给对比和独立/配对设计；无注释先停在探索性marker。富集需明确选基因阈值和实际检验背景，不从全部基因随意取背景。

用户运行必需信息：入口、数据/样本表、执行环境、输出位置。FASTQ另需平台/化学版本、FASTA/GTF；MT过滤另需符号/前缀；统计另需donor/group及配对关系，注释另需用户审核表。默认参数详见parameters.md。

配对 DESeq2 的完整性条件：冻结脚本要求本细胞类型纳入的所有 donor 在两组均恰有一个 pseudobulk 单元，且至少3对。因此“已有7个完整对、另有不完整donor”也会跳过。不得默默删除不完整donor；如用户选择只分析完整配对，需要另生成明确筛选的输入副本并记录筛选清单，原数据和固定脚本保持不变。
