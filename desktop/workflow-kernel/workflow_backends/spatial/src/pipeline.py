"""Each section is analyzed separately: no edges or permutation across tissues."""
import re
import warnings
import numpy as np
import pandas as pd
from common import load_config, resolve, output_dir, validate_counts, read_samples, report, save_json


def check_coordinates(coords, n):
    coords = np.asarray(coords)
    if coords.shape != (n, 2) or not np.isfinite(coords).all():
        raise ValueError("Need finite n_observations x 2 coordinates in obsm['spatial']")
    if len(np.unique(coords, axis=0)) != n:
        raise ValueError("Duplicate coordinates; aggregate duplicate bins before analysis")
    return coords


def analyze(config):
    import scanpy as sc
    import squidpy as sq
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cfg, base = load_config(config)
    sheet = resolve(base, cfg["samples"])
    samples = read_samples(sheet, ("sample_id", "path", "donor", "group", "format"))
    out = output_dir(resolve(base, cfg["outdir"]))
    seed = int(cfg.get("seed", 42))
    summaries = []
    for _, row in samples.iterrows():
        folder = output_dir(out / row.sample_id)
        path = resolve(sheet.parent, row.path)
        if row["format"] == "visium":
            data = sc.read_visium(path, count_file="filtered_feature_bc_matrix.h5", load_images=True)
            if "gene_ids" in data.var:
                data.var["gene_symbols"] = data.var_names.astype(str)
                data.var_names = data.var.gene_ids.astype(str)
        elif row["format"] == "h5ad":
            data = sc.read_h5ad(path)
            layer = cfg.get("counts_layer", "counts")
            if layer != "X":
                if layer not in data.layers:
                    raise ValueError(f"Missing counts layer: {layer}")
                data.X = data.layers[layer].copy()
        else:
            raise ValueError("format must be visium (classic Space Ranger output) or h5ad")
        if not data.var_names.is_unique or not data.obs_names.is_unique:
            raise ValueError("Duplicate gene/observation IDs")
        validate_counts(data.X)
        # Match the computation precision across native Visium and H5AD inputs.
        data.X = data.X.astype(np.float64)
        coords = check_coordinates(data.obsm["spatial"], data.n_obs)
        data.obsm["spatial"] = coords
        sc.pp.calculate_qc_metrics(data, percent_top=None, inplace=True)
        keep = (data.obs.n_genes_by_counts >= cfg["qc"]["min_genes"]) & (data.obs.total_counts >= cfg["qc"]["min_counts"])
        q = cfg["qc"]
        if q.get("max_pct_mt") is not None:
            if not q.get("mitochondrial_pattern"):
                raise ValueError("max_pct_mt requires a species-specific mitochondrial_pattern")
            symbols = data.var.get("gene_symbols", pd.Series(data.var_names, index=data.var_names))
            data.var["mt"] = [bool(re.search(q["mitochondrial_pattern"], str(s))) for s in symbols]
            if not data.var.mt.any():
                raise ValueError("Mitochondrial pattern matched no genes")
            sc.pp.calculate_qc_metrics(data, qc_vars=["mt"], percent_top=None, inplace=True)
            keep &= data.obs.pct_counts_mt <= q["max_pct_mt"]
        else:
            warnings.warn("Mitochondrial filtering disabled; review species/assay-specific QC")
        if "in_tissue" in data.obs:
            keep &= pd.to_numeric(data.obs.in_tissue, errors="raise") == 1
        qc = data.obs.copy(); qc["pass_qc"] = keep
        qc.to_csv(folder / "observations_qc.tsv", sep="\t")
        initial = data.n_obs
        data = data[keep].copy()
        sc.pp.filter_genes(data, min_cells=int(cfg["qc"].get("min_cells_per_gene", 3)))
        if data.n_obs < 30 or data.n_vars < 30:
            raise ValueError(f"Insufficient data after QC: {row.sample_id}")
        for key in ("sample_id", "donor", "group"):
            data.obs[key] = str(row[key])
        data.layers["counts"] = data.X.copy()
        sc.pp.normalize_total(data, target_sum=1e4); sc.pp.log1p(data)
        sc.pp.highly_variable_genes(data, flavor="seurat", n_top_genes=min(cfg.get("n_hvg", 2000), data.n_vars))
        n_pcs = min(30, data.n_obs - 1, int(data.var.highly_variable.sum()) - 1)
        if n_pcs < 2:
            raise ValueError("Insufficient variable genes")
        sc.pp.pca(data, n_comps=n_pcs, mask_var="highly_variable", random_state=seed)
        sc.pp.neighbors(data, n_neighbors=min(15, data.n_obs - 1), random_state=seed)
        sc.tl.leiden(data, key_added="expression_cluster", resolution=float(cfg.get("resolution", 0.6)),
                     flavor="igraph", directed=False, n_iterations=2, random_state=seed)
        graph = cfg["spatial_graph"]
        mode = graph["mode"]
        # Compatibility API is intentionally pinned to Squidpy 1.8.3 (<1.9).
        if mode == "grid":
            if row["format"] != "visium" and not graph.get("grid_confirmed", False):
                raise ValueError("h5ad grid requires spatial_graph.grid_confirmed=true after geometry review; otherwise use radius/knn")
            sq.gr.spatial_neighbors(data, coord_type="grid", n_neighs=int(graph.get("n_neighbors", 6)))
        elif mode == "radius":
            if float(graph["radius"]) <= 0:
                raise ValueError("radius must be positive and expressed in coordinate units")
            sq.gr.spatial_neighbors(data, coord_type="generic", radius=float(graph["radius"]))
        elif mode == "knn":
            sq.gr.spatial_neighbors(data, coord_type="generic", n_neighs=int(graph.get("n_neighbors", 6)))
        else:
            raise ValueError("Graph mode must be grid, radius, or knn")
        graph_matrix = data.obsp["spatial_connectivities"]
        if graph_matrix.nnz == 0:
            raise ValueError("Spatial graph has no edges; check coordinate units/radius")
        degree = np.asarray((graph_matrix != 0).sum(axis=1)).ravel()
        if graph_matrix.shape != (data.n_obs, data.n_obs) or not np.isfinite(graph_matrix.data).all():
            raise ValueError("Invalid spatial graph dimensions or weights")
        pd.DataFrame({"degree": degree}, index=data.obs_names).to_csv(folder / "spatial_graph_degrees.tsv", sep="\t")
        graph_stats = {"mode": mode, "min_degree": int(degree.min()), "median_degree": float(np.median(degree)),
                       "max_degree": int(degree.max()), "isolated_fraction": float(np.mean(degree == 0))}
        save_json(folder / "spatial_graph_audit.json", graph_stats)
        if mode == "grid" and not 4 <= np.median(degree) <= 6:
            warnings.warn("Grid median degree outside 4-6; review tissue boundaries, QC holes and graph geometry")
        permutations = int(cfg.get("n_permutations", 999))
        if permutations < 99:
            raise ValueError("Use at least 99 permutations; 999 or more recommended for exploration")
        warnings.warn(f"Moran empirical p resolution is {1/(permutations+1):.6g}; {data.n_vars} genes tested. More permutations alone do not establish FDR power.")
        # Explicit gene universe, not silently limited to HVGs.
        sq.gr.spatial_autocorr(data, genes=data.var_names.tolist(), mode="moran", n_perms=permutations,
                               seed=seed, n_jobs=int(cfg.get("n_jobs", 1)), use_raw=False, corr_method="fdr_bh",
                               show_progress_bar=bool(cfg.get("show_progress_bar", False)))
        save_json(folder / "moran_interpretation.json", {"permutations": permutations,
            "empirical_p_resolution": 1 / (permutations + 1), "genes_tested": data.n_vars,
            "empirical_fdr_column": "pval_sim_fdr_bh", "analytic_fdr_column": "pval_norm_fdr_bh",
            "interpretation": "Exploratory spatial autocorrelation; distinguish empirical and normal-approximation p values; no group inference"})
        pd.DataFrame({"gene_id": data.var_names.astype(str),
                      "gene_symbol": data.var.get("gene_symbols", pd.Series("", index=data.var_names)).astype("string").fillna("").to_numpy()}).to_csv(
            folder / "gene_mapping.tsv", sep="\t", index=False)
        data.uns["moranI"].to_csv(folder / "spatial_moranI.tsv", sep="\t")
        key = cfg.get("neighborhood_label", "expression_cluster")
        if key not in data.obs or data.obs[key].isna().any():
            raise ValueError(f"Missing/empty neighborhood labels: {key}")
        data.obs[key] = data.obs[key].astype("category").cat.remove_unused_categories()
        if len(data.obs[key].cat.categories) > 1:
            sq.gr.nhood_enrichment(data, cluster_key=key, n_perms=permutations, seed=seed, n_jobs=int(cfg.get("n_jobs", 1)),
                                   backend="threading",
                                   show_progress_bar=bool(cfg.get("show_progress_bar", False)))
            result = data.uns[f"{key}_nhood_enrichment"]
            labels = data.obs[key].cat.categories
            pd.DataFrame(result["zscore"], index=labels, columns=labels).to_csv(folder / "neighborhood_zscores.tsv", sep="\t")
        sizes = data.obs.expression_cluster.value_counts()
        groups = sizes[sizes >= 3].index.tolist()
        if len(sizes) > 1 and groups:
            sc.tl.rank_genes_groups(data, groupby="expression_cluster", groups=groups, method="wilcoxon", use_raw=False)
            sc.get.rank_genes_groups_df(data, group=None).to_csv(folder / "region_markers_exploratory.tsv", sep="\t", index=False)
        fig, ax = plt.subplots(figsize=(7, 6))
        xy = data.obsm["spatial"]
        codes = data.obs.expression_cluster.cat.codes.to_numpy()
        ax.scatter(xy[:, 0], xy[:, 1], c=codes, s=float(cfg.get("point_size", 3)), cmap="tab20")
        ax.set_aspect("equal")
        if cfg.get("invert_y", True): ax.invert_yaxis()
        ax.set_title(f"{row.sample_id}: expression clusters (not validated tissue domains)")
        fig.savefig(folder / "spatial_clusters.png", dpi=180, bbox_inches="tight"); plt.close(fig)
        data.obs.to_csv(folder / "observations.tsv", sep="\t")
        data.write_h5ad(folder / "spatial_processed.h5ad", compression="gzip")
        summaries.append({"sample_id": row.sample_id, "input_observations": initial, "retained": data.n_obs,
                          "genes_tested": data.n_vars, "graph_edges": graph_matrix.nnz,
                          "isolated_observations": int((np.asarray(graph_matrix.sum(axis=1)).ravel() == 0).sum())})
    pd.DataFrame(summaries).to_csv(out / "sections_summary.tsv", sep="\t", index=False)
    report(out, "Independent spatial transcriptomics", cfg, {"sections": summaries,
        "regions": "expression-derived clusters, require histology/marker review",
        "group_statistics": "not performed; sections are not biological replicates",
        "segmentation": "use pinned Sopa upstream if needed; inspect segmentation before downstream analysis"})
