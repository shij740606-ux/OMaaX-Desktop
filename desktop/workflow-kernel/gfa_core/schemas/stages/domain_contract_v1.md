
# Domain Stage Contract v1 (Protein Domain Analysis)

## 1. Workspace 目录归属

```
workspace/<job_id>/
├── domain/analysis/
│   ├── results.domtblout
│   ├── results.tblout (可选)
│   ├── results.out (可选)
│   └── Pfam-A.hmm* (软链接)
├── final/
│   ├── {prefix}.domain.png
│   ├── {prefix}.domain.pdf
│   ├── {prefix}.domain_report.md
│   └── {prefix}.domain.manifest.json
└── logs/
```

## 2. 输入契约

### 2.1 必需输入

| 输入项 | 来源 | 路径 |
|--------|------|------|
| 蛋白序列 | Identify/Phylogeny | 常规 route 使用 `final/{prefix}.final_members.pep.fa`；Scope 模式使用同 scope phylogeny manifest `parameters.scope_input_fasta`；`analysisRoute=subfamily_evidence` 的 scope FASTA 由目标 `curated_members.pep.fa` + reference FASTA 合并生成 |
| 系统发育树 | Phylogeny | `final/{prefix}.tree.nwk` 或 phylogeny.manifest `artifacts.tree.full_path` |
| Pfam 数据库 | 系统配置 | `--pfam-base` 默认 `/home/userlab/home/wutx/05.tool/Pfam-A.hmm` (+ .h3f/.h3i/.h3m/.h3p) |

Scope 模式下，蛋白序列必须优先来自同一 `scope_id` 的 phylogeny manifest `parameters.scope_input_fasta`，并使用 job-local `.clean.fa` 去除 `*` stop marker；不得修改用户原始 FASTA。

### 2.2 可选输入

- **亚家族文件**: 自动查找仅使用 `confirmed_subfamily.tsv`，查找优先级 `final/` > `input/{job_id}/` > `workspace/input/`；`reference_classification.subfamily.tsv` / `geneID.subfamily` 属于 target prediction，只有通过 `--subfamily` 显式指定时才可用于查看预测图。
- **E-value**: `--evalue` (默认 1e-5)
- **Coverage**: `--coverage` (可选，百分比)


> `analysisRoute=subfamily_evidence` 用于亚家族分类证据生成，默认不消费目标物种的 prediction 或 confirmed 标签；后续正式 domain 图必须基于 `final_members.pep.fa` + `confirmed_subfamily.tsv` 重绘。

## 3. 输出契约

### 3.1 final/ 必须产出

| 文件 | 类型 |
|------|------|
| `{prefix}.domain.png` | PNG 图像 (300 DPI) |
| `{prefix}.domain.pdf` | PDF 矢量图 |
| `{prefix}.domain_report.md` | Markdown 报告 |
| `{prefix}.domain.manifest.json` | JSON 清单 |

### 3.2 domain/analysis/ 中间产物

- `results.domtblout`: hmmscan 结构域表
- `results.tblout`: hmmscan 序列命中 (可选)
- `Pfam-A.hmm*`: 软链接

## 4. Manifest Artifacts

```json
{
  "domain_plot_png": {
    "artifact": "domain_plot",
    "path": "final/{prefix}.domain.png",
    "full_path": "绝对路径",
    "type": "image",
    "format": "png",
    "size": "字节数",
    "sha256": "哈希值",
    "description": "Domain architecture visualization (PNG)"
  },
  "domain_plot_pdf": {
    "artifact": "domain_plot",
    "path": "final/{prefix}.domain.pdf",
    "full_path": "绝对路径",
    "type": "image", 
    "format": "pdf",
    "size": "字节数",
    "sha256": "哈希值",
    "description": "Domain architecture visualization (PDF)"
  },
  "domain_report": {
    "artifact": "domain_report",
    "path": "final/{prefix}.domain_report.md",
    "full_path": "绝对路径",
    "type": "report",
    "format": "markdown",
    "size": "字节数",
    "sha256": "哈希值",
    "description": "Human-readable analysis report"
  },
  "domtblout": {
    "artifact": "domtblout",
    "path": "domain/analysis/results.domtblout",
    "full_path": "绝对路径",
    "type": "txt",
    "size": "字节数",
    "description": "hmmscan domain table output (raw)"
  }
}
```

## 5. Metrics

Scope 模式下 manifest 必须额外记录：

- `scope_id`
- `scope_mode`
- `scope_file`
- `target_species`
- `reference_species`
- `comparative_species`
- `selected_species`
- `protein_source`
- `protein_record_count`

| 字段 | 类型 |
|------|------|
| proteins_total | int |
| proteins_with_domains | int |
| domain_coverage_rate | float (0.0-1.0) |
| total_domain_hits | int |
| significant_domain_hits | int |
| unique_domain_types | int |
| avg_domains_per_protein | float |
| top_domains | list |

## 6. Parameters

| 字段 | 类型 |
|------|------|
| cpu | int |
| prefix | string |
| stage | string ("domain") |
| job_id | string |
| run_id | string |
| evalue_cutoff | float |
| coverage_cutoff | float/null |
| pfam_hmm | string |
| pfam_base | string |
| protein_source | string |
| protein_sha256 | string |
| tree_file | string |
| subfamily_file | string/null |
| hmmscan_elapsed_sec | float |
| Rscript_path | string |
| tool_versions | object |
| timestamp_start | string (ISO) |
| timestamp_end | string (ISO) |
| workspace_root | string |
| input_dir | string |
| domain_analysis_dir | string |
| final_dir | string |

## 7. 失败语义

| 状态 | 条件 |
|------|------|
| success | 绘图成功 (PNG/PDF 存在)，无论是否有显著结构域 |
| partial_success | hmmscan 成功但 R 绘图失败，或无显著命中但生成空图 |
| failed | 输入验证失败或 hmmscan 执行失败 |

## 8. 执行示例

```bash
python apps/cli/run_domain.py \
    --prefix Ft.SPL \
    --workspace workspace/20260213_035422_374603_bc1a53 \
    --evalue 1e-5 \
    --cpu 8
```
