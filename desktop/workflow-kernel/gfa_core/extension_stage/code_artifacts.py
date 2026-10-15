"""Persistence for API-generated extension wrapper code."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class ExtensionCodeArtifactError(RuntimeError):
    """Raised when API-generated code artifacts cannot be saved safely."""


def save_code_artifacts(spec: dict[str, Any], converted_code_dir: Path) -> list[dict[str, Any]]:
    artifacts = spec.get("code_artifacts") or spec.get("generated_code_artifacts") or []
    if not artifacts:
        raise ExtensionCodeArtifactError("API conversion did not return code_artifacts")
    converted_code_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ExtensionCodeArtifactError(f"code_artifacts[{index}] must be an object")
        rel = _safe_relative_path(str(artifact.get("path") or artifact.get("filename") or f"generated_{index}.sh"))
        content = _artifact_content(artifact, index)
        path = converted_code_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        _lint_code_artifact(path, rel.as_posix(), content)
        if path.suffix.lower() in {".sh", ".py", ".pl", ".r", ".R"}:
            current = path.stat().st_mode
            os.chmod(path, current | 0o755)
        saved.append({
            "path": rel.as_posix(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "role": artifact.get("role", "wrapper"),
        })
    return saved


def _artifact_content(artifact: dict[str, Any], index: int) -> bytes:
    if artifact.get("content_base64"):
        try:
            return base64.b64decode(str(artifact["content_base64"]), validate=True)
        except Exception as exc:
            raise ExtensionCodeArtifactError(f"code_artifacts[{index}] content_base64 is invalid: {exc}") from exc
    if artifact.get("content_lines") is not None:
        lines = artifact["content_lines"]
        if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
            raise ExtensionCodeArtifactError(f"code_artifacts[{index}] content_lines must be a list of strings")
        return ("\n".join(lines) + "\n").encode("utf-8")
    if artifact.get("content") is not None:
        return str(artifact["content"]).encode("utf-8")
    raise ExtensionCodeArtifactError(f"code_artifacts[{index}] missing content_base64, content_lines, or content")


def _safe_relative_path(value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise ExtensionCodeArtifactError(f"unsafe code artifact path: {value}")
    parts = [part for part in raw.parts if part not in {"", "."}]
    if not parts:
        raise ExtensionCodeArtifactError("empty code artifact path")
    cleaned = Path(*parts)
    if cleaned.as_posix().startswith("/"):
        raise ExtensionCodeArtifactError(f"unsafe code artifact path: {value}")
    return cleaned



def _lint_code_artifact(path: Path, name: str, content: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix == ".sh":
        _lint_shell_wrapper(content.decode("utf-8", errors="ignore"), name)
    elif suffix == ".py":
        _lint_python(path, name)
    elif suffix in {".r"}:
        _lint_r(path, name)
    elif suffix == ".pl":
        _lint_perl(path, name)


def _lint_python(path: Path, name: str) -> None:
    proc = subprocess.run([os.sys.executable, "-m", "py_compile", str(path)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ExtensionCodeArtifactError(f"Generated Python artifact {name} failed py_compile: {_preview_message(proc.stderr or proc.stdout)}")


def _lint_r(path: Path, name: str) -> None:
    rscript = shutil.which("Rscript")
    if not Path(rscript).exists() and shutil.which("Rscript") is None:
        raise ExtensionCodeArtifactError(f"Generated R artifact {name} cannot be syntax-checked because Rscript is unavailable")
    proc = subprocess.run([rscript, "-e", f"parse(file={str(path)!r})"], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ExtensionCodeArtifactError(f"Generated R artifact {name} failed parse check: {_preview_message(proc.stderr or proc.stdout)}")


def _lint_perl(path: Path, name: str) -> None:
    perl = shutil.which("perl")
    if perl is None:
        raise ExtensionCodeArtifactError(f"Generated Perl artifact {name} cannot be syntax-checked because perl is unavailable")
    proc = subprocess.run([perl, "-c", str(path)], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise ExtensionCodeArtifactError(f"Generated Perl artifact {name} failed perl -c: {_preview_message(proc.stderr or proc.stdout)}")


def _lint_common_shell_runtime_errors(text: str, name: str) -> None:
    import re

    checks = [
        (r"(?m)^\s*(?:while|if|elif)\s+\[\s+\[\s+", "mixed single and double bracket test, use '[ ... ]' or '[[ ... ]]'"),
        (r"(?m)^\s*(?:while|if|elif)\s+\[\[.*\]\s*(?:;?\s*(?:do|then))", "double-bracket test closed with a single ']'"),
        (r"(?m)^\s*(?:while|if|elif)\s+\[\s+.*\]\]\s*(?:;?\s*(?:do|then))", "single-bracket test closed with ']]'"),
        (r"(?m)^\s*while\s+\[\s+\[\s+\$#\s+-gt\s+0\s+\]\]", "malformed argv loop; use while [ \"$#\" -gt 0 ]; do or while [[ $# -gt 0 ]]; do"),
    ]
    for pattern, reason in checks:
        match = re.search(pattern, text)
        if match:
            line_no = text[:match.start()].count("\n") + 1
            raise ExtensionCodeArtifactError(f"Generated shell wrapper {name} has malformed shell test at line {line_no}: {reason}")


def _smoke_shell_wrapper_no_args(path: str, name: str) -> None:
    with tempfile.TemporaryDirectory(prefix="bbav5_wrapper_smoke_") as temp_dir:
        proc = subprocess.run(["bash", path], cwd=temp_dir, capture_output=True, text=True, timeout=5)
    message = "\n".join(part for part in [proc.stderr, proc.stdout] if part).strip()
    lower = message.lower()
    runtime_syntax_tokens = [
        "missing `]'",
        "missing ']'",
        "缺少 \"]\"",
        "unary operator expected",
        "binary operator expected",
        "syntax error",
        "unexpected token",
        "未预期的记号",
        "bad substitution",
    ]
    if any(token in lower for token in runtime_syntax_tokens) or "缺少 \"]\"" in message:
        raise ExtensionCodeArtifactError(f"Generated shell wrapper {name} failed no-arg smoke test: {_preview_message(message)}")


def _preview_message(text: str, limit: int = 500) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."

def _lint_shell_wrapper(text: str, name: str) -> None:
    import re

    _lint_common_shell_runtime_errors(text, name)
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        temp_name = handle.name
    try:
        proc = subprocess.run(["bash", "-n", temp_name], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise ExtensionCodeArtifactError(f"Generated shell wrapper {name} failed bash -n: {(proc.stderr or proc.stdout).strip()}")
        _smoke_shell_wrapper_no_args(temp_name, name)
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass

    literal_expansions = sorted(set(re.findall(r"(?<!\$)\{([A-Z][A-Z0-9_]*:[-=?+][^}]*)\}", text)))
    if literal_expansions:
        raise ExtensionCodeArtifactError(
            f"Generated shell wrapper {name} contains shell parameter expansion without $: "
            + ", ".join("{" + item + "}" for item in literal_expansions)
        )
    assigned = set(re.findall(r"(?:^|[;\s])(?:local\s+|export\s+)?([A-Z][A-Z0-9_]{2,})=", text, flags=re.M))
    used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]{2,})(?:[}:])?", text))
    allowed = {
        "HOME", "PATH", "PWD", "OLDPWD", "TMPDIR", "SHELL", "USER", "HOSTNAME",
        "OPTARG", "OPTIND", "RANDOM", "SECONDS", "LINENO", "BASH_SOURCE", "PIPESTATUS",
    }
    undefined = sorted(var for var in used if var not in assigned and var not in allowed)
    if undefined:
        raise ExtensionCodeArtifactError(
            f"Generated shell wrapper {name} uses undefined uppercase variable(s): " + ", ".join(undefined)
        )
