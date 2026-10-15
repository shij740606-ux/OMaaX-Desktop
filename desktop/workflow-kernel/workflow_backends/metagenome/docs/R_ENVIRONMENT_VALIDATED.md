# 已实际验证的 R 环境

Linux x86_64 / R 4.4.3，ANCOMBC 2.8.0；以真实 34 个独立 16S 计数样本实际运行 ANCOM-BC2 和 vegan 通过。此验收属于统计模块，不是 shotgun 组间完整流程。

`config/r_conda_explicit_linux64.txt` 保存本次 conda 精确依赖。它只适用于对应平台，并不包含源码重编译覆盖：CVXR 1.0.15，rbiom 1.0.3；rbiom 与 lme4 1.1-35.5 必须与安装环境的 RcppParallel/Matrix ABI 匹配。本次预编译二进制不匹配，使用以下官方源码重编译后统计运行通过。

在专用环境中下载并核对源码，再执行（将 PREFIX 改为实际环境绝对路径）：

```bash
export PATH="$PREFIX/bin:$PATH"
export CONDA_PREFIX="$PREFIX"
curl --noproxy '*' -fL https://cran.r-project.org/src/contrib/Archive/rbiom/rbiom_1.0.3.tar.gz -o rbiom_1.0.3.tar.gz
curl --noproxy '*' -fL https://cran.r-project.org/src/contrib/Archive/lme4/lme4_1.1-35.5.tar.gz -o lme4_1.1-35.5.tar.gz
R CMD INSTALL --library="$PREFIX/lib/R/library" rbiom_1.0.3.tar.gz
R CMD INSTALL --library="$PREFIX/lib/R/library" lme4_1.1-35.5.tar.gz
Rscript -e 'library(ANCOMBC); library(mia); library(lme4); stopifnot("unifrac" %in% getNamespaceExports("rbiom")); sessionInfo()'
```

需要该环境编译器和头文件；加载成功仍不等于统计分析通过，实际数据运行后核对模型、样本独立性及结果。源码 SHA256 和构建日志保存在本轮最终验证证据 `r_source_rebuild_results.json` 中。修改已有环境前先备份；建议专用环境避免影响其他项目。
