#!/usr/bin/env python3
"""API-only VCF/resequencing extension-stage benchmark runner.

The fixture is deliberately bounded: 01-05 use lightweight historical evidence
and small representative inputs; 06-10 use validated GVCF/VCF artifacts from the
local partial dataset. Full FASTQ-to-clean-VCF recomputation is not repeated for
every API case.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SOURCE_ROOT = Path("/home/xia-lab/wutx_old/wutx/plantAnalysisAgent/vcf_code/vcf")
DEFAULT_0710_ROOT = Path("/tmp/vcf_07_10_extracted_test")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "VCF拓展分析阶段_API20_PartialData_20260623"


@dataclass(frozen=True)
class ApiCase:
    dir_name: str
    case_id: str
    archetype_id: str
    script_name: str
    heavy: bool = False


CASES: tuple[ApiCase, ...] = (
    ApiCase("01_vcf_fastp_clean", "vcf_fastp_clean", "vcf_fastp_clean", "run_vcf_fastp_clean.sh", True),
    ApiCase("02_vcf_reference_index", "vcf_reference_index", "vcf_reference_index", "run_vcf_reference_index.sh", True),
    ApiCase("03_vcf_bwa_align_sort", "vcf_bwa_align_sort", "vcf_bwa_align_sort", "run_vcf_bwa_align_sort.sh", True),
    ApiCase("04_vcf_markduplicates", "vcf_markduplicates", "vcf_markduplicates", "run_vcf_markduplicates.sh", True),
    ApiCase("05_vcf_bam_stats", "vcf_bam_stats", "vcf_bam_stats", "run_vcf_bam_stats.sh"),
    ApiCase("06_vcf_haplotypecaller_gvcf", "vcf_haplotypecaller_gvcf", "vcf_haplotypecaller_gvcf", "run_vcf_haplotypecaller_gvcf.sh", True),
    ApiCase("07_vcf_combine_gvcf", "vcf_combine_gvcf", "vcf_combine_gvcf", "run_vcf_combine_gvcf.sh"),
    ApiCase("08_vcf_genotype_gvcf", "vcf_genotype_gvcf", "vcf_genotype_gvcf", "run_vcf_genotype_gvcf.sh"),
    ApiCase("09_vcf_select_variants", "vcf_select_variants", "vcf_select_variants", "run_vcf_select_variants.sh"),
    ApiCase("10_vcf_hard_filter_clean", "vcf_hard_filter_clean", "vcf_hard_filter_clean", "run_vcf_hard_filter_clean.sh"),
)


CASE_OUTPUTS: dict[str, tuple[str, ...]] = {
    "vcf_fastp_clean": ("12.clean.R1.fastq", "12.clean.R2.fastq", "vcf_metrics.json"),
    "vcf_reference_index": ("reference.fai.preview", "reference_index_metrics.json"),
    "vcf_bwa_align_sort": ("align_flagstat.txt", "bwa_align_metrics.json"),
    "vcf_markduplicates": ("markduplicates.metrics.txt", "markduplicates_metrics.json"),
    "vcf_bam_stats": ("flagstat_summary.txt", "depth_summary.txt", "bam_stats_metrics.json"),
    "vcf_haplotypecaller_gvcf": ("12.g.vcf.gz", "12.g.vcf.gz.tbi", "haplotypecaller_metrics.json"),
    "vcf_combine_gvcf": ("cohort.g.vcf.gz", "combine_gvcf_metrics.json"),
    "vcf_genotype_gvcf": ("cohort.raw.vcf.gz", "genotype_gvcf_metrics.json"),
    "vcf_select_variants": ("cohort.snp.vcf.gz", "cohort.indel.vcf.gz", "select_variants_metrics.json"),
    "vcf_hard_filter_clean": ("clean.vcf.gz", "clean.vcf.gz.tbi", "hard_filter_metrics.json"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--vcf-0710-root", type=Path, default=DEFAULT_0710_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--runtime-env", default="ASAW")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--start-run", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=2, help="Maximum concurrent API case tasks.")
    parser.add_argument("--heavy-jobs", type=int, default=1, help="Maximum concurrent heavy-designated case tasks.")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--retry-failed", action="store_true", help="Retry existing failed result_row.json records and archive their previous run directory.")
    parser.add_argument("--init-fixture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    cases = selected_cases(args.case_ids)
    if args.init_fixture:
        init_fixture(args.source_root, args.vcf_0710_root, args.output_root, cases)
    if args.dry_run:
        dry_run(args.output_root, cases)
    if args.run:
        run_api20(args.output_root, cases, runs=args.runs, runtime_env=args.runtime_env, start_run=args.start_run, jobs=args.jobs, heavy_jobs=args.heavy_jobs, retry_failed=args.retry_failed)
    if args.report:
        write_report(args.output_root)
    if not any((args.init_fixture, args.dry_run, args.run, args.report)):
        parser.print_help()
    return 0


def init_fixture(source_root: Path, vcf_0710_root: Path, output_root: Path, cases: tuple[ApiCase, ...]) -> None:
    fixture_root = output_root / "fixtures" / "packages"
    rows: list[dict[str, Any]] = []
    for case in cases:
        root = fixture_root / case.dir_name
        raw_code = root / "raw_code"
        raw_data = root / "raw_test_data"
        expected = root / "expected_outputs"
        if root.exists():
            shutil.rmtree(root)
        raw_code.mkdir(parents=True)
        raw_data.mkdir(parents=True)
        expected.mkdir(parents=True)
        data_status = prepare_raw_data(case, source_root, vcf_0710_root, raw_data)
        script_path = raw_code / case.script_name
        script_path.write_text(stage_script(case), encoding="utf-8")
        script_path.chmod(0o755)
        run_expected_script(script_path, raw_data, expected)
        rows.append(
            {
                "case_id": case.case_id,
                "archetype_id": case.archetype_id,
                "heavy": case.heavy,
                "status": "ready",
                "fixture_validation_scope": data_status["validation_scope"],
                "raw_data_sources": data_status["sources"],
                "raw_code_files": inventory(raw_code),
                "raw_test_data_files": inventory(raw_data),
                "expected_output_files": inventory(expected),
            }
        )
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(
        output_root / "fixture_manifest.json",
        {
            "schema_version": "vcf_api20_fixture_manifest_v1",
            "created_at": now(),
            "source_root": str(source_root),
            "vcf_0710_root": str(vcf_0710_root),
            "fixture_root": str(fixture_root),
            "benchmark_policy": "API conversion/repair only; Codex prepares bounded fixtures and collects metrics.",
            "parallelism_policy": {"default_jobs": 2, "default_heavy_jobs": 1, "recommended_max_jobs": 4},
            "cases": rows,
        },
    )
    (output_root / "README.md").write_text(
        "\n".join(
            [
                "# VCF API20 Benchmark",
                "",
                "This directory records the VCF/resequencing 01-10 extension-stage API benchmark.",
                "",
                "- 20 API attempts per stage, 10 stages, 200 planned cases.",
                "- Use `--jobs 2 --heavy-jobs 1` by default to avoid saturating the workstation.",
                "- 01-05 fixtures are lightweight historical-evidence fixtures.",
                "- 06-10 fixtures use the partial dataset GVCF/VCF artifacts validated locally.",
                "- Full production FASTQ-to-clean-VCF recomputation is not claimed by this API20 fixture.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def prepare_raw_data(case: ApiCase, source_root: Path, vcf_0710_root: Path, raw_data: Path) -> dict[str, Any]:
    copied: list[str] = []

    def cp(src: Path, name: str | None = None) -> None:
        if not src.exists():
            raise FileNotFoundError(f"missing fixture source for {case.case_id}: {src}")
        dst = raw_data / (name or src.name)
        shutil.copy2(src, dst)
        copied.append(str(src))

    def subset_fastq(src: Path, name: str, lines: int = 400) -> None:
        if not src.exists():
            raise FileNotFoundError(f"missing fixture source for {case.case_id}: {src}")
        with src.open("rt", encoding="utf-8", errors="replace") as inp, (raw_data / name).open("wt", encoding="utf-8") as out:
            for idx, line in enumerate(inp):
                if idx >= lines:
                    break
                out.write(line)
        copied.append(f"{src} first_{lines}_lines")

    if case.case_id == "vcf_fastp_clean":
        subset_fastq(source_root / "data" / "12.R1.fastq", "12.R1.fastq")
        subset_fastq(source_root / "data" / "12.R2.fastq", "12.R2.fastq")
        subset_fastq(source_root / "01.clean" / "12.clean.R1.fastq", "12.clean.R1.fastq")
        subset_fastq(source_root / "01.clean" / "12.clean.R2.fastq", "12.clean.R2.fastq")
        cp(source_root / "01.clean" / "12.json")
        scope = "small_fastq_subset_plus_historical_fastp_json"
    elif case.case_id == "vcf_reference_index":
        cp(source_root / "02.Reference_Genome_Indexing" / "IRGSP.rice.chr.fasta.fai", "reference.fai")
        cp(source_root / "02.Reference_Genome_Indexing" / "IRGSP.rice.chr.dict", "reference.dict")
        scope = "historical_reference_index_manifest"
    elif case.case_id == "vcf_bwa_align_sort":
        cp(source_root / "05.stats" / "flagstat" / "12.flagstat.txt", "align_flagstat.txt")
        cp(source_root / "03.bam" / "logs" / "12.bwa.log", "12.bwa.log")
        scope = "historical_alignment_summary"
    elif case.case_id == "vcf_markduplicates":
        cp(source_root / "04.markdup" / "metrics" / "12.markdup.metrics.txt", "markduplicates.metrics.txt")
        cp(source_root / "04.markdup" / "logs" / "12.markdup.log", "12.markdup.log")
        scope = "historical_markduplicates_summary"
    elif case.case_id == "vcf_bam_stats":
        cp(source_root / "05.stats" / "flagstat" / "flagstat_summary.txt")
        cp(source_root / "05.stats" / "depth" / "depth_summary.txt")
        cp(source_root / "05.stats" / "flagstat" / "12.flagstat.txt")
        scope = "historical_bam_stats_summary"
    elif case.case_id == "vcf_haplotypecaller_gvcf":
        cp(source_root / "06.gvcf" / "12.g.vcf.gz")
        cp(source_root / "06.gvcf" / "12.g.vcf.gz.tbi")
        scope = "partial_dataset_gvcf_artifact"
    elif case.case_id == "vcf_combine_gvcf":
        cp(vcf_0710_root / "07.combined" / "all.g.vcf.gz", "cohort.g.vcf.gz")
        cp(vcf_0710_root / "07.combined" / "all.g.vcf.gz.tbi", "cohort.g.vcf.gz.tbi")
        scope = "validated_07_combinegvcfs_artifact"
    elif case.case_id == "vcf_genotype_gvcf":
        cp(vcf_0710_root / "08.genotyped" / "genotyped.vcf.gz", "cohort.raw.vcf.gz")
        cp(vcf_0710_root / "08.genotyped" / "genotyped.vcf.gz.tbi", "cohort.raw.vcf.gz.tbi")
        cp(vcf_0710_root / "08.genotyped" / "genotype_gvcf_metrics.json")
        scope = "validated_08_genotypegvcfs_artifact"
    elif case.case_id == "vcf_select_variants":
        cp(vcf_0710_root / "09.selected" / "variants.snp.vcf.gz", "cohort.snp.vcf.gz")
        cp(vcf_0710_root / "09.selected" / "variants.snp.vcf.gz.tbi", "cohort.snp.vcf.gz.tbi")
        cp(vcf_0710_root / "09.selected" / "variants.indel.vcf.gz", "cohort.indel.vcf.gz")
        cp(vcf_0710_root / "09.selected" / "variants.indel.vcf.gz.tbi", "cohort.indel.vcf.gz.tbi")
        cp(vcf_0710_root / "09.selected" / "variant_selection_metrics.json")
        scope = "validated_09_selectvariants_artifact"
    elif case.case_id == "vcf_hard_filter_clean":
        cp(vcf_0710_root / "10.clean" / "clean.vcf.gz")
        cp(vcf_0710_root / "10.clean" / "clean.vcf.gz.tbi")
        cp(vcf_0710_root / "10.clean" / "hard_filter_metrics.json")
        scope = "validated_10_hard_filter_clean_artifact"
    else:
        raise ValueError(case.case_id)
    return {"validation_scope": scope, "sources": copied}


def stage_script(case: ApiCase) -> str:
    outputs = list(CASE_OUTPUTS[case.case_id])
    return f"""#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
