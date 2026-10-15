"""Shared local path resolution for the PlantAI/bbav5 runtime."""

from __future__ import annotations

import os
from pathlib import Path


def _load_local_env(default_root: Path) -> None:
    env_path = default_root / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_project_root() -> Path:
    default_root = Path(__file__).resolve().parents[1]
    _load_local_env(default_root)
    configured = os.environ.get("PLANTAI_PROJECT_ROOT") or os.environ.get("BBAV5_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_root


def get_conda_base() -> Path:
    configured = os.environ.get("BBAV5_CONDA_BASE") or os.environ.get("CONDA_BASE")
    if configured:
        return Path(configured).expanduser().resolve()
    prefix = Path(os.environ.get("CONDA_PREFIX", os.path.expanduser("~/miniconda3"))).expanduser().resolve()
    return prefix.parent.parent if prefix.parent.name == "envs" else prefix


def get_conda_env_bin(env_name: str) -> Path:
    """Return the bin directory for a conda environment, e.g. get_conda_env_bin('test4py311') -> /path/to/miniconda3/envs/test4py311/bin"""
    return get_conda_base() / "envs" / env_name / "bin"


def get_conda_env_python(env_name: str) -> Path:
    """Return the python executable for a conda environment."""
    return get_conda_env_bin(env_name) / "python"


def get_conda_env_rscript(env_name: str) -> Path:
    """Return the Rscript executable for a conda environment."""
    return get_conda_env_bin(env_name) / "Rscript"


def get_configured_path(name: str, default: str = "") -> str:
    """Return one deployment path supplied by the loaded project environment."""
    return str(os.environ.get(name) or default).strip()


def get_wgs_samtools(stage: str, requested: str = "") -> str:
    """Resolve the stage-authoritative samtools executable."""
    if str(stage or "") == "wgs_bam_qc":
        rnaseq_env = get_configured_path("BBAV5_RNASEQ_ENV", "rnaseq")
        return str(get_conda_env_bin(rnaseq_env) / "samtools")
    return str(requested or (get_conda_env_bin("ASAW") / "samtools"))


def get_wgs_java(requested: str = "") -> str:
    """Resolve the Java executable used by Beagle imputation."""
    return str(
        requested
        or get_configured_path("WGS_JAVA")
        or (get_conda_env_bin("test4py311") / "java")
    )


def get_wgs_bcftools(requested: str = "") -> str:
    """Resolve the CPU-compatible bcftools executable for WGS stages."""
    return str(
        requested
        or get_configured_path("WGS_BCFTOOLS")
        or (get_conda_env_bin("ASAW") / "bcftools")
    )


PROJECT_ROOT = get_project_root()
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
INPUT_ROOT = PROJECT_ROOT / "input"
MEMORY_ROOT = PROJECT_ROOT / "memory_storage"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CONDA_BASE = get_conda_base()
