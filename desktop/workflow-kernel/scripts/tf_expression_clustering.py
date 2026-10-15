#!/usr/bin/env python3
"""Part A: TF expression time-series clustering across 3 stresses"""

import os, sys, re, csv
import numpy as np
import pandas as pd
from collections import defaultdict, OrderedDict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
import seaborn as sns
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.stats import zscore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = str(PROJECT_ROOT / "workspace")
OUTPUT = os.path.join(WORKSPACE, "cross_stress_analysis")
os.makedirs(OUTPUT, exist_ok=True)

JOBS = {
    "Salt": "job_1780143735966_0e7710",
    "Drought": "job_1780308105970_346d4e",
    "Heat": "job_1780313949486_3f3732",
}
PREFIX = {"Salt": "S", "Drought": "D", "Heat": "H"}
TIME_POINTS = ["Con", "1", "3", "6", "12", "24", "36", "48"]
TIME_LABELS = ["Con", "1h", "3h", "6h", "12h", "24h", "36h", "48h"]

TF_FAMILIES = {
    "WRKY": "PF03106",
    "NAC": "PF02365",
    "AP2_ERF": "PF00847",
    "MYB": "PF00249",
    "bZIP": "PF00170",
    "bHLH": "PF00010",
    "HSF": "PF00447",
}


def parse_featurecounts(filepath):
    df = pd.read_csv(filepath, sep="\t", comment="#", skip_blank_lines=False)
    if "Geneid" not in df.columns:
        print(f"  ERROR: unexpected format in {filepath}")
        return None
    # Keep only Geneid and count columns (drop Chr, Start, End, Strand, Length)
    count_cols = [
        c
        for c in df.columns
        if c != "Geneid" and c not in ("Chr", "Start", "End", "Strand", "Length")
    ]
    counts = df[["Geneid"] + count_cols].copy()
    counts.set_index("Geneid", inplace=True)
    rename_map = {}
    for c in counts.columns:
        base = os.path.basename(c)
        short = base.replace(".sorted.bam", "").replace(".bam", "")
        rename_map[c] = short
    counts.rename(columns=rename_map, inplace=True)
    # Clean gene IDs (remove .1, .2 version suffixes)
    counts.index = counts.index.str.replace(r"\.\d+$", "", regex=True)
    # Aggregate duplicate gene IDs (sum)
    counts = counts.groupby(counts.index).sum()
    return counts


def get_tf_genes(ipr_file, pfam_id):
    genes = set()
    with open(ipr_file) as f:
        for line in f:
            if line.startswith("#"):
                continue
            if f"Name={pfam_id}" in line:
                gene = line.split("\t")[0]
                gene_clean = re.sub(r"\.\d+$", "", gene)
                genes.add(gene_clean)
    return genes


def build_sample_to_condition(job_dir, prefix):
    st_file = os.path.join(job_dir, "rnaseq_sample_qc", "sample_table.tsv")
    mapping = {}
    with open(st_file) as f:
        next(f)
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            sample, cond = parts[0], parts[1]
            # Map to time point
            if cond == "Con":
                mapping[sample] = "Con"
            else:
                # Extract time point number
                tp = sample.replace(prefix, "").split("-")[0]
                mapping[sample] = tp
    return mapping


def compute_mean_expression(counts_df, sample_map, target_genes):
    avail = [g for g in target_genes if g in counts_df.index]
    if not avail:
        return None
    sub = counts_df.loc[avail]
    # Build time-point groups
    tp_groups = defaultdict(list)
    for sample, tp in sample_map.items():
        if sample in sub.columns:
            tp_groups[tp].append(sample)
    # Compute mean per time point
    result = []
    for tp in TIME_POINTS:
        if tp in tp_groups:
            cols = [c for c in tp_groups[tp] if c in sub.columns]
            if cols:
                mean = sub[cols].mean(axis=1)
                result.append(mean)
            else:
                result.append(pd.Series(0, index=sub.index))
        else:
            result.append(pd.Series(0, index=sub.index))
    return pd.concat(result, axis=1)


