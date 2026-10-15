#!/usr/bin/env python3
import sys
if len(sys.argv) != 3:
    sys.exit(f"python3 {sys.argv[0]} <spc2shape&color.table> <gene2spc.table> > out.itol.table")
symbol_file, spc_file = sys.argv[1:]
symbols = {}
with open(symbol_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if not f: continue
        if len(f) < 3 or f[1] not in {'1','2','3','4','5','6'}:
            sys.exit(f"Error: symbol should be a number between 1 and 6 @ {line.rstrip()}")
        symbols[f[0]] = f
print('DATASET_SYMBOL\nSEPARATOR TAB\nDATASET_LABEL\tsymbol\nDATA')
with open(spc_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 2 and f[1] in symbols:
            s = symbols[f[1]]
            print(f"{f[0]}	{s[1]}	5	{s[2]}	1	1")
