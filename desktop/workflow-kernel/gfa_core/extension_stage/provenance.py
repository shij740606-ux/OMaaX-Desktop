"""Provenance records for extension-stage build and run workflows."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now().isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_files(root: Path, *, relative_to: Path | None = None) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rel_root = relative_to or root
    files: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        files.append({
            "path": _rel(path, rel_root),
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
            "mtime": path.stat().st_mtime,
        })
    return files


def write_api_attempt(root: Path, record: dict[str, Any]) -> str:
    attempts_dir = root / "build" / "api_attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    attempt = int(record.get("attempt") or 0)
    phase = str(record.get("phase") or "build")
    path = attempts_dir / f"{phase}_attempt_{attempt:02d}.json"
    payload = {"created_at": now(), **record}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return _rel(path, root)


def write_build_record(root: Path, record: dict[str, Any]) -> str:
    path = root / "extension_build.json"
    payload = {"updated_at": now(), **record}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return _rel(path, root)


def write_run_record(run_dir: Path, run: dict[str, Any]) -> str:
    path = run_dir / "extension_run.json"
    payload = dict(run)
    payload.setdefault("recorded_at", now())
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return _rel(path, run_dir)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
