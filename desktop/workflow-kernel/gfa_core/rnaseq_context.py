"""RNA-seq context helpers for manifest/status compatibility.

RNA-seq stages operate at experiment/sample scope first.  A gene-family member
set can be layered on top for DE/enrichment/expression foreground analyses, but
it is not the same identity as the core RNA-seq experiment.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def _clean_token(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._-") or "unknown"


def species_from_prefix(prefix: str) -> str:
    prefix = str(prefix or "").strip()
    if not prefix:
        return "unknown"
    return re.split(r"[._]", prefix, maxsplit=1)[0] or prefix


GENERIC_RNASEQ_PREFIXES = {"", "at", "ath", "target", "multi", "rnaseq", "genome", "genome_prepare"}


def _clean_project_value(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"", "none", "null", "undefined", "nan", "-"} else text


def _is_gene_family_like_prefix(value: str) -> bool:
    value = _clean_project_value(value)
    return "." in value or value.lower() in {"target", "multi"}


def _looks_like_reference_version(value: str) -> bool:
    text = _clean_project_value(value)
    if not text:
        return False
    lowered = text.lower()
    if lowered in GENERIC_RNASEQ_PREFIXES:
        return True
    if "_" in text and re.search(r"(?:^|[_-])r\d+(?:$|[_-])", lowered):
        return True
    if re.search(r"\d", text) and re.search(r"(asm|assembly|genome|repr|models)", lowered):
        return True
    return False


def _species_meta_from_mapping(meta: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(meta, Mapping):
        species_block = meta.get("species") if isinstance(meta.get("species"), Mapping) else {}
        rnaseq_block = meta.get("rnaseq") if isinstance(meta.get("rnaseq"), Mapping) else {}
        reference_block = meta.get("reference") if isinstance(meta.get("reference"), Mapping) else {}
        out["species_code"] = _clean_project_value(
            meta.get("speciesCode")
            or meta.get("species_code")
            or meta.get("organismCode")
            or meta.get("organism_code")
            or species_block.get("code")
            or species_block.get("speciesCode")
            or species_block.get("species_code")
            or species_block.get("organismCode")
            or species_block.get("organism_code")
        )
        out["species_name"] = _clean_project_value(
            meta.get("speciesName")
            or meta.get("species_name")
            or meta.get("latin_name")
            or species_block.get("speciesName")
            or species_block.get("species_name")
            or species_block.get("latin_name")
            or species_block.get("name")
        )
        out["kegg_organism"] = _clean_project_value(
            meta.get("keggOrganism")
            or meta.get("kegg_organism")
            or meta.get("kegg")
            or species_block.get("keggOrganism")
            or species_block.get("kegg_organism")
            or species_block.get("kegg")
        ).lower()
        out["output_prefix"] = _clean_project_value(
            meta.get("rnaseqPrefix")
            or meta.get("rnaseq_prefix")
            or meta.get("outputPrefix")
            or meta.get("output_prefix")
            or rnaseq_block.get("outputPrefix")
            or rnaseq_block.get("output_prefix")
            or rnaseq_block.get("prefix")
        )
        out["reference_id"] = _clean_project_value(
            meta.get("referenceId")
            or meta.get("reference_id")
            or reference_block.get("id")
            or reference_block.get("referenceId")
            or reference_block.get("reference_id")
        )
    elif isinstance(meta, str):
        out["species_code"] = _clean_project_value(meta)
    return {k: v for k, v in out.items() if v}



def _as_clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;，；/]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    out: list[str] = []
    for item in parts:
        text = _clean_project_value(item)
        if text and text not in out:
            out.append(text)
    return out


def _looks_numeric(value: Any) -> bool:
    text = _clean_project_value(value)
    if not text:
        return False
    try:
        float(text)
        return True
    except Exception:
        return False


def _experiment_meta_from_mapping(meta: Any) -> dict[str, Any]:
    """Extract optional job-level biological context without touching sample groups."""
    if not isinstance(meta, Mapping):
        return {}
    experiment = meta.get("experiment") if isinstance(meta.get("experiment"), Mapping) else {}
    rnaseq = meta.get("rnaseq") if isinstance(meta.get("rnaseq"), Mapping) else {}
    out: dict[str, Any] = {}
    out["treatment"] = _clean_project_value(
        meta.get("treatment")
        or meta.get("stress")
        or experiment.get("treatment")
        or experiment.get("stress")
        or rnaseq.get("treatment")
    )
    out["trait"] = _clean_project_value(
        meta.get("trait")
        or experiment.get("trait")
        or rnaseq.get("trait")
    )
    out["design"] = _clean_project_value(
        meta.get("design")
        or experiment.get("design")
        or rnaseq.get("design")
    )
    out["primary_axis"] = _clean_project_value(
        meta.get("primary_axis")
        or experiment.get("primary_axis")
        or experiment.get("ordered_axis")
        or rnaseq.get("primary_axis")
    )
    out["control_condition"] = _clean_project_value(
        meta.get("control_condition")
        or meta.get("control_group")
        or experiment.get("control_condition")
        or experiment.get("control_group")
        or rnaseq.get("control_condition")
    )
    keywords = []
    for key in ("treatment_keywords", "keywords", "context_keywords"):
        keywords.extend(_as_clean_list(meta.get(key)))
        keywords.extend(_as_clean_list(experiment.get(key)))
        keywords.extend(_as_clean_list(rnaseq.get(key)))
    if out["treatment"]:
        keywords.insert(0, out["treatment"])
    out["treatment_keywords"] = list(dict.fromkeys([x for x in keywords if x]))
    for key in ("stage_order", "time_order"):
        values = _as_clean_list(meta.get(key)) or _as_clean_list(experiment.get(key)) or _as_clean_list(rnaseq.get(key))
        if values:
            out[key] = values
    return {k: v for k, v in out.items() if v not in ("", [], None)}


def _sample_axis_values(config: Mapping[str, Any]) -> dict[str, list[str]]:
    axes = {
        "condition": [],
        "time": [],
        "tissue": [],
        "organ_part": [],
        "stage": [],
        "treatment": [],
        "genotype": [],
        "trait": [],
        "cell_type": [],
    }
    sample_entries = config.get("samples")
    if isinstance(sample_entries, Mapping):
        for _sample, meta in sample_entries.items():
            if not isinstance(meta, Mapping):
                continue
            aliases = {
                "condition": ("condition", "group"),
                "time": ("time", "timepoint", "hour"),
                "tissue": ("tissue", "organ"),
                "organ_part": ("organ_part", "part"),
                "stage": ("stage", "development_stage", "maturity"),
                "treatment": ("treatment", "stress"),
                "genotype": ("genotype", "cultivar", "variety", "accession"),
                "trait": ("trait", "phenotype"),
                "cell_type": ("cell_type", "cluster"),
            }
            for axis, keys in aliases.items():
                for key in keys:
                    if key in meta and meta.get(key) not in (None, ""):
                        text = _clean_project_value(meta.get(key))
                        if text and text not in axes[axis]:
                            axes[axis].append(text)
                        break
    return {axis: values for axis, values in axes.items() if values}


def infer_experiment_design(experiment_meta: Mapping[str, Any], axis_values: Mapping[str, list[str]]) -> str:
    explicit = _clean_project_value(experiment_meta.get("design")).lower()
    if explicit and explicit != "auto":
        return explicit
    axes = {axis for axis, values in axis_values.items() if values}
    has_time = "time" in axes and any(_looks_numeric(v) for v in axis_values.get("time", []))
    semantic_axes = axes & {"treatment", "tissue", "organ_part", "stage", "genotype", "trait", "cell_type"}
    if experiment_meta.get("treatment"):
        semantic_axes.add("treatment")
    if experiment_meta.get("trait"):
        semantic_axes.add("trait")
    if has_time and ("treatment" in semantic_axes or experiment_meta.get("treatment")):
        return "time_series_treatment"
    if len(semantic_axes) > 1:
        return "mixed"
    if has_time:
        return "time_series"
    if "treatment" in semantic_axes:
        return "treatment"
    if "tissue" in semantic_axes:
        return "tissue"
    if "organ_part" in semantic_axes:
        return "organ_part"
    if "stage" in semantic_axes:
        return "development"
    if "genotype" in semantic_axes:
        return "genotype"
    if "cell_type" in semantic_axes:
        return "cell_type"
    return "group_comparison"


def build_experiment_context(
    prefix: str,
    sample_config: str | Path | None = None,
    input_dir: str | Path | None = None,
    final_dir: str | Path | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build optional biological context for recommendation/reporting.

    This context never changes DE grouping. It only records user-provided or
    file-provided metadata used later for interpretation/RAG.
    """
    params = params or {}
    input_path = Path(input_dir) if input_dir else (Path(sample_config).parent if sample_config else None)
    config = _read_sample_config(sample_config)
    project = resolve_rnaseq_project_context(
        input_dir=input_path,
        final_dir=final_dir,
        requested_prefix=prefix,
        sample_config=sample_config,
        params=params,
    )
    experiment_meta = _experiment_meta_from_mapping(config)
    param_experiment = _experiment_meta_from_mapping(params)
    experiment_meta.update({k: v for k, v in param_experiment.items() if v not in ("", [], None)})
    axis_values = _sample_axis_values(config)
    detected_axes = [axis for axis in ("treatment", "tissue", "organ_part", "stage", "time", "genotype", "trait", "cell_type", "condition") if axis in axis_values]
    design = infer_experiment_design(experiment_meta, axis_values)
    context_sources = {
        "species": project.get("source", ""),
        "experiment": "samples_yaml" if experiment_meta else "",
        "sample_axes": "samples_yaml" if axis_values else "",
    }
    explicit_topic = any(experiment_meta.get(key) for key in ("treatment", "trait")) or any(axis in detected_axes for axis in ("tissue", "organ_part", "stage", "genotype", "cell_type"))
    recommendation_mode = "context_aware" if explicit_topic else "context_neutral"
    confidence = "high" if experiment_meta else ("medium" if detected_axes else "low")
    context = {
        "rnaseq_experiment_context_version": "1.0",
        "rnaseq_experiment_design": design,
        "rnaseq_recommendation_mode": recommendation_mode,
        "rnaseq_context_confidence": confidence,
        "rnaseq_detected_axes": detected_axes,
        "rnaseq_axis_values": axis_values,
        "rnaseq_treatment": experiment_meta.get("treatment", ""),
        "rnaseq_treatment_keywords": experiment_meta.get("treatment_keywords", []),
        "rnaseq_trait": experiment_meta.get("trait", ""),
        "rnaseq_primary_axis": experiment_meta.get("primary_axis", ""),
        "rnaseq_control_condition": experiment_meta.get("control_condition", ""),
        "rnaseq_species_name": project.get("species_name", ""),
        "rnaseq_kegg_organism": project.get("kegg_organism", ""),
        "rnaseq_context_sources": {k: v for k, v in context_sources.items() if v},
    }
    for key in ("stage_order", "time_order"):
        if experiment_meta.get(key):
            context[f"rnaseq_{key}"] = experiment_meta[key]
    return {k: v for k, v in context.items() if v not in ("", [], {}, None)}


