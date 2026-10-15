# 独立空间转录组执行契约

固定框架：可选 segment → 可选 SpatialData export → analyze（逐切片QC、表达聚类、空间图、Moran、邻域富集、探索性marker及导出）。不能跨切片连图或当作独立组间统计；内部步骤不能任意断点。

| 入口 | 输入约定 | 命令（B=解包后spatial） | 输出/验收 |
|---|---|---|---|
| 分割 | Sopa样本表按B/config/sopa_samples.example.csv；平台原始目录及支持的technology profile | PY B/run.py segment --params ABS --profile docker,xenium_proseg --work-dir ABS --execute [--resume] | profile只是已测Xenium示例，不为其他平台硬套；检查trace、SpatialData表/shape实例ID、计数、分割报告；完成不等于准确率通过 |
| SpatialData | 明确Zarr路径、table名、X或原始counts layer；obsm spatial为n×2 | PY B/scripts/export_spatialdata.py --input ABS --table NAME --counts-source X --output ABS.h5ad | H5AD counts层、ID/矩阵/坐标与来源一致。脚本不会生成缺失坐标或选择transform；缺失时询问/提供独立适配，不改包。原图像坐标转换与shape变换需记录 |
| Visium | 经典Space Ranger目录含filtered_feature_bc_matrix.h5和spatial图像/坐标；CSV sample_id,path,donor,group,format=visium | PY B/run.py analyze --config ABS | 每sample空间图及处理H5AD，根sections_summary.tsv、report.html、provenance.json |
| H5AD | 同CSV，format=h5ad；明确counts层，唯一obs/var，n×2有限无重复obsm spatial | 同上 | 同上；Visium HD/不规则点不是经典Visium直接入口，须先用户提供符合H5AD契约的适配数据 |

analyze每样本必须交付observations_qc.tsv、spatial_graph_degrees.tsv/audit.json、spatial_moranI.tsv、moran_interpretation.json、gene_mapping.tsv、observations.tsv、spatial_processed.h5ad、spatial_clusters.png；多类别时另有neighborhood_zscores.tsv和region_markers_exploratory.tsv。

图启用条件：grid只用于经典Visium，H5AD还必须有用户/可信几何证据并显式grid_confirmed=true；knn适用于不规则点；radius必须提供正半径与坐标单位。不能假定所有坐标为微米。单类别邻域和marker无对比，注明跳过。

验收：坐标有限唯一、counts非负整数、ID不丢配；QC后≥30观测/基因，图维度正确且有边，记录度和孤立比例；Moran包含本次保留基因、p/q在0..1，明确经验/解析p和置换分辨率；抽样独立Moran核验记录误差。表达簇需组织学/marker审核，靶向panel不能默认为全转录组线粒体QC。不执行组间差异。

询问：已有矩阵无需追问无关上游；segment才问平台及profile；图几何未确定时问grid/knn/radius和必要单位；缺table/原始counts/坐标就阻塞对应接入，不猜测。富集可用B/scripts/enrichment.py，其参数同parameters.md；它是额外ORA，不是analyze自动阶段。

SpatialData表选择的执行前检查：先读取实际对象并列出 `obj.tables.keys()`。只有一个表时可从文件推断表名，记录来源；多个表且用户未指定时询问其选择。`--table NAME` 是占位说明，`table` 不是默认键，不得不检查就传入。验收记录需包含实际键与选择依据，首次错误保留，重验另记结果。
