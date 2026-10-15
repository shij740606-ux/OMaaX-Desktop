# RNA-Seq QC Stage Contract v1 (Quality Control)

## 1. Workspace 目录归属（必须遵守 core/workspace_contract_v1.md）

本阶段必须遵守 workspace_contract_v1.md 中定义的目录结构：
- 最终产物必须位于 `workspace/<job_id>/final/`
- 中间文件应位于 `workspace/<job_id>/rnaseq_qc/`
- 日志文件位于 `workspace/<job_id>/logs/`

## 2. 输入（Input Contract）

### 2.1 必需输入

- **原始FASTQ文件**：双端测序数据 (`*_1.fastq.gz`, `*_2.fastq.gz`)
  - 来源：用户上传
  - 验证：文件必须存在且为gz压缩的FASTQ格式
  - 支持多样本批量处理

- **样本配置文件**：`samples.yaml` 或 `samples.txt`
  - 格式：定义样本名、FASTQ、分组、链特异性和分组角色；DE 阶段会继续消费同一份 `samples.yaml`
  - 示例：
    ```yaml
    samples:
      S1: {r1: rawData/S1_1.fastq.gz, r2: rawData/S1_2.fastq.gz, condition: CK, replicate: 1, library_strandness: reverse, group_role: control, include: true}
      S2: {r1: rawData/S2_1.fastq.gz, r2: rawData/S2_2.fastq.gz, condition: salt, replicate: 1, library_strandness: reverse, group_role: compare, include: true}
    groups:
      S1: CK
      S2: salt
    compare: [["salt", "CK"]]
    ```

### 2.2 可选输入

- **QC 工具参数**：
  - 默认：`qc_tool=fastp`
  - 默认 fastp 可执行文件：`/data_old/home/xia-lab/wutx/02tool/fastp`
  - 可选兼容模式：显式设置 `qc_tool=cutadapt`
  - fastp 路径不存在、不可执行或运行失败时必须失败并报告原因；不得静默回退到 cutadapt

## 3. 输出（Output Contract）

### 3.1 final/ 必须产出（必须存在）

1. **`{prefix}.rnaseq_qc_report.md`**
   - 类型：分析报告
   - 格式：Markdown
   - 内容：质控摘要、样本统计、工具版本

2. **`{prefix}.rnaseq_qc.manifest.json`**
   - 类型：机器清单
   - 格式：JSON（严格符合 manifest_v1.schema.json）
   - 内容：完整的分析元数据

### 3.2 rnaseq_qc/ 推荐产出（可追溯中间态）

以下文件推荐位于 `workspace/<job_id>/rnaseq_qc/` 目录：

1. **fastp 默认模式产物**
   - `{sample}_fastp.html`
   - `{sample}_fastp.json`
   - `{sample}_fastp.log`

2. **`{sample}_1.clean.fastq.gz`**
   - 过滤后的read1

3. **`{sample}_2.clean.fastq.gz`**
   - 过滤后的read2

4. **cutadapt 兼容模式产物**
   - Cutadapt 去除接头日志
   - `{sample}_fastqc/` 或 FastQC HTML/ZIP 仅在 cutadapt 模式下作为质控报告产出

## 4. Artifacts Contract

### 4.1 必选 artifacts（必须存在）

Manifest 的 `artifacts` 字段必须是字典格式（不是列表），包含以下必需产物：

- **fastp_report_{sample}**：fastp HTML 报告（默认模式）
  - path: "final/{prefix}.rnaseq_qc.{sample}_fastp.html"
  - type: "html"

- **fastp_json_{sample}**：fastp JSON 报告（默认模式）
  - path: "final/{prefix}.rnaseq_qc.{sample}_fastp.json"
  - type: "json"

- **fastp_log_{sample}**：fastp 日志（默认模式）
  - path: "final/{prefix}.rnaseq_qc.{sample}_fastp.log"
  - type: "log"

- **clean_reads_r1**：过滤后的read1
  - artifact: "clean_reads_r1"
  - path: "rnaseq_qc/{sample}_1.clean.fastq.gz"
  - type: "fastq"

- **clean_reads_r2**：过滤后的read2
  - artifact: "clean_reads_r2"
  - path: "rnaseq_qc/{sample}_2.clean.fastq.gz"
  - type: "fastq"

- **cutadapt_log_{sample}**：Cutadapt日志（仅 cutadapt 模式）
  - artifact: "cutadapt_log"
  - path: "final/{prefix}.rnaseq_qc.{sample}_cutadapt.log"
  - type: "text"

