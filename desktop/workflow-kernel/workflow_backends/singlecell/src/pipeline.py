"""Real Scanpy execution, explicit count layer, no automatic cell-type claims."""
import re
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
from common import load_config, resolve, output_dir, validate_counts, read_samples, report, save_json


def audit_design(samples, paired=False):
    frame = samples[["donor", "group", "batch"]].drop_duplicates()
    repeated = frame.groupby("donor")["group"].nunique().gt(1)
    if repeated.any() and not paired:
        raise ValueError("Donors span groups: set paired=true only for a planned paired design")
    table = pd.crosstab(frame.batch, frame.group)
    encoded = pd.get_dummies(frame[["batch", "group"]], drop_first=True, dtype=float)
    model = np.column_stack([np.ones(len(frame)), encoded.to_numpy()])
    confounded = np.linalg.matrix_rank(model) < model.shape[1]
    if confounded:
        warnings.warn("Batch/group design is rank deficient; group effects cannot be separated from batch")
    if table.size and not np.allclose(table.div(table.sum(axis=1), axis=0), table.sum(axis=0) / table.to_numpy().sum()):
        warnings.warn("Unbalanced batch/group composition; review design and adjust downstream model if appropriate")
    return {"batch_group_counts": table.to_dict(), "rank_deficient_batch_group": bool(confounded),
            "donors_across_groups": repeated[repeated].index.tolist(), "paired": bool(paired)}


def read_counts(row, base, layer):
    import scanpy as sc
    path = resolve(base, row["path"])
    if path.is_dir():
        data = sc.read_10x_mtx(path, var_names="gene_ids", make_unique=False)
    elif path.suffix == ".h5ad":
        data = sc.read_h5ad(path)
    elif path.suffix == ".h5":
        data = sc.read_10x_h5(path)
        if "gene_ids" in data.var:
            data.var["gene_symbols"] = data.var_names.astype(str)
            data.var_names = data.var["gene_ids"].astype(str)
    else:
        raise ValueError("Input must be 10x MTX directory, 10x .h5, or .h5ad")
    # nf-core/scrnaseq H5AD exports the same symbols under a singular key.
    if "gene_symbols" not in data.var and "gene_symbol" in data.var:
        data.var["gene_symbols"] = data.var["gene_symbol"].copy()
    if not data.var_names.is_unique or not data.obs_names.is_unique:
        raise ValueError(f"Duplicate gene/cell IDs in {path}; resolve upstream, do not rename silently")
    if path.suffix == ".h5ad" and layer != "X":
        if layer not in data.layers:
            raise ValueError(f"Missing explicit count layer {layer}: {path}")
        data.X = data.layers[layer].copy()
    validate_counts(data.X)
    # Scanpy's MTX reader uses float32, while H5AD may store float64.
    # Use the same precision before QC/normalization so equal counts do not
    # produce different PCA/neighbors solely because of their file format.
    data.X = data.X.astype(np.float64)
    return data


