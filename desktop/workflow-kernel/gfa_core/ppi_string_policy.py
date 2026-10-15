"""Shared STRING/PPI policy helpers.

This module intentionally stays small: it centralizes species aliases and
stage-local mapping thresholds without owning PPI execution or Ortholog.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


# Verified with STRING v12 API get_string_ids on 2026-07-07:
# species=4530 resolves rice query IDs to ncbiTaxonId 39947 (Oryza sativa Japonica).
STRING_SPECIES_MAP: Dict[str, str] = {
    "ath": "3702",
    "soe": "3562",
    "osa": "39947",
    "zma": "4577",
    "gma": "3847",
    "ptr": "3694",
    "vvi": "29760",
    "sly": "4081",
    "mtr": "3880",
    "stu": "4113",
    "bdi": "29655",
    "sbi": "4558",
}


STRING_SPECIES_ALIASES: Dict[str, str] = {
    **{code: code for code in STRING_SPECIES_MAP},
    "at": "ath",
    "ath": "ath",
    "arabidopsis": "ath",
    "arabidopsis_thaliana": "ath",
    "arabidopsis thaliana": "ath",
    "soe": "soe",
    "spinacia": "soe",
    "spinacia_oleracea": "soe",
    "spinacia oleracea": "soe",
    "spinach": "soe",
    "os": "osa",
    "osa": "osa",
    "rice": "osa",
    "oryza_sativa": "osa",
    "oryza sativa": "osa",
    "zma": "zma",
    "zm": "zma",
    "maize": "zma",
    "zea_mays": "zma",
    "zea mays": "zma",
    "gma": "gma",
    "gm": "gma",
    "soybean": "gma",
    "glycine_max": "gma",
    "glycine max": "gma",
    "ptr": "ptr",
    "pt": "ptr",
    "populus": "ptr",
    "populus_trichocarpa": "ptr",
    "populus trichocarpa": "ptr",
    "vvi": "vvi",
    "vv": "vvi",
    "grape": "vvi",
    "vitis_vinifera": "vvi",
    "vitis vinifera": "vvi",
    "sly": "sly",
    "tomato": "sly",
    "solanum_lycopersicum": "sly",
    "solanum lycopersicum": "sly",
}

STRING_REFERENCE_RECOMMENDATIONS: Dict[str, List[Dict[str, str]]] = {
    # Fagopyrum tataricum is not present in STRING v12. Spinacia oleracea is
    # STRING-supported and in the same order (Caryophyllales), making it a
    # closer reference than Arabidopsis for ortholog projection.
    "ft": [
        {
            "species_code": "soe",
            "species_name": "Spinacia oleracea",
            "taxon_id": "3562",
            "relationship": "same_order_caryophyllales",
            "source": "STRING v12 species list",
            "confidence": "near_reference",
        }
    ],
    "fagopyrum tataricum": [
        {
            "species_code": "soe",
            "species_name": "Spinacia oleracea",
            "taxon_id": "3562",
            "relationship": "same_order_caryophyllales",
            "source": "STRING v12 species list",
            "confidence": "near_reference",
        }
    ],
    "fagopyrum_tataricum": [
        {
            "species_code": "soe",
            "species_name": "Spinacia oleracea",
            "taxon_id": "3562",
            "relationship": "same_order_caryophyllales",
            "source": "STRING v12 species list",
            "confidence": "near_reference",
        }
    ],
}


def species_to_string_code(species: str, fallback: str = "ath") -> str:
    token = str(species or "").strip().lower()
    if not token:
        return fallback
    return STRING_SPECIES_ALIASES.get(token, fallback)


def recommended_string_reference_species(target_species: str) -> List[Dict[str, str]]:
    token = str(target_species or "").strip().lower()
    if not token:
        return []
    return [dict(item) for item in STRING_REFERENCE_RECOMMENDATIONS.get(token, [])]


def string_taxon_id(species_code: str, fallback: str = "3702") -> str:
    code = species_to_string_code(species_code, "")
    return STRING_SPECIES_MAP.get(code, fallback)


def unique_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    unique = []
    for value in values:
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def normalize_gene_ids_for_string(gene_ids: List[str], species: str) -> List[str]:
    """Convert local transcript IDs to stage-local STRING query IDs."""
    species_code = species_to_string_code(species, species)
    normalized = []
    for gene_id in gene_ids:
        value = str(gene_id or "").strip()
        if species_code == "osa":
            match = re.match(r"^(Os\d{2})t(\d+)(?:-\d+)?$", value, flags=re.IGNORECASE)
            if match:
                value = f"{match.group(1)}g{match.group(2)}"
        normalized.append(value)
    return unique_preserve_order(normalized)


def reference_protein_candidate_paths(species: str, roots: Optional[List[Path]] = None) -> List[Path]:
    """Return conservative local reference proteome candidates for PPI fallback."""
    roots = roots or [Path("/data/ref_genome"), Path("/data")]
    raw = str(species or "").strip()
    code = species_to_string_code(raw, raw.lower())
    tokens = unique_preserve_order([raw, raw.lower(), code])
    suffixes = [
        "{token}.pep.fasta",
        "{token}.pep.fa",
        "{token}.protein.fasta",
        "{token}.protein.fa",
        "{token}.faa",
    ]
    candidates: List[Path] = []
    for root in roots:
        for token in tokens:
            if not token:
                continue
            for suffix in suffixes:
                name = suffix.format(token=token)
                candidates.append(root / token / name)
                candidates.append(root / name)
    return unique_preserve_order(candidates)


def evaluate_string_mapping_quality(input_count: int, mapped_count: int) -> Dict[str, object]:
    input_count = max(int(input_count or 0), 0)
    mapped_count = max(int(mapped_count or 0), 0)
    mapping_rate = (mapped_count / input_count) if input_count else 0.0
    required_count = min(3, input_count)
    ok = input_count > 0 and mapped_count >= required_count and mapping_rate >= 0.5
    if input_count <= 2:
        ok = input_count > 0 and mapped_count == input_count
    return {
        "status": "success" if ok else "failed",
        "issue_code": "" if ok else "ppi_string_id_mapping_low",
        "input_count": input_count,
        "mapped_count": mapped_count,
        "mapping_rate": round(mapping_rate, 6),
        "required_mapped_count": required_count,
    }


def build_ppi_species_diagnostic(
    *,
    requested_ppi_species: str,
    target_species: str,
    target_string_supported: bool,
    reference_species: str = "",
    reference_data_source: str = "",
    ortholog_mapping_used: bool = False,
    fallback_level: str = "",
    fallback_reason: str = "",
    selected_string_species: str = "",
    selected_taxon_id: str = "",
    prefix: str = "",
    artifacts: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Build a failed PPI manifest fragment for missing species/mapping inputs.

    PPI execution does not run Ortholog or download references. This diagnostic
    tells the planner/user what must happen before PPI can run.
    """
    reference_available = bool(reference_data_source)
    issue_code = "ppi_requires_ortholog_mapping" if reference_available else "ppi_reference_data_missing"
    if ortholog_mapping_used:
        issue_code = "ppi_invalid_ortholog_mapping"
    recommendation = (
        "Run Ortholog as a separate stage using the available reference protein FASTA, then rerun PPI."
        if reference_available
        else "Provide or select a near/reference species protein FASTA, then run Ortholog before PPI."
    )
    if reference_available:
        recommended_actions = [
            {
                "id": "run_ortholog",
                "label": "Run Ortholog before PPI",
                "requires_confirmation": False,
                "risk": "required_mapping_step",
            },
            {
                "id": "change_reference_species",
                "label": "Select a different reference species with protein FASTA",
                "requires_confirmation": True,
                "risk": "reference_choice_changes_network",
            },
        ]
    else:
        recommended_actions = [
            {
                "id": "upload_reference_protein",
                "label": "Upload a near/reference species protein FASTA",
                "requires_confirmation": False,
                "risk": "requires_user_data",
            },
            {
                "id": "prepare_reference_download",
                "label": "Prepare a provenance-recorded reference protein download",
                "requires_confirmation": True,
                "risk": "network_and_release_dependent",
            },
            {
                "id": "explicit_arabidopsis_last_resort",
                "label": "Use Arabidopsis as an explicit last-resort model reference",
                "requires_confirmation": True,
                "risk": "low_confidence_model_reference",
            },
        ]
    return {
        "manifest_version": "1.0",
        "stage": "ppi",
        "status": "failed",
        "prefix": prefix,
        "parameters": {
            "ppi_species_resolution": {
                "requested_ppi_species": requested_ppi_species or "auto",
                "target_species": target_species,
                "target_string_supported": bool(target_string_supported),
                "selected_string_species": selected_string_species,
                "selected_taxon_id": selected_taxon_id,
                "reference_species": reference_species,
                "reference_data_source": reference_data_source,
                "ortholog_mapping_used": bool(ortholog_mapping_used),
                "ortholog_mapping_source": "",
                "fallback_level": fallback_level,
                "fallback_reason": fallback_reason,
                "decision": "failed_requires_ortholog",
            }
        },
        "metrics": {
            "id_resolution": {},
            "string_image_status": "skipped",
        },
        "diagnostics": {
            "issue_code": issue_code,
            "severity": "blocking",
            "layer": "input_contract",
            "required_next_stage": "ortholog",
            "recommendation": recommendation,
            "recommended_actions": recommended_actions,
        },
        "artifacts": artifacts or {},
    }
