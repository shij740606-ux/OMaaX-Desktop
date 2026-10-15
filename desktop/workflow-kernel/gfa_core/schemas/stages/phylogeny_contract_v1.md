# Phylogeny Stage Contract v1 (Gene Family Phylogenetic Analysis)

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/phylogeny/analysis/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入
- **蛋白序列文件**：常规 route 使用亚家族确认后的 `final/{prefix}.final_members.pep.fa`；`analysisRoute=subfamily_evidence` 必须使用 identify 成员初筛确认后生成的 `final/{prefix}.curated_members.pep.fa`，缺失时阻断并要求打开成员初筛确认表。
  - 来源：常规 route 为 subfamily_confirm 阶段产物；`subfamily_evidence` route 为 identify_refine/domain_review 确认产物
  - 获取方式：从显式 scope 或 Identify/identify_refine Manifest 中获取；scope 模式由 phylogeny 合并后写入 `parameters.scope_input_fasta`
  - 验证：文件必须存在且不为空

- **Manifest 依赖**：Identify 阶段的 `{prefix}.identify.manifest.json`
  - 用途：获取上游分析参数和元数据

### 2.2 可选输入
- `scope_file`：当用户选择 target/reference/comparative 分析范围时必须传入。
- scoped 蛋白 FASTA：由 phylogeny 根据 scope 生成，路径写入 `parameters.scope_input_fasta`。
- reference/comparative family FASTA：来自 config.xlsx 中 `File_Scope=known_family` 的 `{Species}.{Family}.pep.fa/.fasta`。
- 亚家族标签文件：仅在显式 `--subfamily` 或后端解析到 `confirmed_subfamily.tsv` 时用于绘图；自动查找不得使用 `reference_classification.subfamily.tsv` 或 `geneID.subfamily` 作为 target prediction。
- 圈图亚家族外圈弧线：存在亚家族标签文件且输出 circular/both 时默认启用；弧线需根据基因 ID 文本宽度动态外移，避免遮挡 tip label。

Scope 规则：

- `target_only`：`subfamily_evidence` 使用 target `curated_members`，常规 route 使用 target `final_members`。
- `target_plus_reference`：target `curated_members/final_members` + reference known-family protein FASTA。
- `target_plus_comparative`：target `curated_members/final_members` + comparative known-family protein FASTA。
- `target_plus_reference_plus_comparative`：三类物种全部纳入同一棵树。

蛋白 FASTA 中的 `*` stop marker 必须在 job-local 派生 FASTA 中清理，不能修改用户原始输入。


> `analysisRoute=subfamily_evidence` 用于亚家族分类证据生成，默认不消费目标物种的 prediction 或 confirmed 标签；目标物种成员 FASTA 只能来自 `curated_members.pep.fa`。若用户暂停倒计时或关闭窗口导致未保存成员初筛确认结果，本阶段必须阻断并要求重新打开 identify 成员初筛确认表。分类后的正式重绘必须显式使用 `final_members.pep.fa` + `confirmed_subfamily.tsv`。

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

以下文件必须位于 `workspace/<job_id>/final/` 目录：

1. **`{prefix}.pep.msa.fa`**
   - 类型：多序列比对结果
   - 格式：FASTA 格式
   - 内容：比对后的蛋白序列，保留原始序列ID
   - 要求：序列长度一致，包含gap字符

2. **`{prefix}.tree.nwk`**
   - 类型：系统发育树
   - 格式：Newick 格式
   - 内容：基于比对构建的进化树
   - 要求：有效的 Newick 格式，支持标准软件解析

3. **`{prefix}.phylogeny_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：人可读的分析摘要、参数、统计结果、工具版本

4. **`{prefix}.phylogeny.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

### 3.2 phylogeny/analysis/ 推荐产出（可追溯中间态）

以下文件推荐位于 `workspace/<job_id>/phylogeny/analysis/` 目录：

1. **`{prefix}.pep.mafft.msa.fa`**
   - MAFFT 原始输出文件

2. **`{prefix}.iqtree.*`**
   - IQ-TREE 生成的所有输出文件（treefile, log, ckp.gz 等）

3. **比对和建树日志**
   - 工具的标准输出和错误输出

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式（不是列表），包含以下必需产物：

- **msa**：多序列比对文件
  - artifact: "msa"
  - path: "final/{prefix}.pep.msa.fa"
  - type: "fasta"
  - size: 文件大小（字节）

- **tree**：系统发育树文件
  - artifact: "tree"
  - path: "final/{prefix}.tree.nwk"
  - type: "newick"
  - size: 文件大小（字节）

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.phylogeny_report.md"
  - type: "markdown"
  - size: 文件大小（字节）

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.phylogeny.manifest.json"
  - type: "json"
  - size: 文件大小（字节）

### 4.2 可选 artifacts（条件触发）

