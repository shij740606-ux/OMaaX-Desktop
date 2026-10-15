#!/usr/bin/env python3
import argparse, os, re, sys
usage = '''
Synopsis:

map_fasta_ids genome.all.id.map genome.all.transcripts.fasta

Description:

This script takes a id map file and changes the name of the ID in a
fasta file.
'''
parser=argparse.ArgumentParser(add_help=False, usage=usage)
parser.add_argument('--help', action='store_true'); parser.add_argument('map_file', nargs='?'); parser.add_argument('fasta_file', nargs='?')
args=parser.parse_args()
if args.help or not args.map_file or not args.fasta_file: sys.exit(usage)
mapping={}
with open(args.map_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f=line.split()
        if len(f)>=2: mapping[f[0]]=f[1]
with open(args.fasta_file, encoding='utf-8', errors='replace') as fh: lines=fh.readlines()
os.unlink(args.fasta_file)
with open(args.fasta_file,'w') as out:
    for line in lines:
        if line.startswith('>'):
            m=re.match(r'^>(\S+)', line); old=m.group(1) if m else ''
            if old in mapping: line=re.sub(r'^>'+re.escape(old), '>'+mapping[old], line)
            else: print(f"WARNING:  No mapping available for {old}", file=sys.stderr)
        out.write(line)
