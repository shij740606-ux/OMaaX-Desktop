#!/usr/bin/env python3
"""Representative import and behavior smoke tests for hardened builds."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


PLANTAI_PROBE = r"""
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
module = importlib.import_module("gfa_core.mas.core.orchestrator")

class Dummy:
    def __init__(self, name="dummy"):
        self.name = name
    def __getattr__(self, name):
        return Dummy(f"{self.name}.{name}")
    def __call__(self, *args, **kwargs):
        return Dummy(f"{self.name}()")

instance = module.Orchestrator(Dummy("llm"), Dummy("memory"), None)
parsed = instance._parse_intent_from_query(
    "identify the WRKY gene family in Arabidopsis"
)
route = instance._route_query({"query": "What is WRKY?"})
expected = {
    "gene_family": "WRKY",
    "species": "At",
    "stage": "identify",
    "task_type": "analysis",
}
assert parsed == expected, (parsed, expected)
assert route == "knowledge", route
print("@@RESULT@@" + json.dumps({
    "module": module.__name__,
    "parse_intent": parsed,
    "route": route,
}))
"""


RAG_PROBE = r"""
import importlib
import json
import sys

root = sys.argv[1]
sys.path.insert(0, root)
sys.path.insert(0, root + "/src")
module = importlib.import_module("src.rag.retriever")
instance = module.RAGRetriever()
assert instance.count_chunks() == 0
assert instance.get_context([]) == ""
assert instance.index_documents([]) == 0
api_module = importlib.import_module("api.app")
route_paths = {route.path for route in api_module.app.routes}
assert "/health" in route_paths
assert "/api/v1/methodology/extract-jobs" in route_paths
print("@@RESULT@@" + json.dumps({
    "module": module.__name__,
    "api_module": api_module.__name__,
    "methodology_route": True,
    "count_chunks": 0,
    "empty_context": "",
    "empty_index_count": 0,
}))
"""

QA_PROBE = r"""
import importlib
import json
import sys

root = sys.argv[1]
sys.path.insert(0, root)
module = importlib.import_module("scripts.qa_v1_1_73_server")
route_paths = {route.path for route in module.app.routes}
assert "/health" in route_paths
assert "/api/v1/qa-v1-1-73/health" in route_paths
assert "/api/v1/qa-v1-1-73/info" in route_paths
assert "/api/v1/qa-v1-1-73/chat" in route_paths
print("@@RESULT@@" + json.dumps({
    "module": module.__name__,
    "health_route": True,
    "info_route": True,
    "chat_route": True,
}))
"""


def run_probe(python: str, code: str, root: Path, timeout: int) -> dict:
    environment = os.environ.copy()
    completed = subprocess.run(
        [python, "-B", "-s", "-c", code, str(root)],
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    result = None
    for line in completed.stdout.splitlines():
        if line.startswith("@@RESULT@@"):
            result = json.loads(line[len("@@RESULT@@") :])
    if completed.returncode != 0 or result is None:
        raise RuntimeError(
            f"probe failed rc={completed.returncode}\n"
            f"stdout={completed.stdout[-3000:]}\n"
            f"stderr={completed.stderr[-3000:]}"
        )
    result["stdout_tail"] = completed.stdout[-1000:]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plantai", type=Path)
    parser.add_argument("--rag", type=Path)
    parser.add_argument("--qa", type=Path)
    parser.add_argument("--python")
    parser.add_argument("--qa-python")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.plantai is None and args.rag is None and args.qa is None:
        parser.error("at least one of --plantai, --rag or --qa is required")
    if (args.plantai is not None or args.rag is not None) and not args.python:
        parser.error("--python is required for PlantAI/RAG probes")
    if args.qa is not None and not (args.qa_python or args.python):
        parser.error("--qa-python or --python is required for the QA probe")

    payload = {}
    if args.plantai is not None:
        payload["plantai"] = run_probe(
            args.python, PLANTAI_PROBE, args.plantai.resolve(), args.timeout
        )
    if args.rag is not None:
        payload["rag"] = run_probe(
            args.python, RAG_PROBE, args.rag.resolve(), args.timeout
        )
    if args.qa is not None:
        payload["qa"] = run_probe(
            args.qa_python or args.python,
            QA_PROBE,
            args.qa.resolve(),
            args.timeout,
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
