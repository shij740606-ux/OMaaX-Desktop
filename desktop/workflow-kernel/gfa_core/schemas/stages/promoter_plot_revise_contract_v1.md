# Promoter Plot Revise Contract v1

本契约定义 `promoter_plot_revise`（启动子自定义元件筛选重绘）阶段。

## 1. 定位

- 这是 `promoter` 阶段的衍生重绘工具阶段。
- 只读取已有 PlantCARE filtered 表、同 scope 树文件和亚家族文件。
- 不重新提取启动子序列，不重新提交 PlantCARE，不覆盖 `{prefix}.promoter.manifest.json`。

## 2. 输入

必须已有：

- `workspace/<job_id>/promoter/analysis/plantCARE_output_filtered.tab`
  或 `promoter` manifest 中的 `filtered_plantcare_output` artifact。
- `workspace/<job_id>/final/{prefix}.tree.nwk`
  或 `phylogeny` manifest 中的 tree artifact。

可选：

- `final/{prefix}.geneID.subfamily`
- `final/{prefix}.confirmed_subfamily.tsv`
- 用户显式传入的 subfamily 文件。

参数：

- `promoterElements`：逗号分隔 PlantCARE 元件名。
- `promoterCategories`：big7 类别，逗号分隔。
- `promoterMatchMode`：`exact | contains | regex`，默认 `exact`。
- `promoterPlotModes`：`position_distribution | heatmap | barplot`，默认 `position_distribution,heatmap`。
- `promoterTopN`：heatmap/barplot 展示的元件数，默认 30。
- `promoterOutputLabel`：输出标签，可选。

至少需要提供 `promoterElements` 或 `promoterCategories` 之一。位置分布图和柱状图的图例只应显示本次实际绘制的元件或类别，不应固定显示全量 big7 类别。

## 3. 筛选语义

- 元件列表内部为 OR。
- 类别列表内部为 OR。
- 同时提供元件和类别时，元件筛选与类别筛选取 AND。
- 先按树标签做基因 ID 标准化匹配，再按用户条件筛选。
- 无匹配记录时阶段失败，并产出诊断表；不得生成空图。

## 4. 输出

输出写入 `workspace/<job_id>/final/`：

- `{prefix}.promoter_custom_{label}_position_distribution.{png,pdf}`
- `{prefix}.promoter_custom_{label}_heatmap.{png,pdf}`
- `{prefix}.promoter_custom_{label}_barplot.{png,pdf}`
- `{prefix}.promoter_custom_{label}_matches.tsv`
- `{prefix}.promoter_custom_{label}_element_counts.tsv`
- `{prefix}.promoter_custom_{label}_metadata.tsv`
- `{prefix}.promoter_plot_revise.manifest.json`

如果用户未选择某种图类型，则对应图可不存在。

## 5. Manifest

`manifest.stage = "promoter_plot_revise"`。

必须记录：

- `source_stage = "promoter"`
- `display_stage = "promoter"`
- `parameters.selected_elements`
- `parameters.selected_categories`
- `parameters.match_mode`
- `parameters.plot_modes`
- `parameters.source_promoter_table`
- `parameters.source_tree`
- `metrics.matched_records`
- `metrics.matched_genes`
- `metrics.matched_elements`

结果展示应归入 promoter 结果区域，同时保留独立 `promoter_plot_revise` stage 以便任务状态追踪。
