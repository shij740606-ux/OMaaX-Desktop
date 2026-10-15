# Interspecies Synteny Stage Contract v1 (Comparative Genomics)

## 1. Workspace 目录归属

- 最终产物：`workspace/<job_id>/final/`
- 中间文件：`workspace/<job_id>/interspecies_synteny/analysis/`
- 比对输出：`workspace/<job_id>/interspecies_synteny/analysis/`

## 2. 输入（Input Contract）

### 2.1 必需输入

1. **目标家族蛋白序列**：`final/{prefix}.final_members.pep.fa`
   - 来源：Identify 阶段或同一 `scope_id` 的 phylogeny/identify 产物
   - 用途：目标+参考家族进化树

2. **参考/比较家族蛋白序列**：`{Species}.{Family}.pep.fa` 或 `final/{Species}.{Family}.final_members.pep.fa`
   - 来源：`scope_file` 中 `reference_species` / `comparative_species` 的 `family_pep`，config.xlsx 中 File_Scope=known_family 的文件，或各 ref-side 物种已完成 identify 的 `final_members.pep.fa`
   - 用途：目标+全部 ref-side 家族成员合并建树（多 ref 时必须包含 At/Ha/Os 等各物种家族序列，不能只用主参考 At）

3. **目标物种全蛋白组 + GFF3**：`scope_file.target_species[*].pep/gff`
   - 用途：target-ref 互比 BLAST/DIAMOND 与 MCScanX

4. **参考/比较物种全蛋白组 + GFF3**：`scope_file.reference_species/comparative_species[*].pep/gff`
   - 用途：target-ref 互比 BLAST/DIAMOND 与 MCScanX

> Scope 模式下，`target_species` 只作为 query/target 侧；`reference_species` 与被选中的 `comparative_species` 作为 ref-side 参与跨物种比较。若某个 ref-side 只有 family_pep、没有全蛋白组 pep，则仍可构建家族树，但 MCScanX/JCVI 共线性应标记为 partial_success 或 skipped，不能把 family_pep 当全蛋白组。

### 2.2 可选输入

- **参考序列**：用于进化树构建的锚定序列
- `--scope-file`：target/reference/comparative scope JSON。若传入，CLI 必须优先使用 scope 中的物种选择和文件路径。
- **`refSpeciesList`（前端/API）**：种间共线性 ref-side 多选列表。UI 勾选 At/Ha/Os 后，该列表必须完整传到 `/api/execute`，再由 `pipeline_backend` 转为 `--ref-species At,Ha,Os`。不得被 scope 的单一 `referenceSpecies` 覆盖。
- **`--ref-species`（CLI）**：逗号分隔的 ref-side 物种；与 `--target-species` 做笛卡尔 pairwise 比较（例如 Ls × At,Ha,Os → Ls.At / Ls.Ha / Ls.Os）。
- **`interspeciesPlotMode` / `--plot-mode`**：种间共线性绘图模式。取值 `both`（默认，所有物种总图 + 两两图）、`multi`（仅所有物种总图）、`pairwise`（仅目标物种与 ref-side 物种两两图）。

Scope 写入规则（Plan B）：
- `referenceSpecies`：主参考（通常 At，来自 ortholog scope）
- `comparativeSpecies`：`refSpeciesList` 中除 target 与主参考外的物种（Ha、Os）
- `scope_mode`：多 ref 时自动升级为 `target_plus_reference_plus_comparative`

## 3. 输出（Output Contract）

### 3.1 final/ 必须/可选产出

| 文件 | 说明 |
|------|------|
| `{prefix}.interspecies_synteny.pdf` | 种间共线性图（PDF） |
| `{prefix}.interspecies_synteny.png` | 所有物种总图（PNG；`plot_mode=multi/both`） |
| `{prefix}.{target}.{ref}.interspecies_synteny.pdf` | 两两共线性图（PDF；`plot_mode=pairwise/both`，每个 target-ref 组合一张） |
| `{prefix}.{target}.{ref}.interspecies_synteny.png` | 两两共线性图（PNG；`plot_mode=pairwise/both`，每个 target-ref 组合一张） |
| `{prefix}.{target}.{ref}.interspecies_synteny.marked.pdf/png` | 两两图家族成员高亮版本（可选，存在可标记 anchors 时生成） |
| `{prefix}.interspecies_tree.nwk` | 物种间进化树（Newick格式） |
| `{prefix}.interspecies_tree.pdf` | 进化树图（PDF） |
| `{prefix}.interspecies_tree.circular_tree.pdf/png` | 物种间进化树圈图；存在亚家族标签时默认在 gene ID 外侧绘制动态避让的亚家族外圈弧线 |
| `{prefix}.interspecies_synteny_report.md` | 分析报告 |
| `{prefix}.interspecies_synteny.manifest.json` | 机器清单 |

### 3.2 interspecies_synteny/analysis/ 中间文件

- `{ref}_{target}.blast` - 互比BLAST结果
- `{ref}.gff`, `{target}.gff` - 过滤后的GFF文件
- `{ref}_{target}.collinearity` - MCScanX种间共线性结果
- `{ref}_{target}.anchors` - JCVI anchors格式
- `{ref}_{target}.anchors.simple` - JCVI简化格式
- `seqids`, `layout` - JCVI绘图配置文件
- `*.bed` - JCVI bed格式基因位置

