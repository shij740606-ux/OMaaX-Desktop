#!/usr/bin/env python3
"""GWAS extension-stage experiment scaffold.

This script prepares a small, auditable GWAS extension-stage case without
modifying the core extension-stage archetype registry. It is intentionally a
first-phase harness: environment audit, subset package preparation, raw
baseline execution, and report generation. API autonomous conversion runs are
kept as a later explicit step.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path("/home/xia-lab/wutx_old/wutx/plantAnalysisAgent/vcf_code/gwas")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "GWAS拓展分析阶段_API自主实验_20260621"
DEFAULT_ASAW_ENV = Path("/home/xia-lab/miniconda3/envs/ASAW")


@dataclass(frozen=True)
class GwasCase:
    case_id: str
    extension_archetype: str
    script_name: str
    required_outputs: tuple[str, ...]
    description: str

    @property
    def dir_name(self) -> str:
        return f"01_{self.extension_archetype}"


GWAS_TRAIT_VCF_QC = GwasCase(
    case_id="gwas_trait_vcf_qc_v1",
    extension_archetype="gwas_trait_vcf_qc",
    script_name="run_gwas_trait_vcf_qc.sh",
    required_outputs=("trait.clean.tsv", "sample_match_report.tsv", "qc_summary.json"),
    description="Clean a GWAS trait table, match trait IDs to VCF samples, and summarize VCF/trait QC.",
)


GWAS_QC_PY = r'''
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path


def open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return path.open("r", encoding="utf-8", errors="ignore")


def read_vcf_header_and_count(path: Path):
    samples = []
    variant_count = 0
    contigs = set()
    with open_text(path) as handle:
        for raw in handle:
            if raw.startswith("##"):
                continue
            if raw.startswith("#CHROM"):
                parts = raw.rstrip("\n").split("\t")
                samples = parts[9:]
                continue
            if raw.strip():
                variant_count += 1
                contigs.add(raw.split("\t", 1)[0])
    return samples, variant_count, sorted(contigs)


def read_trait(path: Path, trait_column: str):
    values = defaultdict(list)
    rows = 0
    na_rows = 0
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise SystemExit("Trait file has no header")
        id_col = "ID" if "ID" in reader.fieldnames else reader.fieldnames[0]
        if trait_column not in reader.fieldnames:
            if len(reader.fieldnames) < 2:
                raise SystemExit("Trait file must contain at least ID and one trait column")
            trait_column = reader.fieldnames[1]
        for row in reader:
            rows += 1
            sample = str(row.get(id_col, "")).strip()
            raw_value = str(row.get(trait_column, "")).strip()
            if not sample or raw_value.upper() in {"", "NA", "NAN", "NULL"}:
                na_rows += 1
                continue
            try:
                values[sample].append(float(raw_value))
            except ValueError:
                na_rows += 1
    cleaned = {sample: sum(vals) / len(vals) for sample, vals in values.items() if vals}
    duplicates = {sample: len(vals) for sample, vals in values.items() if len(vals) > 1}
    return cleaned, duplicates, rows, na_rows, trait_column


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcf", required=True)
    parser.add_argument("--trait", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trait-column", default="GC")
    args = parser.parse_args()

    vcf = Path(args.vcf)
    trait = Path(args.trait)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    samples, variant_count, contigs = read_vcf_header_and_count(vcf)
    traits, duplicates, trait_rows, na_rows, trait_column = read_trait(trait, args.trait_column)
    sample_set = set(samples)
    trait_set = set(traits)
    matched = [sample for sample in samples if sample in trait_set]
    vcf_without_trait = [sample for sample in samples if sample not in trait_set]
    trait_without_vcf = sorted(trait_set - sample_set)

    with (out / "trait.clean.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["ID", trait_column])
        for sample in matched:
            writer.writerow([sample, f"{traits[sample]:.6g}"])

    with (out / "sample_match_report.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["sample_id", "in_vcf", "has_trait", "used_for_gwas", "status"])
        for sample in samples:
            has_trait = sample in trait_set
            writer.writerow([sample, "yes", "yes" if has_trait else "no", "yes" if has_trait else "no", "matched" if has_trait else "vcf_without_trait"])
        for sample in trait_without_vcf:
            writer.writerow([sample, "no", "yes", "no", "trait_without_vcf"])

    summary = {
        "schema_version": "gwas_trait_vcf_qc_summary_v1",
        "vcf": str(vcf),
        "trait": str(trait),
        "trait_column": trait_column,
        "vcf_samples": len(samples),
        "trait_rows": trait_rows,
        "trait_na_or_invalid_rows": na_rows,
        "trait_unique_non_na": len(traits),
        "duplicate_trait_ids": len(duplicates),
        "duplicate_trait_id_counts": duplicates,
        "matched_samples": len(matched),
        "vcf_without_trait": vcf_without_trait,
        "trait_without_vcf": trait_without_vcf,
        "variant_count": variant_count,
        "contig_count": len(contigs),
        "contigs_preview": contigs[:20],
        "status": "success" if matched else "failed",
    }
    (out / "qc_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not matched:
        raise SystemExit("No matched VCF/trait samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


GWAS_QC_SH = """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "${OUTPUT_DIR}"
python3 "${SCRIPT_DIR}/run_gwas_trait_vcf_qc.py" \
  --vcf "${INPUT_DIR}/subset.clean.vcf.gz" \
  --trait "${INPUT_DIR}/90.txt" \
  --output-dir "${OUTPUT_DIR}" \
  --trait-column GC
