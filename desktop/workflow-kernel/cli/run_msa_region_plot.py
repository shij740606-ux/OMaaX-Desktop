#!/usr/bin/env python3
"""
Draw a selected region from an existing phylogeny MSA.

This is intentionally a post-processing helper: it does not change the
phylogeny stage or its default full-length MSA figures.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context

DEFAULT_RSCRIPT = Path(shutil.which("Rscript") or "Rscript")


FastaRecords = List[Tuple[str, str]]


def read_fasta(path: Path) -> FastaRecords:
    records: FastaRecords = []
    header: Optional[str] = None
    chunks: List[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    records.append((header, "".join(chunks)))
                header = line[1:].strip()
                chunks = []
            else:
                chunks.append(line)
    if header is not None:
        records.append((header, "".join(chunks)))
    return records


def write_fasta(path: Path, records: FastaRecords, width: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for header, seq in records:
            handle.write(f">{header}\n")
            for idx in range(0, len(seq), width):
                handle.write(seq[idx:idx + width] + "\n")


def validate_alignment(records: FastaRecords) -> int:
    if not records:
        raise ValueError("MSA fasta contains no records")
    lengths = {len(seq) for _, seq in records}
    if len(lengths) != 1:
        raise ValueError(f"MSA sequence lengths are not equal: {sorted(lengths)[:5]}")
    return lengths.pop()


def conservation_profile(records: FastaRecords) -> Tuple[List[float], List[float]]:
    aln_len = validate_alignment(records)
    seqs = [seq for _, seq in records]
    scores: List[float] = []
    nongaps: List[float] = []

    for col_idx in range(aln_len):
        col = [seq[col_idx] for seq in seqs]
        residues = [char for char in col if char not in {"-", "."}]
        nongap_frac = len(residues) / len(col)
        nongaps.append(nongap_frac)
        if not residues:
            scores.append(0.0)
            continue
        counts: Dict[str, int] = {}
        for residue in residues:
            counts[residue] = counts.get(residue, 0) + 1
        scores.append((max(counts.values()) / len(residues)) * nongap_frac)
    return scores, nongaps


def reference_residue_range_to_alignment_cols(
    records: FastaRecords,
    reference_id: str,
    start_residue: int,
    end_residue: int,
) -> Tuple[int, int, str]:
    if start_residue < 1:
        raise ValueError("--reference-start-residue must be >= 1")
    if end_residue < start_residue:
        raise ValueError("--reference-end-residue must be >= --reference-start-residue")
    needle = (reference_id or "").strip()
    if not needle:
        raise ValueError("--reference-id is required for reference-residue mode")

    matched_header = None
    matched_seq = None
    for header, seq in records:
        primary_id = header.split()[0]
        if header == needle or primary_id == needle or needle in header:
            matched_header = header
            matched_seq = seq
            break
    if matched_seq is None or matched_header is None:
        available = ", ".join(header.split()[0] for header, _ in records[:10])
        raise ValueError(f"Reference sequence not found: {needle}. Available examples: {available}")

    residue_to_col: Dict[int, int] = {}
    residue_idx = 0
    for col_idx, char in enumerate(matched_seq, start=1):
        if char not in {"-", "."}:
            residue_idx += 1
            residue_to_col[residue_idx] = col_idx

    if start_residue not in residue_to_col or end_residue not in residue_to_col:
        raise ValueError(
            f"Reference residue range {start_residue}-{end_residue} exceeds "
            f"nongap length {residue_idx} for {matched_header.split()[0]}"
        )
    return residue_to_col[start_residue], residue_to_col[end_residue], matched_header


def best_window(
    scores: Sequence[float],
    nongaps: Sequence[float],
    window_len: int,
    min_nongap: float,
) -> Tuple[int, int, float, float]:
    if window_len < 1:
        raise ValueError("--window-len must be >= 1")
    if not scores:
        raise ValueError("cannot choose a conserved window from an empty alignment")

    aln_len = len(scores)
    win = min(window_len, aln_len)
    best_idx: Optional[int] = None
    best_score = -1.0
    best_nongap = 0.0

    for idx in range(0, aln_len - win + 1):
        mean_nongap = sum(nongaps[idx:idx + win]) / win
        if mean_nongap < min_nongap:
            continue
        mean_score = sum(scores[idx:idx + win]) / win
        if mean_score > best_score:
            best_idx = idx
            best_score = mean_score
            best_nongap = mean_nongap

    if best_idx is None:
        for idx in range(0, aln_len - win + 1):
            mean_score = sum(scores[idx:idx + win]) / win
            if mean_score > best_score:
                best_idx = idx
                best_score = mean_score
                best_nongap = sum(nongaps[idx:idx + win]) / win

    assert best_idx is not None
    return best_idx + 1, best_idx + win, best_score, best_nongap


def extract_region(records: FastaRecords, start_col: int, end_col: int) -> FastaRecords:
    aln_len = validate_alignment(records)
    if start_col < 1:
        raise ValueError("--start-col must be >= 1")
    if end_col < start_col:
        raise ValueError("--end-col must be >= --start-col")
    if end_col > aln_len:
        raise ValueError(f"--end-col {end_col} exceeds alignment length {aln_len}")
    start_idx = start_col - 1
    return [(header, seq[start_idx:end_col]) for header, seq in records]


def resolve_msa(workspace: Path, prefix: str, msa_file: Optional[str]) -> Path:
    if msa_file:
        path = Path(msa_file).expanduser().resolve()
    else:
        path = (workspace / "final" / f"{prefix}.pep.msa.fa").resolve()
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"MSA file not found or empty: {path}")
    return path


def collect_outputs(output_dir: Path, outbase: str) -> List[str]:
    return sorted(
        str(path.resolve())
        for path in output_dir.iterdir()
        if path.is_file() and path.name.startswith(outbase)
    )


def run_r_plot(
    region_fasta: Path,
    output_dir: Path,
    outbase: str,
    r_script: Path,
    rscript: Path,
    method: str,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(rscript),
        str(r_script),
        str(region_fasta.resolve()),
        outbase,
        "--method",
        method,
    ]
    result = subprocess.run(
        cmd,
        cwd=str(output_dir),
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    return {
        "command": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "success": result.returncode == 0,
    }


def write_profile(path: Path, scores: Sequence[float], nongaps: Sequence[float]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("col_1based\tscore\tnongap_frac\n")
        for idx, (score, nongap) in enumerate(zip(scores, nongaps), start=1):
            handle.write(f"{idx}\t{score:.6f}\t{nongap:.6f}\n")



def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def route_from_prefix(prefix: str) -> str:
    text = str(prefix or "")
    if ".subfamily_evidence" in text:
        return "subfamily_evidence"
    if ".subfamily_prediction" in text:
        return "subfamily_prediction"
    return ""


def find_source_manifest(workspace: Path, prefix: str, msa_file: Path, explicit: str = "") -> tuple[dict[str, Any], str]:
    candidates: list[Path] = []
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend([path, PROJECT_ROOT / path, workspace / path])
    final_dir = workspace / "final"
    if prefix:
        candidates.extend([
            final_dir / f"{prefix}.phylogeny.manifest.json",
            final_dir / f"{prefix}.reference_classification.manifest.json",
        ])
        candidates.extend(sorted(final_dir.glob(f"{prefix}*.phylogeny.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(final_dir.glob(f"{prefix}*.reference_classification.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    candidates.extend(sorted(final_dir.glob("*.phylogeny.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True))

    seen: set[Path] = set()
    msa_resolved = str(msa_file.resolve()) if msa_file.exists() else str(msa_file)
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        manifest = read_json_dict(candidate)
        if not manifest:
            continue
        out_prefix = str(manifest.get("output_prefix") or (manifest.get("parameters") or {}).get("output_prefix") or "")
        if prefix and out_prefix == prefix:
            manifest["_manifest_path"] = str(candidate.resolve())
            return manifest, str(candidate.resolve())
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        paths = []
        for value in artifacts.values():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    paths.extend([str(item.get("full_path") or ""), str(item.get("path") or "")])
                elif isinstance(item, str):
                    paths.append(item)
        params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        paths.extend([str(params.get("msa_file") or ""), str(params.get("scope_input_fasta") or "")])
        if any(path and (path == msa_resolved or Path(path).name == msa_file.name) for path in paths):
            manifest["_manifest_path"] = str(candidate.resolve())
            return manifest, str(candidate.resolve())
        if not prefix:
            manifest["_manifest_path"] = str(candidate.resolve())
            return manifest, str(candidate.resolve())
    return {}, ""


def scope_from_manifest(manifest: dict[str, Any], prefix: str) -> dict[str, Any]:
    if not manifest:
        return {}
    stage_context = manifest.get("stage_context") if isinstance(manifest.get("stage_context"), dict) else {}
    scope_obj = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}

    def pick(key: str, default: Any = "") -> Any:
        for source in (manifest, stage_context, scope_obj, params):
            if isinstance(source, dict) and source.get(key) not in (None, "", []):
                return source.get(key)
        return default

    scope_id = str(pick("scope_id", "") or "").strip()
    if not scope_id:
        return {}
    family = str(pick("family", "") or "").strip()
    if not family and ".target-" in scope_id:
        family = scope_id.split(".target-", 1)[0]
    if not family and "." in prefix:
        family = prefix.split(".")[-1]
    targets = [str(x) for x in (pick("target_species", []) or []) if str(x).strip()]
    refs = [str(x) for x in (pick("reference_species", []) or []) if str(x).strip()]
    comps = [str(x) for x in (pick("comparative_species", []) or []) if str(x).strip()]
    selected = [str(x) for x in (pick("selected_species", []) or []) if str(x).strip()] or [*targets, *refs, *comps]
    return {
        "family": family,
        "prefix": pick("prefix", prefix),
        "scope_id": scope_id,
        "scope_mode": str(pick("scope_mode", "") or ""),
        "target_species": targets,
        "reference_species": refs,
        "comparative_species": comps,
        "selected_species": selected,
    }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw a manually selected or auto-detected conserved region from final/{prefix}.pep.msa.fa"
    )
    parser.add_argument("--workspace", required=True, help="Workspace directory, e.g. workspace/job_xxx")
    parser.add_argument("--prefix", required=True, help="Analysis prefix, e.g. Ft.SPL")
    parser.add_argument("--msa-file", help="Override aligned FASTA; defaults to final/{prefix}.pep.msa.fa")
    parser.add_argument("--start-col", type=int, help="1-based inclusive alignment start column")
    parser.add_argument("--end-col", type=int, help="1-based inclusive alignment end column")
    parser.add_argument("--auto-conserved", action="store_true", help="Automatically choose the most conserved window")
    parser.add_argument("--reference-id", help="Reference sequence ID/header for residue-number mode")
    parser.add_argument("--reference-start-residue", type=int, help="1-based nongap residue start in the reference sequence")
    parser.add_argument("--reference-end-residue", type=int, help="1-based nongap residue end in the reference sequence")
    parser.add_argument("--window-len", type=int, default=60, help="Auto mode conserved window length (default: 60)")
    parser.add_argument("--min-nongap", type=float, default=0.6, help="Auto mode mean nongap threshold (default: 0.6)")
    parser.add_argument("--output-dir", help="Output directory; defaults to workspace/final")
    parser.add_argument("--output-prefix", help="Output prefix basename; defaults to {prefix}.msa_region_START_END")
    parser.add_argument("--r-script", default=str(PROJECT_ROOT / "scripts" / "msa_figure_from_aligned.R"))
    parser.add_argument("--rscript", default=str(DEFAULT_RSCRIPT if DEFAULT_RSCRIPT.exists() else shutil.which("Rscript") or "Rscript"))
    parser.add_argument("--method", default="mafft", help="Method label passed to R plotting script (default: mafft)")
    parser.add_argument("--scope-file", default="", help="Scope JSON used by the source phylogeny/reference-classification route")
    parser.add_argument("--analysis-route", default="", help="Analysis route identity copied into the manifest")
    parser.add_argument("--member-set-id", default="", help="Member-set identity copied into the manifest")
    parser.add_argument("--source-manifest", default="", help="Source phylogeny/reference-classification manifest used to inherit scope identity")
    parser.add_argument("--force", action="store_true", help="Overwrite existing region FASTA/metadata files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (workspace / "final").resolve()
    msa_file = resolve_msa(workspace, args.prefix, args.msa_file)
    r_script = Path(args.r_script).expanduser().resolve()
    rscript = Path(args.rscript).expanduser()
    source_manifest, source_manifest_path = find_source_manifest(workspace, args.prefix, msa_file, args.source_manifest)
    scope_data: dict[str, Any] = {}
    scope_file_path = Path(args.scope_file).expanduser() if args.scope_file else None
    if scope_file_path and scope_file_path.exists():
        scope_data = read_scope(scope_file_path)
    if not scope_data:
        scope_data = scope_from_manifest(source_manifest, args.prefix)
    analysis_route = args.analysis_route or str(source_manifest.get("analysis_route") or (source_manifest.get("stage_context") or {}).get("analysis_route") or (source_manifest.get("parameters") or {}).get("analysis_route") or "") or route_from_prefix(args.prefix)
    member_set_id = args.member_set_id or str(source_manifest.get("member_set_id") or (source_manifest.get("stage_context") or {}).get("member_set_id") or (source_manifest.get("parameters") or {}).get("member_set_id") or "")

    if not r_script.exists():
        raise FileNotFoundError(f"R plotting script not found: {r_script}")
    if not rscript.exists() and shutil.which(str(rscript)) is None:
        raise FileNotFoundError(f"Rscript executable not found: {rscript}")

    records = read_fasta(msa_file)
    aln_len = validate_alignment(records)
    scores, nongaps = conservation_profile(records)

    manual = args.start_col is not None or args.end_col is not None
    reference_mode = args.reference_id or args.reference_start_residue is not None or args.reference_end_residue is not None
    active_modes = sum([bool(manual), bool(args.auto_conserved), bool(reference_mode)])
    if active_modes != 1:
        raise ValueError("Use exactly one mode: --start-col/--end-col, --reference-id with reference residues, or --auto-conserved")

    reference_header = None
    reference_start_residue = None
    reference_end_residue = None
    if manual:
        if args.start_col is None or args.end_col is None:
            raise ValueError("Manual mode requires both --start-col and --end-col")
        start_col, end_col = args.start_col, args.end_col
        selection_mode = "manual"
    elif reference_mode:
        if not args.reference_id or args.reference_start_residue is None or args.reference_end_residue is None:
            raise ValueError("Reference-residue mode requires --reference-id, --reference-start-residue, and --reference-end-residue")
        reference_start_residue = args.reference_start_residue
        reference_end_residue = args.reference_end_residue
        start_col, end_col, reference_header = reference_residue_range_to_alignment_cols(
            records=records,
            reference_id=args.reference_id,
            start_residue=reference_start_residue,
            end_residue=reference_end_residue,
        )
        selection_mode = "reference_residue"
    else:
        start_col, end_col, mean_score, mean_nongap = best_window(
            scores=scores,
            nongaps=nongaps,
            window_len=args.window_len,
            min_nongap=args.min_nongap,
        )
        selection_mode = "auto_conserved"

    if selection_mode != "auto_conserved":
        extract_region(records, start_col, end_col)
        mean_score = sum(scores[start_col - 1:end_col]) / (end_col - start_col + 1)
        mean_nongap = sum(nongaps[start_col - 1:end_col]) / (end_col - start_col + 1)

    region_records = extract_region(records, start_col, end_col)
    default_outbase_kind = "msa_conserved_region" if selection_mode == "auto_conserved" else "msa_region"
    outbase = args.output_prefix or f"{args.prefix}.{default_outbase_kind}_{start_col}_{end_col}"
    region_fasta = output_dir / f"{outbase}.fa"
    region_tsv = output_dir / f"{outbase}.region.tsv"
    profile_tsv = output_dir / f"{outbase}.conservation_profile.tsv"
    manifest_json = output_dir / f"{outbase}.manifest.json"

    for path in [region_fasta, region_tsv, profile_tsv, manifest_json]:
        if path.exists() and not args.force:
            raise FileExistsError(f"Output exists, use --force to overwrite: {path}")

    write_fasta(region_fasta, region_records)
    write_profile(profile_tsv, scores, nongaps)
    with region_tsv.open("w", encoding="utf-8") as handle:
        handle.write("prefix\tselection_mode\tmsa_file\talignment_length\tsequence_count\tstart_col_1based\tend_col_1based_inclusive\tregion_len\tmean_score\tmean_nongap\n")
        handle.write(
            f"{args.prefix}\t{selection_mode}\t{msa_file}\t{aln_len}\t{len(records)}\t"
            f"{start_col}\t{end_col}\t{end_col - start_col + 1}\t{mean_score:.6f}\t{mean_nongap:.6f}\n"
        )

    plot_result = run_r_plot(
        region_fasta=region_fasta,
        output_dir=output_dir,
        outbase=outbase,
        r_script=r_script,
        rscript=rscript,
        method=args.method,
    )
    output_files = collect_outputs(output_dir, outbase)

    manifest = {
        "manifest_version": "1.0",
        "stage": "msa_region_plot",
        "status": "success" if plot_result["success"] else "failed",
        "prefix": args.prefix,
        "output_prefix": outbase,
        "workspace": str(workspace),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": {
            "selection_mode": selection_mode,
            "start_col_1based": start_col,
            "end_col_1based_inclusive": end_col,
            "window_len": args.window_len if selection_mode == "auto_conserved" else None,
            "min_nongap": args.min_nongap if selection_mode == "auto_conserved" else None,
            "reference_id": reference_header,
            "reference_start_residue": reference_start_residue,
            "reference_end_residue": reference_end_residue,
            "scope_file": args.scope_file,
            "source_manifest": source_manifest_path,
            "source_stage": source_manifest.get("stage", "") if source_manifest else "",
            "source_output_prefix": source_manifest.get("output_prefix", "") if source_manifest else "",
            "analysis_route": analysis_route,
            "member_set_id": member_set_id,
        },
        "metrics": {
            "alignment_length": aln_len,
            "sequence_count": len(records),
            "region_length": end_col - start_col + 1,
            "mean_score": mean_score,
            "mean_nongap": mean_nongap,
        },
        "artifacts": {
            "source_msa": str(msa_file),
            "region_fasta": str(region_fasta),
            "region_tsv": str(region_tsv),
            "conservation_profile_tsv": str(profile_tsv),
            "output_files": output_files,
        },
        "plot": plot_result,
    }
    context = build_stage_context(
        stage="msa_region_plot",
        prefix=args.prefix,
        scope_data=scope_data,
        scope_file=args.scope_file,
        output_prefix=outbase,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
    )
    apply_stage_context(manifest, context)
    if source_manifest_path:
        manifest["source_manifest"] = source_manifest_path
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MSA region plot finished.")
    print(f"  status: {manifest['status']}")
    print(f"  source_msa: {msa_file}")
    print(f"  region: {start_col}-{end_col} ({selection_mode})")
    print(f"  mean_score: {mean_score:.6f}")
    print(f"  mean_nongap: {mean_nongap:.6f}")
    print(f"  region_fasta: {region_fasta}")
    print(f"  manifest: {manifest_json}")
    print("  outputs:")
    for path in output_files:
        print(f"    - {path}")

    return 0 if plot_result["success"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
