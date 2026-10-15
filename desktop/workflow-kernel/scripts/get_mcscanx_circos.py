#!/usr/bin/env python3
import re
import sys

if len(sys.argv) != 5:
    sys.exit(f"python3 {sys.argv[0]} <mcscan.gff> <mcscanx.collinearity> <geneID.list> <out.pre>")

bed_file, anchors_file, ids_file, outpre = sys.argv[1:]
links = f'{outpre}.links.txt'
gene_pair = f'{outpre}.genepair.txt'
gene_text = f'{outpre}.geneText.txt'
gene_links = f'{outpre}.geneBlocklinks.txt'


def id_variants(value):
    value = (value or '').strip().split()[0]
    variants = {value, value.upper(), value.lower()}
    stripped = re.sub(r'_[PT]\d+$', '', value)
    variants.update({stripped, stripped.upper(), stripped.lower()})
    if re.search(r'\.\d+$', stripped) and not stripped.lower().startswith(('evm.model.', 'evm.tu.')):
        noversion = re.sub(r'\.\d+$', '', stripped)
        variants.update({noversion, noversion.upper(), noversion.lower()})
    return {item for item in variants if item}


ids = set()
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f:
            ids.update(id_variants(f[0]))


def is_family_gene(gene):
    return bool(id_variants(gene) & ids)


bed = {}
with open(gene_text, 'w') as tx, open(bed_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 4:
            bed[f[1]] = f
            if is_family_gene(f[1]):
                tx.write(f"{f[0]}\t{f[2]}\t{f[3]}\t{f[1]}\n")


def mid(gene, mode):
    f = bed[gene]
    if mode == 'perl_A_end':
        return int((int(f[3]) + int(f[3])) / 2)
    return int((int(f[3]) + int(f[2])) / 2)


text = open(anchors_file, encoding='utf-8', errors='replace').read()
with open(links, 'w') as lk, open(gene_links, 'w') as gk, open(gene_pair, 'w') as gp:
    for block in text.split('## Alignment')[1:]:
        rows = [r for r in block.strip().splitlines()]
        if not rows:
            continue
        rows = rows[1:]
        if not rows:
            continue
        first = rows[0].split('\t')
        last = rows[-1].split('\t')
        if len(first) < 3 or len(last) < 3 or first[1] not in bed or first[2] not in bed or last[1] not in bed or last[2] not in bed:
            continue
        A = [bed[first[1]][0], str(mid(first[1], 'normal')), str(mid(last[1], 'perl_A_end'))]
        B = [bed[first[2]][0], str(mid(first[2], 'perl_A_end')), str(mid(last[2], 'normal'))]
        block_has_family = False
        for row in rows:
            f = row.split('\t')
            if len(f) >= 3 and (is_family_gene(f[1]) or is_family_gene(f[2])):
                block_has_family = True
                gp.write(f"{f[1]}\t{f[2]}\n")
        line = '\t'.join(A + B) + '\n'
        if block_has_family:
            gk.write(line)
        lk.write(line)