"""


TOOLS = {
    "bcftools": {"phase": "vcf_qc", "required_for_first_case": False, "version_args": ["--version"]},
    "vcftools": {"phase": "vcf_qc", "required_for_first_case": False, "version_args": ["--version"]},
    "plink": {"phase": "population_structure", "required_for_first_case": False, "version_args": ["--version"]},
    "admixture": {"phase": "population_structure", "required_for_first_case": False, "version_args": []},
    "Rscript": {"phase": "plotting", "required_for_first_case": False, "version_args": ["--version"]},
    "gemma": {"phase": "association", "required_for_first_case": False, "version_args": ["-version"]},
    "run_pipeline.pl": {"phase": "association_tassel", "required_for_first_case": False, "version_args": ["-version"]},
    "gatk": {"phase": "variant_calling_optional", "required_for_first_case": False, "version_args": ["--version"]},
    "java": {"phase": "java_tools", "required_for_first_case": False, "version_args": ["-version"]},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-env", action="store_true", help="audit ASAW GWAS tool availability")
    parser.add_argument("--prepare", action="store_true", help="prepare gwas_trait_vcf_qc subset package and raw baseline")
    parser.add_argument("--report", action="store_true", help="write markdown report from existing audit/prepare outputs")
    parser.add_argument("--all", action="store_true", help="run audit, prepare and report")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--asaw-env", type=Path, default=DEFAULT_ASAW_ENV)
    parser.add_argument("--max-variants", type=int, default=1000)
    args = parser.parse_args()
    if not any((args.audit_env, args.prepare, args.report, args.all)):
        args.all = True

    out = args.output_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.audit_env or args.all:
        audit_environment(args.asaw_env.resolve(), out)
    if args.prepare or args.all:
        prepare_package(args.source_root.resolve(), out, max_variants=max(1, args.max_variants))
    if args.report or args.all:
        write_report(out, args.source_root.resolve(), args.asaw_env.resolve())
    write_manifest(out)
    return 0


def audit_environment(asaw_env: Path, out: Path) -> dict[str, Any]:
    rows = []
    bin_dir = asaw_env / "bin"
    for tool, meta in TOOLS.items():
        executable = bin_dir / tool
        present = executable.exists() and executable.is_file()
        version = ""
        returncode = None
        if present:
            cmd = [str(executable), *meta["version_args"]]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                returncode = proc.returncode
                version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else ""
            except Exception as exc:
                version = f"version_check_failed: {exc}"
        version_lower = version.lower()
        version_check_ok = present and returncode == 0
        usable = present and "error while loading shared libraries" not in version_lower and "no such file or directory" not in version_lower
        rows.append({
            "tool": tool,
            "phase": meta["phase"],
            "path": str(executable),
            "present": present,
            "usable": usable,
            "version": version,
            "version_check_ok": version_check_ok,
            "version_returncode": returncode,
            "required_for_first_case": bool(meta["required_for_first_case"]),
        })
    report = {
        "schema_version": "gwas_extension_env_audit_v1",
        "created_at": now(),
        "asaw_env": str(asaw_env),
        "bin_dir": str(bin_dir),
        "tools": rows,
        "missing_tools": [row["tool"] for row in rows if not row["present"]],
        "broken_tools": [row["tool"] for row in rows if row["present"] and not row["usable"]],
        "first_case_ready": True,
        "notes": [
            "The first GWAS extension case uses Python standard library only for VCF/trait matching.",
            "bcftools/vcftools/plink/admixture are audited for later GWAS substeps.",
            "Missing association tools are recorded but do not block gwas_trait_vcf_qc_v1.",
        ],
    }
    write_json(out / "env_audit.json", report)
    write_csv(out / "env_audit.csv", rows)
    return report


def prepare_package(source_root: Path, out: Path, *, max_variants: int) -> dict[str, Any]:
    clean_vcf = source_root / "clean.vcf.gz"
    trait = source_root / "90.txt"
    if not clean_vcf.exists():
        raise FileNotFoundError(f"clean VCF not found: {clean_vcf}")
    if not trait.exists():
        raise FileNotFoundError(f"trait table not found: {trait}")

    case = GWAS_TRAIT_VCF_QC
    case_root = out / "packages" / case.dir_name
    raw_code = case_root / "raw_code"
    raw_data = case_root / "raw_test_data"
    expected = case_root / "expected_outputs"
    raw_work = case_root / "_raw_run_work"
    for path in (raw_code, raw_data, expected, raw_work):
        clean_dir(path)

    (raw_code / "run_gwas_trait_vcf_qc.py").write_text(GWAS_QC_PY.strip() + "\n", encoding="utf-8")
    (raw_code / case.script_name).write_text(GWAS_QC_SH, encoding="utf-8")
    write_json(raw_code / "bbav5_extension_hint.json", extension_hint(case))
    make_vcf_subset(clean_vcf, raw_data / "subset.clean.vcf.gz", max_variants=max_variants)
    shutil.copy2(trait, raw_data / "90.txt")

    raw = run_raw(case_root, case, out / "logs" / f"{case.dir_name}.raw.log")
    expected_ok = raw["success"] and all((expected / name).is_file() and (expected / name).stat().st_size > 0 for name in case.required_outputs)
    summary_path = expected / "qc_summary.json"
    qc = read_json(summary_path) if summary_path.exists() else {}
    record = {
        "schema_version": "gwas_extension_prepare_record_v1",
        "created_at": now(),
        "case_id": case.case_id,
        "extension_archetype": case.extension_archetype,
        "template_candidate_level": "unassigned_until_extension_lifecycle_passes",
        "codex_intervention": "prepare_protocol_and_raw_package_only",
        "source_root": str(source_root),
        "case_dir": str(case_root),
        "subset": {
            "source_vcf": str(clean_vcf),
            "trait": str(trait),
            "max_variants": max_variants,
            "subset_vcf": str(raw_data / "subset.clean.vcf.gz"),
            "subset_vcf_sha256": sha256(raw_data / "subset.clean.vcf.gz"),
        },
        "required_outputs": list(case.required_outputs),
        "raw_code_ok": raw["success"],
        "expected_output_ok": expected_ok,
        "raw_run": raw,
        "qc_summary": qc,
        "next_step": "Run extension-stage API autonomous conversion with max 20 repair attempts; do not manually edit spec/code during A-level evaluation.",
    }
    write_json(case_root / "prepare_record.json", record)
    write_json(out / "prepare_manifest.json", {
        "schema_version": "gwas_extension_prepare_manifest_v1",
        "created_at": now(),
        "case_count": 1,
        "prepared": 1 if expected_ok else 0,
        "cases": [record],
    })
    (case_root / "README.md").write_text(case_readme(case, record), encoding="utf-8")
    return record


def make_vcf_subset(source: Path, dest: Path, *, max_variants: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written_variants = 0
    with gzip.open(source, "rt", encoding="utf-8", errors="ignore") as src, gzip.open(dest, "wt", encoding="utf-8") as out:
        for raw in src:
            if raw.startswith("#"):
                out.write(raw)
                continue
            if written_variants >= max_variants:
                break
            out.write(raw)
            written_variants += 1
    if written_variants == 0:
        raise RuntimeError(f"No variant records written from {source}")


def run_raw(case_root: Path, case: GwasCase, log_path: Path) -> dict[str, Any]:
    raw_work = case_root / "_raw_run_work"
    expected = case_root / "expected_outputs"
    copy_tree(case_root / "raw_test_data", raw_work / "raw_test_data")
    copy_tree(case_root / "raw_code", raw_work / "raw_code")
    before = file_set(raw_work)
    cmd = ["bash", f"raw_code/{case.script_name}", "raw_test_data", "outputs"]
    proc = subprocess.run(cmd, cwd=raw_work, capture_output=True, text=True, timeout=240)
    after = file_set(raw_work)
    new_files = sorted(after - before)
    outputs_dir = raw_work / "outputs"
    if outputs_dir.exists():
        copy_tree(outputs_dir, expected)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(cmd) + "\n\n[stdout]\n" + proc.stdout + "\n[stderr]\n" + proc.stderr + "\n",
        encoding="utf-8",
    )
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "new_files": new_files,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
    }


def extension_hint(case: GwasCase) -> dict[str, Any]:
    return {
        "schema_version": "bbav5_gwas_extension_hint_v1",
        "case_id": case.case_id,
        "archetype_id": case.extension_archetype,
        "representative_stage": "gwas_trait_prepare",
        "recommended_original_command": f"bash {case.script_name}",
        "analysis_type": "gwas_qc",
        "tool_family": "python",
        "input_roles": {
            "input_vcf": {"role": "gwas_vcf", "required": True},
            "input_trait_table": {"role": "gwas_trait_table", "required": True},
        },
        "output_contract": [
            {"name_pattern": "trait.clean.tsv", "type": "table", "primary": True, "required": True, "allow_empty": False, "semantic_role": "clean_trait_table", "compare_mode": "table", "required_for_golden": True},
            {"name_pattern": "sample_match_report.tsv", "type": "table", "primary": False, "required": True, "allow_empty": False, "semantic_role": "sample_match_report", "compare_mode": "table", "required_for_golden": True},
            {"name_pattern": "qc_summary.json", "type": "json", "primary": False, "required": True, "allow_empty": False, "semantic_role": "gwas_qc_summary", "compare_mode": "json", "required_for_golden": True},
        ],
        "comparator_plan": {
            "mode": "pipeline_golden_diff",
            "default_compare": "structured",
            "steps": [
                {"name_pattern": "trait.clean.tsv", "compare_mode": "table", "required": True},
                {"name_pattern": "sample_match_report.tsv", "compare_mode": "table", "required": True},
                {"name_pattern": "qc_summary.json", "compare_mode": "json", "required": True},
            ],
        },
        "requires_user_confirmation": False,
        "template_admission_policy": {
            "A_api_autonomous": "No Codex edits to generated spec/code; golden/template/publish gates pass.",
            "B_codex_assisted": "Codex edits are allowed after API failure; can improve templates but not API autonomous statistics.",
            "C_negative_or_failed": "Failure is retained as repair lesson/negative evidence; not publishable template.",
        },
    }


def write_report(out: Path, source_root: Path, asaw_env: Path) -> None:
    env = read_json(out / "env_audit.json")
    prep = read_json(out / "prepare_manifest.json")
    lines = [
        "# GWAS Extension-Stage Experiment v1",
        "",
        f"Generated at: {now()}",
        f"Source root: `{source_root}`",
        f"Output root: `{out}`",
        f"Runtime env target: `conda activate ASAW` (`{asaw_env}`)",
        "",
        "## Boundary",
        "",
        "- This package does not modify core bbav5 stage registration or core extension archetypes.",
        "- This package prepares the first GWAS-specific extension experiment case only: `gwas_trait_vcf_qc_v1`.",
        "- API autonomous conversion should be run later with a 20-attempt cap and no Codex spec/code edits for A-level scoring.",
        "",
        "## Environment Audit",
        "",
    ]
    if env:
        lines.append("| Tool | Present | Usable | Phase | Version |")
        lines.append("|---|---:|---:|---|---|")
        for row in env.get("tools", []):
            version = str(row.get("version") or "").replace("|", "/")
            lines.append(f"| `{row.get('tool')}` | {row.get('present')} | {row.get('usable')} | `{row.get('phase')}` | {version[:120]} |")
        lines.append("")
        missing = ", ".join(env.get("missing_tools") or []) or "none"
        broken = ", ".join(env.get("broken_tools") or []) or "none"
        lines.append(f"Missing tools recorded: `{missing}`.")
        lines.append(f"Broken tools recorded: `{broken}`.")
        lines.append("")
    else:
        lines.append("Environment audit has not been run.")
        lines.append("")

    lines.extend([
        "## Prepared Case",
        "",
    ])
    cases = prep.get("cases") if isinstance(prep, dict) else []
    if cases:
        case = cases[0]
        qc = case.get("qc_summary") or {}
        lines.extend([
            f"- case_id: `{case.get('case_id')}`",
            f"- extension_archetype: `{case.get('extension_archetype')}`",
            f"- expected_output_ok: `{case.get('expected_output_ok')}`",
            f"- subset max variants: `{(case.get('subset') or {}).get('max_variants')}`",
            f"- VCF samples: `{qc.get('vcf_samples')}`",
            f"- trait rows: `{qc.get('trait_rows')}`",
            f"- trait unique non-NA: `{qc.get('trait_unique_non_na')}`",
            f"- matched samples: `{qc.get('matched_samples')}`",
            f"- subset variant_count: `{qc.get('variant_count')}`",
            "",
        ])
    else:
        lines.append("No prepared case found.")
        lines.append("")

    lines.extend([
        "## Required Experiment Row Fields",
        "",
        "`case_id`, `analysis_substep`, `converter_mode`, `codex_intervention`, `max_api_attempts`, `actual_api_attempts`, `build_success`, `trial_success`, `confirm_success`, `validate_success`, `golden_status`, `template_gate_status`, `publish_success`, `failure_phase`, `last_error`, `input_dataset`, `vcf_samples`, `trait_samples`, `matched_samples`, `variant_count_before`, `variant_count_after`, `output_contract_passed`, `semantic_validation_passed`, `template_candidate_level`.",
        "",
        "## Template Admission",
        "",
        "- A: API autonomous success; counts toward API success rate.",
        "- B: Codex-assisted success; can enrich templates and repair lessons, but is not API autonomous success.",
        "- C: failed or blocked negative evidence; can enrich repair lessons and gates, but is not publishable template.",
        "",
        "## Next Command",
        "",
        "```bash",
        "/home/xia-lab/miniconda3/envs/test4py311/bin/python scripts/run_gwas_extension_stage_experiment.py --all",
        "```",
        "",
    ])
    (out / "experiment_protocol.md").write_text("\n".join(lines), encoding="utf-8")


def write_manifest(out: Path) -> None:
    files = []
    for path in sorted(p for p in out.rglob("*") if p.is_file() and p.name != "MANIFEST.json"):
        files.append({"path": path.relative_to(out).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    write_json(out / "MANIFEST.json", {
        "schema_version": "gwas_extension_experiment_manifest_v1",
        "created_at": now(),
        "root": str(out),
        "file_count": len(files),
        "files": files,
    })


def case_readme(case: GwasCase, record: dict[str, Any]) -> str:
    return textwrap.dedent(f"""\
    # {case.extension_archetype}

    Case ID: `{case.case_id}`

    Purpose: {case.description}

    This is an experiment-only GWAS extension-stage package. It is not registered
    as a core bbav5 stage and is not part of the core template archetype registry.

    Required outputs:
    {chr(10).join(f"- `{name}`" for name in case.required_outputs)}

    Subset:
    - source VCF: `{(record.get('subset') or {}).get('source_vcf')}`
    - trait table: `{(record.get('subset') or {}).get('trait')}`
    - subset VCF: `{(record.get('subset') or {}).get('subset_vcf')}`

    Template admission after extension lifecycle:
    - A: API autonomous success.
    - B: Codex-assisted success.
    - C: failed/negative lesson.
    """)


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def file_set(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
