from __future__ import annotations

from typing import Any


CONFIG_HEADERS = [
    "Original_File_Name",
    "Species_Code",
    "Species_Name",
    "Gene_Family",
    "File_Type",
    "Ref",
    "Species_Role",
    "File_Scope",
    "Reference_Use",
]

CONFIG_TEMPLATE_ROWS = [
    ["Os.genome.fasta", "Os", "Oryza sativa", "", "genome", "FALSE", "target", "whole_genome", ""],
    ["Os.gff3", "Os", "Oryza sativa", "", "gff", "FALSE", "target", "whole_genome", ""],
    ["Os.cds.fasta", "Os", "Oryza sativa", "", "cds", "FALSE", "target", "whole_genome", ""],
    ["Os.pep.fasta", "Os", "Oryza sativa", "", "pep", "FALSE", "target", "whole_genome", ""],
    ["At.genome.fasta", "At", "Arabidopsis thaliana", "", "genome", "TRUE", "reference", "whole_genome", "downstream_primary"],
    ["At.gff3", "At", "Arabidopsis thaliana", "", "gff", "TRUE", "reference", "whole_genome", "downstream_primary"],
    ["At.cds.fasta", "At", "Arabidopsis thaliana", "", "cds", "TRUE", "reference", "whole_genome", "downstream_primary"],
    ["At.pep.fasta", "At", "Arabidopsis thaliana", "", "pep", "TRUE", "reference", "whole_genome", "downstream_primary"],
    ["Zm.gff3", "Zm", "Zea mays", "", "gff", "FALSE", "comparative", "whole_genome", ""],
    ["Zm.pep.fasta", "Zm", "Zea mays", "", "pep", "FALSE", "comparative", "whole_genome", ""],
    ["Vv.gff3", "Vv", "Vitis vinifera", "", "gff", "FALSE", "outgroup", "whole_genome", ""],
    ["Vv.pep.fasta", "Vv", "Vitis vinifera", "", "pep", "FALSE", "outgroup", "whole_genome", ""],
    ["At.WRKY.pep.fasta", "At", "Arabidopsis thaliana", "WRKY", "family_pep", "TRUE", "seed_reference", "known_family", "both"],
    ["Os.WRKY.pep.fasta", "Os", "Oryza sativa", "WRKY", "family_pep", "TRUE", "seed_reference", "known_family", "identify_blast"],
]

