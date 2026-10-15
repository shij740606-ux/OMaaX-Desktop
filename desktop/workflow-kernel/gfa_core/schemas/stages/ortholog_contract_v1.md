# Ortholog Stage Contract v1.0

## 阶段信息

- **阶段名称**: ortholog
- **阶段描述**: 使用 BLASTP 在目标物种家族蛋白与参考物种家族蛋白之间建立同源/映射关系，供 PPI、KaKs 等下游阶段复用
- **脚本**: `apps/cli/run_ortholog.py`

职责边界：

- Ortholog 是 PPI/KaKs 的上游 producer，不是同源/近源参考物种需求的 owner。
- PPI/KaKs 或批量调度器应先完成 reference dependency preflight，决定是否需要参考注释包、STRING 支持、CDS/PEP 等资源。
- Ortholog 只接受已经确定的 target/reference 和 PEP 输入，并 materialize `target -> reference` mapping。
- PPI-owned producer 必须以 `ppiReferenceStorageKey` 作为技术 reference 身份和本地 PEP 目录；项目分析 scope 中的 reference 仅保留为项目语义，不能覆盖 PPI owner。
- 若 PPI 自身预检返回 `needs_ortholog`，或当前 KaKs 缺少映射，调度器可以为对应阶段自动插入 Ortholog；目标物种可直接查询 STRING 的单 target PPI 不依赖 Ortholog。

## 输入契约

### Scope 模式

| 字段 | 类型 | 描述 |
|------|------|------|
| scope_file | json | target/reference/comparative scope JSON |
| target family pep | fasta | 每个 target 物种的家族蛋白，优先 `identify/{Species}/final/{Species}.final_gfmembers.pep.fa`，也接受 `final/Target.{Species}.{Family}.final_members.pep.fa` |
| reference family pep | fasta | reference 物种的家族蛋白，优先 scope 中 `family_pep`，例如 `{Species}.{Family}.pep.fa` |

规则：

- target 可以是多个物种；Ortholog 会逐 target 与第一个 reference 物种分别运行，再合并结果。
- reference 必须至少有一个物种；当前阶段使用第一个 reference 物种作为 subject。
- reference 的 `family_pep` 优先于全蛋白组 `pep`，避免把基因家族映射误扩展到全基因组背景。

### Legacy 模式

- query: `final/{prefix}.final_members.pep.fa`
- subject: `At.{Family}.pep.fa`、`Ath.pep.fasta`、`At.pep.fasta` 或旧文件名 `SPL_Ath.fasta`
- Legacy 模式中的 At/Ath 只能作为旧兼容路径；PPI/KaKs dependency preflight 不得把 At 静默当作所有植物的默认近源参考。

## 输出契约

### 中间产物（CLI，位于 `workspace/<job>/ortholog/`）

| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.{target}_to_{reference}_pairs.txt` | table | BLAST 原始 best-hit 对，列顺序为 `Subject_ID Query_ID Identity(%)` |
| `{prefix}.{target}_to_{reference}.blastp.txt` | blast | BLAST outfmt6 原始结果 |
| `{prefix}.{target}_to_{reference}_pairs.txt.identity.json` | json | producer 身份 sidecar，用于严格判断中间 mapping 是否可复用 |

中间产物**不能**作为阶段完成依据；必须由 `pipeline_backend` 落盘到 `final/`。

### 最终产物（位于 `final/`）

| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.ortholog_pairs.txt` | table | 下游 PPI/KaKs 使用的标准映射：`Source_ID Target_ID Identity Source_Species Target_Species` |
| `{prefix}.orthologs.txt` | table | 与 ortholog_pairs 同内容的兼容文件 |
| `{prefix}.ortholog_pairs.raw.txt` | table | run_ortholog.py 原始方向记录，附加物种列 |
| `{prefix}.ortholog_blastp.txt` | blast | 合并后的 BLASTP outfmt6 结果 |
| `{prefix}.ortholog.manifest.json` | json | 标准 manifest |
| `{prefix}.{scope_id}.ortholog.manifest.json` | json | scope 模式下的 scoped manifest 副本 |

Owner namespace：

