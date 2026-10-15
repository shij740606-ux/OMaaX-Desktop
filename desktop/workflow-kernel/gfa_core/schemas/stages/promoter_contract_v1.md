# Promoter Stage Contract v1 (Cis-acting Element Prediction)

本契约定义 Promoter（顺式作用元件预测）阶段的：
- 输入要求
- 输出产物（final 必有）
- 可追溯的中间态（promoter/analysis）
- manifest 中 artifacts/metrics/parameters 的最小约束

---

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

Promoter 相关文件必须落在：
- workspace/<job_id>/promoter/analysis/   # 工具原生输出/中间态
- workspace/<job_id>/final/              # 对外最终产物（下游 stage 只读这里）
- workspace/<job_id>/logs/               # 日志

---

## 2. 输入（Input Contract）

### 2.1 必需输入
- **基因组注释 GFF3**：物种基因组注释文件
  - 来源：用户上传到 input/<job_id>/
  - 命名规则：{prefix}.gff3 或 {species_short}*.gff3
  - Scope 模式：从 `scope_file` 的单个 `target_species` 中读取该目标物种的 GFF/GFF3；不能从 `Multi.<family>` prefix 猜测物种

- **基因组 FASTA**：物种基因组序列文件
  - 来源：用户上传或参考基因组
  - 命名规则：{prefix}.genome.fasta 或 *genome*.fasta
  - Scope 模式：从同一目标物种读取 genome FASTA

- **基因 ID 列表**：`final/{prefix}.final_members.id`
  - 来源：Identify 阶段产物或用户显式传入 `--gene-id`
  - 用途：过滤 GTF，只提取家族成员启动子

> Promoter 阶段一次只支持一个目标物种。若 `scope_file.target_species` 包含多个物种，应要求用户选择单个 target 或分别运行每个目标物种，不能静默合并不同基因组的启动子。

### 2.2 图形输出必需输入
- **系统发育树**：`final/{prefix}.tree.nwk` 或同 scope phylogeny manifest 中的 `artifacts.tree`
  - 来源：Phylogeny 阶段产物
  - 用途：R可视化时与树结合展示
  - 执行约束：标准 Promoter 阶段若需要生成图，必须在同 scope Phylogeny 完成后执行；缺少树时不得静默产出无图的成功结果。

### 2.3 可选输入
- **亚家族分类**：`final/{prefix}.geneID.subfamily`
  - 来源：用户上传或 Identify 阶段
  - 用途：R可视化时分组展示

- **元件描述文件**：CSV格式
  - 用途：提供元件的功能描述

### 2.4 参数
- `--upstream-length`：上游序列长度（默认2000bp）
- `--email`：接收结果的邮箱地址
- `--scope-file`：target/reference/comparative scope JSON；Promoter 只使用单个 target 的 GFF/genome，并记录 scope 元数据
- PlantCARE 自动提交时按上传 FASTA 条数和字节大小双限制分批；默认每批不超过 30 条且不超过约 95 KB，以适配官网约 100 KB 的上传限制并降低网页提交超时风险
- 多批次运行时先提交全部 batch，再按 batch reference 轮询邮箱结果，避免每批串行等待造成总耗时成倍增加
- `--plantcare-max-records`：每批最多上传的 FASTA 记录数（默认 30）
- `--plantcare-max-upload-bytes`：每批最多上传的 FASTA 字节数（默认 95000）
- `--plantcare-submit-timeout`：每批 HTTP 提交等待 PlantCARE 网页响应的秒数（默认 300）

---

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1) `{prefix}.promoter.manifest.json`
   - 机器可读清单（JSON格式）

2) `{prefix}.promoter_report.md`
   - 人可读报告

### 3.2 promoter/analysis/ 推荐产出（可追溯中间态）

1) `gene.upstream.changeID.fasta`
   - 处理后的启动子序列（已格式化ID）

2) `PlantCARE_output_*.tab`
   - PlantCARE 原始输出

3) `element_counts.txt`
   - 元件统计（每种元件的出现次数）

4) `PlantCARE.bed`
   - BED格式文件（用于GSDS等可视化）

5) `{prefix}_promoter*.{pdf,png,csv}`
   - R 可视化输出（如果有树文件）
   - **必须**写入 `workspace/<job_id>/final/`，禁止落在项目根目录
   - Python 执行 R 时使用 `cwd=final/`；若发现根目录遗留 `{prefix}_promoter*`，会自动搬迁或去重

---

## 4. Manifest 要求（Promoter v1）

### 4.1 artifacts 字段结构

- **plantcare_output**：PlantCARE原始输出表格
  - type: table
  - path: "promoter/analysis/PlantCARE_output_*.tab"

