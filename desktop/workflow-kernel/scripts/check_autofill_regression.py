#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cli.autofill_common import scan_files
from cli.run_config_autofill import build_rows, merge_rows


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_at_spl_seed_reference() -> None:
    with tempfile.TemporaryDirectory(prefix="bbav5_autofill_at_") as tmp:
        input_dir = Path(tmp) / "input"
        input_dir.mkdir()
        (input_dir / "At.SPL.pep.fa").write_text(">AT1G01010\nMAAA\n", encoding="utf-8")
        rows = build_rows(input_dir, input_dir, "", "", "SPL")
        row = next((item for item in rows if item["Original_File_Name"] == "At.SPL.pep.fa"), None)
        require(row is not None, "At.SPL.pep.fa was not detected from current job input")
        expected = {
            "Species_Code": "At",
            "Gene_Family": "SPL",
            "File_Type": "pep",
            "Ref": "TRUE",
            "Species_Role": "seed_reference",
            "File_Scope": "known_family",
            "confidence": "high",
        }
        for key, value in expected.items():
            require(row.get(key) == value, f"At.SPL.pep.fa {key} expected {value!r}, got {row.get(key)!r}")


def check_scan_filters_generated_dirs() -> None:
    with tempfile.TemporaryDirectory(prefix="bbav5_autofill_scan_") as tmp:
        input_dir = Path(tmp) / "input"
        input_dir.mkdir()
        (input_dir / "At.SPL.pep.fa").write_text(">AT\nMA\n", encoding="utf-8")
        for subdir in ["final", "uploaded_configs"]:
            (input_dir / subdir).mkdir()
            (input_dir / subdir / "At.SPL.pep.fa").write_text(">skip\nMA\n", encoding="utf-8")
        scanned = [path.relative_to(input_dir).as_posix() for path in scan_files(input_dir)]
        require(scanned == ["At.SPL.pep.fa"], f"scan_files should only keep current input file, got {scanned}")


def check_merge_keeps_existing_and_adds_new_upload() -> None:
    with tempfile.TemporaryDirectory(prefix="bbav5_autofill_merge_") as tmp:
        input_dir = Path(tmp) / "input"
        input_dir.mkdir()
        (input_dir / "At.SPL.pep.fa").write_text(">AT1G01010\nMAAA\n", encoding="utf-8")
        existing_rows = [{
            "Original_File_Name": "Arabidopsis_thaliana.TAIR10.pep.all.fa",
            "Species_Code": "At",
            "Gene_Family": "",
            "File_Type": "pep",
            "Ref": "TRUE",
            "Species_Role": "seed_reference",
            "File_Scope": "known_family",
            "confidence": "high",
            "notes": "",
        }]
        merged = merge_rows(existing_rows, build_rows(input_dir, input_dir, "", "", "SPL"))
        names = [row["Original_File_Name"] for row in merged]
        require("Arabidopsis_thaliana.TAIR10.pep.all.fa" in names, "existing draft row was dropped")
        require("At.SPL.pep.fa" in names, "new uploaded At.SPL.pep.fa was not merged into draft")
        require(len(names) == len(set(names)), f"merged rows contain duplicates: {names}")


def check_frontend_entrypoint_contract() -> None:
    html = (ROOT / "apps/web/vue3/index.html").read_text(encoding="utf-8")
    queue_start = html.index("const queueAutofillFromPayload")
    queue_end = html.index("const handleUploadSuccess", queue_start)
    queue_block = html[queue_start:queue_end]
    require(queue_block.count("sourceDir: ''") >= 2, "upload autofill must force blank sourceDir for config and samples")
    require("await openAutofillDialog('config'" in queue_block, "upload config autofill entrypoint missing")
    require("await openAutofillDialog('samples'" in queue_block, "upload samples autofill entrypoint missing")
    require("const openExistingAutofillDraft" in html, "natural-language open must load existing autofill draft")
    require(
        "options.keepCurrentRows && !(autofillState.rows && autofillState.rows.length)" in html,
        "open config/samples must fetch existing rows when frontend state is empty",
    )
    require("autoGenerate: wantsAutofillRegenerate" in html, "open and regenerate natural-language intents must stay separate")


def check_backend_open_contract() -> None:
    main_py = (ROOT / "apps/web/vue3/main.py").read_text(encoding="utf-8")
    expected = [
        '@app.post("/api/autofill/config/open")',
        'workspace_dir / "final" / "config.autofill.draft.xlsx"',
        'input_dir / "config.xlsx"',
        '@app.post("/api/autofill/samples/open")',
        'workspace_dir / "final" / "samples.autofill.draft.yaml"',
        'input_dir / "samples.yaml"',
    ]
    for text in expected:
        require(text in main_py, f"backend open contract missing: {text}")


def main() -> int:
    checks = [
        check_at_spl_seed_reference,
        check_scan_filters_generated_dirs,
        check_merge_keeps_existing_and_adds_new_upload,
        check_frontend_entrypoint_contract,
        check_backend_open_contract,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print("PASS autofill regression checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
