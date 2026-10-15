所有命令均从代码包根目录执行。配置文件与样本表在 config/，辅助分析入口在 scripts/。

# Shotgun 宏基因组代码包

交付日期：2026-10-08。适用于 shotgun 宏基因组，不是 16S / ITS 扩增子流程。包含实际群落统计 Python 源码、ANCOM-BC2 / vegan R 脚本、配置和固定版本 nf-core 上游入口。

## 流程

1. reads 分支：FASTQ → nf-core/taxprofiler 2.0.1 → QC、按需去宿主 → Kraken2 / Bracken → 同层级丰度合并 → alpha 多样性、Bray–Curtis、PCoA → PERMANOVA + 离散度检验 → ANCOM-BC2。
2. MAG 分支：FASTQ → nf-core/mag 5.5.0 的组装 / 分箱 / 质量与分类等步骤（实际启用工具取决于上游配置）。

数据库构建版本、分类层级、样本测序策略必须保持可比。宿主相关样本请在 taxprofiler 参数中设置 `perform_shortread_hostremoval: true` 和 `hostremoval_reference` 的真实 FASTA 路径；长读长去宿主另用对应开关。示例不自带宿主参考。MAG 去宿主参数见其上游文档，不能套用 taxprofiler 参数。

## 安装与执行

建议 Linux / WSL + Python 3.12；上游另需兼容的 Nextflow、Java、Docker / Apptainer。taxprofiler 2.0.1 要求 Nextflow >=25.10.4；mag 5.5.0 要求 Nextflow >=26.04.0，25.10.8 会被其版本检查拒绝。可为两者设置独立的运行环境；本轮为 MAG 准备固定 Nextflow 26.04.7。数据库必须按上游参数显式提供，尤其不要在未审核预算时触发 MAG 默认完整 GTDB 下载。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# 默认仅预览；修改参数、样本表和数据库表后加 --execute 运行
python run.py profile --params config/taxprofiler.params.example.json --work-dir work/taxprofiler
python run.py mag --params config/mag.params.example.json --work-dir work/mag

# Bracken 输出合并；同一分类层级、同一数据库版本
python run.py merge-bracken --samples config/bracken_samples.example.csv --outdir results/merged
# 将 config 的 abundance 指向 ../results/merged/abundance.tsv 并填写真实数据库版本
python run.py summarize --config config/config.example.json

