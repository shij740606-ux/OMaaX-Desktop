# PEP Stage Contract v1.0

## 阶段信息
- **阶段名称**: pep (Protein Feature Analysis)
- **阶段描述**: 蛋白特征分析，包括理化性质、跨膜结构预测、亚细胞定位（默认使用 WoLF PSORT；Plant-mPLoc 代码保留但默认关闭）
- **技能目录**: `skills/pep/`

## 输入契约

### 必需输入
| 字段 | 类型 | 描述 | 来源 |
|------|------|------|------|
| protein_fasta | file | 蛋白序列FASTA文件 | scope模式：同一scope的phylogeny manifest `parameters.scope_input_fasta`；target_only可退回identify输出 `final/{prefix}.final_members.pep.fa`；非scope模式沿用identify输出 |

### 可选输入
| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| cpu | int | 8 | CPU线程数 |
| scope_file | file | 无 | target/reference/comparative scope JSON；PEP只分析该scope实际选中的蛋白集合 |

## 输出契约

### 最终产物 (final/)
| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.pep_properties.pdf` | pdf | 理化性质分布图 |
| `{prefix}.pep_properties.png` | png | 理化性质分布图(PNG) |
| `{prefix}.pep_tmhmm.pdf` | pdf | 跨膜结构预测图 |
| `{prefix}.pep_tmhmm.png` | png | 跨膜结构预测图(PNG) |
| `{prefix}.pep_subcellular.pdf` | pdf | 亚细胞定位图 |
| `{prefix}.pep_subcellular.png` | png | 亚细胞定位图(PNG) |
| `{prefix}.pep_report.md` | markdown | 分析报告 |
| `{prefix}.pep.manifest.json` | json | 机器可读清单 |

### 中间产物 (pep/analysis/)
| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}_properties.tsv` | tsv | 理化性质数据表 |
| `{prefix}_wolfpsort.txt` | tsv | WoLF PSORT预测结果，默认亚细胞定位数量来源 |
| `{prefix}_plant_mploc.txt` | tsv | Plant-mPLoc预测结果；当前默认关闭，仅保留兼容代码 |
| `deeptmhmm_results.md` | markdown | DeepTMHMM结果 |
| `predicted_topologies.3line` | txt | 拓扑结构预测 |

## Manifest 结构

```json
{
  "manifest_version": "1.0",
  "stage": "pep",
  "status": "success",
  "prefix": "Ft.SPL",
  "timestamp": "2026-04-06T10:20:53",
  "elapsed_sec": 45.2,
  "input": {
    "protein_fasta": "phylogeny/analysis/Multi.SPL.<scope_id>.input.pep.clean.fa"
  },
  "parameters": {
    "scope_id": "SPL.target-Lc-Dl.ref-At.comp-Os.target_plus_reference",
    "scope_mode": "target_plus_reference",
    "target_species": ["Lc", "Dl"],
    "reference_species": ["At"],
    "comparative_species": ["Os"],
    "selected_species": ["Lc", "Dl", "At"]
  },
  "metrics": {
    "protein_count": 15,
    "avg_length": 356.4,
    "avg_molecular_weight": 38952.3,
    "avg_pi": 7.23,
    "avg_gravy": 0.452,
    "subcellular_method": "WoLF PSORT",
    "plant_mploc_enabled": false
  },
  "artifacts": {
    "properties_pdf": {...},
    "properties_png": {...},
    "tmhmm_pdf": {...},
    "tmhmm_png": {...},
    "subcellular_pdf": {...},
    "subcellular_png": {...},
    "report": {...},
    "manifest": {...}
  }
}
```

## 依赖

### 工具依赖
- R (ggplot2, patchwork)
- biolib (DeepTMHMM)
- Python: biopython, pandas, numpy, matplotlib

### Conda环境
```yaml
name: test4py311
channels:
  - conda-forge
  - bioconda
dependencies:
  - python=3.11
  - biopython
  - pandas
  - numpy
  - matplotlib
  - seaborn
  - r-base
  - r-ggplot2
  - r-patchwork
```

## 执行命令

```bash
python apps/cli/run_pep.py \
  --workspace {workspace} \
  --prefix {prefix} \
  --scope-file final/{prefix}.phylogeny_ref_scope.json \
  --cpu 8
```

## 上游依赖
- **identify**: target_only 场景提供目标家族蛋白序列文件
- **phylogeny**: scope 场景提供当前 scope 的合并蛋白 FASTA，保证 PEP 与 tree/motif/domain 使用同一批成员

## 下游阶段
- summary: 可汇总PEP结果

## 版本历史
- v1.0.0 (2026-04-06): 初始技能化版本
