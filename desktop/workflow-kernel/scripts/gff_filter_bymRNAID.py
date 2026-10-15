#!/usr/bin/env python3
import re, sys
from collections import defaultdict
if len(sys.argv) != 5:
    sys.exit(f"python3 {sys.argv[0]} <input.gff3> <mRNA_id.list> <out.IDtable> <out.gff3>")
gff, ids_file, otab, ogff = sys.argv[1:]
gene2mrna = defaultdict(set)
with open(gff, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        if line.startswith('#'): continue
        f = line.rstrip('\n').split('	')
        if len(f) < 9 or 'ID=' not in f[8] or 'Parent=' not in f[8]: continue
        id1 = re.search(r'ID=([^;]+)', f[8]).group(1); id2 = re.search(r'Parent=([^;]+)', f[8]).group(1)
        if f[2] == 'mRNA': gene2mrna[id2].add(id1)
mrnas = set()
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f: mrnas.add(f[0])
genes = {}
with open(otab, 'w') as out:
    for gene in gene2mrna:
        for mrna in gene2mrna[gene]:
            if mrna in mrnas:
                genes[gene] = mrna; out.write(f"{gene}	{mrna}\n")
with open(ogff, 'w') as out, open(gff, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        raw = line.rstrip('\n')
        if raw.startswith('#'): continue
        f = raw.split('	')
        if len(f) != 9: continue
        m1 = re.search(r'ID=([^;]+)', f[8]); m2 = re.search(r'Parent=([^;]+)', f[8])
        id1 = m1.group(1) if m1 else 'NA'; id2 = m2.group(1) if m2 else 'NA'
        keep = (f[2] == 'gene' and id1 in genes) or (f[2] == 'mRNA' and id1 in mrnas) or (re.search(r'CDS|exon|UTR', f[2], re.I) and id2 in mrnas)
        if keep: out.write(raw + '\n')
