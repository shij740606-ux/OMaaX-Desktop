"""Strict schema checks for API-generated extension-stage specs."""

from __future__ import annotations

from typing import Any

from gfa_core.extension_stage.spec_v2 import (
    PLACEHOLDERS as ALLOWED_PLACEHOLDERS,
    ExtensionSpecV2Error,
    normalize_input_roles,
    normalize_output_contract,
    normalize_parameters,
    normalize_spec_v2,
    safe_parameter_name,
    unsupported_placeholders,
)


class ExtensionSpecValidationError(RuntimeError):
    """Raised when an API-generated extension spec is not publishable."""


def validate_conversion_spec(spec: dict[str, Any], *, require_code_artifacts: bool = True) -> dict[str, Any]:
    try:
        return normalize_spec_v2(spec, require_code_artifacts=require_code_artifacts)
    except ExtensionSpecV2Error as exc:
        raise ExtensionSpecValidationError(str(exc)) from exc
