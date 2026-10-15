#!/usr/bin/env python3
"""Extract FASTA records by user-provided IDs.

This stage is intentionally independent from identify/genome_prepare.  It is
for personalized sequence extraction from uploaded or existing FASTA files.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


FASTA_SUFFIXES = {".fa", ".fasta", ".faa", ".fna", ".ffn", ".pep", ".cds"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 镜像标准形态: 参考数据走 BIOFLOW_DATA 挂载根(环境变量覆盖), 不再硬编码宿主路径
_BIOFLOW_DATA = Path(os.environ.get("BIOFLOW_DATA", "")).expanduser() or Path("/bioflow-data")
DEFAULT_ATH_PEP = _BIOFLOW_DATA / "raw_genome/ath/Arabidopsis_thaliana.TAIR10.pep.all.fa"
DEFAULT_ATH_CDS = _BIOFLOW_DATA / "raw_genome/ath/Arabidopsis_thaliana.TAIR10.cds.all.fa"
HEADER_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]*[A-Za-z0-9]")
COMMON_HEADER_WORDS = {
    "id", "gene", "gene_id", "genes", "locus", "locus_id", "protein", "pep",
    "cds", "sequence", "seqid", "name", "family",
}


@dataclass
class FastaRecord:
    record_no: int
    primary_id: str
    header: str
    sequence: str
    source: Path


def normalize_id(value: str) -> str:
    text = str(value or "").strip()
    text = text.lstrip(">")
    text = re.sub(r"^(gene:|gene=|id=|name=|locus_tag=)", "", text, flags=re.I)
    text = text.strip()
    text = re.sub(r"\.(?:t|p)?\d+$", "", text, flags=re.I)
    text = re.sub(r"-P[A-Z]?$", "", text, flags=re.I)
    return text.upper()


def split_inline_ids(text: str) -> list[str]:
    values = []
    for item in re.split(r"[\s,;，；]+", str(text or "")):
        item = item.strip().strip("'\"")
        if not item or item.lower() in COMMON_HEADER_WORDS:
            continue
        values.append(item)
    return values


def read_ids(ids_file: str | None, inline_ids: Iterable[str]) -> list[str]:
    ids: list[str] = []
    seen = set()

    def add(value: str) -> None:
        value = value.strip().strip("'\"")
        if not value or value.startswith("#"):
            return
        if value.lower() in COMMON_HEADER_WORDS:
            return
        if value not in seen:
            seen.add(value)
            ids.append(value)

    if ids_file:
        path = Path(ids_file).expanduser()
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.excel_tab if "\t" in sample else csv.excel
            for row in csv.reader(handle, dialect=dialect):
                if not row:
                    continue
                first = row[0].strip()
                if not first or first.startswith("#"):
                    continue
                if first.lower() in COMMON_HEADER_WORDS:
                    continue
                # If the first column itself contains whitespace-separated IDs,
                # accept them. This handles pasted one-line lists.
                parts = split_inline_ids(first)
                if len(parts) > 1:
                    for part in parts:
                        add(part)
                else:
                    add(first)

    for text in inline_ids:
        for item in split_inline_ids(text):
            add(item)
    return ids


def fasta_candidates(workspace: Path, seq_type: str) -> list[Path]:
    roots = [
        workspace / "input",
        workspace / "final",
        workspace,
    ]
    # The web app stores uploads in project input/<job_id>, not workspace/input.
    job_input = Path("input") / workspace.name
    if job_input.exists():
        roots.insert(0, job_input)

    candidates: list[Path] = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in FASTA_SUFFIXES:
                continue
            if path.stat().st_size <= 0:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(path)

    def score(path: Path) -> tuple[int, float, str]:
        name = path.name.lower()
        value = 0
        if seq_type == "pep":
            if any(x in name for x in ["pep", "protein", "faa"]):
                value -= 20
            if any(x in name for x in ["cds", "fna", "ffn", "genome"]):
                value += 20
        elif seq_type == "cds":
            if any(x in name for x in ["cds", "fna", "ffn"]):
                value -= 20
            if any(x in name for x in ["pep", "protein", "faa"]):
                value += 20
        else:
            if any(x in name for x in ["final_members", "pep", "protein", "cds"]):
                value -= 10
        return (value, -path.stat().st_mtime, str(path))

    return sorted(candidates, key=score)


def _species_code_from_prefix(prefix: str) -> str:
    token = str(prefix or "").strip().split(".", 1)[0].lower()
    if token in {"at", "ath"}:
        return "At"
    return ""


def _add_bundled_reference_candidates(candidates: list[Path], seq_type: str, preferred_prefix: str) -> list[Path]:
    species = _species_code_from_prefix(preferred_prefix)
    if species != "At":
        return candidates
    extra: list[Path] = []
    if seq_type in {"pep", "fasta"} and DEFAULT_ATH_PEP.exists():
        extra.append(DEFAULT_ATH_PEP)
    if seq_type in {"cds", "fasta"} and DEFAULT_ATH_CDS.exists():
        extra.append(DEFAULT_ATH_CDS)
    seen = {str(path.resolve()) for path in candidates if path.exists()}
    for path in extra:
        key = str(path.resolve())
        if key not in seen:
            candidates.append(path)
            seen.add(key)
    return candidates


def choose_fastas(workspace: Path, explicit_fastas: list[str], seq_type: str, preferred_prefix: str = "") -> tuple[list[Path], list[Path], str | None]:
    if explicit_fastas:
        paths = [Path(p).expanduser() for p in explicit_fastas if str(p).strip()]
        missing = [p for p in paths if not p.exists()]
        if missing:
            return [], paths, f"FASTA file not found: {missing[0]}"
        return paths, paths, None

    candidates = _add_bundled_reference_candidates(fasta_candidates(workspace, seq_type), seq_type, preferred_prefix)
    if not candidates:
        return [], [], "No FASTA file found. Please upload a FASTA or specify --fasta."
    if len(candidates) == 1:
        return [candidates[0]], candidates, None

    prefix = str(preferred_prefix or "").strip()
    if prefix:
        exact_names = [
            f"{prefix}.final_members.pep.fa",
            f"{prefix}.final_members.pep.fasta",
            f"{prefix}.final_members.cds.fa",
            f"{prefix}.final_members.cds.fasta",
        ]
        exact_candidates = [
            workspace / "final" / name for name in exact_names
            if (workspace / "final" / name).exists()
        ]
        if len(exact_candidates) == 1:
            return [exact_candidates[0]], candidates, None
        prefix_lower = prefix.lower()
        preferred = [
            path for path in candidates
            if prefix_lower in path.name.lower()
            and any(token in path.name.lower() for token in ["final_members", "sequence_extract", "pep", "protein"])
        ]
        preferred = [path for path in preferred if "sequence_clean" not in path.name.lower()]
        if len(preferred) == 1:
            return [preferred[0]], candidates, None
        final_members = [
            path for path in preferred
            if "final_members" in path.name.lower()
            and "interproscan" not in path.name.lower()
        ]
        if len(final_members) == 1:
            return [final_members[0]], candidates, None
        workspace_final_members = [path for path in final_members if path.parent == workspace / "final"]
        if len(workspace_final_members) == 1:
            return [workspace_final_members[0]], candidates, None

    species = _species_code_from_prefix(prefix)
    if species == "At":
        if seq_type == "pep" and DEFAULT_ATH_PEP.exists():
            return [DEFAULT_ATH_PEP], candidates, None
        if seq_type == "cds" and DEFAULT_ATH_CDS.exists():
            return [DEFAULT_ATH_CDS], candidates, None

    # If a sequence type was requested, accept a single clear top-scoring group.
    top = candidates[0]
    top_name = top.name.lower()
    if seq_type == "pep" and any(x in top_name for x in ["pep", "protein", "faa"]):
        second_name = candidates[1].name.lower()
        if not any(x in second_name for x in ["pep", "protein", "faa"]):
            return [top], candidates, None
    if seq_type == "cds" and any(x in top_name for x in ["cds", "fna", "ffn"]):
        second_name = candidates[1].name.lower()
        if not any(x in second_name for x in ["cds", "fna", "ffn"]):
            return [top], candidates, None

    return [], candidates, "Multiple FASTA candidates found; specify the source FASTA to avoid extracting from the wrong file."


def parse_fasta(path: Path) -> list[FastaRecord]:
    records: list[FastaRecord] = []
    header = ""
    seq_lines: list[str] = []
    record_no = 0

    def flush() -> None:
        nonlocal record_no, header, seq_lines
        if not header:
            return
        record_no += 1
        primary = header[1:].strip().split()[0] if header.startswith(">") else header.strip().split()[0]
        records.append(FastaRecord(record_no, primary, header, "".join(seq_lines), path))

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.rstrip("\n\r")
            if line.startswith(">"):
                flush()
                header = line
                seq_lines = []
            elif header:
                seq_lines.append(line.strip())
    flush()
    return records


def header_keys(record: FastaRecord) -> set[str]:
    keys = {record.primary_id}
    text = record.header[1:] if record.header.startswith(">") else record.header
    for token in re.split(r"[\s|;,]+", text):
        token = token.strip().strip("'\"")
        if token:
            keys.add(token)
        for match in re.finditer(r"(?:gene|gene_id|locus_tag|ID|Name)=([^;\s,]+)", token):
            keys.add(match.group(1))
    # Generic extraction covers headers such as "gene:AT1G01010.1".
    for token in HEADER_ID_RE.findall(text):
        if len(token) >= 3 and token.lower() not in COMMON_HEADER_WORDS:
            keys.add(token)
    return keys


def build_index(records: list[FastaRecord]) -> tuple[dict[str, list[FastaRecord]], dict[str, list[FastaRecord]]]:
    exact: dict[str, list[FastaRecord]] = defaultdict(list)
    normalized: dict[str, list[FastaRecord]] = defaultdict(list)
    for record in records:
        for key in header_keys(record):
            exact[key].append(record)
            norm = normalize_id(key)
            if norm:
                normalized[norm].append(record)
    return exact, normalized


def unique_records(items: Iterable[FastaRecord]) -> list[FastaRecord]:
    seen = set()
    out = []
    for item in items:
        key = (str(item.source), item.record_no)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def resolve_query(query: str, exact: dict[str, list[FastaRecord]], normalized: dict[str, list[FastaRecord]]) -> tuple[str, list[FastaRecord]]:
    if query in exact:
        return "exact", unique_records(exact[query])
    norm = normalize_id(query)
    if norm in normalized:
        return "normalized", unique_records(normalized[norm])
    return "unresolved", []


def renamed_header(record: FastaRecord, query: str, rename_mode: str, prefix: str, index: int) -> str:
    if rename_mode == "query":
        return f">{query} source={record.primary_id} file={record.source.name}"
    if rename_mode == "prefix_index":
        return f">{prefix}_{index:04d} query={query} source={record.primary_id} file={record.source.name}"
    if rename_mode == "simple":
        return f">{record.primary_id}"
    return record.header


def write_outputs(
    final_dir: Path,
    prefix: str,
    seq_type: str,
    ids: list[str],
    records: list[FastaRecord],
    exact: dict[str, list[FastaRecord]],
    normalized: dict[str, list[FastaRecord]],
    rename_mode: str,
) -> dict:
    suffix = {"pep": "pep.fa", "cds": "cds.fa"}.get(seq_type, "fa")
    out_fa = final_dir / f"{prefix}.sequence_extract.{suffix}"
    mapping_tsv = final_dir / f"{prefix}.sequence_extract.mapping.tsv"
    unresolved_tsv = final_dir / f"{prefix}.sequence_extract.unresolved.tsv"
    ambiguous_tsv = final_dir / f"{prefix}.sequence_extract.ambiguous.tsv"
    report_md = final_dir / f"{prefix}.sequence_extract.report.md"

    selected: list[tuple[str, str, FastaRecord]] = []
    unresolved: list[str] = []
    ambiguous: list[tuple[str, str, list[FastaRecord]]] = []

    for query in ids:
        mode, hits = resolve_query(query, exact, normalized)
        if not hits:
            unresolved.append(query)
        elif len(hits) > 1:
            ambiguous.append((query, mode, hits))
        else:
            selected.append((query, mode, hits[0]))

    with out_fa.open("w", encoding="utf-8") as handle:
        for idx, (query, _mode, record) in enumerate(selected, start=1):
            handle.write(renamed_header(record, query, rename_mode, prefix, idx) + "\n")
            seq = record.sequence
            for i in range(0, len(seq), 60):
                handle.write(seq[i:i + 60] + "\n")

    with mapping_tsv.open("w", encoding="utf-8") as handle:
        handle.write("query_id\tmatch_mode\trecord_id\tsource_fasta\trecord_no\toutput_header\n")
        for idx, (query, mode, record) in enumerate(selected, start=1):
            out_header = renamed_header(record, query, rename_mode, prefix, idx).lstrip(">")
            handle.write(f"{query}\t{mode}\t{record.primary_id}\t{record.source}\t{record.record_no}\t{out_header}\n")

    with unresolved_tsv.open("w", encoding="utf-8") as handle:
        handle.write("query_id\n")
        for query in unresolved:
            handle.write(f"{query}\n")

    with ambiguous_tsv.open("w", encoding="utf-8") as handle:
        handle.write("query_id\tmatch_mode\tcandidate_record_id\tsource_fasta\trecord_no\theader\n")
        for query, mode, hits in ambiguous:
            for record in hits:
                handle.write(f"{query}\t{mode}\t{record.primary_id}\t{record.source}\t{record.record_no}\t{record.header}\n")

    status = "success"
    if unresolved or ambiguous:
        status = "partial_success" if selected else "failed"

    report_md.write_text(
        "\n".join([
            f"# Sequence Extract Report",
            "",
            f"- Prefix: `{prefix}`",
            f"- Status: `{status}`",
            f"- Requested IDs: {len(ids)}",
            f"- Extracted records: {len(selected)}",
            f"- Unresolved IDs: {len(unresolved)}",
            f"- Ambiguous IDs: {len(ambiguous)}",
            f"- Source FASTA files: {len({str(r.source) for r in records})}",
            "",
            "Unresolved or ambiguous IDs are written to TSV files for user review.",
        ]) + "\n",
        encoding="utf-8",
    )

    return {
        "status": status,
        "selected": selected,
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "artifacts": {
            "extracted_fasta": {"path": str(out_fa), "type": "fasta"},
            "mapping": {"path": str(mapping_tsv), "type": "tsv"},
            "unresolved": {"path": str(unresolved_tsv), "type": "tsv"},
            "ambiguous": {"path": str(ambiguous_tsv), "type": "tsv"},
            "report": {"path": str(report_md), "type": "markdown"},
        },
    }


def create_family_reference_alias(prefix: str, fasta_path: Path) -> tuple[Path | None, str]:
    """Expose Species.Family sequence extracts through the standard family PEP name."""
    parts = [part.strip() for part in str(prefix or "").split(".") if part.strip()]
    if len(parts) < 2 or not fasta_path.exists() or fasta_path.stat().st_size == 0:
        return None, ""
    alias_path = fasta_path.parent / f"{parts[0]}.{parts[1]}.pep.fa"
    if alias_path.resolve() == fasta_path.resolve():
        return alias_path, "self"
    try:
        if alias_path.exists() or alias_path.is_symlink():
            if alias_path.is_symlink() and alias_path.resolve() == fasta_path.resolve():
                return alias_path, "symlink"
            alias_path.unlink()
        alias_path.symlink_to(fasta_path.name)
        return alias_path, "symlink"
    except Exception:
        try:
            shutil.copy2(fasta_path, alias_path)
            return alias_path, "copy"
        except Exception:
            return None, ""


def write_clean_all_outputs(final_dir: Path, prefix: str, seq_type: str, records: list[FastaRecord]) -> dict:
    suffix = {"pep": "pep.fa", "cds": "cds.fa"}.get(seq_type, "fa")
    out_fa = final_dir / f"{prefix}.sequence_clean.{suffix}"
    mapping_tsv = final_dir / f"{prefix}.sequence_clean.mapping.tsv"
    report_md = final_dir / f"{prefix}.sequence_clean.report.md"

    with out_fa.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(f">{record.primary_id}\n")
            seq = record.sequence
            for i in range(0, len(seq), 60):
                handle.write(seq[i:i + 60] + "\n")

    with mapping_tsv.open("w", encoding="utf-8") as handle:
        handle.write("record_no\tsource_fasta\told_header\tnew_header\n")
        for record in records:
            handle.write(f"{record.record_no}\t{record.source}\t{record.header.lstrip('>')}\t{record.primary_id}\n")

    report_md.write_text(
        "\n".join([
            "# Sequence Header Clean Report",
            "",
            f"- Prefix: `{prefix}`",
            "- Mode: `clean_all_headers`",
            f"- Cleaned records: {len(records)}",
            f"- Source FASTA files: {len({str(r.source) for r in records})}",
            f"- Output FASTA: `{out_fa}`",
            "",
            "Each output FASTA header keeps only the first whitespace-delimited ID token from the original header.",
        ]) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "success",
        "artifacts": {
            "cleaned_fasta": {"path": str(out_fa), "type": "fasta"},
            "mapping": {"path": str(mapping_tsv), "type": "tsv"},
            "report": {"path": str(report_md), "type": "markdown"},
        },
        "metrics": {
            "cleaned_records": len(records),
            "source_fastas": len({str(r.source) for r in records}),
        },
    }


def write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract FASTA records by ID list or inline IDs.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("-p", "--prefix", default="sequence_extract")
    parser.add_argument("-j", "--job-id", default="")
    parser.add_argument("--fasta", action="append", default=[], help="Source FASTA path. Can be repeated.")
    parser.add_argument("--ids-file", default="")
    parser.add_argument("--id", action="append", default=[], help="Single ID. Can be repeated.")
    parser.add_argument("--ids", default="", help="Comma/space/newline separated IDs.")
    parser.add_argument("--seq-type", choices=["auto", "pep", "cds", "fasta"], default="auto")
    parser.add_argument("--rename-mode", choices=["keep", "query", "prefix_index", "simple"], default="keep")
    parser.add_argument("--clean-all-headers", action="store_true", help="Clean all FASTA headers without requiring an ID list.")
    args = parser.parse_args()

    start = time.time()
    workspace = Path(args.workspace).expanduser()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = workspace / "sequence_extract_by_id" / "analysis"
    stage_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or "sequence_extract"
    seq_type = "fasta" if args.seq_type == "auto" else args.seq_type
    manifest_path = final_dir / f"{prefix}.sequence_extract.manifest.json"

    try:
        ids = read_ids(args.ids_file or None, [*args.id, args.ids])
        if not ids and not args.clean_all_headers:
            raise ValueError("No query IDs provided. Use --ids-file, --id, --ids, or --clean-all-headers.")

        fastas, candidates, error = choose_fastas(workspace, args.fasta, seq_type, prefix)
        if error:
            status = "needs_user_confirmation" if candidates else "failed"
            manifest = {
                "manifest_version": "1.0",
                "stage": "sequence_extract_by_id",
                "status": status,
                "prefix": prefix,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "message": error,
                "inputs": {"ids_file": args.ids_file, "inline_ids": args.id + ([args.ids] if args.ids else []), "fasta": args.fasta},
                "candidates": [str(p) for p in candidates[:50]],
                "metrics": {"requested_ids": len(ids), "candidate_fastas": len(candidates)},
                "artifacts": {},
                "elapsed_sec": round(time.time() - start, 3),
            }
            write_manifest(manifest_path, manifest)
            return 2

        records: list[FastaRecord] = []
        for fasta in fastas:
            records.extend(parse_fasta(fasta))
        if not records:
            raise ValueError("No FASTA records were found in the selected source file(s).")

        if args.clean_all_headers:
            result = write_clean_all_outputs(final_dir, prefix, seq_type, records)
            manifest = {
                "manifest_version": "1.0",
                "stage": "sequence_extract_by_id",
                "status": result["status"],
                "prefix": prefix,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mode": "clean_all_headers",
                "inputs": {
                    "fasta": [str(p) for p in fastas],
                    "seq_type": args.seq_type,
                    "rename_mode": "simple",
                },
                "metrics": result["metrics"],
                "artifacts": result["artifacts"],
                "elapsed_sec": round(time.time() - start, 3),
            }
            write_manifest(manifest_path, manifest)
            print("Sequence header clean status: success")
            print(f"Cleaned records: {result['metrics']['cleaned_records']}")
            print(f"Manifest: {manifest_path}")
            return 0

        exact, normalized = build_index(records)
        result = write_outputs(final_dir, prefix, seq_type, ids, records, exact, normalized, args.rename_mode)
        standard_family_pep, standard_family_pep_mode = (None, "")
        if seq_type == "pep" and result["status"] in {"success", "partial_success"} and result["selected"]:
            extracted = Path(result["artifacts"]["extracted_fasta"]["path"])
            standard_family_pep, standard_family_pep_mode = create_family_reference_alias(prefix, extracted)
            if standard_family_pep:
                result["artifacts"]["standard_family_pep"] = {
                    "path": str(standard_family_pep),
                    "type": "fasta",
                    "source": str(extracted),
                    "mode": standard_family_pep_mode,
                }
        manifest = {
            "manifest_version": "1.0",
            "stage": "sequence_extract_by_id",
            "status": result["status"],
            "prefix": prefix,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "inputs": {
                "ids_file": args.ids_file,
                "inline_ids": args.id + ([args.ids] if args.ids else []),
                "fasta": [str(p) for p in fastas],
                "seq_type": args.seq_type,
                "rename_mode": args.rename_mode,
            },
            "metrics": {
                "requested_ids": len(ids),
                "source_fastas": len(fastas),
                "source_records": len(records),
                "extracted_records": len(result["selected"]),
                "unresolved_ids": len(result["unresolved"]),
                "ambiguous_ids": len(result["ambiguous"]),
            },
            "artifacts": result["artifacts"],
            "elapsed_sec": round(time.time() - start, 3),
        }
        if standard_family_pep:
            manifest["standard_family_pep"] = str(standard_family_pep)
            manifest["standard_family_pep_mode"] = standard_family_pep_mode
        write_manifest(manifest_path, manifest)
        print(f"Sequence extract status: {result['status']}")
        print(f"Extracted records: {len(result['selected'])}")
        print(f"Manifest: {manifest_path}")
        return 0 if result["status"] in {"success", "partial_success"} else 1
    except Exception as exc:
        manifest = {
            "manifest_version": "1.0",
            "stage": "sequence_extract_by_id",
            "status": "failed",
            "prefix": prefix,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message": str(exc),
            "metrics": {},
            "artifacts": {},
            "elapsed_sec": round(time.time() - start, 3),
        }
        write_manifest(manifest_path, manifest)
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
