#!/usr/bin/env python3
"""Annotation stage runner.

Local annotation runs InterProScan for GO and KofamScan for KO as independent
jobs when both are requested.  Raw outputs are kept under annotation/analysis/
and reusable background files are written under final/.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.rnaseq_env import rnaseq_bin, rnaseq_rscript


DEFAULT_INTERPROSCAN = os.environ.get(
    "INTERPROSCAN_BIN",
    str(PROJECT_ROOT / "tools" / "interproscan-5.78-109.0" / "interproscan.sh"),
)
DEFAULT_KOFAM_CONFIG = str(PROJECT_ROOT / "data" / "kegg_new" / "config.yml")
DEFAULT_BIOC_RSCRIPT = str(PROJECT_ROOT / "scripts" / "fetch_gene2go_bioc.R")
DEFAULT_INTERPROSCAN_TMP_ROOT = "/tmp/interproscan"
RNASEQ_RSCRIPT = rnaseq_rscript()
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")
GO_RE = re.compile(r"GO:\d{7}")
KO_RE = re.compile(r"^K\d{5}$")
SPECIES_ALIASES = {
    "at": "Arabidopsis thaliana",
    "ath": "Arabidopsis thaliana",
    "arabidopsis": "Arabidopsis thaliana",
    "arabidopsis_thaliana": "Arabidopsis thaliana",
    "os": "Oryza sativa",
    "osa": "Oryza sativa",
    "rice": "Oryza sativa",
    "oryza_sativa": "Oryza sativa",
    "sl": "Solanum lycopersicum",
    "sly": "Solanum lycopersicum",
    "tomato": "Solanum lycopersicum",
    "solanum_lycopersicum": "Solanum lycopersicum",
    "hsa": "Homo sapiens",
    "human": "Homo sapiens",
    "mmu": "Mus musculus",
    "mouse": "Mus musculus",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def compute_file_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return ""


def read_fasta_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            if raw.startswith(">"):
                gene_id = raw[1:].strip().split()[0]
                if gene_id:
                    ids.append(gene_id)
    return ids


def sanitize_protein_fasta_for_local_annotation(source: Path, dest: Path) -> dict[str, Any]:
    """Write an InterProScan-safe FASTA by removing ORF stop markers."""
    stats: dict[str, Any] = {
        "source": str(source),
        "clean_fasta": str(dest),
        "sequence_count": 0,
        "modified_sequences": 0,
        "removed_asterisks": 0,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    current_modified = False
    with source.open("r", encoding="utf-8", errors="ignore") as infile, dest.open("w", encoding="utf-8") as outfile:
        for raw in infile:
            if raw.startswith(">"):
                if stats["sequence_count"] and current_modified:
                    stats["modified_sequences"] += 1
                stats["sequence_count"] += 1
                current_modified = False
                outfile.write(raw)
                continue
            removed = raw.count("*")
            if removed:
                stats["removed_asterisks"] += removed
                current_modified = True
                raw = raw.replace("*", "")
            outfile.write(raw)
    if stats["sequence_count"] and current_modified:
        stats["modified_sequences"] += 1
    return stats


def canonical_gene_id(gene_id: str) -> str:
    """Return a gene-level ID used for cross-stage matching.

    Examples:
      FtPinG0001.01.T01 -> FtPinG0001.01
      FtPinG0001.T01 -> FtPinG0001
      LOC_Os01g01010.1 -> LOC_Os01g01010
      Os01t0100100-01 -> Os01g0100100
    """
    value = gene_id.strip()
    value = re.split(r"\s+", value, maxsplit=1)[0].strip().lstrip(">")
    value = re.sub(r"\.T\d+$", "", value)
    value = re.sub(r"\.mRNA\d+$", "", value)
    value = re.sub(r"(?i)([._-](?:mrna|rna|transcript|cds|protein|pep|peptide)\d+[A-Za-z]?)$", "", value)
    value = re.sub(r"(\.\d+)$", "", value)
    rap_match = re.match(r"^(Os\d{2})t(\d+)(?:-\d+)?$", value, flags=re.IGNORECASE)
    if rap_match:
        value = f"{rap_match.group(1)}g{rap_match.group(2)}"
    return value


def normalize_species_hint(value: str) -> str:
    hint = (value or "").strip()
    if not hint:
        return ""
    key = re.sub(r"\s+", "_", hint.lower())
    return SPECIES_ALIASES.get(key, hint)


def infer_species_hint(prefix: str) -> str:
    code = (prefix or "").split(".", 1)[0].strip()
    return normalize_species_hint(code)


def prepare_dirs(workspace: Path) -> dict[str, Path]:
    paths = {
        "analysis_dir": workspace / "annotation" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def infer_annotation_scope(annotation_scope: str, skip_go: bool, skip_kegg: bool) -> str:
    scope = (annotation_scope or "auto").strip().lower()
    if scope != "auto":
        return scope
    if skip_go and not skip_kegg:
        return "kegg"
    if skip_kegg and not skip_go:
        return "go"
    return "both"


def scoped_name(prefix: str, stem: str, annotation_scope: str, suffix: str) -> str:
    if annotation_scope in {"go", "kegg"}:
        return f"{prefix}.{stem}_{annotation_scope}{suffix}"
    return f"{prefix}.{stem}{suffix}"


def run_interproscan(
    protein_fasta: Path,
    output_base: Path,
    interproscan: Path,
    cpu: int,
    applications: str,
    temp_dir: Path,
) -> dict[str, Any]:
    cmd = [
        str(interproscan),
        "-i",
        str(protein_fasta),
        "-t",
        "p",
        "-f",
        "TSV,XML,GFF3",
        "--goterms",
        "--pathways",
        "--iprlookup",
        "-cpu",
        str(cpu),
        "-T",
        str(temp_dir),
        "-b",
        str(output_base),
    ]
    if applications:
        cmd.extend(["-appl", applications])

    start = time.time()
    env = os.environ.copy()
    rnaseq_bin_text = str(rnaseq_bin())
    if rnaseq_bin_text not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = rnaseq_bin_text + os.pathsep + env.get("PATH", "")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_sec": round(time.time() - start, 3),
    }


def make_interproscan_run_dir(analysis_dir: Path, prefix: str, annotation_scope: str) -> tuple[str, Path]:
    return make_annotation_run_dir(analysis_dir, prefix, annotation_scope, "interproscan")


def interproscan_tmp_root() -> Path:
    configured = os.environ.get("BBAV5_INTERPROSCAN_TMP_ROOT", DEFAULT_INTERPROSCAN_TMP_ROOT).strip()
    return Path(configured or DEFAULT_INTERPROSCAN_TMP_ROOT).expanduser()


def make_interproscan_scratch_dir(job_id: str, run_id: str) -> Path:
    safe_job = re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id).strip("._") or "manual"
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id).strip("._") or f"run_{os.getpid()}"
    scratch_dir = interproscan_tmp_root() / safe_job / safe_run
    scratch_dir.mkdir(parents=True, exist_ok=False)
    return scratch_dir


def make_annotation_run_dir(analysis_dir: Path, prefix: str, annotation_scope: str, tool_name: str) -> tuple[str, Path]:
    safe_prefix = re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix).strip("._") or "annotation"
    safe_scope = re.sub(r"[^A-Za-z0-9_.-]+", "_", annotation_scope).strip("._") or "auto"
    safe_tool = re.sub(r"[^A-Za-z0-9_.-]+", "_", tool_name).strip("._") or "tool"
    base = f"{safe_prefix}.{safe_scope}.{safe_tool}.{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}"
    runs_dir = analysis_dir / f"{safe_tool}_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    for index in range(100):
        run_id = base if index == 0 else f"{base}_{index}"
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_id, run_dir
        except FileExistsError:
            continue
    raise RuntimeError(f"Unable to create unique {tool_name} run directory under {runs_dir}")


def copy_interproscan_outputs(run_output_base: Path, canonical_output_base: Path) -> dict[str, Path]:
    copied: dict[str, Path] = {}
    for suffix in (".tsv", ".xml", ".gff3"):
        source = Path(str(run_output_base) + suffix)
        target = Path(str(canonical_output_base) + suffix)
        if source.exists() and source.stat().st_size > 0:
            shutil.copy2(source, target)
            copied[suffix.lstrip(".")] = target
    return copied


def parse_interproscan_tsv(tsv_path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], int]:
    """Parse InterProScan TSV into raw and canonical gene2go maps."""
    raw_gene2go: dict[str, set[str]] = defaultdict(set)
    canonical_gene2go: dict[str, set[str]] = defaultdict(set)
    gene2ipr: dict[str, set[str]] = defaultdict(set)
    rows = 0

    if not tsv_path.exists():
        return {}, {}, {}, 0

    with tsv_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line:
                continue
            rows += 1
            parts = line.split("\t")
            if len(parts) < 1:
                continue
            gene_id = parts[0].strip()
            if not gene_id:
                continue
            canon_id = canonical_gene_id(gene_id)
            if len(parts) > 11 and parts[11].startswith("IPR"):
                gene2ipr[canon_id].add(parts[11])
            for go_id in GO_RE.findall(line):
                raw_gene2go[gene_id].add(go_id)
                canonical_gene2go[canon_id].add(go_id)

    return dict(raw_gene2go), dict(canonical_gene2go), dict(gene2ipr), rows


def write_gene2go(path: Path, gene2go: dict[str, set[str]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        for gene_id in sorted(gene2go):
            for go_id in sorted(gene2go[gene_id]):
                handle.write(f"{gene_id}\t{go_id}\n")
                count += 1
    return count


def parse_gene2go_tsv(path: Path, protein_ids: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]], int]:
    """Parse a two-column gene2go file and keep only annotations matching local IDs.

    Bioconductor OrgDb resources can return species-level numeric IDs that do
    not match the uploaded proteome. Those mappings are valid for the OrgDb
    key space but invalid as a local RNA-seq background, so they must not be
    written as this job's canonical gene2go file.
    """
    raw_gene2go: dict[str, set[str]] = defaultdict(set)
    canonical_gene2go: dict[str, set[str]] = defaultdict(set)
    local_by_upper: dict[str, str] = {}
    for protein_id in protein_ids:
        local_by_upper.setdefault(canonical_gene_id(protein_id).upper(), canonical_gene_id(protein_id))
        local_by_upper.setdefault(protein_id.upper(), protein_id)

    rows = 0
    if not path.exists():
        return {}, {}, 0

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[\t, ]+", line)
            if len(parts) < 2:
                continue
            gene_id = parts[0].strip()
            go_ids = GO_RE.findall(line)
            if not gene_id or not go_ids:
                continue
            rows += 1
            local_id = local_by_upper.get(canonical_gene_id(gene_id).upper()) or local_by_upper.get(gene_id.upper())
            if not local_id:
                raw_gene2go[gene_id].update(go_ids)
                continue
            raw_gene2go[gene_id].update(go_ids)
            raw_gene2go[local_id].update(go_ids)
            canonical_gene2go[canonical_gene_id(local_id)].update(go_ids)
    return dict(raw_gene2go), dict(canonical_gene2go), rows


def run_bioc_gene2go(
    species: str,
    output_file: Path,
    report_file: Path,
    cache_dir: Path,
    source: str,
    rscript_bin: Path,
    helper_script: Path,
) -> dict[str, Any]:
    cmd = [
        str(rscript_bin),
        str(helper_script),
        "--species",
        species,
        "--source",
        source,
        "--out",
        str(output_file),
        "--report",
        str(report_file),
        "--cache-dir",
        str(cache_dir),
    ]
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    report: dict[str, Any] = {}
    if report_file.exists():
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except Exception:
            report = {}
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_sec": round(time.time() - start, 3),
        "report": report,
    }


def write_gene2terms(path: Path, gene2terms: dict[str, set[str]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        for gene_id in sorted(gene2terms):
            for term in sorted(gene2terms[gene_id]):
                handle.write(f"{gene_id}\t{term}\n")
                count += 1
    return count


def write_gene_id_map(path: Path, ids: list[str]) -> int:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["raw_id", "canonical_gene_id"])
        for raw_id in ids:
            writer.writerow([raw_id, canonical_gene_id(raw_id)])
    return len(ids)


def run_kofamscan(
    protein_fasta: Path,
    output_file: Path,
    config_file: Path,
    cpu: int,
    temp_dir: Path,
) -> dict[str, Any]:
    cmd = [
        "exec_annotation",
        "-o",
        str(output_file),
        "--config",
        str(config_file),
        "--cpu",
        str(cpu),
        "-f",
        "detail-tsv",
        "--no-report-unannotated",
        "--tmp-dir",
        str(temp_dir),
        str(protein_fasta),
    ]
    env = os.environ.copy()
    if CONDA_BIN not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = CONDA_BIN + os.pathsep + env.get("PATH", "")
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    return {
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_sec": round(time.time() - start, 3),
    }


def kofam_config_ready(config_file: Path) -> tuple[bool, str]:
    if not config_file.exists():
        return False, f"KofamScan config not found: {config_file}"
    profile_path = ""
    ko_list_path = ""
    with config_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip().strip("'\"")
            if key.strip() == "profile":
                profile_path = value
            elif key.strip() == "ko_list":
                ko_list_path = value
    if not profile_path:
        return False, f"KofamScan profile path missing in {config_file}"
    if not ko_list_path:
        return False, f"KofamScan ko_list path missing in {config_file}"
    profile = Path(profile_path)
    ko_list = Path(ko_list_path)
    if not profile.exists():
        return False, f"KofamScan profiles not found: {profile}"
    if profile.is_dir() and not any(profile.glob("*.hmm")) and not any(profile.glob("*.hal")):
        return False, f"KofamScan profiles directory has no .hmm/.hal files: {profile}"
    if not ko_list.exists():
        return False, f"KofamScan ko_list not found: {ko_list}"
    if profile.is_dir():
        hmm_count = sum(1 for _ in profile.glob("*.hmm"))
        ko_count = 0
        with ko_list.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                if raw.startswith("K"):
                    ko_count += 1
        if ko_count and hmm_count < int(ko_count * 0.95):
            return False, (
                f"KofamScan profiles look incomplete: {hmm_count} .hmm files for "
                f"{ko_count} KO entries under {profile}"
            )
    return True, "ready"


def parse_kofamscan_detail_tsv(path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], int, int]:
    raw_gene2ko: dict[str, set[str]] = defaultdict(set)
    canonical_gene2ko: dict[str, set[str]] = defaultdict(set)
    rows = 0
    accepted = 0
    if not path.exists():
        return {}, {}, 0, 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line:
                continue
            rows += 1
            parts = line.split("\t")
            # detail-tsv high-confidence rows start with "*"; columns used by
            # KofamScan examples are: marker, gene, KO, threshold, score, ...
            if not parts or parts[0] != "*" or len(parts) < 3:
                continue
            gene_id = parts[1].strip()
            ko = parts[2].strip()
            if not gene_id or not KO_RE.match(ko):
                continue
            raw_gene2ko[gene_id].add(ko)
            canonical_gene2ko[canonical_gene_id(gene_id)].add(ko)
            accepted += 1
    return dict(raw_gene2ko), dict(canonical_gene2ko), rows, accepted


def write_summary(
    path: Path,
    gene2go: dict[str, set[str]],
    gene2ipr: dict[str, set[str]],
    gene2ko: dict[str, set[str]],
) -> None:
    rows = []
    for gene_id in sorted(set(gene2go) | set(gene2ipr) | set(gene2ko)):
        rows.append(
            {
                "gene_id": gene_id,
                "go_count": len(gene2go.get(gene_id, set())),
                "ipr_count": len(gene2ipr.get(gene_id, set())),
                "ko_count": len(gene2ko.get(gene_id, set())),
                "go_terms": ",".join(sorted(gene2go.get(gene_id, set()))),
                "interpro_ids": ",".join(sorted(gene2ipr.get(gene_id, set()))),
                "ko_terms": ",".join(sorted(gene2ko.get(gene_id, set()))),
            }
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["gene_id", "go_count", "ipr_count", "ko_count", "go_terms", "interpro_ids", "ko_terms"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, args: argparse.Namespace, metrics: dict[str, Any], artifacts: dict[str, Any]) -> None:
    lines = [
        "# Annotation Report",
        "",
        f"- Prefix: {args.prefix}",
        f"- Job ID: {args.job_id}",
        f"- Annotation scope: {metrics.get('annotation_scope', 'both')}",
        f"- Method: {metrics.get('method_used', 'interproscan+kofamscan')}",
        f"- Input proteins: {metrics.get('input_proteins', 0)}",
        f"- InterProScan rows: {metrics.get('interproscan_rows', 0)}",
        f"- Raw IDs with GO: {metrics.get('raw_genes_with_go', 0)}",
        f"- Canonical genes with GO: {metrics.get('canonical_genes_with_go', 0)}",
        f"- Unique GO terms: {metrics.get('go_term_count', 0)}",
        f"- Bioconductor status: {metrics.get('bioc_status', 'skipped')}",
        f"- Bioconductor species: {metrics.get('bioc_species', '')}",
        f"- Bioconductor source: {metrics.get('bioc_source', '')}",
        f"- KofamScan status: {metrics.get('kofamscan_status', 'not_run')}",
        f"- Canonical genes with KO: {metrics.get('canonical_genes_with_ko', 0)}",
        f"- Unique KO terms: {metrics.get('ko_term_count', 0)}",
        "",
        "## Output Files",
    ]
    for artifact in artifacts.values():
        rel = artifact.get("path") or artifact.get("full_path") or ""
        lines.append(f"- {rel}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rel_artifact(workspace: Path, path: Path, file_type: str) -> dict[str, str]:
    try:
        rel = str(path.relative_to(workspace))
    except ValueError:
        rel = str(path)
    return {"path": rel, "full_path": str(path.resolve()), "type": file_type}


def write_reproducibility(
    analysis_dir: Path,
    commands: list[list[str]],
    files: list[Path],
    annotation_scope: str = "both",
) -> tuple[Path, Path, Path]:
    infix = f".{annotation_scope}" if annotation_scope in {"go", "kegg"} else ""
    commands_file = analysis_dir / f"commands{infix}.sh"
    with commands_file.open("w", encoding="utf-8") as handle:
        handle.write("#!/bin/bash\nset -euo pipefail\n\n")
        for cmd in commands:
            handle.write(" ".join(subprocess.list2cmdline([part]) for part in cmd) + "\n")
    os.chmod(commands_file, 0o755)

    env_file = analysis_dir / f"environment{infix}.yml"
    env_file.write_text(
        "name: annotation\n"
        "channels:\n"
        "  - bioconda\n"
        "  - conda-forge\n"
        "dependencies:\n"
        "  - interproscan\n",
        encoding="utf-8",
    )

    checksums_file = analysis_dir / f"checksums{infix}.sha256"
    with checksums_file.open("w", encoding="utf-8") as handle:
        for path in files:
            if path.exists() and path.is_file():
                handle.write(f"{compute_file_hash(path)}  {path}\n")

    return commands_file, env_file, checksums_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local GO annotation with InterProScan.")
    parser.add_argument("-i", "--input", required=True, help="Protein FASTA")
    parser.add_argument("-o", "--workspace", required=True, help="Workspace root: workspace/<job_id>")
    parser.add_argument("-p", "--prefix", required=True, help="Analysis prefix")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("-t", "--cpu", type=int, default=8, help="CPU cores")
    parser.add_argument("--interproscan", default=DEFAULT_INTERPROSCAN, help="interproscan.sh path")
    parser.add_argument(
        "--applications",
        default="Pfam,ProSiteProfiles",
        help="Comma-separated InterProScan applications. Empty means all available analyses.",
    )
    parser.add_argument("--kofam-config", default=DEFAULT_KOFAM_CONFIG, help="KofamScan config.yml path")
    parser.add_argument("--skip-go", action="store_true", help="Skip InterProScan GO annotation")
    parser.add_argument("--skip-kegg", action="store_true", help="Skip KofamScan KO annotation")
    parser.add_argument("--annotation-scope", choices=["auto", "both", "go", "kegg"], default="auto", help="Internal state scope for automatic background annotation")
    parser.add_argument("--require-kegg", action="store_true", help="Fail if KofamScan is unavailable or finds no KO")
    parser.add_argument("--skip-existing", action="store_true", help="Reuse existing InterProScan TSV if present")
    parser.add_argument("--skip-existing-kegg", action="store_true", help="Reuse existing KofamScan detail TSV if present")
    parser.add_argument("--species-name", default="", help="Species hint for Bioconductor gene2go, e.g. Arabidopsis thaliana or ath")
    parser.add_argument(
        "--bioc-source",
        choices=["auto", "biomart", "annotationhub", "off"],
        default="auto",
        help="Try Bioconductor gene2go before InterProScan for GO background",
    )
    parser.add_argument("--bioc-rscript", default=RNASEQ_RSCRIPT, help="Rscript binary with AnnotationHub/biomaRt installed")
    parser.add_argument("--bioc-helper", default=DEFAULT_BIOC_RSCRIPT, help="R helper script for Bioconductor gene2go")
    args = parser.parse_args()
    if args.skip_go and args.skip_kegg:
        parser.error("--skip-go and --skip-kegg cannot both be set")
    annotation_scope = infer_annotation_scope(args.annotation_scope, args.skip_go, args.skip_kegg)
    if args.annotation_scope == "both" and (args.skip_go or args.skip_kegg):
        parser.error("--annotation-scope both cannot be combined with --skip-go or --skip-kegg")
    if annotation_scope == "go":
        args.skip_kegg = True
    elif annotation_scope == "kegg":
        args.skip_go = True
    run_go = not args.skip_go
    run_kegg = not args.skip_kegg
    manifest_stage = "annotation" if annotation_scope == "both" else f"annotation_{annotation_scope}"

    start_time = now_iso()
    start = time.time()
    workspace = Path(args.workspace).resolve()
    protein_fasta = Path(args.input).resolve()
    interproscan = Path(args.interproscan)
    paths = prepare_dirs(workspace)
    analysis_dir = paths["analysis_dir"]
    final_dir = paths["final_dir"]
    progress_file = analysis_dir / (f"{annotation_scope}.progress.json" if annotation_scope in {"go", "kegg"} else "progress.json")
    manifest_name = f"{args.prefix}.annotation_{annotation_scope}.manifest.json" if annotation_scope in {"go", "kegg"} else f"{args.prefix}.annotation.manifest.json"
    manifest_path = final_dir / manifest_name

    output_base = analysis_dir / f"{args.prefix}.interproscan"
    tsv_path = Path(str(output_base) + ".tsv")
    xml_path = Path(str(output_base) + ".xml")
    gff3_path = Path(str(output_base) + ".gff3")
    prefixed_gene2go = final_dir / f"{args.prefix}.gene2go.txt"
    compatibility_gene2go = final_dir / "gene2go.txt"
    raw_gene2go_file = analysis_dir / f"{args.prefix}.raw_id.gene2go.txt"
    bioc_gene2go_file = analysis_dir / f"{args.prefix}.bioc.gene2go.txt"
    bioc_report_file = analysis_dir / f"{args.prefix}.bioc.gene2go.report.json"
    bioc_cache_dir = analysis_dir / "bioc_cache"
    kofam_detail_file = analysis_dir / f"{args.prefix}.kofamscan.detail.tsv"
    raw_gene2ko_file = analysis_dir / f"{args.prefix}.raw_id.gene2ko.txt"
    prefixed_gene2ko = final_dir / f"{args.prefix}.gene2ko.txt"
    compatibility_gene2ko = final_dir / "gene2ko.txt"
    gene_id_map = analysis_dir / (f"{args.prefix}.{annotation_scope}.gene_id_map.tsv" if annotation_scope in {"go", "kegg"} else f"{args.prefix}.gene_id_map.tsv")
    summary_file = final_dir / scoped_name(args.prefix, "annotation", annotation_scope, "_summary.tsv")
    report_path = final_dir / scoped_name(args.prefix, "annotation", annotation_scope, "_report.md")
    clean_protein_fasta = analysis_dir / (f"{args.prefix}.{annotation_scope}.annotation_safe.pep.fasta" if annotation_scope in {"go", "kegg"} else f"{args.prefix}.annotation_safe.pep.fasta")

    commands: list[list[str]] = []
    status = "success"
    error = ""
    sanitize_stats: dict[str, Any] = {}
    interproscan_run: dict[str, Any] = {}
    kofamscan_run: dict[str, Any] = {}
    protein_ids: list[str] = []
    method_used = "kofamscan" if not run_go else ("interproscan" if not run_kegg else "interproscan+kofamscan")

    try:
        if not protein_fasta.exists() or protein_fasta.stat().st_size == 0:
            raise FileNotFoundError(f"Protein FASTA not found or empty: {protein_fasta}")

        protein_ids = read_fasta_ids(protein_fasta)
        sanitize_stats = sanitize_protein_fasta_for_local_annotation(protein_fasta, clean_protein_fasta)
        protein_fasta_for_tools = clean_protein_fasta
        if sanitize_stats.get("removed_asterisks", 0):
            print(
                "Sanitized protein FASTA for InterProScan/KofamScan: "
                f"removed {sanitize_stats['removed_asterisks']} '*' from "
                f"{sanitize_stats['modified_sequences']} sequences -> {clean_protein_fasta}"
            )
        raw_gene2ko: dict[str, set[str]] = {}
        canonical_gene2ko: dict[str, set[str]] = {}
        raw_gene2go: dict[str, set[str]] = {}
        canonical_gene2go: dict[str, set[str]] = {}
        gene2ipr: dict[str, set[str]] = {}
        rows = 0
        raw_pairs = 0
        pairs = 0
        kofam_rows = 0
        kofam_hits = 0
        kofam_status = "skipped"
        kofam_message = ""
        raw_ko_pairs = 0
        ko_pairs = 0
        kofam_future: Future[dict[str, Any]] | None = None
        interpro_future: Future[dict[str, Any]] | None = None
        bioc_go_available = False
        bioc_status = "skipped"
        bioc_message = ""
        bioc_source_used = ""
        bioc_resource = ""
        bioc_rows = 0
        bioc_species = normalize_species_hint(args.species_name) or infer_species_hint(args.prefix)

        write_progress(
            progress_file,
            {
                "stage": manifest_stage,
                "status": "running",
                "method": method_used,
                "progress": 5,
                "message": f"starting {method_used} background annotation",
                "total": len(protein_ids),
                "processed": 0,
                "updated_at": now_iso(),
            },
        )

        if run_go and args.bioc_source != "off" and bioc_species:
            rscript_bin = Path(args.bioc_rscript)
            helper_script = Path(args.bioc_helper)
            if rscript_bin.exists() and helper_script.exists():
                write_progress(
                    progress_file,
                    {
                        "stage": manifest_stage,
                        "status": "running",
                        "method": "bioconductor",
                        "progress": 15,
                        "message": f"trying Bioconductor gene2go for {bioc_species}",
                        "total": len(protein_ids),
                        "processed": 0,
                        "updated_at": now_iso(),
                    },
                )
                bioc_result = run_bioc_gene2go(
                    bioc_species,
                    bioc_gene2go_file,
                    bioc_report_file,
                    bioc_cache_dir,
                    args.bioc_source,
                    rscript_bin,
                    helper_script,
                )
                commands.append(bioc_result["cmd"])
                (analysis_dir / f"{args.prefix}.bioc_gene2go.stdout.log").write_text(
                    bioc_result["stdout"], encoding="utf-8", errors="ignore"
                )
                (analysis_dir / f"{args.prefix}.bioc_gene2go.stderr.log").write_text(
                    bioc_result["stderr"], encoding="utf-8", errors="ignore"
                )
                raw_gene2go, canonical_gene2go, bioc_rows = parse_gene2go_tsv(bioc_gene2go_file, protein_ids)
                report = bioc_result.get("report") or {}
                bioc_source_used = str(report.get("source") or "")
                bioc_resource = str(report.get("resource") or "")
                if canonical_gene2go:
                    bioc_status = "success"
                    bioc_go_available = True
                    method_used = "bioconductor" if not run_kegg else "bioconductor+kofamscan"
                else:
                    bioc_status = "failed"
                    bioc_message = str(
                        report.get("error")
                        or bioc_result["stderr"]
                        or "Bioconductor returned GO mappings, but none matched the uploaded protein IDs"
                    )
            else:
                bioc_status = "unavailable"
                missing = rscript_bin if not rscript_bin.exists() else helper_script
                bioc_message = f"Bioconductor helper unavailable: {missing}"

        if run_go and not bioc_go_available and not interproscan.exists():
            raise FileNotFoundError(f"InterProScan executable not found: {interproscan}")

        interpro_needs_run = (
            run_go
            and not bioc_go_available
            and not (args.skip_existing and tsv_path.exists() and tsv_path.stat().st_size > 0)
        )
        kofam_needs_run = False
        kofam_ready = False
        kofam_reason = ""
        if run_kegg:
            kofam_ready, kofam_reason = kofam_config_ready(Path(args.kofam_config))
            if kofam_ready:
                kofam_needs_run = not (
                    args.skip_existing_kegg and kofam_detail_file.exists() and kofam_detail_file.stat().st_size > 0
                )
            else:
                kofam_status = "unavailable"
                kofam_message = kofam_reason
                if args.require_kegg:
                    raise RuntimeError(kofam_reason)

        with ThreadPoolExecutor(max_workers=2) as executor:
            if interpro_needs_run:
                interpro_run_id, interpro_run_dir = make_interproscan_run_dir(analysis_dir, args.prefix, annotation_scope)
                run_output_base = interpro_run_dir / interpro_run_id
                scratch_dir = make_interproscan_scratch_dir(args.job_id, interpro_run_id)
                temp_dir = scratch_dir / "tmp"
                temp_dir.mkdir(parents=True, exist_ok=True)
                interproscan_run = {
                    "run_id": interpro_run_id,
                    "run_dir": str(interpro_run_dir),
                    "output_base": str(run_output_base),
                    "scratch_dir": str(scratch_dir),
                    "temp_dir": str(temp_dir),
                    "tmp_root": str(interproscan_tmp_root()),
                    "stdout_log": str(interpro_run_dir / f"{interpro_run_id}.stdout.log"),
                    "stderr_log": str(interpro_run_dir / f"{interpro_run_id}.stderr.log"),
                    "canonical_stdout_log": str(analysis_dir / f"{args.prefix}.interproscan.stdout.log"),
                    "canonical_stderr_log": str(analysis_dir / f"{args.prefix}.interproscan.stderr.log"),
                }
                interpro_future = executor.submit(
                    run_interproscan,
                    protein_fasta_for_tools,
                    run_output_base,
                    interproscan,
                    args.cpu,
                    args.applications.strip(),
                    temp_dir,
                )
            else:
                interpro_future = None
                if run_go:
                    commands.append(["# reused existing", str(tsv_path)])

            if run_kegg and kofam_ready:
                if kofam_needs_run:
                    kofam_run_id, kofam_run_dir = make_annotation_run_dir(analysis_dir, args.prefix, annotation_scope, "kofamscan")
                    kofam_output_file = kofam_run_dir / f"{kofam_run_id}.detail.tsv"
                    kofam_temp = kofam_run_dir / "tmp"
                    kofam_temp.mkdir(parents=True, exist_ok=True)
                    kofamscan_run = {
                        "run_id": kofam_run_id,
                        "run_dir": str(kofam_run_dir),
                        "output_file": str(kofam_output_file),
                        "temp_dir": str(kofam_temp),
                        "stdout_log": str(kofam_run_dir / f"{kofam_run_id}.stdout.log"),
                        "stderr_log": str(kofam_run_dir / f"{kofam_run_id}.stderr.log"),
                        "canonical_stdout_log": str(analysis_dir / f"{args.prefix}.kofamscan.stdout.log"),
                        "canonical_stderr_log": str(analysis_dir / f"{args.prefix}.kofamscan.stderr.log"),
                    }
                    kofam_future = executor.submit(
                        run_kofamscan,
                        protein_fasta_for_tools,
                        kofam_output_file,
                        Path(args.kofam_config),
                        args.cpu,
                        kofam_temp,
                    )
                else:
                    commands.append(["# reused existing", str(kofam_detail_file)])
                    kofam_status = "success"

            if interpro_future is not None:
                run_result = interpro_future.result()
                commands.append(run_result["cmd"])
                stdout_log = Path(interproscan_run["stdout_log"])
                stderr_log = Path(interproscan_run["stderr_log"])
                canonical_stdout_log = Path(interproscan_run["canonical_stdout_log"])
                canonical_stderr_log = Path(interproscan_run["canonical_stderr_log"])
                stdout_log.write_text(run_result["stdout"], encoding="utf-8", errors="ignore")
                stderr_log.write_text(run_result["stderr"], encoding="utf-8", errors="ignore")
                shutil.copy2(stdout_log, canonical_stdout_log)
                shutil.copy2(stderr_log, canonical_stderr_log)
                interproscan_run["returncode"] = run_result["returncode"]
                interproscan_run["elapsed_sec"] = run_result["elapsed_sec"]
                if run_result["returncode"] != 0:
                    raise RuntimeError(
                        f"InterProScan failed with exit code {run_result['returncode']}. "
                        f"See {stderr_log}"
                    )
                copied_outputs = copy_interproscan_outputs(Path(interproscan_run["output_base"]), output_base)
                interproscan_run["copied_outputs"] = {key: str(path) for key, path in copied_outputs.items()}
                if not tsv_path.exists() or tsv_path.stat().st_size == 0:
                    raise RuntimeError(
                        "InterProScan completed but did not create a non-empty TSV output. "
                        f"Run directory: {interproscan_run['run_dir']}"
                    )
                shutil.rmtree(Path(interproscan_run["scratch_dir"]), ignore_errors=True)
                interproscan_run["scratch_cleaned"] = True

            write_progress(
                progress_file,
                {
                    "stage": manifest_stage,
                    "status": "running",
                    "method": method_used,
                    "progress": 70 if kofam_future is not None else 80,
                    "message": "parsing annotation outputs",
                    "total": len(protein_ids),
                    "processed": len(protein_ids),
                    "updated_at": now_iso(),
                },
            )

            write_gene_id_map(gene_id_map, protein_ids)
            if run_go:
                if bioc_go_available:
                    rows = bioc_rows
                else:
                    raw_gene2go, canonical_gene2go, gene2ipr, rows = parse_interproscan_tsv(tsv_path)
                raw_pairs = write_gene2go(raw_gene2go_file, raw_gene2go)
                pairs = write_gene2go(prefixed_gene2go, canonical_gene2go)
                if canonical_gene2go and prefixed_gene2go.exists():
                    shutil.copy2(prefixed_gene2go, compatibility_gene2go)

            if kofam_future is not None:
                write_progress(
                    progress_file,
                    {
                        "stage": manifest_stage,
                        "status": "running",
                        "method": method_used,
                        "progress": 88,
                        "message": "waiting for KofamScan KO annotation",
                        "total": len(protein_ids),
                        "processed": len(protein_ids),
                        "updated_at": now_iso(),
                    },
                )
                kofam_result = kofam_future.result()
                commands.append(kofam_result["cmd"])
                if kofamscan_run:
                    kofam_stdout_log = Path(kofamscan_run["stdout_log"])
                    kofam_stderr_log = Path(kofamscan_run["stderr_log"])
                    kofam_stdout_log.write_text(kofam_result["stdout"], encoding="utf-8", errors="ignore")
                    kofam_stderr_log.write_text(kofam_result["stderr"], encoding="utf-8", errors="ignore")
                    shutil.copy2(kofam_stdout_log, Path(kofamscan_run["canonical_stdout_log"]))
                    shutil.copy2(kofam_stderr_log, Path(kofamscan_run["canonical_stderr_log"]))
                    kofamscan_run["returncode"] = kofam_result["returncode"]
                    kofamscan_run["elapsed_sec"] = kofam_result["elapsed_sec"]
                else:
                    (analysis_dir / f"{args.prefix}.kofamscan.stdout.log").write_text(
                        kofam_result["stdout"], encoding="utf-8", errors="ignore"
                    )
                    (analysis_dir / f"{args.prefix}.kofamscan.stderr.log").write_text(
                        kofam_result["stderr"], encoding="utf-8", errors="ignore"
                    )
                if kofam_result["returncode"] != 0:
                    kofam_status = "failed"
                    stderr_path = (
                        Path(kofamscan_run["stderr_log"])
                        if kofamscan_run.get("stderr_log")
                        else analysis_dir / (args.prefix + ".kofamscan.stderr.log")
                    )
                    kofam_message = (
                        f"KofamScan failed with exit code {kofam_result['returncode']}. "
                        f"See {stderr_path}"
                    )
                    if args.require_kegg:
                        raise RuntimeError(kofam_message)
                else:
                    kofam_status = "success"
                    if kofamscan_run.get("output_file"):
                        run_kofam_output = Path(kofamscan_run["output_file"])
                        if run_kofam_output.exists() and run_kofam_output.stat().st_size > 0:
                            shutil.copy2(run_kofam_output, kofam_detail_file)
                            kofamscan_run["copied_output"] = str(kofam_detail_file)

        if run_kegg and kofam_ready and kofam_detail_file.exists() and kofam_detail_file.stat().st_size > 0:
            raw_gene2ko, canonical_gene2ko, kofam_rows, kofam_hits = parse_kofamscan_detail_tsv(kofam_detail_file)
            raw_ko_pairs = write_gene2terms(raw_gene2ko_file, raw_gene2ko)
            ko_pairs = write_gene2terms(prefixed_gene2ko, canonical_gene2ko)
            if canonical_gene2ko and prefixed_gene2ko.exists():
                shutil.copy2(prefixed_gene2ko, compatibility_gene2ko)
            if not canonical_gene2ko:
                kofam_status = "no_hits" if kofam_status == "success" else kofam_status
                kofam_message = kofam_message or "KofamScan completed but no high-confidence KO hits were parsed."
                if args.require_kegg:
                    raise RuntimeError(kofam_message)

        write_summary(summary_file, canonical_gene2go, gene2ipr, canonical_gene2ko)

        go_terms = set()
        for terms in canonical_gene2go.values():
            go_terms.update(terms)
        ko_terms = set()
        for terms in canonical_gene2ko.values():
            ko_terms.update(terms)

        metrics = {
            "annotation_scope": annotation_scope,
            "background_kind": annotation_scope,
            "input_proteins": len(protein_ids),
            "interproscan_rows": rows,
            "raw_genes_with_go": len(raw_gene2go),
            "canonical_genes_with_go": len(canonical_gene2go),
            "go_term_count": len(go_terms),
            "gene2go_pairs": pairs,
            "raw_gene2go_pairs": raw_pairs,
            "bioc_status": bioc_status,
            "bioc_message": bioc_message,
            "bioc_species": bioc_species,
            "bioc_source": bioc_source_used,
            "bioc_resource": bioc_resource,
            "bioc_rows": bioc_rows,
            "kofamscan_status": kofam_status,
            "kofamscan_message": kofam_message,
            "kofamscan_rows": kofam_rows,
            "kofamscan_high_confidence_hits": kofam_hits,
            "raw_genes_with_ko": len(raw_gene2ko),
            "canonical_genes_with_ko": len(canonical_gene2ko),
            "ko_term_count": len(ko_terms),
            "gene2ko_pairs": ko_pairs,
            "raw_gene2ko_pairs": raw_ko_pairs,
            "method_used": method_used,
            "id_normalization": "strip .T<digits> and .mRNA<digits> transcript suffixes",
            "sanitized_fasta_removed_asterisks": sanitize_stats.get("removed_asterisks", 0),
            "sanitized_fasta_modified_sequences": sanitize_stats.get("modified_sequences", 0),
            "interproscan_run": interproscan_run,
            "kofamscan_run": kofamscan_run,
        }

        artifacts = {
            "annotation_safe_fasta": rel_artifact(workspace, clean_protein_fasta, "fasta"),
            "interproscan_tsv": rel_artifact(workspace, tsv_path, "tsv") if tsv_path.exists() else {},
            "interproscan_xml": rel_artifact(workspace, xml_path, "xml") if xml_path.exists() else {},
            "interproscan_gff3": rel_artifact(workspace, gff3_path, "gff3") if gff3_path.exists() else {},
            "interproscan_run_dir": rel_artifact(workspace, Path(interproscan_run["run_dir"]), "directory") if interproscan_run.get("run_dir") else {},
            "interproscan_stdout": rel_artifact(workspace, Path(interproscan_run["stdout_log"]), "log") if interproscan_run.get("stdout_log") else {},
            "interproscan_stderr": rel_artifact(workspace, Path(interproscan_run["stderr_log"]), "log") if interproscan_run.get("stderr_log") else {},
            "bioc_gene2go_raw": rel_artifact(workspace, bioc_gene2go_file, "tsv") if bioc_gene2go_file.exists() else {},
            "bioc_gene2go_report": rel_artifact(workspace, bioc_report_file, "json") if bioc_report_file.exists() else {},
            "gene2go": rel_artifact(workspace, prefixed_gene2go, "background_go") if prefixed_gene2go.exists() else {},
            "gene2go_compat": rel_artifact(workspace, compatibility_gene2go, "background_go") if compatibility_gene2go.exists() else {},
            "raw_id_gene2go": rel_artifact(workspace, raw_gene2go_file, "tsv") if raw_gene2go_file.exists() else {},
            "kofamscan_detail": rel_artifact(workspace, kofam_detail_file, "tsv") if kofam_detail_file.exists() else {},
            "kofamscan_run_dir": rel_artifact(workspace, Path(kofamscan_run["run_dir"]), "directory") if kofamscan_run.get("run_dir") else {},
            "kofamscan_stdout": rel_artifact(workspace, Path(kofamscan_run["stdout_log"]), "log") if kofamscan_run.get("stdout_log") else {},
            "kofamscan_stderr": rel_artifact(workspace, Path(kofamscan_run["stderr_log"]), "log") if kofamscan_run.get("stderr_log") else {},
            "gene2ko": rel_artifact(workspace, prefixed_gene2ko, "background_ko") if prefixed_gene2ko.exists() else {},
            "gene2ko_compat": rel_artifact(workspace, compatibility_gene2ko, "background_ko") if compatibility_gene2ko.exists() else {},
            "raw_id_gene2ko": rel_artifact(workspace, raw_gene2ko_file, "tsv") if raw_gene2ko_file.exists() else {},
            "gene_id_map": rel_artifact(workspace, gene_id_map, "tsv"),
            "summary": rel_artifact(workspace, summary_file, "tsv"),
            "report": rel_artifact(workspace, report_path, "markdown"),
            "manifest": rel_artifact(workspace, manifest_path, "json"),
        }
        artifacts = {k: v for k, v in artifacts.items() if v}

        write_report(report_path, args, metrics, artifacts)
        repro_files = [
            protein_fasta,
            clean_protein_fasta,
            tsv_path,
            xml_path,
            gff3_path,
            prefixed_gene2go,
            compatibility_gene2go,
            raw_gene2go_file,
            kofam_detail_file,
            prefixed_gene2ko,
            compatibility_gene2ko,
            raw_gene2ko_file,
            gene_id_map,
            summary_file,
            report_path,
        ]
        commands_file, env_file, checksums_file = write_reproducibility(analysis_dir, commands, repro_files, annotation_scope)
        artifacts["commands"] = rel_artifact(workspace, commands_file, "shell")
        artifacts["environment"] = rel_artifact(workspace, env_file, "yaml")
        artifacts["checksums"] = rel_artifact(workspace, checksums_file, "sha256")

        if run_go and not canonical_gene2go:
            status = "failed"
            error = "GO annotation completed but no GO terms were parsed."

    except Exception as exc:
        status = "failed"
        error = str(exc)
        metrics = {
            "annotation_scope": annotation_scope,
            "background_kind": annotation_scope,
            "method_used": method_used,
            "input_proteins": len(protein_ids),
            "bioc_status": locals().get("bioc_status", "skipped"),
            "bioc_message": locals().get("bioc_message", ""),
            "sanitized_fasta_removed_asterisks": sanitize_stats.get("removed_asterisks", 0),
            "sanitized_fasta_modified_sequences": sanitize_stats.get("modified_sequences", 0),
            "interproscan_run": interproscan_run,
            "kofamscan_run": kofamscan_run,
        }
        artifacts = {
            "manifest": rel_artifact(workspace, manifest_path, "json"),
            "interproscan_run_dir": rel_artifact(workspace, Path(interproscan_run["run_dir"]), "directory") if interproscan_run.get("run_dir") else {},
            "interproscan_stdout": rel_artifact(workspace, Path(interproscan_run["stdout_log"]), "log") if interproscan_run.get("stdout_log") else {},
            "interproscan_stderr": rel_artifact(workspace, Path(interproscan_run["stderr_log"]), "log") if interproscan_run.get("stderr_log") else {},
            "kofamscan_run_dir": rel_artifact(workspace, Path(kofamscan_run["run_dir"]), "directory") if kofamscan_run.get("run_dir") else {},
            "kofamscan_stdout": rel_artifact(workspace, Path(kofamscan_run["stdout_log"]), "log") if kofamscan_run.get("stdout_log") else {},
            "kofamscan_stderr": rel_artifact(workspace, Path(kofamscan_run["stderr_log"]), "log") if kofamscan_run.get("stderr_log") else {},
        }
        artifacts = {k: v for k, v in artifacts.items() if v}

    end_time = now_iso()
    manifest = {
        "manifest_version": "1.0",
        "stage": manifest_stage,
        "status": status,
        "prefix": args.prefix,
        "job_id": args.job_id,
        "timestamp": end_time,
        "start_time": start_time,
        "end_time": end_time,
        "elapsed_sec": round(time.time() - start, 3),
        "workspace_root": str(workspace),
        "workspace": str((workspace / "annotation").resolve()),
        "input": {
            "protein_fasta": str(protein_fasta),
            "annotation_safe_fasta": str(clean_protein_fasta) if clean_protein_fasta.exists() else "",
        },
        "parameters": {
            "cpu": args.cpu,
            "prefix": args.prefix,
            "method": method_used,
            "annotation_scope": annotation_scope,
            "interproscan": str(interproscan),
            "applications": args.applications,
            "kofam_config": str(Path(args.kofam_config).resolve()),
            "skip_kegg": args.skip_kegg,
            "skip_go": args.skip_go,
            "require_kegg": args.require_kegg,
            "species_name": args.species_name,
            "bioc_source": args.bioc_source,
            "bioc_rscript": args.bioc_rscript,
            "interproscan_run": interproscan_run,
            "kofamscan_run": kofamscan_run,
        },
        "metrics": metrics,
        "artifacts": artifacts,
        "cmd": commands,
        "error": error,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_progress(
        progress_file,
        {
            "stage": manifest_stage,
            "status": status,
            "method": method_used,
            "progress": 100,
            "message": error or "annotation completed",
            "total": metrics.get("input_proteins", 0),
            "processed": metrics.get("input_proteins", 0),
            "annotated": metrics.get("canonical_genes_with_go", 0),
            "ko_annotated": metrics.get("canonical_genes_with_ko", 0),
            "updated_at": now_iso(),
        },
    )

    if status == "success":
        print(f"Annotation completed: {manifest_path}")
        return 0
    print(f"Annotation failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
