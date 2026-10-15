# Motif Stage Contract v1 (MEME Motif Prediction and Visualization)

## 1. Workspace 目录归属

- 最终产物：`workspace/&lt;job_id&gt;/final/`
- 中间文件：`workspace/&lt;job_id&gt;/motif/analysis/`
- MEME 输出：`workspace/&lt;job_id&gt;/motif/analysis/meme_out/`

## 2. 输入（Input Contract）

### 2.1 必需输入

1. **蛋白序列文件**：常规 route 使用 `final/{prefix}.final_members.pep.fa`。
   - 来源：Identify 阶段
   - 获取方式：自动从 `final/` 目录查找
   - Scope 模式下必须优先使用同 scope phylogeny manifest `parameters.scope_input_fasta`；`analysisRoute=subfamily_evidence` 的该 FASTA 由目标 `curated_members.pep.fa` + reference FASTA 合并生成。
   - 在 job-local 派生 FASTA 中清理 `*` stop marker。

2. **基因ID列表**：`final/{prefix}.final_members.id`
3. **系统发育树**：`final/{prefix}.tree.nwk`

### 2.2 可选输入

- **亚家族文件**：自动查找仅使用 `confirmed_subfamily.tsv`；`reference_classification.subfamily.tsv` / `geneID.subfamily` 属于 target prediction，只有通过 `--subfamily` 显式指定时才可用于查看预测图。


> `analysisRoute=subfamily_evidence` 用于亚家族分类证据生成，默认不消费目标物种的 prediction 或 confirmed 标签；后续正式 motif 图必须基于 `final_members.pep.fa` + `confirmed_subfamily.tsv` 重绘。

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（六件套）

| 文件 | 说明 |
|------|------|
| `{prefix}.motif.pdf` | 树+Motif 组合图（PDF） |
| `{prefix}.motif.png` | 树+Motif 组合图（PNG） |
| `{prefix}.motif_seqlogo.pdf` | Motif 序列标志（PDF） |
| `{prefix}.motif_seqlogo.png` | Motif 序列标志（PNG） |
| `{prefix}.motif_report.md` | 分析报告 |
| `{prefix}.motif.manifest.json` | 机器清单 |

### 3.2 motif/analysis/ 中间文件

- `meme_out/` - MEME 完整输出目录
- `{prefix}_final.motif_prot.txt` - Motif 位置汇总（Perl脚本生成）

## 4. 执行流程（4个阶段）
[Stage 1] MEME 鉴定
Input:  {prefix}.final_members.pep.fa
Output: motif/analysis/meme_out/meme.xml, meme.txt
Params: -mod anr -protein -nmotifs 10 -minw 6 -maxw 200
[Stage 2] 信息汇总
Input:  meme_out/meme.txt
Output: motif/analysis/{prefix}_final.motif_prot.txt
Script: python scripts/get_meme_pos.py
[Stage 3] SeqLogo 绘图
Input:  {prefix}_final.motif_prot.txt
Output: motif/analysis/{prefix}_final_motif_seqlogo.pdf/png
Script: Rscript scripts/draw_motif_seqlogo.R
[Stage 4] 树+Motif 组合图
Input:  tree.nwk, subfamily, meme.xml
Output: final/{prefix}.motif.pdf/png
Script: Rscript scripts/plot_motif.R

## 5. Manifest Schema

### 5.1 必选 artifacts

- `motif_plot_pdf/png` - 树+Motif 组合图
- `motif_seqlogo_pdf/png` - SeqLogo 图
- `motif_report` - 分析报告
- `manifest` - 清单自身

### 5.2 可选 artifacts

- `meme_results` - MEME 输出目录
- `motif_prot_txt` - Motif 位置汇总文件

## 6. metrics / parameters

### 6.1 metrics

- `sequence_count` - 序列数量
- `motif_count` - Motif 数量
- `total_sites` - Motif 位点总数
- `avg_motif_width` - 平均 Motif 宽度
- `motifs` - Motif 详情列表

### 6.2 parameters

包含 MEME 参数（nmotifs, minw, maxw）、工具版本、各阶段状态等。
Scope 模式下还必须记录 `scope_id`、`scope_mode`、`scope_file`、`target_species`、`reference_species`、`comparative_species`、`selected_species`、实际 `protein_fasta` 及其 sha256。

## 7. 失败语义

支持 **partial_success**：任一阶段失败不影响其他阶段，最终状态根据各阶段完成情况确定。

### 阶段状态记录

```json
"stage_status": {
  "meme": true,
  "summary": true,
  "seqlogo": false,
  "tree_motif": true
}
