所有命令均从代码包根目录执行。配置文件与样本表在 config/，辅助分析入口在 scripts/。

# 单细胞 / 单核转录组代码包

交付日期：2026-10-08。包含实际下游分析源码、R 差异分析脚本、配置和样本表；上游通过固定版本 nf-core/scrnaseq 4.2.0 调用，不内置上游仓库、容器、参考基因组或示例测序数据。

## 分析流程

FASTQ → 上游计数（可选）→ 原始 counts 校验 → 按样本 QC / 可选 Scrublet → 基因交集 → 归一化、HVG、PCA → 可选 Harmony → 邻居图、UMAP、Leiden → 探索性 cluster markers → 人工审核细胞类型 → donor × group × cell_type 原始计数 pseudobulk → DESeq2 → 本地 GMT 富集。

细胞和细胞核共用下游入口；snRNA 的内含子计数由上游计数工具、参考注释和参数决定，`assay` 标签不会自动改变上游行为。QC 数值只是起点，须按物种、组织、平台调整。线粒体模式默认关闭；植物可另外审核叶绿体比例，当前没有专用叶绿体过滤模块。

## 安装与运行

建议 Linux / WSL，Python 3.12，独立环境。Nextflow 上游另需兼容 Java、Nextflow 和 Docker 或 Apptainer。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py analyze --config config/config.example.json
```

先编辑配置与样本表中的真实路径。JSON 内路径相对 JSON 所在目录；下游样本表中的路径相对样本表目录；上游 FASTQ 样本表内请全部用绝对路径。Windows 的虚拟环境激活方式为 `.venv\Scripts\Activate.ps1`；上游推荐在 Linux / WSL 执行。

- `samples.example.csv`：sample_id、path、donor、group、batch。技术重复可使用相同 donor；不同生物个体必须区分。
- 输入：10x MTX 文件夹、10x h5 或 h5ad。h5ad 默认读取 `layers['counts']`，原始计数确在 X 时显式配置 `counts_layer: "X"`。基因 ID 必须唯一；不同样本保留共同基因。
- `config.example.json`：QC、doublets、integration、聚类参数；Harmony 不能解决批次与分组完全混杂。
- 输出：processed.h5ad、QC / marker / 比例表、UMAP、annotation_template.csv、report.html 和 provenance.json。

```bash
# 上游命令预览；未加 --execute 时不运行、不生成中间数据
python run.py upstream --params config/upstream.params.example.json --work-dir work/scrnaseq
# 真正执行；重跑恢复须保留原 work 目录并加 --resume
python run.py upstream --params config/upstream.params.example.json --work-dir work/scrnaseq --execute

