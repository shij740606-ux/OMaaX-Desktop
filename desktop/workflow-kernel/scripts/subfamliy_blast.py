#!/usr/bin/env python3
import sys
if len(sys.argv) != 4:
    sys.exit(f"python3 {sys.argv[0]} <blast.out> <reference_fam.table> <geneIDs.list> > out_genefam.table")
blast_file, ref_file, ids_file = sys.argv[1:]
blast = {}
with open(blast_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) > 1 and f[0] not in blast: blast[f[0]] = f[1]
reffam = {}
with open(ref_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 2: reffam[f[0]] = f[1]
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if not f: continue
        refid = blast.get(f[0], 'un')
        famid = reffam.get(refid, 'un') if refid != 'un' else 'un'
        print(f"{f[0]}	{famid}")
