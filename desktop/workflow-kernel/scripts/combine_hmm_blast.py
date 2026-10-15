#!/usr/bin/env python3
import sys
if len(sys.argv) != 3:
    sys.exit(f"python3 {sys.argv[0]} <hmm.domtblout> <blast.m6> > combine.out")
hmm, m6 = sys.argv[1:]
blast = {}
with open(m6, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        line = line.rstrip('\n')
        if line.startswith('#'):
            continue
        fields = line.split('	')
        if fields and len(fields) > 1 and fields[0] not in blast:
            blast[fields[0]] = fields[1]
with open(hmm, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        raw = line.rstrip('\n')
        fields = raw.split()
        if fields and fields[0] in blast:
            print('	'.join(fields) + '	' + blast[fields[0]])