| producer 身份 | 映射/manifest stem | 用途 |
|---|---|---|
| standalone (`dependency_owner=""`) | `{prefix}.ortholog*` | 用户显式请求的 Ortholog，保持原契约 |
| PPI (`dependency_owner="ppi"`) | `{prefix}.ppi.ortholog*` | PPI STRING 技术参考专属内部映射 |
| KaKs (`dependency_owner="kaks"`) | `{prefix}.kaks.ortholog*` | KaKs 显式项目参考专属内部映射 |

- owner namespace 同时作用于 orthologs、pairs、raw pairs、BLAST、identity sidecar、标准 manifest 与 scoped manifest。
- manifest 的 `stage` 仍为 `ortholog`；`parameters.dependency_owner` 记录 producer 身份。
- standalone、PPI、KaKs 三套产物可为同一 prefix 并存；重跑一个 owner 不得覆盖或改变另一个 owner。
- 下游按 owner token 解析候选路径，不接受前端传入任意文件路径，也不得跨 owner 回落。

## Manifest 最小字段

```json
{
  "manifest_version": "1.0",
  "stage": "ortholog",
  "status": "success",
  "prefix": "Multi.SPL",
  "scope_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference",
  "scope_mode": "target_plus_reference",
  "analysis_route": "multi_species",
  "member_set_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference:multi_species:all_members",
  "target_species": ["Lc", "Dl"],
  "reference_species": ["At"],
  "stage_context": {
    "stage": "ortholog",
    "scope_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference",
    "analysis_route": "multi_species",
    "member_set_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference:multi_species:all_members"
  },
  "parameters": {
    "reference_species": "At",
    "reference_protein_fasta": "input/.../At.SPL.pep.fa",
    "reference_taxon_id": "3702",
    "reference_storage_key": "At",
    "reference_pep_sha256": "...",
    "target_identity": "...",
    "member_identity": "...",
    "mapping_identity": "...",
    "mapping_path": ".../final/Multi.SPL.orthologs.txt",
    "analysis_route": "multi_species",
    "member_set_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference:multi_species:all_members"
  },
  "metrics": {
    "ortholog_pairs": 54
  }
}
```

身份字段规则：

- `analysis_route` / `member_set_id` 由 `build_stage_context(stage=ortholog, scope_data=...)` 推断并写入 manifest 根级、`parameters` 与 `stage_context`。
- 单 target + `target_only` scope 时，`analysis_route` 通常为 `single_species`；含 reference 的 comparative scope 通常为 `multi_species`。
- postflight strict identity 诊断依赖上述字段；materialize 落盘时必须补齐，不能只写 `scope_id`。
- PPI-owned producer 的 `reference_taxon_id` 来自已确认 STRING TaxID，`reference_storage_key` 决定本地 PEP 路径及 mapping 的 Target_Species。
- PPI-owned producer 的生物学 scope 必须与 PPI consumer 相同，固定为目标家族 `target_only`；技术参考物种只写入 owner/provenance 参数。
- sidecar 与当前请求必须在 `reference_taxon_id`、`reference_storage_key`、reference PEP SHA256、target/query PEP identity、scope/member identity、mapping path/SHA256 和 `mapping_identity` 上完全一致，才允许跳过 BLAST 并复用中间 pairs。
- sidecar 缺失、无法解析、字段缺失或任一 identity 冲突都视为不可复用；应按原 Ortholog 命令重新生成，不得根据“文件存在”推断身份。
- PPI preflight 不得只读取 final mapping 的 Target_Species 后判定 ready；还必须同时验证 final mapping sidecar、对应 Ortholog manifest，以及当前 reference PEP、target/query PEP、scope/member 和 mapping identity。任一输入变化或身份材料缺失均返回 `needs_ortholog` / stale identity conflict。

## 失败语义

- target 家族蛋白缺失：失败，并指出期望的 `identify/{Species}/final/{Species}.final_gfmembers.pep.fa`、`final/Target.{Species}.{Family}.final_members.pep.fa` 或 `{Species}.{Family}.pep.fa`
- reference 家族蛋白缺失：失败，并指出需要 `{Reference}.{Family}.pep.fa`
- BLASTP 失败：失败，返回日志路径
- BLAST 成功但 `final/` manifest/映射缺失：判定为 **intermediate 未 materialize**；应复用 `ortholog/*_pairs.txt` 执行 backend 收尾落盘，而不是重复 BLAST
