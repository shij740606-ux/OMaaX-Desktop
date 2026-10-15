# PPI Stage Contract v1.0

## 阶段信息
- **阶段名称**: ppi (Protein-Protein Interaction)
- **阶段描述**: 蛋白互作网络分析，基于STRING数据库
- **技能目录**: `skills/ppi/`

## 输入契约

### 必需输入
| 字段 | 类型 | 描述 | 来源 |
|------|------|------|------|
| gene_ids | file | 基因ID列表文件 | identify阶段输出: `final/{prefix}.final_members.id` |
| species | string | auto | STRING 物种代码；auto 时从 target/reference scope 推断 (ath/osa/zma/etc) |

Scope 模式下：

- target-only 且目标物种有 STRING 代码时，优先直接使用目标物种 STRING 库，例如 `Os -> osa`。
- 水稻 `osa` 查询会将本地转录本式 ID 规范化为 STRING 常用基因 ID，例如 `Os01t0810800-01 -> Os01g0810800`。
- 只有显式选择 reference STRING 物种、target 物种无法直接映射到 STRING、或多 target scope 时，才使用 `{prefix}.orthologs.txt` / `{prefix}.ortholog_pairs.txt` 将 target source IDs 转换为 reference STRING IDs。
- 多 target scope 必须先运行 Ortholog；PPI 不直接把多物种本地 ID 发给 STRING。
- reference_species 会自动转换为 STRING 物种代码，例如 `At -> ath`、`Os -> osa`。
- 当目标物种不支持 STRING 且没有可用 ortholog mapping 时，PPI 必须返回结构化诊断；不得静默 fallback 到 `ath`。
- `/api/ppi/species-resolution` 提供只读预检，返回 `ready`、`needs_ortholog`、`needs_reference_data` 或 `invalid_mapping`，供前端弹窗提示上传/下载参考蛋白、运行 Ortholog 或显式选择拟南芥兜底。
- 预检不代表 PPI 阶段已提交。`confirmation_required`、`needs_reference_data`、`needs_ortholog` 等等待态只追加写入 `reproducibility/{prefix}.ppi.preflight_decisions.jsonl`，不得生成或覆盖 `final/{prefix}.ppi.manifest.json`。
- 当目标物种不支持 STRING 时，预检诊断应返回 `reference_recommendations`。推荐候选必须是 STRING 支持物种并记录来源/关系；例如苦荞 `Fagopyrum tataricum/Ft` 优先推荐同属石竹目的 `Spinacia oleracea` (`soe`, taxon 3562)，不得直接把拟南芥作为近源推荐。
- 拟南芥只能作为用户显式确认的最后模型参考；它不是所有植物的近源物种，不得在 manifest/report 中标为 near species。
- PPI 是 reference dependency owner：当缺少 target->reference mapping 时，前端/调度器可以自动插入 Ortholog 作为 producer；Ortholog 不决定为什么需要同源/近源物种。
- PPI 与 KaKs 即使位于同一批次，也必须分别按 `requested_stages=["ppi"]` 与 `requested_stages=["kaks"]` 判定就绪状态；调度器可以汇总展示，但不得用 KaKs 缺少 reference PEP/CDS 的状态阻断已可直接查询 STRING 的 PPI。
- 已有 Ortholog mapping 的 reference species 必须与当前 PPI/KaKs reference policy 一致；例如当前选择 Spinacia 时不得静默复用旧 Ft->At mapping。

### 可选输入
| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| score | int | 200 | 置信度阈值 (0-1000) |
| network_type | string | functional | 网络类型 (functional/physical) |
| scope_file | file | 无 | target/reference/comparative scope JSON |

## 输出契约

### 最终产物 (final/)
| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.ppi_network.tsv` | tsv | 互作网络数据表 |
| `{prefix}.ppi_report.md` | markdown | 分析报告 |
| `{prefix}.ppi.manifest.json` | json | 机器可读清单 |
| `{prefix}.ppi_id_resolution.tsv` | tsv | STRING ID解析/规范化报告 |

### 可选展示产物 (final/)
| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.ppi_network.string.png` | png | 默认尝试下载的 STRING 网络图；下载失败不阻断PPI |
| `{prefix}.ppi_network.pdf` | pdf | 本地网络可视化图；仅在执行路径生成时记录 |
| `{prefix}.ppi_network.png` | png | 本地网络可视化图；仅在执行路径生成时记录 |

### 中间产物 (ppi/analysis/)
| 文件 | 类型 | 描述 |
|------|------|------|
| `network_data.json` | json | STRING API原始响应 |
| `node_attributes.tsv` | tsv | 节点属性表 |
| `edge_list.tsv` | tsv | 边列表 |

## Manifest 结构

执行身份必须分离：`ppiSpecies/string_taxon_id` 是经固定 STRING snapshot
registry 验证的最终 STRING TaxID；`normalizationSpeciesLabel` 只选择本地基因 ID
规范化规则。任意数字不得绕过 snapshot registry 校验，也不得把目标 tree TaxID
与最终 STRING 查询 TaxID混用。旧 `ath/osa/zma` 等 code 仅作为兼容输入。

实际提交后的 success/failed manifest 必须共享唯一的 `ppi_provenance`
结构；API/backend/CLI/status/diagnosis 不得各自维护一套身份字段。历史
manifest 由 status 原样读取，不得用当前 snapshot 回填或重写：

