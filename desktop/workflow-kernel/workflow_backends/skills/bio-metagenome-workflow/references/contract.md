# 元基因组执行契约

固定两分支：profile → merge-bracken → summarize → 可选R统计；mag → 组装/分箱/QC/注释 → 可选分类。可以从Bracken文件、丰度矩阵或FASTQ接入，MAG启动器不额外承诺任意组装文件入口。

| 入口 | 输入约定 | 命令（B=解包后metagenome） | 输出/验收 |
|---|---|---|---|
| profile | FASTQ表按config/fastq_samples.example.csv；databases表tool,db_name,db_params,db_path；版本与读长一致 | PY B/run.py profile --params ABS --profile docker --work-dir ABS --execute [--resume] | nf-core/taxprofiler2.0.1 trace、QC、分类和所选工具输出；Bracken分支实际需run_kraken2/run_bracken及适配数据库，按固定版WORKFLOW核对；不能假定不同版本输出名 |
| Bracken汇总 | CSV sample_id,path；统一数据库/分类rank；Bracken含taxonomy_id/name/taxonomy_lvl/new_est_reads | PY B/run.py merge-bracken --samples ABS --outdir ABS | abundance.tsv、taxon_names.tsv、report/provenance；taxon_id×sample_id，检查每原始表与合并列计数一致、无重复ID/混rank |
| 丰度分析 | 非负有限taxon×sample TSV，各列和>0；CSV sample_id,donor,group，ID集合精确一致；profiler/database_version/taxonomic_rank/abundance_type明确 | PY B/run.py summarize --config ABS | relative_abundance.tsv、alpha_diversity.tsv、bray_curtis.tsv、pcoa.tsv/eigenvalues.tsv、top_taxa.tsv、报告；可选pcoa.png；列比例和1、距离对称对角0、数值有限、样本标签匹配 |
| ANCOM-BC2 | 未标准化整数read counts，不能百分比/相对丰度；恰两组各≥3独立donor | Rscript B/scripts/differential_abundance.R COUNTS META.csv REFERENCE_GROUP OUT | ancombc2_results.tsv、ancombc2_model.rds、analysis_notes.txt、sessionInfo.txt；q范围/模型可读、设计合法；固定group模型不支持配对/协变量 |
| vegan | 按B/scripts/community_stats.R的位置参数，读该脚本再执行 | Rscript B/scripts/community_stats.R RELATIVE_ABUNDANCE.tsv META.csv OUT | permanova.tsv、dispersion_test.tsv、sessionInfo.txt；检查PERMANOVA/离散度检验；独立donor、单因子。不要仅凭显著PERMANOVA解释组差异 |
| MAG | 按config/mag_samples.example.csv，FASTQ/平台；已选择组装器/分箱器/QC/注释及各参考路径 | PY B/run.py mag --params ABS --profile docker --work-dir ABS --execute [--resume] | nf-core/mag5.5.0 trace、Assembly、GenomeBinning、QC/BUSCO、Annotation及MultiQC；只验本次启用分支，0 bins可为数据不足，原始bins不等于高质量MAG |

分支条件：无数据库可先做已有矩阵分析；Bracken需要匹配读长和Bracken建库文件，不从Kraken比例伪造reads。Python permanova仅independent_one_factor且每组≥3独立donor，不支持配对或协变量；R重复donor也拒绝。多重设计请求列为unsupported，不在Skill内改统计公式。

MAG逐个检查trace的FAILED/ignored；标记基因不足须保留exit/log并注明数据不足，其他失败需定位。验证bin序列属于对应组装（SPAdes默认scaffolds）；核对实际BUSCO/Prokka输出而非目录存在。完整GTDB/CheckM/GUNC/CAT库未有路径时询问预算和是否启用，不自动下载；精简mock仅接口测试。参考库版本和实际完整性决定声明范围。

询问：当前入口的数据、输出位置、实际数据库版本/rank/abundance_type；仅FASTQ分支问参考库及平台，统计分支问donor/group/对比，MAG问具体分支和参考资源。无法确认计数来源时不能启动ANCOM-BC2。
