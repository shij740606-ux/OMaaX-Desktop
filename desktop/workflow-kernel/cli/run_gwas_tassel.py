#!/usr/bin/env python3
"""Sort an external VCF and convert it to TASSEL HapMap format."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from gfa_core.genotype_context import genotype_scope_id_from_sha256, sha256_file


STAGE = "gwas_fmt"
SOURCE_LABEL = "18_gwas_tassel_convert"


def _open_text(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _vcf_shape(path: Path) -> tuple[list[str], int]:
    samples: list[str] | None = None
    variants = 0
    with _open_text(path) as handle:
        for raw in handle:
            if raw.startswith("#CHROM\t"):
                samples = raw.rstrip("\r\n").split("\t")[9:]
            elif raw and not raw.startswith("#"):
                variants += 1
    if samples is None:
        raise ValueError(f"VCF header #CHROM was not found: {path}")
    return samples, variants


def _vcf_genotypes(path: Path) -> tuple[list[str], dict[tuple[str, str, str, str], tuple[str, ...]]]:
    samples: list[str] | None = None
    records: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    with _open_text(path) as handle:
        for line_number, raw in enumerate(handle, start=1):
            if raw.startswith("#CHROM\t"):
                columns = raw.rstrip("\r\n").split("\t")
                samples = columns[9:]
                continue
            if not raw.strip() or raw.startswith("#"):
                continue
            fields = raw.rstrip("\r\n").split("\t")
            if len(fields) < 9:
                raise ValueError(f"Malformed VCF record at line {line_number}")
            key = (fields[0], fields[1], fields[3], fields[4])
            if key in records:
                raise ValueError(f"Duplicate VCF variant key {key} in {path}")
            format_fields = fields[8].split(":")
            try:
                gt_index = format_fields.index("GT")
            except ValueError as exc:
                raise ValueError(f"VCF record has no GT field at {fields[0]}:{fields[1]}") from exc
            calls = []
            for sample_field in fields[9:]:
                values = sample_field.split(":")
                gt = values[gt_index] if gt_index < len(values) else "."
                calls.append(_canonical_gt(gt))
            records[key] = tuple(calls)
    if samples is None:
        raise ValueError(f"VCF header #CHROM was not found: {path}")
    if not samples:
        raise ValueError(f"VCF must contain at least one sample: {path}")
    if not records:
        raise ValueError(f"VCF must contain at least one variant: {path}")
    return samples, records


def _canonical_gt(value: str) -> str:
    raw = str(value or ".").replace("|", "/")
    tokens = raw.split("/")
    if not tokens or any(token in {"", "."} for token in tokens):
        return "."
    try:
        return "/".join(str(index) for index in sorted(int(token) for token in tokens))
    except ValueError as exc:
        raise ValueError(f"Invalid VCF GT value: {value}") from exc


_IUPAC = {
    frozenset(("A", "G")): "R", frozenset(("C", "T")): "Y",
    frozenset(("G", "C")): "S", frozenset(("A", "T")): "W",
    frozenset(("G", "T")): "K", frozenset(("A", "C")): "M",
}


def _sample_index_map(input_samples: list[str], converted_samples: list[str]) -> list[int]:
    """Map TASSEL's deterministic ``sample_sample`` aliases back to VCF IDs."""
    if len(input_samples) != len(converted_samples):
        raise RuntimeError("TASSEL conversion changed sample count")
    indexes: list[int] = []
    used: set[int] = set()
    for sample in input_samples:
        matches = [
            index for index, converted in enumerate(converted_samples)
            if converted == sample or converted == f"{sample}_{sample}"
        ]
        matches = [index for index in matches if index not in used]
        if len(matches) != 1:
            raise RuntimeError(f"TASSEL conversion changed or ambiguously renamed sample: {sample}")
        indexes.append(matches[0])
        used.add(matches[0])
    return indexes


