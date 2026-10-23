# OMaaX 科研绘图第三方来源

## 生物矢量素材

来源：https://github.com/duerrsimon/bioicons

固定来源版本：d29e766ea7580b8063c4f47b29e872db40a4d979。

当前 148 个素材均来自该版本的 `static/icons/cc-0/`，保留首批 18 个素材的 ID，旧绘图仍可打开。每项的作者、来源 URL、原文件 SHA-256 和许可证标识记录在 `drawing-assets.json`；使用的来源信息也写入导出 SVG 的 metadata。下载时校验 Git blob 哈希，清理非绘图元素和外部资源引用。不能据此认定 Bioicons 其他素材也采用 CC0。

分类包括植物与藻类、动物与模式生物、细胞与组织、器官与生理、微生物与病毒、核酸、蛋白与膜、细胞器、实验器材和氨基酸。中文清单位于 `desktop/scripts/drawing-assets-extra.json`，刷新工具为 `desktop/scripts/fetch-drawing-assets.py`；软件运行时不需要下载素材。

CC0 条款：https://creativecommons.org/publicdomain/zero/1.0/

## 三维显示

3Dmol.js 2.5.5，https://github.com/3dmol/3Dmol.js ，BSD-3-Clause。完整声明见本目录 `3Dmol-BSD-3-Clause.txt`。引用：Rego N, Koes D. 3Dmol.js: molecular visualization with WebGL. Bioinformatics (2015), doi:10.1093/bioinformatics/btu829。

## 相互作用计算

ProLIF 2.1.0，https://github.com/chemosim-lab/ProLIF ，Apache-2.0。完整声明见 `ProLIF-Apache-2.0.txt`。运行环境按需安装，使用 RDKit 2025.9.6 处理化学结构、MDAnalysis 处理蛋白结构。环境内各依赖的 LICENSE 保留在其发行包中。

ProLIF 示例仅用于开发验收，未将示例输入纳入正式产品素材。计算输出保留引擎版本、输入 SHA-256 和原子索引。二维配体描绘使用独立副本，原三维坐标保持不变。
