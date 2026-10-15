#!/usr/bin/env python3
"""Fetch protein and CDS sequences from NCBI by gene IDs or gene/family query.

This runner is intentionally conservative:
  * GeneID inputs can be fetched directly.
  * Symbol/locus/family queries must be scoped by --taxid or --species.
  * Ambiguous and unresolved queries are written to TSV instead of being guessed.
  * Family-mode downloads require --allow-family-download; otherwise a candidate
    table is produced for user confirmation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Iterable

from Bio import Entrez, SeqIO
from Bio.SeqRecord import SeqRecord


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EMAIL = os.environ.get("NCBI_EMAIL", "bbav5-user@example.org")
# 镜像标准形态: 参考数据走 BIOFLOW_DATA 挂载根(环境变量覆盖), 不再硬编码宿主路径
_BIOFLOW_DATA = Path(os.environ.get("BIOFLOW_DATA", "")).expanduser() or Path("/bioflow-data")
DEFAULT_ATH_PEP = _BIOFLOW_DATA / "raw_genome/ath/Arabidopsis_thaliana.TAIR10.pep.all.fa"
DEFAULT_ATH_CDS = _BIOFLOW_DATA / "raw_genome/ath/Arabidopsis_thaliana.TAIR10.cds.all.fa"
LOCUS_TAG_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*g\d+)", re.IGNORECASE)
TAIR_GENE_RE = re.compile(r"gene:(AT[0-9]G\d+)", re.IGNORECASE)
GENE_ID_RE = re.compile(r"(?:GeneID:|Acc:)(\d+)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return value.strip("._") or "query"


def read_query_file(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            ids.append(re.split(r"[\s,]+", text)[0])
    return ids


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def wrap_fasta(seq: str, width: int = 70) -> str:
    return "\n".join(seq[i:i + width] for i in range(0, len(seq), width))


def write_fasta(path: Path, records: list[dict[str, Any]], seq_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in records:
            seq = str(row.get(seq_key, "") or "").replace("\n", "").replace(" ", "")
            if not seq:
                continue
            header_parts = [
                row.get("input_id") or row.get("query") or row.get("gene_id"),
                f"GeneID:{row.get('gene_id', '')}",
                row.get("symbol", ""),
                row.get("transcript_accession", ""),
                row.get("protein_accession", ""),
            ]
            header = " ".join(str(item) for item in header_parts if item)
            handle.write(f">{header}\n{wrap_fasta(seq)}\n")


def create_family_reference_link(prefix: str, protein_fa: Path) -> Path | None:
    """Create the legacy family-reference name Species.Family.pep.fa for NCBI protein output."""
    parts = [p for p in safe_name(prefix).split(".") if p]
    if len(parts) < 2 or not protein_fa.exists() or protein_fa.stat().st_size == 0:
        return None
    link_path = protein_fa.parent / f"{parts[0]}.{parts[1]}.pep.fa"
    if link_path.resolve() == protein_fa.resolve():
        return link_path
    try:
        if link_path.exists() or link_path.is_symlink():
            if link_path.is_symlink() and link_path.resolve() == protein_fa.resolve():
                return link_path
            link_path.unlink()
        os.symlink(protein_fa.name, link_path)
        return link_path
    except Exception as exc:
        print(f"WARNING: failed to create family reference symlink {link_path}: {exc}")
        return None


def normalize_locus_tag(value: str) -> str:
    match = LOCUS_TAG_RE.match(str(value or "").strip())
    return match.group(1).upper() if match else ""


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        candidate = Path(path)
        if not candidate.exists():
            continue
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate.resolve())
    return unique


def _paths_from_genome_prepare_manifests(workspace: Path) -> tuple[list[Path], list[Path]]:
    pep_paths: list[Path] = []
    cds_paths: list[Path] = []
    final_dir = workspace / "final"
    if not final_dir.exists():
        return pep_paths, cds_paths
    for manifest_path in sorted(final_dir.glob("*.genome_prepare*.manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[WARN] Failed to read genome_prepare manifest {manifest_path}: {exc}")
            continue
        for artifact in (data.get("artifacts") or {}).values():
            raw_path = artifact.get("full_path") or artifact.get("path") or ""
            if not raw_path:
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
            if not candidate.exists():
                continue
            name = candidate.name.lower()
            kind = str(artifact.get("type") or artifact.get("artifact") or "").lower()
            if kind in {"pep", "all_pep", "protein", "fasta"} or "pep" in name or name.endswith(".faa"):
                pep_paths.append(candidate)
            if kind == "cds" or "cds" in name:
                cds_paths.append(candidate)
    return pep_paths, cds_paths


def discover_local_reference_files(workspace: Path) -> tuple[list[Path], list[Path]]:
    """Discover job-local and bundled reference proteome/CDS files (preferred over NCBI web)."""
    job_id = workspace.name
    input_dir = PROJECT_ROOT / "input" / job_id
    pep_paths: list[Path] = []
    cds_paths: list[Path] = []
    search_roots = [input_dir, workspace / "input", workspace / "genome_prepare"]
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file():
                continue
            name = candidate.name.lower()
            suffix = candidate.suffix.lower()
            if suffix not in {".fa", ".fasta", ".faa", ".fna"} and ".pep." not in name and ".cds." not in name:
                continue
            if "pep" in name or name.endswith(".faa") or ".protein." in name:
                pep_paths.append(candidate)
            if "cds" in name or name.endswith(".fna") or ".cds." in name:
                cds_paths.append(candidate)
    manifest_pep, manifest_cds = _paths_from_genome_prepare_manifests(workspace)
    pep_paths.extend(manifest_pep)
    cds_paths.extend(manifest_cds)
    for env_key, bucket in (
        ("NCBI_LOCAL_PEP_PATH", pep_paths),
        ("NCBI_LOCAL_CDS_PATH", cds_paths),
    ):
        env_path = os.environ.get(env_key, "").strip()
        if env_path:
            for item in env_path.split(":"):
                bucket.append(Path(item))
    if DEFAULT_ATH_PEP.exists():
        pep_paths.append(DEFAULT_ATH_PEP)
    if DEFAULT_ATH_CDS.exists():
        cds_paths.append(DEFAULT_ATH_CDS)
    return _dedupe_paths(pep_paths), _dedupe_paths(cds_paths)


def _index_local_record(
    index: dict[str, list[dict[str, Any]]],
    key: str,
    row: dict[str, Any],
) -> None:
    if not key:
        return
    index.setdefault(key, []).append(row)


def build_local_sequence_index(paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        try:
            for record in SeqIO.parse(str(path), "fasta"):
                header = f"{record.id} {record.description or ''}"
                locus = normalize_locus_tag(record.id) or normalize_locus_tag(record.description)
                tair_match = TAIR_GENE_RE.search(header)
                if tair_match:
                    locus = tair_match.group(1).upper()
                gene_id_match = GENE_ID_RE.search(header)
                row = {
                    "accession": str(record.id),
                    "description": str(record.description or ""),
                    "sequence": str(record.seq),
                    "source": str(path),
                    "gene_id": gene_id_match.group(1) if gene_id_match else "",
                    "isoform_rank": int(record.id.split(".")[-1]) if "." in record.id and record.id.split(".")[-1].isdigit() else 0,
                }
                if locus:
                    _index_local_record(index, f"locus:{locus}", row)
                if row["gene_id"]:
                    _index_local_record(index, f"gene_id:{row['gene_id']}", row)
                _index_local_record(index, f"accession:{record.id}", row)
                base_accession = record.id.split(".")[0]
                if base_accession != record.id:
                    _index_local_record(index, f"accession:{base_accession}", row)
        except Exception as exc:
            print(f"[WARN] Failed to index local reference file {path}: {exc}")
    return index


def build_local_reference_store(workspace: Path) -> LocalReferenceStore:
    pep_paths, cds_paths = discover_local_reference_files(workspace)
    return LocalReferenceStore(
        pep_index=build_local_sequence_index(pep_paths),
        cds_index=build_local_sequence_index(cds_paths),
        pep_sources=[str(path) for path in pep_paths],
        cds_sources=[str(path) for path in cds_paths],
    )


def lookup_local_candidates(
    index: dict[str, list[dict[str, Any]]],
    gene: dict[str, Any],
) -> list[dict[str, Any]]:
    keys: list[str] = []
    locus = normalize_locus_tag(gene.get("input_id") or gene.get("locus_tag") or "")
    if locus:
        keys.append(f"locus:{locus}")
    gene_id = str(gene.get("gene_id") or "")
    if gene_id:
        keys.append(f"gene_id:{gene_id}")
    input_id = str(gene.get("input_id") or "").strip()
    if input_id:
        keys.append(f"accession:{input_id}")
        keys.append(f"accession:{input_id.split('.')[0]}")
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key in keys:
        for row in index.get(key, []):
            marker = (row.get("accession", ""), row.get("source", ""))
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(row)
    if gene_id:
        matched = [row for row in merged if row.get("gene_id") == gene_id]
        if matched:
            return matched
    return merged


def _select_local_records(
    candidates: list[dict[str, Any]],
    transcript_policy: str,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    if transcript_policy == "all":
        return candidates
    if transcript_policy == "longest":
        return sorted(candidates, key=lambda row: len(str(row.get("sequence") or "")), reverse=True)[:1]
    return sorted(
        candidates,
        key=lambda row: (
            0 if str(row.get("accession", "")).endswith(".1") else 1,
            int(row.get("isoform_rank") or 999),
            -len(str(row.get("sequence") or "")),
        ),
    )[:1]


def fetch_gene_from_local_reference(
    store: LocalReferenceStore,
    gene: dict[str, Any],
    *,
    seq_types: str,
    transcript_policy: str,
) -> tuple[list[dict[str, Any]], str]:
    """Extract protein/CDS from local reference genome/proteome before NCBI web download."""
    if not store.pep_sources and not store.cds_sources:
        return [], "no local reference genome/proteome found in job input or bundled ref_data"

    need_protein = seq_types in {"protein", "both"}
    need_cds = seq_types in {"cds", "both"}
    pep_candidates = lookup_local_candidates(store.pep_index, gene) if need_protein else []
    cds_candidates = lookup_local_candidates(store.cds_index, gene) if need_cds else []

    if need_protein and not pep_candidates and need_cds and not cds_candidates:
        locus = normalize_locus_tag(gene.get("input_id") or gene.get("locus_tag") or "")
        hint = locus or gene.get("gene_id") or gene.get("input_id") or "query"
        return [], f"no local reference match for {hint}"

    selected_pep = _select_local_records(pep_candidates, transcript_policy) if pep_candidates else []
    selected_cds = _select_local_records(cds_candidates, transcript_policy) if cds_candidates else []

    if need_protein and not selected_pep and not (need_cds and selected_cds):
        return [], "local reference indexed but no matching protein/CDS record"
    if need_cds and not selected_cds and not (need_protein and selected_pep):
        return [], "local reference indexed but no matching CDS record"
    if need_protein and not selected_pep and need_cds and selected_cds:
        # Protein-only fallback from CDS record is not supported; caller may still use CDS.
        if seq_types == "cds":
            selected_pep = []
        else:
            return [], "local CDS found but protein sequence unavailable without NCBI"

    locus = normalize_locus_tag(gene.get("input_id") or gene.get("locus_tag") or "")
    pep_item = selected_pep[0] if selected_pep else None
    cds_item = selected_cds[0] if selected_cds else None
    if pep_item and cds_item and pep_item.get("accession") != cds_item.get("accession"):
        # Prefer paired .1 isoforms when both indexes exist.
        pass

    source_name = Path((pep_item or cds_item or {}).get("source", "")).name
    row = {
        **gene,
        "transcript_accession": (pep_item or cds_item or {}).get("accession", ""),
        "protein_accession": (pep_item or {}).get("accession", ""),
        "product": (pep_item or cds_item or {}).get("description", ""),
        "locus_tag": locus,
        "cds_length": len((cds_item or {}).get("sequence", "") or ""),
        "protein_length": len((pep_item or {}).get("sequence", "") or ""),
        "cds_sequence": (cds_item or {}).get("sequence", "") if need_cds else "",
        "protein_sequence": (pep_item or {}).get("sequence", "") if need_protein else "",
        "status": "resolved",
        "fetch_route": f"local_reference:{source_name or 'reference'}",
        "transcript_policy": transcript_policy,
        "transcript_candidates": max(len(pep_candidates), len(cds_candidates)),
    }
    if need_protein and not row["protein_sequence"]:
        return [], "local reference match missing protein sequence"
    if need_cds and not row["cds_sequence"]:
        return [], "local reference match missing CDS sequence"
    return [row], ""


@dataclass
class LocalReferenceStore:
    pep_index: dict[str, list[dict[str, Any]]]
    cds_index: dict[str, list[dict[str, Any]]]
    pep_sources: list[str]
    cds_sources: list[str]


@dataclass
class EntrezClient:
    email: str
    api_key: str = ""
    cache_dir: Path | None = None
    delay_sec: float = 0.34

    def __post_init__(self) -> None:
        Entrez.email = self.email
        if self.api_key:
            Entrez.api_key = self.api_key
            self.delay_sec = min(self.delay_sec, 0.11)
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, kind: str, key: str, suffix: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{kind}.{safe_name(key)[:60]}.{digest}.{suffix}"

    def _with_retries(self, label: str, call: Callable[[], Any], attempts: int = 5) -> Any:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                time.sleep(self.delay_sec)
                return call()
            except Exception as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                backoff = min(30.0, (2.0 ** attempt) + self.delay_sec * (attempt + 1))
                time.sleep(backoff)
        raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error

    def esearch(self, db: str, term: str, retmax: int = 50) -> dict[str, Any]:
        key = f"{db}|{term}|{retmax}"
        cache = self._cache_path("esearch", key, "json")
        if cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        def call() -> Any:
            with Entrez.esearch(db=db, term=term, retmax=retmax, retmode="xml") as handle:
                return Entrez.read(handle)
        data = self._with_retries(f"esearch:{db}", call)
        payload = json.loads(json.dumps(data))
        if cache:
            cache.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return payload

    def esummary_gene(self, gene_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = [str(i) for i in gene_ids if str(i).strip()]
        if not ids:
            return {}
        key = ",".join(ids)
        cache = self._cache_path("esummary_gene", key, "json")
        if cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        def call() -> Any:
            with Entrez.esummary(db="gene", id=key, retmode="xml") as handle:
                return Entrez.read(handle)
        data = self._with_retries("esummary:gene", call)
        result: dict[str, dict[str, Any]] = {}
        for item in data.get("DocumentSummarySet", {}).get("DocumentSummary", []):
            gene_id = str(item.attributes.get("uid") or item.get("uid") or "")
            result[gene_id] = json.loads(json.dumps(item))
        if cache:
            cache.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    def elink_ids(self, dbfrom: str, db: str, uid: str, preferred_terms: list[str] | None = None) -> list[dict[str, str]]:
        key = f"{dbfrom}|{db}|{uid}"
        cache = self._cache_path("elink", key, "json")
        if cache and cache.exists():
            linksets = json.loads(cache.read_text(encoding="utf-8"))
        else:
            def call() -> Any:
                with Entrez.elink(dbfrom=dbfrom, db=db, id=str(uid), retmode="xml") as handle:
                    return Entrez.read(handle)
            data = self._with_retries(f"elink:{dbfrom}->{db}", call)
            linksets = json.loads(json.dumps(data))
            if cache:
                cache.write_text(json.dumps(linksets, indent=2, ensure_ascii=False), encoding="utf-8")

        rows: list[dict[str, str]] = []
        for linkset in linksets:
            for linkdb in linkset.get("LinkSetDb", []) or []:
                linkname = str(linkdb.get("LinkName", ""))
                for item in linkdb.get("Link", []) or []:
                    rows.append({"id": str(item.get("Id", "")), "linkname": linkname})
        if preferred_terms:
            preferred = [row for row in rows if any(term.lower() in row["linkname"].lower() for term in preferred_terms)]
            if preferred:
                return preferred
        return rows

    def efetch_text(self, db: str, ids: Iterable[str], rettype: str, retmode: str = "text") -> str:
        clean_ids = [str(i) for i in ids if str(i).strip()]
        if not clean_ids:
            return ""
        key = f"{db}|{rettype}|{retmode}|{','.join(clean_ids)}"
        cache = self._cache_path("efetch", key, "txt")
        if cache and cache.exists():
            return cache.read_text(encoding="utf-8", errors="ignore")
        def call() -> str:
            with Entrez.efetch(db=db, id=",".join(clean_ids), rettype=rettype, retmode=retmode) as handle:
                return handle.read()
        text = self._with_retries(f"efetch:{db}:{rettype}", call)
        if cache:
            cache.write_text(text, encoding="utf-8")
        return text


def taxon_clause(taxid: str = "", species: str = "") -> str:
    if taxid:
        return f"txid{taxid}[Organism:exp]"
    if species:
        return f'"{species}"[Organism]'
    return ""


def build_gene_search_term(query: str, id_type: str, taxid: str = "", species: str = "", mode: str = "gene") -> str:
    taxon = taxon_clause(taxid, species)
    if id_type == "geneid" or re.fullmatch(r"\d+", query.strip()):
        term = f"{query.strip()}[uid]"
    elif id_type == "symbol":
        term = f"{query.strip()}[Gene Name]"
    elif id_type == "locus_tag":
        term = f"{query.strip()}[Locus Tag]"
    elif mode == "family":
        cleaned = query.strip()
        term = f"({cleaned}[All Fields] OR {cleaned} family[All Fields])"
    else:
        cleaned = query.strip()
        term = f"({cleaned}[Gene Name] OR {cleaned}[Locus Tag] OR {cleaned}[All Fields])"
    return f"({term}) AND {taxon}" if taxon else term


def gene_summary_taxid(summary: dict[str, Any]) -> str:
    organism = summary.get("Organism")
    if isinstance(organism, dict):
        return str(organism.get("TaxID") or organism.get("TaxId") or organism.get("taxid") or "")
    return str(summary.get("TaxID") or summary.get("TaxId") or summary.get("taxid") or "")


def gene_summary_organism(summary: dict[str, Any]) -> str:
    organism = summary.get("Organism")
    if isinstance(organism, dict):
        return str(organism.get("ScientificName") or organism.get("CommonName") or "")
    return str(organism or "")


def summary_exactly_matches_query(summary: dict[str, Any], query: str) -> bool:
    query_norm = str(query or "").strip().upper()
    if not query_norm:
        return False
    values = [
        summary.get("Name", ""),
        summary.get("NomenclatureSymbol", ""),
    ]
    for alias in re.split(r"[,;]\s*", str(summary.get("OtherAliases", "") or "")):
        values.append(alias)
    return any(str(value).strip().upper() == query_norm for value in values if str(value).strip())


def resolve_gene_queries(
    client: EntrezClient,
    queries: list[str],
    *,
    id_type: str,
    mode: str,
    taxid: str,
    species: str,
    retmax: int,
    allow_family_download: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    resolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for query in queries:
        query = str(query).strip()
        if not query:
            continue
        if (id_type in {"auto", "symbol", "locus_tag"} or mode == "family") and not (taxid or species) and not re.fullmatch(r"\d+", query):
            unresolved.append({
                "input_id": query,
                "reason": "symbol/locus/family query requires --taxid or --species",
            })
            continue

        term = build_gene_search_term(query, id_type, taxid, species, mode)
        search = client.esearch("gene", term, retmax=retmax)
        ids = [str(i) for i in search.get("IdList", [])]
        if not ids:
            unresolved.append({"input_id": query, "reason": "no NCBI Gene match", "search_term": term})
            continue
        if mode != "family" and len(ids) > 1:
            summaries = client.esummary_gene(ids)
            exact_ids = [gid for gid in ids if summary_exactly_matches_query(summaries.get(gid, {}), query)]
            if len(exact_ids) == 1:
                ids = exact_ids
            else:
                ambiguous.append({
                    "input_id": query,
                    "reason": f"matched {len(ids)} NCBI Gene records",
                    "search_term": term,
                    "candidate_gene_ids": ",".join(ids[:20]),
                    "candidate_symbols": ";".join(f"{gid}:{summaries.get(gid, {}).get('Name', '')}" for gid in ids[:20]),
                })
                continue
        if mode == "family" and len(ids) > 1 and not allow_family_download:
            summaries = client.esummary_gene(ids[:retmax])
            for gid in ids:
                summary = summaries.get(gid, {})
                ambiguous.append({
                    "input_id": query,
                    "reason": "family candidate requires user confirmation",
                    "search_term": term,
                    "candidate_gene_id": gid,
                    "candidate_symbol": summary.get("Name", ""),
                    "candidate_description": summary.get("Description", ""),
                    "candidate_taxid": gene_summary_taxid(summary),
                    "candidate_organism": gene_summary_organism(summary),
                })
            continue

        summaries = client.esummary_gene(ids)
        for gid in ids:
            summary = summaries.get(gid, {})
            resolved.append({
                "input_id": query,
                "gene_id": gid,
                "symbol": summary.get("Name", ""),
                "description": summary.get("Description", ""),
                "taxid": gene_summary_taxid(summary),
                "organism": gene_summary_organism(summary),
                "search_term": term,
            })
    return resolved, ambiguous, unresolved


def cds_matches_gene(feature: Any, gene_id: str) -> bool:
    qualifiers = getattr(feature, "qualifiers", {}) or {}
    db_xrefs = qualifiers.get("db_xref", []) or []
    if any(str(x) == f"GeneID:{gene_id}" for x in db_xrefs):
        return True
    return False


def transcript_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    tx = str(row.get("transcript_accession", ""))
    protein = str(row.get("protein_accession", ""))
    refseq_score = 0
    if tx.startswith(("NM_", "NR_")):
        refseq_score += 4
    elif tx.startswith("XM_"):
        refseq_score += 2
    if protein.startswith("NP_"):
        refseq_score += 4
    elif protein.startswith("XP_"):
        refseq_score += 2
    cds_len = int(row.get("cds_length") or 0)
    return (refseq_score, cds_len, -len(tx))


def select_transcripts(rows: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    if policy == "all":
        return rows
    if not rows:
        return []
    ordered = sorted(rows, key=transcript_rank, reverse=True)
    if policy == "longest":
        ordered = sorted(rows, key=lambda r: int(r.get("cds_length") or 0), reverse=True)
    return ordered[:1]


def protein_rank(row: dict[str, Any]) -> tuple[int, int]:
    acc = str(row.get("protein_accession", ""))
    score = 0
    if acc.startswith("NP_"):
        score += 4
    elif acc.startswith("XP_"):
        score += 2
    elif acc.startswith("WP_"):
        score += 1
    return (score, int(row.get("protein_length") or 0))


def fetch_gene_protein_via_elink(
    client: EntrezClient,
    gene: dict[str, Any],
    *,
    transcript_policy: str,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch protein sequences via gene->protein elink when nuccore is unavailable."""
    gene_id = str(gene["gene_id"])
    links = client.elink_ids(
        "gene",
        "protein",
        gene_id,
        preferred_terms=["gene_protein_refseq", "refseq", "gene_protein"],
    )
    protein_ids: list[str] = []
    seen: set[str] = set()
    for row in links:
        uid = row.get("id", "")
        if uid and uid not in seen:
            protein_ids.append(uid)
            seen.add(uid)
    if not protein_ids:
        return [], "no linked protein records"

    fasta_text = client.efetch_text("protein", protein_ids, rettype="fasta", retmode="text")
    parsed = list(SeqIO.parse(StringIO(fasta_text), "fasta"))
    candidates: list[dict[str, Any]] = []
    for record in parsed:
        accession = str(record.id)
        candidates.append({
            **gene,
            "transcript_accession": "",
            "protein_accession": accession,
            "product": str(record.description or ""),
            "locus_tag": str(gene.get("input_id") or ""),
            "cds_length": 0,
            "protein_length": len(record.seq),
            "cds_sequence": "",
            "protein_sequence": str(record.seq),
            "status": "resolved",
            "fetch_route": "gene_protein_elink",
        })

    if not candidates:
        return [], f"protein efetch returned no records for GeneID:{gene_id}"

    if transcript_policy == "all":
        selected = candidates
    elif transcript_policy == "longest":
        selected = sorted(candidates, key=lambda row: int(row.get("protein_length") or 0), reverse=True)[:1]
    else:
        selected = sorted(candidates, key=protein_rank, reverse=True)[:1]
    for row in selected:
        row["transcript_policy"] = transcript_policy
        row["transcript_candidates"] = len(candidates)
    return selected, ""


