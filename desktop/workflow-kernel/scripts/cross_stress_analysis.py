#!/usr/bin/env python3
"""
Cross-stress DEG comparison: PRJNA530826 Salt vs Drought vs Heat
Outputs: Venn intersections, DEG time trends, upSet-like comparison
"""

import os, sys, glob, re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib_venn import venn3, venn2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = str(PROJECT_ROOT / "workspace")
OUTPUT = os.path.join(WORKSPACE, "cross_stress_analysis")
os.makedirs(OUTPUT, exist_ok=True)

# Map job to stress
JOBS = {
    "Salt": "job_1780143735966_0e7710",
    "Drought": "job_1780308105970_346d4e",
    "Heat": "job_1780313949486_3f3732",
}
# Map stress prefix for time point comparisons
PREFIX = {"Salt": "S", "Drought": "D", "Heat": "H"}


def read_gene_ids(filepath):
    """Read gene IDs from DE_genes.txt (first column, skip header)"""
    genes = set()
    with open(filepath) as f:
        header = f.readline()
        for line in f:
            gene = line.strip().split("\t")[0]
            if gene:
                genes.add(gene)
    return genes


def parse_time_comparison(filename):
    """Parse filename to extract comparison type and time point"""
    # Patterns: Con_vs_T1, T1_vs_Con, Tx_vs_Ty, time_lrt
    f = filename.replace(".DE_genes.txt", "")
    return f


def collect_stress_data(job_dir, stress):
    """Collect all DEG info for one stress"""
    de_dir = os.path.join(job_dir, "rnaseq_de", "analysis")
    if not os.path.isdir(de_dir):
        return None

    all_deg_files = sorted(glob.glob(os.path.join(de_dir, "*DE_genes.txt")))

    result = {
        "stress": stress,
        "job": job_dir,
        "files": {},
        "time_lrt_genes": set(),
        "con_vs_t1_genes": set(),
        "con_vs_t48_genes": set(),
        "all_con_vs_tx_genes": set(),
    }

    for fpath in all_deg_files:
        fname = os.path.basename(fpath)
        genes = read_gene_ids(fpath)
        result["files"][fname] = genes

        if fname == "time_lrt.DE_genes.txt":
            result["time_lrt_genes"] = genes

        # Detect Con_vs_T1 (1h vs Con)
        pref = PREFIX[stress]
        if fname in [f"Con_vs_{pref}1.DE_genes.txt", f"{pref}1_vs_Con.DE_genes.txt"]:
            result["con_vs_t1_genes"] = genes
            result["all_con_vs_tx_genes"] |= genes

        # Con_vs_T48
        if fname == f"Con_vs_{pref}48.DE_genes.txt":
            result["con_vs_t48_genes"] = genes
            result["all_con_vs_tx_genes"] |= genes

    return result


def deduplicate_gene_ids(genes_set):
    """Some genes might appear as LOC_Os and Os (alternate IDs) - normalize"""
    # For now just return as-is since all use LOC_Os prefix consistently
    return genes_set


def plot_venn(set1, set2, set3, labels, title, filename):
    """Plot 3-way Venn diagram"""
    if not set1 or not set2 or not set3:
        print(f"  WARNING: Empty set for {title}")
        return

    plt.figure(figsize=(10, 8))
    v = venn3([set1, set2, set3], set_labels=labels)

    # Add counts to each region
    for region in ["100", "010", "001", "110", "101", "011", "111"]:
        area = v.get_patch_by_id(region)
        label = v.get_label_by_id(region)
        if label:
            count = int(label.get_text())
            # Color the central intersection differently
            if region == "111" and area:
                area.set_color("#E74C3C")
                area.set_alpha(0.6)

    plt.title(title, fontsize=14, fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT, filename), dpi=150)
    plt.close()
    print(f"  Saved: {filename}")


