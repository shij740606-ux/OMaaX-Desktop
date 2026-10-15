#!/usr/bin/env python3
"""GMT overrepresentation using explicit tested-gene universe, hypergeometric + BH."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def bh(p):
    p = np.asarray(p, dtype=float)
    if not len(p): return p
    order = np.argsort(p); ranked = p[order] * len(p) / np.arange(1, len(p)+1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1].clip(0,1)
    out = np.empty_like(p); out[order] = adjusted
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selected",required=True,help="One gene ID per line, no header")
    p.add_argument("--universe",required=True,help="All tested genes, one per line, no header")
    p.add_argument("--gmt",required=True,help="Species/identifier-matched local GMT")
    p.add_argument("--output",required=True)
    p.add_argument("--min-size",type=int,default=10)
    p.add_argument("--max-size",type=int,default=500)
    p.add_argument("--gene-map", help="TSV with gene_id,gene_symbol; selected/universe must be IDs, GMT must be symbols")
    args=p.parse_args()
    from scipy.stats import hypergeom
    selected=set(Path(args.selected).read_text(encoding="utf-8").split())
    universe=set(Path(args.universe).read_text(encoding="utf-8").split())
    if not selected or not universe or not selected<=universe: raise ValueError("Selected must be nonempty subset of tested universe")
    if args.gene_map:
        mapping = pd.read_csv(args.gene_map, sep="\t", dtype=str, keep_default_na=False)
        if not {"gene_id", "gene_symbol"} <= set(mapping.columns) or mapping.gene_id.duplicated().any():
            raise ValueError("Mapping must have unique gene_id and gene_symbol columns")
        lookup = mapping.set_index("gene_id").gene_symbol.to_dict()
        missing = {g for g in universe if not lookup.get(g, "").strip()}
        if missing:
            raise ValueError(f"Missing symbols for {len(missing)} universe genes; supply matched annotation or an ID-based GMT")
        selected = {lookup[g] for g in selected}
        universe = {lookup[g] for g in universe}
    rows=[]; names=set()
    for line in Path(args.gmt).read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        fields=line.split("\t")
        if len(fields)<3 or fields[0] in names: raise ValueError("Malformed GMT or duplicate set name")
        names.add(fields[0]); genes=set(fields[2:]) & universe
        if not args.min_size<=len(genes)<=args.max_size: continue
        hits=genes & selected
        rows.append({"term":fields[0],"universe_size":len(universe),"set_size":len(genes),"selected_size":len(selected),
                     "overlap":len(hits),"p_value":float(hypergeom.sf(len(hits)-1,len(universe),len(genes),len(selected))),
                     "genes":";".join(sorted(hits))})
    if not rows: raise ValueError("No eligible gene sets")
    frame=pd.DataFrame(rows); frame["padj"]=bh(frame.p_value)
    out=Path(args.output)
    if out.exists(): raise FileExistsError(out)
    out.parent.mkdir(parents=True,exist_ok=True)
    frame.sort_values("padj").to_csv(out,sep="\t",index=False)


if __name__=="__main__": main()
