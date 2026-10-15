#!/usr/bin/env python3
import argparse, os, re, sys
usage = '''
Synopsis:

map_gff_ids.py genome.all.id.map genome.all.gff
'''
parser=argparse.ArgumentParser(add_help=False, usage=usage)
parser.add_argument('--help', action='store_true'); parser.add_argument('map_file', nargs='?'); parser.add_argument('gff_file', nargs='?')
args=parser.parse_args()
if args.help or not args.map_file or not args.gff_file: sys.exit(usage)
mapping={}
with open(args.map_file, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        f=line.split()
        if len(f)>=2: mapping[f[0]]=f[1]
order={'ID':1,'Parent':2,'Name':3,'Alias':4}
def parse_attrs(text):
    attrs={}
    for pair in re.split(r'\s*;\s*', text.rstrip('\n')):
        if not pair: continue
        p=re.split(r'\s*=\s*', pair, maxsplit=1)
        if len(p)==2: attrs[p[0]]=re.split(r'\s*,\s*', p[1])
    return attrs
with open(args.gff_file, encoding='utf-8', errors='replace') as fh: lines=fh.readlines()
os.unlink(args.gff_file); fasta=False
with open(args.gff_file,'w') as out:
    for line in lines:
        if re.match(r'^##FASTA', line): fasta=True
        p=line.split('	')
        if not fasta and not re.match(r'^\s*#', line) and len(p)>=9 and p[3].isdigit() and p[4].isdigit() and p[8]:
            seq,source,ftype,start,end,score,strand,phase,attr_text=p[:9]
            attrs=parse_attrs(attr_text); alias=None
            if ftype in {'mRNA','gene'} and attrs.get('ID'):
                alias=attrs['ID'][0]
                if attrs.get('Alias'): attrs['Alias']=[x for x in attrs['Alias'] if x != attrs['ID'][0]]
                if re.search(r'^[^:]+:temp\d+:|^CG:', alias): alias=None
            ids=[]
            for tag in ('Parent','ID','Alias'): ids.extend(attrs.get(tag, []))
            map_ids=[x for x in ids if x in mapping]
            if map_ids:
                for tag, vals in list(attrs.items()):
                    if tag not in {'ID','Name','Parent','Derives_from'}: continue
                    for i,value in enumerate(vals):
                        for mid in map_ids:
                            if value == mid or re.match(re.escape(mid)+r':', value):
                                if not (tag == 'Name' and not re.search(r'-gene-\d+\.\d+|^CG:|^....:|^[^:]+:temp\d+:', mid)):
                                    vals[i]=value.replace(mid, mapping[mid], 1)
                if alias and attrs.get('ID') and attrs.get('Name') and (alias == attrs['ID'][0] or alias == attrs['Name'][0]): alias=None
                if alias: attrs.setdefault('Alias', []).append(alias)
                for k in attrs: order.setdefault(k, 99)
                attr=''.join(f"{tag}={','.join(attrs[tag])};" for tag in sorted(attrs, key=lambda x:(order[x],x)))+'\n'
                line='	'.join([seq,source,ftype,start,end,score,strand,phase,attr])
        out.write(line)