# 将 annotation_template.csv 审核填好，保存为 reviewed_annotations.csv
python run.py annotate --config config/annotate.example.json
python run.py pseudobulk --config config/pseudobulk.example.json
Rscript scripts/pseudobulk_deseq2.R results/pseudobulk/counts.tsv results/pseudobulk/samples.tsv control treated results/deseq2 unpaired
python scripts/enrichment.py --selected selected_genes.txt --universe tested_genes.txt --gmt species.gmt --output results/enrichment.tsv
```

R 依赖：`install.packages('BiocManager'); BiocManager::install('DESeq2')`。R 环境没有在交付机上运行，安装后应记录版本；脚本会保存 sessionInfo。DESeq2 支持简单独立两组或配对两组，要求至少 3 个独立 donor / 组或 3 对完整 donor；设计需加入协变量时请扩展并审核模型。富集的 universe 必须是实际进入检验的基因，不能随意替换为全基因组。

## 方法与代码审核结论

采用 [nf-core/scrnaseq 4.2.0](https://github.com/nf-core/scrnaseq/tree/4.2.0)、[Scanpy](https://github.com/scverse/scanpy)、[DESeq2](https://bioconductor.org/packages/DESeq2)。这些项目有公开文档、版本管理和方法论文，适合作为常规模块基础；这不等于本包已经获得上游认证或适用于所有实验。

已落实：原始计数校验、保留 counts、技术样本聚合到 donor、避免把细胞当独立生物重复、缺失细胞类型不补造零样本、注释要求填写依据。cluster markers 仅作探索，不能作为组间差异表达证据。未实现环境 RNA 校正、自动参考注释、轨迹、RNA velocity、细胞通讯或跨组学整合。

本包为独立适配代码，没有复制上游实现。主要工具版本固定，requirements 中部分基础依赖为范围约束，尚不是完整环境锁；生产部署应完成真实数据验收后冻结环境。所有数据库、基因集和参考数据需另外记录来源与授权。

## 运行范围与恢复

本精简包不含测试代码和验证记录。已完成本包 README 所列真实数据范围的服务器验收；未覆盖分支不能据此认定通过。目标环境仍需按依赖要求安装。

下游输出目录必须不存在或为空；失败后使用新的输出目录保留旧日志。仅上游 Nextflow 支持显式 `--resume`。

## 2026-10-08 审查修订

- analyze 输出 `design_audit.json`，用 batch + group 的设计矩阵秩检查完全混杂，比例不平衡给出提醒；完全混杂时禁止 Harmony，pseudobulk 也会报错停止后续差异分析。观察性聚类仍可运行，不代表可以识别组效应。技术重复样本只按 donor/group/batch 去重用于审计。
- donor 跨组须在 analyze 和 pseudobulk 配置显式设 `paired=true`，并在 R 命令使用 `paired`。配对是否完整仍由 R 按细胞类型检查。当前 DESeq2 设计仍只含 group 或 donor + group，未增加 batch 协变量。
- 示例改为三个批次各一对独立 control/treated donor。原示例是不平衡而非完全混杂，不应一概判错。
- DESeq2 按细胞类型捕获错误，增量写 `manifest.tsv` 的 status/error；部分成功不代表全部成功，调用者必须检查 manifest。
- pseudobulk 另输出 `gene_mapping.tsv`，来自输入的 gene_symbols。h5ad 必须自行提供可信基因注释；没有 symbol 时导出空字段，不猜测或在线补注释。
- GMT 为 symbol 而 selected/universe 为 ID 时，富集命令加 `--gene-map results/pseudobulk/gene_mapping.tsv`。映射同时应用于 selected/universe，多 ID 同 symbol 去重；任一 universe 基因缺映射即报错。需要固定物种和注释版本，或使用同 ID 类型的 GMT。不得只映射 selected 而不映射 universe。
- 线粒体过滤关闭时明确警告。人 scRNA 可将 `mitochondrial_pattern` 设为 `^MT-` 并结合分布选择 `max_pct_mt`；不能将人阈值默认套用于植物或 snRNA。
- [PyPI Scanpy 1.12.4](https://pypi.org/project/scanpy/1.12.4/) 已核实存在；存在不等于整套依赖已安装验收。


共同修订：执行 Nextflow 前检查 CSV 中本地 FASTQ/FASTA/数据库等已识别路径列，要求绝对路径且存在；远程 URI 交由上游验证，不验证下载可达性。dry-run 仍仅预览。provenance 覆盖 run.py、src/*.py、scripts/ 文件和 requirements.txt；这不是完整数据/容器追溯。三个包保持独立，common.py 同步维护，避免引入跨包安装依赖。

## 多样本轻量验收补充（2026-10-08）

已实际运行 24 个合成样本的 MTX/H5/H5AD 混合输入、Harmony 和 snRNA/Scrublet 分支，processed → annotation → pseudobulk 的不同入口，以及重复种子和异常输入检查。2304 个细胞的整数 counts 与 gene_symbols 保留，合成类别聚类 ARI=0.997，24 donor 的 96 列 pseudobulk 计数独立核对通过；另覆盖 48 donor / 96 技术样本的三种矩阵格式。

Harmony 固定为此次实测通过的 harmonypy 0.0.10，避免宽范围安装选择未验收的新实现及额外 PyTorch 依赖。Windows 使用短的 NUMBA_CACHE_DIR。本包仍不内置测试或测试数据；本轮未执行 R DESeq2，也未执行真实 FASTQ 计数或 snRNA 内含子策略验证。

恢复执行记录：显式 `--resume` 后，启动器将上一轮状态、参数、trace、执行报告和日志保存到工作目录的 `launch_history/`，再写入本轮状态与报告。下游独立分析仍要求新建或空输出目录。