## 4. 执行流程

```
[Stage 1] 数据准备与合并
  Input:  {ref}.final_members.pep.fa, {target}.pep.fasta
  Output: {ref}_{target}_all.pep.fa
  
[Stage 2] 系统发育树构建
  Input:  合并后的蛋白序列
  Output: {prefix}.interspecies_tree.nwk, {prefix}.interspecies_tree.pdf
  Logic:  复用 run_phylogeny.py 的逻辑

[Stage 3] GFF过滤
  Input:  {ref}_final.gff3, {target}_final.gff3
  Output: {ref}.gff, {target}.gff

[Stage 4] BLAST互比
  Input:  {ref}.pep.fasta, {target}.pep.fasta
  Output: {ref}_{target}.blast
  Cmd:    blastp -query {target} -db {ref} + blastp -query {ref} -db {target}

[Stage 5] MCScanX种间共线性
  Input:  {ref}_{target}.blast, {ref}.gff, {target}.gff
  Output: {ref}_{target}.collinearity
  Cmd:    MCScanX ./{ref}_{target}

[Stage 6] JCVI格式转换
  Input:  {ref}_{target}.collinearity
  Output: {ref}_{target}.anchors, {ref}_{target}.anchors.simple

[Stage 7] JCVI绘图
  Input:  anchors, bed files, seqids, layout
  Output: {prefix}.interspecies_synteny.pdf/png and/or {prefix}.{target}.{ref}.interspecies_synteny.pdf/png
  Cmd:    python -m jcvi.graphics.karyotype seqids layout
```

## 5. 依赖工具

- `mafft` 或 `muscle` - 多序列比对（进化树）
- `iqtree` 或 `fasttree` - 进化树构建
- `makeblastdb`, `blastp` - 蛋白比对
- `MCScanX` - 种间共线性分析
- `jcvi` (Python包) - 共线性可视化

## 6. Manifest Schema

### 6.1 必选 artifacts

- `tree_nwk` - 进化树Newick文件
- `tree_pdf/png` - 进化树可视化
- `synteny_pdf/png` - 所有物种总图（`plot_mode=multi/both`）
- `*_pairwise_pdf/png` - 两两共线性图（`plot_mode=pairwise/both`）
- `collinearity` - MCScanX结果
- `report` - 分析报告
- `manifest` - 清单自身

### 6.2 metrics

- `ref_gene_count` - 参考物种基因数
- `target_gene_count` - 目标物种基因数
- `synteny_blocks` - 共线性块数
- `anchor_pairs` - 锚定基因对数
- `tree_nodes` - 进化树节点数
- `plot_mode` - 实际绘图模式：`multi` / `pairwise` / `both`
- `multi_plot_generated` - 所有物种总图是否生成
- `pairwise_plot_count` - 成功生成的两两共线性图数量
- `pairwise_plots` - 两两图产物列表，记录 pair、target_species、ref_species、pdf/png/marked 文件路径

### 6.3 parameters

- `ref_species` - 参考物种
- `refSpeciesList` - 前端/API 传入的 ref-side 多选（与 `ref_species` 等价，逗号展开）
- `target_species` - 目标物种
- `reference_species` / `comparative_species` / `selected_species` - Scope 模式下必须记录
- `scope_id` / `scope_mode` / `scope_file` - Scope 模式下必须记录
- `evalue` - BLAST阈值
- `minspan` - JCVI最小跨度
- `interspeciesPlotMode` / `plot_mode` - 绘图模式，默认 `both`
- `tree_subfamily_arc_enabled` / `treeSubfamilyArcEnabled` - 物种间进化树圈图是否显示亚家族外圈弧线，默认 true
- `tree_subfamily_arc_gap` / `treeSubfamilyArcGap` - 弧线相对 gene ID 外边界的额外避让距离，默认 1.2；前端修订面板可调
- `tree_subfamily_arc_label_gap` / `treeSubfamilyArcLabelGap` - 亚家族名称相对弧线的额外距离，默认 0.6
- `tree_subfamily_arc_label_size` / `treeSubfamilyArcLabelSize` - 亚家族名称字号；0 或空值表示自动

## 7. 失败语义

- **failed**: 输入文件缺失或关键工具失败
- **partial_success**: 进化树或共线性部分失败
- **success**: 完整成功

## 8. 与任务1的区别

| 特性 | 任务1（种内） | 任务3（种间） |
|------|-------------|-------------|
| 比对方式 | 自比（diamond/blastp） | 互比（双向blastp） |
| MCScanX参数 | `./{prefix}` | `./{ref}_{target}` |
| 进化树 | 基因家族内 | 物种间比较 |
| 可视化 | Circos圈图 | JCVI线性图 |
| 输入物种 | 单物种 | 多物种（2+） |

## 9. 参考文档

- 测试代码: `/home/xia-lab/wutx/00test/geneFamily20200803/66.SPL_ComparativeGenome`
- JCVI文档: https://github.com/tanghaibao/jcvi
