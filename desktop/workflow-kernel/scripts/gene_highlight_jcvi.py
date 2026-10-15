#!/usr/bin/env python3
import sys
if len(sys.argv) != 4:
    sys.exit(f"python3 {sys.argv[0]} <geneID.list> <jcvi.anchors.new> <jcvi.anchors.simple> > out.highlight.simple")
ids_file, anchors_file, simple_file = sys.argv[1:]
ids = set()
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if f: ids.add(f[0])
blocks = {}
with open(anchors_file, encoding='utf-8', errors='replace') as fh:
    for block in fh.read().split('###'):
        block = block.strip()
        if not block: continue
        left=[]; right=[]; hit=False
        for line in block.splitlines():
            f = line.split()
            if len(f) < 2: continue
            if f[0] in ids or f[1] in ids: hit = True
            left.append(f[0]); right.append(f[1])
        if hit and left and right:
            blocks['	'.join(sorted([left[0], left[-1]]))] = '	'.join(sorted([right[0], right[-1]]))
with open(simple_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        raw = line.rstrip('\n'); f = raw.split(); flag = False
        if len(f) >= 4:
            flag = blocks.get('	'.join(sorted([f[0], f[1]]))) == '	'.join(sorted([f[2], f[3]]))
        print(('r*' if flag else '') + raw)
