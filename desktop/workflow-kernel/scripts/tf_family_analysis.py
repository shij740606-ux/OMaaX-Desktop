#!/usr/bin/env python3
"""
TF Family analysis among cross-stress DEGs.
Uses InterProScan annotations + pfam mapping to identify TF families.
"""

import os, csv, re, json
from collections import defaultdict, Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = str(PROJECT_ROOT / "workspace")
OUTPUT = os.path.join(WORKSPACE, "cross_stress_analysis")
ANNO_FILE = os.path.join(
    WORKSPACE,
    "job_1780143735966_0e7710/final/Os.rnaseq_candidate_family.gene_annotations.tsv",
)
VENN_DIR = os.path.join(OUTPUT, "time_lrt_venn_gene_lists")

# Key stress-related TF families to check
TF_FAMILIES = {
    "WRKY": ["PF03106", "WRKY"],
    "NAC": ["PF02365", "NAC domain"],
    "MYB": ["PF00249", "PF13921", "MYB"],
    "AP2_ERF": ["PF00847", "AP2", "ERF", "DREB"],
    "bZIP": ["PF00170", "PF07716", "bZIP"],
    "bHLH": ["PF00010", "bHLH", "helix-loop-helix"],
    "HSF": ["PF00447", "HSF"],
    "MADS": ["PF00319", "PF01486", "MADS"],
    "TCP": ["PF03634", "TCP"],
    "ARF": ["PF06507", "ARF"],
    "GRAS": ["PF03514", "GRAS"],
    "HD-ZIP": ["PF00046", "PF02183", "homeobox", "HD-ZIP"],
    "Dof": ["PF02701", "Dof"],
    "GATA": ["PF00320", "GATA"],
    "Trihelix": ["PF13837", "Trihelix"],
    "SPL_SBP": ["PF03110", "SBP"],
    "GRF": ["PF06850", "GRF"],
    "LBD": ["PF03195", "LBD"],
    "NF-Y": ["PF02045", "PF00808", "NF-Y", "CBF"],
    "E2F_DP": ["PF02319", "E2F"],
    "B3": ["PF02362", "B3"],
    "CPP": ["PF03641", "CPP"],
    "YABBY": ["PF04690", "YABBY"],
    "TALE": ["PF05920", "TALE"],
    "VOZ": ["PF07878", "VOZ"],
    "STAT": ["PF02864", "STAT"],
    "ZF_HD": ["PF04770", "ZF-HD"],
}


def parse_candidate_families(family_str):
    """Parse candidate_families column and return list of detected TF families"""
    if not family_str or family_str == "NA":
        return []
    family_str = family_str.upper()
    detected = []
    for tf_name, keywords in TF_FAMILIES.items():
        for kw in keywords:
            if kw.upper() in family_str:
                detected.append(tf_name)
                break
    return detected


def read_gene_list(filepath):
    """Read gene IDs from a file (one per line)"""
    genes = set()
    with open(filepath) as f:
        for line in f:
            gene = line.strip()
            if gene and not gene.startswith("gene_id"):
                genes.add(gene)
    return genes


