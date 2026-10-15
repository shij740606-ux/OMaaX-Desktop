"""Host-shared pacing for requests to the STRING API."""

from __future__ import annotations

import fcntl
import math
import os
import re
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Callable


DEFAULT_STRING_RATE_GATE = Path("/tmp/bbav5_string_rate_gate.timestamp")


class StringRateGateError(RuntimeError):
    """The shared gate cannot safely determine or record request pacing."""


def is_authoritative_string_https_url(value: object) -> bool:
    """Return whether a URL uses an unadorned authoritative STRING HTTPS host."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    labels_valid = all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    )
    authoritative_host = labels_valid and (
        hostname == "string-db.org"
        or (
            hostname.endswith(".string-db.org")
            and len(hostname) > len(".string-db.org")
        )
    )
    return bool(
        parsed.scheme.lower() == "https"
        and authoritative_host
        and parsed.username is None
        and parsed.password is None
        and port is None
    )


def string_https_request_identity(
    value: object,
) -> tuple[str, str, int, str] | None:
    """Normalize a STRING request URL to scheme, host, effective port, and path."""
    if not is_authoritative_string_https_url(value):
        return None
    parsed = urllib.parse.urlsplit(str(value).strip())
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or 443,
        parsed.path or "/",
    )


class StringRateGate:
    """Serialize STRING requests across processes and enforce a minimum interval."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        configured_path = path or os.environ.get("BBAV5_STRING_RATE_GATE")
        self.path = Path(configured_path or DEFAULT_STRING_RATE_GATE)
        if (
            isinstance(min_interval, bool)
            or not isinstance(min_interval, (int, float))
            or not math.isfinite(min_interval)
        ):
            raise ValueError("min_interval must be a finite number")
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")
        self.min_interval = float(min_interval)
        self._clock = clock
        self._sleeper = sleeper

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.lock")

    def wait(self) -> None:
        """Wait until this process owns the next host-wide request slot."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                previous = self._read_timestamp()
                now = self._finite_clock_value(self._clock())
                if previous is not None:
                    remaining = self.min_interval - (now - previous)
                    if remaining > 0:
                        self._sleeper(remaining)
                        now = self._finite_clock_value(self._clock())
                self._write_timestamp(now)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _read_timestamp(self) -> float | None:
        try:
            value = self.path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise StringRateGateError(
                f"Cannot read STRING rate-gate timestamp: {exc}"
            ) from exc
        if not value:
            raise StringRateGateError("STRING rate-gate timestamp is empty")
        try:
            timestamp = float(value)
        except ValueError as exc:
            raise StringRateGateError(
                f"STRING rate-gate timestamp is corrupt: {value!r}"
            ) from exc
        if not math.isfinite(timestamp):
            raise StringRateGateError("STRING rate-gate timestamp is non-finite")
        return timestamp

    @staticmethod
    def _finite_clock_value(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StringRateGateError("STRING rate-gate clock must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise StringRateGateError("STRING rate-gate clock is non-finite")
        return numeric

    def _write_timestamp(self, value: float) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                handle.write(f"{value:.9f}\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary_name, self.path)
            except OSError as exc:
                raise StringRateGateError(
                    f"Cannot write STRING rate-gate timestamp: {exc}"
                ) from exc
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
