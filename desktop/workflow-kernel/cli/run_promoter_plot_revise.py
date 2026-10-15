#!/usr/bin/env python3
"""Custom promoter plotting from an existing promoter/PlantCARE result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RSCRIPT = Path(shutil.which("Rscript") or "Rscript")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value).split(",")
    out: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def sanitize_label(value: str, default: str = "custom") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    text = text or default
    if len(text) <= 64:
        return text
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"{text[:50].rstrip('._-')}_{digest}"


def relpath(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path.resolve())


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def artifact_path(item: Any, workspace: Path) -> Path | None:
    if not isinstance(item, dict):
        return None
    raw = item.get("full_path") or item.get("path") or item.get("relpath_or_hint")
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path if path.exists() else None


def latest_manifest(final_dir: Path, suffix: str) -> Path | None:
    candidates = sorted(final_dir.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def find_promoter_file(workspace: Path, prefix: str, explicit: str = "") -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = workspace / path
        if path.exists():
            return path
    final_dir = workspace / "final"
    for manifest_path in sorted(final_dir.glob(f"{prefix}*.promoter.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = load_json(manifest_path)
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        for key in ("filtered_plantcare_output", "plantcare_output"):
            path = artifact_path(artifacts.get(key), workspace)
            if path:
                return path
    fallback = workspace / "promoter" / "analysis" / "plantCARE_output_filtered.tab"
    return fallback if fallback.exists() else None


def find_tree_file(workspace: Path, prefix: str, explicit: str = "") -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = workspace / path
        if path.exists():
            return path
    direct = workspace / "final" / f"{prefix}.tree.nwk"
    if direct.exists():
        return direct
    for manifest_path in sorted((workspace / "final").glob(f"{prefix}*.phylogeny.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        manifest = load_json(manifest_path)
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        for key, item in artifacts.items():
            name = str(key).lower()
            path = artifact_path(item, workspace)
            if path and (name == "tree" or path.name.endswith(".tree.nwk") or path.suffix == ".nwk"):
                return path
    candidates = sorted((workspace / "final").glob(f"{prefix}*.tree.nwk"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def find_subfamily_file(workspace: Path, prefix: str, explicit: str = "") -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = workspace / path
        if path.exists():
            return path
    final_dir = workspace / "final"
    names = [
        f"{prefix}.confirmed_subfamily.tsv",
        f"{prefix}.geneID.subfamily",
        f"{prefix}.subfamily.tsv",
        f"{prefix}.assignment.tsv",
    ]
    for name in names:
        path = final_dir / name
        if path.exists():
            return path
    candidates = sorted(final_dir.glob(f"{prefix}*subfamily*.tsv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def artifact_record(path: Path, workspace: Path, artifact: str) -> dict[str, Any]:
    suffix = path.suffix.lower().lstrip(".")
    return {
        "artifact": artifact,
        "path": relpath(path, workspace),
        "full_path": str(path.resolve()),
        "type": "image" if suffix in {"png", "jpg", "jpeg", "svg"} else ("pdf" if suffix == "pdf" else "table" if suffix in {"tsv", "csv"} else "file"),
        "format": suffix,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--tree", default="")
    parser.add_argument("--subfamily", default="")
    parser.add_argument("--promoter-file", default="")
    parser.add_argument("--elements", default="")
    parser.add_argument("--categories", default="")
    parser.add_argument("--match-mode", default="exact", choices=["exact", "contains", "regex"])
    parser.add_argument("--plot-modes", default="position_distribution,heatmap")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--color-by", default="auto", choices=["auto", "element", "category"])
    parser.add_argument("--output-label", default="")
    parser.add_argument("--scope-file", default="")
    parser.add_argument("--rscript", default=str(DEFAULT_RSCRIPT if DEFAULT_RSCRIPT.exists() else "Rscript"))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    start = time.time()
    workspace = Path(args.workspace).expanduser().resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    elements = split_csv(args.elements)
    categories = split_csv(args.categories)
    plot_modes = split_csv(args.plot_modes) or ["position_distribution", "heatmap"]
    if not elements and not categories:
        print("ERROR: Provide at least one selected element or category.", file=sys.stderr)
        return 2

    label_seed = args.output_label or "_".join(elements[:6] + [c.replace(" ", "") for c in categories[:3]])
    output_label = sanitize_label(label_seed, "custom")
    output_prefix = f"{args.prefix}.promoter_custom_{output_label}"

    promoter_file = find_promoter_file(workspace, args.prefix, args.promoter_file)
    tree_file = find_tree_file(workspace, args.prefix, args.tree)
    subfamily_file = find_subfamily_file(workspace, args.prefix, args.subfamily)

    errors = []
    if not promoter_file:
        errors.append("missing existing promoter filtered table; run promoter first")
    if not tree_file:
        errors.append("missing phylogeny tree; run same-scope phylogeny first")

    manifest_path = final_dir / f"{args.prefix}.promoter_plot_revise.manifest.json"
    labeled_manifest_path = final_dir / f"{output_prefix}.promoter_plot_revise.manifest.json"

    if errors:
        manifest = {
            "manifest_version": "1.0",
            "stage": "promoter_plot_revise",
            "status": "failed",
            "prefix": args.prefix,
            "source_stage": "promoter",
            "display_stage": "promoter",
            "created_at": datetime.now().isoformat(),
            "parameters": vars(args) | {"output_label": output_label, "plot_modes": plot_modes, "selected_elements": elements, "selected_categories": categories},
            "artifacts": {},
            "metrics": {"elapsed_sec": round(time.time() - start, 3), "matched_records": 0},
            "errors": errors,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        labeled_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print("ERROR: " + "; ".join(errors), file=sys.stderr)
        return 2

    script = PROJECT_ROOT / "scripts" / "promoter_custom_plot.R"
    cmd = [
        args.rscript,
        str(script),
        str(tree_file),
        str(subfamily_file) if subfamily_file else "NA",
        str(promoter_file),
        "NA",
        output_prefix,
        "--elements",
        ",".join(elements),
        "--categories",
        ",".join(categories),
        "--match-mode",
        args.match_mode,
        "--plot-modes",
        ",".join(plot_modes),
        "--top-n",
        str(max(1, args.top_n)),
        "--color-by",
        args.color_by,
    ]
    proc = subprocess.run(cmd, cwd=final_dir, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    output_files = sorted(p for p in final_dir.glob(f"{output_prefix}_*") if p.is_file())
    artifacts: dict[str, Any] = {
        "source_promoter_table": artifact_record(promoter_file, workspace, "source_promoter_table"),
        "source_tree": artifact_record(tree_file, workspace, "source_tree"),
    }
    if subfamily_file:
        artifacts["source_subfamily"] = artifact_record(subfamily_file, workspace, "source_subfamily")
    for path in output_files:
        base_key = sanitize_label(path.stem.replace(output_prefix, "").strip("_"), "custom_output")
        suffix_key = path.suffix.lower().lstrip(".") or "file"
        key = sanitize_label(f"{base_key}_{suffix_key}", "custom_output")
        artifact_kind = "promoter_custom_plot" if path.suffix.lower() in {".png", ".pdf", ".svg"} else "promoter_custom_table"
        artifacts[key] = artifact_record(path, workspace, artifact_kind)

    metadata = {}
    metadata_file = final_dir / f"{output_prefix}_metadata.tsv"
    if metadata_file.exists():
        for line in metadata_file.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                metadata[parts[0]] = parts[1]

    success = proc.returncode == 0
    metrics = {
        "elapsed_sec": round(time.time() - start, 3),
        "matched_records": int(metadata.get("matched_records") or 0),
        "matched_genes": int(metadata.get("matched_genes") or 0),
        "matched_elements": int(metadata.get("matched_elements") or 0),
        "plot_file_count": len([p for p in output_files if p.suffix.lower() in {".png", ".pdf", ".svg"}]),
    }
    manifest = {
        "manifest_version": "1.0",
        "stage": "promoter_plot_revise",
        "status": "success" if success else "failed",
        "prefix": args.prefix,
        "source_stage": "promoter",
        "display_stage": "promoter",
        "output_prefix": output_prefix,
        "created_at": datetime.now().isoformat(),
        "parameters": {
            "output_label": output_label,
            "selected_elements": elements,
            "selected_categories": categories,
            "match_mode": args.match_mode,
            "plot_modes": plot_modes,
            "top_n": args.top_n,
            "color_by": args.color_by,
            "resolved_color_by": metadata.get("resolved_color_by", ""),
            "legend_groups": metadata.get("legend_groups", ""),
            "source_promoter_table": str(promoter_file),
            "source_tree": str(tree_file),
            "source_subfamily": str(subfamily_file or ""),
            "scope_file": args.scope_file,
        },
        "artifacts": artifacts,
        "metrics": metrics,
        "stdout_tail": proc.stdout[-4000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-4000:] if proc.stderr else "",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    labeled_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if success:
        print(f"Promoter custom plot complete: {manifest_path}")
        return 0
    print(f"Promoter custom plot failed: {manifest_path}", file=sys.stderr)
    return proc.returncode or 2


if __name__ == "__main__":
    sys.exit(main())
