"""Shared repair lesson storage for extension stages."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().isoformat()


def lesson_path(store_root: Path) -> Path:
    root = Path(store_root) / "_repair_lessons"
    root.mkdir(parents=True, exist_ok=True)
    return root / "extension_repair_lessons.jsonl"


def load_recent_lessons(store_root: Path, limit: int = 12) -> list[dict[str, Any]]:
    path = lesson_path(store_root)
    if not path.exists():
        return []
    lessons: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        lessons.append(item)
    return lessons


def append_lesson(store_root: Path, extension: dict[str, Any], lesson: dict[str, Any]) -> None:
    event = {
        "created_at": _now(),
        "extension_id": extension.get("extension_id", ""),
        "extension_name": extension.get("name", ""),
        "display_name": extension.get("display_name", ""),
        **lesson,
    }
    path = lesson_path(store_root)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
