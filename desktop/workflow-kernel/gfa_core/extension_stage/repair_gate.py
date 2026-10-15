"""Contract regression gate for API repair candidates."""

from __future__ import annotations

from typing import Any

from gfa_core.extension_stage.spec_v2 import ExtensionSpecV2Error, contract_regressions, normalize_spec_v2


class ExtensionRepairGateError(RuntimeError):
    """Raised when an API repair candidate weakens the extension contract."""


def validate_repair_candidate(before: dict[str, Any], candidate_spec: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = normalize_spec_v2(candidate_spec, require_code_artifacts=False)
    except ExtensionSpecV2Error as exc:
        raise ExtensionRepairGateError(str(exc)) from exc
    errors = contract_regressions(before, normalized)
    if errors:
        raise ExtensionRepairGateError("repair candidate regressed contract: " + "; ".join(errors))
    return normalized
