#!/usr/bin/env python3
"""Audit a compiled tree and bind it to a tamper-evident build marker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import sysconfig
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elf_visibility import summarize  # noqa: E402


MARKER_NAME = ".hardened-build.json"
TREE_DIGEST_POLICY = "immutable-runtime-v2"
MUTABLE_TOP_LEVEL_DIRS = {
    "cost_logs",
    "data",
    "input",
    "logs",
    "memory_storage",
    "neo4j",
    "output",
    "outputs",
    "rag_kb_store",
    "service_data",
    "temp",
    "v15_output",
    "workspace",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_digest_excluded(relative: str) -> bool:
    parts = Path(relative).parts
    return (
        relative == MARKER_NAME
        or not parts
        or parts[0] in MUTABLE_TOP_LEVEL_DIRS
        or "__pycache__" in parts
        or relative.endswith((".pyc", ".pyo"))
    )


def tree_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if is_digest_excluded(relative):
            continue
        count += 1
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_sha256(path)))
        digest.update(b"\0")
    return digest.hexdigest(), count


def audit_tree(root: Path, project: str) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    extensions = sorted(root.rglob("*.so"))
    expected_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    hidden_non_pyinit = 0

    if not extensions:
        errors.append({"kind": "no_extensions"})

    for extension in extensions:
        relative = extension.relative_to(root).as_posix()
        if not extension.name.endswith(expected_suffix):
            errors.append(
                {
                    "kind": "python_abi_suffix_mismatch",
                    "file": relative,
                    "expected_suffix": expected_suffix,
                }
            )
        try:
            info = summarize(extension, extension.read_bytes())
        except BaseException as exc:
            errors.append(
                {
                    "kind": "elf_parse_failed",
                    "file": relative,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if info["has_symtab"]:
            errors.append({"kind": "symtab_present", "file": relative})
        if len(info["pyinit"]) != 1 or not info["all_pyinit_default"]:
            errors.append(
                {
                    "kind": "invalid_pyinit",
                    "file": relative,
                    "pyinit": info["pyinit"],
                }
            )
        if not info["all_non_pyinit_hidden"]:
            errors.append(
                {
                    "kind": "visible_non_pyinit",
                    "file": relative,
                    "symbols": info["non_pyinit"],
                }
            )
        hidden_non_pyinit += len(info["non_pyinit"])

    c_files = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.c")
        if path.is_file()
    ]
    if c_files:
        errors.append({"kind": "generated_c_retained", "files": c_files})

    sensitive = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        lowered = path.name.lower()
        if (
            lowered.endswith(".key")
            or "private_key" in lowered
            or "master_key" in lowered
            or lowered == "private_key.pass"
        ):
            sensitive.append(path.relative_to(root).as_posix())
    if sensitive:
        errors.append({"kind": "sensitive_filename", "files": sensitive})

    digest, file_count = tree_digest(root)
    return {
        "project": project,
        "tree_digest_policy": TREE_DIGEST_POLICY,
        "python_version": sys.version.split()[0],
        "extension_suffix": expected_suffix,
        "extension_count": len(extensions),
        "hidden_non_pyinit_count": hidden_non_pyinit,
        "file_count_without_marker": file_count,
        "tree_sha256": digest,
        "error_count": len(errors),
        "errors": errors,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o640)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--project", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-marker", action="store_true")
    mode.add_argument("--require-marker", action="store_true")
    args = parser.parse_args()

    root = args.tree.resolve()
    if not root.is_dir():
        raise SystemExit(f"Compiled tree missing: {root}")

    report = audit_tree(root, args.project)
    marker_path = root / MARKER_NAME

    if args.require_marker:
        if not marker_path.is_file():
            report["errors"].append({"kind": "marker_missing"})
        else:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            report["marker"] = marker
            for field in (
                "project",
                "tree_digest_policy",
                "python_version",
                "extension_suffix",
                "extension_count",
                "tree_sha256",
            ):
                if marker.get(field) != report.get(field):
                    report["errors"].append(
                        {
                            "kind": "marker_mismatch",
                            "field": field,
                            "expected": marker.get(field),
                            "actual": report.get(field),
                        }
                    )

    report["error_count"] = len(report["errors"])
    report["ok"] = report["error_count"] == 0

    if args.write_marker and report["ok"]:
        marker = {
            key: report[key]
            for key in (
                "project",
                "tree_digest_policy",
                "python_version",
                "extension_suffix",
                "extension_count",
                "hidden_non_pyinit_count",
                "file_count_without_marker",
                "tree_sha256",
            )
        }
        marker["created_at"] = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )
        atomic_write_json(marker_path, marker)
        report["marker_path"] = str(marker_path)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
