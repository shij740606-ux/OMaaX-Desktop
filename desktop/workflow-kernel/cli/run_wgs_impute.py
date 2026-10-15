#!/usr/bin/env python3
"""Run Stage 11 Beagle imputation from one authoritative Stage10 PASS SNP VCF."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid
import zipfile

from gfa_core.wgs_scientific_identity import build_wgs_analysis_id, sha256_file
from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts


STAGE = "wgs_impute"
ROLES = (
    "imputed_snp_vcf",
    "imputed_snp_vcf_index",
    "impute_report",
    "impute_log",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Impute the authoritative Stage10 PASS SNP VCF with Beagle"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--filter-manifest", required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--beagle-jar", required=True)
    parser.add_argument("--memory", default="8g")
    parser.add_argument("--window", type=float, default=40.0)
    parser.add_argument("--overlap", type=float, default=2.0)
    parser.add_argument("--ne", type=int, default=100000)
    parser.add_argument("--iterations", type=int, default=12)
    parser.add_argument("--phase-states", type=int, default=280)
    parser.add_argument("--imp-states", type=int, default=1600)
    parser.add_argument("--cluster", type=float, default=0.005)
    return parser


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_component(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value or ""):
        raise ValueError(f"{label} must be a non-empty safe path component")


def _invocation_path(command: str) -> Path:
    resolved = shutil.which(command)
    if not resolved:
        raise RuntimeError(f"executable is unavailable: {command}")
    return Path(resolved).absolute()


def _environment(command: str) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": str(_invocation_path(command).parent)
        + os.pathsep
        + os.environ.get("PATH", ""),
    }


def _java_version(java: str) -> str:
    result = subprocess.run(
        [java, "-version"],
        text=True,
        capture_output=True,
        check=False,
        env=_environment(java),
    )
    if result.returncode:
        raise RuntimeError(
            f"Java version check failed with exit code {result.returncode}"
        )
    lines = (result.stderr or result.stdout).strip().splitlines()
    if not lines:
        raise RuntimeError("Java version check produced no version")
    return lines[0]


def _java_major(version: str) -> int:
    match = re.search(r'version\s+"([0-9]+)(?:\.([0-9]+))?', version)
    if not match:
        raise RuntimeError(f"cannot parse Java version: {version}")
    major = int(match.group(1))
    if major == 1 and match.group(2):
        major = int(match.group(2))
    return major


def _beagle_required_java(jar: Path) -> int:
    try:
        with zipfile.ZipFile(jar) as archive:
            header = archive.read("main/Main.class")[:8]
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError(
            "Beagle JAR does not contain a readable main/Main.class"
        ) from exc
    if len(header) < 8 or header[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("Beagle main/Main.class header is invalid")
    class_major = int.from_bytes(header[6:8], "big")
    if class_major < 45:
        raise ValueError("Beagle main/Main.class version is invalid")
    return class_major - 44


def _validate_java_for_beagle(java_version: str, jar: Path) -> None:
    configured = _java_major(java_version)
    required = _beagle_required_java(jar)
    if configured < required:
        raise RuntimeError(
            f"Beagle requires Java {required} or newer; "
            f"configured Java is {configured}"
        )


def _authority_header(
    path: str | Path,
    *,
    workspace: Path,
    prefix: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "wgs_variant_filter authority Manifest is unreadable"
        ) from exc
    method = str(manifest.get("method") or "")
    if (
        not isinstance(manifest, dict)
        or manifest.get("stage") != "wgs_variant_filter"
        or manifest.get("status") != "success"
        or method not in {"gatk", "bcftools"}
    ):
        raise ValueError(
            "wgs_variant_filter authority Manifest is not a legal success"
        )
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
        or (manifest.get("parameters") or {}).get("method") != method
    ):
        raise ValueError(
            "wgs_variant_filter authority Manifest identity mismatch"
        )
    return manifest


def _vcf_identity(
    path: Path,
) -> tuple[list[str], list[tuple[str, int, str, str]]]:
    import pysam

    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("VCF is missing or empty")
    try:
        with pysam.VariantFile(str(path)) as vcf:
            samples = list(vcf.header.samples)
            if not samples or len(samples) != len(set(samples)):
                raise RuntimeError("VCF sample header is invalid")
            records = list(vcf)
            keys = [
                (str(record.contig), int(record.pos), record.ref, ",".join(record.alts or ()))
                for record in records
            ]
            if not keys:
                raise RuntimeError("VCF has no variant records")
            contig = keys[0][0]
        with pysam.VariantFile(str(path)) as indexed:
            next(indexed.fetch(contig, 0, None), None)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"VCF is invalid or its TBI is not queryable: {exc}") from exc
    return samples, keys


def _tabix_index(path: Path) -> str:
    import pysam

    pysam.tabix_index(str(path), preset="vcf", force=True)
    return str(pysam.__version__)


def _run_beagle(
    java: str,
    jar: Path,
    source: Path,
    output_prefix: Path,
    parameters: dict[str, Any],
    log: Any,
) -> None:
    command = [
        java,
        f"-Xmx{parameters['memory']}",
        "-jar",
        str(jar),
        f"gt={source}",
        f"out={output_prefix}",
        f"window={parameters['window']}",
        f"overlap={parameters['overlap']}",
        f"ne={parameters['ne']}",
        f"iterations={parameters['iterations']}",
        f"phase-states={parameters['phase_states']}",
        f"imp-states={parameters['imp_states']}",
        f"cluster={parameters['cluster']}",
    ]
    log.write(f"Beagle argv: {json.dumps(command)}\n")
    log.flush()
    result = subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=_environment(java),
    )
    log.write(f"Beagle exit={result.returncode}\n")
    log.flush()
    if result.returncode:
        raise RuntimeError(
            f"Beagle failed with exit code {result.returncode}"
        )


def _beagle_reported_version(path: Path) -> str:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Beagle generated log is missing or empty")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            return line.strip()
    raise RuntimeError("Beagle generated log has no version evidence")


def _artifact(
    workspace: Path, source: Path, destination: Path, role: str
) -> dict[str, Any]:
    size = source.stat().st_size
    return {
        "artifact": role,
        "role": role,
        "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()),
        "type": source.suffix.lstrip(".") or "file",
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(source),
    }


def _publish(
    bundle: Path,
    destination: Path,
    manifest_work: Path,
    authority: Path,
    started_at: str,
) -> bool:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if authority.is_file():
            try:
                current = json.loads(authority.read_text(encoding="utf-8"))
                if (
                    current.get("status") == "success"
                    and str(current.get("start_time") or "") >= started_at
                ):
                    return False
            except Exception:
                pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(bundle, destination)
        os.replace(manifest_work, authority)
    return True


def _failure(
    args: argparse.Namespace, run_id: str, started_at: str, error: str
) -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "stage": STAGE,
        "status": "failed",
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": run_id,
        "start_time": started_at,
        "artifacts": {},
        "errors": [error],
    }


def _publish_failure(
    authority: Path,
    args: argparse.Namespace,
    run_id: str,
    started_at: str,
    error: str,
) -> None:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        failure_path = (
            authority
            if not authority.exists()
            else authority.parent
            / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        )
        _atomic_json(
            failure_path, _failure(args, run_id, started_at, error)
        )


def _validated_parameters(args: argparse.Namespace) -> dict[str, Any]:
    if not re.fullmatch(r"[1-9][0-9]*[gGmMkK]", args.memory or ""):
        raise ValueError("memory must be a positive JVM size such as 8g")
    values = {
        "window": args.window,
        "overlap": args.overlap,
        "ne": args.ne,
        "iterations": args.iterations,
        "phase_states": args.phase_states,
        "imp_states": args.imp_states,
        "cluster": args.cluster,
    }
    if any(value <= 0 for value in values.values()):
        raise ValueError("all Beagle scientific parameters must be positive")
    if args.overlap >= args.window:
        raise ValueError("overlap must be smaller than window")
    return {
        "method": "beagle",
        "java": args.java,
        "beagle_jar": str(Path(args.beagle_jar).absolute()),
        "memory": args.memory.lower(),
        **values,
    }


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    try:
        _safe_component(args.prefix, "prefix")
    except ValueError as exc:
        _atomic_json(
            final / f"{STAGE}.failed.{run_id}.manifest.json",
            _failure(args, run_id, started_at, str(exc)),
        )
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1
    authority = final / f"{args.prefix}.{STAGE}.manifest.json"
    try:
        parameters = _validated_parameters(args)
        jar = Path(parameters["beagle_jar"])
        if not jar.is_file() or jar.stat().st_size <= 0:
            raise ValueError("Beagle JAR is missing or empty")
        if jar.suffix.lower() != ".jar":
            raise ValueError("Beagle JAR path must end in .jar")
        header = _authority_header(
            args.filter_manifest,
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(header.get("wgs_sample_set_id") or "")
        reference_id = str(header.get("wgs_reference_id") or "")
        upstream_analysis_id = str(header.get("wgs_analysis_id") or "")
        upstream_method = str(header.get("method") or "")
        if not sample_set_id or not reference_id or not upstream_analysis_id:
            raise ValueError(
                "wgs_variant_filter scientific lineage is incomplete"
            )
        resolved = resolve_wgs_artifacts(
            args.filter_manifest,
            "wgs_variant_filter",
            ("pass_snp_vcf", "pass_snp_vcf_index"),
            expected_sample_set_id=sample_set_id,
            expected_reference_id=reference_id,
            expected_analysis_id=upstream_analysis_id,
            expected_method=upstream_method,
        )
        if any(len(resolved[role]) != 1 for role in resolved):
            raise RuntimeError("authoritative PASS SNP VCF and TBI must be singletons")
        source = resolved["pass_snp_vcf"][0].path
        source_index = resolved["pass_snp_vcf_index"][0].path
        if source_index != Path(f"{source}.tbi"):
            raise RuntimeError("PASS SNP VCF index is not its declared sidecar")
        expected_samples = (header.get("metrics") or {}).get(
            "output_sample_ids"
        )
        if (
            not isinstance(expected_samples, list)
            or not expected_samples
            or len(expected_samples) != len(set(expected_samples))
            or (header.get("metrics") or {}).get("input_sample_ids")
            != expected_samples
        ):
            raise RuntimeError("wgs_variant_filter sample set is incomplete")
        input_samples, input_keys = _vcf_identity(source)
        if input_samples != expected_samples:
            raise RuntimeError("PASS SNP VCF sample header mismatch")
        java_version = _java_version(args.java)
        _validate_java_for_beagle(java_version, jar)
        jar_sha256 = sha256_file(jar)
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        beagle_prefix = attempt / "beagle"
        base = f"{args.prefix}.{STAGE}.cohort"
        output = bundle / f"{base}.imputed.snp.vcf.gz"
        output_index = Path(f"{output}.tbi")
        report = bundle / f"{args.prefix}.{STAGE}.cohort.report.md"
        log_path = bundle / f"{args.prefix}.{STAGE}.cohort.impute.log"
        with log_path.open("w", encoding="utf-8") as log:
            _run_beagle(
                args.java, jar, source, beagle_prefix, parameters, log
            )
            generated = Path(f"{beagle_prefix}.vcf.gz")
            generated_log = Path(f"{beagle_prefix}.log")
            if not generated.is_file() or generated.stat().st_size <= 0:
                raise RuntimeError("Beagle output VCF is missing or empty")
            shutil.move(str(generated), output)
            stale_index = Path(f"{generated}.tbi")
            stale_index.unlink(missing_ok=True)
            pysam_version = _tabix_index(output)
            beagle_version = _beagle_reported_version(generated_log)
            log.write(f"Beagle reported version: {beagle_version}\n")
            log.flush()
        if not output_index.is_file() or output_index.stat().st_size <= 0:
            raise RuntimeError("imputed SNP VCF index is missing or empty")
        output_samples, output_keys = _vcf_identity(output)
        if output_samples != input_samples:
            raise RuntimeError("imputed SNP VCF sample header mismatch")
        if output_keys != input_keys:
            raise RuntimeError(
                "imputed SNP VCF site keys are not conserved in input order"
            )
        report.write_text(
            "# WGS Beagle imputation\n\n"
            "- site policy: ordered (CHROM,POS,REF,ALT) keys are exactly "
            "conserved; Beagle may change genotype, phase, and imputation "
            "fields only.\n"
            f"- samples: {len(output_samples)}\n"
            f"- sites: {len(output_keys)}\n"
            f"- parameters: {json.dumps(parameters, sort_keys=True)}\n",
            encoding="utf-8",
        )
        software = {
            "java": {
                "path": str(_invocation_path(args.java).resolve()),
                "version": java_version,
            },
            "beagle": {
                "path": str(jar.resolve()),
                "version": beagle_version,
                "sha256": jar_sha256,
            },
            "pysam": {
                "version": pysam_version,
            },
        }
        analysis_id = build_wgs_analysis_id(
            STAGE,
            [item.sha256 for values in resolved.values() for item in values],
            parameters,
            {
                "java": java_version,
                "beagle": beagle_version,
                "beagle_jar_sha256": jar_sha256,
            },
            method="beagle",
        )
        destination = (
            final / ".wgs_impute_runs" / args.prefix / run_id
        )
        sources = {
            "imputed_snp_vcf": output,
            "imputed_snp_vcf_index": output_index,
            "impute_report": report,
            "impute_log": log_path,
        }
        artifacts = {
            role: _artifact(
                workspace, source_path, destination / source_path.name, role
            )
            for role, source_path in sources.items()
        }
        manifest = {
            "manifest_version": "1.0",
            "stage": STAGE,
            "status": "success",
            "prefix": args.prefix,
            "job_id": args.job_id,
            "run_id": run_id,
            "start_time": started_at,
            "workspace": str(workspace),
            "method": "beagle",
            "upstream_method": upstream_method,
            "wgs_sample_set_id": sample_set_id,
            "wgs_reference_id": reference_id,
            "upstream_wgs_analysis_ids": {
                "wgs_variant_filter": upstream_analysis_id
            },
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": software,
            "metrics": {
                "input_sample_ids": input_samples,
                "output_sample_ids": output_samples,
                "total_samples": len(output_samples),
                "input_sites": len(input_keys),
                "output_sites": len(output_keys),
                "input_site_keys": [list(key) for key in input_keys],
                "output_site_keys": [list(key) for key in output_keys],
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(
            bundle, destination, manifest_work, authority, started_at
        ):
            raise RuntimeError(
                "stale imputation attempt cannot replace newer run"
            )
        return 0
    except Exception as exc:
        _publish_failure(authority, args, run_id, started_at, str(exc))
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