def write_experiment_context(final_dir: str | Path, prefix: str, context: Mapping[str, Any]) -> Path:
    path = Path(final_dir) / f"{prefix}.rnaseq_experiment_context.json"
    payload = dict(context or {})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_rnaseq_sample_metadata(sample_config: str | Path | None = None, input_dir: str | Path | None = None) -> dict[str, str]:
    paths: list[Path] = []
    if sample_config:
        paths.append(Path(sample_config))
    if input_dir:
        base = Path(input_dir)
        for pattern in ("samples.yaml", "samples.yml", "samples*.yaml", "samples*.yml", "*sample*.yaml", "*sample*.yml"):
            paths.extend(sorted(base.glob(pattern)))
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.exists() or yaml is None:
            continue
        seen.add(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, Mapping):
            continue
        merged: dict[str, str] = {}
        for meta in (data, data.get("species"), data.get("organism"), data.get("metadata"), data.get("experiment"), data.get("rnaseq")):
            for key, value in _species_meta_from_mapping(meta).items():
                if value and key not in merged:
                    merged[key] = value
        if merged:
            return merged
    return {}


def _infer_species_from_config_xlsx(input_dir: Path) -> str:
    config_path = input_dir / "config.xlsx"
    if not config_path.exists():
        return ""
    try:
        import pandas as pd  # type: ignore
        sheets = pd.read_excel(config_path, sheet_name=None)
    except Exception:
        return ""
    priority: list[str] = []
    fallback: list[str] = []
    for _sheet, df in sheets.items():
        if "Species_Code" not in df.columns:
            continue
        for _idx, row in df.fillna("").iterrows():
            species = _clean_project_value(row.get("Species_Code"))
            if not species:
                continue
            role = _clean_project_value(row.get("Species_Role")).lower()
            scope = _clean_project_value(row.get("File_Scope")).lower()
            file_type = _clean_project_value(row.get("File_Type")).lower()
            if role in {"target", ""} or scope in {"whole_genome", "genome"} or file_type in {"genome", "gff", "cds", "pep"}:
                priority.append(species)
            else:
                fallback.append(species)
    for group in (priority, fallback):
        unique: list[str] = []
        for item in group:
            if item and item not in unique:
                unique.append(item)
        if unique:
            return unique[0]
    return ""