- **report**：分析报告
  - artifact: "report"
  - path: "final/{prefix}.rnaseq_qc_report.md"
  - type: "markdown"

- **manifest**：清单文件自身
  - artifact: "manifest"
  - path: "final/{prefix}.rnaseq_qc.manifest.json"
  - type: "json"

### 4.2 可选 artifacts（条件触发）

- **adapter_file**：自定义接头序列（如果提供）

## 5. metrics / parameters 最小字段约定（v1）

### 5.1 metrics（至少包含）

- **total_raw_reads**：原始reads总数（整数）
- **total_clean_reads**：过滤后reads总数（整数）
- **reads_after_qc**：质控后reads数（整数）
- **adapter_removed**：去除的接头数（整数）
- **average_read_length**：平均读长（浮点数）
- **gc_content**：GC含量（浮点数）

### 5.2 parameters（至少包含）

- **cpu**：使用的 CPU 核心数（整数）
- **prefix**：分析前缀（字符串）
- **fastqc_params**：FastQC参数（字符串）
- **qc_tool**：QC 工具，默认 `"fastp"`，可显式为 `"cutadapt"`
- **fastp_bin**：fastp 可执行文件路径；默认 `/data_old/home/xia-lab/wutx/02tool/fastp`
- **fastp_params**：fastp 参数字典，至少记录 adapter 检测、剪切窗口、质量阈值、N 碱基限制和最短长度
- **cutadapt_params**：Cutadapt参数（字符串；仅 cutadapt 模式非空）
- **sample_parallel_jobs**：样本并行数（整数）
- **threads_per_sample**：单样本工具线程数（整数；fastp `--thread` 必须使用该值）
- **stage**：阶段名称，固定为 "rnaseq_qc"（字符串）
- **job_id**：任务ID（字符串）
- **run_id**：运行ID（字符串）
- **manifest_version**：Manifest版本，固定为 "1.0"（字符串）
- **status**：状态，"success" 或 "failed"（字符串）
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

1. **输入验证失败**：FASTQ文件不存在或格式无效
2. **fastp失败**：默认 QC 工具返回非零状态码，或 HTML/JSON/log/clean FASTQ 缺失
3. **Cutadapt失败**：显式 cutadapt 模式下接头去除失败
4. **文件系统错误**：无法写入输出文件

### 6.2 失败处理

失败时必须生成 Manifest，包含：
- `"status": "failed"`
- `"metrics"`: 可为空或包含部分成功指标
- `"artifacts"`: 可包含已成功生成的部分产物
- 报告应说明失败原因和关键日志位置

### 6.3 部分成功

如果部分样本处理成功：
- 状态可设为 `"partial_success"`
- 包含已成功处理的样本结果
- 记录详细的失败原因

## 7. 示例（运行结果形态）

### 7.1 目录结构示例

```
workspace/20250101_123456_abcdef/
├── rnaseq_qc/
│   ├── S1_fastp.html
│   ├── S1_fastp.json
│   ├── S1_fastp.log
│   ├── S1_1.clean.fastq.gz
│   ├── S1_2.clean.fastq.gz
│   ├── S2_fastp.html
│   ├── S2_fastp.json
│   ├── S2_fastp.log
│   ├── S2_1.clean.fastq.gz
│   ├── S2_2.clean.fastq.gz
│   └── S2_cutadapt.log
├── final/
│   ├── rnaseq_qc_report.md
│   └── rnaseq_qc.manifest.json
└── logs/
    └── 20250101_123500_rnaseq_qc.log
```

### 7.2 关键验证点

1. **样本名精确匹配**：处理多个样本时确保一一对应
2. **Manifest 格式**：artifacts 必须是字典，不是列表
3. **双端文件配对**：read1和read2必须匹配
4. **工具版本记录**：必须记录 fastp 版本；cutadapt 模式下还必须记录 FastQC、Cutadapt版本
5. **必需字段完整**：Manifest 必须包含所有 schema 要求的字段

### 7.3 执行参数示例

默认执行参数：

- **fastp**: `fastp -i {input_r1} -I {input_r2} -o {out_r1} -O {out_r2} --html {sample}_fastp.html --json {sample}_fastp.json --thread {threads_per_sample}`
- **Cutadapt兼容模式**: `cutadapt -a AGATCGGAAGAGC -A AGATCGGAAGAGC -o {out_r1} -p {out_r2} {input_r1} {input_r2}`

用户可通过命令行参数覆盖默认参数。