- **bootstrap_tree**：引导支持树（如果生成）
- **iqtree_log**：IQ-TREE 详细日志文件
- **mafft_log**：MAFFT 详细日志文件
- **alignment_stats**：比对统计文件



## 4.3 Tree replot / circular subfamily arc parameters

- `tree_subfamily_arc_enabled` / `treeSubfamilyArcEnabled`：圈图是否显示亚家族外圈弧线，默认 true。
- `tree_subfamily_arc_gap` / `treeSubfamilyArcGap`：弧线相对基因 ID 外边界的额外距离，默认 1.2；前端修订面板默认 1.6 用于更保守避让。
- `tree_subfamily_arc_label_gap` / `treeSubfamilyArcLabelGap`：亚家族名称相对弧线的额外距离，默认 0.6。
- `tree_subfamily_arc_label_size` / `treeSubfamilyArcLabelSize`：亚家族名称字号；0 表示自动。

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **sequences_count**：比对序列数量（整数）
- **msa_length**：比对长度/列数（整数）
- **gap_percentage**：比对中的 gap 比例（浮点数，百分比）
- **tree_model**：IQ-TREE 选择的最佳替代模型（字符串）
- **alignment_time_sec**：MAFFT 比对耗时（浮点数，秒）
- **tree_building_time_sec**：IQ-TREE 建树耗时（浮点数，秒）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀，格式：物种.家族（字符串）
- **mafft_params**：MAFFT 实际使用的参数（字符串）
- **iqtree_params**：IQ-TREE 实际使用的参数（字符串）
- **mafft_version**：MAFFT 工具版本（字符串）
- **iqtree_version**：IQ-TREE 工具版本（字符串）
- **stage**：阶段名称，固定为 "phylogeny"（字符串）
- **job_id**：任务ID（字符串）
- **run_id**：运行ID（字符串）
- **manifest_version**：Manifest版本，固定为 "1.0"（字符串）
- **status**：状态，"success" 或 "failed"（字符串）
- **scope_id / scope_mode / scope_file**：若本阶段由 scope 驱动则必须记录
- **target_species / reference_species / comparative_species / selected_species**：若本阶段由 scope 驱动则必须记录
- **scope_input_fasta**：实际用于 MAFFT/IQ-TREE 的 scoped protein FASTA
- **timestamp**：时间戳，ISO格式（字符串）
- **start_time**：开始时间，ISO格式（字符串）
- **end_time**：结束时间，ISO格式（字符串）
- **elapsed_sec**：耗时，秒（浮点数）
- **cmd**：执行命令列表（数组）
- **workspace_root**：工作空间根目录路径（字符串）
- **workspace**：当前阶段工作目录路径（字符串）
- **input_dir**：输入目录路径（字符串）

## 6. 失败语义（Failure Semantics）

### 6.1 失败条件

以下情况视为阶段失败：

1. **输入验证失败**：Identify 输出不存在或无效
2. **MAFFT 失败**：比对工具返回非零状态码
3. **IQ-TREE 失败**：建树工具返回非零状态码
4. **文件系统错误**：无法写入输出文件

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空或包含部分成功指标
- `"artifacts"`: 可包含已成功生成的部分产物
- 报告应说明失败原因和关键日志位置

### 6.3 部分成功

如果 MAFFT 成功但 IQ-TREE 失败：
- 状态可设为 `"partial_success"`
- 包含已生成的 MSA 文件
- 记录详细的失败原因

## 7. 示例（运行结果形态）

### 7.1 目录结构示例
workspace/20250101_123456_abcdef/
├── phylogeny/
│ └── analysis/
│ ├── Ft.SPL.pep.mafft.msa.fa
│ ├── Ft.SPL.iqtree.treefile
│ ├── Ft.SPL.iqtree.log
│ └── Ft.SPL.iqtree.ckp.gz
├── final/
│ ├── Ft.SPL.pep.msa.fa
│ ├── Ft.SPL.tree.nwk
│ ├── Ft.SPL.phylogeny_report.md
│ └── Ft.SPL.phylogeny.manifest.json
└── logs/
└── 20250101_123500_phylogeny.log


### 7.2 关键验证点

1. **文件名精确匹配**：必须使用 `{prefix}.pep.msa.fa` 和 `{prefix}.tree.nwk`
2. **Manifest 格式**：artifacts 必须是字典，不是列表
3. **输入依赖**：必须从 Identify 的 Manifest 获取输入文件路径
4. **工具版本记录**：必须记录 MAFFT 和 IQ-TREE 版本
5. **必需字段完整**：Manifest 必须包含所有 schema 要求的 17 个字段

### 7.3 执行参数示例

默认执行参数：
- **MAFFT**: `mafft --auto --thread {cpu} input.fasta`
- **IQ-TREE**: `iqtree -s {msa_file} -m MFP -bb 1000 -nt {cpu} -pre {prefix}.iqtree`

用户可通过命令行参数覆盖默认参数。