def _gt_to_hapmap(gt: str, ref: str, alt: str) -> str:
    if gt == ".":
        return "N"
    alleles = [ref, *alt.split(",")]
    try:
        bases = [alleles[int(token)].upper() for token in gt.split("/")]
    except (IndexError, ValueError):
        return "N"
    if any(len(base) != 1 or base not in {"A", "C", "G", "T"} for base in bases):
        return "N"
    unique = frozenset(bases)
    if len(unique) == 1:
        return next(iter(unique))
    return _IUPAC.get(unique, "N")


def _strict_hapmap_marker(key: tuple[str, str, str, str], genotypes: tuple[str, ...]) -> bool:
    """Return whether a VCF marker is reversibly representable in HapMap."""
    _chrom, _pos, ref, alt = key
    ref = ref.upper()
    alt = alt.upper()
    if len(ref) != 1 or ref not in {"A", "C", "G", "T"}:
        return False
    if "," in alt or len(alt) != 1 or alt not in {"A", "C", "G", "T"} or alt == ref:
        return False
    for gt in genotypes:
        if gt == ".":
            continue
        tokens = gt.split("/")
        if len(tokens) != 2 or any(token not in {"0", "1"} for token in tokens):
            return False
    return True


def _marker_audit(records: dict[tuple[str, str, str, str], tuple[str, ...]]) -> dict[str, object]:
    strict = {key for key, genotypes in records.items() if _strict_hapmap_marker(key, genotypes)}
    unsupported = set(records) - strict
    return {
        "strict_keys": strict,
        "unsupported_keys": unsupported,
        "strict_marker_count": len(strict),
        "unsupported_marker_count": len(unsupported),
        "lossy_conversion": bool(unsupported),
    }


def _hapmap_genotypes(path: Path) -> tuple[list[str], dict[tuple[str, str], tuple[str, tuple[str, ...]]]]:
    samples, _count = _hapmap_shape(path)
    records: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    with path.open("r", encoding="utf-8") as handle:
        next(handle)
        for line_number, raw in enumerate(handle, start=2):
            if not raw.strip():
                continue
            fields = raw.rstrip("\r\n").split("\t")
            if len(fields) < 11 + len(samples):
                raise ValueError(f"Malformed HapMap record at line {line_number}")
            key = (fields[2], fields[3])
            if key in records:
                raise ValueError(f"Duplicate HapMap locus {key} in {path}")
            records[key] = (fields[1], tuple(value.upper() for value in fields[11:]))
    return samples, records


