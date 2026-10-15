#!/usr/bin/env python3
class Node:
    def __init__(self, idx, id='', length=None):
        self.idx=idx; self.id=id; self.length=length; self.children=[]; self.parent=None
class Tree:
    def __init__(self, root): self.root=root
    def depthfirst(self):
        out=[]
        def rec(n):
            out.append(n)
            for c in n.children: rec(c)
        rec(self.root); return out
    def leaves(self): return [n for n in self.depthfirst() if not n.children]
    def count_leaves(self):
        d={}
        def rec(n):
            d[n]=1 if not n.children else sum(rec(c) for c in n.children); return d[n]
        rec(self.root); return d
    def leaf_sets(self):
        d={}
        def rec(n):
            if not n.children: d[n]=frozenset([n.id]); return d[n]
            s=frozenset().union(*(rec(c) for c in n.children)); d[n]=s; return s
        rec(self.root); return d
    def canonical_splits(self):
        leafsets=self.leaf_sets(); all_leaves=leafsets[self.root]; splits={}
        for n,s in leafsets.items():
            if n is self.root or len(s)==0 or len(s)==len(all_leaves): continue
            other=all_leaves-s
            key=s if (len(s), sorted(s)) <= (len(other), sorted(other)) else other
            splits[key]=n
        return splits
    def compare(self, other):
        a=self.canonical_splits(); b=other.canonical_splits(); return {n:b[s] for s,n in a.items() if s in b}
    def to_newick(self):
        def fmt(n):
            label=n.id or ''
            text='('+','.join(fmt(c) for c in n.children)+')'+label if n.children else label
            if n.length not in (None,''): text+=':'+str(n.length)
            return text
        return fmt(self.root)+';'
def split_newick_records(text):
    records=[]; buf=[]; depth=0; quote=None
    for ch in text:
        buf.append(ch)
        if quote:
            if ch==quote: quote=None
        elif ch in "'\"": quote=ch
        elif ch=='(': depth+=1
        elif ch==')': depth-=1
        elif ch==';' and depth==0:
            records.append(''.join(buf).strip()); buf=[]
    return [r for r in records if r]
def parse_newick(text):
    s=text.strip()
    if s.endswith(';'): s=s[:-1]
    i=0; idx=0
    def read_label_len():
        nonlocal i
        label=[]
        while i<len(s) and s[i] not in ',():': label.append(s[i]); i+=1
        lab=''.join(label).strip(); length=None
        if i<len(s) and s[i]==':':
            i+=1; val=[]
            while i<len(s) and s[i] not in ',()': val.append(s[i]); i+=1
            length=''.join(val).strip()
        return lab,length
    def sub():
        nonlocal i,idx
        n=Node(idx); idx+=1
        if i<len(s) and s[i]=='(':
            i+=1
            while True:
                c=sub(); c.parent=n; n.children.append(c)
                if i<len(s) and s[i]==',': i+=1; continue
                if i<len(s) and s[i]==')': i+=1; break
                break
        n.id,n.length=read_label_len(); return n
    return Tree(sub())
def read_trees(path):
    with open(path, encoding='utf-8', errors='replace') as fh: return [parse_newick(x) for x in split_newick_records(fh.read())]
def trim_leaf_underscores(tree):
    for n in tree.leaves():
        if n.id is not None: n.id=n.id.strip('_')