def plot_deg_time_trends(all_data):
    """Plot DEG counts across time points for each stress"""
    plt.figure(figsize=(12, 6))

    colors = {"Salt": "#3498DB", "Drought": "#E67E22", "Heat": "#E74C3C"}
    markers = {"Salt": "o", "Drought": "s", "Heat": "^"}

    time_points = ["T1", "T3", "T6", "T12", "T24", "T36", "T48"]

    for stress in ["Salt", "Drought", "Heat"]:
        pref = PREFIX[stress]
        data = all_data[stress]

        # Counts for Tx_vs_T1 files (show cumulative change from 1h)
        counts = []
        for tp in time_points:
            if tp == "T1":
                cnt = len(data["con_vs_t1_genes"])
                counts.append(cnt)
            else:
                fname = f"{pref}{tp.replace('T', '')}_vs_{pref}1.DE_genes.txt"
                if fname in data["files"]:
                    counts.append(len(data["files"][fname]))
                else:
                    counts.append(0)

        plt.plot(
            time_points,
            counts,
            color=colors[stress],
            marker=markers[stress],
            label=f"{stress}",
            linewidth=2.5,
            markersize=8,
        )

        # Annotate each point
        for i, cnt in enumerate(counts):
            plt.annotate(
                f"{cnt}",
                (time_points[i], cnt),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=8,
                color=colors[stress],
            )

    plt.xlabel("Time Point", fontsize=13)
    plt.ylabel("Number of DEGs", fontsize=13)
    plt.title(
        "DEG Counts Across Time Points (vs 1h baseline)", fontsize=14, fontweight="bold"
    )
    plt.legend(fontsize=12)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT, "DEG_time_trends.png"), dpi=150)
    plt.close()
    print("  Saved: DEG_time_trends.png")

    # Also plot Con_vs_T1 and Con_vs_T48 comparison
    fig, ax = plt.subplots(figsize=(8, 6))
    x = np.arange(3)
    width = 0.25

    early = [len(all_data[s]["con_vs_t1_genes"]) for s in ["Salt", "Drought", "Heat"]]
    late = [len(all_data[s]["con_vs_t48_genes"]) for s in ["Salt", "Drought", "Heat"]]

    bars1 = ax.bar(
        x - width / 2, early, width, label="Early (1h vs Con)", color="#3498DB"
    )
    bars2 = ax.bar(
        x + width / 2, late, width, label="Late (48h vs Con)", color="#E74C3C"
    )

    for bar in bars1:
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 100,
            f"{int(bar.get_height())}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    for bar in bars2:
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 100,
            f"{int(bar.get_height())}",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(["Salt", "Drought", "Heat"], fontsize=12)
    ax.set_ylabel("Number of DEGs", fontsize=12)
    ax.set_title("Early vs Late Response Comparison", fontsize=13, fontweight="bold")
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT, "early_vs_late_response.png"), dpi=150)
    plt.close()
    print("  Saved: early_vs_late_response.png")


def print_venn_summary(name, salt_set, drought_set, heat_set):
    """Print detailed Venn breakdown"""
    only_salt = salt_set - drought_set - heat_set
    only_drought = drought_set - salt_set - heat_set
    only_heat = heat_set - salt_set - drought_set

    salt_drought = (salt_set & drought_set) - heat_set
    salt_heat = (salt_set & heat_set) - drought_set
    drought_heat = (drought_set & heat_set) - salt_set

    all_three = salt_set & drought_set & heat_set

    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    print(f"  Salt only:             {len(only_salt):>6}")
    print(f"  Drought only:          {len(only_drought):>6}")
    print(f"  Heat only:             {len(only_heat):>6}")
    print(f"  Salt ∩ Drought:        {len(salt_drought):>6}")
    print(f"  Salt ∩ Heat:           {len(salt_heat):>6}")
    print(f"  Drought ∩ Heat:        {len(drought_heat):>6}")
    print(f"  All Three (S∩D∩H):     {len(all_three):>6}")
    print(f"  {'─' * 40}")
    print(f"  Salt total:            {len(salt_set):>6}")
    print(f"  Drought total:         {len(drought_set):>6}")
    print(f"  Heat total:            {len(heat_set):>6}")

    return {
        "only_salt": only_salt,
        "only_drought": only_drought,
        "only_heat": only_heat,
        "salt_drought": salt_drought,
        "salt_heat": salt_heat,
        "drought_heat": drought_heat,
        "all_three": all_three,
    }