def _prove_genotype_conservation(input_vcf: Path, sorted_vcf: Path, hapmap: Path) -> dict[str, object]:
    input_samples, input_records = _vcf_genotypes(input_vcf)
    sorted_samples, sorted_records = _vcf_genotypes(sorted_vcf)
    sorted_indexes = _sample_index_map(input_samples, sorted_samples)
    normalized_sorted: dict[tuple[str, str], tuple[tuple[str, str, str, str], tuple[str, ...]]] = {}
    for sorted_key, calls in sorted_records.items():
        locus = (sorted_key[0].casefold(), sorted_key[1])
        if locus in normalized_sorted:
            raise RuntimeError(f"TASSEL sorted VCF produced ambiguous normalized locus {sorted_key[0]}:{sorted_key[1]}")
        normalized_sorted[locus] = (sorted_key, calls)
    if {(key[0].casefold(), key[1]) for key in input_records} != set(normalized_sorted):
        raise RuntimeError("TASSEL sorted VCF changed chrom,pos locus identity")
    marker_audit = _marker_audit(input_records)
    strict_keys = marker_audit["strict_keys"]
    for key in strict_keys:
        expected = input_records[key]
        sorted_key, sorted_calls = normalized_sorted[(key[0].casefold(), key[1])]
        if sorted_key[2:] != key[2:]:
            raise RuntimeError(f"TASSEL sorted VCF changed strict SNP allele identity at {key[0]}:{key[1]}")
        observed_sorted = tuple(sorted_calls[index] for index in sorted_indexes)
        if observed_sorted != expected:
            raise RuntimeError(f"TASSEL sorted VCF changed genotype calls at {key[0]}:{key[1]}")

    hapmap_samples, hapmap_records = _hapmap_genotypes(hapmap)
    hapmap_indexes = _sample_index_map(input_samples, hapmap_samples)
    normalized_input_loci = {(key[0].casefold(), key[1]) for key in input_records}
    normalized_hapmap: dict[tuple[str, str], tuple[str, str, tuple[str, ...]]] = {}
    for (chrom, pos), (alleles, calls) in hapmap_records.items():
        normalized_key = (chrom.casefold(), pos)
        if normalized_key in normalized_hapmap:
            raise RuntimeError(f"TASSEL HapMap produced ambiguous normalized locus {chrom}:{pos}")
        normalized_hapmap[normalized_key] = (chrom, alleles, calls)
    if normalized_input_loci != set(normalized_hapmap):
        raise RuntimeError("TASSEL HapMap changed chrom,pos locus identity")
    compared = 0
    normalized_contigs = 0
    for (chrom, pos, ref, alt), genotypes in input_records.items():
        observed_chrom, allele_text, raw_observed = normalized_hapmap[(chrom.casefold(), pos)]
        if observed_chrom != chrom:
            normalized_contigs += 1
        if (chrom, pos, ref, alt) not in strict_keys:
            continue
        observed = tuple(raw_observed[index] for index in hapmap_indexes)
        expected_alleles = {ref.upper(), *(item.upper() for item in alt.split(","))}
        observed_alleles = {item.upper() for item in allele_text.replace(",", "/").split("/") if item}
        if observed_alleles != expected_alleles:
            raise RuntimeError(f"TASSEL HapMap changed allele identity at {chrom}:{pos}")
        expected = tuple(_gt_to_hapmap(gt, ref, alt) for gt in genotypes)
        if observed != expected:
            raise RuntimeError(f"TASSEL HapMap genotype discordance at {chrom}:{pos}")
        compared += len(expected)
    return {
        "variants_compared": len(strict_keys),
        "genotypes_compared": compared,
        "strict_marker_count": marker_audit["strict_marker_count"],
        "unsupported_marker_count": marker_audit["unsupported_marker_count"],
        "normalized_contig_count": normalized_contigs,
        "strict_genotype_conservation": True,
        "lossy_conversion": marker_audit["lossy_conversion"],
    }


def _hapmap_shape(path: Path) -> tuple[list[str], int]:
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\r\n").split("\t")
        if len(header) < 11 or header[:4] != ["rs#", "alleles", "chrom", "pos"]:
            raise ValueError(f"Invalid TASSEL HapMap header: {path}")
        samples = header[11:]
        variants = sum(1 for raw in handle if raw.strip())
    return samples, variants


def _artifact(workspace: Path, path: Path, role: str, media_type: str) -> dict[str, object]:
    size = path.stat().st_size
    return {
        "artifact": role,
        "role": role,
        "path": str(path.relative_to(workspace)),
        "full_path": str(path.resolve()),
        "type": media_type,
        "media_type": media_type,
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(path),
    }


def _run(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except OSError:
        raise RuntimeError("TASSEL execution could not be started") from None
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"TASSEL execution failed with exit code {completed.returncode}")
    return output


