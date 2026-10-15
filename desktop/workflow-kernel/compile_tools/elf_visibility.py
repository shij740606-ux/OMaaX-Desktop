#!/usr/bin/env python3
"""Inspect or hide non-PyInit defined symbols in an ELF64 little-endian .dynsym."""

from __future__ import annotations

import argparse
import json
import os
import struct
import tempfile
from pathlib import Path
from typing import Any


STN_UNDEF = 0
STV_DEFAULT = 0
STV_HIDDEN = 2


def c_string(data: bytes | bytearray, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return bytes(data[offset:end]).decode("utf-8", errors="replace")


def parse_sections(data: bytes | bytearray) -> tuple[list[dict[str, Any]], bytes]:
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2:
        raise ValueError("only ELF64 is supported")
    if data[5] != 1:
        raise ValueError("only little-endian ELF is supported")

    section_offset = struct.unpack_from("<Q", data, 40)[0]
    section_entry_size = struct.unpack_from("<H", data, 58)[0]
    section_count = struct.unpack_from("<H", data, 60)[0]
    section_name_index = struct.unpack_from("<H", data, 62)[0]
    if section_entry_size < 64 or not section_count:
        raise ValueError("invalid or missing section table")

    sections = []
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        fields = struct.unpack_from("<IIQQQQIIQQ", data, offset)
        sections.append(
            {
                "index": index,
                "header_offset": offset,
                "name_offset": fields[0],
                "type": fields[1],
                "flags": fields[2],
                "address": fields[3],
                "offset": fields[4],
                "size": fields[5],
                "link": fields[6],
                "info": fields[7],
                "alignment": fields[8],
                "entry_size": fields[9],
            }
        )

    if section_name_index >= len(sections):
        raise ValueError("invalid section-name string-table index")
    names_section = sections[section_name_index]
    names = bytes(
        data[
            names_section["offset"] : names_section["offset"] + names_section["size"]
        ]
    )
    for section in sections:
        section["name"] = c_string(names, section["name_offset"])
    return sections, names


def inspect_dynsym(data: bytes | bytearray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sections, _ = parse_sections(data)
    dynsym = next((section for section in sections if section["name"] == ".dynsym"), None)
    if dynsym is None:
        raise ValueError("missing .dynsym")
    if dynsym["link"] >= len(sections):
        raise ValueError("invalid .dynsym string-table link")
    dynstr_section = sections[dynsym["link"]]
    dynstr = data[
        dynstr_section["offset"] : dynstr_section["offset"] + dynstr_section["size"]
    ]
    entry_size = dynsym["entry_size"] or 24
    if entry_size < 24:
        raise ValueError("invalid ELF64 symbol entry size")

    symbols = []
    count = dynsym["size"] // entry_size
    for index in range(count):
        offset = dynsym["offset"] + index * entry_size
        name_offset = struct.unpack_from("<I", data, offset)[0]
        info = data[offset + 4]
        other = data[offset + 5]
        section_index = struct.unpack_from("<H", data, offset + 6)[0]
        value, size = struct.unpack_from("<QQ", data, offset + 8)
        name = c_string(dynstr, name_offset) if name_offset < len(dynstr) else ""
        symbols.append(
            {
                "index": index,
                "entry_offset": offset,
                "name": name,
                "binding": info >> 4,
                "type": info & 0xF,
                "visibility": other & 0x3,
                "section_index": section_index,
                "value": value,
                "size": size,
                "defined": section_index != STN_UNDEF,
            }
        )
    return symbols, dynsym


def summarize(path: Path, data: bytes | bytearray) -> dict[str, Any]:
    sections, _ = parse_sections(data)
    symbols, _ = inspect_dynsym(data)
    defined = [symbol for symbol in symbols if symbol["defined"] and symbol["name"]]
    pyinit = [symbol for symbol in defined if symbol["name"].startswith("PyInit_")]
    non_pyinit = [
        symbol for symbol in defined if not symbol["name"].startswith("PyInit_")
    ]
    return {
        "path": str(path),
        "has_symtab": any(section["name"] == ".symtab" for section in sections),
        "defined_dynamic_count": len(defined),
        "pyinit": [
            {"name": symbol["name"], "visibility": symbol["visibility"]}
            for symbol in pyinit
        ],
        "non_pyinit": [
            {"name": symbol["name"], "visibility": symbol["visibility"]}
            for symbol in non_pyinit
        ],
        "all_non_pyinit_hidden": all(
            symbol["visibility"] == STV_HIDDEN for symbol in non_pyinit
        ),
        "all_pyinit_default": bool(pyinit)
        and all(symbol["visibility"] == STV_DEFAULT for symbol in pyinit),
    }


def hide_non_pyinit(path: Path) -> dict[str, Any]:
    original = path.read_bytes()
    data = bytearray(original)
    symbols, _ = inspect_dynsym(data)
    changed = []
    for symbol in symbols:
        if (
            symbol["defined"]
            and symbol["name"]
            and not symbol["name"].startswith("PyInit_")
            and symbol["visibility"] != STV_HIDDEN
        ):
            offset = symbol["entry_offset"] + 5
            data[offset] = (data[offset] & ~0x3) | STV_HIDDEN
            changed.append(symbol["name"])

    if changed:
        stat = path.stat()
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode)
        os.replace(temporary, path)

    result = summarize(path, data)
    result["changed"] = changed
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--hide-non-pyinit", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    path = args.elf.resolve()
    result = (
        hide_non_pyinit(path)
        if args.hide_non_pyinit
        else summarize(path, path.read_bytes())
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.check:
        valid = (
            not result["has_symtab"]
            and result["all_pyinit_default"]
            and result["all_non_pyinit_hidden"]
            and len(result["pyinit"]) == 1
        )
        return 0 if valid else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
