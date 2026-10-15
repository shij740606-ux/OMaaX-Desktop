#!/usr/bin/env python3
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def site_sequence(site):
    return "".join(ref.get("letter_id", "") for ref in site.findall("letter_ref"))


def parse_xml(xml_file, out):
    tree = ET.parse(xml_file)
    root = tree.getroot()
    seq_names = {
        seq.get("id"): seq.get("name")
        for seq in root.findall(".//sequence")
    }

    n_sites = 0
    with open(f"{out}.motif_seq.txt", "w") as o1, \
            open(f"{out}.motif_prot.bed", "w") as o2, \
            open(f"{out}.motif_prot.txt", "w") as o3:
        for motif in root.findall(".//motif"):
            motif_name = motif.get("name") or motif.get("id", "")
            meme = f"MEME-{motif_name}"
            for hit in motif.findall("./contributing_sites/contributing_site"):
                seq_name = seq_names.get(hit.get("sequence_id"), hit.get("sequence_id"))
                start0 = int(hit.get("position"))
                seq = site_sequence(hit.find("site"))
                start1 = start0 + 1
                end = start0 + len(seq)
                pvalue = hit.get("pvalue", "")
                o1.write(f"{meme}\t{seq_name}\n")
                o2.write(f"{seq_name}\t{start0}\t{end}\t{meme}\n")
                o3.write(f"{seq_name}\t{start1}\t{end}\t{seq}\t{meme}\t{pvalue}\n")
                n_sites += 1
    return n_sites


def parse_txt(meme_file, out):
    n_sites = 0
    with open(f"{out}.motif_seq.txt", "w") as o1, \
            open(f"{out}.motif_prot.bed", "w") as o2, \
            open(f"{out}.motif_prot.txt", "w") as o3, \
            open(meme_file, encoding="utf-8", errors="replace") as fh:
        it = iter(fh)
        for line in it:
            m = re.match(r"^\s*Motif\s+(\S+)(?:\s+\S+)?\s+sites sorted by position p-value", line.rstrip("\n"))
            if not m:
                continue
            meme = f"MEME-{m.group(1)}"
            for _ in range(3):
                next(it, "")
            for hit in it:
                hit = hit.rstrip("\n")
                if re.match(r"^-{5,}", hit):
                    break
                f = hit.split()
                if len(f) < 5:
                    continue
                start = int(f[1])
                seq = f[4]
                end = start + len(seq) - 1
                o1.write(f"{meme}\t{f[0]}\n")
                o2.write(f"{f[0]}\t{start - 1}\t{end}\t{meme}\n")
                o3.write(f"{f[0]}\t{start}\t{end}\t{seq}\t{meme}\t{f[2]}\n")
                n_sites += 1
    return n_sites


if len(sys.argv) != 3:
    sys.exit(f"python3 {sys.argv[0]} <input.meme.txt> <output.prefix>")

meme_file = Path(sys.argv[1])
out = sys.argv[2]
xml_file = meme_file if meme_file.suffix == ".xml" else meme_file.with_name("meme.xml")

if xml_file.exists():
    sites = parse_xml(xml_file, out)
else:
    sites = parse_txt(meme_file, out)

if sites == 0:
    sys.exit("No MEME motif sites parsed")