def _public_failure_error(exc: Exception) -> str:
    text = str(exc).strip()
    malformed = re.match(r"^(Malformed (?:VCF|HapMap) record)(?: at line (\d+))?", text)
    if malformed:
        return f"{malformed.group(1)} at line {malformed.group(2)}" if malformed.group(2) else malformed.group(1)
    for private_prefix, public_error in (
        ("Invalid VCF GT value", "Invalid VCF GT value"),
        ("VCF record has no GT field", "VCF record has no GT field"),
        ("TASSEL conversion changed or ambiguously renamed sample", "TASSEL conversion changed or ambiguously renamed sample"),
        ("Duplicate VCF variant key", "Duplicate VCF variant key"),
        ("Duplicate HapMap locus", "Duplicate HapMap locus"),
        ("VCF header #CHROM was not found", "VCF header #CHROM was not found"),
        ("VCF must contain at least one sample", "VCF must contain at least one sample"),
        ("VCF must contain at least one variant", "VCF must contain at least one variant"),
        ("Invalid TASSEL HapMap header", "Invalid TASSEL HapMap header"),
        ("External VCF does not exist", "External VCF does not exist"),
    ):
        if text.startswith(private_prefix):
            return public_error
    safe_exact = {
        "TASSEL conversion changed sample count",
        "TASSEL sorted VCF changed chrom,pos locus identity",
        "TASSEL HapMap changed chrom,pos locus identity",
        "Mask policy cannot prove conversion: strict_marker_count is zero",
        "TASSEL pipeline executable was not found or is not executable",
        "TASSEL execution could not be started",
        "TASSEL completed without producing the sorted VCF and HapMap outputs",
        "TASSEL version could not be proven from executable or run log",
        "Mask conversion is unproven: strict_marker_count and genotypes_compared must both be positive",
    }
    if text in safe_exact:
        return text
    if re.fullmatch(r"TASSEL execution failed with exit code \d+", text):
        return text
    if re.fullmatch(r"\d+ unsupported HapMap marker\(s\) found under fail policy", text):
        return text
    if re.fullmatch(
        r"TASSEL conversion changed sample or variant cardinality: input=\d+/\d+, sorted=\d+/\d+, hapmap=\d+/\d+",
        text,
    ):
        return text
    for private_prefix in (
        "TASSEL sorted VCF produced ambiguous normalized locus",
        "TASSEL sorted VCF changed strict SNP allele identity",
        "TASSEL sorted VCF changed genotype calls",
        "TASSEL HapMap produced ambiguous normalized locus",
        "TASSEL HapMap changed allele identity",
        "TASSEL HapMap genotype discordance",
    ):
        if text.startswith(private_prefix):
            return private_prefix
    actionable = re.search(r"(?i)(?:line|exit code)\s+\d+", text)
    if actionable:
        return f"{exc.__class__.__name__}: {actionable.group(0)}"
    return "GWAS TASSEL conversion failed"


def _resolved_command(command: str) -> str:
    if command == "run_pipeline.pl":
        command = str(os.environ.get("TASSEL_PIPELINE") or command)
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or "/" in command:
        return str(candidate.resolve())
    return str(Path(shutil.which(command) or command))


def _version(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=10)
        text = (completed.stdout or completed.stderr or "").strip().splitlines()
        return text[0][:300] if text else "unknown"
    except Exception:
        return "unknown"


def _parse_tassel_version(text: str) -> str:
    for pattern in (
        r"(?i)TASSEL(?:\s+version)?\s*[:=]?\s*(\d+\.\d+(?:\.\d+)?)",
        r"(?i)version\s*[:=]?\s*(5\.\d+(?:\.\d+)?)",
    ):
        match = re.search(pattern, str(text or ""))
        if match:
            return match.group(1)
    return "unknown"


def _tassel_version(resolved_tassel: str, run_output: str = "") -> str:
    parsed = _parse_tassel_version(run_output)
    if parsed != "unknown":
        return parsed
    try:
        completed = subprocess.run(
            [resolved_tassel, "-version"], text=True, capture_output=True,
            check=False, timeout=10,
        )
        return _parse_tassel_version(f"{completed.stdout or ''}\n{completed.stderr or ''}")
    except Exception:
        return "unknown"


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_identity_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
    return token or "cohort"


