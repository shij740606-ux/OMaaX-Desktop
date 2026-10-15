from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.config_templates import CONFIG_HEADERS


FASTQ_SUFFIX_RE = re.compile(r"\.(fastq|fq)(\.gz)?$", re.I)
CONFIG_COLUMNS = CONFIG_HEADERS
CONFIG_REVIEW_COLUMNS = [*CONFIG_HEADERS, "confidence", "notes"]


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "undefined"} else text


def parse_key_values(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    if not text:
        return data
    patterns = {
        "species_code": r"(?:species_code|物种代码|代码)\s*(?:是|为|=|:|：)?\s*([A-Za-z][A-Za-z0-9_-]{0,12})",
        "species_name": r"(?:species_name|拉丁名|物种名|物种名称)\s*(?:是|为|=|:|：)?\s*([A-Za-z][A-Za-z ._-]{2,80})",
        "gene_family": r"(?:gene_family|基因家族|家族)\s*(?:是|为|=|:|：)?\s*([A-Za-z0-9_.-]{2,30})",
        "target": r"(?:target|目标物种)\s*(?:是|为|=|:|：)?\s*([A-Za-z][A-Za-z0-9_-]{0,12})",
        "control": r"(?:control|对照组|ck)\s*(?:是|为|=|:|：)?\s*([A-Za-z0-9_.-]+)",
        "treatment": r"(?:treatment|stress|处理|处理方式|实验处理)\s*(?:是|为|=|:|：)?\s*([A-Za-z0-9\u4e00-\u9fff _.+-]{1,40})",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            data[key] = match.group(1).strip(" ，,;；。")
    if "treatment" not in data:
        keyword_patterns = [
            ("salt stress", ["盐胁迫", "盐处理", "salt stress", "salinity", "nacl"]),
            ("drought stress", ["干旱胁迫", "干旱", "drought"]),
            ("heat stress", ["热胁迫", "高温胁迫", "heat stress", "heat"]),
            ("cold stress", ["冷胁迫", "低温胁迫", "cold stress", "cold"]),
            ("cadmium stress", ["镉胁迫", "镉处理", "cadmium", "cd stress"]),
            ("pathogen stress", ["病原", "侵染", "pathogen", "infection"]),
        ]
        lower = text.lower()
        for label, markers in keyword_patterns:
            if any(marker.lower() in lower for marker in markers):
                data["treatment"] = label
                break
    return data


def infer_species_code(name: str, hint: str = "") -> tuple[str, str]:
    lower = name.lower()
    known = [
        ("Lc", ["litchi_chinensis", "litchi-chinensis", "litchi.chinensis", "litchi", "lychee", "lchinesis", "lchinensis", "lc."]),
        ("Dl", ["dimocarpus_longan", "dimocarpus-longan", "dimocarpus.longan", "dimocarpus", "longan", "dl."]),
        ("Os", ["oryza_sativa", "oryza-sativa", "oryza.sativa", "oryza", "irgsp", "osa", "os."]),
        ("At", ["arabidopsis_thaliana", "arabidopsis-thaliana", "arabidopsis.thaliana", "arabidopsis", "thaliana", "tair", "ath", "at."]),
        ("Zm", ["zea_mays", "zea-mays", "zea.mays", "zea", "maize", "mays", "zma", "zm."]),
        ("Sl", ["solanum_lycopersicum", "solanum-lycopersicum", "solanum.lycopersicum", "solanum", "tomato", "sly", "sl."]),
        ("Cp", ["carica_papaya", "carica-papaya", "carica.papaya", "cpapaya", "papaya", "cpap", "cp."]),
        ("Ft", ["fagopyrum_tataricum", "fagopyrum-tataricum", "fagopyrum.tataricum", "fagopyrum", "tataricum", "buckwheat", "ft."]),
    ]
    for code, markers in known:
        if any(marker in lower for marker in markers):
            return code, "high"
    match = re.match(r"([A-Z][a-zA-Z0-9]{1,5})[._-]", name)
    if match:
        return match.group(1), "high"
    if hint:
        return hint, "medium"
    return "", "low"


def infer_file_type(path: Path) -> tuple[str, str, str]:
    name = path.name.lower()
    if FASTQ_SUFFIX_RE.search(name):
        return "fastq", "high", "RNA-seq FASTQ"
    if "promoter" in name or "upstream" in name or "up2000" in name:
        return "", "low", "Promoter FASTA is not a config.xlsx genome/cds/pep/gff input"
    if name.endswith((".gff", ".gff3", ".gtf", ".gff.gz", ".gff3.gz", ".gtf.gz")):
        return "gff", "high", ""
    if any(x in name for x in ["cds", "cdna"]) and name.endswith((".fa", ".fasta", ".fna", ".ffn", ".cds", ".fa.gz", ".fasta.gz", ".fna.gz", ".ffn.gz", ".cds.gz")):
        return "cds", "high", ""
    if any(x in name for x in ["pep", "protein", ".faa"]) and name.endswith((".fa", ".fasta", ".faa", ".pep", ".fa.gz", ".fasta.gz", ".faa.gz", ".pep.gz")):
        return "pep", "high", ""
    if any(x in name for x in ["genome", "genomic", "dna", "toplevel", "chr", "chromosome"]) and name.endswith((".fa", ".fasta", ".fna", ".fa.gz", ".fasta.gz", ".fna.gz")):
        return "genome", "medium", ""
    if name.endswith((".fa", ".fasta", ".fna", ".faa", ".fa.gz", ".fasta.gz", ".fna.gz", ".faa.gz")):
        return "pep", "low", "FASTA type is ambiguous; confirm pep/cds/genome"
    return "", "low", "Unrecognized file type"


def scan_files(source_dir: Path, recursive: bool = True) -> list[Path]:
    if not source_dir.exists():
        return []
    iterator = source_dir.rglob("*") if recursive else source_dir.iterdir()
    skip_parts = {
        "uploaded_configs",
        "logs",
        "cache",
        "__pycache__",
        "final",
        "genome_prepare",
        "reproducibility",
    }
    out = []
    for path in iterator:
        if not path.is_file():
            continue
        if any(part in skip_parts for part in path.parts):
            continue
        if path.name.startswith("."):
            continue
        out.append(path)
    return sorted(out)


def backup_if_exists(path: Path, archive_dir: Path, label: str) -> str:
    if not path.exists():
        return ""
    archive_dir.mkdir(parents=True, exist_ok=True)
    backup = archive_dir / f"{now_tag()}_previous_{label}"
    shutil.copy2(path, backup)
    return str(backup)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