python3 - "$SCRIPT_DIR" "$@" <<'PY'
import gzip
import json
import os
import shutil
import sys
from pathlib import Path

case_id = {case.case_id!r}
archetype_id = {case.archetype_id!r}
expected = {outputs!r}

script_dir = Path(sys.argv[1]).resolve()
input_dir = Path(os.environ.get("INPUT_DIR") or (script_dir / "raw_test_data"))
output_dir = Path(os.environ.get("OUTPUT_DIR") or "outputs")
args = sys.argv[2:]
positionals = []
idx = 0
while idx < len(args):
    arg = args[idx]
    if arg == "--input-dir" and idx + 1 < len(args):
        input_dir = Path(args[idx + 1])
        idx += 2
    elif arg == "--output-dir" and idx + 1 < len(args):
        output_dir = Path(args[idx + 1])
        idx += 2
    elif arg == "--input-file" and idx + 1 < len(args):
        idx += 2
    else:
        positionals.append(arg)
        idx += 1
if positionals:
    input_dir = Path(positionals[0])
if len(positionals) > 1:
    output_dir = Path(positionals[1])
input_dir = input_dir.resolve()
output_dir = output_dir.resolve()
if not input_dir.exists() and (script_dir / "raw_test_data").exists():
    input_dir = (script_dir / "raw_test_data").resolve()
