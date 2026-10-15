#!/usr/bin/env python3
import sys
from collections import defaultdict
if len(sys.argv) != 5:
    sys.exit(f"python3 {sys.argv[0]} <genome.len> <mcscanx.gff> <tandem.pair> <genelist> > out.mct")
len_file, gff_file, tandem_file, ids_file = sys.argv[1:]
lengths = {}
with open(len_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 2: lengths[f[0]] = float(f[1])
ids = {}
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f: ids[f[0]] = 1
bed = defaultdict(dict)
with open(gff_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 4 and f[1] in ids: bed[f[0]][int(f[2])] = f
with open(tandem_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        for gene in line.rstrip('\n').split(','):
            if gene in ids: ids[gene] = 'tandem'
for chrom in sorted(bed):
    print(f"chrom   {chrom}   S=0   E={lengths[chrom]/1000000}")
    for pos in sorted(bed[chrom]):
        rec = bed[chrom][pos]; gid = rec[1]; mid = (int(rec[3]) + pos) / 2 / 1000000
        print(f"{gid}  {mid}  {'C2' if ids.get(gid) == 'tandem' else 'C4'}")
