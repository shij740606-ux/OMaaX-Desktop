"""Lightweight extension analysis stage support."""

from .manager import (
    ExtensionStageError,
    ExtensionStageManager,
)
from .agent import ExtensionStageAgent

__all__ = ["ExtensionStageError", "ExtensionStageManager", "ExtensionStageAgent"]
