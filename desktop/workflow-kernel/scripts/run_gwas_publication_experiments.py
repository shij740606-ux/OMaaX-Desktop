#!/usr/bin/env python3
"""Publication-style GWAS extension-stage experiment harness.

The harness keeps GWAS as candidate extension evidence until dedicated GWAS
template archetypes are admitted. It records both extension-stage lifecycle
metrics and GWAS-specific biological/statistical metrics.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
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

DEFAULT_SOURCE_ROOT = Path("/data_old/home/xia-lab/wutx/plantAnalysisAgent/vcf_code/gwas")
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "GWAS拓展分析阶段_发表级完整实验_20260622"
DEFAULT_ASAW_ENV = Path("/data/miniconda3/envs/ASAW")


@dataclass(frozen=True)
class GwasCase:
    index: int
    case_id: str
    group: str
    stage: str
    archetype_id: str
    script_name: str
    script_content: str
    required_outputs: tuple[str, ...]
    dependencies: tuple[str, ...]
    description: str
    candidate_only: bool = True
    validation_scope: str = "subset"

    @property
    def dir_name(self) -> str:
        return f"{self.index:02d}_{self.case_id}"


COMMON_PY = r'''
import csv
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path


def open_text(path):
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return path.open("r", encoding="utf-8", errors="ignore")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_cmd(cmd, cwd=None, timeout=240):
    started = time.time()

    def stable_cmd(items):
        stable = []
        for item in items:
            text = str(item)
            stable.append(Path(text).name if "/" in text else text)
        return stable

    def stable_tail(text):
        text = re.sub(r"/[A-Za-z0-9_./\\-]+", "<PATH>", text or "")
        text = re.sub(r"\\d{4}-\\d{2}-\\d{2}[T ][0-9:.+-]+", "<DATETIME>", text)
        return text[-4000:]

    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        success = proc.returncode == 0
        return {
            "cmd": stable_cmd(cmd),
            "returncode": proc.returncode,
            "success": success,
            "stdout_tail": "" if success else stable_tail(proc.stdout),
            "stderr_tail": "" if success else stable_tail(proc.stderr),
            "wall_sec": 0,
        }
    except Exception as exc:
        return {"cmd": stable_cmd(cmd), "returncode": None, "success": False, "stdout_tail": "", "stderr_tail": stable_tail(str(exc)), "wall_sec": 0}


def vcf_stats(path, max_records=0):
    samples = []
    variants = 0
    contigs = set()
    missing_genotypes = 0
    called_genotypes = 0
    with open_text(path) as handle:
        for raw in handle:
            if raw.startswith("##"):
                continue
            if raw.startswith("#CHROM"):
                samples = raw.rstrip("\n").split("\t")[9:]
                continue
            if not raw.strip():
                continue
            variants += 1
            parts = raw.rstrip("\n").split("\t")
            contigs.add(parts[0])
            for cell in parts[9:]:
                gt = cell.split(":", 1)[0]
                if "." in gt:
                    missing_genotypes += 1
                else:
                    called_genotypes += 1
            if max_records and variants >= max_records:
                break
    total = missing_genotypes + called_genotypes
    return {
        "vcf_samples": len(samples),
        "sample_ids": samples,
        "variant_count": variants,
        "contig_count": len(contigs),
        "contigs_preview": sorted(contigs)[:20],
        "missing_genotype_count": missing_genotypes,
        "called_genotype_count": called_genotypes,
        "missing_genotype_rate": round(missing_genotypes / total, 6) if total else 0,
    }


def read_trait(path, trait_column="GC"):
    values = defaultdict(list)
    rows = 0
    na_rows = 0
    with Path(path).open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise SystemExit("trait file has no header")
        id_col = "ID" if "ID" in reader.fieldnames else reader.fieldnames[0]
        if trait_column not in reader.fieldnames:
            trait_column = reader.fieldnames[1]
        for row in reader:
            rows += 1
            sid = str(row.get(id_col, "")).strip()
            raw_value = str(row.get(trait_column, "")).strip()
            if not sid or raw_value.upper() in {"", "NA", "NAN", "NULL"}:
                na_rows += 1
                continue
            try:
                values[sid].append(float(raw_value))
            except ValueError:
                na_rows += 1
    cleaned = {sid: sum(vals) / len(vals) for sid, vals in values.items() if vals}
    duplicates = {sid: len(vals) for sid, vals in values.items() if len(vals) > 1}
    return cleaned, duplicates, rows, na_rows, trait_column


def write_trait_outputs(vcf, trait, output_dir, prefix="trait"):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = vcf_stats(vcf)
    traits, duplicates, trait_rows, na_rows, trait_column = read_trait(trait)
    samples = stats["sample_ids"]
    trait_set = set(traits)
    sample_set = set(samples)
    matched = [sid for sid in samples if sid in trait_set]
    vcf_without_trait = [sid for sid in samples if sid not in trait_set]
    trait_without_vcf = sorted(trait_set - sample_set)
    with (out / f"{prefix}.clean.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["ID", trait_column])
        for sid in matched:
            writer.writerow([sid, f"{traits[sid]:.8g}"])
    with (out / "sample_match_report.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["sample_id", "in_vcf", "has_trait", "used_for_gwas", "status"])
        for sid in samples:
            has_trait = sid in trait_set
            writer.writerow([sid, "yes", "yes" if has_trait else "no", "yes" if has_trait else "no", "matched" if has_trait else "vcf_without_trait"])
        for sid in trait_without_vcf:
            writer.writerow([sid, "no", "yes", "no", "trait_without_vcf"])
    metrics = dict(stats)
    metrics.update({
        "trait_rows": trait_rows,
        "trait_na_or_invalid_rows": na_rows,
        "trait_unique_non_na": len(traits),
        "duplicate_trait_ids": len(duplicates),
        "duplicate_trait_id_counts": duplicates,
        "matched_samples": len(matched),
        "sample_match_rate": round(len(matched) / len(samples), 6) if samples else 0,
        "vcf_without_trait": vcf_without_trait,
        "trait_without_vcf": trait_without_vcf,
        "trait_column": trait_column,
        "status": "success" if matched else "failed",
    })
    return metrics


def dosage_from_gt(gt):
    gt = (gt or "").replace("|", "/").split(":", 1)[0]
    if "." in gt or not gt:
        return None
    alleles = gt.split("/")
    try:
        return sum(1 for allele in alleles if int(allele) > 0)
    except Exception:
        return None


def parse_vcf_records(path, max_records=0):
    samples = []
    records = []
    with open_text(path) as handle:
        for raw in handle:
            if raw.startswith("##"):
                continue
            if raw.startswith("#CHROM"):
                samples = raw.rstrip("\n").split("\t")[9:]
                continue
            if not raw.strip():
                continue
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 10:
                continue
            records.append({
                "chrom": parts[0],
                "pos": parts[1],
                "id": parts[2] if parts[2] not in {"", "."} else f"{parts[0]}:{parts[1]}",
                "ref": parts[3],
                "alt": parts[4].split(",", 1)[0],
                "calls": parts[9:],
            })
            if max_records and len(records) >= max_records:
                break
    return samples, records


def hapmap_gt(ref, alt, call):
    gt = (call or "").split(":", 1)[0].replace("|", "/")
    if "." in gt or not gt:
        return "NN"
    alleles = []
    for item in gt.split("/"):
        alleles.append(alt if item != "0" else ref)
    if not alleles:
        return "NN"
    if len(alleles) == 1:
        alleles.append(alleles[0])
    return "".join(alleles[:2])


def write_hapmap_from_vcf(vcf, out_path):
    samples, records = parse_vcf_records(vcf)
    out_path = Path(out_path)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["rs#", "alleles", "chrom", "pos", "strand", "assembly#", "center", "protLSID", "assayLSID", "panelLSID", "QCcode", *samples])
        for rec in records:
            writer.writerow([
                rec["id"],
                f"{rec['ref']}/{rec['alt']}",
                rec["chrom"],
                rec["pos"],
                "+",
                "NA",
                "NA",
                "NA",
                "NA",
                "NA",
                "NA",
                *[hapmap_gt(rec["ref"], rec["alt"], call) for call in rec["calls"]],
            ])
    return {"samples": len(samples), "rows": len(records)}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def write_candidate_pvalues(vcf, trait_table, out_path):
    traits = {}
    with Path(trait_table).open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        value_col = ""
        if reader.fieldnames:
            for name in reader.fieldnames:
                if name != "ID":
                    value_col = name
                    break
        for row in reader:
            sid = str(row.get("ID", "")).strip()
            if not sid:
                continue
            try:
                traits[sid] = float(row.get(value_col, "nan"))
            except Exception:
                continue
    samples, records = parse_vcf_records(vcf)
    rows = []
    for rec in records:
        xs = []
        ys = []
        for sid, call in zip(samples, rec["calls"]):
            if sid not in traits:
                continue
            dosage = dosage_from_gt(call)
            if dosage is None:
                continue
            xs.append(float(dosage))
            ys.append(float(traits[sid]))
        r = pearson(xs, ys)
        n = len(xs)
        z = abs(r) * math.sqrt(max(n - 2, 1))
        p_value = max(min(math.erfc(z / math.sqrt(2)), 1.0), 1e-300)
        rows.append({"chr": rec["chrom"], "pos": rec["pos"], "marker": rec["id"], "n": n, "r": r, "p": p_value})
    with Path(out_path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["chr", "pos", "marker", "n", "r", "p"])
        for row in rows:
            writer.writerow([row["chr"], row["pos"], row["marker"], row["n"], f"{row['r']:.8g}", f"{row['p']:.8g}"])
    top = min(rows, key=lambda row: row["p"]) if rows else {}
    return {"rows": len(rows), "top_marker": top.get("marker", ""), "min_p": top.get("p")}
'''


SCRIPT_TRAIT_VCF_QC = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
metrics = write_trait_outputs(input_dir / "subset.clean.vcf.gz", input_dir / "90.txt", output_dir, prefix="trait")
metrics["schema_version"] = "gwas_trait_vcf_qc_metrics_v1"
metrics["case_id"] = "gwas_trait_vcf_qc"
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("no matched samples")
PY
'''


SCRIPT_TRAIT_PREPARE = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
metrics = write_trait_outputs(input_dir / "subset.clean.vcf.gz", input_dir / "90.txt", output_dir, prefix="trait.prepared")
metrics["schema_version"] = "gwas_trait_prepare_match_metrics_v1"
metrics["case_id"] = "gwas_trait_prepare_match"
shutil.copy2(output_dir / "trait.prepared.clean.tsv", output_dir / "trait.prepared.tsv")
write_json(output_dir / "gwas_metrics.json", metrics)
PY
'''


SCRIPT_VCF_FILTER = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
vcf = input_dir / "subset.clean.vcf.gz"
before = vcf_stats(vcf)
cmd = ["bcftools", "view", "-i", "QUAL>=0", "-Oz", "-o", str(output_dir / "filtered.vcf.gz"), str(vcf)]
view = run_cmd(cmd, timeout=240)
if not view["success"]:
    write_json(output_dir / "gwas_metrics.json", {"schema_version": "gwas_vcf_filter_index_metrics_v1", "status": "failed", "bcftools_view": view, "input": before})
    raise SystemExit("bcftools view failed")
tabix = run_cmd(["tabix", "-f", "-p", "vcf", str(output_dir / "filtered.vcf.gz")], timeout=120)
after = vcf_stats(output_dir / "filtered.vcf.gz")
metrics = {
    "schema_version": "gwas_vcf_filter_index_metrics_v1",
    "case_id": "gwas_vcf_filter_index",
    "filter_expression": "QUAL>=0",
    "input_variant_count": before["variant_count"],
    "output_variant_count": after["variant_count"],
    "variant_retention_rate": round(after["variant_count"] / before["variant_count"], 6) if before["variant_count"] else 0,
    "input_sample_count": before["vcf_samples"],
    "output_sample_count": after["vcf_samples"],
    "index_exists": (output_dir / "filtered.vcf.gz.tbi").exists(),
    "bcftools_view": view,
    "tabix": tabix,
    "status": "success" if tabix["success"] else "failed",
}
write_json(output_dir / "gwas_metrics.json", metrics)
if not tabix["success"]:
    raise SystemExit("tabix failed")
PY
'''


SCRIPT_PLINK_PCA = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
vcf = input_dir / "subset.clean.vcf.gz"
stats = vcf_stats(vcf)
prefix = output_dir / "plink_pca"
cmd = ["plink", "--vcf", str(vcf), "--double-id", "--allow-extra-chr", "--pca", "3", "--out", str(prefix)]
plink = run_cmd(cmd, timeout=240)
eigenvec = prefix.with_suffix(".eigenvec")
eigenval = prefix.with_suffix(".eigenval")
vec_rows = [line for line in eigenvec.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()] if eigenvec.exists() else []
val_rows = [line for line in eigenval.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()] if eigenval.exists() else []
metrics = {
    "schema_version": "gwas_plink_pca_metrics_v1",
    "case_id": "gwas_plink_pca",
    "vcf_samples": stats["vcf_samples"],
    "variant_count": stats["variant_count"],
    "plink": plink,
    "eigenvec_exists": eigenvec.exists(),
    "eigenval_exists": eigenval.exists(),
    "eigenvec_rows": len(vec_rows),
    "eigenval_count": len(val_rows),
    "pc_count_requested": 3,
    "status": "success" if plink["success"] and eigenvec.exists() and eigenval.exists() else "failed",
}
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("plink PCA failed")
PY
'''


SCRIPT_TRAIT_BLUP_FORMAT = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
metrics = write_trait_outputs(input_dir / "subset.clean.vcf.gz", input_dir / "90.txt", output_dir, prefix="heading_days_BLUP_value")
shutil.copy2(output_dir / "heading_days_BLUP_value.clean.tsv", output_dir / "heading_days_BLUP_value.tsv")
with (output_dir / "heading_days_BLUP_value.tsv").open("r", encoding="utf-8") as src, (output_dir / "heading_days_BLUP_value.tassel.tsv").open("w", encoding="utf-8", newline="") as dst:
    reader = csv.reader(src, delimiter="\t")
    writer = csv.writer(dst, delimiter="\t")
    header = next(reader, [])
    writer.writerow(["taxa", header[1] if len(header) > 1 else "GC"])
    for row in reader:
        if len(row) >= 2:
            writer.writerow([row[0], row[1]])
metrics["schema_version"] = "gwas_trait_blup_format_metrics_v1"
metrics["case_id"] = "gwas_trait_blup_format"
metrics["blup_policy"] = "duplicate_sample_mean_as_blup_compatible_value"
metrics["tassel_trait_table"] = "heading_days_BLUP_value.tassel.tsv"
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("no matched samples")
PY
'''


SCRIPT_PLINK_IMPUTE = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
vcf = input_dir / "subset.clean.vcf.gz"
before = vcf_stats(vcf)
sorted_prefix = output_dir / "sorted_bed"
imputed_prefix = output_dir / "imputed_bed"
vcf_prefix = output_dir / "all.impute"
make_bed = run_cmd(["plink", "--vcf", str(vcf), "--double-id", "--allow-extra-chr", "--set-missing-var-ids", "@:#", "--make-bed", "--out", str(sorted_prefix)], timeout=240)
fill = run_cmd(["plink", "--bfile", str(sorted_prefix), "--allow-extra-chr", "--make-bed", "--fill-missing-a2", "--out", str(imputed_prefix)], timeout=240) if make_bed["success"] else {"success": False, "stderr_tail": "make-bed failed"}
recode = run_cmd(["plink", "--bfile", str(imputed_prefix), "--allow-extra-chr", "--recode", "vcf-iid", "--out", str(vcf_prefix)], timeout=240) if fill["success"] else {"success": False, "stderr_tail": "fill-missing-a2 failed"}
bgzip = run_cmd(["bgzip", "-f", str(vcf_prefix) + ".vcf"], timeout=120) if recode["success"] else {"success": False, "stderr_tail": "recode vcf failed"}
tabix = run_cmd(["tabix", "-f", "-p", "vcf", str(vcf_prefix) + ".vcf.gz"], timeout=120) if bgzip["success"] else {"success": False, "stderr_tail": "bgzip failed"}
after_path = output_dir / "all.impute.vcf.gz"
after = vcf_stats(after_path) if after_path.exists() else {}
metrics = {
    "schema_version": "gwas_plink_impute_metrics_v1",
    "case_id": "gwas_plink_impute",
    "input_variant_count": before["variant_count"],
    "input_sample_count": before["vcf_samples"],
    "input_missing_genotype_rate": before["missing_genotype_rate"],
    "output_variant_count": after.get("variant_count", 0),
    "output_sample_count": after.get("vcf_samples", 0),
    "output_missing_genotype_rate": after.get("missing_genotype_rate", 0),
    "make_bed": make_bed,
    "fill_missing_a2": fill,
    "recode_vcf": recode,
    "bgzip": bgzip,
    "tabix": tabix,
    "index_exists": (output_dir / "all.impute.vcf.gz.tbi").exists(),
    "status": "success" if tabix["success"] and after_path.exists() and (output_dir / "all.impute.vcf.gz.tbi").exists() else "failed",
}
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("plink imputation failed")
PY
'''


SCRIPT_TASSEL_COMPATIBLE = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
vcf = input_dir / "subset.clean.vcf.gz"
trait = input_dir / "90.txt"
trait_metrics = write_trait_outputs(vcf, trait, output_dir, prefix="tassel_trait_work")
shutil.copy2(output_dir / "tassel_trait_work.clean.tsv", output_dir / "tassel_trait.tsv")
hapmap = write_hapmap_from_vcf(vcf, output_dir / "hapmap.hmp.txt")
assoc = write_candidate_pvalues(vcf, output_dir / "tassel_trait.tsv", output_dir / "glm_pvalue.txt")
probe = run_cmd(["run_pipeline.pl", "-Xmx2G", "-importGuess", str(vcf), "-ExportPlugin", "-saveAs", str(output_dir / "tassel_probe.hmp.txt"), "-format", "Hapmap"], timeout=240)
probe_record = {
    "schema_version": "gwas_tassel_probe_v1",
    "case_id": "gwas_tassel_compatible",
    "command": probe.get("cmd"),
    "success": bool(probe.get("success")),
    "returncode": probe.get("returncode"),
    "stderr_tail": probe.get("stderr_tail", ""),
    "stdout_tail": probe.get("stdout_tail", ""),
    "interpretation": "TASSEL import/export probe only; subset data may be rejected by TASSEL contig ordering while compatible HapMap/trait/pvalue artifacts remain generated.",
}
write_json(output_dir / "tassel_probe.json", probe_record)
metrics = dict(trait_metrics)
metrics.update({
    "schema_version": "gwas_tassel_compatible_metrics_v1",
    "case_id": "gwas_tassel_compatible",
    "hapmap_rows": hapmap["rows"],
    "hapmap_samples": hapmap["samples"],
    "glm_rows": assoc["rows"],
    "top_marker": assoc["top_marker"],
    "min_p": assoc["min_p"],
    "tassel_probe_success": bool(probe.get("success")),
    "tassel_full_model_executed": False,
    "compatibility_policy": "produce TASSEL-compatible HapMap/trait/pvalue package and record real TASSEL probe result separately",
    "status": "success" if hapmap["rows"] > 0 and assoc["rows"] > 0 else "failed",
})
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("TASSEL-compatible package generation failed")
PY
'''


SCRIPT_GEMMA_ASSOC = r'''#!/usr/bin/env bash
set -euo pipefail
for _gwas_bin in /data/miniconda3/envs/ASAW/bin /home/xia-lab/miniconda3/envs/ASAW/bin; do
  if [[ -d "$_gwas_bin" ]]; then export PATH="$_gwas_bin:$PATH"; break; fi
done
INPUT_DIR="${1:-${INPUT_DIR:-raw_test_data}}"
OUTPUT_DIR="${2:-${OUTPUT_DIR:-outputs}}"
mkdir -p "$OUTPUT_DIR"
python3 - "$INPUT_DIR" "$OUTPUT_DIR" <<'PY'
''' + COMMON_PY + r'''
input_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
vcf = input_dir / "subset.clean.vcf.gz"
trait = input_dir / "90.txt"
trait_metrics = write_trait_outputs(vcf, trait, output_dir, prefix="trait")
plink_prefix = output_dir / "gemma_snp"
plink = run_cmd(["plink", "--vcf", str(vcf), "--double-id", "--allow-extra-chr", "--make-bed", "--out", str(plink_prefix)], timeout=240)
fam = plink_prefix.with_suffix(".fam")
trait_values = {}
with (output_dir / "trait.clean.tsv").open("r", encoding="utf-8") as handle:
    next(handle, None)
    for line in handle:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2:
            trait_values[parts[0]] = parts[1]
if fam.exists():
    rows = []
    for line in fam.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 6:
            sid = parts[1]
            parts[5] = trait_values.get(sid, "-9")
        rows.append(" ".join(parts))
    fam.write_text("\n".join(rows) + "\n", encoding="utf-8")
gemma_version = run_cmd(["gemma", "-h", "1"], timeout=30)
kin = run_cmd(["gemma", "-bfile", str(plink_prefix), "-gk", "1", "-o", "kin", "-outdir", str(output_dir)], timeout=240) if plink["success"] else {"success": False, "stderr_tail": "plink failed"}
assoc = run_cmd(["gemma", "-bfile", str(plink_prefix), "-lm", "4", "-o", "lm", "-outdir", str(output_dir)], timeout=240) if plink["success"] else {"success": False, "stderr_tail": "plink failed"}
assoc_files = sorted(output_dir.glob("*.assoc.txt"))
assoc_rows = 0
min_p = None
top_marker = ""
if assoc_files:
    with assoc_files[0].open("r", encoding="utf-8", errors="ignore") as handle:
        header = next(handle, "").rstrip("\n").split("\t")
        p_index = header.index("p_wald") if "p_wald" in header else -1
        rs_index = header.index("rs") if "rs" in header else 0
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(p_index, rs_index):
                continue
            assoc_rows += 1
            try:
                p = float(parts[p_index])
            except Exception:
                continue
            if min_p is None or p < min_p:
                min_p = p
                top_marker = parts[rs_index]
metrics = dict(trait_metrics)
metrics.update({
    "schema_version": "gwas_gemma_assoc_metrics_v1",
    "case_id": "gwas_gemma_assoc",
    "plink": plink,
    "gemma_version_probe": {
        "cmd": gemma_version.get("cmd"),
        "success": bool(gemma_version.get("stdout_tail") or gemma_version.get("success")),
        "probe": "GEMMA 0.98.3" if "GEMMA 0.98.3" in str(gemma_version.get("stdout_tail") or "") else str(gemma_version.get("stdout_tail") or "")[:80],
    },
    "gemma_kinship": kin,
    "gemma_lm": assoc,
    "assoc_file_count": len(assoc_files),
    "assoc_rows": assoc_rows,
    "min_p": min_p,
    "top_marker": top_marker,
    "status": "success" if assoc["success"] and assoc_rows > 0 else "failed",
})
write_json(output_dir / "gwas_metrics.json", metrics)
if metrics["status"] != "success":
    raise SystemExit("gemma association failed")
PY
'''


def cases() -> tuple[GwasCase, ...]:
    return (
        GwasCase(1, "gwas_trait_vcf_qc", "gwas_trait", "gwas_trait_prepare", "gwas_trait_vcf_qc", "run_gwas_trait_vcf_qc.sh", SCRIPT_TRAIT_VCF_QC, ("trait.clean.tsv", "sample_match_report.tsv", "gwas_metrics.json"), (), "Clean trait values and match VCF samples."),
        GwasCase(2, "gwas_vcf_filter_index", "gwas_vcf", "gwas_vcf_filter", "gwas_vcf_filter_index", "run_gwas_vcf_filter_index.sh", SCRIPT_VCF_FILTER, ("filtered.vcf.gz", "filtered.vcf.gz.tbi", "gwas_metrics.json"), ("bcftools", "tabix"), "Filter/index a bounded VCF subset with bcftools/tabix."),
        GwasCase(3, "gwas_trait_prepare_match", "gwas_trait", "gwas_trait_prepare", "gwas_trait_prepare_match", "run_gwas_trait_prepare_match.sh", SCRIPT_TRAIT_PREPARE, ("trait.prepared.tsv", "sample_match_report.tsv", "gwas_metrics.json"), (), "Trait cleaning as a standalone GWAS substep."),
        GwasCase(4, "gwas_plink_pca", "gwas_population_structure", "gwas_pca", "gwas_plink_pca", "run_gwas_plink_pca.sh", SCRIPT_PLINK_PCA, ("plink_pca.eigenvec", "plink_pca.eigenval", "gwas_metrics.json"), ("plink",), "Run PLINK PCA on a bounded VCF subset."),
        GwasCase(5, "gwas_trait_blup_format", "gwas_trait", "gwas_trait_blup_format", "gwas_trait_blup_format", "run_gwas_trait_blup_format.sh", SCRIPT_TRAIT_BLUP_FORMAT, ("heading_days_BLUP_value.tsv", "heading_days_BLUP_value.tassel.tsv", "sample_match_report.tsv", "gwas_metrics.json"), (), "Prepare BLUP-compatible trait tables for downstream GWAS tools."),
        GwasCase(6, "gwas_plink_impute", "gwas_imputation", "gwas_impute", "gwas_plink_impute", "run_gwas_plink_impute.sh", SCRIPT_PLINK_IMPUTE, ("all.impute.vcf.gz", "all.impute.vcf.gz.tbi", "imputed_bed.bed", "imputed_bed.bim", "imputed_bed.fam", "gwas_metrics.json"), ("plink", "bgzip", "tabix"), "Impute missing genotypes with PLINK fill-missing-a2 and emit VCF/BED artifacts."),
        GwasCase(7, "gwas_tassel_compatible", "gwas_tassel", "gwas_tassel_assoc", "gwas_tassel_compatible", "run_gwas_tassel_compatible.sh", SCRIPT_TASSEL_COMPATIBLE, ("hapmap.hmp.txt", "tassel_trait.tsv", "glm_pvalue.txt", "tassel_probe.json", "gwas_metrics.json"), ("run_pipeline.pl",), "Generate a TASSEL-compatible GWAS package and record the real TASSEL probe result.", validation_scope="subset_tassel_compatible"),
        GwasCase(8, "gwas_gemma_assoc", "gwas_association", "gwas_gemma_assoc", "gwas_gemma_assoc", "run_gwas_gemma_assoc.sh", SCRIPT_GEMMA_ASSOC, ("lm.assoc.txt", "gwas_metrics.json"), ("plink", "gemma"), "Run GEMMA LM on a bounded VCF/trait subset.", validation_scope="subset_association"),
    )


TOOL_ARGS = {
    "bcftools": ["--version"],
    "tabix": ["--version"],
    "bgzip": ["--version"],
    "vcftools": ["--version"],
    "plink": ["--version"],
    "admixture": [],
    "run_pipeline.pl": [],
    "run_tassel_pipeline.pl": [],
    "gemma": [],
    "java": ["-version"],
    "Rscript": ["--version"],
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def asaw_env(asaw: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{asaw / 'bin'}:{env.get('PATH', '')}"
    env["CONDA_PREFIX"] = str(asaw)
    return env


def run_cmd(cmd: list[str], *, timeout: int = 30, env: dict[str, str] | None = None, cwd: Path | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return {"cmd": cmd, "returncode": proc.returncode, "success": proc.returncode == 0, "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-4000:], "wall_sec": round(time.time() - started, 3)}
    except Exception as exc:
        return {"cmd": cmd, "returncode": None, "success": False, "stdout_tail": "", "stderr_tail": str(exc), "wall_sec": round(time.time() - started, 3)}


def preflight(asaw: Path, out: Path) -> dict[str, Any]:
    env = asaw_env(asaw)
    rows = []
    for tool, args in TOOL_ARGS.items():
        exe = asaw / "bin" / tool
        present = exe.exists()
        result = run_cmd([str(exe), *args], env=env, timeout=60) if present else {"returncode": None, "success": False, "stdout_tail": "", "stderr_tail": "not present", "cmd": [str(exe), *args], "wall_sec": 0}
        output = (result.get("stdout_tail") or result.get("stderr_tail") or "").strip()
        usable = present and ("error while loading shared libraries" not in output.lower()) and ("no such file or directory" not in output.lower())
        if tool == "admixture" and present and result.get("returncode") in {0, 255}:
            usable = True
        if tool == "gemma" and present and "GEMMA" in output:
            usable = True
        rows.append({"tool": tool, "path": str(exe), "present": present, "usable": bool(usable), "returncode": result.get("returncode"), "probe": output.splitlines()[0] if output else "", "stdout_tail": result.get("stdout_tail", ""), "stderr_tail": result.get("stderr_tail", "")})
    report = {"schema_version": "gwas_publication_tool_preflight_v1", "created_at": now(), "asaw_env": str(asaw), "tools": rows, "usable_tools": sorted(row["tool"] for row in rows if row["usable"]), "blocked_tools": sorted(row["tool"] for row in rows if row["present"] and not row["usable"]), "missing_tools": sorted(row["tool"] for row in rows if not row["present"])}
    write_json(out / "tool_preflight.json", report)
    write_csv(out / "tool_preflight.csv", rows)
    return report


def prepare(source_root: Path, out: Path, asaw: Path, max_variants: int) -> None:
    pre = preflight(asaw, out)
    usable = set(pre.get("usable_tools") or [])
    clean_vcf = source_root / "clean.vcf.gz"
    trait = source_root / "90.txt"
    if not clean_vcf.exists():
        raise FileNotFoundError(f"clean VCF not found: {clean_vcf}")
    if not trait.exists():
        raise FileNotFoundError(f"trait table not found: {trait}")
    rows = []
    for case in cases():
        root = out / "packages" / case.dir_name
        raw_code = root / "raw_code"
        raw_data = root / "raw_test_data"
        expected = root / "expected_outputs"
        raw_work = root / "_raw_run_work"
        for path in (raw_code, raw_data, expected, raw_work):
            clean_dir(path)
        script = raw_code / case.script_name
        script.write_text(case.script_content, encoding="utf-8")
        script.chmod(0o755)
        write_json(raw_code / "bbav5_extension_hint.json", extension_hint(case))
        subset = raw_data / "subset.clean.vcf.gz"
        make_vcf_subset(clean_vcf, subset, max_variants=max_variants)
        shutil.copy2(trait, raw_data / "90.txt")
        missing = [tool for tool in case.dependencies if tool not in usable]
        if missing:
            raw = {"success": False, "skipped": True, "returncode": None, "new_files": [], "stderr_tail": "missing or unusable dependency: " + ", ".join(missing), "wall_sec": 0}
            metrics = {"schema_version": "gwas_case_blocked_metrics_v1", "case_id": case.case_id, "status": "blocked", "missing_dependencies": missing}
            write_json(expected / "gwas_metrics.json", metrics)
        else:
            raw = run_raw(root, case, asaw, out / "logs" / f"{case.dir_name}.raw.log")
        gwas_metrics = read_json(expected / "gwas_metrics.json") if (expected / "gwas_metrics.json").exists() else {}
        ok = raw.get("success") and all((expected / name).is_file() and (expected / name).stat().st_size > 0 for name in case.required_outputs)
        rec = {
            "schema_version": "gwas_publication_prepare_record_v1",
            "created_at": now(),
            "case_id": case.case_id,
            "group": case.group,
            "stage": case.stage,
            "archetype_id": case.archetype_id,
            "candidate_only": case.candidate_only,
            "validation_scope": case.validation_scope,
            "case_dir": str(root),
            "source_vcf": str(clean_vcf),
            "trait": str(trait),
            "subset": {"max_variants": max_variants, "subset_vcf": str(subset), "sha256": sha256(subset)},
            "dependencies": list(case.dependencies),
            "raw_code_ok": bool(raw.get("success")),
            "expected_output_ok": bool(ok),
            "required_outputs": list(case.required_outputs),
            "raw_run": raw,
            "gwas_metrics": gwas_metrics,
        }
        rows.append(rec)
        write_json(root / "prepare_record.json", rec)
        (root / "README.md").write_text(case_readme(case, rec), encoding="utf-8")
    write_json(out / "prepare_manifest.json", {"schema_version": "gwas_publication_prepare_manifest_v1", "created_at": now(), "source_root": str(source_root), "case_count": len(rows), "prepared": sum(1 for row in rows if row["expected_output_ok"]), "blocked": sum(1 for row in rows if (row.get("raw_run") or {}).get("skipped")), "cases": rows})


def run_raw(root: Path, case: GwasCase, asaw: Path, log_path: Path) -> dict[str, Any]:
    work = root / "_raw_run_work"
    expected = root / "expected_outputs"
    copy_tree(root / "raw_test_data", work / "raw_test_data")
    copy_tree(root / "raw_code", work / "raw_code")
    before = file_set(work)
    cmd = ["bash", f"raw_code/{case.script_name}", "raw_test_data", "outputs"]
    started = time.time()
    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=600, env=asaw_env(asaw))
    after = file_set(work)
    new_files = sorted(after - before)
    if (work / "outputs").exists():
        copy_tree(work / "outputs", expected)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("$ " + " ".join(cmd) + "\n\n[stdout]\n" + proc.stdout + "\n[stderr]\n" + proc.stderr + "\n", encoding="utf-8")
    return {"success": proc.returncode == 0, "returncode": proc.returncode, "new_files": new_files, "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-4000:], "wall_sec": round(time.time() - started, 3)}


def run_batch(out: Path, converter: str, runtime_env: str, include_blocked: bool, stop_on_failure: bool) -> None:
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    effective = "deepseek_api" if converter in {"archetype_spec", "deepseek_agent"} else converter
    os.environ["BBAV5_EXTENSION_CONVERTER"] = effective
    if converter == "archetype_spec":
        import gfa_core.extension_stage.agent as agent_module
        import gfa_core.extension_stage.manager as manager_module

        def _convert_with_archetype_spec(**kwargs: Any) -> dict[str, Any]:
            return local_conversion_spec(str(kwargs.get("display_name") or ""), str(kwargs.get("runtime_env") or runtime_env))

        agent_module.convert_with_deepseek_api = _convert_with_archetype_spec
        agent_module.repair_build_with_deepseek_api = _convert_with_archetype_spec
        agent_module.build_api_conversion_spec = _convert_with_archetype_spec
        manager_module.build_api_conversion_spec = _convert_with_archetype_spec
    run_root = out / "runtime" / converter
    for path in (run_root / "extension_stages", run_root / "workspace", run_root / "input", out / "results" / converter):
        clean_dir(path)
    manager = ExtensionStageManager(PROJECT_ROOT, store_root=run_root / "extension_stages", workspace_root=run_root / "workspace", input_root=run_root / "input")
    agent = ExtensionStageAgent(manager)
    rows = []
    for case in cases():
        prep = read_json(out / "packages" / case.dir_name / "prepare_record.json") if (out / "packages" / case.dir_name / "prepare_record.json").exists() else {}
        if not include_blocked and not prep.get("expected_output_ok"):
            rows.append(blocked_row(case, prep, converter))
            continue
        row = run_one(agent, out, case, converter, runtime_env)
        rows.append(row)
        if stop_on_failure and not row_success(row):
            break
    summary = {"schema_version": "gwas_publication_batch_result_v1", "created_at": now(), "converter": converter, "runtime_env": runtime_env, "case_count": len(rows), "passed": sum(1 for row in rows if row_success(row)), "candidate_passed": sum(1 for row in rows if row.get("candidate_success")), "template_blocked": sum(1 for row in rows if row.get("template_blocked")), "failed": sum(1 for row in rows if not row_success(row) and not row.get("template_blocked")), "rows": rows}
    result_dir = out / "results" / converter
    write_json(result_dir / "summary.json", summary)
    write_csv(result_dir / "summary.csv", rows)
    (result_dir / "summary.md").write_text(summary_markdown(summary), encoding="utf-8")


def run_one(agent: Any, out: Path, case: GwasCase, converter: str, runtime_env: str) -> dict[str, Any]:
    case_root = out / "packages" / case.dir_name
    res_dir = out / "results" / converter / "per_case" / case.dir_name
    clean_dir(res_dir)
    prep = read_json(case_root / "prepare_record.json")
    row = lifecycle_base_row(case, converter, prep)
    allow_api_repair = converter != "archetype_spec"
    try:
        build = agent.create_from_uploads(display_name=f"gwas_publication_{case.case_id}", command=f"bash {case.script_name}", runtime_env=runtime_env, code_files=upload_files(case_root / "raw_code"), data_files=upload_files(case_root / "raw_test_data"), description=case.description)
        write_json(res_dir / "build_response.json", build)
        ext = build.get("extension") or {}
        eid = str(ext.get("extension_id") or "")
        row.update({"build_success": bool(build.get("success") and eid), "extension_id": eid, "api_attempt_count": len(ext.get("build_attempts") or []), "build_api_attempt_count": len(ext.get("build_attempts") or []), "sanitizer_action_count": len(ext.get("sanitizer_actions") or [])})
        trial = agent.trial_run(eid)
        write_json(res_dir / "trial_run.json", trial)
        if not trial.get("success") and allow_api_repair:
            repair = agent.repair_from_context(eid, phase="trial", run_context=trial.get("run") or {}, max_attempts=20, auto_publish=False)
            write_json(res_dir / "trial_repair_response.json", repair)
            row["trial_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                trial = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
            else:
                row["last_repair_phase"] = "trial"
                row["last_repair_error"] = str(repair.get("last_error") or "")
        elif not trial.get("success"):
            row["last_repair_phase"] = "trial"
            row["last_repair_error"] = str((trial.get("run") or {}).get("error") or "trial failed; API repair disabled for deterministic baseline")
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len((trial.get("run") or {}).get("outputs") or [])
        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(eid, outputs=outputs, nl_triggers=[case.case_id, case.stage, case.archetype_id])
        write_json(res_dir / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        val = agent.validate(eid)
        write_json(res_dir / "validate_response.json", val)
        run = val.get("run") or {}
        gate = run.get("template_gate") or {}
        golden = run.get("golden_comparison") or {}
        row["validate_success"] = bool(val.get("success"))
        row["golden_status"] = str(golden.get("status") or "")
        row["template_gate_status"] = str(gate.get("status") or "")
        row["final_validation_status"] = str(run.get("status") or "")
        gate_errors = "; ".join(gate.get("errors") or [])
        if gate.get("status") == "failed" and ("unknown archetype_id" in gate_errors or "missing archetype_id" in gate_errors):
            row["template_blocked"] = True
            row["candidate_success"] = row["golden_status"] == "passed" and row["trial_success"] and row["confirm_success"]
            row["last_repair_phase"] = "template_gate"
            row["last_repair_error"] = gate_errors
        elif not val.get("success") and allow_api_repair:
            repair = agent.repair_from_context(eid, phase="validation", run_context=run, max_attempts=20, auto_publish=False)
            write_json(res_dir / "validation_repair_response.json", repair)
            row["validation_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                run = repair.get("run") or {}
                gate = run.get("template_gate") or {}
                golden = run.get("golden_comparison") or {}
                row["validate_success"] = True
                row["golden_status"] = str(golden.get("status") or "")
                row["template_gate_status"] = str(gate.get("status") or "")
                row["final_validation_status"] = str(run.get("status") or "")
            else:
                row["last_repair_phase"] = "validation"
                row["last_repair_error"] = str(repair.get("last_error") or "")
        elif not val.get("success"):
            row["last_repair_phase"] = "validation"
            row["last_repair_error"] = str(run.get("error") or "validation failed; API repair disabled for deterministic baseline")
        if not row.get("template_blocked"):
            try:
                pub = agent.publish(eid)
            except Exception as pub_exc:
                if not allow_api_repair:
                    row["last_repair_phase"] = "publish_gate"
                    row["last_repair_error"] = str(pub_exc)
                    raise
                publish_context = {"repair_phase": "publish_gate", "status": "failed", "error": str(pub_exc), "publish_checks": (agent.load(eid).get("publish_checks") or {}) if hasattr(agent, "load") else {}, "last_validation": run, "golden_comparison": golden, "template_gate": gate}
                repair = agent.repair_from_context(eid, phase="publish_gate", run_context=publish_context, max_attempts=20, auto_publish=True)
                write_json(res_dir / "publish_repair_response.json", repair)
                row["publish_repair_attempt_count"] = int(repair.get("attempts") or 0)
                if repair.get("success") and repair.get("published"):
                    pub = {"success": True, "extension": repair.get("extension") or {}}
                else:
                    row["last_repair_phase"] = "publish_gate"
                    row["last_repair_error"] = str(repair.get("last_error") or str(pub_exc))
                    raise pub_exc
            write_json(res_dir / "publish_response.json", pub)
            row["publish_success"] = bool(pub.get("success"))
            row["final_publish_status"] = str((pub.get("extension") or {}).get("status") or "")
    except Exception as exc:
        row["error"] = str(exc)
        write_json(res_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc(), "row": row})
    row["total_api_repair_attempt_count"] = int(row.get("trial_repair_attempt_count") or 0) + int(row.get("validation_repair_attempt_count") or 0) + int(row.get("publish_repair_attempt_count") or 0)
    write_json(res_dir / "result_row.json", row)
    return row


def lifecycle_base_row(case: GwasCase, converter: str, prep: dict[str, Any]) -> dict[str, Any]:
    return {"case_id": case.case_id, "group": case.group, "stage": case.stage, "archetype_id": case.archetype_id, "candidate_only": case.candidate_only, "converter": converter, "raw_code_ok": bool(prep.get("raw_code_ok")), "expected_output_ok": bool(prep.get("expected_output_ok")), "build_success": False, "extension_id": "", "trial_success": False, "confirm_success": False, "validate_success": False, "golden_status": "", "template_gate_status": "", "publish_success": False, "candidate_success": False, "template_blocked": False, "output_count": 0, "api_attempt_count": 0, "build_api_attempt_count": 0, "trial_repair_attempt_count": 0, "validation_repair_attempt_count": 0, "publish_repair_attempt_count": 0, "total_api_repair_attempt_count": 0, "final_validation_status": "", "final_publish_status": "", "last_repair_phase": "", "last_repair_error": "", "sanitizer_action_count": 0, "gwas_metrics": prep.get("gwas_metrics") or {}, "error": ""}


def blocked_row(case: GwasCase, prep: dict[str, Any], converter: str) -> dict[str, Any]:
    row = lifecycle_base_row(case, converter, prep)
    row["error"] = str((prep.get("raw_run") or {}).get("stderr_tail") or "case not prepared")
    return row


def extension_hint(case: GwasCase) -> dict[str, Any]:
    volatile_binary = {"filtered.vcf.gz", "filtered.vcf.gz.tbi", "all.impute.vcf.gz", "all.impute.vcf.gz.tbi", "imputed_bed.bed"}
    output_contract = [{"name_pattern": name, "type": out_type(name), "primary": index == 0, "required": True, "allow_empty": False, "semantic_role": semantic_role(name), "compare_mode": compare_mode(name), "required_for_golden": name not in volatile_binary} for index, name in enumerate(case.required_outputs)]
    return {"schema_version": "bbav5_gwas_publication_extension_hint_v1", "case_id": case.case_id, "archetype_id": case.archetype_id, "representative_stage": case.stage, "recommended_original_command": f"bash {case.script_name}", "analysis_type": "generic_cli", "tool_family": "generic", "input_roles": {"input_file": {"role": "generic_input_file", "required": True}}, "output_contract": output_contract, "comparator_plan": {"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "tool runtime logs"], "steps": [{"name_pattern": item["name_pattern"], "compare_mode": item["compare_mode"], "required": True} for item in output_contract]}, "requires_user_confirmation": True, "candidate_only": case.candidate_only, "validation_scope": case.validation_scope}


def local_conversion_spec(display_name: str, runtime_env: str) -> dict[str, Any]:
    case = case_from_display(display_name)
    hint = extension_hint(case)
    original_command = f"bash {case.script_name} . outputs"
    templated_command = f"bash {{code_dir}}/{case.script_name} {{input_dir}} {{output_dir}}"
    return {"template_type": "gwas_publication_candidate_spec", "archetype_id": case.archetype_id, "original_command": original_command, "command_template": templated_command, "semantic_command_template": "", "wrapper_command_template": templated_command, "analysis_type": hint["analysis_type"], "tool_family": hint["tool_family"], "input_roles": hint["input_roles"], "inputs": {"input_file": {"required": True}}, "parameters": {}, "outputs": {"directory": "outputs", "patterns": list(case.required_outputs)}, "output_contract": hint["output_contract"], "comparator_plan": hint["comparator_plan"], "requires_user_confirmation": True, "notes": "GWAS publication candidate spec; template gate is expected to block until GWAS archetypes are admitted.", "converter": {"type": "archetype_spec", "mode": "gwas_publication_candidate", "runtime_env": runtime_env}}


def write_report(out: Path) -> None:
    summaries = {}
    for converter in ("archetype_spec", "deepseek_agent", "deepseek_api"):
        path = out / "results" / converter / "summary.json"
        if path.exists():
            summaries[converter] = read_json(path)
    prepare_manifest = read_json(out / "prepare_manifest.json") if (out / "prepare_manifest.json").exists() else {}
    pre = read_json(out / "tool_preflight.json") if (out / "tool_preflight.json").exists() else {}
    metrics = {"schema_version": "gwas_publication_metrics_v1", "created_at": now(), "tool_preflight": pre, "prepare": {"case_count": prepare_manifest.get("case_count", 0), "prepared": prepare_manifest.get("prepared", 0), "blocked": prepare_manifest.get("blocked", 0)}, "summaries": summaries}
    write_json(out / "publication_metrics.json", metrics)
    tables = out / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    for converter, summary in summaries.items():
        write_csv(tables / f"table_{converter}_results.csv", summary.get("rows") or [])
    write_csv(tables / "table_prepare_cases.csv", prepare_manifest.get("cases") or [])
    (out / "summary_report.html").write_text(report_html(metrics), encoding="utf-8")


def report_html(metrics: dict[str, Any]) -> str:
    rows = []
    for name, summary in (metrics.get("summaries") or {}).items():
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{summary.get('case_count')}</td><td>{summary.get('passed')}</td><td>{summary.get('candidate_passed')}</td><td>{summary.get('template_blocked')}</td><td>{summary.get('failed')}</td></tr>")
    tool_rows = []
    for row in ((metrics.get("tool_preflight") or {}).get("tools") or []):
        tool_rows.append(f"<tr><td>{html.escape(str(row.get('tool')))}</td><td>{row.get('present')}</td><td>{row.get('usable')}</td><td>{html.escape(str(row.get('probe') or ''))}</td></tr>")
    return "<!doctype html><html><head><meta charset='utf-8'><title>GWAS extension publication experiment</title><style>body{font:14px Arial,sans-serif;margin:24px;color:#172033}table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #d8dee9;padding:6px;text-align:left}th{background:#edf3f7}</style></head><body><h1>GWAS extension publication experiment</h1><h2>Run summaries</h2><table><tr><th>converter</th><th>cases</th><th>published passed</th><th>candidate passed</th><th>template blocked</th><th>failed</th></tr>" + "".join(rows) + "</table><h2>Tool preflight</h2><table><tr><th>tool</th><th>present</th><th>usable</th><th>probe</th></tr>" + "".join(tool_rows) + "</table></body></html>"


def summary_markdown(summary: dict[str, Any]) -> str:
    lines = ["# GWAS publication extension summary", "", f"- converter: `{summary.get('converter')}`", f"- cases: `{summary.get('case_count')}`", f"- published passed: `{summary.get('passed')}`", f"- candidate passed: `{summary.get('candidate_passed')}`", f"- template blocked: `{summary.get('template_blocked')}`", f"- failed: `{summary.get('failed')}`", ""]
    return "\n".join(lines)


def row_success(row: dict[str, Any]) -> bool:
    return all(bool(row.get(key)) for key in ("raw_code_ok", "expected_output_ok", "build_success", "trial_success", "confirm_success", "validate_success", "publish_success")) and row.get("golden_status") == "passed" and row.get("template_gate_status") == "passed"


def make_vcf_subset(source: Path, dest: Path, *, max_variants: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with gzip.open(source, "rt", encoding="utf-8", errors="ignore") as src, gzip.open(dest, "wt", encoding="utf-8") as out:
        for raw in src:
            if raw.startswith("#"):
                out.write(raw)
                continue
            if written >= max_variants:
                break
            out.write(raw)
            written += 1
    if written == 0:
        raise RuntimeError(f"no variants copied from {source}")


def case_from_display(text: str) -> GwasCase:
    for case in cases():
        if case.case_id in text or case.archetype_id in text:
            return case
    raise ValueError(f"cannot infer GWAS case from display name: {text}")


def case_readme(case: GwasCase, record: dict[str, Any]) -> str:
    return f"# {case.case_id}\n\n- stage: `{case.stage}`\n- archetype_id: `{case.archetype_id}`\n- candidate_only: `{case.candidate_only}`\n- validation_scope: `{case.validation_scope}`\n- expected_output_ok: `{record.get('expected_output_ok')}`\n\nThis package is part of the GWAS extension-stage publication experiment and uses a bounded VCF subset.\n"


def upload_files(root: Path) -> list[tuple[str, bytes]]:
    return [(path.name, path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file()]


def expected_ok(root: Path, case: GwasCase) -> bool:
    return all((root / "expected_outputs" / name).is_file() and (root / "expected_outputs" / name).stat().st_size > 0 for name in case.required_outputs)


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
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def out_type(name: str) -> str:
    if name.endswith(".json"):
        return "json"
    if name.endswith((".tsv", ".csv", ".txt")):
        return "table"
    if name.endswith(".gz"):
        return "vcf"
    return "file"


def compare_mode(name: str) -> str:
    if name.endswith(".json"):
        return "json"
    if name.endswith((".tsv", ".csv", ".txt")):
        return "table"
    return "file_presence"


def semantic_role(name: str) -> str:
    mapping = {
        "heading_days_BLUP_value.tsv": "gwas_blup_trait_table",
        "heading_days_BLUP_value.tassel.tsv": "gwas_tassel_trait_table",
        "all.impute.vcf.gz": "gwas_imputed_vcf_gz",
        "all.impute.vcf.gz.tbi": "gwas_imputed_vcf_index",
        "imputed_bed.bed": "gwas_imputed_plink_bed",
        "imputed_bed.bim": "gwas_imputed_plink_bim",
        "imputed_bed.fam": "gwas_imputed_plink_fam",
        "hapmap.hmp.txt": "gwas_tassel_hapmap",
        "tassel_trait.tsv": "gwas_tassel_trait_table",
        "glm_pvalue.txt": "gwas_tassel_glm_pvalue_table",
        "tassel_probe.json": "gwas_tassel_probe_record",
        "lm.assoc.txt": "gwas_gemma_assoc_table",
    }
    if name in mapping:
        return mapping[name]
    if "metrics" in name:
        return "gwas_metrics"
    if "trait" in name:
        return "gwas_trait_table"
    if "sample_match" in name:
        return "gwas_sample_match_report"
    if "eigen" in name:
        return "gwas_pca_output"
    if "vcf" in name:
        return "gwas_vcf"
    return "gwas_output"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--asaw-env", type=Path, default=DEFAULT_ASAW_ENV)
    parser.add_argument("--converter", default="archetype_spec", choices=["archetype_spec", "deepseek_agent", "deepseek_api", "local_rule", "rule"])
    parser.add_argument("--runtime-env", default="ASAW")
    parser.add_argument("--max-variants", type=int, default=1000)
    parser.add_argument("--include-blocked", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()
    if not any((args.preflight, args.prepare, args.run, args.report, args.all)):
        args.preflight = True
    out = args.output_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.preflight or args.all:
        preflight(args.asaw_env.resolve(), out)
    if args.prepare or args.all:
        prepare(args.source_root.resolve(), out, args.asaw_env.resolve(), max_variants=max(1, args.max_variants))
    if args.run or args.all:
        run_batch(out, args.converter, args.runtime_env, args.include_blocked, args.stop_on_failure)
    if args.report or args.all:
        write_report(out)
    write_json(out / "MANIFEST.json", {"schema_version": "gwas_publication_experiment_manifest_v1", "created_at": now(), "output_root": str(out), "script": str(Path(__file__).resolve()), "cases": [case.case_id for case in cases()]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
