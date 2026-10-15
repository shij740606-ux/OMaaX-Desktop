#!/usr/bin/env python3
"""Prefix target/reference GFF3 IDs so they match joint-tree tip labels."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_attrs(attr_text: str) -> list[tuple[str, str | None]]:
    attrs: list[tuple[str, str | None]] = []
    for item in attr_text.split(";"):
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            attrs.append((key, value))
        else:
            attrs.append((item, None))
    return attrs


def format_attrs(attrs: list[tuple[str, str | None]]) -> str:
    return ";".join(key if value is None else f"{key}={value}" for key, value in attrs)


def at_locus(value: str) -> str:
    match = re.match(r"^(AT[1-5CM]G\d+)", value, flags=re.IGNORECASE)
    return match.group(1).upper() if match else value


def transform_value(value: str, species: str, feature: str, key: str) -> str:
    if species == "At":
        if key in {"ID", "Parent", "Name"} and (feature in {"mRNA", "transcript"} or key == "Parent"):
            return f"At|{at_locus(value)}"
        if key in {"ID", "Name"} and feature == "gene":
            return f"At|{at_locus(value)}"
        return f"At|{value}" if key == "ID" else value

    if species == "Ft":
        if key in {"ID", "Parent", "Name"}:
            return f"Ft|{value}"
        return value

    return value


def rewrite_gff(infile: Path, species: str, out_handle) -> int:
    count = 0
    with infile.open(encoding="utf-8") as handle:
        for raw in handle:
            if raw.startswith("#") or not raw.strip():
                continue
            parts = raw.rstrip("\n").split("\t")
            if len(parts) != 9:
                continue
            parts[0] = f"{species}_{parts[0]}"
            feature = parts[2]
            attrs = []
            for key, value in parse_attrs(parts[8]):
                if value is None:
                    attrs.append((key, value))
                else:
                    attrs.append((key, transform_value(value, species, feature, key)))
            parts[8] = format_attrs(attrs)
            out_handle.write("\t".join(parts) + "\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ft-gff", required=True, type=Path)
    parser.add_argument("--at-gff", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        ft_count = rewrite_gff(args.ft_gff, "Ft", out)
        at_count = rewrite_gff(args.at_gff, "At", out)
    print(f"ft_lines={ft_count}")
    print(f"at_lines={at_count}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
