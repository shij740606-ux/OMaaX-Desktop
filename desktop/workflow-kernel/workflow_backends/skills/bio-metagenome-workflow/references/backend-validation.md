# 后端版本验收证据

冻结包内README历史说明可能早于最终验收。下列是已运行后端的版本证据，不是当前用户数据的验收结果。

2026-10-09 后端实际验收：真实四样本taxprofiler/Bracken及MAG。MAG167任务，163完成、4MaxBin2输入标记基因不足；8组装、12原始bins与来源序列一致，12BUSCO/Prokka产物核验；不等于高质量MAG。ANCOM-BC2/vegan以34个真实独立16S样本验统计模块，不冒充shotgun组间端到端；完整GTDB及其他未启用分支未覆盖。

本地详细证据文件：三个流程真实数据全量测试报告_20261009.md、final_small_evidence.tar.gz（随项目验证材料保留）。本Skill不复制大型数据。归档身份以assets/backend.json中SHA256为准。


本轮Skill高级实际执行（2026-10-09）：真实四样本FASTQ→Bracken→merge/summarize通过，恢复执行证据保留；MAG167任务163成功、4MaxBin2标记不足跳过，12原始bins及BUSCO/Prokka核对通过，不代表高质量MAG。34真实16S的ANCOM-BC2/vegan模块正向通过，重复donor负向拒绝；不等于shotgun独立组间统计。完整GTDB未覆盖。
详细证据：skill_advanced_validation/三个Skill高级分支测试报告.md及各分支acceptance/run_manifest。测试输入和中间大文件按用户授权清理，保留报告、重要结果和清理清单。