CONFIG_README_ROWS = [
    ["config.xlsx template"],
    ["Download this file, fill the Analysis sheet and config sheet, then upload it together with genome/GFF/CDS/protein files."],
    ["Use the Analysis sheet to declare the gene family once for the whole job. The config sheet keeps per-file metadata."],
    ["The first sheet named config is read by the pipeline. Do not put notes or merged cells in that sheet."],
    [],
    ["Column", "Chinese meaning", "English description", "Required", "Example / Allowed values"],
    ["Original_File_Name", "uploaded original file name", "Original uploaded file name; must match the uploaded file", "Yes", "At.WRKY.pep.fa"],
    ["Species_Code", "species short code", "Species code; keep consistent for one species", "Yes", "At, Os, Lc"],
    ["Species_Name", "species scientific name", "Optional Latin/scientific name used only as an alias; filenames and Analysis species fields still use Species_Code", "Optional", "Arabidopsis thaliana, Oryza sativa"],
    ["Gene_Family", "gene family name", "Optional for whole-genome rows; required for known-family seed rows (e.g. At.WRKY.pep.fasta)", "Optional", "WRKY, NAC, MYB; leave blank for whole-genome resources"],
    ["File_Type", "file type", "File type", "Yes", "genome, gff, cds, pep, family_pep, family_cds (legacy: all_pep, protein)"],
    ["Ref", "legacy reference flag", "Backward compatible flag. Prefer Species_Role for new projects.", "Recommended", "TRUE or FALSE"],
    ["Species_Role", "species role", "Biological role in the analysis", "Recommended", "target, reference, comparative, outgroup, seed_reference"],
    ["File_Scope", "file scope", "Whether the file is whole-genome resource or known family seed", "Recommended", "whole_genome, known_family"],
    ["Reference_Use", "reference usage", "Optional role for reference rows: identify BLAST evidence, downstream primary reference, or both", "Optional", "identify_blast, downstream_primary, both, none"],
    [],
    ["File_Type notes"],
    ["genome", "Genome FASTA"],
    ["gff", "GFF or GFF3 annotation file"],
    ["cds", "CDS FASTA"],
    ["pep", "Whole proteome; used for identify and MCScanX background"],
    ["all_pep or protein", "Legacy whole-proteome value; treated as pep"],
    ["family_pep", "Known family proteins; standardized as Species.Family.pep.fasta"],
    ["family_cds", "Known family CDS; standardized as Species.Family.cds.fasta"],
    ["pep", "Compatible value: whole_genome rows become Species.pep.fasta; known_family rows become Species.Family.pep.fasta"],
    [],
    ["Naming rule"],
    ["whole_genome files are standardized as Species.pep.fasta, Species.cds.fasta, Species.genome.fasta, Species_final.gff3"],
    ["known_family seed files are standardized as Species.Family.pep.fasta or Species.Family.cds.fasta"],
    [],
    ["Upload rule"],
    ["You may upload any .xlsx file name. The latest uploaded Excel file will be normalized to config.xlsx."],
    ["If you upload another Excel file later, it replaces the current config.xlsx for this job."],
    [],
    ["Filling guide (how to think about this table)"],
    ["1) Genome Prepare = species-level normalization (no family binding)"],
    ["- For target/reference/comparative/outgroup species, upload whole-genome resources and set File_Scope=whole_genome."],
    ["- Use Species_Role=reference for whole-genome resources of the reference species (for example Arabidopsis At)."],
    ["- Use Species_Role=seed_reference only for curated known family member FASTA/subfamily labels (File_Scope=known_family, Gene_Family required)."],
    ["2) Two analysis paths are both supported"],
    ["- Path A (known family): you already know the family (e.g. WRKY). Fill Analysis!Family=WRKY, use seed_reference (At.WRKY.pep.fa) + whole-genome pep of target species; then run identify with prefix like Multi.WRKY."],
    ["- Path B (unknown family): leave Gene_Family empty for whole-genome rows, run RNA-seq -> enrichment -> choose candidate family; then run identify using Species.pep.fasta."],
    ["3) Roles and what they mean"],
    ["- target: main species you study in the paper."],
    ["- reference: whole-genome resources for the reference species (background/reference proteome, GFF, CDS, genome)."],
    ["- comparative: additional species for cross-species comparison/synteny."],
    ["- outgroup: distant species for tree rooting or evolution context."],
    ["- seed_reference: curated known family members used as identify BLAST evidence and/or downstream reference context, depending on Reference_Use."],
    ["- Reference_Use=identify_blast: use this known-family FASTA only for identify reference-protein search."],
    ["- Reference_Use=downstream_primary: use this species as the default downstream reference, not as extra identify BLAST evidence unless separately listed."],
    ["- Reference_Use=both: use for both identify BLAST and downstream reference context."],
    ["4) Minimal rules to avoid mistakes"],
    ["- Do not put random notes/merged cells in the config sheet."],
    ["- Species_Code is the machine identity and filename prefix; do not put Latin names in Species_Code."],
    ["- Species_Name is optional and only maps Latin names back to Species_Code during literature/methodology extraction."],
    ["- Whole-genome rows: Gene_Family can be empty; file naming will be Species.*."],
    ["- Known-family rows: Gene_Family must be filled; naming will be Species.Family.*."],
    ["- Identify BLAST runs only when config declares a known-family/seed reference protein row; otherwise identify is HMM-only."],
    ["- Ref is legacy; Species_Role is recommended for new jobs."],
]


def config_template_examples() -> list[dict[str, Any]]:
    return [dict(zip(CONFIG_HEADERS, row)) for row in CONFIG_TEMPLATE_ROWS]


def build_config_template_workbook():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception as exc:
        raise RuntimeError(f"openpyxl is required to generate config.xlsx template: {exc}")

    wb = Workbook()
    ws = wb.active
    ws.title = "config"
    ws.append(CONFIG_HEADERS)
    for row in CONFIG_TEMPLATE_ROWS:
        ws.append(row)
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    widths = [28, 16, 28, 16, 18, 10, 18, 18, 22]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"

    analysis = wb.create_sheet("Analysis", 0)
    analysis_rows = [
        ["Key", "Value", "Description"],
        ["Family", "WRKY", "Gene family for this job. This is the preferred source when the user does not name a family."],
        ["Target_Species", "Os", "Optional comma-separated target species codes."],
        ["Reference_Species", "At", "Optional reference species code."],
        ["Comparative_Species", "Zm,Vv", "Optional comma-separated comparative/outgroup species codes."],
        ["Required_Pfams", "PF03106", "Optional Pfam accession(s) for identify."],
    ]
    for row in analysis_rows:
        analysis.append(row)
    for cell in analysis[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E2F0D9")
        cell.alignment = Alignment(horizontal="center")
    analysis.column_dimensions["A"].width = 24
    analysis.column_dimensions["B"].width = 22
    analysis.column_dimensions["C"].width = 76

    readme = wb.create_sheet("README")
    for row in CONFIG_README_ROWS:
        readme.append(row)
    readme["A1"].font = Font(bold=True, size=14)
    for cell in readme[5]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E2F0D9")
    readme.column_dimensions["A"].width = 24
    readme.column_dimensions["B"].width = 46
    readme.column_dimensions["C"].width = 52
    readme.column_dimensions["D"].width = 12
    readme.column_dimensions["E"].width = 34
    for row in readme.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    return wb