def fetch_gene_sequences(
    client: EntrezClient,
    gene: dict[str, Any],
    *,
    transcript_policy: str,
    max_nuccore: int,
    seq_types: str = "both",
) -> tuple[list[dict[str, Any]], str]:
    if seq_types == "protein":
        return fetch_gene_protein_via_elink(client, gene, transcript_policy=transcript_policy)

    gene_id = str(gene["gene_id"])
    try:
        links = client.elink_ids(
            "gene",
            "nuccore",
            gene_id,
            preferred_terms=["gene_nuccore_refseqrna", "refseqrna", "rna"],
        )
    except Exception as exc:
        if seq_types in {"protein", "both"}:
            rows, reason = fetch_gene_protein_via_elink(client, gene, transcript_policy=transcript_policy)
            if rows:
                return rows, ""
            return [], f"nuccore elink failed ({exc}); protein fallback failed ({reason})"
        return [], str(exc)
    nuccore_ids: list[str] = []
    seen: set[str] = set()
    for row in links:
        uid = row.get("id", "")
        if uid and uid not in seen:
            nuccore_ids.append(uid)
            seen.add(uid)
        if len(nuccore_ids) >= max_nuccore:
            break
    if not nuccore_ids:
        if seq_types in {"protein", "both"}:
            rows, reason = fetch_gene_protein_via_elink(client, gene, transcript_policy=transcript_policy)
            if rows:
                return rows, ""
            return [], f"no linked RefSeq RNA/nuccore records; protein fallback failed ({reason})"
        return [], "no linked RefSeq RNA/nuccore records"

    gb_text = client.efetch_text("nuccore", nuccore_ids, rettype="gb", retmode="text")
    records = list(SeqIO.parse(StringIO(gb_text), "genbank"))
    candidates: list[dict[str, Any]] = []
    for record in records:
        accession = str(record.id)
        for feature in record.features:
            if feature.type != "CDS":
                continue
            if not cds_matches_gene(feature, gene_id):
                continue
            qualifiers = feature.qualifiers or {}
            cds_seq = str(feature.extract(record.seq))
            protein_seq = str((qualifiers.get("translation") or [""])[0])
            protein_id = str((qualifiers.get("protein_id") or [""])[0])
            product = str((qualifiers.get("product") or [""])[0])
            locus_tag = str((qualifiers.get("locus_tag") or [""])[0])
            if not cds_seq and not protein_seq:
                continue
            row = {
                **gene,
                "transcript_accession": accession,
                "protein_accession": protein_id,
                "product": product,
                "locus_tag": locus_tag,
                "cds_length": len(cds_seq),
                "protein_length": len(protein_seq),
                "cds_sequence": cds_seq,
                "protein_sequence": protein_seq,
                "status": "resolved",
            }
            candidates.append(row)

    if not candidates:
        if seq_types in {"protein", "both"}:
            rows, reason = fetch_gene_protein_via_elink(client, gene, transcript_policy=transcript_policy)
            if rows:
                return rows, ""
            return [], f"linked nuccore records had no CDS feature with db_xref GeneID:{gene_id}; protein fallback failed ({reason})"
        return [], f"linked nuccore records had no CDS feature with db_xref GeneID:{gene_id}"
    selected = select_transcripts(candidates, transcript_policy)
    for row in selected:
        row["transcript_policy"] = transcript_policy
        row["transcript_candidates"] = len(candidates)
    return selected, ""


