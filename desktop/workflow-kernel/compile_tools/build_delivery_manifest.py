#!/usr/bin/env python3
"""Write the customer-facing manifest for the three-project offline delivery."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def tree_bytes(path: Path) -> int:
    total = 0
    for directory, _, files in os.walk(path, followlinks=True):
        for filename in files:
            total += (Path(directory) / filename).stat().st_size
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--generation-model", required=True)
    parser.add_argument("--embed-model", required=True)
    args = parser.parse_args()

    root = args.runtime_root.resolve()
    assets = {
        "qdrant_storage": root / "qdrant" / "storage",
        "neo4j_data": root / "neo4j" / "data",
        "bge_reranker": root / "QA" / "models" / "bge-reranker-base",
        "ollama_models": root / "ollama-models",
    }
    missing = [str(path) for path in assets.values() if not path.is_dir()]
    if missing:
        raise SystemExit("Missing runtime asset directories: " + ", ".join(missing))

    payload = {
        "release_version": args.version,
        "runtime_data_archive": f"plantai-rag-runtime-data-{args.version}.tar",
        "applications": {
            "PlantAI": {"path": "app/PlantAI_compiled", "port": 7880},
            "RAG": {"path": "app/RAG", "port": 7900},
            "QA": {"path": "app/QA", "port": 7925},
        },
        "containers": {
            "qdrant": {
                "image": "qdrant/qdrant:latest",
                "http_port": 16333,
                "grpc_port": 16334,
                "data_path": "runtime-assets/qdrant/storage",
            },
            "neo4j": {
                "image": "neo4j:5.15.0-community",
                "http_port": 17474,
                "bolt_port": 17687,
                "data_path": "runtime-assets/neo4j/data",
            },
        },
        "models": {
            "ollama_generation": args.generation_model,
            "ollama_embedding": args.embed_model,
            "ollama_path": "runtime-assets/ollama-models",
            "reranker_path": "runtime-assets/QA/models/bge-reranker-base",
        },
        "asset_bytes": {name: tree_bytes(path) for name, path in assets.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["asset_bytes"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
