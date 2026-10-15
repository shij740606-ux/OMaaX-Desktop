"""Community description on one profiler/database/rank; no fake functional inference."""
import numpy as np
import pandas as pd
from common import load_config, resolve, output_dir, read_samples, report


def validate_abundance(table):
    if table.empty or not table.index.is_unique or not table.columns.is_unique:
        raise ValueError("Abundance table needs unique taxa and sample IDs")
    x = table.to_numpy(dtype=float)
    if not np.isfinite(x).all() or (x < 0).any() or (x.sum(axis=0) <= 0).any():
        raise ValueError("Abundances must be finite, nonnegative, and nonzero per sample")
    return x / x.sum(axis=0)


def diversity(p):
    safe = np.where(p > 0, p, 1.0)
    return pd.DataFrame({"detected_taxa": (p > 0).sum(axis=0),
                         "shannon": -(p * np.log(safe)).sum(axis=0),
                         "simpson_1_minus_D": 1 - (p * p).sum(axis=0)})


def bray_curtis(p):
    n = p.shape[1]
    distances = np.zeros((n, n))
    for i in range(n):
        for j in range(i):
            denominator = np.sum(p[:, i] + p[:, j])
            distances[i, j] = distances[j, i] = np.abs(p[:, i] - p[:, j]).sum() / denominator
    return distances


def pcoa(distances):
    n = len(distances)
    centering = np.eye(n) - np.ones((n, n)) / n
    matrix = -0.5 * centering @ (distances ** 2) @ centering
    values, vectors = np.linalg.eigh(matrix)
    order = np.argsort(values)[::-1]; values, vectors = values[order], vectors[:, order]
    positive = values > 1e-10
    coords = vectors[:, positive] * np.sqrt(values[positive])
    coords = np.pad(coords[:, :2], ((0, 0), (0, max(0, 2 - coords.shape[1]))))
    denominator = np.abs(values).sum()
    negative_fraction = float(np.abs(values[values < 0]).sum() / denominator) if denominator else 0.0
    return coords, values, negative_fraction


def pseudo_f(distances, labels):
    labels = np.asarray(labels); n = len(labels); groups = np.unique(labels)
    if not 1 < len(groups) < n:
        raise ValueError("PERMANOVA requires >=2 groups and residual degrees of freedom")
    total = np.sum(distances ** 2) / (2 * n)
    within = sum(np.sum(distances[np.ix_(labels == g, labels == g)] ** 2) / (2 * np.sum(labels == g)) for g in groups)
    if within <= 0:
        raise ValueError("Zero within-group distance: PERMANOVA undefined")
    return ((total - within) / (len(groups) - 1)) / (within / (n - len(groups)))


def permanova(distances, labels, n_permutations=999, seed=42):
    if n_permutations < 99:
        raise ValueError("Use at least 99 permutations")
    observed = pseudo_f(distances, labels)
    rng = np.random.default_rng(seed)
    count = sum(pseudo_f(distances, rng.permutation(labels)) >= observed - 1e-12 for _ in range(n_permutations))
    return {"pseudo_F": float(observed), "p_value": (count + 1) / (n_permutations + 1), "permutations": n_permutations}


def merge_bracken(samples_file, outdir):
    samples = read_samples(samples_file.resolve(), ("sample_id", "path"))
    tables, annotations, ranks = [], {}, set()
    for _, row in samples.iterrows():
        frame = pd.read_csv(resolve(samples_file.resolve().parent, row.path), sep="\t", dtype={"taxonomy_id": str})
        required = {"taxonomy_id", "taxonomy_lvl", "name", "new_est_reads"}
        if not required <= set(frame.columns) or frame.taxonomy_id.duplicated().any():
            raise ValueError("Expected a Bracken abundance file with unique taxonomy_id")
        ranks.update(frame.taxonomy_lvl.astype(str))
        for taxid, name in zip(frame.taxonomy_id, frame["name"]):
            if taxid in annotations and annotations[taxid] != name:
                raise ValueError("Taxonomy names differ; check database versions")
            annotations[taxid] = name
        tables.append(frame.set_index("taxonomy_id").new_est_reads.rename(row.sample_id))
    if len(ranks) != 1:
        raise ValueError("All Bracken inputs must use a single taxonomic rank")
    matrix = pd.concat(tables, axis=1).fillna(0)
    validate_abundance(matrix)
    out = output_dir(outdir)
    matrix.to_csv(out / "abundance.tsv", sep="\t", index_label="taxon_id")
    pd.Series(annotations, name="name").to_csv(out / "taxon_names.tsv", sep="\t", index_label="taxon_id")
    report(out, "Bracken merge", {"rank": list(ranks)}, {"samples": len(samples), "taxa": len(matrix), "database_consistency": "user must provide the same database version for every sample"})


