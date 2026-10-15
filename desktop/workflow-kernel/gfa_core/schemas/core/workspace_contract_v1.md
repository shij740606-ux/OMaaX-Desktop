# Workspace & Manifest Contract v1 (Identify 已跑通基线)

本契约用于冻结：
- workspace 目录骨架
- Identify 阶段的 final/analysis 归类
- manifest / artifacts 的字段约定（供后续 phylogeny/motif/msa_figure/summary 复用）

---

## 1. Workspace 目录骨架（必须固定）

以 `workspace/<job_id>/` 为根，目录必须满足：

workspace/<job_id>/
├── logs/                      # 每个 stage 的日志（排错用）
├── <stage>/                   # stage 工作目录
│   └── analysis/              # 原生输出/中间态（大文件、可复查但不对外展示）
└── final/                     # 对外最终产物 + 各 stage 的 manifest/report（下游 stage 只读这里）

说明：
- `<stage>` 至少包含 identify；后续阶段（phylogeny/motif/...）按相同规则新增
- `final/` 只放“稳定、可展示、可复用”的产物；工具原生输出默认留在 `<stage>/analysis/`

---

## 2. 命名约定（必须固定）

所有对外文件必须以 `{prefix}` 作为前缀（如 `Ft.SPL`）。

Identify 产物命名：
- 成员ID：`final/{prefix}.final_members.id`
- 成员蛋白：`final/{prefix}.final_members.pep.fa`
- 报告：`final/{prefix}.identify_report.md`
- 清单：`final/{prefix}.identify.manifest.json`

通用约定（所有 stage）：
- 报告：`final/{prefix}.{stage}_report.md`
- 清单：`final/{prefix}.{stage}.manifest.json`

---

## 3. Identify 的最终产物（final/ 必有四件套）

`final/` 必须至少包含 4 个文件：

1) `final/{prefix}.final_members.id`
- 含义：最终家族成员 ID 列表（每行一个）
- 作为下游输入：可用于一致性校验/统计

2) `final/{prefix}.final_members.pep.fa`
- 含义：按 `.final_members.id` 从全蛋白组提取的蛋白序列
- 作为下游输入：phylogeny/motif/MSA 等通常直接使用它
- 约束：FASTA 里的序列ID集合应与 `.final_members.id` 一致；如有缺失，需在 report/metrics 中说明

3) `final/{prefix}.identify_report.md`
- 含义：人可读报告（建议包含 Reproducibility：版本/cmd/inputs hash/时间）

4) `final/{prefix}.identify.manifest.json`
- 含义：机器可读契约（前端展示/下游 stage 消费/复现审计）

---

## 4. Identify 的中间态文件（identify/analysis/）

`identify/analysis/` 用于存放工具原生输出和中间文件，例如：
- BLAST 数据库：`blast_db.target_proteome.*`，由目标物种全蛋白 FASTA 建库
- BLAST 输出：`{prefix}.blast.{reference_label}.out`、`{prefix}.blast.{reference_label}.ids`、`{prefix}.blast.ids`；`.ids` 必须为目标命中序列 `sseqid`
- HMM 输出：`{prefix}.hmmout`、`{prefix}.hmm.ids`
- domtblout：`{prefix}.domtblout`、`{prefix}.domtblout.filter`
- 其它大文件/临时文件

规则：
- 中间态文件“可以多、可以变”，但必须放在 `identify/analysis/`，不得污染 `final/`

---

## 5. Manifest（JSON）契约（v1）

### 5.1 必须是严格 JSON
- 禁止 `# 注释`
- 禁止 Python 表达式（如 `final_ids_file.name`）
- 禁止重复 key（JSON 不允许）

### 5.2 artifacts 字段结构（v1）
manifest 中的 `"artifacts"` 必须是字典：
- key：artifact 名称（如 `final_members`）
- value：artifact 信息对象（至少包含）
  - `artifact`（建议与 key 相同）
  - `path` 或 `relpath_or_hint`（相对 workspace_root 的路径）
  - `full_path`（绝对路径）
  - `type`（table/fasta/markdown/image/newick/...）
  - `size`（字节数）

Identify 阶段：
- 必选 artifacts：
  - `final_members`
  - `final_proteins`
  - `identify_report`
  - `hmm_results`（可指向 identify/analysis/，用于追溯）
- 可选 artifacts：
  - `blast_results`（仅当 BLAST 实际运行并生成 `{prefix}.blast.ids` 时写入；ID 来自 target `sseqid`）
  - `blast_source_matrix`（记录 reference query sources/query IDs 与 best hit 指标）

---

## 6. 示例（真实运行结果形态）

final/ 目录（示例）：
final/
├── Ft.SPL.final_members.id
├── Ft.SPL.final_members.pep.fa
├── Ft.SPL.identify.manifest.json
└── Ft.SPL.identify_report.md
