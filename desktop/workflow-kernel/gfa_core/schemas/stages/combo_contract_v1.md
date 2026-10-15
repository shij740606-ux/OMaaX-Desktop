# Combo Stage Contract v1 (Tree + Motif + Structure + Domain Combination Plot)

## 1. Workspace 目录归属

- 最终产物：`workspace/<job_id>/final/`
- 中间文件：本阶段不产生中间文件，直接读取其他阶段产物

## 2. 输入（Input Contract）

### 2.1 必需输入（依赖前置阶段）

1. **系统发育树**：`final/{prefix}.tree.nwk`
   - 来源：Phylogeny 阶段

2. **MEME XML**：`motif/analysis/meme_out/meme.xml`
   - 来源：Motif 阶段

3. **GFF3（结构）**：`structure/analysis/{prefix}.structure.filtered.gff3`
   - 来源：Structure 阶段
   - 当 `panels` 不包含 `structure` 时可不读取。

4. **domtblout**：`domain/analysis/results.domtblout`
   - 来源：Domain 阶段
   - 当 `panels` 不包含 `domain` 时可不读取。

5. **MEME XML 可选性**：默认读取；当 `panels` 不包含 `motif` 时可不读取。

### 2.2 可选输入

- **亚家族文件**：自动查找仅使用 `confirmed_subfamily.tsv`；`reference_classification.subfamily.tsv` / `geneID.subfamily` 属于 target prediction，只有通过 `--subfamily` 显式指定时才可用于查看预测图。
- **展示类型**：`--panels motif,structure,domain`，默认三类全展示；修订图允许选择任意两个类型或三类全选，单类型图应使用对应阶段已有图。


> `analysisRoute=subfamily_evidence` 用于亚家族分类证据生成，默认不消费目标物种的 prediction 或 confirmed 标签；分类后的正式重绘必须显式使用 confirmed 标签。

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出

| 文件 | 说明 |
|------|------|
| `{prefix}.combo_tree_motif_structure_domain.pdf` | 组合图（PDF） |
| `{prefix}.combo.manifest.json` | 机器清单 |

### 3.2 组合图面板说明

- **Panel A**: 系统发育树（含基因 ID）
- **Panel B**: 亚家族竖线
- **Panel C/E 可变**: Motif、基因结构、蛋白结构域按 `panels` 动态展示。
- **Motif**: 按 XML 实际长度比例。
- **Gene structure**: 按 GFF3 实际长度比例，只画 CDS + UTR。
- **Domain**: 按蛋白长度 qlen 比例。

## 4. 执行流程

调用 R 脚本：`scripts/tree_motif_domain_structure.R/combo_tree_motif_structure_domain.R`

参数：
```
Rscript combo_tree_motif_structure_domain.R \
  <treefile> <subfamily_file> <meme_xml> <gff3_file> <domtblout_file> \
  [evalue_cutoff] [coverage_percent] [output_pdf] [panels]
```

默认值：
- evalue_cutoff: 1e-5
- coverage_percent: 0（不启用）
- panels: `motif,structure,domain`；至少两个类型。

## 5. Manifest Schema

### 5.1 artifacts

```json
{
  "combo_plot_pdf": {
    "artifact": "combo_plot",
    "type": "image",
    "format": "pdf",
    "description": "Tree + Motif + Structure + Domain combo visualization"
  }
}
```

### 5.2 parameters

- `evalue`: E-value 阈值
- `coverage`: 覆盖度阈值（可选）
- `subfamily_file`: 亚家族文件路径
- `tree_file`: 树文件路径
- `meme_xml`: MEME XML 路径
- `gff3_file`: GFF3 文件路径
- `domtblout_file`: domtblout 文件路径
- `panels`: 实际展示的类型列表

## 6. 依赖阶段

Combo 阶段默认依赖以下阶段完成：
- Phylogeny（树文件）
- Motif（meme.xml）
- Structure（GFF3）
- Domain（domtblout）

当通过修订图指定 `panels` 时，除 Phylogeny 外，只检查所选类型对应的上游阶段。

运行前检查依赖，如缺失则报错提示。

Scope 模式下依赖检查必须使用同一 `scope_id` 的 scoped manifest：

- `final/{prefix}.{scope_id}.phylogeny.manifest.json`
- `final/{prefix}.{scope_id}.motif.manifest.json`
- `final/{prefix}.{scope_id}.structure.manifest.json`
- `final/{prefix}.{scope_id}.domain.manifest.json`

manifest 必须记录 `scope_id`、`scope_mode`、`scope_file` 和用于组合图的上游文件路径。若找不到 scoped manifest，不应静默回退到其他 scope 的最新结果。