def _write_reproducibility_bundle(
    *, workspace: Path, prefix: str, input_sha256: str,
    manifest_path: Path, manifest: dict[str, object],
    runner_command: list[str], environment_text: str,
    log_path: Path | None,
) -> Path:
    bundle_dir = workspace / "reproducibility" / STAGE / f"{_safe_identity_token(prefix)}.{input_sha256[:12]}"
    if bundle_dir.is_symlink() or bundle_dir.is_file():
        bundle_dir.unlink()
    elif bundle_dir.is_dir():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    canonical_runner = shlex.join(runner_command)
    (bundle_dir / "commands.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + canonical_runner + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "environment.txt").write_text(environment_text, encoding="utf-8")

    manifest_relative = str(manifest_path.relative_to(workspace))
    log_relative = _workspace_relative_or_absolute(workspace, log_path)
    environment_relative = str((bundle_dir / "environment.txt").relative_to(workspace))
    status = str(manifest.get("status") or "failed")
    progress = {
        "stage": STAGE,
        "status": status,
        "job_id": manifest.get("job_id", ""),
        "prefix": prefix,
        "genotype_scope_id": manifest.get("genotype_scope_id", ""),
        "input_sha256": input_sha256,
        "started_at": manifest.get("start_time", ""),
        "completed_at": manifest.get("end_time", ""),
        "manifest_path": manifest_relative,
        "log_path": log_relative,
        "message": manifest.get("error") if status == "failed" else f"{STAGE} completed successfully",
    }
    provenance = {
        "stage": STAGE,
        "status": status,
        "job_id": manifest.get("job_id", ""),
        "prefix": prefix,
        "genotype_scope_id": manifest.get("genotype_scope_id", ""),
        "input_sha256": input_sha256,
        "runner_command": canonical_runner,
        "manifest_path": manifest_relative,
        "log_path": log_relative,
        "environment_path": environment_relative,
        "software": manifest.get("software", {}),
        "artifacts": manifest.get("artifacts", {}),
    }
    _write_json(bundle_dir / "progress.json", progress)
    _write_json(bundle_dir / "provenance.json", provenance)

    checksum_targets = [manifest_path]
    artifacts = manifest.get("artifacts", {})
    if isinstance(artifacts, dict):
        for artifact in artifacts.values():
            if not isinstance(artifact, dict) or "path" not in artifact:
                continue
            candidate = workspace / str(artifact["path"])
            if candidate.is_file():
                checksum_targets.append(candidate)
    checksum_lines = [
        f"{sha256_file(path)}  {path.relative_to(workspace)}"
        for path in sorted(checksum_targets)
    ]
    (bundle_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return bundle_dir


def _workspace_relative_or_absolute(workspace: Path, path: Path | None) -> str:
    if path is None:
        return ""
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(workspace))
    except ValueError:
        return str(resolved)


def _runner_command(args: argparse.Namespace, workspace: Path, input_vcf: Path) -> list[str]:
    return [
        "python", "-m", "cli.run_gwas_tassel",
        "--workspace", str(workspace),
        "--job-id", args.job_id,
        "--prefix", args.prefix,
        "--vcf", str(input_vcf),
        "--xmx", args.xmx,
        "--unsupported-marker-policy", args.unsupported_marker_policy,
    ]


def _environment_text(
    *, resolved_tassel: str, tassel_version: str,
    resolved_java: str, java_version: str,
) -> str:
    return "\n".join([
        "runner=cli.run_gwas_tassel.py",
        f"runtime_environment={os.environ.get('CONDA_DEFAULT_ENV', 'unknown')}",
        f"python_resolved={Path(sys.executable).resolve()}",
        f"python_version={platform.python_version()}",
        "external_tool=TASSEL",
        f"external_launcher={resolved_tassel}",
        f"tassel_version={tassel_version}",
        f"java_resolved={resolved_java}",
        f"java_version={java_version}",
        f"platform={platform.platform()}",
    ]) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", "-p", required=True)
    parser.add_argument("--vcf", required=True, type=Path)
    parser.add_argument("--tassel-pipeline", default="run_pipeline.pl")
    parser.add_argument("--xmx", default="10g")
    parser.add_argument("--unsupported-marker-policy", choices=("mask", "fail"), default="mask")
    parser.add_argument("--log-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    input_vcf = args.vcf.resolve()
    final_dir = workspace / "final"
    stage_dir = workspace / STAGE
    final_dir.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = final_dir / f"{args.prefix}.{STAGE}.manifest.json"
    sorted_work = stage_dir / "all.sorted.vcf.gz"
    hapmap_base = stage_dir / "hapmap"
    hapmap_work = stage_dir / "hapmap.hmp.txt"
    sorted_final = final_dir / f"{args.prefix}.{STAGE}.sorted.vcf.gz"
    hapmap_final = final_dir / f"{args.prefix}.{STAGE}.hapmap.hmp.txt"
    environment_path = final_dir / f"{args.prefix}.{STAGE}.environment.txt"
    report_path = final_dir / f"{args.prefix}.{STAGE}.report.md"
    formal_paths = (sorted_final, hapmap_final, environment_path, report_path)
    for formal_path in formal_paths:
        formal_path.unlink(missing_ok=True)
    (final_dir / f"{args.prefix}.{STAGE}.commands.sh").unlink(missing_ok=True)
    (final_dir / f"{args.prefix}.{STAGE}.log.txt").unlink(missing_ok=True)

    digest = ""
    genotype_scope_id = ""
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    runner_command = _runner_command(args, workspace, input_vcf)
    environment_text = _environment_text(
        resolved_tassel="unknown", tassel_version="unknown",
        resolved_java="unknown", java_version="unknown",
    )
    manifest: dict[str, object] = {
        "manifest_version": "1.0",
        "stage": STAGE,
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": f"{STAGE}_{started_at.strftime('%Y%m%d_%H%M%S')}",
        "status": "failed",
        "timestamp": started_at.isoformat(),
        "start_time": started_at.isoformat(),
        "end_time": started_at.isoformat(),
        "elapsed_sec": 0.0,
        "cmd": runner_command,
        "tool_versions": {"tassel": "unknown", "java": "unknown", "python": platform.python_version()},
        "workspace_root": str(workspace),
        "workspace": str(final_dir),
        "input_dir": str(input_vcf.parent),
        "parameters": {
            "source_label": SOURCE_LABEL,
            "tassel_pipeline": args.tassel_pipeline,
            "xmx": args.xmx,
            "unsupported_marker_policy": args.unsupported_marker_policy,
        },
        "provenance": {
            "runner_command": shlex.join(runner_command),
            "log_path": _workspace_relative_or_absolute(workspace, args.log_file),
        },
        "metrics": {},
        "artifacts": {},
    }
    try:
        if not input_vcf.is_file():
            raise FileNotFoundError(f"External VCF does not exist: {input_vcf}")
        digest = sha256_file(input_vcf)
        genotype_scope_id = genotype_scope_id_from_sha256(digest)
        manifest["genotype_scope_id"] = genotype_scope_id
        manifest["input_sha256"] = digest
        manifest["inputs"] = [{
            "role": "external_vcf",
            "path": str(input_vcf),
            "size_bytes": input_vcf.stat().st_size,
            "sha256": digest,
        }]
        # Reject semantically invalid or empty cohorts before invoking TASSEL.
        _input_samples_for_audit, input_records_for_audit = _vcf_genotypes(input_vcf)
        marker_audit = _marker_audit(input_records_for_audit)
        manifest["metrics"] = {
            "strict_marker_count": marker_audit["strict_marker_count"],
            "unsupported_marker_count": marker_audit["unsupported_marker_count"],
            "lossy_conversion": marker_audit["lossy_conversion"],
        }
        warnings: list[str] = []
        if marker_audit["unsupported_marker_count"]:
            warnings.append(
                f"{marker_audit['unsupported_marker_count']} marker(s) are not reversibly representable in HapMap; "
                "strict genotype conservation applies only to the reversible diploid biallelic SNP subset."
            )
        manifest["warnings"] = warnings
        if args.unsupported_marker_policy == "fail" and marker_audit["unsupported_marker_count"]:
            raise ValueError(
                f"{marker_audit['unsupported_marker_count']} unsupported HapMap marker(s) found under fail policy"
            )
        if args.unsupported_marker_policy == "mask" and int(marker_audit["strict_marker_count"]) <= 0:
            raise RuntimeError("Mask policy cannot prove conversion: strict_marker_count is zero")

        resolved_tassel = _resolved_command(args.tassel_pipeline)
        if not Path(resolved_tassel).is_file() or not os.access(resolved_tassel, os.X_OK):
            raise FileNotFoundError("TASSEL pipeline executable was not found or is not executable")
        resolved_java = _resolved_command("java")
        software = {
            "tassel": {"requested": args.tassel_pipeline, "resolved_path": resolved_tassel, "version": "unknown"},
            "java": {"resolved_path": resolved_java, "version": _version([resolved_java, "-version"])},
            "python": {"resolved_path": sys.executable, "version": platform.python_version()},
        }
        manifest["software"] = software
        manifest["tool_versions"] = {
            "tassel": "unknown",
            "java": str(software["java"]["version"]),
            "python": platform.python_version(),
        }
        environment_text = _environment_text(
            resolved_tassel=resolved_tassel,
            tassel_version="unknown",
            resolved_java=resolved_java,
            java_version=str(software["java"]["version"]),
        )

        sort_command = [
            args.tassel_pipeline, f"-Xmx{args.xmx}", "-SortGenotypeFilePlugin",
            "-inputFile", str(input_vcf), "-outputFile", str(sorted_work), "-fileType", "VCF",
        ]
        export_command = [
            args.tassel_pipeline, f"-Xmx{args.xmx}", "-importGuess", str(sorted_work),
            "-export", str(hapmap_base), "-exportType", "Hapmap",
        ]
        sorted_work.unlink(missing_ok=True)
        hapmap_work.unlink(missing_ok=True)
        tassel_output = _run(sort_command) + _run(export_command)
        if not sorted_work.is_file() or not hapmap_work.is_file():
            raise RuntimeError("TASSEL completed without producing the sorted VCF and HapMap outputs")

        input_samples, input_variants = _vcf_shape(input_vcf)
        sorted_samples, sorted_variants = _vcf_shape(sorted_work)
        hapmap_samples, hapmap_variants = _hapmap_shape(hapmap_work)
        _sample_index_map(input_samples, sorted_samples)
        _sample_index_map(input_samples, hapmap_samples)
        sample_conservation = len(input_samples) == len(sorted_samples) == len(hapmap_samples)
        variant_conservation = input_variants == sorted_variants == hapmap_variants
        if not sample_conservation or not variant_conservation:
            raise RuntimeError(
                "TASSEL conversion changed sample or variant cardinality: "
                f"input={len(input_samples)}/{input_variants}, "
                f"sorted={len(sorted_samples)}/{sorted_variants}, "
                f"hapmap={len(hapmap_samples)}/{hapmap_variants}"
            )
        software["tassel"]["version"] = _tassel_version(resolved_tassel, tassel_output)
        if software["tassel"]["version"] == "unknown":
            raise RuntimeError("TASSEL version could not be proven from executable or run log")
        manifest["tool_versions"]["tassel"] = str(software["tassel"]["version"])
        environment_text = _environment_text(
            resolved_tassel=resolved_tassel,
            tassel_version=str(software["tassel"]["version"]),
            resolved_java=resolved_java,
            java_version=str(software["java"]["version"]),
        )
        environment_path.write_text(environment_text, encoding="utf-8")
        genotype_metrics = _prove_genotype_conservation(input_vcf, sorted_work, hapmap_work)
        if int(genotype_metrics["strict_marker_count"]) <= 0 or int(genotype_metrics["genotypes_compared"]) <= 0:
            raise RuntimeError(
                "Mask conversion is unproven: strict_marker_count and genotypes_compared must both be positive"
            )

        shutil.copy2(sorted_work, sorted_final)
        shutil.copy2(hapmap_work, hapmap_final)
        report_path.write_text(
            f"# GWAS format conversion\n\n"
            f"- Input SHA256: `{digest}`\n"
            f"- Genotype scope: `{genotype_scope_id}`\n"
            f"- Samples conserved: {len(input_samples)}\n"
            f"- Variants conserved: {input_variants}\n"
            f"- Unsupported marker policy: `{args.unsupported_marker_policy}`\n"
            f"- Strict reversible marker subset: {genotype_metrics['strict_marker_count']}\n"
            f"- Unsupported/lossy markers: {genotype_metrics['unsupported_marker_count']}\n"
            f"- Strict genotype conservation: {genotype_metrics['strict_genotype_conservation']}\n",
            encoding="utf-8",
        )
        manifest["metrics"] = {
            "input": {"samples": len(input_samples), "variants": input_variants},
            "sorted_vcf": {"samples": len(sorted_samples), "variants": sorted_variants},
            "hapmap": {"samples": len(hapmap_samples), "variants": hapmap_variants},
            "sample_conservation": sample_conservation,
            "variant_conservation": variant_conservation,
            "genotype_conservation": not bool(genotype_metrics["lossy_conversion"]),
            **genotype_metrics,
        }
        manifest["artifacts"] = {
            "sorted_vcf": _artifact(workspace, sorted_final, "sorted_vcf", "application/gzip"),
            "tassel_hapmap": _artifact(workspace, hapmap_final, "tassel_hapmap", "text/tab-separated-values"),
            "environment": _artifact(workspace, environment_path, "environment", "text/plain"),
            "report": _artifact(workspace, report_path, "report", "text/markdown"),
        }
        manifest["status"] = "success"
        completed_at = datetime.now(timezone.utc).isoformat()
        manifest["timestamp"] = completed_at
        manifest["end_time"] = completed_at
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        _write_json(manifest_path, manifest)
        _write_reproducibility_bundle(
            workspace=workspace,
            prefix=args.prefix,
            input_sha256=digest,
            manifest_path=manifest_path,
            manifest=manifest,
            runner_command=runner_command,
            environment_text=environment_text,
            log_path=args.log_file,
        )
        return 0
    except Exception as exc:
        reproducibility_publication_failed = manifest.get("status") == "success"
        failed_software = manifest.get("software")
        if isinstance(failed_software, dict) and isinstance(failed_software.get("tassel"), dict):
            failed_tassel = failed_software["tassel"]
            resolved_failed_tassel = str(failed_tassel.get("resolved_path") or "")
            if resolved_failed_tassel:
                failed_tassel["version"] = _tassel_version(resolved_failed_tassel)
                tool_versions = manifest.get("tool_versions")
                if isinstance(tool_versions, dict):
                    tool_versions["tassel"] = str(failed_tassel["version"])
                failed_java = failed_software.get("java", {})
                environment_text = _environment_text(
                    resolved_tassel=resolved_failed_tassel,
                    tassel_version=str(failed_tassel["version"]),
                    resolved_java=str(failed_java.get("resolved_path", "unknown")),
                    java_version=str(failed_java.get("version", "unknown")),
                )
        generic_reproducibility_error = "Stage-local reproducibility evidence could not be written"
        manifest["status"] = "failed"
        manifest["artifacts"] = {}
        manifest["error"] = generic_reproducibility_error if reproducibility_publication_failed else _public_failure_error(exc)
        if reproducibility_publication_failed:
            manifest["reproducibility_error"] = generic_reproducibility_error
        completed_at = datetime.now(timezone.utc).isoformat()
        manifest["timestamp"] = completed_at
        manifest["end_time"] = completed_at
        manifest["elapsed_sec"] = round(time.monotonic() - started_clock, 6)
        if genotype_scope_id:
            manifest["genotype_scope_id"] = genotype_scope_id
        for formal_path in formal_paths:
            formal_path.unlink(missing_ok=True)
        _write_json(manifest_path, manifest)
        if digest:
            try:
                _write_reproducibility_bundle(
                    workspace=workspace,
                    prefix=args.prefix,
                    input_sha256=digest,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    runner_command=runner_command,
                    environment_text=environment_text,
                    log_path=args.log_file,
                )
            except Exception:
                manifest["reproducibility_error"] = generic_reproducibility_error
                _write_json(manifest_path, manifest)
        print(f"ERROR: {manifest['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