- **element_counts**：元件统计表
  - type: table
  - path: "promoter/analysis/element_counts.txt"

- **bed_file**：BED格式文件
  - type: bed
  - path: "promoter/analysis/PlantCARE.bed"

- **promoter_report**：分析报告
  - type: markdown
  - path: "final/{prefix}.promoter_report.md"

- **manifest**：清单文件自身
  - type: json
  - path: "final/{prefix}.promoter.manifest.json"

### 4.2 metrics 最小字段

- total_elements：总元件数
- unique_elements：不同元件类型数
- genes_with_elements：含有元件的基因数

### 4.3 parameters 最小字段

- upstream_length：上游序列长度
- email：接收结果的邮箱
- gff3_file / genome_file / gene_id_file：实际使用的输入文件
- plantcare_submission_mode / plantcare_batching_enabled / plantcare_max_upload_bytes / plantcare_max_records / plantcare_submit_timeout / plantcare_batch_count：PlantCARE 提交模式、分批限制与提交超时信息
- plantcare_batch_files / plantcare_batch_result_files / plantcare_merged_result：多批次运行时的可追溯文件
- scope_id / scope_mode / scope_file / target_species / reference_species / comparative_species / selected_species：Scope 模式下必须记录

---

## 5. 执行流程

1. **数据准备**
   - GFF3 → GTF (gffread)
   - GTF → 上游序列提取 (seqkit subseq)
   - FASTA header 格式化

2. **PlantCARE 提交**
   - multipart/form-data POST 到服务器
   - 若 `gene.upstream.changeID.fasta` 超过安全上传阈值，按 FASTA 记录边界拆为多个批次分别提交
   - 多批次 reference 使用 `{prefix}_b001`、`{prefix}_b002` 等唯一后缀，避免邮件附件混淆
   - 等待异步处理

3. **结果获取**
   - IMAP 轮询邮箱
   - 等待 no-reply@psb.vib-ugent.be 邮件
   - 下载并解压附件
   - 多批次结果必须全部返回后合并为统一 `.tab`，再进入解析；任一批缺失不得静默生成部分结果

4. **结果解析**
   - 统计元件类型和数量
   - 转换为 BED 格式

5. **可视化**（可选）
   - 调用 promoter_analysis_complete.R
   - 生成树+元件组合图

---

## 6. 失败语义（Failure Semantics）

### 6.1 失败条件
- GFF3/基因组文件不存在
- PlantCARE 提交失败
- PlantCARE 提交响应超时；应减小每批条数/字节数或提高 `--plantcare-submit-timeout`，不要只延长邮箱等待 timeout
- 邮箱获取超时（默认3600秒）；若 FASTA 超过 PlantCARE 上传限制，应分批而不是只延长 timeout
- 解析失败

### 6.2 失败处理
- manifest.status = "failed"
- report 仍建议产出（写明失败原因）
- artifacts 可包含部分成功产物

---

## 7. 示例（运行结果形态）

### 7.1 目录结构示例
```
workspace/<job_id>/
├── promoter/
│   └── analysis/
│       ├── gene.upstream.gtf
│       ├── gene.upstream.changeID.fasta
│       ├── PlantCARE_output_12345.tab
│       ├── element_counts.txt
│       ├── PlantCARE.bed
│       └── Ft_SPL_promoter_tree_promoter_combined.pdf
├── final/
│   ├── Ft.SPL.promoter.manifest.json
│   └── Ft.SPL.promoter_report.md
└── logs/
    └── promoter.log
```

### 7.2 关键验证点

1. **文件名精确匹配**：使用契约约定的命名
2. **Manifest 格式**：artifacts 必须是字典
3. **输入依赖**：必须验证 GFF3 和基因组存在
4. **工具版本记录**：必须记录 gffread, seqkit, R 版本

---

## 8. 与其他阶段的依赖关系

| 阶段 | 依赖方向 | 说明 |
|------|----------|------|
| Identify | 无直接依赖 | 需要 GFF3 和基因组 |
| Phylogeny | 无直接依赖 | 可提供树文件用于可视化 |
| Structure | 需要GFF3 | 可复用部分输入文件 |
| Domain | 无直接依赖 | 独立的分析流程 |
| Motif | 无直接依赖 | 独立的分析流程 |

---

## 9. 注意事项

1. **PlantCARE 异步性**：结果通过邮件返回，需要轮询邮箱
2. **邮箱配置**：需要有效的 IMAP 访问凭证
3. **超时设置**：默认等待3600秒，可配置；上传超过官网限制时需自动分批
4. **可视化依赖**：R 脚本执行需要树文件存在