def plot_tf_heatmap(expr_dict, tf_name, genes_list):
    """Create heatmap of z-score expression across all 3 stresses"""
    n_genes = len(genes_list)
    if n_genes == 0:
        print(f"  No {tf_name} genes to plot")
        return

    # Build matrix: genes × (stress_timepoint)
    all_data = []
    for stress in ["Salt", "Drought", "Heat"]:
        if stress not in expr_dict or expr_dict[stress] is None:
            continue
        mat = expr_dict[stress].copy()
        if mat.shape[1] != len(TIME_LABELS):
            print(
                f"    WARNING: {stress} has {mat.shape[1]} cols, expected {len(TIME_LABELS)}"
            )
            continue
        mat.columns = [f"{stress}_{t}" for t in TIME_LABELS]
        mat.index = mat.index.astype(str)
        all_data.append(mat)

    if not all_data:
        return

    combined = pd.concat(all_data, axis=1)
    # Ensure string column names
    combined.columns = [str(c) for c in combined.columns]

    # Z-score normalize per gene (row-wise)
    mean_val = combined.mean(axis=1)
    std_val = combined.std(axis=1)
    z = combined.subtract(mean_val, axis=0).div(std_val.replace(0, 1), axis=0)
    z = z.fillna(0)

    # Filter to our TF genes
    common_genes = [g for g in genes_list if g in z.index]
    if not common_genes or len(common_genes) == 0:
        return
    z = z.loc[common_genes]
    # Ensure it's a DataFrame even with 1 gene
    if isinstance(z, pd.Series):
        z = z.to_frame().T

    # Hierarchical clustering
    if len(z) > 2:
        try:
            Z = linkage(z.values, method="ward", metric="euclidean")
            order = leaves_list(Z)
            z = z.iloc[order]
        except:
            pass

    # Plot
    fig_height = max(4, min(20, n_genes * 0.3))
    fig, ax = plt.subplots(figsize=(14, fig_height))

    # Color bar for stresses
    stress_colors = {"Salt": "#3498DB", "Drought": "#E67E22", "Heat": "#E74C3C"}
    col_colors = []
    for col in z.columns:
        for s in ["Salt", "Drought", "Heat"]:
            if col.startswith(s):
                col_colors.append(stress_colors[s])
                break

    sns.heatmap(
        z,
        cmap="RdBu_r",
        center=0,
        ax=ax,
        cbar_kws={"label": "Z-score"},
        xticklabels=True,
        yticklabels=len(common_genes) <= 50,
    )
    ax.set_title(
        f"{tf_name} Expression Across Stresses (z-score)",
        fontsize=14,
        fontweight="bold",
    )
    ax.set_ylabel("Genes")
    ax.set_xlabel("Stress × Time Point")

    # Legend for stress colors
    patches = [mpatches.Patch(color=c, label=s) for s, c in stress_colors.items()]
    ax.legend(handles=patches, loc="upper right", fontsize=10)

    plt.tight_layout()
    outfile = os.path.join(OUTPUT, f"heatmap_{tf_name.lower()}.png")
    fig.savefig(outfile, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {os.path.basename(outfile)}")


def plot_tf_family_trends(expr_dict, tf_name):
    """Line plot of mean expression trend per stress for a TF family"""
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = {"Salt": "#3498DB", "Drought": "#E67E22", "Heat": "#E74C3C"}
    markers = {"Salt": "o", "Drought": "s", "Heat": "^"}

    for stress in ["Salt", "Drought", "Heat"]:
        if stress not in expr_dict or expr_dict[stress] is None:
            continue
        mat = expr_dict[stress]
        mean_vals = mat.mean(axis=0).values
        sem_vals = mat.sem(axis=0).values

        ax.errorbar(
            range(len(TIME_LABELS)),
            mean_vals,
            yerr=sem_vals,
            color=colors[stress],
            marker=markers[stress],
            label=stress,
            linewidth=2,
            capsize=4,
            markersize=6,
        )

        # Annotate
        for i, v in enumerate(mean_vals):
            ax.annotate(
                f"{v:.0f}",
                (i, v),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=7,
                color=colors[stress],
            )

    ax.set_xticks(range(len(TIME_LABELS)))
    ax.set_xticklabels(TIME_LABELS, fontsize=11)
    ax.set_xlabel("Time Point", fontsize=12)
    ax.set_ylabel("Mean CPM", fontsize=12)
    ax.set_title(f"{tf_name} Family Expression Trend", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()

    outfile = os.path.join(OUTPUT, f"trend_{tf_name.lower()}.png")
    fig.savefig(outfile, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {os.path.basename(outfile)}")


def main():
    ipr_file = os.path.join(
        WORKSPACE, "job_1780143735966_0e7710/annotation/analysis/Os.interproscan.gff3"
    )

    # Step 1: Read featurecounts for all stresses
    print("Reading featurecounts...")
    count_data = {}
    sample_maps = {}
    for stress, job_id in JOBS.items():
        fc_file = os.path.join(
            WORKSPACE, job_id, "rnaseq_quant/analysis/featurecounts.txt"
        )
        job_dir = os.path.join(WORKSPACE, job_id)
        if os.path.exists(fc_file):
            counts = parse_featurecounts(fc_file)
            if counts is not None:
                count_data[stress] = counts
                # CPM normalization
                lib_sizes = counts.sum(axis=0)
                cpm = counts.div(lib_sizes, axis=1) * 1e6
                count_data[stress] = cpm
                sample_maps[stress] = build_sample_to_condition(job_dir, PREFIX[stress])
                print(f"  {stress}: {counts.shape[0]} genes, {counts.shape[1]} samples")

    # Step 2: Get TF genes
    print("\nGetting TF family members from InterProScan...")
    tf_genes = {}
    for tf_name, pfam_id in TF_FAMILIES.items():
        genes = get_tf_genes(ipr_file, pfam_id)
        print(f"  {tf_name}: {len(genes)} genes (PFAM: {pfam_id})")
        tf_genes[tf_name] = sorted(genes)

    # Step 3: Compute mean expression per TF per stress
    print("\nComputing time-series expression...")
    tf_expr = defaultdict(dict)
    for tf_name, genes in tf_genes.items():
        if not genes:
            continue
        for stress in ["Salt", "Drought", "Heat"]:
            if stress not in count_data:
                continue
            expr = compute_mean_expression(
                count_data[stress], sample_maps[stress], genes
            )
            if expr is not None:
                expr.columns = TIME_LABELS
                tf_expr[tf_name][stress] = expr

    # Step 4: Generate plots for each TF family
    print("\nGenerating plots...")
    for tf_name in TF_FAMILIES.keys():
        genes = tf_genes.get(tf_name, [])
        if not genes:
            continue
        print(f"\n--- {tf_name} ({len(genes)} genes) ---")
        plot_tf_heatmap(dict(tf_expr.get(tf_name, {})), tf_name, genes)
        plot_tf_family_trends(dict(tf_expr.get(tf_name, {})), tf_name)

    # Step 5: Combined ALL TF comparison heatmap
    print("\n--- Combined TF comparison ---")
    n_cols = len(TIME_LABELS)
    fig, axes = plt.subplots(len(TF_FAMILIES), 3, figsize=(15, len(TF_FAMILIES) * 3.5))

    for i, tf_name in enumerate(TF_FAMILIES.keys()):
        for j, stress in enumerate(["Salt", "Drought", "Heat"]):
            ax = axes[i][j] if len(TF_FAMILIES) > 1 else axes[j]
            if stress in tf_expr.get(tf_name, {}):
                mat = tf_expr[tf_name][stress]
                mean_vals = mat.mean(axis=0).values
                sem_vals = mat.sem(axis=0).values
                ax.errorbar(
                    range(n_cols),
                    mean_vals,
                    yerr=sem_vals,
                    color=["#3498DB", "#E67E22", "#E74C3C"][j],
                    marker="o",
                    linewidth=2,
                    capsize=3,
                )
                ax.set_xticks(range(n_cols))
                ax.set_xticklabels(TIME_LABELS, fontsize=7, rotation=45)
                if j == 0:
                    ax.set_ylabel(f"{tf_name}\n(CPM)", fontsize=9)
                if i == 0:
                    ax.set_title(stress, fontsize=11, fontweight="bold")

    plt.tight_layout()
    outfile = os.path.join(OUTPUT, "tf_family_comparison_grid.png")
    fig.savefig(outfile, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {os.path.basename(outfile)}")

    print(f"\nAll plots saved to: {OUTPUT}")


if __name__ == "__main__":
    main()
