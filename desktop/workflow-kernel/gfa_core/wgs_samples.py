"""Strict parsing of confirmed paired-end WGS sample manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


_SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FASTQ_SUFFIXES = (".fastq", ".fastq.gz", ".fq", ".fq.gz")
_FORBIDDEN_RECORD_KEYS = {"include", "exclude", "group_role", "read1", "read2"}


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _DuplicateKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class WgsSample:
    sample_id: str
    r1: Path
    r2: Path


def _read_path(value: object, *, field: str, yaml_parent: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        path = yaml_parent / path
    if not path.name.endswith(_FASTQ_SUFFIXES):
        raise ValueError(f"{field} has an unsupported FASTQ suffix")
    if not path.is_file():
        raise ValueError(f"{field} must be an existing file")
    if path.stat().st_size == 0:
        raise ValueError(f"{field} must be non-empty")
    return path


def load_wgs_samples(path: str | Path) -> tuple[WgsSample, ...]:
    """Load explicitly listed, valid paired-end samples from a YAML document."""
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_DuplicateKeySafeLoader)
    if not isinstance(data, dict) or not isinstance(data.get("samples"), dict) or not data["samples"]:
        raise ValueError("wgs_samples.yaml must contain a non-empty samples mapping")
    if set(data) != {"samples"}:
        raise ValueError("wgs_samples.yaml accepts only the samples mapping")

    samples: list[WgsSample] = []
    for raw_id, record in data["samples"].items():
        if not isinstance(raw_id, str) or not _SAMPLE_ID.fullmatch(raw_id):
            raise ValueError("sample ID is unsafe")
        if not isinstance(record, dict):
            raise ValueError(f"sample {raw_id} must be a dictionary")
        forbidden = _FORBIDDEN_RECORD_KEYS.intersection(record)
        if forbidden:
            raise ValueError(f"sample {raw_id} contains prohibited field {sorted(forbidden)[0]}")
        if set(record) != {"r1", "r2"}:
            raise ValueError(f"sample {raw_id} must contain only r1 and r2")
        r1 = _read_path(record["r1"], field=f"sample {raw_id} r1", yaml_parent=config_path.parent)
        r2 = _read_path(record["r2"], field=f"sample {raw_id} r2", yaml_parent=config_path.parent)
        if r1 == r2:
            raise ValueError(f"sample {raw_id} r1 and r2 must be different files")
        samples.append(WgsSample(raw_id, r1, r2))
    return tuple(sorted(samples, key=lambda sample: sample.sample_id))