output_dir.mkdir(parents=True, exist_ok=True)

def copy_required(src_name, dst_name=None):
    src = input_dir / src_name
    if not src.exists():
        raise SystemExit(f"missing required input {{src}}")
    dst = output_dir / (dst_name or src_name)
    shutil.copy2(src, dst)
    return dst

def count_fastq(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", errors="replace") as handle:
        return sum(1 for _ in handle) // 4

def count_text_lines(path):
    with open(path, "rt", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())

def count_vcf_records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", errors="replace") as handle:
        return sum(1 for line in handle if line and not line.startswith("#"))

metrics = {{"schema_version": "vcf_api_fixture_metrics_v1", "case_id": case_id, "archetype_id": archetype_id}}

if case_id == "vcf_fastp_clean":
    for name in ("12.clean.R1.fastq", "12.clean.R2.fastq"):
        copy_required(name)
    metrics.update({{
        "raw_r1_records": count_fastq(input_dir / "12.R1.fastq"),
        "raw_r2_records": count_fastq(input_dir / "12.R2.fastq"),
        "clean_r1_records": count_fastq(input_dir / "12.clean.R1.fastq"),
        "clean_r2_records": count_fastq(input_dir / "12.clean.R2.fastq"),
    }})
    if (input_dir / "12.json").exists():
        metrics["fastp_json_present"] = True
elif case_id == "vcf_reference_index":
    fai = copy_required("reference.fai", "reference.fai.preview")
    copy_required("reference.dict", "reference.dict.preview")
    metrics.update({{"fai_nonempty_lines": count_text_lines(fai), "dict_present": True}})
elif case_id == "vcf_bwa_align_sort":
    copy_required("align_flagstat.txt")
    metrics.update({{"flagstat_lines": count_text_lines(input_dir / "align_flagstat.txt"), "bwa_log_present": (input_dir / "12.bwa.log").exists()}})
elif case_id == "vcf_markduplicates":
    copy_required("markduplicates.metrics.txt")
    metrics.update({{"metrics_lines": count_text_lines(input_dir / "markduplicates.metrics.txt"), "markdup_log_present": (input_dir / "12.markdup.log").exists()}})
elif case_id == "vcf_bam_stats":
    copy_required("flagstat_summary.txt")
    copy_required("depth_summary.txt")
    metrics.update({{"flagstat_summary_lines": count_text_lines(input_dir / "flagstat_summary.txt"), "depth_summary_lines": count_text_lines(input_dir / "depth_summary.txt")}})
elif case_id == "vcf_haplotypecaller_gvcf":
    copy_required("12.g.vcf.gz")
    copy_required("12.g.vcf.gz.tbi")
    metrics.update({{"gvcf_records": count_vcf_records(input_dir / "12.g.vcf.gz"), "gvcf_index_present": True}})
elif case_id == "vcf_combine_gvcf":
    copy_required("cohort.g.vcf.gz")
    if (input_dir / "cohort.g.vcf.gz.tbi").exists():
        copy_required("cohort.g.vcf.gz.tbi")
    metrics.update({{"combined_gvcf_records": count_vcf_records(input_dir / "cohort.g.vcf.gz")}})
elif case_id == "vcf_genotype_gvcf":
    copy_required("cohort.raw.vcf.gz")
    if (input_dir / "cohort.raw.vcf.gz.tbi").exists():
        copy_required("cohort.raw.vcf.gz.tbi")
    metrics.update({{"raw_vcf_records": count_vcf_records(input_dir / "cohort.raw.vcf.gz")}})
elif case_id == "vcf_select_variants":
    copy_required("cohort.snp.vcf.gz")
    copy_required("cohort.indel.vcf.gz")
    for name in ("cohort.snp.vcf.gz.tbi", "cohort.indel.vcf.gz.tbi"):
        if (input_dir / name).exists():
            copy_required(name)
    metrics.update({{"snp_records": count_vcf_records(input_dir / "cohort.snp.vcf.gz"), "indel_records": count_vcf_records(input_dir / "cohort.indel.vcf.gz")}})
elif case_id == "vcf_hard_filter_clean":
    copy_required("clean.vcf.gz")
    copy_required("clean.vcf.gz.tbi")
    metrics.update({{"clean_vcf_records": count_vcf_records(input_dir / "clean.vcf.gz"), "clean_vcf_index_present": True}})
else:
    raise SystemExit(f"unsupported case {{case_id}}")

metric_name = next((name for name in expected if name.endswith("_metrics.json") or name == "vcf_metrics.json"), "vcf_metrics.json")
with open(output_dir / metric_name, "w", encoding="utf-8") as handle:
    json.dump(metrics, handle, indent=2, ensure_ascii=False)
    handle.write("\\n")
for name in expected:
    if not (output_dir / name).exists():
        raise SystemExit(f"expected output was not produced: {{name}}")
PY
"""


def run_expected_script(script_path: Path, raw_data: Path, expected: Path) -> None:
    subprocess.run(["bash", str(script_path), str(raw_data), str(expected)], check=True, cwd=str(script_path.parent))


def dry_run(output_root: Path, cases: tuple[ApiCase, ...]) -> None:
    from gfa_core.extension_stage.template_gate import evaluate_template_gate

    fixture_root = output_root / "fixtures" / "packages"
    rows = []
    for case in cases:
        root = fixture_root / case.dir_name
        expected = root / "expected_outputs"
        extension = {
            "extension_id": f"dry_run_{case.case_id}",
            "display_name": f"dry_run_{case.case_id}",
            "description": f"VCF API20 dry run for {case.case_id}",
            "archetype_id": case.archetype_id,
            "analysis_type": "generic_cli",
            "tool_family": "generic",
            "input_roles": {"input_file": {"role": "generic_input_file", "required": True}},
            "output_contract": output_contract_from_expected(expected),
            "golden_comparison": {"status": "passed"},
        }
        gate = evaluate_template_gate(extension)
        rows.append(
            {
                "case_id": case.case_id,
                "fixture_exists": root.exists(),
                "raw_code_exists": (root / "raw_code" / case.script_name).exists(),
                "raw_data_file_count": len(inventory(root / "raw_test_data")),
                "expected_output_count": len(inventory(expected)),
                "template_gate_status": gate.get("status", ""),
                "template_gate_errors": "; ".join(gate.get("errors") or []),
            }
        )
    write_json(output_root / "dry_run.json", {"schema_version": "vcf_api20_dry_run_v1", "created_at": now(), "rows": rows})
    write_csv(output_root / "dry_run.csv", rows)


def run_api20(output_root: Path, cases: tuple[ApiCase, ...], *, runs: int, runtime_env: str, start_run: int, jobs: int, heavy_jobs: int, retry_failed: bool = False) -> None:
    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    tasks: list[tuple[str, ApiCase]] = []
    for run_index in range(start_run, runs + 1):
        run_id = f"api_run_{run_index:03d}"
        for case in cases:
            result_row = output_root / "runs" / run_id / case.case_id / "result_row.json"
            if not result_row.exists() or (retry_failed and result_row.exists() and not bool(read_json(result_row).get("api_autonomous_success"))):
                tasks.append((run_id, case))
    if not tasks:
        write_summary(output_root, existing_result_rows(output_root))
        return
    jobs = max(1, min(int(jobs or 1), 8))
    heavy_jobs = max(1, min(int(heavy_jobs or 1), jobs))
    pending = list(tasks)
    running: dict[concurrent.futures.Future[dict[str, Any]], tuple[str, ApiCase]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        while pending or running:
            heavy_active = sum(1 for _, case in running.values() if case.heavy)
            submitted = True
            while pending and len(running) < jobs and submitted:
                submitted = False
                for idx, (run_id, case) in enumerate(pending):
                    if case.heavy and heavy_active >= heavy_jobs:
                        continue
                    pending.pop(idx)
                    running[pool.submit(run_one_case_worker, output_root, run_id, case, runtime_env, retry_failed)] = (run_id, case)
                    heavy_active += int(case.heavy)
                    submitted = True
                    break
            if not running:
                continue
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                run_id, case = running.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    run_case_root = output_root / "runs" / run_id / case.case_id
                    run_case_root.mkdir(parents=True, exist_ok=True)
                    row = finalize_row(base_row(run_id, case, runtime_env), time.time())
                    row["error"] = str(exc)
                    row["traceback"] = traceback.format_exc()
                    row["first_failure_phase"] = infer_failure_phase(row)
                    write_json(run_case_root / "result_row.json", row)
                write_partial_summary(output_root, existing_result_rows(output_root))
    write_summary(output_root, existing_result_rows(output_root))


def run_one_case_worker(output_root: Path, run_id: str, case: ApiCase, runtime_env: str, retry_failed: bool = False) -> dict[str, Any]:
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    run_case_root = output_root / "runs" / run_id / case.case_id
    existing_result = run_case_root / "result_row.json"
    if existing_result.exists():
        existing = read_json(existing_result)
        if not retry_failed or bool(existing.get("api_autonomous_success")):
            return existing
        archive_case_run(run_case_root)
    run_case_root.mkdir(parents=True, exist_ok=True)
    manager = ExtensionStageManager(
        PROJECT_ROOT,
        store_root=run_case_root / "extension_stages",
        workspace_root=run_case_root / "workspace",
        input_root=run_case_root / "input",
    )
    row = run_one_case(ExtensionStageAgent(manager), output_root, run_case_root, run_id, case, runtime_env)
    write_json(run_case_root / "result_row.json", row)
    return row


def run_one_case(agent: Any, output_root: Path, run_case_root: Path, run_id: str, case: ApiCase, runtime_env: str) -> dict[str, Any]:
    fixture_case = output_root / "fixtures" / "packages" / case.dir_name
    row = base_row(run_id, case, runtime_env)
    started = time.time()
    try:
        build = agent.create_from_uploads(
            display_name=f"vcf_api20_{case.case_id}_{run_id}",
            command=f"bash {case.script_name}",
            runtime_env=runtime_env,
            code_files=upload_files(fixture_case / "raw_code"),
            data_files=upload_files(fixture_case / "raw_test_data"),
            description=f"API-only VCF/resequencing benchmark case {case.case_id}",
        )
        write_json(run_case_root / "build_response.json", build)
        ext = build.get("extension") or {}
        eid = str(ext.get("extension_id") or "")
        row.update(
            {
                "extension_id": eid,
                "build_success": bool(build.get("success") and eid),
                "sanitize_success": bool(build.get("success") and eid),
                "spec_validate_success": bool(build.get("success") and eid),
                "wrapper_success": bool(build.get("success") and eid),
                "build_api_attempt_count": len(ext.get("build_attempts") or []),
                "sanitizer_action_count": len(ext.get("sanitizer_actions") or []),
            }
        )
        if not row["build_success"]:
            row["first_failure_phase"] = "build"
            return finalize_row(row, started)
        trial = agent.trial_run(eid)
        write_json(run_case_root / "trial_run.json", trial)
        if not trial.get("success"):
            repair = agent.repair_from_context(eid, phase="trial", run_context=trial.get("run") or {}, max_attempts=20, auto_publish=False)
            write_json(run_case_root / "trial_repair_response.json", repair)
            row["trial_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                trial = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
            else:
                row["first_failure_phase"] = "trial"
                row["last_error"] = str(repair.get("last_error") or "")
                return finalize_row(row, started)
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len((trial.get("run") or {}).get("outputs") or [])
        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(eid, outputs=outputs, nl_triggers=[case.case_id, case.archetype_id, "vcf", "resequencing"])
        write_json(run_case_root / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        if not row["confirm_success"]:
            row["first_failure_phase"] = "confirm"
            return finalize_row(row, started)
        val = agent.validate(eid)
        write_json(run_case_root / "validate_response.json", val)
        row.update(validation_status(val))
        if not val.get("success"):
            repair = agent.repair_from_context(eid, phase="validation", run_context=val.get("run") or {}, max_attempts=20, auto_publish=False)
            write_json(run_case_root / "validation_repair_response.json", repair)
            row["validation_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                val = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
                row.update(validation_status(val))
            else:
                row["first_failure_phase"] = "validation"
                row["last_error"] = str(repair.get("last_error") or "")
                return finalize_row(row, started)
        try:
            pub = agent.publish(eid)
            write_json(run_case_root / "publish_response.json", pub)
            row["publish_gate_status"] = "passed"
            row["publish_success"] = bool(pub.get("success"))
            row["final_publish_status"] = str((pub.get("extension") or {}).get("status") or "")
        except Exception as pub_exc:
            repair = agent.repair_from_context(eid, phase="publish_gate", run_context={"error": str(pub_exc), "last_validation": val.get("run") or {}}, max_attempts=20, auto_publish=True)
            write_json(run_case_root / "publish_repair_response.json", repair)
            row["publish_gate_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success") and repair.get("published"):
                row["publish_gate_status"] = "passed"
                row["publish_success"] = True
                row["final_publish_status"] = "published"
            else:
                row["publish_gate_status"] = "failed"
                row["first_failure_phase"] = "publish_gate"
                row["last_error"] = str(repair.get("last_error") or str(pub_exc))
                return finalize_row(row, started)
        if row["publish_success"]:
            job_id = f"{run_id}_{case.case_id}_published"
            materialize_job_inputs(agent, job_id, fixture_case / "raw_test_data")
            published = agent.run(extension_id=eid, job_id=job_id)
            write_json(run_case_root / "published_run_response.json", published)
            row["published_run_success"] = bool(published.get("success"))
            if not row["published_run_success"]:
                row["first_failure_phase"] = "published_run"
                row["last_error"] = str((published.get("run") or {}).get("error") or "")
    except Exception as exc:
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc()
        row["first_failure_phase"] = row.get("first_failure_phase") or infer_failure_phase(row)
        write_json(run_case_root / "error.json", {"error": str(exc), "traceback": row["traceback"], "row": row})
    return finalize_row(row, started)


def materialize_job_inputs(agent: Any, job_id: str, raw_data: Path) -> None:
    input_dir = agent.manager.input_root / job_id
    input_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(raw_data.iterdir()):
        if item.is_file():
            shutil.copy2(item, input_dir / item.name)


def validation_status(val: dict[str, Any]) -> dict[str, Any]:
    run = val.get("run") or {}
    golden = run.get("golden_comparison") or {}
    gate = run.get("template_gate") or {}
    return {
        "validate_success": bool(val.get("success")),
        "golden_status": str(golden.get("status") or ""),
        "template_gate_status": str(gate.get("status") or ""),
        "final_validation_status": str(run.get("status") or ""),
    }


def base_row(run_id: str, case: ApiCase, runtime_env: str) -> dict[str, Any]:
    return {
        "schema_version": "vcf_api20_case_result_v1",
        "created_at": now(),
        "run_id": run_id,
        "case_id": case.case_id,
        "archetype_id": case.archetype_id,
        "converter": "deepseek_api",
        "runtime_env": runtime_env,
        "heavy_case": case.heavy,
        "codex_manual_spec_edit": False,
        "build_success": False,
        "sanitize_success": False,
        "spec_validate_success": False,
        "wrapper_success": False,
        "trial_success": False,
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_gate_status": "",
        "publish_success": False,
        "published_run_success": False,
        "published_runnable": False,
        "template_eligible": False,
        "api_autonomous_success": False,
        "extension_id": "",
        "output_count": 0,
        "build_api_attempt_count": 0,
        "trial_repair_attempt_count": 0,
        "validation_repair_attempt_count": 0,
        "publish_gate_repair_attempt_count": 0,
        "total_api_attempt_count": 0,
        "sanitizer_action_count": 0,
        "first_failure_phase": "",
        "last_error": "",
        "error": "",
        "traceback": "",
        "wall_sec": 0,
    }


def finalize_row(row: dict[str, Any], started: float) -> dict[str, Any]:
    row["total_api_attempt_count"] = sum(int(row.get(key) or 0) for key in ("build_api_attempt_count", "trial_repair_attempt_count", "validation_repair_attempt_count", "publish_gate_repair_attempt_count"))
    row["published_runnable"] = all(
        [
            row.get("build_success"),
            row.get("sanitize_success"),
            row.get("spec_validate_success"),
            row.get("wrapper_success"),
            row.get("trial_success"),
            row.get("confirm_success"),
            row.get("validate_success"),
            row.get("golden_status") == "passed",
            row.get("publish_gate_status") == "passed",
            row.get("publish_success"),
            row.get("published_run_success"),
        ]
    )
    row["template_eligible"] = bool(row.get("published_runnable") and row.get("template_gate_status") == "passed")
    row["api_autonomous_success"] = bool(row.get("published_runnable") and not row.get("codex_manual_spec_edit"))
    if not row.get("first_failure_phase") and not row["api_autonomous_success"]:
        row["first_failure_phase"] = infer_failure_phase(row)
    row["wall_sec"] = round(time.time() - started, 3)
    return row


def infer_failure_phase(row: dict[str, Any]) -> str:
    for phase, key in [
        ("build", "build_success"),
        ("sanitize", "sanitize_success"),
        ("spec_validate", "spec_validate_success"),
        ("wrapper", "wrapper_success"),
        ("trial", "trial_success"),
        ("confirm", "confirm_success"),
        ("validate", "validate_success"),
        ("golden", "golden_status"),
        ("publish_gate", "publish_gate_status"),
        ("publish", "publish_success"),
        ("published_run", "published_run_success"),
    ]:
        value = row.get(key)
        if key.endswith("_status"):
            if value != "passed":
                return phase
        elif not value:
            return phase
    return ""


def output_contract_from_expected(expected: Path) -> list[dict[str, Any]]:
    return [{"name_pattern": path.name, "semantic_role": semantic_role(path.name), "required": path.name in required_names()} for path in sorted(expected.iterdir()) if path.is_file()]


def semantic_role(name: str) -> str:
    mapping = {
        "vcf_metrics.json": "vcf_metrics",
        "12.clean.R1.fastq": "vcf_clean_fastq_r1",
        "12.clean.R2.fastq": "vcf_clean_fastq_r2",
        "reference_index_metrics.json": "vcf_reference_index_metrics",
        "reference.fai.preview": "vcf_reference_fai_preview",
        "bwa_align_metrics.json": "vcf_bwa_align_metrics",
        "align_flagstat.txt": "vcf_align_flagstat",
        "markduplicates_metrics.json": "vcf_markduplicates_metrics",
        "markduplicates.metrics.txt": "vcf_markduplicates_metrics_table",
        "bam_stats_metrics.json": "vcf_bam_stats_metrics",
        "flagstat_summary.txt": "vcf_flagstat_summary",
        "depth_summary.txt": "vcf_depth_summary",
        "12.g.vcf.gz": "vcf_sample_gvcf",
        "12.g.vcf.gz.tbi": "vcf_sample_gvcf_index",
        "haplotypecaller_metrics.json": "vcf_haplotypecaller_metrics",
        "cohort.g.vcf.gz": "vcf_cohort_gvcf",
        "combine_gvcf_metrics.json": "vcf_combine_gvcf_metrics",
        "cohort.raw.vcf.gz": "vcf_raw_cohort_vcf",
        "genotype_gvcf_metrics.json": "vcf_genotype_gvcf_metrics",
        "cohort.snp.vcf.gz": "vcf_snp_vcf",
        "cohort.indel.vcf.gz": "vcf_indel_vcf",
        "select_variants_metrics.json": "vcf_select_variants_metrics",
        "clean.vcf.gz": "vcf_clean_snp_vcf",
        "clean.vcf.gz.tbi": "vcf_clean_snp_vcf_index",
        "hard_filter_metrics.json": "vcf_hard_filter_metrics",
    }
    return mapping.get(name, "vcf_auxiliary_output")


def required_names() -> set[str]:
    return {name for names in CASE_OUTPUTS.values() for name in names}


def selected_cases(case_ids: list[str] | None) -> tuple[ApiCase, ...]:
    if not case_ids:
        return CASES
    wanted = set(case_ids)
    return tuple(case for case in CASES if case.case_id in wanted or case.archetype_id in wanted)


def write_partial_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    write_json(output_root / "partial_summary.json", summarize(rows))
    write_csv(output_root / "partial_summary.csv", rows)


def write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    write_json(output_root / "summary.json", summarize(rows))
    write_csv(output_root / "summary.csv", rows)
    write_failure_matrix(output_root, rows)


def write_report(output_root: Path) -> None:
    rows = existing_result_rows(output_root)
    write_summary(output_root, rows)
    write_json(output_root / "publication_metrics.json", {"schema_version": "vcf_api20_publication_metrics_v1", "created_at": now(), "benchmark_type": "api_only", "summary": summarize(rows)})


def existing_result_rows(output_root: Path) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted((output_root / "runs").glob("api_run_*/*/result_row.json"))]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "vcf_api20_summary_v1",
        "created_at": now(),
        "benchmark_type": "api_only",
        "planned_cases_per_run": len(CASES),
        "case_result_count": len(rows),
        "api_autonomous_success": sum(1 for row in rows if row.get("api_autonomous_success")),
        "published_runnable": sum(1 for row in rows if row.get("published_runnable")),
        "template_eligible": sum(1 for row in rows if row.get("template_eligible")),
        "failed": sum(1 for row in rows if not row.get("api_autonomous_success")),
        "rows": rows,
    }


def write_failure_matrix(output_root: Path, rows: list[dict[str, Any]]) -> None:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row.get("case_id") or ""), str(row.get("first_failure_phase") or "none"))
        counts[key] = counts.get(key, 0) + 1
    write_csv(output_root / "failure_matrix.csv", [{"case_id": case_id, "first_failure_phase": phase, "count": count} for (case_id, phase), count in sorted(counts.items())])


def upload_files(root: Path) -> list[tuple[str, bytes]]:
    return [(path.name, path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file()]


def archive_case_run(run_case_root: Path) -> None:
    if not run_case_root.exists():
        return
    archive_root = run_case_root / "retry_archives" / now().replace(":", "").replace("-", "")
    archive_root.mkdir(parents=True, exist_ok=True)
    for item in sorted(run_case_root.iterdir()):
        if item.name == "retry_archives":
            continue
        shutil.move(str(item), str(archive_root / item.name))


def inventory(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [{"path": path.relative_to(root).as_posix(), "size": path.stat().st_size} for path in sorted(root.rglob("*")) if path.is_file()]


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})


if __name__ == "__main__":
    raise SystemExit(main())
