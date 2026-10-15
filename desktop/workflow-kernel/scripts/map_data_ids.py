#!/usr/bin/env python3
import argparse, os, sys
usage = '''
Synopsis:

map_data_ids genome.all.id.map data.txt

Description:

This script takes a id map file and changes the name of the ID in a
data file.  The map file is a two column tab delimited file with two
columns: old_name and new_name.  The data file is assumed to be
tab delimited by default, but this can be altered with the delimit
option.  The ID in the data file can be in any column and is specified
by the col option which defaults to the first column.
'''
parser=argparse.ArgumentParser(add_help=False, usage=usage)
parser.add_argument('--help', action='store_true'); parser.add_argument('--col', type=int, default=1); parser.add_argument('--delimit', default='	'); parser.add_argument('map_file', nargs='?'); parser.add_argument('data_file', nargs='?')
args=parser.parse_args()
if args.help or not args.map_file or not args.data_file: sys.exit(usage)
col=args.col-1; mapping={}
with open(args.map_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f=line.split()
        if len(f)>=2: mapping[f[0]]=f[1]
with open(args.data_file, encoding='utf-8', errors='replace') as fh: lines=fh.readlines()
os.unlink(args.data_file)
with open(args.data_file,'w') as out:
    for line in lines:
        f=line.rstrip('\n').split(args.delimit)
        if col < len(f) and f[col] in mapping: f[col]=mapping[f[col]]
        else: print(f"WARNING: No mapping available for {f[col] if col < len(f) else ''}", file=sys.stderr)
        out.write(args.delimit.join(f)+'\n')
