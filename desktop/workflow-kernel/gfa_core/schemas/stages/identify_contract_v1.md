# Identify Stage Contract v1 (Gene Family Member Identification)

本契约定义 Identify（基因家族成员鉴定）阶段的：
- 输入要求
- 输出产物（final 必有）
- 可追溯的中间态（identify/analysis）
- manifest 中 artifacts/metrics/parameters 的最小约束

> Identify 已跑通的基线示例：final/ 下存在 4 个文件：
> - {prefix}.final_members.id
> - {prefix}.final_members.pep.fa
> - {prefix}.identify_report.md
> - {prefix}.identify.manifest.json

---

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

Identify 相关文件必须落在：
- workspace/<job_id>/identify/analysis/   # 工具原生输出/中间态
- workspace/<job_id>/final/              # 对外最终产物（下游 stage 只读这里）
- workspace/<job_id>/logs/               # 日志

---

## 2. 输入（Input Contract）

Identify 的输入至少包含：
- 全蛋白 FASTA（proteome pep fasta）
- PFAM/HMM 或相关域模型（如用 HMMER）
- 可选：BLAST/reference-protein homology search 参考家族蛋白；这些 FASTA 固定作为 query，目标物种全蛋白 FASTA 固定作为 BLAST database；可为多个 identify-only reference species / reference sets，例如 Arabidopsis ANAC 与 rice OsNAC
- 可选：`Primary_Reference_Species`，用于后续阶段默认参考物种；不得与 identify-only BLAST reference 混用

输入文件路径通常来自 input_dir（由前端/cli 传入），但 Identify 阶段必须把“输入摘要”写入 manifest.inputs：
- path/full_path
- size
- checksum/sha256（推荐）
- source（推荐：input_dir 或上游 stage）

---

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

final/ 目录必须至少包含 4 个文件：

1) final/{prefix}.final_members.id
- 每行一个最终成员 ID
- 作为下游 stage 的基础输入/一致性校验

2) final/{prefix}.final_members.pep.fa
- 从全蛋白 FASTA 按 final_members.id 提取的蛋白序列
- 作为下游 phylogeny/motif/MSA 的主要输入
- 约束：FASTA header 的 ID 集应尽量覆盖 final_members.id；
  若存在缺失，需要在 report/metrics 中说明（missing_count/missing 示例）

3) final/{prefix}.identify_report.md
- 人可读报告
- 强烈建议包含 Reproducibility（cmd、tool_versions、inputs hash、运行时间）

4) final/{prefix}.identify.manifest.json
- 机器可读清单（必须是严格 JSON，无注释/无重复 key）

---

### 3.2 identify/analysis/ 推荐产出（可追溯中间态）

Identify 的中间态文件应保存在 identify/analysis/，常见包括：
- HMMER 输出：{prefix}.hmmout, {prefix}.domtblout, {prefix}.domtblout.filter, {prefix}.hmm.ids
- BLAST 输出：单参考时兼容 `{prefix}.blast.ids`；多参考时必须逐参考输出 `{prefix}.blast.{reference_label}.out`、`{prefix}.blast.{reference_label}.ids`，并生成 union 后的 `{prefix}.blast.ids`；所有 `.ids` 必须来自 outfmt6 `sseqid`，即目标物种命中蛋白/基因 ID
- BLAST 来源矩阵：`{prefix}.blast.source_matrix.tsv`，至少包含 `gene_id`、`blast_reference_sources`、`blast_reference_count`，并建议包含支持该 target 的 reference query IDs、best evalue、best bitscore
- BLAST 数据库：`blast_db.target_proteome.*`，由目标物种全蛋白 FASTA 建库；不得为每个参考物种建立 reference-family 数据库作为 identify 默认行为

说明：
- 中间态文件“可多、可变”，但位置必须固定在 identify/analysis/
- 是否复制到 final 不强制，但必须可通过 manifest.artifacts 追溯到 full_path

---

## 4. Manifest 要求（Identify v1）

Identify manifest 必须满足 core/manifest_v1.schema.json。

此外，Identify 阶段对 artifacts 的最小要求：

### 4.1 必选 artifacts（必须存在）
- final_members
  - type: table
  - 指向 final/{prefix}.final_members.id

- final_proteins
  - type: fasta
  - 指向 final/{prefix}.final_members.pep.fa

- identify_report
  - type: markdown
  - 指向 final/{prefix}.identify_report.md

- hmm_results
  - type: table
  - 可指向 identify/analysis/{prefix}.hmm.ids（或等价文件）
  - 用于追溯 HMM 筛选结果

### 4.2 可选 artifacts（条件触发）
- blast_results
  - 仅当 BLAST/reference-protein homology search 实际运行且生成 `{prefix}.blast.ids` 时写入
  - 只有 `config.xlsx` 明确声明 known-family / seed reference protein 行时才运行；未声明时 Identify 为 HMM-only 成功路径，不应要求该 artifact
  - type: table 或 id_list
  - 指向 identify/analysis/{prefix}.blast.ids
  - 多参考时表示所有 reference query sets 命中的 target `sseqid` union

- blast_source_matrix
  - 多参考 BLAST/reference-protein homology search 时写入
  - type: tsv
  - 指向 identify/analysis/{prefix}.blast.source_matrix.tsv
  - 用于追踪每个候选 target gene 来自哪些参考物种/参考蛋白 query IDs 支持

