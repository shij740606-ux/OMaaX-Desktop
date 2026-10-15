#!/usr/bin/env python3
"""Shared conda environment path resolution for RNA-seq CLI stages."""

from __future__ import annotations

import os
from pathlib import Path


# 镜像标准形态: 环境前缀默认由当前解释器推导(sys.executable/../..),
# PLANTAI_CONDA_BASE / PLANTAI_*_BIN / PLANTAI_*_ENV 为既有环境变量覆盖点。
DEFAULT_CONDA_BASE = Path(__file__).resolve().parents[1]


def conda_base() -> Path:
    return Path(os.environ.get("PLANTAI_CONDA_BASE", str(DEFAULT_CONDA_BASE))).expanduser()


def env_bin(env_name: str, bin_override_var: str, env_override_var: str) -> Path:
    explicit_bin = os.environ.get(bin_override_var)
    if explicit_bin:
        return Path(explicit_bin).expanduser()
    explicit_env = os.environ.get(env_override_var)
    if explicit_env:
        return Path(explicit_env).expanduser() / "bin"
    return conda_base() / "envs" / env_name / "bin"


def prepend_path(bin_dir: str | Path) -> None:
    bin_text = str(Path(bin_dir).expanduser())
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if bin_text not in parts:
        os.environ["PATH"] = bin_text + os.pathsep + os.environ.get("PATH", "")


def rnaseq_bin() -> Path:
    return env_bin("rnaseq", "PLANTAI_RNASEQ_BIN", "PLANTAI_RNASEQ_ENV")


def rnaseq_tool(tool: str) -> str:
    return str(rnaseq_bin() / tool)


def rnaseq_rscript() -> str:
    return rnaseq_tool("Rscript")


def rnar_bin() -> Path:
    return env_bin("rnaR", "PLANTAI_RNAR_BIN", "PLANTAI_RNAR_ENV")


def rnar_rscript() -> str:
    return str(rnar_bin() / "Rscript")


def capped_threads(value: int | str | None, default: int = 12, cap: int = 12) -> int:
    try:
        threads = int(value) if value is not None else int(default)
    except (TypeError, ValueError):
        threads = int(default)
    return max(1, min(int(cap), threads))
