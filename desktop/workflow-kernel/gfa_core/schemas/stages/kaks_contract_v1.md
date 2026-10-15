# Ka/Ks Stage Contract v1.0

## 阶段信息

- **阶段名称**: kaks
- **阶段描述**: 对 target 基因家族成员与 reference 同源基因对计算 Ka/Ks、Ks 和分化时间
- **脚本**: `apps/cli/run_kaks_v2.py`

## 输入契约

### Scope 模式

| 字段 | 类型 | 描述 |
|------|------|------|
| scope_file | json | target/reference/comparative scope JSON |
| ortholog mapping | table | Ortholog 阶段输出 `{prefix}.orthologs.txt` / `{prefix}.ortholog_pairs.txt` |
| target CDS | fasta | target 物种 CDS，如 `{Species}.cds.fasta` 或 scope 中 `cds` |
| target PEP | fasta | target 物种家族蛋白，优先 `identify/{Species}/final/{Species}.final_gfmembers.pep.fa` |
| reference CDS | fasta | reference 物种全 CDS 或家族 CDS |
| reference PEP | fasta | reference 家族蛋白，优先 `{Reference}.{Family}.pep.fa` |

规则：

- 多 target scope 会按 target 物种拆分运行：`{prefix}.{Target}_to_{Reference}`。
- 只使用第一个 reference 物种作为 Ka/Ks 的参考侧。
- Ortholog 映射必须包含或可推断 `Source_Species/Target_Species`，避免把 Lc/Dl 混到同一套 `cds1/pep1`。
- 若 reference CDS 在 config/scope 中缺失，可从已知 reference 数据目录解析，例如 At 的 TAIR10 CDS。
- KaKs 是 reference dependency owner：若缺少 ortholog mapping，可以要求调度器先自动插入 Ortholog；但若 reference CDS/PEP 不完整，必须先提示上传/下载完整参考注释包，不能只跑 Ortholog 后再失败。
- 若 PPI 与 KaKs 同批执行，reference resolver 必须按阶段分别检查；KaKs 使用 `requested_stages=["kaks"]` 检查 reference PEP/CDS 与 Ortholog，而其缺失状态不得覆盖 PPI 的独立就绪状态。
- 普通计划不显示隐式 Ortholog，KaKs 也不声明对 Ortholog 或 PPI 的静态硬依赖。执行层仅用软顺序保证 KaKs 参数确认发生在 PPI 真实终态之后。
- KaKs 确认必须显式选择参考来源：复用本次 PPI 技术参考，或使用项目配置参考。选择只写入 KaKs 参数，不得修改 PPI owner 或其它阶段的参考 scope。
- 复用 PPI 参考时，只有 identity 完全匹配的 `final/{prefix}.ppi.orthologs.txt` 可用，并以 `orthologMappingOwner=ppi` 提交。
- 选择项目参考时，缺少精确映射则运行 `dependency_owner=kaks` 的内部 Ortholog，输出 `final/{prefix}.kaks.orthologs.txt`，随后以 `orthologMappingOwner=kaks` 提交。
- 两种 owner 都不得回落到另一 owner 或 standalone 映射。缺少所选参考的 PEP/CDS/映射只暂停 KaKs，不影响已完成 PPI 或其它阶段。

### Legacy 模式

沿用旧输入：

- `cds1.fasta`
- `cds2.fasta`
- `pep1.fasta`
- `pep2.fasta`
- `ortholog_pairs.txt`

## 输出契约

Scope 模式：

| 文件 | 类型 | 描述 |
|------|------|------|
| `{prefix}.{Target}_to_{Reference}.kaks_results.csv` | csv | 单 target-reference Ka/Ks 结果 |
| `{prefix}.{Target}_to_{Reference}.divergence_time.txt` | txt | 分化时间 |
| `{prefix}.{Target}_to_{Reference}.kaks.manifest.json` | json | 单次运行 manifest |
| `{prefix}.kaks.manifest.json` | json | 聚合 manifest |
| `{prefix}.{scope_id}.kaks.manifest.json` | json | scoped manifest 副本 |

## 失败语义

- 缺少 Ortholog 映射：失败，提示先运行 Ortholog。
- 某 target 缺少 CDS/PEP：失败并指出具体物种和文件类型。
- reference 缺少 CDS/PEP：失败并指出需要完整 reference annotation bundle；不得自动换用 At 或复用旧 reference mapping。
- 某 target 没有 target→reference pairs：跳过；所有 target 都无 pairs 时失败。
- `run_kaks_v2.py` 对某 target 返回非零：失败并返回日志路径。
