# Annotation Stage Contract v1

## 1. Workspace 目录

本阶段遵守 `workspace_contract_v1.md`：

- 最终可复用产物位于 `workspace/<job_id>/final/`
- 原始注释和中间文件位于 `workspace/<job_id>/annotation/analysis/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入

### 必需输入

- 蛋白序列 FASTA：
  - 上传文件：`*.pep.fa`, `*.pep.fasta`, `*.protein.fa`, `*.protein.fasta`
  - 或 identify 阶段输出：`final/{prefix}.final_members.pep.fa`

### 可选输入

- 用户上传的 `gene2go.txt` / `gene2go.tsv`
- 用户上传的 `gene2ko.txt` / `gene2ko.tsv`
- 用户上传的 eggNOG `.emapper.annotations`

## 3. 当前本地注释方法

### GO

使用 InterProScan：

```bash
interproscan.sh -i input.pep.fa -t p -f TSV,XML,GFF3 --goterms --pathways --iprlookup
```

从 InterProScan TSV 中解析 `GO:xxxxxxx`，生成 GO 背景文件。

### KEGG

KEGG/KO 本地注释规划使用 KofamScan：

```bash
exec_annotation -f detail-tsv --no-report-unannotated input.pep.fa
```

从高置信 `*` 命中解析 `Kxxxxx`，生成 `gene2ko.txt`。若 KOfam profiles
或 `ko_list` 不完整，GO 注释仍可成功，Manifest 中记录 `kofamscan_status`
和具体原因。

## 4. 输出

### final/ 必须产出

- `{prefix}.gene2go.txt`
- `{prefix}.gene2ko.txt`（当 KofamScan 可用且有高置信 KO 命中时）
- `gene2go.txt`，兼容旧富集流程的非前缀副本
- `gene2ko.txt`，兼容旧富集流程的非前缀副本（仅在不存在或为空时写入）
- `{prefix}.annotation_summary.tsv`
- `{prefix}.annotation_report.md`
- `{prefix}.annotation.manifest.json`（直接执行 annotation/both 时）
- `{prefix}.annotation_go.manifest.json`（RNA-seq GO 自动注释内部 scope）
- `{prefix}.annotation_kegg.manifest.json`（RNA-seq KEGG 自动注释内部 scope）

### annotation/analysis/ 推荐产出

- `{prefix}.interproscan.tsv`
- `{prefix}.interproscan.xml`
- `{prefix}.interproscan.gff3`
- `{prefix}.raw_id.gene2go.txt`
- `{prefix}.kofamscan.detail.tsv`
- `{prefix}.raw_id.gene2ko.txt`
- `{prefix}.gene_id_map.tsv`
- `progress.json`（直接执行 annotation/both 时）
- `go.progress.json` / `kegg.progress.json`（RNA-seq GO/KEGG 自动注释内部 scope）
- `commands.sh` / `environment.yml` / `checksums.sha256`（both）
- `commands.go.sh` / `environment.go.yml` / `checksums.go.sha256`（GO scope）
- `commands.kegg.sh` / `environment.kegg.yml` / `checksums.kegg.sha256`（KEGG scope）

## 5. ID 归一化

本阶段保留原始 ID，同时生成 gene-level ID 用于后续表达和富集匹配。

默认规则：

- `FtPinG0001.01.T01` -> `FtPinG0001.01`
- `FtPinG0001.T01` -> `FtPinG0001`
- `*.mRNA1` 后缀同样移除

原始 ID 到归一化 ID 的映射写入：

```text
annotation/analysis/{prefix}.gene_id_map.tsv
```

## 6. Manifest 最小字段

- `stage`: 直接执行 annotation 时固定为 `annotation`；RNA-seq 自动注释内部 scope 可为 `annotation_go` 或 `annotation_kegg`
- `parameters.annotation_scope`: `both` / `go` / `kegg`
- `status`: `success` / `failed` / `partial_success`
- `prefix`
- `job_id`
- `workspace_root`
- `workspace`
- `input.protein_fasta`
- `parameters.method`
- `metrics.input_proteins`
- `metrics.canonical_genes_with_go`
- `metrics.go_term_count`
- `metrics.gene2go_pairs`
- `metrics.kofamscan_status`
- `metrics.canonical_genes_with_ko`
- `metrics.ko_term_count`
- `metrics.gene2ko_pairs`
- `artifacts.gene2go`
- `artifacts.interproscan_tsv`
- `artifacts.report`
- `artifacts.manifest`

## 7. 失败语义

- InterProScan 执行失败：`status = failed`
- InterProScan 成功但未解析到 GO：`status = failed`
- GO scope 成功但 KEGG 未运行：`annotation_go` 视为 `success`，KEGG 指标为空或 0
- KEGG scope 成功但 GO 未运行：`annotation_kegg` 视为 `success`，GO 指标为空或 0
- 直接执行 legacy `annotation` 阶段默认 scope 为 `both`，保持 `{prefix}.annotation.manifest.json` 兼容旧流程