- blast_{reference_label}_out
  - 多参考时每个 reference set 分别写入
  - type: blast_outfmt6
  - 指向 identify/analysis/{prefix}.blast.{reference_label}.out

---

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）
- total_proteins
- candidates
- final_members

（可选：missing_count / extracted 等序列提取统计）

### 5.2 parameters（至少包含）
- required_pfams（或等价：用于定义家族识别条件）
- dom_i_evalue_max
- hmm_cov_min
- cpu
- hmm_combine_mode
- hmm_blast_merge_mode
- blast_reference_mode（如 `per_reference_union`）
- blast_reference_sets（实际用于 reference-protein search 的 reference labels）
- identify_blast_reference_species（只用于 identify BLAST/reference-protein homology search，可多个）
- primary_reference_species（后续阶段默认主参考，通常一个；identify 不应把 BLAST-only reference 自动写入该字段）
- blast_database_role（固定为 `target_proteome`）
- blast_query_role（固定为 `reference_family_pep`）
- blast_candidate_id_column（固定为 `sseqid`）

### 5.3 hmm_models（多 HMM/Pfam 审计，必须包含）

当 Identify 使用 `required_pfams` 或多个 HMM 模型时，manifest 必须记录：
- requested_pfams：用户/配置请求的 Pfam accession，去掉版本号后比较，如 PF01849.24 -> PF01849
- loaded_accessions：实际 query HMM 中解析到的 Pfam ACC
- missing_requested_pfams：requested 中未实际加载的 Pfam
- query_name_to_accession：HMMER domtblout query name 到 Pfam accession 的映射
- per_model_hits：每个 HMM/Pfam 模型命中的成员数

失败语义：`missing_requested_pfams` 非空时，manifest.status 必须为 `failed`，不得写成 success 或 partial_success。

### 5.4 multi-reference BLAST / reference-protein homology search

当论文方法或用户输入包含多个参考物种家族蛋白时，Identify 不应先无追踪地合并参考 FASTA 后只产出单一路径。推荐流程：

BLAST/reference-protein homology search 的启用条件以 `config.xlsx` 为准：只有 config 中声明的 known-family/seed reference protein（推荐 `File_Type=family_pep`，兼容 known_family/seed_reference 语义下的 `pep`）才作为 identify BLAST query。若 config 未声明该类参考家族蛋白，即使输入目录存在形如 `{Species}.{Family}.pep.fasta` 的文件，也不得仅凭文件名自动运行 BLAST；本阶段应按 HMM-only 路径成功完成。

1. 每个 reference species / reference set 的家族蛋白 FASTA 单独作为 BLASTP query。
2. 每个 target species 的全蛋白 FASTA 在 `identify/<Species>/analysis/` 下建一次 `blast_db.target_proteome.*` 并复用。
3. 每一路输出独立 raw outfmt6 和 ids 文件；ids 从 outfmt6 `sseqid` 提取。
4. 按 target gene 对所有 reference search candidates 取 union。
5. 在 source matrix 中保留每个 target gene 的参考来源和 reference query IDs，例如 `Arabidopsis_NAC_BLAST`、`Rice_NAC_BLAST`。
6. 最终候选集合按 `HMM candidates union multi-reference BLAST candidates` 或用户指定的 `hmm_blast_merge_mode` 合并，再进入 Pfam/CDD domain confirmation。

若工程上必须使用合并参考 FASTA，也必须保证 FASTA header 或解析结果能恢复 reference source；manifest 中仍需写明 `reference_sets` 和来源矩阵。

配置语义必须分离：`Identify_Blast_Reference_Species` 只定义 identify 搜索证据来源；`Primary_Reference_Species` 才定义后续 phylogeny/reference_classification/ortholog/KaKs/PPI 等阶段的默认参考物种。若只配置旧字段 `Reference_Species`，可兼容作为 `Primary_Reference_Species`，但应在 manifest warning 中提示用户升级配置。

---

## 6. 失败语义（Failure Semantics）

若 Identify 失败：
- manifest.status = "failed"
- report 仍建议产出（写明失败原因、失败点、关键日志位置）
- final_members / final_proteins 可以不存在，但 manifest 必须存在且描述失败
- 多 Pfam/HMM 请求未完整加载时必须失败；不能保留候选结果并标记 success

---

## 7. 示例（运行结果形态）

final/
├── Ft.SPL.final_members.id
├── Ft.SPL.final_members.pep.fa
├── Ft.SPL.identify.manifest.json
└── Ft.SPL.identify_report.md

---

## 8. Scope 扩展约定（target/reference/comparative）

`prefix` 只作为输出命名空间，不能单独表达本次分析范围。多物种或带参考物种的分析必须在 manifest 中记录：

- `scope_id`
- `scope_mode`，如 `target_only`、`target_plus_reference`
- `scope_file`
- `target_species`
- `reference_species`
- `comparative_species`
- `selected_species`

Identify 阶段只鉴定 `target_species`。`reference_species` 中的 known-family FASTA 可作为 seed/reference，但不应被写入 target 的 `final_members.id`。

兼容要求：

- 保留 legacy `final/{prefix}.identify.manifest.json`
- 同时建议写入 scoped manifest：`final/{prefix}.{scope_id}.identify.manifest.json`
- 下游应优先读取 scoped manifest；找不到时再回退 legacy manifest。
