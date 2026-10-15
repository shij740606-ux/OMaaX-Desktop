#!/usr/bin/env python3
"""Export one reviewed Sopa SpatialData table with coordinates to H5AD."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import argparse
from pathlib import Path
import numpy as np


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--table", required=True, help="Explicit table key; no implicit table selection")
    p.add_argument("--output", required=True)
    p.add_argument("--counts-source", required=True, help="X or named raw counts layer")
    args = p.parse_args()
    import spatialdata as sd
    from common import validate_counts
    obj = sd.read_zarr(args.input)
    data = obj.tables[args.table].copy()
    if "spatial" not in data.obsm:
        raise ValueError("Table lacks obsm['spatial']; export centroids in the intended coordinate system first")
    if np.asarray(data.obsm["spatial"]).shape != (data.n_obs, 2):
        raise ValueError("Need 2D coordinates")
    raw = data.X if args.counts_source == "X" else data.layers[args.counts_source]
    validate_counts(raw)
    data.layers["counts"] = raw.copy()
    # SpatialData may use nullable strings that older H5AD readers cannot read.
    # Object indices and categorical columns preserve labels and missing values.
    import pandas as pd
    frames = {key: value.copy() for key, value in data.obsm.items() if isinstance(value, pd.DataFrame)}
    for key in frames:
        del data.obsm[key]
    data.obs_names = pd.Index(data.obs_names.to_numpy(dtype=object), name=data.obs_names.name)
    data.var_names = pd.Index(data.var_names.to_numpy(dtype=object), name=data.var_names.name)
    for key, frame in frames.items():
        frame.index = data.obs_names.copy()
        data.obsm[key] = frame
    for frame in (data.obs, data.var):
        for column in frame.columns:
            if isinstance(frame[column].dtype, pd.StringDtype):
                frame[column] = pd.Categorical(frame[column].to_numpy(dtype=object, na_value=None))
    out = Path(args.output)
    if out.exists(): raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data.write_h5ad(out, compression="gzip")


if __name__ == "__main__": main()
