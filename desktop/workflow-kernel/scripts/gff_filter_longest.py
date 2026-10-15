#!/usr/bin/env python3
import re, sys
from collections import defaultdict
if len(sys.argv) != 4:
    sys.exit(f"python3 {sys.argv[0]} <ncbi.gff3> <out.longestID> <out.ncbi_longest.gff3>")
gff, otab, ogff = sys.argv[1:]

def attrs_to_dict(attr):
    parsed = {}
    for item in attr.split(';'):
        if not item:
            continue
        if '=' in item:
            key, value = item.split('=', 1)
        elif ' ' in item:
            key, value = item.split(' ', 1)
            value = value.strip('"')
        else:
            continue
        parsed[key] = value
    return parsed

def first_attr(attrs, *keys):
    for key in keys:
        value = attrs.get(key)
        if value:
            return value.split(',')[0]
    return None

def public_mrna_id(attrs, raw_id):
    """Prefer stable transcript names over provider-internal PAC IDs."""
    if raw_id and raw_id.startswith('PAC:'):
        for key in ('Name', 'transcript', 'transcript_id'):
            value = first_attr(attrs, key)
            if value and not value.startswith('PAC:'):
                return value
    return raw_id

def replace_attr_value(attr, key, value):
    if not value:
        return attr
    pattern = re.compile(r'(^|;)' + re.escape(key) + r'=([^;]*)')
    if pattern.search(attr):
        return pattern.sub(lambda m: f"{m.group(1)}{key}={value}", attr, count=1)
    return attr + f";{key}={value}"

def rewrite_public_attrs(attr, ftype, raw_to_public):
    attrs = attrs_to_dict(attr)
    lower_type = ftype.lower()
    if lower_type in {'mrna', 'transcript'}:
        raw_id = first_attr(attrs, 'ID', 'transcript_id')
        public_id = raw_to_public.get(raw_id, raw_id)
        if raw_id and public_id and public_id != raw_id:
            attr = replace_attr_value(attr, 'ID', public_id)
            attr = replace_attr_value(attr, 'original_id', raw_id)
    elif re.search(r'CDS|exon|UTR', ftype, re.I):
        parent = first_attr(attrs, 'Parent')
        public_parent = raw_to_public.get(parent, parent)
        if parent and public_parent and public_parent != parent:
            attr = replace_attr_value(attr, 'Parent', public_parent)
            raw_id = first_attr(attrs, 'ID')
            if raw_id and raw_id.startswith(parent):
                attr = replace_attr_value(attr, 'ID', public_parent + raw_id[len(parent):])
            attr = replace_attr_value(attr, 'original_parent', parent)
    return attr

gene2mrna=defaultdict(set); mrna2cds={}; cdslen=defaultdict(int); selected_genes=set()
mrna2gene={}; raw_to_public={}
with open(gff, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        if line.startswith('#'): continue
        f=line.rstrip('\n').split('	')
        if len(f)<9: continue
        attrs=attrs_to_dict(f[8])
        id1=first_attr(attrs, 'ID', 'transcript_id')
        parent=first_attr(attrs, 'Parent')
        ftype=f[2].lower()
        if ftype in {'mrna', 'transcript'}:
            if not id1:
                continue
            public_id=public_mrna_id(attrs, id1)
            raw_to_public[id1]=public_id
            gene_id=parent or first_attr(attrs, 'geneID', 'gene_id', 'gene', 'locus', 'gene_name', 'Name')
            if not gene_id:
                gene_id=re.sub(r'(\.mRNA\d+|\.m\d+|\.t\d+|\.p\d+)$', '', public_id)
            gene2mrna[gene_id].add(public_id)
            mrna2gene[public_id]=gene_id
        if ftype == 'cds':
            raw_mrna=parent or first_attr(attrs, 'transcript_id', 'transcript')
            mrna=raw_to_public.get(raw_mrna, raw_mrna)
            if not mrna:
                continue
            cds_id=id1 or mrna
            if raw_mrna and raw_mrna.startswith('PAC:') and mrna != raw_mrna:
                cds_id = mrna
            mrna2cds[mrna]=cds_id
            cdslen[mrna]+=int(f[4])-int(f[3])+1
longest={}
with open(otab,'w') as out:
    for gene,mrnas in gene2mrna.items():
        best_len=0; best=None
        for mrna in mrnas:
            cds=mrna2cds.get(mrna)
            if cds is not None and cdslen.get(mrna,0)>best_len: best_len=cdslen[mrna]; best=mrna
        if best_len:
            out.write(f"{gene}	{best}	{mrna2cds[best]}\n"); longest[best]=mrna2cds[best]; selected_genes.add(gene)
with open(ogff,'w') as out, open(gff, encoding='utf-8', errors='replace') as fh:
    for line in fh:
        raw=line.rstrip('\n')
        if raw.startswith('#'): continue
        f=raw.split('	')
        if len(f)!=9: continue
        attrs=attrs_to_dict(f[8])
        id1=first_attr(attrs, 'ID', 'transcript_id') or 'NA'
        id2=first_attr(attrs, 'Parent') or 'NA'
        public_id1=raw_to_public.get(id1, id1)
        public_id2=raw_to_public.get(id2, id2)
        gene_id=first_attr(attrs, 'geneID', 'gene_id', 'gene', 'locus', 'gene_name', 'Name') or mrna2gene.get(public_id1)
        keep=(f[2]=='gene' and id1 in selected_genes) or (f[2].lower() in {'mrna','transcript'} and public_id1 in longest) or (re.search(r'CDS|exon|UTR', f[2], re.I) and public_id2 in longest) or (f[2]=='gene' and gene_id in selected_genes)
        if keep:
            f[8]=rewrite_public_attrs(f[8], f[2], raw_to_public)
            out.write('\t'.join(f)+'\n')