```json
{
  "ppi_provenance": {
    "schema_version": "ppi_provenance_v1",
    "raw_target_species": ["Sv"],
    "canonical_target_name": "Setaria viridis",
    "target_tree_taxon_id": "4556",
    "selected_string_taxon_id": "4555",
    "selection_mode": "tree_nearest",
    "string_snapshot": {
      "version": "12.0",
      "snapshot_id": "<content-derived snapshot id>",
      "sha256": "<source_manifest.json sha256>"
    },
    "user_confirmation": {"required": true, "confirmed": true},
    "ortholog_mapping": {
      "reference_species": "string_taxon_4555",
      "path": "final/Sv.HKL.orthologs.txt"
    },
    "normalization_species_label": "string_taxon_4555",
    "diagnosis_code": "ppi_string_tree_nearest"
  }
}
```

稳定诊断码为 `ppi_string_exact`、`ppi_string_tree_nearest`、
`ppi_string_model_fallback`、`ppi_string_unresolved`、
`ppi_string_resource_error`。CLI 已启动后即使失败也必须写出 failed manifest；
等待下载/确认/Ortholog 的 preflight 不属于执行失败。

```json
{
  "manifest_version": "1.0",
  "stage": "ppi",
  "status": "success",
  "prefix": "Ft.SPL",
  "timestamp": "2026-04-06T10:20:54",
  "elapsed_sec": 12.3,
  "input": {
    "gene_ids": "final/Ft.SPL.final_members.id",
    "species": "ath",
    "ortholog_mapping": "final/Multi.SPL.orthologs.txt"
  },
  "parameters": {
    "string_taxon_id": "3702",
    "normalization_species_label": "ath",
    "scope_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference",
    "target_species": ["Lc", "Dl"],
    "reference_species": ["At"],
    "string_species": "ath"
  },
  "metrics": {
    "input_genes": 15,
    "genes_in_network": 12,
    "total_interactions": 28,
    "avg_score": 0.823,
    "high_confidence": 20,
    "medium_confidence": 6,
    "low_confidence": 2
  },
  "artifacts": {
    "network": {...},
    "plot": {...},
    "report": {...},
    "id_resolution": {...},
    "string_network_png": {...},
    "manifest": {...}
  }
}
```

## 依赖

### 工具依赖
- Python: requests, pandas
- R: igraph, ggplot2 (可选，用于高级可视化)
- STRING API (在线服务)

### Conda环境
```yaml
name: test4py311
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - requests
  - pandas
  - numpy
```

## 执行命令

```bash
python skills/ppi/run.py \
  --gene-ids final/{prefix}.final_members.id \
  --species auto \
  --prefix {prefix} \
  --output {workspace}/ppi \
  --provenance-json {workspace}/ppi/{prefix}.ppi_provenance.json \
  --score 200
```

## 上游依赖
- **identify**: 单 target 且目标物种支持 STRING 时提供本物种基因ID列表
- **ortholog**: 多 target、显式 reference STRING 查询、或目标物种不支持直接 STRING 查询时提供 target→reference 映射

## 下游阶段
- summary: 可汇总PPI结果

## 内部 Ortholog 身份与调度

- 普通基因家族计划只展示 PPI；PPI 所需的 Ortholog 是执行层内部 producer，不作为可见计划阶段或独立确认弹窗。
- PPI 的生物学分析身份固定为目标家族成员 `target_only`；项目参考物种不得进入 PPI 的 `scope_id`、`analysis_route` 或 `member_set_id`。
- STRING TaxID、技术参考 storage key 和同源映射参考只属于 PPI 技术溯源，不得被当作生物学 `referenceSpecies` 重建 PPI scope。
- 非目标物种直连 STRING 路线使用 `dependency_owner=ppi`，映射输出为 `final/{prefix}.ppi.orthologs.txt`（及同 owner 的 pairs/raw/BLAST/manifest/sidecar）。
- PPI 只能读取 PPI owner 映射；不得因 standalone 或 KaKs owner 文件存在而静默复用。
- PPI 的 STRING 技术参考只属于 PPI。它不得覆盖项目参考物种或 KaKs 的显式参考选择。
- PPI 成功、失败或用户明确跳过都会释放后续 KaKs 确认；正在进行的 PPI 参考包下载不是终态。
- PPI-owned Ortholog 完成后必须用同一 target-only 身份重新执行预检；只有返回 `ready` 才能提交 PPI。

## 网络统计指标

| 指标 | 描述 |
|------|------|
| input_genes | 输入基因数量 |
| genes_in_network | 在STRING网络中找到的基因数 |
| total_interactions | 互作关系总数 |
| avg_score | 平均置信度分数 |
| high_confidence | 高置信度互作数 (≥0.7) |
| medium_confidence | 中置信度互作数 (0.4-0.7) |
| low_confidence | 低置信度互作数 (<0.4) |

## STRING物种代码

| 代码 | 物种 | STRING ID |
|------|------|-----------|
| ath | Arabidopsis thaliana | 3702 |
| soe | Spinacia oleracea | 3562 |
| osa | Oryza sativa Japonica | 39947 |
| zma | Zea mays | 4577 |
| gma | Glycine max | 3847 |
| ptr | Populus trichocarpa | 3694 |

## 版本历史
- v1.0.0 (2026-04-06): 初始技能化版本