def analyze(config):
    import anndata as ad
    import scanpy as sc
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cfg, base = load_config(config)
    samples = read_samples(resolve(base, cfg["samples"]), ("sample_id", "path", "donor", "group", "batch"))
    out = output_dir(resolve(base, cfg["outdir"]))
    design_audit = audit_design(samples, cfg.get("paired", False))
    save_json(out / "design_audit.json", design_audit)
    if cfg.get("integration") == "harmony" and design_audit["rank_deficient_batch_group"]:
        raise ValueError("Cannot use Harmony to resolve complete batch/group confounding")
    seed = int(cfg.get("seed", 42))
    q = cfg["qc"]
    if q.get("max_pct_mt") is None:
        warnings.warn("Mitochondrial filtering disabled: review species/assay-specific QC before interpretation")
    objects, audit = {}, []
    for _, row in samples.iterrows():
        data = read_counts(row, resolve(base, cfg["samples"]).parent, cfg.get("counts_layer", "counts"))
        data.obs_names = data.obs_names.astype(str)
        sc.pp.calculate_qc_metrics(data, percent_top=None, inplace=True)
        pattern = q.get("mitochondrial_pattern")
        if pattern:
            symbols = data.var.get("gene_symbols", pd.Series(data.var_names, index=data.var_names))
            data.var["mt"] = [bool(re.search(pattern, str(s))) for s in symbols]
            if not data.var["mt"].any():
                raise ValueError("Mitochondrial pattern matched no genes; provide symbols or disable MT filtering")
            sc.pp.calculate_qc_metrics(data, qc_vars=["mt"], percent_top=None, inplace=True)
        elif q.get("max_pct_mt") is not None:
            raise ValueError("max_pct_mt requires an explicit species-appropriate mitochondrial_pattern")
        keep = (data.obs.n_genes_by_counts >= q["min_genes"]) & (data.obs.total_counts > 0)
        if q.get("max_genes") is not None:
            keep &= data.obs.n_genes_by_counts <= q["max_genes"]
        if q.get("max_pct_mt") is not None:
            keep &= data.obs.pct_counts_mt <= q["max_pct_mt"]
        qc = data.obs.copy()
        qc["pass_qc"] = keep
        qc.to_csv(out / f"{row.sample_id}.qc.tsv", sep="\t")
        initial = data.n_obs
        data = data[keep].copy()
        if data.n_obs < 30:
            raise ValueError(f"Too few cells after QC for {row.sample_id}: {data.n_obs}")
        if cfg.get("doublets", {}).get("enabled", False):
            if cfg.get("assay") not in ("scRNA", "snRNA"):
                raise ValueError("Specify assay scRNA or snRNA")
            sc.pp.scrublet(data, expected_doublet_rate=cfg["doublets"]["expected_rate"],
                           random_state=seed, n_prin_comps=min(30, data.n_obs - 2, data.n_vars - 2))
            data.obs[["doublet_score", "predicted_doublet"]].to_csv(out / f"{row.sample_id}.doublets.tsv", sep="\t")
            data = data[~data.obs.predicted_doublet].copy()
        for col in ("sample_id", "donor", "group", "batch"):
            data.obs[col] = str(row[col])
        objects[row.sample_id] = data
        audit.append({"sample_id": row.sample_id, "input_cells": initial, "retained_cells": data.n_obs, "input_genes": data.n_vars})
    data = ad.concat(objects, join="inner", index_unique="::", merge="same")
    # Intersection avoids treating genes unmeasured in one sample as biological zeros.
    sc.pp.filter_genes(data, min_cells=int(q.get("min_cells_per_gene", 3)))
    if data.n_vars < 50 or data.n_obs < 30:
        raise ValueError("Too few shared genes/cells for this workflow")
    data.layers["counts"] = data.X.copy()
    pd.DataFrame(audit).to_csv(out / "sample_qc.tsv", sep="\t", index=False)
    sc.pp.normalize_total(data, target_sum=1e4)
    sc.pp.log1p(data)
    sc.pp.highly_variable_genes(data, n_top_genes=min(int(cfg.get("n_hvg", 2000)), data.n_vars),
                                flavor="seurat", batch_key="sample_id" if len(objects) > 1 else None)
    n_pcs = min(int(cfg.get("n_pcs", 30)), int(data.var.highly_variable.sum()) - 1, data.n_obs - 1)
    if n_pcs < 2:
        raise ValueError("Insufficient variable genes for PCA")
    sc.pp.pca(data, n_comps=n_pcs, mask_var="highly_variable", random_state=seed)
    # Keep uncorrected embedding for comparison; Harmony is opt-in, never automatic.
    integration = cfg.get("integration", "none")
    rep = "X_pca"
    if integration == "harmony":
        if samples.batch.nunique() < 2:
            raise ValueError("Harmony requires at least two batches")
        sc.external.pp.harmony_integrate(data, key="batch", random_state=seed)
        rep = "X_pca_harmony"
    elif integration != "none":
        raise ValueError("integration must be none or harmony")
    sc.pp.neighbors(data, n_neighbors=min(int(cfg.get("n_neighbors", 15)), data.n_obs - 1), use_rep=rep, random_state=seed)
    sc.tl.umap(data, random_state=seed)
    sc.tl.leiden(data, resolution=float(cfg.get("resolution", 0.6)), key_added="cluster",
                 flavor="igraph", directed=False, n_iterations=2, random_state=seed)
    sizes = data.obs.cluster.value_counts()
    groups = sizes[sizes >= 3].index.tolist()
    if len(sizes) > 1 and groups:
        sc.tl.rank_genes_groups(data, groupby="cluster", groups=groups, method="wilcoxon", use_raw=False)
        sc.get.rank_genes_groups_df(data, group=None).to_csv(out / "cluster_markers_exploratory.tsv", sep="\t", index=False)
    data.obs["cell_type"] = "unannotated"
    data.obs.to_csv(out / "cells.tsv", sep="\t")
    pd.DataFrame({"cluster": sorted(sizes.index), "cell_type": "", "evidence": ""}).to_csv(out / "annotation_template.csv", index=False)
    composition = pd.crosstab(data.obs.sample_id, data.obs.cluster).reindex(index=samples.sample_id, fill_value=0)
    composition.to_csv(out / "cluster_counts.tsv", sep="\t")
    composition.div(composition.sum(axis=1), axis=0).to_csv(out / "cluster_fractions.tsv", sep="\t")
    sc.pl.umap(data, color=["cluster", "sample_id", "group"], show=False)
    plt.savefig(out / "umap.png", dpi=160, bbox_inches="tight"); plt.close("all")
    data.write_h5ad(out / "processed.h5ad", compression="gzip")
    report(out, "Single-cell / single-nucleus", cfg, {
        "cells": data.n_obs, "shared_genes": data.n_vars, "samples": len(objects),
        "integration": integration, "annotation": "requires review",
        "ambient_RNA_correction": "not implemented; correct upstream if needed",
        "marker_statistics": "cluster description only; not donor-level treatment tests"})


