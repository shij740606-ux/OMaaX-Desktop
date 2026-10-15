#!/usr/bin/env python3
"""Copy selected Ollama manifests and their referenced blobs."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def model_manifest(source: Path, model: str) -> Path:
    repository, sep, tag = model.partition(":")
    if not sep:
        tag = "latest"
    return source / "manifests" / "registry.ollama.ai" / "library" / repository / tag


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    copied_blobs: set[str] = set()
    summary: list[dict[str, object]] = []

    for model in args.model:
        manifest = model_manifest(source, model)
        if not manifest.is_file():
            raise SystemExit(f"Ollama model manifest missing: {model}: {manifest}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        digests = [payload["config"]["digest"]]
        digests.extend(layer["digest"] for layer in payload.get("layers", []))

        if not args.check_only:
            destination = output / manifest.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest, destination)

        model_bytes = 0
        for digest in digests:
            if not digest.startswith("sha256:"):
                raise SystemExit(f"Unsupported Ollama digest for {model}: {digest}")
            blob_name = digest.replace(":", "-", 1)
            blob_source = source / "blobs" / blob_name
            if not blob_source.is_file():
                raise SystemExit(f"Ollama blob missing for {model}: {blob_source}")
            model_bytes += blob_source.stat().st_size
            if args.check_only or blob_name in copied_blobs:
                continue
            blob_output = output / "blobs" / blob_name
            blob_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob_source, blob_output)
            copied_blobs.add(blob_name)
        summary.append({"model": model, "bytes": model_bytes, "blobs": len(digests)})

    if not args.check_only:
        (output / "DELIVERED_MODELS.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
