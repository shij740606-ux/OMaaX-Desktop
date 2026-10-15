<!-- GFA_SECTION common.front -->
# {{PREFIX}} 基因家族分析结果解读报告

- 任务：{{JOB_ID}}
- 分析前缀：{{PREFIX}}
- 生成时间：{{GENERATED_AT}}


## 一、报告首页

### 1.1 项目信息

本报告只覆盖用户计划中的成功阶段；不同阶段的目标集合在各节单独标明。

### 1.2 研究目的

本报告用于确认家族成员，比较目标成员及必要参考成员的关系、结构、调控序列与蛋白预测特征，并界定结果能够支持的结论范围。

### 1.3 一页式结论摘要

{{SECTION_CONTENT}}

### 1.4 结论可信度说明

成员数和命名表中的统计可直接复核；树、启动子、TFBS、共线性和蛋白定位只提供计算线索，不等同于功能或因果证据。

<!-- GFA_SECTION common.route -->
## 二、分析路线与阅读顺序

{{SECTION_CONTENT}}

### 2.1 统一解读顺序

各阶段依次写明分析对象、源结果中的直接观察、谨慎解释、限制，并仅在有助复核时列出少量相对路径。

<!-- GFA_SECTION common.completeness -->
## 三、结果总览与完整性检查

### 3.1 阶段完成情况

{{SECTION_CONTENT}}

### 3.2 结果完整性概述

报告按计划逐阶段选择成功 Manifest；当各阶段使用的成员集合不同，正文明确区分目标成员、目标加参考成员和全基因组背景。

<!-- GFA_SECTION stage.identify -->
## 四、基因家族鉴定（Identify）

### 4.3 最终成员数量与命名

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.phylogeny -->
## 五、亲缘关系分析（Phylogeny）

### 5.5 亲缘关系阶段小结

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.reference_classification -->
## 六、参考物种辅助的小组分类（Reference Classification）

### 6.2 分类可信度表述

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.motif -->
## 七、短保守片段分析（Motif）

### 7.2 Motif 数量与出现频率

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.domain -->
## 八、主要功能区域分析（Domain）

### 8.1 Domain 整体统计

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.structure -->
## 九、基因内部结构分析（Structure）

### 9.1 结构覆盖与异常结构

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.combo -->
## 十、综合组合图（Combo）

### 10.1 树 + Motif + Domain + Structure 组合图

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.promoter -->
## 十一、启动子元件分析（Promoter）

### 11.1 元件总量、类别与高级统计

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.tfbs -->
## 十二、转录因子结合位点预测（TFBS）

### 12.1 高频结合片段与统计边界

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.synteny -->
## 十三、种内共线性分析（Synteny）

### 13.1 全基因组共线性背景与家族证据缺口

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.karyotype -->
## 十四、染色体分布（Karyotype）

### 14.1 染色体位置概述

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.interspecies_synteny -->
## 十五、种间共线性与跨物种关系（Interspecies Synteny）

### 15.6 Interspecies Synteny 阶段小结

{{SECTION_CONTENT}}

<!-- GFA_SECTION stage.pep -->
## 十六、蛋白基本特征分析（PEP）

### 16.1 蛋白理化性质分布图

逐蛋白理化性质保留在命名结果表中；源 Manifest 没有汇总长度、分子量或等电点范围时，正文不从图片反推数值。

### 16.4 亚细胞定位与阶段小结

{{SECTION_CONTENT}}

<!-- GFA_SECTION common.integrated -->
## 十七、多证据综合分析

### 17.5 综合结论

{{SECTION_CONTENT}}