def annotate(config):
    import anndata as ad
    cfg, base = load_config(config)
    data = ad.read_h5ad(resolve(base, cfg["input"]))
    labels = pd.read_csv(resolve(base, cfg["annotations"]), dtype=str, keep_default_na=False)
    if not {"cluster", "cell_type", "evidence"} <= set(labels.columns):
        raise ValueError("Annotations need cluster,cell_type,evidence")
    if labels.cluster.duplicated().any() or (labels.cell_type.str.strip() == "").any() or (labels.evidence.str.strip() == "").any():
        raise ValueError("Labels must be unique, nonempty, and supported by evidence")
    mapping = labels.set_index("cluster")
    clusters = data.obs.cluster.astype(str)
    if not set(clusters) <= set(mapping.index):
        raise ValueError("Every cluster must have an annotation (use unknown if unresolved)")
    data.obs["cell_type"] = clusters.map(mapping.cell_type).astype("category")
    data.obs["annotation_evidence"] = clusters.map(mapping.evidence)
    out = output_dir(resolve(base, cfg["outdir"]))
    data.write_h5ad(out / "annotated.h5ad", compression="gzip")
    pd.crosstab(data.obs.sample_id, data.obs.cell_type).to_csv(out / "cell_type_counts.tsv", sep="\t")
    labels.to_csv(out / "annotations.csv", index=False)
    report(out, "Reviewed annotation", cfg, {"cells": data.n_obs})


def aggregate_counts(matrix, obs, minimum):
    """Sum technical samples within donor/group/cell_type, preserving biological units."""
    validate_counts(matrix)
    keys = ["donor", "group", "cell_type"]
    arrays, records = [], []
    for key, positions in obs.groupby(keys, observed=True, sort=True).indices.items():
        if len(positions) < minimum:
            continue
        records.append(dict(zip(keys, map(str, key)), pb_id=f"pb{len(records)+1:05d}", n_cells=len(positions)))
        arrays.append(np.asarray(matrix[positions].sum(axis=0)).ravel())
    if not arrays:
        raise ValueError("No pseudobulk unit passes min_cells")
    return np.asarray(arrays, dtype=np.int64).T, pd.DataFrame(records)


def pseudobulk(config):
    import anndata as ad
    cfg, base = load_config(config)
    data = ad.read_h5ad(resolve(base, cfg["input"]))
    for col in ("donor", "group", "cell_type"):
        if col not in data.obs or data.obs[col].isna().any():
            raise ValueError(f"Missing metadata: {col}")
    if data.obs.cell_type.astype(str).isin(["unannotated", ""]).any():
        raise ValueError("Complete annotation first; unresolved types may be labelled unknown")
    if data.obs.groupby("donor", observed=True)["group"].nunique().gt(1).any() and not cfg.get("paired", False):
        raise ValueError("Donors span groups; pseudobulk requires explicit paired=true")
    matrix, meta = aggregate_counts(data.layers["counts"], data.obs, int(cfg.get("min_cells", 20)))
    out = output_dir(resolve(base, cfg["outdir"]))
    pd.DataFrame(matrix, index=data.var_names, columns=meta.pb_id).to_csv(out / "counts.tsv", sep="\t", index_label="gene_id")
    meta.to_csv(out / "samples.tsv", sep="\t", index=False)
    symbols = data.var.get("gene_symbols", pd.Series("", index=data.var_names)).astype("string").fillna("")
    pd.DataFrame({"gene_id": data.var_names.astype(str), "gene_symbol": symbols.to_numpy()}).to_csv(
        out / "gene_mapping.tsv", sep="\t", index=False)
    if "batch" in data.obs:
        audit = audit_design(data.obs, cfg.get("paired", False))
        save_json(out / "design_audit.json", audit)
        if audit["rank_deficient_batch_group"]:
            raise ValueError("Pseudobulk exported for inspection only: batch/group confounded; do not run group-only DE")
    report(out, "Pseudobulk export", cfg, {"units": len(meta), "replicate_unit": "donor", "differential_expression": "run pseudobulk_deseq2.R separately"})