def artifact(workspace: Path, path: Path, typ: str) -> dict[str, Any]:
    try:
        rel = str(path.relative_to(workspace))
    except ValueError:
        rel = str(path)
    return {
        "path": rel,
        "full_path": str(path.resolve()),
        "type": typ,
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": file_sha256(path) if path.exists() else "",
    }


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest.get("metrics", {})
    params = manifest.get("parameters", {})
    lines = [
        "# NCBI Sequence Fetch Report",
        "",
        f"- Stage: `{manifest.get('stage')}`",
        f"- Status: `{manifest.get('status')}`",
        f"- Prefix: `{manifest.get('prefix')}`",
        f"- Query mode: `{params.get('mode')}`",
        f"- ID type: `{params.get('id_type')}`",
        f"- Taxid/species: `{params.get('taxid') or 'N/A'}` / `{params.get('species') or 'N/A'}`",
        f"- Resolved genes: {metrics.get('resolved_genes')}",
        f"- Protein records: {metrics.get('protein_records')}",
        f"- CDS records: {metrics.get('cds_records')}",
        f"- Ambiguous records: {metrics.get('ambiguous_records')}",
        f"- Unresolved records: {metrics.get('unresolved_records')}",
        f"- Local reference records: {metrics.get('local_reference_records', 0)}",
        f"- NCBI web records: {metrics.get('ncbi_web_records', 0)}",
        "",
        "## Boundary",
        "",
        "Reference-genome priority: sequences are extracted from job input / genome_prepare / bundled "
        "reference FASTA first; NCBI E-utilities is used only when no local match exists. "
        "Family-mode candidates are not downloaded unless `--allow-family-download` is set. "
        "Ambiguous and unresolved IDs are written to TSV files for user review.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch protein/CDS sequences from NCBI Gene records.")
    parser.add_argument("--workspace", required=True, help="Job workspace directory")
    parser.add_argument("-p", "--prefix", default="NCBI.fetch", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("--gene-ids", help="Text file with one query/GeneID per line")
    parser.add_argument("--id", dest="ids", action="append", default=[], help="Single query/GeneID; can be repeated")
    parser.add_argument("--query", default="", help="Gene symbol/locus/family query")
    parser.add_argument("--mode", choices=["gene", "family"], default="gene")
    parser.add_argument("--id-type", choices=["auto", "geneid", "symbol", "locus_tag"], default="auto")
    parser.add_argument("--taxid", default="", help="NCBI taxonomy ID; recommended for symbol/family queries")
    parser.add_argument("--species", default="", help="Species scientific name; used if taxid is absent")
    parser.add_argument("--seq-types", choices=["protein", "cds", "both"], default="both")
    parser.add_argument("--transcript-policy", choices=["refseq_preferred", "canonical", "longest", "all"], default="refseq_preferred")
    parser.add_argument("--allow-family-download", action="store_true", help="Download all family search candidates without further confirmation")
    parser.add_argument("--retmax", type=int, default=50, help="Max NCBI Gene candidates for search queries")
    parser.add_argument("--max-nuccore", type=int, default=30, help="Max linked nuccore records to inspect per GeneID")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="NCBI Entrez email")
    parser.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY", ""), help="NCBI API key")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = datetime.now()
    workspace = Path(args.workspace).resolve()
    analysis_dir = workspace / "ncbi_fetch_sequences" / "analysis"
    final_dir = workspace / "final"
    cache_dir = workspace / "cache" / "ncbi"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    policy = "refseq_preferred" if args.transcript_policy == "canonical" else args.transcript_policy
    queries = list(args.ids or [])
    if args.gene_ids:
        queries.extend(read_query_file(Path(args.gene_ids)))
    if args.query:
        queries.append(args.query)
    queries = [q for q in queries if str(q).strip()]

    prefix = safe_name(args.prefix)
    protein_fa = final_dir / f"{prefix}.ncbi_fetch.protein.fa"
    cds_fa = final_dir / f"{prefix}.ncbi_fetch.cds.fa"
    mapping_tsv = final_dir / f"{prefix}.ncbi_fetch.mapping.tsv"
    unresolved_tsv = final_dir / f"{prefix}.ncbi_fetch.unresolved.tsv"
    ambiguous_tsv = final_dir / f"{prefix}.ncbi_fetch.ambiguous.tsv"
    report_path = final_dir / f"{prefix}.ncbi_fetch.report.md"
    manifest_path = final_dir / f"{prefix}.ncbi_fetch.manifest.json"
    commands_path = analysis_dir / "commands.sh"

    commands_path.write_text(" ".join(shlex.quote(part) for part in sys.argv) + "\n", encoding="utf-8")
    os.chmod(commands_path, 0o755)

    resolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    status = "success"
    error = ""
    local_reference = LocalReferenceStore({}, {}, [], [])

    try:
        if not queries:
            raise ValueError("No query provided. Use --gene-ids, --id, or --query.")

        local_reference = build_local_reference_store(workspace)
        if local_reference.pep_sources or local_reference.cds_sources:
            print(
                "Local reference priority enabled: "
                f"{len(local_reference.pep_sources)} protein source(s), "
                f"{len(local_reference.cds_sources)} CDS source(s)"
            )

        remaining_queries: list[str] = []
        for query in queries:
            local_gene = {
                "input_id": query,
                "gene_id": query if re.fullmatch(r"\d+", str(query).strip()) else "",
                "symbol": "",
                "locus_tag": normalize_locus_tag(query) or "",
                "description": "",
                "taxid": args.taxid,
                "organism": args.species,
            }
            rows, _reason = fetch_gene_from_local_reference(
                local_reference,
                local_gene,
                seq_types=args.seq_types,
                transcript_policy=policy,
            )
            if rows:
                sequence_rows.extend(rows)
                resolved.append(local_gene)
            else:
                remaining_queries.append(query)

        if remaining_queries:
            client = EntrezClient(email=args.email, api_key=args.api_key, cache_dir=cache_dir)
            web_resolved, ambiguous, unresolved = resolve_gene_queries(
                client,
                remaining_queries,
                id_type=args.id_type,
                mode=args.mode,
                taxid=args.taxid,
                species=args.species,
                retmax=args.retmax,
                allow_family_download=args.allow_family_download,
            )
            resolved.extend(web_resolved)
        else:
            web_resolved = []

        seen_gene_ids: set[str] = set()
        unique_resolved: list[dict[str, Any]] = []
        for row in resolved:
            marker = str(row.get("gene_id") or row.get("input_id") or "")
            if marker and marker not in seen_gene_ids:
                unique_resolved.append(row)
                seen_gene_ids.add(marker)
        resolved = unique_resolved

        for gene in web_resolved:
            rows: list[dict[str, Any]] = []
            reason = ""
            rows, reason = fetch_gene_from_local_reference(
                local_reference,
                gene,
                seq_types=args.seq_types,
                transcript_policy=policy,
            )
            if not rows:
                try:
                    rows, reason = fetch_gene_sequences(
                        client,
                        gene,
                        transcript_policy=policy,
                        max_nuccore=args.max_nuccore,
                        seq_types=args.seq_types,
                    )
                except Exception as exc:
                    rows = []
                    reason = str(exc)
            if rows:
                sequence_rows.extend(rows)
            else:
                unresolved.append({
                    "input_id": gene.get("input_id", ""),
                    "gene_id": gene.get("gene_id", ""),
                    "symbol": gene.get("symbol", ""),
                    "reason": reason,
                })

        if not sequence_rows and (ambiguous or unresolved):
            status = "needs_user_confirmation" if ambiguous else "failed"
        elif ambiguous or unresolved:
            status = "partial_success"

    except Exception as exc:
        status = "failed"
        error = str(exc)
        unresolved.append({"input_id": ",".join(queries), "reason": str(exc)})

    mapping_fields = [
        "input_id", "gene_id", "symbol", "description", "taxid", "organism",
        "transcript_accession", "protein_accession", "product", "locus_tag",
        "transcript_policy", "transcript_candidates", "cds_length", "protein_length", "status",
    ]
    unresolved_fields = ["input_id", "gene_id", "symbol", "reason", "search_term"]
    ambiguous_fields = [
        "input_id", "reason", "search_term", "candidate_gene_id", "candidate_gene_ids",
        "candidate_symbol", "candidate_symbols", "candidate_description", "candidate_taxid", "candidate_organism",
    ]

    write_tsv(mapping_tsv, sequence_rows, mapping_fields)
    write_tsv(unresolved_tsv, unresolved, unresolved_fields)
    write_tsv(ambiguous_tsv, ambiguous, ambiguous_fields)
    if args.seq_types in {"protein", "both"}:
        write_fasta(protein_fa, sequence_rows, "protein_sequence")
    else:
        protein_fa.write_text("", encoding="utf-8")
    if args.seq_types in {"cds", "both"}:
        write_fasta(cds_fa, sequence_rows, "cds_sequence")
    else:
        cds_fa.write_text("", encoding="utf-8")

    end = datetime.now()
    artifacts: dict[str, dict[str, Any]] = {
        "mapping": artifact(workspace, mapping_tsv, "tsv"),
        "unresolved": artifact(workspace, unresolved_tsv, "tsv"),
        "ambiguous": artifact(workspace, ambiguous_tsv, "tsv"),
        "commands": artifact(workspace, commands_path, "shell"),
    }
    if protein_fa.exists():
        artifacts["protein_fasta"] = artifact(workspace, protein_fa, "fasta")
        family_ref_link = create_family_reference_link(prefix, protein_fa)
        if family_ref_link:
            artifacts["family_reference_pep"] = artifact(workspace, family_ref_link, "fasta")
    if cds_fa.exists():
        artifacts["cds_fasta"] = artifact(workspace, cds_fa, "fasta")

    manifest: dict[str, Any] = {
        "manifest_version": "1.0",
        "stage": "ncbi_fetch_sequences",
        "status": status,
        "prefix": prefix,
        "job_id": args.job_id,
        "created_at": start.isoformat(),
        "completed_at": end.isoformat(),
        "elapsed_sec": round((end - start).total_seconds(), 3),
        "parameters": {
            "mode": args.mode,
            "id_type": args.id_type,
            "taxid": args.taxid,
            "species": args.species,
            "seq_types": args.seq_types,
            "transcript_policy": policy,
            "allow_family_download": args.allow_family_download,
            "retmax": args.retmax,
            "max_nuccore": args.max_nuccore,
            "email": args.email,
            "api_key_used": bool(args.api_key),
            "queries": queries,
            "fetch_policy": "local_reference_first",
            "local_reference_protein_sources": local_reference.pep_sources,
            "local_reference_cds_sources": local_reference.cds_sources,
        },
        "metrics": {
            "queries": len(queries),
            "resolved_genes": len(resolved),
            "sequence_rows": len(sequence_rows),
            "protein_records": sum(1 for row in sequence_rows if row.get("protein_sequence")),
            "cds_records": sum(1 for row in sequence_rows if row.get("cds_sequence")),
            "ambiguous_records": len(ambiguous),
            "unresolved_records": len(unresolved),
            "local_fallback_records": sum(
                1 for row in sequence_rows if str(row.get("fetch_route", "")).startswith("local_reference:")
            ),
            "local_reference_records": sum(
                1 for row in sequence_rows if str(row.get("fetch_route", "")).startswith("local_reference:")
            ),
            "ncbi_web_records": sum(
                1 for row in sequence_rows if str(row.get("fetch_route", "")).startswith(("gene_protein_elink", "nuccore"))
                or "ncbi" in str(row.get("fetch_route", "")).lower()
            ),
        },
        "artifacts": artifacts,
        "error": error,
        "tool_versions": {
            "biopython": getattr(sys.modules.get("Bio"), "__version__", "unknown"),
            "entrez": "NCBI E-utilities",
        },
        "policy": {
            "ambiguous_queries_are_not_downloaded": True,
            "family_download_requires_confirmation_flag": True,
            "no_silent_gene_symbol_guessing": True,
        },
    }
    write_report(report_path, manifest)
    artifacts["report"] = artifact(workspace, report_path, "markdown")
    manifest["artifacts"] = artifacts
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["artifacts"]["manifest"] = artifact(workspace, manifest_path, "json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"NCBI fetch status: {status}")
    print(f"Resolved genes: {len(resolved)}")
    print(f"Sequence rows: {len(sequence_rows)}")
    print(f"Protein FASTA: {protein_fa}")
    print(f"CDS FASTA: {cds_fa}")
    print(f"Manifest: {manifest_path}")
    return 0 if status in {"success", "partial_success", "needs_user_confirmation"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
