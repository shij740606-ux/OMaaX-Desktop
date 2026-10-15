#!/usr/bin/env python3
"""Expose pysam's bundled bcftools commands as a small executable wrapper."""

from __future__ import annotations

import sys

import pysam
import pysam.bcftools as bcftools
from pysam.utils import SamtoolsError


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["-v"]):
        print(f"pysam-bcftools {pysam.__version__} (HTSlib {pysam.__samtools_version__})")
        return 0
    if not args or args[0].startswith("-"):
        print("usage: pysam_bcftools.py COMMAND [ARGS...]", file=sys.stderr)
        return 2
    command = args.pop(0).replace("-", "_")
    runner = getattr(bcftools, command, None)
    if runner is None:
        print(f"unsupported bcftools command: {command}", file=sys.stderr)
        return 2
    try:
        output = runner(*args)
    except SamtoolsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if output:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
