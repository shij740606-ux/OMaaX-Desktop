"""Extension-stage specific intent slot resolver.

This module is deliberately not a global IntentAgent. It only extracts
extension-stage slots after the global router has identified an extension task.
"""

from __future__ import annotations

import re
from typing import Any

FILE_PATTERN = r"[\w.-]+\.(?:pep\.fasta|pep\.fa|cds\.fasta|cds\.fa|geneID|geneid|ids|id|fasta|faa|fa|hmm|tsv|csv|txt)"


def extract_explicit_input_files(message: str) -> list[str]:
    text = _normalize_spaced_suffix(str(message or ""))
    files: list[str] = []
    for match in re.finditer(FILE_PATTERN, text, flags=re.I):
        value = match.group(0).strip("，,。.;；:：()[]{}\"'")
        if value and value not in files:
            files.append(value)
    return files


def resolve_extension_slots(message: str, extension: dict[str, Any] | None = None, *, known_species: list[str] | None = None) -> dict[str, Any]:
    text = _normalize_spaced_suffix(str(message or ""))
    explicit_files = extract_explicit_input_files(text)
    file_roles = _directional_file_roles(text)
    species = []
    for code in known_species or []:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", text):
            species.append(code)
    if not species:
        for file_name in explicit_files:
            match = re.match(r"([A-Z][A-Za-z0-9]*)\.", file_name)
            if match and match.group(1) not in species:
                species.append(match.group(1))
    target_species = species[:1]
    reference_species = []
    for file_name, role in file_roles.items():
        match = re.match(r"([A-Z][A-Za-z0-9]*)\.", file_name)
        if not match:
            continue
        code = match.group(1)
        if role in {"reference", "database"} and code not in reference_species:
            reference_species.append(code)
        if role in {"query", "fasta", "id_list"} and not target_species:
            target_species = [code]
    return {
        "extension_id": (extension or {}).get("extension_id", ""),
        "extensionName": (extension or {}).get("display_name") or (extension or {}).get("name", ""),
        "analysis_type": (extension or {}).get("analysis_type", ""),
        "targetSpecies": target_species,
        "referenceSpecies": reference_species,
        "explicit_input_files": explicit_files,
        "input_paths": explicit_files,
        "file_roles": file_roles,
        "parameters": {},
    }


def _directional_file_roles(text: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    file_re = f"({FILE_PATTERN})"
    extraction_patterns = [
        rf"(?:用|使用|根据)\s*{file_re}\s*(?:从|在)\s*{file_re}\s*(?:中)?\s*(?:提取|抽取)",
        rf"{file_re}\s*(?:从|在)\s*{file_re}\s*(?:中)?\s*(?:提取|抽取)",
    ]
    for pattern in extraction_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            roles[match.group(1)] = "id_list"
            roles[match.group(2)] = "fasta"
            return roles
    blast_patterns = [
        rf"(?:用|使用)\s*{file_re}\s*(?:来)?\s*(?:进行|对)?\s*{file_re}\s*(?:的)?\s*(?:比对|blast|blastp)",
        rf"(?:根据|用|使用)\s*{file_re}\s*(?:来)?\s*(?:进行|对)\s*{file_re}\s*(?:的)?\s*(?:比对|blast|blastp)",
        rf"{file_re}\s*(?:vs|VS|对|比对)\s*{file_re}",
    ]
    for pattern in blast_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            roles[match.group(1)] = "query"
            roles[match.group(2)] = "reference"
            return roles
    for file_name in extract_explicit_input_files(text):
        lower = file_name.lower()
        if lower.endswith((".id", ".ids", ".geneid", ".geneids")):
            roles[file_name] = "id_list"
        elif lower.endswith(".hmm"):
            roles[file_name] = "hmm_profile"
        elif lower.endswith((".fa", ".fasta", ".faa", ".pep", ".pep.fa", ".pep.fasta", ".cds.fa", ".cds.fasta")):
            roles[file_name] = "fasta"
    return roles


def _normalize_spaced_suffix(text: str) -> str:
    return re.sub(
        r"([A-Za-z0-9_.-]+)\s+(\.(?:fasta|fa|faa|pep|hmm|geneids|geneid|geneld|ids|id|txt|tsv|csv)\b)",
        r"\1\2",
        text,
        flags=re.I,
    )