Rscript scripts/community_stats.R results/community/relative_abundance.tsv config/samples.example.csv results/vegan
Rscript scripts/differential_abundance.R results/merged/abundance.tsv config/samples.example.csv control results/ancombc2
```

R 安装：`install.packages(c('BiocManager','vegan')); BiocManager::install(c('ANCOMBC','TreeSummarizedExperiment','S4Vectors','mia'))`。ANCOMBC 2.8.0 处理 TreeSummarizedExperiment 时实际要求 mia，不能仅通过 library(ANCOMBC) 加载检查判定环境完整。R 环境应完成实际统计验收后锁定；脚本输出 sessionInfo。CVXR、lme4、Matrix 的版本与编译接口必须兼容。

## 数据约定与产出

- `fastq_samples.example.csv` 与 `mag_samples.example.csv` 分属两个上游，列名不同，不能混用。上游 CSV 中的文件路径使用绝对路径。
- `databases.example.csv` 对接 taxprofiler；Bracken 要求该 Kraken2 数据库带适配所用 read length 的 Bracken 数据，按固定版本文档核对。
- 丰度 TSV：首列 taxon ID，行为 taxa，列为 sample_id；不允许负数、非有限值和全零样本。
- `samples.example.csv`：sample_id、donor、group，与丰度矩阵严格对应。JSON 路径相对配置目录，下游 Bracken 路径相对其样本表。
- 配置必须记录 profiler、真实 database_version、taxonomic_rank、abundance_type。
- 输出相对丰度、Shannon / Simpson / 检测到的 taxa 数、Bray–Curtis 矩阵、PCoA、常见 taxa 图及 report.html / provenance.json。

检测到的 taxa 数依赖测序深度与分类器，不是稀释曲线校正后的物种丰富度；本包没有稀释分析。Bray–Curtis PCoA 可出现负特征值，报告会记录其比例。

Python PERMANOVA 默认关闭，仅支持独立 donor 的单因素设计；启用需每组至少 3 个 donor。解释组间差异时应同时运行 vegan 脚本的离散度检验。配对、重复测量、批次和协变量需要另外设计模型，不适用现成单因素脚本。

ANCOM-BC2 入口要求未归一化的非负整数计数，不能输入百分比或相对丰度；默认仅支持独立 donor 的两组比较、每组至少 3 个样本。Bracken 是估计 reads，输入口径及其方法适用性仍需项目审核。不能把简单丰度 t 检验当作本包的差异丰度方法。

## 方法与代码审核结论

基础来自 [nf-core/taxprofiler 2.0.1](https://github.com/nf-core/taxprofiler/tree/2.0.1)、[nf-core/mag 5.5.0](https://github.com/nf-core/mag/tree/5.5.0)、[ANCOMBC](https://bioconductor.org/packages/ANCOMBC)、[vegan](https://vegandevs.github.io/vegan/reference/adonis.html)。这些具备公开文档、版本和方法依据，适合模块化复用；未宣称某种组合适用于全部物种、宿主和研究设计。

未包括 HUMAnN 功能丰度、抗性基因 / 毒力基因专门分析、菌株追踪、病毒专门流程；MAG 上游输出不等同于这些模块。上游仓库和容器由 Nextflow 按版本获取，本包不包含其完整源码和数据库。基础 Python 依赖采用范围约束，生产环境应在验证后完整冻结。

## 运行范围与恢复

本精简包不含测试代码和验证记录。已完成本包 README 所列真实数据范围的服务器验收；未覆盖分支不能据此认定通过。目标环境仍需按依赖要求安装。

下游要求新建或空输出目录，避免覆盖；Nextflow 恢复使用原 work-dir 并显式加 --resume。示例路径是占位数据，不含真实测序样本。

## 2026-10-08 审查修订

- `databases.example.csv` 使用 `bracken` 行，`db_params=;-r 150`：分号前是 Kraken2 参数，后是 Bracken 参数。taxprofiler **2.0.1** 要求同时设置 `run_bracken=true`、`run_kraken2=true`；数据库表仅提供 `bracken` 行即可进入先 Kraken2、后 Bracken 的链路，不必为同一分析再添加独立 Kraken2 行。150 是示例读长，数据库必须含对应分布文件，不能只改参数而不建库。真实服务器运行证实，关闭 `run_kraken2` 会在启动检查阶段失败；旧文档相反说明已纠正。该版本还要求 Nextflow >=25.10.4，本次使用 25.10.8。
- 依据 taxprofiler **2.0.1** 的 `conf/modules.config`，不合并 run 时输出为 `bracken/<db_name>/<sample>_<run_accession>_<db_name>.bracken.tsv`；合并后不含 run_accession。本示例固定 `perform_runmerging=false`，三张样本表统一为六个样本。路径仍为占位符，须替换为实际文件。
- summarize 默认衔接 `../results/merged/abundance.tsv`，同时读取 `taxon_names.tsv`，在 top_taxa 表保留 ID 和名称。
- ANCOM-BC2 显式构造 TreeSummarizedExperiment；不是认定矩阵输入必然错误，而是减少跨版本接口差异。当前未运行 R。流行率阈值 0.30，六样本中至少两个检出；并非通用最优阈值，每组 n=3 功效有限。
- 官方依据：[2.0.1 usage](https://github.com/nf-core/taxprofiler/blob/2.0.1/docs/usage.md)、[2.0.1 输出命名](https://github.com/nf-core/taxprofiler/blob/2.0.1/conf/modules.config)、[ANCOMBC 手册](https://bioconductor.org/packages/ANCOMBC)。


共同修订：执行 Nextflow 前检查 CSV 中本地 FASTQ/FASTA/数据库等已识别路径列，要求绝对路径且存在；远程 URI 交由上游验证，不验证下载可达性。dry-run 仍仅预览。provenance 覆盖 run.py、src/*.py、scripts/ 文件和 requirements.txt；这不是完整数据/容器追溯。三个包保持独立，common.py 同步维护，避免引入跨包安装依赖。

## 多样本轻量验收补充（2026-10-08）

已实际运行 48 个合成样本的 Bracken 表合并、计数/相对丰度两种起点的群落分析及固定种子重现。输入行顺序打乱后仍正确按 taxon ID 合并；Bray–Curtis 与 SciPy、多样性与直接公式、PERMANOVA pseudo-F 与独立 Gower 投影矩阵实现一致。测试数据和记录独立交付。

本轮未执行 R ANCOM-BC2/vegan，未执行真实 taxprofiler 分类或 mag 组装。启动器的多样本、状态与恢复用模拟外部进程检查通过，该证据不能替代真实 nf-core test profile 或 FASTQ 生物学验收。

恢复执行记录：显式 `--resume` 后，启动器将上一轮状态、参数、trace、执行报告和日志保存到工作目录的 `launch_history/`，再写入本轮状态与报告。下游独立分析仍要求新建或空输出目录。
