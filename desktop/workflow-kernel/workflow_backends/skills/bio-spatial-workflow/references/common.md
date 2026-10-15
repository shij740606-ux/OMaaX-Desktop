# 运行约定、缺失信息及验收

## 输入和运行目录
先检查用户提供的实际文件、元数据和已安装环境，再选择最晚可用入口。不得把已有标准化矩阵当原始 counts，也不能编造 donor、group、batch、基因映射、空间坐标或参考库版本。上游参考所需的物种/基因组/平台必须提供；下游步骤若无需该信息，不反复询问。

每次任务使用独立 RUN_ROOT，包含 config/、logs/、results/、acceptance.json、run_manifest.json。解包后端到新目录；复制后端 config 示例到 RUN_ROOT/config，按用户输入改副本。使用绝对路径减少歧义；包中 JSON 相对路径相对 JSON 文件，样本 CSV 的数据路径相对 CSV 文件。输出目录须为空或不存在。不要把数据和输出写进 Skill 或后端源码目录。后端参数不支持任意输入路径：必须核验每个实际文件存在、可读、格式和 ID 匹配。

run_manifest.json 记录 workflow、backend SHA256、入口、样本表、原始/有效参数、参数来源（user/default/inferred-from-file）、环境版本、参考库及 GMT 版本、每步实际 argv、日志和输出路径、起止时间、下载大小。参数文件生成后展示简要运行范围；用户已授权执行则继续，不再要求重复确认。

## 缺失信息询问规则
1. 一次集中询问当前步骤的阻塞项：实际输入与原始计数来源、输出/执行位置、必要元数据、所需参考/图几何/统计设计。说明每项用途。可从可信文件推断的字段记录来源；推断有歧义才问。
2. 用户未指定普通数值参数时使用参数表中的默认值并记录，不逐个追问。MT 规则、空间图几何、参考库、物种匹配 GMT、配对关系不能凭示例猜测。线粒体过滤未启用须在报告说明。
3. 只询问已启用分支需要的信息。缺注释可先完成聚类；缺 GMT 可完成差异；缺 GTDB 可完成组装/分箱。缺阻塞项时标记 needs_input，继续不依赖它的分支。
4. 用户明确设置的合法参数优先；说明异常边界后保留选择。负半径、非整数原始 counts、伪造重复、不满足脚本设计等不能通过调参绕过。统计要求不满足标记 skipped，不把技术重复伪装成 donor。
5. 超出后端公开参数的请求标记 unsupported，列出限制；不得偷偷改已验证包源码。新算法/模型扩展应另立版本，不能沿用本包已验证声明。

## 分支、环境和执行
读取本 Skill contract.md 和解包后 README.md；只有启用上游或高级统计时才读 backend WORKFLOW。FASTQ 上游需 Nextflow 和容器/对应 profile，Python/R 下游使用包的固定 requirements 和环境说明。MAG 5.5.0 需 Nextflow >=26.04.0，已验证26.04.7；其余上游已验证25.10.8。Squidpy 保持包锁定1.8.3，不能直接升级移除兼容API。R 环境加载不代表实际统计通过；metagenome 参考 R_ENVIRONMENT_VALIDATED.md。

执行上游需要 --execute；默认 dry-run 只验启动，不记 passed。续跑必须检查同一 work-dir 的进程/锁，确认无活跃任务后使用 --resume，避免重复启动。已有用户授权内继续，首次下载需明确大小与目的；大下载直接公开源，不使用个人代理，数据库单列预算。不修改共享 Docker/服务；空间不足先停止新任务并报资源需求，不清理无关材料。

## 验收状态与规则
acceptance.json 的 stages 是数组，每项记录 name、status、reason、argv、exit_code、expected_outputs、checks、evidence。status 限 passed / failed / skipped / needs_input / not_covered；运行中可另在 run_manifest 标记 running。overall 取 passed / completed_with_skips / failed / needs_input，未覆盖清单永远保留，不将 completed_with_skips 描述为全部分支通过。

- passed：实际计算结束，日志无未解释失败，预期文件存在且能读入，执行分支的 ID、维度、数值及科学前提核验通过。
- failed：命令错误、预期产物缺失/损坏或一致性错误；保留原输出和日志。不得只根据退出码0、启动成功、语法检查或页面显示判通过。
- skipped：本次明确不启用或真实数据未满足条件，写出原因（例如少于3独立 donor/组、单标本无组间统计、无足够标记基因）；实际算法报错仍保留 exit_code 和错误证据，不能抹去失败历史。
- needs_input：已请求必要资料，相关分支未执行。
- not_covered：未实际验收的算法、参考库、输入或设计；说明当前任务的范围。

Nextflow 逐项检查 trace.tsv；成功退出也可能含 FAILED/ignored。已确认的 MaxBin2 标记基因不足可归为数据不足项，保留任务失败记录；其他错误不能自动归此类。DESeq2逐项读 manifest.tsv。抽样数值核验须记录抽样方式，不能称全量一致。

报告区分计算成功、数据完整性、统计设计、解释准确性；不把聚类当细胞注释或组织真值，不把原始 bins 当高质量MAG。结束交付输入/参数/环境记录、结果索引、日志、acceptance.json与未覆盖项。诊断修复仅作用于外层配置/环境；后端代码问题保留证据另报，不在此Skill中直接改包。

可复制assets/run_manifest.template.json与assets/acceptance.template.json到任务目录填充；stage示例：`{"name":"analyze","status":"passed","reason":"实际产物检查通过","argv":[],"exit_code":0,"expected_outputs":[],"checks":{},"evidence":[]}`。模板null字段不能留在已验收记录中。
