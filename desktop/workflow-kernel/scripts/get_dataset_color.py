#!/usr/bin/env python3
import sys
if len(sys.argv) != 4:
    sys.exit(f"python3 {sys.argv[0]} <fam2color.table> <gene2fam.table> <label or range> > out.itol.table")
color_file, fam_file, dtype = sys.argv[1:]
if dtype not in {'label', 'range'}:
    sys.exit('Error: type should be lable or range')
colors = {}
with open(color_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f: colors[f[0]] = f[1]
print('TREE_COLORS\nSEPARATOR TAB\nDATA')
with open(fam_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if len(f) >= 2 and f[1] in colors:
            print(f"{f[0]}	{dtype}	{colors[f[1]]}" + (f"	{f[1]}" if dtype == 'range' else ''))
