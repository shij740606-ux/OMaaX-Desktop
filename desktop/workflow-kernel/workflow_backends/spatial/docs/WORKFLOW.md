所有命令均从代码包根目录执行。配置文件与样本表在 config/，辅助分析入口在 scripts/。

# 独立空间转录组代码包

交付日期：2026-10-08。以每张切片独立分析为单位，不依赖配套单细胞参考。包含实际 Python 分析代码与固定版本 nf-core/sopa 1.0.1 的上游入口；不含图像、测序数据、容器和上游仓库副本。

## 流程和输入边界

原始计数 + 二维空间坐标 → 切片内 QC → 归一化、HVG、PCA → 表达聚类 → 空间邻接图 → Moran's I 空间变异基因 → 邻域富集 → 探索性区域 markers → 坐标散点图、h5ad、表格与报告。

- `format=visium`：经典 Visium Space Ranger outs，包含 filtered_feature_bc_matrix.h5 和 spatial 文件夹。
- `format=h5ad`：提供未经归一化的 `layers['counts']`（或配置明确指定 X）、唯一基因 ID、`obsm['spatial']` 的 n × 2 有限坐标。
- 不能把 Visium HD、Xenium、MERFISH 或任意图像文件直接当经典 Visium 输入。此类数据需经平台上游或 Sopa 处理，审核分割、坐标变换和计数后导出 h5ad。
- 样本表列 sample_id、path、donor、group、format。每张切片单独建图，不跨切片连接坐标。

## 安装与运行

建议 Linux / WSL + Python 3.12。单独环境中安装：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py analyze --config config/config.example.json
```

修改样本表和配置路径后运行。JSON 路径相对配置文件，样本路径相对样本表。默认 `grid` 邻域仅适用于规则 spot 网格；细胞分辨率数据用 `knn` 或 `radius`，半径与输入坐标单位一致。阈值与点大小应按组织、物种和平台调整。

每张切片输出质控信息、h5ad、表达聚类、空间自相关结果、邻域富集和散点图；根目录汇总 report.html / provenance.json。散点图不是组织图像叠加，表达聚类不是经过空间域算法验证的组织分区。

```bash
# 可选上游：先按 Sopa 官方 usage 选平台 profile，替换下方 PLATFORM_PROFILE
python run.py segment --params config/sopa.params.example.json --profile docker,PLATFORM_PROFILE --work-dir work/sopa
# 确认配置完成后加 --execute 执行；需 Nextflow、Java 与容器环境

# Sopa 环境中导出已审核的表，明确表名和原始 counts 来源
python scripts/export_spatialdata.py --input /data/sample.zarr --table table --counts-source X --output /data/sample.h5ad
python scripts/enrichment.py --selected spatial_genes.txt --universe tested_genes.txt --gmt species.gmt --output results/enrichment.tsv
```

导出器额外依赖 `spatialdata`（请使用对应 Sopa 环境）。它要求表中已有合适的二维坐标，不猜测细胞形状中心或跨坐标系转换。没有现成坐标时需先在平台分析环境正确生成。

## 方法与代码审核结论

依据 [Squidpy](https://github.com/scverse/squidpy)、[Scanpy](https://github.com/scverse/scanpy)、[nf-core/sopa 1.0.1](https://github.com/nf-core/sopa/tree/1.0.1) 官方流程和接口整理。公开方法、文档和版本管理支持将其作为模块基础；没有做“市场占有率”或文章数排名，不把仓库热度当作科学有效性的证明。

Squidpy 固定 1.8.3；本实现的 spatial_neighbors 接口已被上游标为将来移除，不能未经检查直接升级到 1.9。Moran 检验面向保留的全部基因，使用置换和多重检验校正。单切片 marker / Moran / 邻域结果均属切片内结果，不构成跨供体处理效应证据。

未实现：细胞类型反卷积、配套单细胞整合、切片配准、3D 重建、细胞通讯、专门的空间域模型、供体层面组间统计。这些应作为后续独立模块，而非由现有输出冒充。图像分割仅通过 Sopa 上游调用，必须另做图像质控。

## 运行范围与恢复

本精简包不含测试代码和验证记录。已完成本包 README 所列真实数据范围的服务器验收；未覆盖分支不能据此认定通过。目标环境仍需按依赖要求安装。
主工具版本固定，基础依赖尚未完整锁定。下游只接受新建或空输出目录；上游保留原工作目录后可显式 --resume。

## 2026-10-08 审查修订

- h5ad 使用 grid 须在 `spatial_graph` 明确设 `grid_confirmed=true`，表示已人工核对网格；任意坐标优先选匹配设计的 radius/knn。这是声明门槛，并非自动证明几何正确。
- 每张切片输出 `spatial_graph_degrees.tsv` 和 `spatial_graph_audit.json`，记录 min/median/max 度和孤立点比例，检查图形状及权重有限性。grid 中位度不在 4–6 时提醒人工复核，不强制等于 6：边界、组织空洞和 QC 都可能降低度数。
- 默认置换数 999，可配 `n_jobs`。输出 `moran_interpretation.json` 区分 `pval_sim_fdr_bh`（经验置换）和 `pval_norm_fdr_bh`（正态近似），以及基因数和最小经验 p 分辨率。999 次仍不保证全基因 FDR 功效，先在代表性子集估算时间和内存；正式检验需预先设计置换规模、检验集合与解释策略。不可把显著结果数量作为调参目标。
- 增加按物种配置的 `mitochondrial_pattern` / `max_pct_mt`；默认关闭并警告，避免硬套人类阈值。每张切片导出 `gene_mapping.tsv`，供 enrichment 的 `--gene-map` 使用（同单细胞映射规则）。
- 仍锁 Squidpy 1.8.3 使用兼容 spatial_neighbors；本轮没有升级 API，也未实际运行 read_visium、Scanpy 或 Squidpy。官方依据：[1.8.3 spatial_autocorr 源码](https://github.com/scverse/squidpy/blob/v1.8.3/src/squidpy/gr/_ppatterns.py)。


共同修订：执行 Nextflow 前检查 CSV 中本地 FASTQ/FASTA/数据库等已识别路径列，要求绝对路径且存在；远程 URI 交由上游验证，不验证下载可达性。dry-run 仍仅预览。provenance 覆盖 run.py、src/*.py、scripts/ 文件和 requirements.txt；这不是完整数据/容器追溯。三个包保持独立，common.py 同步维护，避免引入跨包安装依赖。

## 多样本轻量验收补充（2026-10-08）

已实际运行 24 张切片的合成 Visium/H5AD 混合输入，以及 grid、knn、radius、999 次置换、坐标缩放和重复种子检查。Moran 与独立直接公式一致；测试记录单独交付，不放入此包。

Moran 和邻域富集显式传入 `show_progress_bar`，默认 false。Squidpy 的默认进度条在 n_jobs=1 时也创建进程队列，受限 Windows 可因此失败；关闭进度条保留同一统计计算。需要可视进度时可设 true，但要确保进程通信可用。

Windows 的 Numba 缓存路径应短，必要时将 NUMBA_CACHE_DIR 指向可写的短目录。SpatialData/Zarr 的事件循环需要本地通信；本轮该分支在允许本地通信的宿主环境完成，不能视为在受限沙箱通过。此轮没有执行真实 Sopa 图像分割，也没有验证大型切片或真实物种的生物学结果。

恢复执行记录：显式 `--resume` 后，启动器将上一轮状态、参数、trace、执行报告和日志保存到工作目录的 `launch_history/`，再写入本轮状态与报告。下游独立分析仍要求新建或空输出目录。
