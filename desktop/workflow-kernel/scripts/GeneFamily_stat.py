#!/usr/bin/env python3
import re, sys
if len(sys.argv) != 3:
    sys.exit(f"{sys.argv[0]} <input.gff> <input.pIkD> > genefamily_out.table")
gff, pi_file = sys.argv[1:]
pi = {}
with open(pi_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f: pi[f[0]] = '	'.join(f[1:])
print('geneID	Chr	start	end	aalen	MolWt	pI')
with open(gff, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        line = line.rstrip('\n')
        if line.startswith('#'): continue
        f = line.split('	')
        if len(f) >= 9 and f[2] == 'mRNA':
            m = re.search(r'ID=([^;]+)', f[8])
            if m and m.group(1) in pi:
                gid = m.group(1)
                print(f"{gid}	{f[0]}	{f[3]}	{f[4]}	{pi[gid]}")
