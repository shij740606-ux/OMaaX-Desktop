#!/usr/bin/env python3
"""Scope-aware whole-job summary and DeepSeek report stage.

The deterministic layer first builds a traceable manifest/artifact index.  By
default that evidence is then passed to DeepSeek to produce the human-readable
Chinese report.  The evidence index remains a separate artifact so generated
prose can always be audited against local results.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def sanitize_token(value: str) -> str:
    safe = []
    for char in str(value):
        safe.append(char if char.isalnum() or char in "._-" else "-")
    return "".join(safe).strip(".-") or "default"


def infer_workspace(manifest_path: Optional[Path], workspace_arg: str = "") -> Path:
    if workspace_arg:
        return Path(workspace_arg).expanduser().resolve()
    if not manifest_path:
        raise ValueError("--workspace is required when --manifest is not provided")
    data = read_json(manifest_path) or {}
    for key in ("workspace_root", "workspace"):
        value = data.get(key)
        if not value:
            continue
        p = Path(value).expanduser()
        if p.name == "final":
            p = p.parent
        if p.exists():
            return p.resolve()
    return manifest_path.resolve().parent.parent if manifest_path.parent.name == "final" else manifest_path.resolve().parent


def artifact_iter(artifacts: Any) -> Iterable[Tuple[str, Dict[str, Any]]]:
    if isinstance(artifacts, dict):
        for key, value in artifacts.items():
            if isinstance(value, dict):
                yield key, value
            else:
                yield key, {"path": str(value)}
    elif isinstance(artifacts, list):
        for i, value in enumerate(artifacts):
            if isinstance(value, dict):
                yield str(value.get("artifact") or value.get("name") or i), value
            else:
                yield str(i), {"path": str(value)}


def resolve_artifact_path(workspace: Path, manifest_file: Path, artifact: Dict[str, Any]) -> Optional[Path]:
    path_text = str(artifact.get("full_path") or artifact.get("path") or artifact.get("relpath_or_hint") or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    if path.is_absolute():
        return path
    candidates = [
        workspace / path,
        workspace / "final" / path.name,
        manifest_file.parent / path.name,
        Path(path_text),
    ]
    return next((p for p in candidates if p.exists()), candidates[0])


def manifest_scope(data: Dict[str, Any]) -> str:
    return str(data.get("scope_id") or data.get("parameters", {}).get("scope_id") or "unscoped")


def manifest_family(data: Dict[str, Any], prefix: str) -> str:
    return str(data.get("family") or data.get("parameters", {}).get("family") or (prefix.split(".", 1)[1] if "." in prefix else ""))


def is_scoped_filename(path: Path, scope_id: str) -> bool:
    return scope_id != "unscoped" and sanitize_token(scope_id) in path.name


def collect_manifests(workspace: Path, prefix: str = "", scope_id: str = "") -> List[Dict[str, Any]]:
    final_dir = workspace / "final"
    paths = sorted(final_dir.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime)
    records: List[Dict[str, Any]] = []
    for path in paths:
        data = read_json(path)
        if not data:
            continue
        stage = str(data.get("stage") or "")
        if not stage or stage in {"minimal", "summary"}:
            continue
        rec_prefix = str(data.get("prefix") or prefix)
        if prefix and rec_prefix and rec_prefix != prefix:
            # Per-target child manifests such as Multi.SPL.Lc_to_At.kaks.manifest.json
            # are indexed through the aggregate scoped manifest instead of treated as
            # independent top-level stages.
            continue
        rec_scope = manifest_scope(data)
        if scope_id and rec_scope != scope_id:
            continue
        records.append({"path": path, "data": data, "stage": stage, "prefix": rec_prefix, "scope_id": rec_scope})

    dedup: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for rec in records:
        key = (rec["stage"], rec["scope_id"], rec["prefix"])
        prev = dedup.get(key)
        if not prev:
            dedup[key] = rec
            continue
        prev_scoped = is_scoped_filename(prev["path"], rec["scope_id"])
        rec_scoped = is_scoped_filename(rec["path"], rec["scope_id"])
        if (rec_scoped and not prev_scoped) or rec["path"].stat().st_mtime >= prev["path"].stat().st_mtime:
            dedup[key] = rec
    return sorted(dedup.values(), key=lambda r: (r["scope_id"], r["stage"]))


def summarize_record(workspace: Path, rec: Dict[str, Any]) -> Dict[str, Any]:
    data = rec["data"]
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    artifacts = data.get("artifacts")
    artifact_rows = []
    missing = []
    for name, artifact in artifact_iter(artifacts):
        resolved = resolve_artifact_path(workspace, rec["path"], artifact)
        exists = bool(resolved and resolved.exists())
        if not exists:
            missing.append(name)
        artifact_rows.append({
            "stage": rec["stage"],
            "scope_id": rec["scope_id"],
            "artifact": name,
            "path": str(resolved or artifact.get("path") or ""),
            "exists": exists,
            "size": resolved.stat().st_size if exists else "",
            "type": artifact.get("type") or artifact.get("format") or "",
        })
    return {
        "stage": rec["stage"],
        "prefix": rec["prefix"],
        "scope_id": rec["scope_id"],
        "status": data.get("status", "unknown"),
        "family": manifest_family(data, rec["prefix"]),
        "target_species": data.get("target_species") or data.get("parameters", {}).get("target_species") or [],
        "reference_species": data.get("reference_species") or data.get("parameters", {}).get("reference_species") or [],
        "comparative_species": data.get("comparative_species") or data.get("parameters", {}).get("comparative_species") or [],
        "metrics": metrics,
        "artifact_count": len(artifact_rows),
        "missing_artifacts": missing,
        "manifest": str(rec["path"]),
        "artifact_rows": artifact_rows,
    }


def compact_metrics(stage: str, metrics: Dict[str, Any]) -> str:
    priority = [
        "member_count", "total_members", "protein_count", "sequence_count", "num_sequences",
        "genes_analyzed", "gene_count", "ortholog_pairs", "species_runs", "input_pairs",
        "successful_pairs", "mean_ka_ks", "mean_ks", "total_interactions", "genes_in_network",
        "total_samples", "de_genes", "go_term_count", "module_count", "status",
    ]
    parts = []
    for key in priority:
        if key in metrics:
            parts.append(f"{key}={metrics[key]}")
    if not parts:
        for key, value in list(metrics.items())[:5]:
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key}={value}")
    return "; ".join(parts)


def build_checks(summaries: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    checks = []
    by_scope: Dict[str, List[Dict[str, Any]]] = {}
    for item in summaries:
        by_scope.setdefault(item["scope_id"], []).append(item)
        if item["status"] not in {"success", "partial_success"}:
            checks.append({"level": "error", "scope_id": item["scope_id"], "message": f"{item['stage']} status={item['status']}"})
        if item["missing_artifacts"]:
            checks.append({"level": "warning", "scope_id": item["scope_id"], "message": f"{item['stage']} missing artifacts: {', '.join(item['missing_artifacts'][:5])}"})

    for scope, items in by_scope.items():
        families = sorted({str(x.get("family") or "") for x in items if x.get("family")})
        if len(families) > 1:
            checks.append({"level": "warning", "scope_id": scope, "message": f"Multiple family labels in same scope: {', '.join(families)}"})
        stages = {x["stage"] for x in items}
        if "kaks" in stages and "ortholog" not in stages:
            checks.append({"level": "warning", "scope_id": scope, "message": "kaks present but ortholog manifest not found in same scope"})
        if "combo" in stages:
            missing = [s for s in ("phylogeny", "motif", "domain", "structure") if s not in stages]
            if missing:
                checks.append({"level": "warning", "scope_id": scope, "message": f"combo present but missing dependencies: {', '.join(missing)}"})
    if not checks:
        checks.append({"level": "ok", "scope_id": "all", "message": "No manifest-level consistency issues detected"})
    return checks


def write_files_tsv(path: Path, rows: List[Dict[str, Any]]) -> None:
    headers = ["scope_id", "stage", "artifact", "type", "exists", "size", "path"]
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(headers) + "\n")
        for row in rows:
            handle.write("\t".join(str(row.get(h, "")) for h in headers) + "\n")


def write_report(path: Path, prefix: str, summaries: List[Dict[str, Any]], checks: List[Dict[str, str]], files_tsv: Path) -> None:
    by_scope: Dict[str, List[Dict[str, Any]]] = {}
    for item in summaries:
        by_scope.setdefault(item["scope_id"], []).append(item)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# Analysis Summary - {prefix}\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        f.write("This summary is an objective result index and QC report. Deep biological interpretation should be generated as a separate step.\n\n")
        f.write("## Scope Overview\n\n")
        for scope, items in sorted(by_scope.items()):
            stages = ", ".join(sorted({x["stage"] for x in items}))
            f.write(f"- **{scope}**: {len(items)} stage manifest(s); stages: {stages}\n")
        f.write("\n## Stage Metrics\n\n")
        f.write("| Scope | Stage | Status | Target | Reference | Key metrics |\n")
        f.write("|---|---|---|---|---|---|\n")
        for item in summaries:
            target = ",".join(map(str, item.get("target_species") or []))
            ref = ",".join(map(str, item.get("reference_species") or []))
            f.write(f"| {item['scope_id']} | {item['stage']} | {item['status']} | {target} | {ref} | {compact_metrics(item['stage'], item['metrics'])} |\n")
        f.write("\n## Consistency Checks\n\n")
        for check in checks:
            f.write(f"- **{check['level']}** `{check['scope_id']}`: {check['message']}\n")
        f.write("\n## Files\n\n")
        f.write(f"Artifact index: `{files_tsv.name}`\n")


def main():
    parser = argparse.ArgumentParser(description="Summary stage - whole-job manifest summary")
    parser.add_argument("--manifest", help="Any manifest from the job; used to infer workspace/prefix")
    parser.add_argument("--workspace", help="workspace/<job_id> directory")
    parser.add_argument("--prefix", help="analysis prefix")
    parser.add_argument("--scope-file", help="optional scope JSON; when set, writes an additional scoped summary")
    parser.add_argument("--scope-id", help="optional explicit scope_id filter")
    parser.add_argument("--force", action="store_true", help="overwrite existing results")
    parser.add_argument("--log-file", help="log file path")
    parser.add_argument("--no-deepseek", action="store_true", help="write only the deterministic evidence index")
    parser.add_argument("--framework", help="optional Markdown framework for the DeepSeek report")
    parser.add_argument("--deepseek-model", default="", help="optional DeepSeek model override")
    parser.add_argument("--deepseek-max-tokens", type=int, default=16000)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else None
    if manifest_path and not manifest_path.exists():
        print(f"Error: manifest not found: {manifest_path}")
        sys.exit(1)

    workspace = infer_workspace(manifest_path, args.workspace)
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    seed_manifest = read_json(manifest_path) if manifest_path else {}
    prefix = args.prefix or str((seed_manifest or {}).get("prefix") or "")
    scope_id = args.scope_id or ""
    if args.scope_file:
        scope_data = read_json(Path(args.scope_file).expanduser())
        if scope_data:
            scope_id = scope_id or str(scope_data.get("scope_id") or "")
            prefix = prefix or str(scope_data.get("prefix") or "")
    if not prefix:
        candidates = collect_manifests(workspace)
        prefix = candidates[0]["prefix"] if candidates else workspace.name

    all_records = collect_manifests(workspace, prefix=prefix)
    summaries = [summarize_record(workspace, rec) for rec in all_records]
    artifact_rows = [row for item in summaries for row in item["artifact_rows"]]
    checks = build_checks(summaries)

    evidence_index = final_dir / f"{prefix}.summary_evidence_index.md"
    files_tsv = final_dir / f"{prefix}.summary.files.tsv"
    manifest_out = final_dir / f"{prefix}.summary.manifest.json"
    write_files_tsv(files_tsv, artifact_rows)
    write_report(evidence_index, prefix, summaries, checks, files_tsv)

    manifest_data = {
        "manifest_version": "1.0",
        "stage": "summary",
        "status": "success",
        "prefix": prefix,
        "timestamp": datetime.now().isoformat(),
        "workspace": str(workspace),
        "metrics": {
            "manifest_count": len(summaries),
            "scope_count": len({x["scope_id"] for x in summaries}),
            "artifact_count": len(artifact_rows),
            "check_count": len(checks),
        },
        "summaries": summaries,
        "checks": checks,
        "artifacts": {
            "summary_evidence_index": {"path": f"final/{evidence_index.name}", "type": "markdown", "size": evidence_index.stat().st_size},
            "files_tsv": {"path": f"final/{files_tsv.name}", "type": "tsv", "size": files_tsv.stat().st_size},
        },
    }
    manifest_out.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    scoped_manifest = None
    scoped_data = None
    if scope_id:
        scoped_items = [x for x in summaries if x["scope_id"] == scope_id]
        scoped_rows = [row for x in scoped_items for row in x["artifact_rows"]]
        scoped_checks = [x for x in checks if x["scope_id"] in {scope_id, "all"}]
        token = sanitize_token(scope_id)
        scoped_evidence_index = final_dir / f"{prefix}.{token}.summary_evidence_index.md"
        scoped_files = final_dir / f"{prefix}.{token}.summary.files.tsv"
        scoped_manifest = final_dir / f"{prefix}.{token}.summary.manifest.json"
        write_files_tsv(scoped_files, scoped_rows)
        write_report(scoped_evidence_index, prefix, scoped_items, scoped_checks, scoped_files)
        scoped_data = dict(manifest_data)
        scoped_data.update({
            "scope_id": scope_id,
            "metrics": {
                "manifest_count": len(scoped_items),
                "scope_count": 1 if scoped_items else 0,
                "artifact_count": len(scoped_rows),
                "check_count": len(scoped_checks),
            },
            "summaries": scoped_items,
            "checks": scoped_checks,
            "artifacts": {
                "summary_evidence_index": {"path": f"final/{scoped_evidence_index.name}", "type": "markdown", "size": scoped_evidence_index.stat().st_size},
                "files_tsv": {"path": f"final/{scoped_files.name}", "type": "tsv", "size": scoped_files.stat().st_size},
            },
        })
        scoped_manifest.write_text(json.dumps(scoped_data, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.no_deepseek:
        fallback_report = final_dir / (
            f"{prefix}.{sanitize_token(scope_id)}.summary_report.md" if scope_id else f"{prefix}.summary_report.md"
        )
        source_index = scoped_evidence_index if scope_id else evidence_index
        fallback_report.write_text(source_index.read_text(encoding="utf-8"), encoding="utf-8")
        target_manifest = scoped_manifest if scope_id and scoped_manifest else manifest_out
        target_data = read_json(target_manifest) or manifest_data
        target_data.setdefault("artifacts", {})["summary_report"] = {
            "path": f"final/{fallback_report.name}",
            "type": "markdown",
            "size": fallback_report.stat().st_size,
            "generation": "deterministic_no_deepseek",
        }
        target_data["deepseek_generation"] = {"status": "skipped", "reason": "--no-deepseek"}
        target_manifest.write_text(json.dumps(target_data, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        try:
            from gfa_core.mas.agents.deepseek_summary import generate_deepseek_summary

            result = generate_deepseek_summary(
                final_dir=final_dir,
                prefix=prefix,
                job_id=workspace.name,
                summaries=summaries,
                scope_id=scope_id,
                framework_path=Path(args.framework).expanduser() if args.framework else None,
                model=args.deepseek_model,
                max_tokens=max(4096, int(args.deepseek_max_tokens)),
            )
            effective_scope = str(result.get("scope_id") or "")
            target_manifest = scoped_manifest if scope_id and scoped_manifest else manifest_out
            target_data = read_json(target_manifest) or manifest_data
            report_path = Path(result["report_path"])
            evidence_path = Path(result["evidence_path"])
            metadata_path = Path(result["metadata_path"])
            target_data.setdefault("artifacts", {}).update({
                "summary_report": {
                    "path": f"final/{report_path.name}", "type": "markdown", "size": report_path.stat().st_size,
                    "generation": "deepseek_api",
                },
                "summary_evidence": {
                    "path": f"final/{evidence_path.name}", "type": "json", "size": evidence_path.stat().st_size,
                },
                "deepseek_metadata": {
                    "path": f"final/{metadata_path.name}", "type": "json", "size": metadata_path.stat().st_size,
                },
            })
            target_data["deepseek_generation"] = {
                "status": "success",
                "scope_id": effective_scope,
                "provider": result["metadata"].get("provider"),
                "model": result["metadata"].get("model"),
                "validation": result["metadata"].get("validation"),
            }
            target_manifest.write_text(json.dumps(target_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            target_manifest = scoped_manifest if scope_id and scoped_manifest else manifest_out
            target_data = read_json(target_manifest) or manifest_data
            target_data["status"] = "failed"
            target_data["deepseek_generation"] = {"status": "failed", "error": str(exc)[:2000]}
            target_manifest.write_text(json.dumps(target_data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Error: DeepSeek summary generation failed: {exc}", file=sys.stderr)
            sys.exit(2)

    print("Summary complete")
    print(f"  - {evidence_index}")
    print(f"  - {files_tsv}")
    print(f"  - {manifest_out}")


if __name__ == "__main__":
    main()