def summarize(config):
    cfg, base = load_config(config)
    if not all(cfg.get(k) for k in ("profiler", "database_version", "taxonomic_rank", "abundance_type")):
        raise ValueError("Record profiler, database_version, taxonomic_rank and abundance_type")
    if str(cfg["database_version"]).startswith("REPLACE_"):
        raise ValueError("Replace database_version with the actual database build/version")
    table = pd.read_csv(resolve(base, cfg["abundance"]), sep="\t", index_col=0)
    table.index = table.index.astype(str)
    names = None
    if cfg.get("taxon_names"):
        annotations = pd.read_csv(resolve(base, cfg["taxon_names"]), sep="\t", dtype=str).set_index("taxon_id")
        if not annotations.index.is_unique or not set(table.index) <= set(annotations.index):
            raise ValueError("Taxon names must uniquely cover all abundance IDs")
        names = annotations["name"].reindex(table.index)
    metadata = read_samples(resolve(base, cfg["samples"]), ("sample_id", "donor", "group")).set_index("sample_id")
    if set(table.columns) != set(metadata.index):
        raise ValueError("Abundance and metadata sample IDs must match exactly")
    table = table.loc[:, metadata.index]
    p = validate_abundance(table)
    out = output_dir(resolve(base, cfg["outdir"]))
    pd.DataFrame(p, index=table.index, columns=table.columns).to_csv(out / "relative_abundance.tsv", sep="\t")
    alpha = diversity(p); alpha.index = table.columns
    alpha.join(metadata).to_csv(out / "alpha_diversity.tsv", sep="\t")
    distances = bray_curtis(p)
    pd.DataFrame(distances, index=table.columns, columns=table.columns).to_csv(out / "bray_curtis.tsv", sep="\t")
    coords, eigenvalues, negative = pcoa(distances)
    pd.DataFrame(coords, index=table.columns, columns=["PCoA1", "PCoA2"]).join(metadata).to_csv(out / "pcoa.tsv", sep="\t")
    pd.DataFrame({"eigenvalue": eigenvalues}).to_csv(out / "pcoa_eigenvalues.tsv", sep="\t", index=False)
    top = np.argsort(p.mean(axis=1))[::-1][:int(cfg.get("top_taxa", 20))]
    top_table = pd.DataFrame(p[top], index=table.index[top], columns=table.columns)
    if names is not None:
        top_table.insert(0, "taxon_name", names.reindex(top_table.index))
        names.to_csv(out / "taxon_names.tsv", sep="\t", index_label="taxon_id")
    top_table.to_csv(out / "top_taxa.tsv", sep="\t", index_label="taxon_id")
    stats = {"samples": len(metadata), "taxa": len(table), "pcoa_negative_eigenvalue_fraction": negative,
             "alpha_caveat": "detected taxa is profiler/depth dependent; no rarefaction and no unseen richness estimate",
             "differential_abundance": "Run differential_abundance.R separately on unnormalized count data; not computed here",
             "permanova": "disabled"}
    if cfg.get("permanova", False):
        if cfg.get("design") != "independent_one_factor" or metadata.donor.duplicated().any():
            raise ValueError("PERMANOVA supports only independent donors, one group factor, no repeated measures/covariates")
        if metadata.group.nunique() < 2 or (metadata.groupby("group").size() < 3).any():
            raise ValueError("Need >=2 groups and >=3 independent donors per group")
        stats["permanova"] = permanova(distances, metadata.group.to_numpy(), int(cfg.get("n_permutations", 999)), int(cfg.get("seed", 42)))
        stats["permanova_caveat"] = "Exploratory: dispersion has not been tested; a significant result can reflect dispersion differences"
    # Charts are optional; tables and statistics only require NumPy/Pandas.
    if cfg.get("plots", True):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 5))
        for group in metadata.group.unique():
            mask = metadata.group.to_numpy() == group
            ax.scatter(coords[mask, 0], coords[mask, 1], label=group)
        ax.set(xlabel="PCoA1", ylabel="PCoA2", title="Bray-Curtis PCoA")
        ax.legend(); fig.savefig(out / "pcoa.png", dpi=160, bbox_inches="tight"); plt.close(fig)
    report(out, "Shotgun community analysis", cfg, stats)