def main():
    # Read annotation file - build gene → TF families mapping
    print("Reading annotation file...")
    gene_to_tf = defaultdict(set)
    tf_gene_count = Counter()

    with open(ANNO_FILE) as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            gene = row.get("gene_id", "")
            families_str = row.get("candidate_families", "")
            sig_str = row.get("signatures", "")
            domain_desc = row.get("domain_descriptions", "")

            # Combine all text fields for searching
            combined = f"{families_str} {sig_str} {domain_desc}"
            detected = parse_candidate_families(combined)
            if detected:
                gene_to_tf[gene].update(detected)
                for tf in detected:
                    tf_gene_count[tf] += 1

    print(f"  Total annotated genes with TF: {len(gene_to_tf)}")
    print(f"  Total TF assignments: {sum(tf_gene_count.values())}")

    # Read Venn gene lists
    print("\nReading Venn gene lists...")
    venn_categories = {}
    if os.path.isdir(VENN_DIR):
        for fname in sorted(os.listdir(VENN_DIR)):
            if fname.endswith(".txt"):
                key = fname.replace(".txt", "")
                venn_categories[key] = read_gene_list(os.path.join(VENN_DIR, fname))
                print(f"  {key}: {len(venn_categories[key])} genes")

    # Analyze TF families in each Venn category
    print("\n" + "=" * 80)
    print("TF FAMILY DISTRIBUTION ACROSS CROSS-STRESS CATEGORIES")
    print("=" * 80)

    results = {}

    # Also do the early Venn
    for analysis_name, venn_dir_base in [
        ("time_lrt_venn", "time_lrt_venn_gene_lists"),
        ("early_venn", "early_venn_gene_lists"),
    ]:
        venn_dir = os.path.join(OUTPUT, venn_dir_base)
        if not os.path.isdir(venn_dir):
            continue

        print(f"\n{'#' * 70}")
        print(f"  Analysis: {analysis_name}")
        print(f"{'#' * 70}")

        category_genes = {}
        for fname in sorted(os.listdir(venn_dir)):
            if fname.endswith(".txt"):
                key = fname.replace(".txt", "")
                category_genes[key] = read_gene_list(os.path.join(venn_dir, fname))

        # Count TF families in each category
        tf_counts = {}
        for cat_name, genes in category_genes.items():
            tf_in_cat = Counter()
            for gene in genes:
                if gene in gene_to_tf:
                    for tf in gene_to_tf[gene]:
                        tf_in_cat[tf] += 1
            tf_counts[cat_name] = tf_in_cat

        # Print as table
        categories_ordered = [
            "all_three",
            "salt_drought",
            "salt_heat",
            "drought_heat",
            "only_salt",
            "only_drought",
            "only_heat",
        ]

        # Header
        header = f"{'TF Family':<15}"
        for cat in categories_ordered:
            if cat in tf_counts:
                total = len(category_genes.get(cat, set()))
                header += f" {cat:<18}"
        print(header)
        print("-" * len(header))

        # TF families sorted by total prevalence
        all_tfs = sorted(TF_FAMILIES.keys())

        tf_row_data = {}
        for tf in all_tfs:
            row = f"{tf:<15}"
            row_data = {}
            for cat in categories_ordered:
                if cat in tf_counts:
                    count = tf_counts[cat].get(tf, 0)
                    total = len(category_genes.get(cat, set()))
                    pct = (count / total * 100) if total > 0 else 0
                    row += f" {count:<5}({pct:.1f}%)        "
                    row_data[cat] = count
            tf_row_data[tf] = row_data
            print(row)

        # Summary
        print(f"\n{'─' * 70}")
        print(f"{'Category':<25} {'Total Genes':<15} {'TF Genes':<15} {'TF %':<10}")
        print(f"{'─' * 70}")
        for cat in categories_ordered:
            if cat in tf_counts:
                total = len(category_genes.get(cat, set()))
                tf_genes = set()
                for gene in category_genes.get(cat, set()):
                    if gene in gene_to_tf:
                        tf_genes.add(gene)
                pct = len(tf_genes) / total * 100 if total > 0 else 0
                print(f"{cat:<25} {total:<15} {len(tf_genes):<15} {pct:<10.1f}%")

        # Save to file
        outfile = os.path.join(OUTPUT, f"{analysis_name}_tf_summary.txt")
        with open(outfile, "w") as f:
            f.write(f"TF Family Analysis: {analysis_name}\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"{'TF Family':<15}")
            for cat in categories_ordered:
                if cat in tf_counts:
                    f.write(f"\t{cat}")
            f.write("\n")
            for tf in all_tfs:
                f.write(f"{tf:<15}")
                for cat in categories_ordered:
                    if cat in tf_counts:
                        count = tf_counts[cat].get(tf, 0)
                        f.write(f"\t{count}")
                f.write("\n")
        print(f"\n  Saved: {outfile}")


if __name__ == "__main__":
    main()
