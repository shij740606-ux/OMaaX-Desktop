"""Config-aware input binding for extension stage job runs."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.spec_v2 import infer_analysis_type
from gfa_core.extension_stage.file_resolution import (
    family_from_species_filename,
    looks_like_fasta_file,
    looks_like_id_file,
    resolve_input_path_variant,
)


class ExtensionInputBindingError(RuntimeError):
    """Raised for user-facing config/input binding failures."""


def materialize_config_inputs(extension: dict[str, Any], job_id: str, parameters: dict[str, Any], input_root: Path, input_dir: Path) -> dict[str, Any]:
    job_token = _safe_path_token(job_id)
    job_input_dir = Path(input_root) / job_token
    if not job_input_dir.exists():
        return {}
    config_path = job_input_dir / "config.xlsx"
    explicit = _resolve_explicit_runtime_bindings(extension, parameters, input_dir)
    if explicit.get("complete"):
        explicit.pop("complete", None)
        return explicit
    if not config_path.exists():
        explicit.pop("complete", None)
        return explicit

    if _looks_like_sequence_extraction_extension(extension):
        selection = _resolve_sequence_extraction_config_inputs(config_path, job_input_dir, parameters)
        pep_path = selection.get("pep_path")
        id_path = selection.get("id_path")
        if not pep_path or not id_path:
            return {}
        pep_dest = input_dir / Path(pep_path).name
        id_dest = input_dir / Path(id_path).name
        shutil.copy2(pep_path, pep_dest)
        shutil.copy2(id_path, id_dest)
        return {
            "source": "config.xlsx",
            "binding_type": "sequence_extraction",
            "target_species": selection.get("target_species", ""),
            "pep_path": str(pep_path),
            "id_path": str(id_path),
            "placeholders": {
                "input_fasta": pep_dest.as_posix(),
                "input_pep_fasta": pep_dest.as_posix(),
                "input_file": id_dest.as_posix(),
                "input_id_file": id_dest.as_posix(),
            },
            "placeholder_to_file": {
                "input_fasta": pep_dest.as_posix(),
                "input_pep_fasta": pep_dest.as_posix(),
                "input_file": id_dest.as_posix(),
                "input_id_file": id_dest.as_posix(),
            },
            "role_to_file": {"target_pep_fasta": pep_dest.as_posix(), "target_id_list": id_dest.as_posix()},
        }

    if not _looks_like_blastp_extension(extension):
        return {}
    selection = _resolve_blastp_config_inputs(config_path, job_input_dir, parameters)
    if not selection:
        return {}

    query_path = selection.get("query_path")
    db_path = selection.get("db_path")
    query_dest = None
    db_dest = None
    if query_path and Path(query_path).exists():
        query_dest = input_dir / Path(query_path).name
        shutil.copy2(query_path, query_dest)
        for alias in _blastp_query_aliases(extension):
            shutil.copy2(query_path, input_dir / alias)
    if db_path and Path(db_path).exists():
        db_dest = input_dir / Path(db_path).name
        shutil.copy2(db_path, db_dest)
        for alias in _blastp_db_aliases(extension):
            shutil.copy2(db_path, input_dir / alias)
    placeholders = {}
    if query_dest:
        placeholders.update({
            "input_query_fasta": query_dest.as_posix(),
            "input_fasta": query_dest.as_posix(),
            "input_pep_fasta": query_dest.as_posix(),
        })
    if db_dest:
        placeholders["input_reference_fasta"] = db_dest.as_posix()
    return {
        "source": "config.xlsx",
        "binding_type": "blastp",
        "target_species": selection.get("target_species", ""),
        "reference_species": selection.get("reference_species", ""),
        "query_path": str(query_path or ""),
        "db_path": str(db_path or ""),
        "query_aliases": _blastp_query_aliases(extension),
        "db_aliases": _blastp_db_aliases(extension),
        "placeholders": placeholders,
        "placeholder_to_file": placeholders,
        "role_to_file": {"target_pep_fasta": str(query_dest or ""), "reference_pep_fasta": str(db_dest or "")},
    }


def _resolve_explicit_runtime_bindings(extension: dict[str, Any], parameters: dict[str, Any], input_dir: Path) -> dict[str, Any]:
    explicit_files = _first_param_list(parameters, "explicit_input_files", "explicitInputFiles", "input_paths")
    file_roles = parameters.get("file_roles") or parameters.get("fileRoles") or {}
    if not explicit_files and not isinstance(file_roles, dict):
        return {"complete": False}
    resolved: dict[str, Path] = {}
    for raw in explicit_files:
        candidate = resolve_input_path_variant(input_dir, Path(raw).name)
        if candidate and candidate.exists() and candidate.is_file():
            resolved[Path(raw).name] = candidate
    for raw in (file_roles if isinstance(file_roles, dict) else {}):
        candidate = resolve_input_path_variant(input_dir, Path(str(raw)).name)
        if candidate and candidate.exists() and candidate.is_file():
            resolved[Path(str(raw)).name] = candidate
    analysis_type = str(extension.get("analysis_type") or infer_analysis_type(extension))
    placeholders: dict[str, str] = {}
    roles: dict[str, str] = {}
    def by_role(*wanted: str) -> Path | None:
        if isinstance(file_roles, dict):
            for name, role in file_roles.items():
                if str(role) in wanted:
                    p = resolved.get(Path(str(name)).name)
                    if p:
                        return p
        return None
    if analysis_type == "blast_search" or _looks_like_blastp_extension(extension):
        query = by_role("query") or _first_resolved_fasta(resolved)
        ref = by_role("reference", "database")
        if query and ref:
            placeholders = {
                "input_query_fasta": query.as_posix(),
                "input_fasta": query.as_posix(),
                "input_pep_fasta": query.as_posix(),
                "input_reference_fasta": ref.as_posix(),
            }
            roles = {"target_pep_fasta": query.as_posix(), "reference_pep_fasta": ref.as_posix()}
    elif analysis_type == "hmmer_search":
        hmm = by_role("hmm_profile") or _first_resolved_suffix(resolved, (".hmm",))
        fasta = by_role("fasta", "query") or _first_resolved_fasta(resolved)
        if hmm and fasta:
            placeholders = {"input_hmm": hmm.as_posix(), "input_fasta": fasta.as_posix(), "input_pep_fasta": fasta.as_posix()}
            roles = {"hmm_profile": hmm.as_posix(), "target_pep_fasta": fasta.as_posix()}
    elif analysis_type == "sequence_extraction" or _looks_like_sequence_extraction_extension(extension):
        id_file = by_role("id_list") or _first_resolved_id(resolved)
        fasta = by_role("fasta") or _first_resolved_fasta(resolved)
        if id_file and fasta:
            cds = "cds" in fasta.name.lower() or "input_cds_fasta" in str((extension.get("entrypoint") or {}).get("command_template") or "")
            placeholders = {
                "input_file": id_file.as_posix(),
                "input_id_file": id_file.as_posix(),
                "input_fasta": fasta.as_posix(),
                "input_cds_fasta" if cds else "input_pep_fasta": fasta.as_posix(),
            }
            roles = {"target_id_list": id_file.as_posix(), "target_cds_fasta" if cds else "target_pep_fasta": fasta.as_posix()}
    return {
        "complete": bool(placeholders),
        "source": "natural_language_explicit_files",
        "binding_type": analysis_type,
        "target_species": (_first_param_list(parameters, "targetSpecies", "target_species", "species") or [""])[0],
        "reference_species": (_first_param_list(parameters, "referenceSpecies", "reference_species", "refSpeciesList") or [""])[0],
        "placeholders": placeholders,
        "placeholder_to_file": placeholders,
        "role_to_file": roles,
        "explicit_files": [str(x) for x in explicit_files],
        "file_roles": file_roles if isinstance(file_roles, dict) else {},
        "ambiguities": [] if placeholders else ["explicit files did not provide a complete semantic binding"],
    }


def _first_resolved_suffix(resolved: dict[str, Path], suffixes: tuple[str, ...]) -> Path | None:
    for path in resolved.values():
        if path.name.lower().endswith(suffixes):
            return path
    return None


def _first_resolved_fasta(resolved: dict[str, Path]) -> Path | None:
    for path in resolved.values():
        if looks_like_fasta_file(path):
            return path
    return None


def _first_resolved_id(resolved: dict[str, Path]) -> Path | None:
    for path in resolved.values():
        if looks_like_id_file(path):
            return path
    return None


def _looks_like_blastp_extension(extension: dict[str, Any]) -> bool:
    template = str((extension.get("entrypoint") or {}).get("command_template") or "").lower()
    requirements = " ".join(str(x).lower() for x in ((extension.get("requirements") or {}).get("system") or []))
    return "blastp" in template or "blastp" in requirements


def _looks_like_sequence_extraction_extension(extension: dict[str, Any]) -> bool:
    template = str((extension.get("entrypoint") or {}).get("command_template") or "").lower()
    requirements = " ".join(str(x).lower() for x in ((extension.get("requirements") or {}).get("system") or []))
    name_text = " ".join(str(extension.get(key, "")).lower() for key in ("name", "display_name", "description"))
    return ("seqtk" in template and "subseq" in template) or "sequence_extraction" in name_text or "protein_sequence_extraction" in name_text or ("seqtk" in requirements and "subseq" in template)


def _blastp_query_aliases(extension: dict[str, Any]) -> list[str]:
    template = str((extension.get("entrypoint") or {}).get("command_template") or "")
    aliases = []
    for match in re.finditer(r"blastp\s+.*?-query\s+\{input_dir\}/([^\s;&|]+)", template):
        name = Path(match.group(1).strip("'\"")).name
        if name and name not in aliases:
            aliases.append(name)
    if not aliases and "Ft.pep.fasta" in template:
        aliases.append("Ft.pep.fasta")
    return aliases


def _blastp_db_aliases(extension: dict[str, Any]) -> list[str]:
    template = str((extension.get("entrypoint") or {}).get("command_template") or "")
    aliases = []
    for match in re.finditer(r"makeblastdb\s+.*?-in\s+\{input_dir\}/([^\s;&|]+)", template):
        name = Path(match.group(1).strip("'\"")).name
        if name and name not in aliases:
            aliases.append(name)
    if not aliases and "SPL_Ath.fasta" in template:
        aliases.append("SPL_Ath.fasta")
    return aliases


def _resolve_sequence_extraction_config_inputs(config_path: Path, input_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        import pandas as pd
        df = pd.read_excel(config_path).fillna("")
    except Exception as exc:
        raise ExtensionInputBindingError(f"Failed to read config.xlsx for extension sequence extraction inputs: {exc}") from exc

    rows = []
    for _, row in df.iterrows():
        item = {str(k): row.get(k, "") for k in df.columns}
        species = _clean_config_cell(item.get("Species_Code"))
        filename = Path(_clean_config_cell(item.get("Original_File_Name"))).name
        file_type = _clean_config_cell(item.get("File_Type")).lower()
        role = _clean_config_cell(item.get("Species_Role")).lower()
        scope = _clean_config_cell(item.get("File_Scope")).lower()
        if not species or not filename:
            continue
        path = _resolve_config_declared_file(input_dir, species, filename)
        rows.append({"species": species, "filename": filename, "file_type": file_type, "role": role, "scope": scope, "path": path})

    target_species = _first_param_list(parameters, "targetSpecies", "target_species", "species")
    if not target_species:
        target_rows = [r for r in rows if r["role"] == "target"]
        unique_targets = _unique([r["species"] for r in target_rows])
        if len(unique_targets) == 1:
            target_species = unique_targets
        elif len(unique_targets) > 1:
            raise ExtensionInputBindingError("Multiple target species found in config.xlsx; specify one species in natural language.")
    if not target_species:
        raise ExtensionInputBindingError("No target species was specified and config.xlsx has no unique target row.")

    target = target_species[0]
    pep_rows = [
        r for r in rows
        if r["species"].lower() == target.lower()
        and r["path"] and r["path"].exists()
        and (r["file_type"] in {"pep", "protein", "all_pep", "fasta", "faa"} or r["filename"].lower().endswith((".pep", ".pep.fa", ".pep.fasta", ".faa", ".fa", ".fasta")))
    ]
    pep_rows = sorted(pep_rows, key=lambda r: (r["role"] != "target", r["scope"] == "known_family", r["filename"]))
    if not pep_rows:
        raise ExtensionInputBindingError(f"config.xlsx does not contain an existing pep/fasta file for target species {target}.")

    explicit_inputs = _first_param_list(parameters, "explicit_input_files", "explicitInputFiles", "input_paths")
    explicit_ids = _first_param_list(parameters, "idFile", "id_file", "ids", "geneIdFile", "gene_id_file") + explicit_inputs
    explicit_fastas = _first_param_list(parameters, "inputFasta", "input_fasta", "fastaFile", "fasta_file", "pepFile", "pep_file") + explicit_inputs
    id_path = None
    for raw in explicit_ids:
        candidate = resolve_input_path_variant(input_dir, raw)
        if candidate and candidate.exists() and candidate.is_file() and looks_like_id_file(candidate):
            id_path = candidate
            break
    if id_path is None:
        id_path = _resolve_target_id_file(input_dir, target)
    if id_path is None:
        raise ExtensionInputBindingError(f"Could not find an ID list for target species {target}; expected files like {target}.final_gfmembers.id or {target}*.id.")

    explicit_fasta_path = _resolve_explicit_fasta(input_dir, explicit_fastas)
    requested_family = family_from_species_filename(id_path.name, target)
    pep_path = explicit_fasta_path or _select_sequence_fasta(input_dir, target, requested_family, pep_rows)
    if pep_path is None:
        family_text = f" {requested_family}" if requested_family else ""
        raise ExtensionInputBindingError(f"Could not find a compatible {target}{family_text} protein fasta for {id_path.name}.")

    return {"target_species": target, "pep_path": pep_path, "id_path": id_path}



def _resolve_explicit_fasta(input_dir: Path, raw_paths: list[str]) -> Path | None:
    for raw in raw_paths:
        candidate = resolve_input_path_variant(input_dir, raw)
        if candidate and candidate.exists() and candidate.is_file() and looks_like_fasta_file(candidate):
            return candidate
    return None


def _select_sequence_fasta(input_dir: Path, species: str, family: str, pep_rows: list[dict[str, Any]]) -> Path | None:
    if family:
        family_candidates = _candidate_existing_files(input_dir, [
            f"{species}.{family}.pep.fasta",
            f"{species}.{family}.pep.fa",
            f"{species}.{family}.faa",
            f"{species}.{family}.fasta",
            f"{species}.{family}.fa",
        ])
        if family_candidates:
            return family_candidates[0]

    whole_candidates = _candidate_existing_files(input_dir, [
        f"{species}.pep.fasta",
        f"{species}.pep.fa",
        f"{species}.faa",
        f"{species}.fasta",
        f"{species}.fa",
    ])
    if whole_candidates:
        return whole_candidates[0]

    if family:
        compatible_rows = [r for r in pep_rows if family_from_species_filename(r["filename"], species).lower() == family.lower()]
        if compatible_rows:
            return compatible_rows[0]["path"]
        whole_rows = [r for r in pep_rows if not family_from_species_filename(r["filename"], species) and r.get("scope") != "known_family"]
        if whole_rows:
            return whole_rows[0]["path"]
        return None

    return pep_rows[0]["path"] if pep_rows else None


def _candidate_existing_files(input_dir: Path, names: list[str]) -> list[Path]:
    paths = []
    for name in names:
        candidate = input_dir / name
        if candidate.exists() and candidate.is_file():
            paths.append(candidate)
    return paths


def _resolve_blastp_config_inputs(config_path: Path, input_dir: Path, parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        import pandas as pd
        df = pd.read_excel(config_path).fillna("")
    except Exception as exc:
        raise ExtensionInputBindingError(f"Failed to read config.xlsx for extension BLASTP inputs: {exc}") from exc

    rows = []
    for _, row in df.iterrows():
        item = {str(k): row.get(k, "") for k in df.columns}
        species = _clean_config_cell(item.get("Species_Code"))
        filename = Path(_clean_config_cell(item.get("Original_File_Name"))).name
        file_type = _clean_config_cell(item.get("File_Type")).lower()
        if not species or not filename or file_type not in {"pep", "protein", "all_pep", "family_pep"}:
            continue
        role = _clean_config_cell(item.get("Species_Role")).lower()
        scope = _clean_config_cell(item.get("File_Scope")).lower()
        family = _clean_config_cell(item.get("Gene_Family"))
        is_ref = _config_truthy(item.get("Ref")) or role in {"ref", "reference", "seed", "seed_reference", "known_family_reference"}
        path = _resolve_config_declared_file(input_dir, species, filename)
        rows.append({
            "species": species,
            "filename": filename,
            "role": role,
            "scope": scope,
            "family": family,
            "is_ref": is_ref,
            "path": path,
        })

    target_species = _first_param_list(parameters, "targetSpecies", "target_species", "species")
    if not target_species:
        target_rows = [r for r in rows if r["role"] == "target"]
        unique_targets = _unique([r["species"] for r in target_rows])
        if len(unique_targets) == 1:
            target_species = unique_targets
        elif len(unique_targets) > 1:
            raise ExtensionInputBindingError("Multiple target species found in config.xlsx; specify one species in natural language.")

    if not target_species:
        raise ExtensionInputBindingError("No target species was specified and config.xlsx has no unique target pep row.")
    target = target_species[0]
    query_candidates = [r for r in rows if r["species"].lower() == target.lower() and r["path"] and r["path"].exists()]
    query_candidates = sorted(query_candidates, key=lambda r: (r["role"] != "target", r["scope"] == "known_family", r["filename"]))
    if not query_candidates:
        raise ExtensionInputBindingError(f"config.xlsx does not contain an existing pep file for target species {target}.")

    ref_species = _first_param_list(parameters, "referenceSpecies", "reference_species", "refSpeciesList")
    ref_rows = [r for r in rows if r["is_ref"] and r["path"] and r["path"].exists()]
    if ref_species:
        ref_rows = [r for r in ref_rows if r["species"].lower() in {x.lower() for x in ref_species}]
    ref_rows = sorted(ref_rows, key=lambda r: (r["scope"] != "known_family", r["role"] not in {"seed_reference", "known_family_reference", "seed"}, r["filename"]))
    if not ref_rows:
        raise ExtensionInputBindingError("config.xlsx does not contain an existing reference/known_family pep file for BLAST database.")

    query = query_candidates[0]
    ref = ref_rows[0]
    return {
        "target_species": query["species"],
        "reference_species": ref["species"],
        "query_path": query["path"],
        "db_path": ref["path"],
    }


def _resolve_target_id_file(input_dir: Path, species: str) -> Path | None:
    names = [
        f"{species}.final_gfmembers.id",
        f"{species}.gfmembers.id",
        f"{species}.geneID",
        f"{species}.geneIDs",
        f"{species}.ids",
        f"{species}.id",
    ]
    for name in names:
        candidate = input_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    patterns = [f"{species}*.id", f"{species}*.ids", f"{species}*.geneID", f"{species}*.geneIDs", f"{species}*members*.txt", f"{species}*ids*.txt"]
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(p for p in input_dir.glob(pattern) if p.is_file())
    if matches:
        return sorted(matches, key=lambda p: ("final_gfmembers" not in p.name.lower(), p.name))[0]
    return None


def _resolve_config_declared_file(input_dir: Path, species: str, filename: str) -> Path | None:
    candidates = [
        input_dir / filename,
        input_dir / species / filename,
        input_dir / f"{species}.pep.fasta",
        input_dir / f"{species}.pep.fa",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _clean_config_cell(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in {"nan", "none", "null", "-"}:
        return ""
    return text


def _config_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "ture", "yes", "y", "ref"}


def _first_param_list(parameters: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        if key not in parameters:
            continue
        value = parameters.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = re.split(r"[,;，、\s]+", value)
        elif isinstance(value, (list, tuple, set)):
            items = value
        else:
            items = [value]
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        if cleaned:
            return cleaned
    return []


def _unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _safe_path_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))
    return token.strip("._") or "unknown"
