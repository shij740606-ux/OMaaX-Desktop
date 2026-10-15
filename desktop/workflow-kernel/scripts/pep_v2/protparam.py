"""Fetch and parse ExPASy ProtParam results (official web endpoint)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTPARAM_URL = "https://web.expasy.org/cgi-bin/protparam/protparam"
ALLOWED_AA = set("ACDEFGHIKLMNPQRSTVWY")


def clean_sequence(sequence: str) -> str:
    return "".join(aa for aa in sequence.upper() if aa in ALLOWED_AA)


def parse_protparam_html(html: str) -> Dict[str, Any]:
    if "ERROR" in html and "at least 5 residues" in html:
        raise ValueError("ProtParam rejected sequence (<5 aa)")

    def grab(pattern: str, flags: int = 0) -> Optional[str]:
        match = re.search(pattern, html, flags)
        return match.group(1).strip() if match else None

    props: Dict[str, Any] = {
        "length": int(grab(r"Number of amino acids:</strong>\s*(\d+)") or 0),
        "molecular_weight": float(grab(r"Molecular weight:</strong>\s*([\d.]+)") or 0),
        "isoelectric_point": float(grab(r"Theoretical pI:</strong>\s*([\d.]+)") or 0),
        "gravy": float(grab(r"Grand average of hydropathicity \(GRAVY\):</strong>\s*([-\d.]+)") or 0),
        "aliphatic_index": float(grab(r"Aliphatic index:</strong>\s*([\d.]+)") or 0),
        "instability_index": grab(r"Instability index:</strong>\s*The instability index \(II\) is computed to be\s*([\d.]+)")
            or grab(r"Instability index \(II\) is computed to be\s*([\d.]+)"),
        "instability_class": grab(r"classifies the protein as\s+(\w+)", re.I),
        "negative_charges": grab(r"negatively charged residues \(Asp \+ Glu\):</strong>\s*(\d+)"),
        "positive_charges": grab(r"positively charged residues \(Arg \+ Lys\):</strong>\s*(\d+)"),
        "extinction_280": grab(r"Ext\. coefficient\s+(\d+)"),
        "abs_0_1pct": grab(r"Abs 0\.1% \(=1 g/l\)\s+([\d.]+)"),
        "formula": None,
        "half_life_mammal": grab(r"(\d+\s*hours)\s*\(mammalian reticulocytes", re.I),
        "half_life_yeast": grab(r"(>?\d+\s*hours)\s*\(yeast", re.I),
        "half_life_ecoli": grab(r"(>?\d+\s*hours)\s*\(Escherichia coli", re.I),
        "method": "ExPASy ProtParam",
        "source_url": PROTPARAM_URL,
    }
    if props["instability_index"] is not None:
        props["instability_index"] = float(props["instability_index"])
    for key in ("negative_charges", "positive_charges", "extinction_280"):
        if props[key] is not None:
            props[key] = int(props[key])
    formula_match = re.search(r"Formula:</strong>\s*(.+?)(?:<br|Total number of atoms)", html, re.I | re.S)
    if formula_match:
        formula_raw = formula_match.group(1)
        props["formula"] = re.sub(r"<[^>]+>", "", formula_raw).strip()
    return props


def fetch_protparam_sequence(sequence: str, timeout_sec: int = 120) -> Dict[str, Any]:
    clean = clean_sequence(sequence)
    if len(clean) < 5:
        raise ValueError(f"Sequence too short for ProtParam: {len(clean)} aa")
    data = urllib.parse.urlencode({"sequence": clean}).encode("utf-8")
    request = urllib.request.Request(
        PROTPARAM_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        html = response.read().decode("utf-8", errors="ignore")
    return parse_protparam_html(html)


def fetch_protparam_batch(
    sequences: Dict[str, str],
    output_dir: Path,
    delay_sec: float = 1.0,
) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []
    for idx, (seq_id, sequence) in enumerate(sequences.items(), start=1):
        record: Dict[str, Any] = {"id": seq_id}
        try:
            clean = clean_sequence(sequence)
            data = urllib.parse.urlencode({"sequence": clean}).encode("utf-8")
            request = urllib.request.Request(
                PROTPARAM_URL,
                data=data,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                html = response.read().decode("utf-8", errors="ignore")
            (output_dir / f"{seq_id}.protparam.html").write_text(html, encoding="utf-8")
            props = parse_protparam_html(html)
            record.update(props)
            record["status"] = "success"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)[:500]
        results.append(record)
        if idx < len(sequences) and delay_sec > 0:
            time.sleep(delay_sec)
    return results


def write_protparam_tables(
    results: List[Dict[str, Any]],
    analysis_dir: Path,
    final_dir: Path,
    prefix: str = "",
) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    full_tsv = analysis_dir / "protparam_full.tsv"
    if prefix:
        contract_tsv = analysis_dir / f"{prefix}_properties.tsv"
    else:
        contract_tsv = None
    plot_tsv = final_dir / "properties_data.tsv"

    columns = [
        "id", "length", "molecular_weight", "isoelectric_point", "gravy", "aliphatic_index",
        "instability_index", "instability_class", "negative_charges", "positive_charges",
        "extinction_280", "abs_0_1pct", "formula",
        "half_life_mammal", "half_life_yeast", "half_life_ecoli", "status", "method",
    ]
    table_lines = ["\t".join(columns)]
    for row in results:
        if row.get("status") != "success":
            continue
        table_lines.append("\t".join(str(row.get(col, "")) for col in columns))
    table_text = "\n".join(table_lines) + "\n"
    full_tsv.write_text(table_text, encoding="utf-8")
    if contract_tsv is not None:
        contract_tsv.write_text(table_text, encoding="utf-8")

    with open(plot_tsv, "w", encoding="utf-8") as handle:
        handle.write("id\tlength\tmw\tpI\tgravy\taliphatic\tinstability\tinstability_class\n")
        for row in results:
            if row.get("status") != "success":
                continue
            handle.write(
                f"{row['id']}\t{row.get('length', '')}\t{row.get('molecular_weight', '')}\t"
                f"{row.get('isoelectric_point', '')}\t{row.get('gravy', '')}\t"
                f"{row.get('aliphatic_index', '')}\t{row.get('instability_index', '')}\t"
                f"{row.get('instability_class', '')}\n"
            )

    json_path = analysis_dir / "protparam_results.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return full_tsv