def save_gene_lists(sets_dict, name):
    """Save gene lists to files"""
    outdir = os.path.join(OUTPUT, f"{name}_gene_lists")
    os.makedirs(outdir, exist_ok=True)

    for key, genes in sets_dict.items():
        if genes:
            fpath = os.path.join(outdir, f"{key}.txt")
            with open(fpath, "w") as f:
                for g in sorted(genes):
                    f.write(f"{g}\n")
            print(f"    Saved {key}: {len(genes)} genes → {fpath}")


def main():
    # Collect all data
    all_data = {}
    for stress, job_id in JOBS.items():
        job_dir = os.path.join(WORKSPACE, job_id)
        print(f"Reading {stress} data from {job_id}...")
        data = collect_stress_data(job_dir, stress)
        if data:
            all_data[stress] = data
            print(f"  time_lrt: {len(data['time_lrt_genes'])} genes")
            print(f"  Con_vs_T1: {len(data['con_vs_t1_genes'])} genes")
            print(f"  Con_vs_T48: {len(data['con_vs_t48_genes'])} genes")

    if len(all_data) < 3:
        print("ERROR: Not enough stress data collected")
        sys.exit(1)

    # === ANALYSIS 1: Time LRT Venn ===
    print("\n" + "█" * 60)
    print("  ANALYSIS 1: Full Temporal Response (time_lrt)")
    print("█" * 60)
    salt_lrt = all_data["Salt"]["time_lrt_genes"]
    drought_lrt = all_data["Drought"]["time_lrt_genes"]
    heat_lrt = all_data["Heat"]["time_lrt_genes"]

    sets_lrt = print_venn_summary("time_lrt DEGs", salt_lrt, drought_lrt, heat_lrt)
    save_gene_lists(sets_lrt, "time_lrt_venn")
    plot_venn(
        salt_lrt,
        drought_lrt,
        heat_lrt,
        ["Salt", "Drought", "Heat"],
        "Core Stress Response Genes (timeLRT)",
        "venn_timelrt.png",
    )

    # === ANALYSIS 2: Early Response (Con_vs_T1) Venn ===
    print("\n" + "█" * 60)
    print("  ANALYSIS 2: Early Response (1h vs Con)")
    print("█" * 60)
    salt_early = all_data["Salt"]["con_vs_t1_genes"]
    drought_early = all_data["Drought"]["con_vs_t1_genes"]
    heat_early = all_data["Heat"]["con_vs_t1_genes"]

    sets_early = print_venn_summary(
        "Early Response (1h vs Con)", salt_early, drought_early, heat_early
    )
    save_gene_lists(sets_early, "early_venn")
    plot_venn(
        salt_early,
        drought_early,
        heat_early,
        ["Salt", "Drought", "Heat"],
        "Early Response DEGs (1h vs Con)",
        "venn_early.png",
    )

    # === ANALYSIS 3: Late Response (Con_vs_T48) Venn ===
    if all(s["con_vs_t48_genes"] for s in all_data.values()):
        print("\n" + "█" * 60)
        print("  ANALYSIS 3: Late Response (48h vs Con)")
        print("█" * 60)
        salt_late = all_data["Salt"]["con_vs_t48_genes"]
        drought_late = all_data["Drought"]["con_vs_t48_genes"]
        heat_late = all_data["Heat"]["con_vs_t48_genes"]

        sets_late = print_venn_summary(
            "Late Response (48h vs Con)", salt_late, drought_late, heat_late
        )
        save_gene_lists(sets_late, "late_venn")
        plot_venn(
            salt_late,
            drought_late,
            heat_late,
            ["Salt", "Drought", "Heat"],
            "Late Response DEGs (48h vs Con)",
            "venn_late.png",
        )

    # === ANALYSIS 4: Time Trend Plot ===
    print("\n" + "█" * 60)
    print("  ANALYSIS 4: DEG Time Trends")
    print("█" * 60)
    plot_deg_time_trends(all_data)

    # === SUMMARY FILE ===
    print("\n" + "█" * 60)
    print("  GENERATING SUMMARY REPORT")
    print("█" * 60)

    with open(os.path.join(OUTPUT, "cross_stress_summary.txt"), "w") as f:
        f.write("=" * 70 + "\n")
        f.write("PRJNA530826 Cross-Stress DEG Comparison Summary\n")
        f.write("Salt (S) vs Drought (D) vs Heat (H)\n")
        f.write("=" * 70 + "\n\n")

        f.write("DATASET OVERVIEW\n")
        f.write("-" * 40 + "\n")
        f.write(f"{'Stress':<12} {'Job ID':<30} {'Samples':<10}\n")
        f.write(f"{'─' * 52}\n")
        for stress in ["Salt", "Drought", "Heat"]:
            f.write(f"{stress:<12} {JOBS[stress]:<30} {'24':<10}\n")

        f.write("\n\nDEG COUNT SUMMARY\n")
        f.write("-" * 40 + "\n")
        f.write(f"{'Comparison':<25} {'Salt':<10} {'Drought':<10} {'Heat':<10}\n")
        f.write(f"{'─' * 55}\n")
        f.write(
            f"{'time_lrt':<25} {len(salt_lrt):<10} {len(drought_lrt):<10} {len(heat_lrt):<10}\n"
        )
        f.write(
            f"{'1h vs Con':<25} {len(salt_early):<10} {len(drought_early):<10} {len(heat_early):<10}\n"
        )
        if all(s["con_vs_t48_genes"] for s in all_data.values()):
            f.write(
                f"{'48h vs Con':<25} {len(all_data['Salt']['con_vs_t48_genes']):<10} {len(all_data['Drought']['con_vs_t48_genes']):<10} {len(all_data['Heat']['con_vs_t48_genes']):<10}\n"
            )

        f.write("\n\nVENN INTERSECTIONS (time_lrt)\n")
        f.write("-" * 40 + "\n")
        for key, label in [
            ("all_three", "All Three (S∩D∩H)"),
            ("salt_drought", "Salt ∩ Drought"),
            ("salt_heat", "Salt ∩ Heat"),
            ("drought_heat", "Drought ∩ Heat"),
            ("only_salt", "Salt only"),
            ("only_drought", "Drought only"),
            ("only_heat", "Heat only"),
        ]:
            f.write(f"{label:<25} {len(sets_lrt[key]):>7}\n")

        f.write("\n\nVENN INTERSECTIONS (early - 1h vs Con)\n")
        f.write("-" * 40 + "\n")
        for key, label in [
            ("all_three", "All Three (S∩D∩H)"),
            ("salt_drought", "Salt ∩ Drought"),
            ("salt_heat", "Salt ∩ Heat"),
            ("drought_heat", "Drought ∩ Heat"),
            ("only_salt", "Salt only"),
            ("only_drought", "Drought only"),
            ("only_heat", "Heat only"),
        ]:
            f.write(f"{label:<25} {len(sets_early[key]):>7}\n")

    print(f"\n  ✅ All results saved to: {OUTPUT}")
    print(f"  📄 Summary: cross_stress_summary.txt")
    print(f"  📊 Venn diagrams: venn_timelrt.png, venn_early.png, venn_late.png")
    print(f"  📈 Time trends: DEG_time_trends.png, early_vs_late_response.png")


if __name__ == "__main__":
    main()
