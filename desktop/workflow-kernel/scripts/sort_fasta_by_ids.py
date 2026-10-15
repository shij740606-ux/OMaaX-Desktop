#!/usr/bin/env python3
import sys
if len(sys.argv) != 3:
    sys.exit(f"python3 {sys.argv[0]} <input.fasta> <ID.list> > out.OrderByID.fasta")
fasta_file, ids_file = sys.argv[1:]
seqs = {}; current = None; chunks = []
with open(fasta_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        line = line.rstrip('\n')
        if line.startswith('>'):
            if current is not None:
                if current in seqs: sys.exit(f"erro  {current} more than once in {fasta_file}")
                seqs[current] = ''.join(chunks)
            current = line[1:].split()[0]; chunks = []
        else:
            chunks.append(line)
if current is not None:
    if current in seqs: sys.exit(f"erro  {current} more than once in {fasta_file}")
    seqs[current] = ''.join(chunks)
with open(ids_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f = line.split()
        if not f: continue
        name = f[0]
        if name in seqs: print(f">{name}\n{seqs[name]}")
        else: print(f"warning:{name} not in the {fasta_file} file", file=sys.stderr)
