#!/usr/bin/env python3
"""Refine identify members from a curated ID list.

This stage lets users add/remove gene family members after identify. It writes
the pre-final review set used by the subfamily evidence route:
  - final/{prefix}.curated_members.id
  - final/{prefix}.curated_members.pep.fa
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ID_CANDIDATE_NAMES = (
    "{prefix}.curated_members.id",
    "{prefix}.final_members.curated.id",
    "{prefix}.final_members.edited.id",
    "{prefix}.final_members.manual.id",
    "curated_members.id",
    "final_members.curated.id",
    "final_members.edited.id",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_ids(path: Path) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            seq_id = line.split()[0]
            if seq_id not in seen:
                ids.append(seq_id)
                seen.add(seq_id)
    return ids


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def single_target_prefix(prefix: str) -> str:
    parts = [p for p in str(prefix or "").split(".") if p]
    if len(parts) == 2 and parts[0] != "Target":
        return f"Target.{parts[0]}.{parts[1]}"
    if len(parts) == 3 and parts[0] == "Target" and parts[1] != "SPL":
        return prefix
    return ""


def find_id_file(input_dir: Path, workspace: Path, prefix: str, explicit: str = "", target_prefix: str = "") -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = input_dir / path
        if path.exists():
            return path
        raise FileNotFoundError(f"Curated ID file not found: {path}")

    candidates: list[Path] = []
    prefixes = [target_prefix, prefix] if target_prefix and target_prefix != prefix else [prefix]
    for root in (input_dir, input_dir / "final", workspace / "final"):
        for candidate_prefix in prefixes:
            for template in ID_CANDIDATE_NAMES:
                candidates.append(root / template.format(prefix=candidate_prefix))
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    raise FileNotFoundError(
        "No curated ID file found. Upload one of: "
        + ", ".join(t.format(prefix=prefix) for t in ID_CANDIDATE_NAMES)
    )


def fasta_record_iter(path: Path):
    header = None
    seq_lines: list[str] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith(">"):
                if header is not None:
                    yield header, seq_lines
                header = line.rstrip("\n")
                seq_lines = []
            elif header is not None:
                seq_lines.append(line.rstrip("\n"))
    if header is not None:
        yield header, seq_lines


def fasta_ids(path: Path) -> set[str]:
    ids = set()
    for header, _ in fasta_record_iter(path):
        parts = header[1:].strip().split()
        if parts:
            ids.add(parts[0])
    return ids


def first_column_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        header_seen = False
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            first = line.split("	", 1)[0].split()[0]
            if not header_seen and first.lower() in {"gene_id", "gene", "id"}:
                header_seen = True
                continue
            header_seen = True
            ids.add(first)
    return ids


def find_source_fastas(input_dir: Path, workspace: Path, prefix: str, explicit: str = "", target_prefix: str = "") -> list[Path]:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = input_dir / path
        if path.exists():
            return [path]
        raise FileNotFoundError(f"Source protein FASTA not found: {path}")

    manifest = read_manifest(workspace / "final" / f"{prefix}.identify.manifest.json")
    paths: list[Path] = []
    if target_prefix:
        paths.extend([
            workspace / "final" / f"{target_prefix}.identified_members.pep.fa",
            input_dir / "final" / f"{target_prefix}.identified_members.pep.fa",
            workspace / "final" / f"{target_prefix}.final_members.pep.fa",
            input_dir / "final" / f"{target_prefix}.final_members.pep.fa",
        ])
    for item in manifest.get("inputs", []) if isinstance(manifest.get("inputs"), list) else []:
        full = item.get("full_path")
        if full:
            paths.append(Path(full))

    patterns = (
        "*.pep.fasta", "*.pep.fa", "*.protein.fasta", "*.protein.fa",
        "*.faa", "*protein*.fasta", "*protein*.fa", "*pep*.fasta", "*pep*.fa",
    )
    for root in (input_dir, input_dir / "genome_prepare", workspace / "final"):
        for pattern in patterns:
            paths.extend(root.glob(pattern))

    for existing in (
        workspace / "final" / f"{prefix}.identified_members.pep.fa",
        workspace / "final" / f"{prefix}.final_members.pep.fa",
    ):
        if existing.exists():
            paths.append(existing)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        name = path.name.lower()
        if any(token in name for token in ("pep", "protein", ".faa", "final_members")):
            unique.append(path)
            seen.add(resolved)
    return unique


def write_subset(ids: list[str], fastas: list[Path], out_fasta: Path) -> tuple[int, list[str], list[Path]]:
    wanted = set(ids)
    found: dict[str, tuple[str, list[str]]] = {}
    used_sources: list[Path] = []

    for fasta in fastas:
        source_used = False
        for header, seq_lines in fasta_record_iter(fasta):
            parts = header[1:].strip().split()
            if not parts:
                continue
            seq_id = parts[0]
            if seq_id in wanted and seq_id not in found:
                found[seq_id] = (header, seq_lines)
                source_used = True
        if source_used:
            used_sources.append(fasta)

    out_fasta.parent.mkdir(parents=True, exist_ok=True)
    with open(out_fasta, "w", encoding="utf-8") as out:
        for seq_id in ids:
            if seq_id not in found:
                continue
            header, seq_lines = found[seq_id]
            out.write(header + "\n")
            for seq in seq_lines:
                out.write(seq + "\n")

    missing = [seq_id for seq_id in ids if seq_id not in found]
    return len(found), missing, used_sources


def artifact(path: Path, name: str, artifact_type: str) -> dict[str, Any]:
    return {
        "artifact": name,
        "file": path.name,
        "full_path": str(path.resolve()),
        "relpath_or_hint": f"final/{path.name}",
        "type": artifact_type,
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path) if path.exists() else "",
    }


def backup(path: Path, tag: str) -> str:
    if not path.exists():
        return ""
    bak = path.with_name(f"{path.name}.bak_{tag}")
    shutil.copy2(path, bak)
    return str(bak.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild identify final members from a curated ID list")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--id-file", default="")
    parser.add_argument("--source-fasta", default="")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--curated-only", action="store_true", help="Compatibility flag; identify_refine always writes curated_members.*")
    args = parser.parse_args()

    start = datetime.now()
    final_dir = args.workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    tag = start.strftime("%Y%m%d_%H%M%S")

    target_prefix = single_target_prefix(args.prefix)
    id_file = find_id_file(args.input_dir, args.workspace, args.prefix, args.id_file, target_prefix=target_prefix)
    ids = read_ids(id_file)
    if not ids:
        raise RuntimeError(f"Curated ID file is empty: {id_file}")

    source_fastas = find_source_fastas(args.input_dir, args.workspace, args.prefix, args.source_fasta, target_prefix=target_prefix)
    if not source_fastas:
        raise RuntimeError("No source protein FASTA found for curated IDs")

    target_source = final_dir / f"{target_prefix}.identified_members.pep.fa" if target_prefix else None
    if target_prefix and not target_source.exists():
        target_source = final_dir / f"{target_prefix}.final_members.pep.fa"
    if target_prefix and not args.source_fasta:
        review_ids = first_column_ids(final_dir / f"{target_prefix}.domain_review.tsv")
        allowed_ids = review_ids or (fasta_ids(target_source) if target_source and target_source.exists() else set())
        foreign_ids = [seq_id for seq_id in ids if allowed_ids and seq_id not in allowed_ids]
        if foreign_ids:
            raise RuntimeError(
                f"Curated IDs do not belong to single target scope {target_prefix}: "
                + ", ".join(foreign_ids[:20])
                + (" ..." if len(foreign_ids) > 20 else "")
            )

    output_prefix = target_prefix or args.prefix
    member_stem = "curated_members"
    out_id = final_dir / f"{output_prefix}.{member_stem}.id"
    out_pep = final_dir / f"{output_prefix}.{member_stem}.pep.fa"
    manifest_path = final_dir / f"{args.prefix}.identify.manifest.json"
    old_manifest = read_manifest(manifest_path)
    backups = {
        "id": backup(out_id, tag),
        "pep": backup(out_pep, tag),
        "manifest": "",
    }

    matched, missing, used_sources = write_subset(ids, source_fastas, out_pep)
    if missing and not args.allow_missing:
        raise RuntimeError(
            f"{len(missing)} curated IDs were not found in source FASTA: "
            + ", ".join(missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )
    out_id.write_text("\n".join([seq_id for seq_id in ids if seq_id not in set(missing)]) + "\n", encoding="utf-8")

    report = final_dir / f"{args.prefix}.identify_refine_report.md"
    report.write_text(
        "\n".join([
            "# Identify Member Refinement",
            "",
            f"- prefix: {args.prefix}",
            f"- curated_id_file: {id_file}",
            f"- requested_ids: {len(ids)}",
            f"- matched_ids: {matched}",
            f"- missing_ids: {len(missing)}",
            f"- source_fastas: {', '.join(str(p) for p in used_sources)}",
        ]) + "\n",
        encoding="utf-8",
    )

    manifest = old_manifest if old_manifest else {}
    manifest.update({
        "manifest_version": manifest.get("manifest_version", "1.0"),
        "stage": "identify_refine",
        "status": "success",
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "timestamp": datetime.now().isoformat(),
        "end_time": datetime.now().isoformat(),
        "elapsed_sec": round((datetime.now() - start).total_seconds(), 2),
        "workspace_root": str(args.workspace.resolve()),
        "workspace": str(final_dir.resolve()),
        "input_dir": str(args.input_dir.resolve()),
        "curated_revision": {
            "enabled": True,
            "stage": "identify_refine",
            "curated_only": True,
            "curated_id_file": str(id_file.resolve()),
            "requested_ids": len(ids),
            "matched_ids": matched,
            "missing_ids": missing,
            "source_fastas": [str(p.resolve()) for p in used_sources],
            "backups": backups,
        },
    })
    metrics = manifest.get("metrics", {}) if isinstance(manifest.get("metrics"), dict) else {}
    metrics.update({"curated_members": matched, "curated_requested_ids": len(ids), "curated_missing_ids": len(missing)})
    manifest["metrics"] = metrics
    artifacts = manifest.get("artifacts", {}) if isinstance(manifest.get("artifacts"), dict) else {}
    artifacts.update({
        "curated_members": artifact(out_pep, "curated_members", "fasta"),
        "curated_members_id": artifact(out_id, "curated_members_id", "id_list"),
        "identify_refine_report": artifact(report, "identify_refine_report", "markdown"),
    })
    if target_prefix and target_prefix != args.prefix:
        compat_id = final_dir / f"{args.prefix}.{member_stem}.id"
        compat_pep = final_dir / f"{args.prefix}.{member_stem}.pep.fa"
        shutil.copy2(out_id, compat_id)
        shutil.copy2(out_pep, compat_pep)
        artifacts["compat_curated_members"] = artifact(compat_pep, "compat_curated_members", "fasta")
        artifacts["compat_curated_members_id"] = artifact(compat_id, "compat_curated_members_id", "id_list")
    manifest["artifacts"] = artifacts
    refine_manifest_path = final_dir / f"{args.prefix}.identify_refine.manifest.json"
    refine_manifest = dict(manifest)
    refine_manifest["stage"] = "identify_refine"
    refine_manifest["derived_stage"] = "identify"
    refine_manifest["artifacts"] = dict(artifacts)
    if manifest_path.exists():
        refine_manifest["artifacts"]["identify_manifest"] = artifact(manifest_path, "identify_manifest", "json")
    refine_manifest_path.write_text(json.dumps(refine_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    input_final = args.input_dir / "final"
    input_final.mkdir(parents=True, exist_ok=True)
    copy_paths = [out_id, out_pep, refine_manifest_path, report]
    for path in copy_paths:
        try:
            shutil.copy2(path, input_final / path.name)
        except Exception:
            pass

    print(f"Identify members refined: {matched}/{len(ids)} IDs")
    print("Mode: curated_members")
    print(f"Manifest: {refine_manifest_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"identify_refine failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
