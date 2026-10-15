"""Utility-task catalog separate from formal analysis pipelines.

Utility tasks are TBtools-like one-off operations for file preparation,
sequence extraction, ID cleanup and external sequence fetching. They remain
executable, but they must not be expanded into gene-family or RNA-seq pipeline
dependencies from natural-language planning.
"""
from __future__ import annotations

from typing import Any


UTILITY_TASKS: dict[str, dict[str, Any]] = {
    "fasta_header_clean": {
        "stage": "sequence_extract_by_id",
        "name": "清理FASTA序列ID/header",
        "description": "Remove long FASTA header annotations and keep the first ID token.",
    },
    "sequence_extract_by_id": {
        "stage": "sequence_extract_by_id",
        "name": "按ID从FASTA提取序列",
        "description": "Extract protein/CDS/FASTA records from local files using an ID list or inline IDs.",
    },
    "ncbi_fetch_sequences": {
        "stage": "ncbi_fetch_sequences",
        "name": "NCBI序列下载",
        "description": "Fetch protein/CDS sequences from NCBI by GeneID, gene symbol, locus tag or ID list.",
    },
    "prepare_organize": {
        "stage": "prepare_organize",
        "name": "整理输入文件",
        "description": "Organize uploaded files by config.xlsx without starting a gene-family pipeline.",
    },
    "prepare_normalize_gff": {
        "stage": "prepare_normalize_gff",
        "name": "规范化GFF ID",
        "description": "Normalize GFF/GTF attributes and IDs as a standalone file-preparation task.",
    },
    "prepare_longest_transcript": {
        "stage": "prepare_longest_transcript",
        "name": "提取最长转录本",
        "description": "Select the longest transcript/CDS per gene as a standalone preparation task.",
    },
    "prepare_extract_pep": {
        "stage": "prepare_extract_pep",
        "name": "从Genome+GFF提取全蛋白组",
        "description": "Run gffread peptide extraction from genome FASTA and GFF/GTF.",
    },
    "prepare_extract_cds": {
        "stage": "prepare_extract_cds",
        "name": "从Genome+GFF提取全CDS",
        "description": "Run gffread CDS extraction from genome FASTA and GFF/GTF.",
    },
    "prepare_filter_fasta": {
        "stage": "prepare_filter_fasta",
        "name": "按ID筛选FASTA",
        "description": "Filter FASTA records using an ID list as a standalone task.",
    },
    "prepare_simplify_ncbi": {
        "stage": "prepare_simplify_ncbi",
        "name": "简化NCBI注释/序列ID",
        "description": "Simplify NCBI GFF attributes and normalize NCBI CDS headers.",
    },
}


STAGE_TO_DEFAULT_TOOL = {
    value["stage"]: key for key, value in UTILITY_TASKS.items()
}
STAGE_TO_DEFAULT_TOOL["sequence_extract_by_id"] = "sequence_extract_by_id"


def utility_tool_for_stage(stage: str, params: dict[str, Any] | None = None) -> str:
    params = params or {}
    if stage == "sequence_extract_by_id" and params.get("sequenceExtractCleanHeaders"):
        return "fasta_header_clean"
    return STAGE_TO_DEFAULT_TOOL.get(stage, stage)


def utility_display_name(stage: str, params: dict[str, Any] | None = None) -> str:
    tool = utility_tool_for_stage(stage, params)
    return UTILITY_TASKS.get(tool, {}).get("name", stage)