def _infer_resource_reference_token(input_dir: Path) -> str:
    tokens: list[str] = []
    patterns = (
        "*.genome.fasta", "*.genome.fa", "*.genome.fna",
        "*.gff", "*.gff3", "*.gtf",
        "*.cds.fasta", "*.cds.fa", "*.pep.fasta", "*.pep.fa",
        "*.fa", "*.fasta", "*.fna",
    )
    if not input_dir.exists():
        return ""
    for pattern in patterns:
        for file in sorted(input_dir.glob(pattern)):
            token = file.name.split(".", 1)[0].strip()
            if token and re.match(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$", token):
                tokens.append(token)
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        token = child.name.strip()
        if token and re.match(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$", token):
            for pattern in patterns:
                if any(child.glob(pattern)):
                    tokens.append(token)
                    break
    for token in tokens:
        if token.lower() not in GENERIC_RNASEQ_PREFIXES:
            return token
    return tokens[0] if tokens else ""



def _reference_roots(input_dir: Path, species_code: str = "", reference_id: str = "") -> list[Path]:
    """Return prioritized one-level roots for RNA-seq reference resources."""
    roots: list[Path] = []

    def add(path: Path) -> None:
        if path.exists() and path not in roots:
            roots.append(path)

    add(input_dir)
    for token in (species_code, reference_id):
        token = _clean_project_value(token)
        if token:
            add(input_dir / token)
    if input_dir.exists():
        for child in sorted(input_dir.iterdir()):
            if child.is_dir():
                add(child)
    return roots


def _existing_first(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def find_rnaseq_hisat2_index(input_dir: str | Path, species_code: str = "", reference_id: str = "") -> Path | None:
    """Find a genome HISAT2 index prefix, never a CDS/PEP index."""
    base = Path(input_dir)
    species = _clean_project_value(species_code)
    reference = _clean_project_value(reference_id)
    for root in _reference_roots(base, species, reference):
        exact: list[Path] = []
        if species:
            exact.append(root / f"{species}.genome.1.ht2")
        marker = _existing_first(exact)
        if marker:
            return Path(str(marker)[: -len(".1.ht2")])
    for root in _reference_roots(base, species, reference):
        for marker in sorted(root.glob("*.genome.1.ht2")):
            name = marker.name.lower()
            if ".cds." in name or ".pep." in name or ".protein." in name:
                continue
            return Path(str(marker)[: -len(".1.ht2")])
    return None


def find_rnaseq_reference_file(
    input_dir: str | Path,
    kind: str,
    species_code: str = "",
    reference_id: str = "",
) -> Path | None:
    """Find canonical RNA-seq reference files with species-first priority."""
    base = Path(input_dir)
    species = _clean_project_value(species_code)
    reference = _clean_project_value(reference_id)
    kind = str(kind or "").strip().lower()
    suffixes = {
        "genome": ("genome.fasta", "genome.fa", "genome.fna"),
        "annotation": ("gff3", "gff", "gtf"),
        "cds": ("cds.fasta", "cds.fa", "cds.fna"),
        "pep": ("pep.fasta", "pep.fa", "protein.fasta", "protein.fa"),
        "transcriptome": ("cds.fasta", "cds.fa", "transcript.fasta", "transcript.fa"),
    }.get(kind, ())
    if not suffixes:
        return None

    for root in _reference_roots(base, species, reference):
        exact: list[Path] = []
        if species:
            if kind == "annotation":
                exact.extend([root / f"{species}_final.gff3", root / f"{species}.gff3", root / f"{species}.gff", root / f"{species}.gtf"])
            else:
                exact.extend(root / f"{species}.{suffix}" for suffix in suffixes)
        marker = _existing_first(exact)
        if marker:
            return marker

    for root in _reference_roots(base, species, reference):
        patterns = [f"*.{suffix}" for suffix in suffixes]
        if kind == "annotation":
            patterns.extend(["*_final.gff3", "*all_models.gff3"])
        for pattern in patterns:
            for file in sorted(root.glob(pattern)):
                lowered = file.name.lower()
                if kind == "genome" and any(token in lowered for token in (".cds.", ".pep.", ".protein.", "repr.cds")):
                    continue
                if file.is_file() and file.stat().st_size > 0:
                    return file
    return None


def _prefix_from_final_manifest(final_dir: Path) -> str:
    if not final_dir.exists():
        return ""
    preferred = (
        "*.rnaseq_wgcna.manifest.json",
        "*.rnaseq_fgsea.manifest.json",
        "*.rnaseq_candidate_family.manifest.json",
        "*.rnaseq_sample_qc.manifest.json",
        "*.rnaseq_de.manifest.json",
        "*.rnaseq_quant.manifest.json",
        "*.rnaseq_align.manifest.json",
        "*.rnaseq_qc.manifest.json",
    )
    for pattern in preferred:
        matches = sorted(final_dir.glob(pattern))
        if len(matches) == 1:
            suffix = pattern[1:]
            return matches[0].name[: -len(suffix)]
    return ""


def resolve_rnaseq_project_context(
    input_dir: str | Path | None = None,
    final_dir: str | Path | None = None,
    requested_prefix: str = "",
    sample_config: str | Path | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve RNA-seq species/output prefix without confusing reference IDs."""
    params = params or {}
    input_path = Path(input_dir) if input_dir else (Path(sample_config).parent if sample_config else Path("."))
    final_path = Path(final_dir) if final_dir else None
    sample_meta = read_rnaseq_sample_metadata(sample_config=sample_config, input_dir=input_path)
    param_species = _clean_project_value(params.get("speciesCode") or params.get("species_code"))
    param_output = _clean_project_value(
        params.get("rnaseqPrefix")
        or params.get("rnaseq_prefix")
        or params.get("rnaseqOutputPrefix")
        or params.get("rnaseq_output_prefix")
        or params.get("outputPrefix")
        or params.get("output_prefix")
    )
    config_species = _infer_species_from_config_xlsx(input_path)
    resource_token = _infer_resource_reference_token(input_path)
    final_prefix = _prefix_from_final_manifest(final_path) if final_path else ""
    requested = _clean_project_value(requested_prefix)

    species_code = param_species or sample_meta.get("species_code") or config_species
    reference_id = sample_meta.get("reference_id") or (resource_token if _looks_like_reference_version(resource_token) else "")

    explicit_output = param_output or sample_meta.get("output_prefix")
    if explicit_output:
        output_prefix = explicit_output
        source = "explicit_output_prefix"
    elif species_code:
        output_prefix = species_code
        source = "species_metadata" if (param_species or sample_meta.get("species_code")) else "config_xlsx"
    elif requested and not _is_gene_family_like_prefix(requested) and requested.lower() not in GENERIC_RNASEQ_PREFIXES and not _looks_like_reference_version(requested):
        output_prefix = requested
        species_code = species_code or species_from_prefix(requested)
        source = "requested_prefix"
    elif final_prefix and not _looks_like_reference_version(final_prefix):
        output_prefix = final_prefix
        species_code = species_code or species_from_prefix(final_prefix)
        source = "final_manifest"
    elif resource_token and not _looks_like_reference_version(resource_token):
        output_prefix = resource_token
        species_code = species_code or species_from_prefix(resource_token)
        source = "resource_filename"
    elif requested and requested.lower() not in GENERIC_RNASEQ_PREFIXES and not _looks_like_reference_version(requested):
        output_prefix = requested
        species_code = species_code or species_from_prefix(requested)
        source = "requested_prefix_fallback"
    else:
        output_prefix = ""
        species_code = species_code or ""
        source = "none"

    result = {
        "output_prefix": output_prefix,
        "species_code": species_code,
        "species_name": sample_meta.get("species_name", ""),
        "kegg_organism": sample_meta.get("kegg_organism", ""),
        "reference_id": reference_id,
        "resource_token": resource_token,
        "final_prefix": final_prefix,
        "source": source,
    }
    return {k: v for k, v in result.items() if v}


def _read_sample_config(path: str | Path | None) -> dict[str, Any]:
    if not path or yaml is None:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sample_names_from_count_matrix(path: str | Path | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        header = p.open(encoding="utf-8").readline().rstrip("\n").split("\t")
    except Exception:
        return []
    if len(header) < 2:
        return []
    samples = header[1:]
    featurecounts_meta = {"Chr", "Start", "End", "Strand", "Length"}
    if featurecounts_meta.issubset(set(samples)):
        samples = [s for s in samples if s not in featurecounts_meta]
        samples = [re.sub(r"\.sorted$", "", Path(s).stem) for s in samples]
    return samples


def _conditions_from_config(config: Mapping[str, Any], samples: list[str]) -> dict[str, str]:
    conditions: dict[str, str] = {}
    sample_meta = config.get("sample_info") or config.get("samples_info") or config.get("metadata")
    if isinstance(sample_meta, Mapping):
        for sample, meta in sample_meta.items():
            if isinstance(meta, Mapping):
                cond = meta.get("condition") or meta.get("group") or meta.get("treatment") or meta.get("time")
            else:
                cond = meta
            if cond is not None:
                conditions[str(sample)] = str(cond)
    groups = config.get("groups") or config.get("conditions")
    if isinstance(groups, Mapping):
        sample_set = set(samples)
        if groups and all(str(key) in sample_set for key in groups.keys()):
            for sample, cond in groups.items():
                if cond is not None:
                    conditions[str(sample)] = str(cond)
        else:
            for cond, members in groups.items():
                if isinstance(members, (list, tuple)):
                    for sample in members:
                        conditions[str(sample)] = str(cond)
    sample_entries = config.get("samples")
    if isinstance(sample_entries, Mapping):
        for sample, meta in sample_entries.items():
            if isinstance(meta, Mapping):
                cond = meta.get("condition") or meta.get("group") or meta.get("treatment") or meta.get("time")
                if cond is not None:
                    conditions[str(sample)] = str(cond)
    return {sample: conditions.get(sample, "") for sample in samples if sample in conditions}


def _condition_signature(conditions: Mapping[str, str], samples: list[str]) -> str:
    values = [str(conditions.get(sample) or "") for sample in samples]
    unique = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    if unique:
        return "_".join(_clean_token(v) for v in unique[:8])
    return "nogroups"


ALLOWED_RNASEQ_LIBRARY_STRANDNESS = {"non_stranded", "forward", "reverse"}
RNASEQ_LIBRARY_STRANDNESS_ALIASES = {
    "none": "non_stranded",
    "no": "non_stranded",
    "unstranded": "non_stranded",
    "non-stranded": "non_stranded",
    "non stranded": "non_stranded",
    "non_stranded": "non_stranded",
    "0": "non_stranded",
    "fr-unstranded": "non_stranded",
    "forward": "forward",
    "sense": "forward",
    "stranded": "forward",
    "yes": "forward",
    "1": "forward",
    "fr-firststrand": "forward",
    "reverse": "reverse",
    "reverse_stranded": "reverse",
    "reverse-stranded": "reverse",
    "antisense": "reverse",
    "2": "reverse",
    "fr-secondstrand": "reverse",
}


class RnaseqLibraryStrandnessError(ValueError):
    """Raised when samples.yaml does not explicitly define library strandness."""


def normalize_rnaseq_library_strandness(value: Any) -> str:
    text = _clean_project_value(value).strip().lower().replace("_", "-")
    text = text.replace(" ", "-")
    if not text:
        return ""
    normalized = RNASEQ_LIBRARY_STRANDNESS_ALIASES.get(text) or RNASEQ_LIBRARY_STRANDNESS_ALIASES.get(text.replace("-", "_"))
    if normalized:
        return normalized
    if text in ALLOWED_RNASEQ_LIBRARY_STRANDNESS:
        return text
    raise RnaseqLibraryStrandnessError(
        "samples.yaml 中 library_strandness 的取值无效: "
        f"{value!r}。允许值: non_stranded, forward, reverse。"
    )


def resolve_rnaseq_library_strandness(
    sample_config: str | Path | None,
    samples: list[str] | None = None,
    require_all: bool = False,
) -> dict[str, Any]:
    config = _read_sample_config(sample_config)
    sample_entries = config.get("samples") if isinstance(config.get("samples"), Mapping) else {}
    sample_names = list(samples or [])
    if not sample_names and isinstance(sample_entries, Mapping):
        sample_names = list(map(str, sample_entries.keys()))
    rnaseq_block = config.get("rnaseq") if isinstance(config.get("rnaseq"), Mapping) else {}
    project_value = (
        rnaseq_block.get("library_strandness")
        or config.get("library_strandness")
        or config.get("strandness")
        or config.get("libraryType")
        or config.get("library_type")
    )
    project_strandness = normalize_rnaseq_library_strandness(project_value) if project_value not in (None, "") else ""

    by_sample: dict[str, str] = {}
    missing: list[str] = []
    for sample in sample_names:
        meta = sample_entries.get(sample) if isinstance(sample_entries, Mapping) else {}
        sample_value = None
        if isinstance(meta, Mapping):
            sample_value = (
                meta.get("library_strandness")
                or meta.get("strandness")
                or meta.get("libraryType")
                or meta.get("library_type")
            )
        value = normalize_rnaseq_library_strandness(sample_value) if sample_value not in (None, "") else project_strandness
        if value:
            by_sample[str(sample)] = value
        else:
            missing.append(str(sample))

    if require_all:
        if not sample_config:
            raise RnaseqLibraryStrandnessError(
                "RNA-seq Align/Quant 必须提供 samples.yaml，并在 rnaseq.library_strandness "
                "或每个 samples.<sample>.library_strandness 中明确链特异性。"
            )
        if missing:
            preview = ", ".join(missing[:10])
            suffix = f" 等 {len(missing)} 个样本" if len(missing) > 10 else ""
            raise RnaseqLibraryStrandnessError(
                "samples.yaml 缺少 library_strandness，不能默认按非链特异性处理。"
                f"请为样本 {preview}{suffix} 填写 non_stranded / forward / reverse。"
            )

    values = sorted(set(by_sample.values()))
    signature_parts = [f"{sample}:{by_sample.get(sample, '')}" for sample in sorted(by_sample)]
    signature = _short_hash(signature_parts) if signature_parts else ""
    return {
        "project_library_strandness": project_strandness,
        "library_strandness_by_sample": by_sample,
        "library_strandness_values": values,
        "library_strandness_missing_samples": missing,
        "library_strandness_mixed": len(values) > 1,
        "library_strandness_signature": signature,
    }


def require_rnaseq_library_strandness(sample_config: str | Path | None, samples: list[str]) -> dict[str, Any]:
    return resolve_rnaseq_library_strandness(sample_config, samples=samples, require_all=True)


def hisat2_rna_strandness_args(strandness: str) -> list[str]:
    value = normalize_rnaseq_library_strandness(strandness)
    if value == "forward":
        return ["--rna-strandness", "FR"]
    if value == "reverse":
        return ["--rna-strandness", "RF"]
    return []


def featurecounts_strandness_value(strandness: str) -> str:
    value = normalize_rnaseq_library_strandness(strandness)
    return {"non_stranded": "0", "forward": "1", "reverse": "2"}[value]


def _short_hash(parts: list[str]) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    return digest[:8]


def build_rnaseq_context(
    prefix: str,
    sample_config: str | Path | None = None,
    count_matrix: str | Path | None = None,
    sample_table: str | Path | None = None,
    foreground_mode: str = "global",
    family_members_file: str | Path | None = None,
    family_scope_id: str = "",
    family_label: str = "",
    stage: str = "",
) -> dict[str, Any]:
    config = _read_sample_config(sample_config)
    samples = _sample_names_from_count_matrix(count_matrix)
    table_conditions: dict[str, str] = {}
    table_axis_values: dict[str, list[str]] = {}
    if sample_table:
        p = Path(sample_table)
        if p.exists():
            try:
                rows = [row for row in p.read_text(encoding="utf-8").splitlines() if row.strip()]
                if len(rows) > 1:
                    header = [h.strip().lower() for h in rows[0].split("\t")]
                    sample_idx = header.index("sample") if "sample" in header else 0
                    condition_idx = header.index("condition") if "condition" in header else (1 if len(header) > 1 else -1)
                    time_idx = next((header.index(key) for key in ("time", "timepoint", "hour") if key in header), -1)
                    parsed_samples = []
                    for row in rows[1:]:
                        parts = row.split("\t")
                        if len(parts) <= sample_idx:
                            continue
                        sample = parts[sample_idx].strip()
                        if not sample:
                            continue
                        parsed_samples.append(sample)
                        if condition_idx >= 0 and len(parts) > condition_idx:
                            table_conditions[sample] = parts[condition_idx].strip()
                        if time_idx >= 0 and len(parts) > time_idx:
                            time_value = parts[time_idx].strip()
                            if time_value:
                                table_axis_values.setdefault("time", [])
                                if time_value not in table_axis_values["time"]:
                                    table_axis_values["time"].append(time_value)
                    if not samples:
                        samples = parsed_samples
            except Exception:
                if not samples:
                    samples = []
    if not samples:
        sample_entries = config.get("samples")
        if isinstance(sample_entries, Mapping):
            samples = list(map(str, sample_entries.keys()))
    conditions = _conditions_from_config(config, samples)
    for sample, condition in table_conditions.items():
        if sample in samples and condition:
            conditions[sample] = condition
    condition_sig = _condition_signature(conditions, samples)
    project_context = resolve_rnaseq_project_context(
        input_dir=Path(sample_config).parent if sample_config else None,
        requested_prefix=prefix,
        sample_config=sample_config,
    )
    species = project_context.get("species_code") or species_from_prefix(prefix)
    output_prefix = project_context.get("output_prefix") or prefix
    sample_count = len(samples)
    hash_parts = [output_prefix, species, condition_sig, str(sample_count), *(samples[:20])]
    rnaseq_scope_id = f"{_clean_token(species)}.rnaseq.{condition_sig}.{sample_count}samples.{_short_hash(hash_parts)}"
    fg = str(foreground_mode or "global").strip().lower()
    if fg not in {"auto", "global", "family", "both"}:
        fg = "global"
    family_path = str(family_members_file or "")
    context = {
        "scope_type": "rnaseq_experiment",
        "rnaseq_scope_id": rnaseq_scope_id,
        "rnaseq_species": species,
        "rnaseq_output_prefix": output_prefix,
        "rnaseq_reference_id": project_context.get("reference_id", ""),
        "rnaseq_context_source": project_context.get("source", ""),
        "rnaseq_sample_count": sample_count,
        "rnaseq_condition_count": len({v for v in conditions.values() if v}),
        "rnaseq_condition_signature": condition_sig,
        "rnaseq_stage": stage,
        "foreground_mode": fg,
        "family_scope_id": str(family_scope_id or ""),
        "family_label": str(family_label or ""),
        "family_members_file": family_path,
        "rnaseq_context_version": "1.0",
    }
    experiment_context = build_experiment_context(prefix=prefix, sample_config=sample_config, params={})
    if table_axis_values:
        axis_values = dict(experiment_context.get("rnaseq_axis_values", {}))
        for axis, values in table_axis_values.items():
            existing = axis_values.setdefault(axis, [])
            for value in values:
                if value not in existing:
                    existing.append(value)
        experiment_context["rnaseq_axis_values"] = axis_values
        detected_axes = [
            axis
            for axis in ("treatment", "tissue", "organ_part", "stage", "time", "genotype", "trait", "cell_type", "condition")
            if axis in axis_values
        ]
        experiment_context["rnaseq_detected_axes"] = detected_axes
        if experiment_context.get("rnaseq_experiment_design") in (None, "", "group_comparison"):
            experiment_context["rnaseq_experiment_design"] = infer_experiment_design({}, axis_values)
        sources = dict(experiment_context.get("rnaseq_context_sources", {}))
        sources["sample_axes"] = sources.get("sample_axes") or "sample_table"
        experiment_context["rnaseq_context_sources"] = sources
        if experiment_context.get("rnaseq_context_confidence") in (None, "", "low"):
            experiment_context["rnaseq_context_confidence"] = "medium"
    context.update(experiment_context)
    strandness = resolve_rnaseq_library_strandness(sample_config, samples=samples, require_all=False)
    if strandness.get("library_strandness_by_sample"):
        context.update({
            "rnaseq_library_strandness_by_sample": strandness["library_strandness_by_sample"],
            "rnaseq_library_strandness_values": strandness["library_strandness_values"],
            "rnaseq_library_strandness_signature": strandness["library_strandness_signature"],
            "rnaseq_library_strandness_mixed": strandness["library_strandness_mixed"],
        })
    return context


def apply_rnaseq_context(manifest: dict[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    ctx = dict(context or {})
    for key, value in ctx.items():
        manifest[key] = value
    params = manifest.setdefault("parameters", {})
    if isinstance(params, dict):
        for key, value in ctx.items():
            params.setdefault(key, value)
    return manifest

RNASEQ_CONTEXT_KEYS = {
    "scope_type",
    "rnaseq_scope_id",
    "rnaseq_species",
    "rnaseq_output_prefix",
    "rnaseq_reference_id",
    "rnaseq_context_source",
    "rnaseq_sample_count",
    "rnaseq_condition_count",
    "rnaseq_condition_signature",
    "rnaseq_stage",
    "foreground_mode",
    "family_scope_id",
    "family_label",
    "family_members_file",
    "rnaseq_context_version",
    "rnaseq_experiment_context_version",
    "rnaseq_experiment_design",
    "rnaseq_recommendation_mode",
    "rnaseq_context_confidence",
    "rnaseq_detected_axes",
    "rnaseq_axis_values",
    "rnaseq_treatment",
    "rnaseq_treatment_keywords",
    "rnaseq_trait",
    "rnaseq_primary_axis",
    "rnaseq_control_condition",
    "rnaseq_species_name",
    "rnaseq_kegg_organism",
    "rnaseq_context_sources",
    "rnaseq_library_strandness_by_sample",
    "rnaseq_library_strandness_values",
    "rnaseq_library_strandness_signature",
    "rnaseq_library_strandness_mixed",
    "rnaseq_stage_order",
    "rnaseq_time_order",
    "rnaseq_experiment_context_path",
}


def context_from_manifest(manifest: Mapping[str, Any], stage: str = "") -> dict[str, Any]:
    """Extract RNA-seq context fields from an upstream manifest."""
    context = {key: manifest.get(key) for key in RNASEQ_CONTEXT_KEYS if manifest.get(key) not in (None, "")}
    params = manifest.get("parameters")
    if isinstance(params, Mapping):
        for key in RNASEQ_CONTEXT_KEYS:
            if key not in context and params.get(key) not in (None, ""):
                context[key] = params.get(key)
    if context:
        context["scope_type"] = "rnaseq_experiment"
        context["rnaseq_stage"] = stage or str(context.get("rnaseq_stage") or "")
        context.setdefault("foreground_mode", "global")
        context.setdefault("rnaseq_context_version", "1.0")
    return context


def find_upstream_rnaseq_context(
    final_dir: str | Path,
    prefix: str,
    stage: str = "",
    upstream_stages: tuple[str, ...] = (
        "rnaseq_de",
        "expression",
        "rnaseq_sample_qc",
        "rnaseq_quant",
        "rnaseq_align",
        "rnaseq_qc",
    ),
) -> dict[str, Any]:
    """Find the nearest available RNA-seq context in final/ manifests."""
    base = Path(final_dir)
    candidates: list[Path] = []
    clean_prefix = str(prefix or "").strip()
    for upstream in upstream_stages:
        if clean_prefix:
            candidates.extend(base.glob(f"{clean_prefix}.{upstream}.manifest.json"))
        candidates.extend(base.glob(f"*.{upstream}.manifest.json"))
    seen: set[Path] = set()
    for manifest_path in candidates:
        if manifest_path in seen or not manifest_path.exists():
            continue
        seen.add(manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        context = context_from_manifest(manifest, stage=stage)
        if context.get("rnaseq_scope_id"):
            return context
    return {}


def merge_rnaseq_context(*contexts: Mapping[str, Any], stage: str = "") -> dict[str, Any]:
    """Merge context dictionaries, preserving later non-empty values."""
    merged: dict[str, Any] = {}
    for context in contexts:
        for key, value in dict(context or {}).items():
            if value not in (None, ""):
                merged[key] = value
    if merged:
        merged["scope_type"] = "rnaseq_experiment"
        if stage:
            merged["rnaseq_stage"] = stage
        merged.setdefault("foreground_mode", "global")
        merged.setdefault("rnaseq_context_version", "1.0")
    return merged
