# 后端版本验收证据

冻结包内README历史说明可能早于最终验收。下列是已运行后端的版本证据，不是当前用户数据的验收结果。

2026-10-09 后端实际验收：24250单细胞，MTX/H5AD计数一致、ARI=1；8donors、106pseudobulk单元；5细胞类型DESeq2成功、3样本不足跳过。真实鼠肾单核911→904，原始count层和symbol映射一致。官方chr19 fixture仅技术接口数据；CellBender/QCATCH及更多平台/设计未覆盖。

本地详细证据文件：三个流程真实数据全量测试报告_20261009.md、final_small_evidence.tar.gz（随项目验证材料保留）。本Skill不复制大型数据。归档身份以assets/backend.json中SHA256为准。


本轮Skill高级实际执行（2026-10-09）：chr19技术fixture18新任务，真实单核12新任务，均无缓存或失败；新911核矩阵接analyze保留904核，原始counts全矩阵一致。paired DESeq2五类型及Hallmark ORA实际完成，三类型完整配对/样本不足跳过。只读复用STAR索引；CellBender/QCATCH未覆盖。
详细证据：skill_advanced_validation/三个Skill高级分支测试报告.md及各分支acceptance/run_manifest。测试输入和中间大文件按用户授权清理，保留报告、重要结果和清理清单。
