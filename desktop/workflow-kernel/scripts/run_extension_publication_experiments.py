#!/usr/bin/env python3
"""Publication-grade extension-stage experiments for bbav5."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_ID = "job_1781621211960_ed6af0"
DEFAULT_JOB_ROOT = PROJECT_ROOT / "workspace" / DEFAULT_JOB_ID
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "拓展分析阶段_发表级完整实验_20260616"


@dataclass(frozen=True)
class PubCase:
    index: int
    archetype_id: str
    stage: str
    script_name: str
    script_content: str
    source_files: tuple[tuple[str, str], ...]
    required_outputs: tuple[str, ...]
    description: str

    @property
    def dir_name(self) -> str:
        return f"{self.index:02d}_{self.archetype_id}"


def _sh(script: str) -> str:
    return "#!/usr/bin/env bash\nset -euo pipefail\npython3 - <<'PY'\n" + script + "\nPY\n"


SCRIPT_SEQUENCE = """
from pathlib import Path
ids = {line.strip().split()[0] for line in Path('ids.id').read_text().splitlines() if line.strip()}
out=[]; keep=False
for line in Path('input.fa').read_text().splitlines():
    if line.startswith('>'):
        gid=line[1:].split()[0]
        keep=gid in ids
    if keep:
        out.append(line)
Path('extracted.fa').write_text('\\n'.join(out)+'\\n')
Path('extracted.id').write_text('\\n'.join(sorted(ids))+'\\n')
"""
SCRIPT_HMMER = """
from pathlib import Path
rows=[]
for line in Path('input.domtblout').read_text(errors='ignore').splitlines():
    if line and not line.startswith('#'):
        rows.append(line)
Path('filtered.domtblout').write_text('\\n'.join(rows)+'\\n')
seqs=sum(1 for line in Path('input.fa').read_text(errors='ignore').splitlines() if line.startswith('>'))
Path('domain_input_summary.tsv').write_text(f'sequence_count\\t{seqs}\\ndomain_rows\\t{len(rows)}\\n')
"""
SCRIPT_MOTIF = """
from pathlib import Path
import re
Path('meme.xml').write_text(Path('input.meme.xml').read_text(errors='ignore'))
Path('meme.txt').write_text(Path('input.meme.txt').read_text(errors='ignore'))
text=Path('input.meme.txt').read_text(errors='ignore')
motifs=len(re.findall(r'^MOTIF\\s+', text, flags=re.M))
seqs=sum(1 for line in Path('input.fa').read_text(errors='ignore').splitlines() if line.startswith('>'))
Path('motif_input_summary.tsv').write_text(f'sequence_count\\t{seqs}\\nmotif_count\\t{motifs}\\n')
"""
SCRIPT_PHYLOGENY = """
from pathlib import Path
msa=Path('input.msa.fa').read_text(errors='ignore')
tree=Path('tree.nwk').read_text(errors='ignore').strip()
Path('output.msa.fa').write_text(msa)
Path('output.nwk').write_text(tree+'\\n')
seqs=sum(1 for line in msa.splitlines() if line.startswith('>'))
tips=tree.count(':') if tree else 0
Path('phylogeny_summary.tsv').write_text(f'alignment_sequence_count\\t{seqs}\\ntree_tip_like_count\\t{tips}\\n')
"""
SCRIPT_REFCLS = """
from pathlib import Path
for src,dst in [('assignment.tsv','output.assignment.tsv'),('subfamily.tsv','output.subfamily.tsv'),('confidence.tsv','output.confidence.tsv')]:
    p=Path(src)
    if p.exists():
        Path(dst).write_text(p.read_text(errors='ignore'))
rows=sum(1 for line in Path('assignment.tsv').read_text(errors='ignore').splitlines() if line.strip())
Path('reference_classification_summary.tsv').write_text(f'assignment_rows\\t{rows}\\n')
"""
SCRIPT_SYNTENY = """
from pathlib import Path
Path('output.blast').write_text(Path('links.blast').read_text(errors='ignore'))
Path('output.collinearity').write_text(Path('links.collinearity').read_text(errors='ignore'))
blast_rows=sum(1 for line in Path('links.blast').read_text(errors='ignore').splitlines() if line.strip())
q=sum(1 for line in Path('query.fa').read_text(errors='ignore').splitlines() if line.startswith('>'))
r=sum(1 for line in Path('reference.fa').read_text(errors='ignore').splitlines() if line.startswith('>'))
Path('synteny_summary.tsv').write_text(f'query_sequence_count\\t{q}\\nreference_sequence_count\\t{r}\\nblast_rows\\t{blast_rows}\\n')
"""
SCRIPT_KAKS = """
from pathlib import Path
text=Path('kaks_results.csv').read_text(errors='ignore')
Path('output.kaks_results.csv').write_text(text)
rows=[x for x in text.splitlines() if x.strip()]
Path('kaks_summary.csv').write_text(f'kaks_rows,{len(rows)}\\n')
"""
SCRIPT_PEP = """
from pathlib import Path
src=Path('properties.tsv')
Path('output.properties.tsv').write_text(src.read_text(errors='ignore'))
if Path('subcellular.tsv').exists():
    Path('output.subcellular.tsv').write_text(Path('subcellular.tsv').read_text(errors='ignore'))
seqs=sum(1 for line in Path('input.fa').read_text(errors='ignore').splitlines() if line.startswith('>'))
rows=sum(1 for line in src.read_text(errors='ignore').splitlines() if line.strip())
Path('pep_properties_summary.tsv').write_text(f'protein_count\\t{seqs}\\nproperty_rows\\t{rows}\\n')
"""


def cases(job_root: Path) -> tuple[PubCase, ...]:
    return (
        PubCase(1, "sequence_extraction", "identify", "run_sequence_extraction.sh", _sh(SCRIPT_SEQUENCE), (("final/Ft.SPL.curated_members.pep.fa", "input.fa"), ("final/Ft.SPL.curated_members.id", "ids.id")), ("extracted.fa", "extracted.id"), "Extract Ft.SPL protein FASTA records by curated member IDs."),
        PubCase(2, "hmmer_domain", "domain", "run_hmmer_domain.sh", _sh(SCRIPT_HMMER), (("domain/analysis/Ft.SPL.results.domtblout", "input.domtblout"), ("final/Ft.SPL.curated_members.pep.fa", "input.fa")), ("filtered.domtblout", "domain_input_summary.tsv"), "Parse real HMMER domtblout rows and summarize the matching FASTA input."),
        PubCase(3, "motif_meme", "motif", "run_motif_meme.sh", _sh(SCRIPT_MOTIF), (("final/Ft.SPL.meme.xml", "input.meme.xml"), ("final/Ft.SPL.meme.txt", "input.meme.txt"), ("final/Ft.SPL.curated_members.pep.fa", "input.fa")), ("meme.xml", "meme.txt", "motif_input_summary.tsv"), "Preserve and summarize real MEME motif outputs."),
        PubCase(4, "phylogeny_tree", "phylogeny", "run_phylogeny_tree.sh", _sh(SCRIPT_PHYLOGENY), (("phylogeny/analysis/Ft.SPL.pep.mafft.msa.fa", "input.msa.fa"), ("final/Ft.SPL.tree.nwk", "tree.nwk")), ("output.msa.fa", "output.nwk", "phylogeny_summary.tsv"), "Preserve real MSA and Newick tree with structural summary."),
        PubCase(5, "reference_classification", "reference_classification", "run_reference_classification.sh", _sh(SCRIPT_REFCLS), (("final/Ft.SPL.target_plus_reference.reference_classification.assignment.tsv", "assignment.tsv"), ("final/Ft.SPL.target_plus_reference.reference_classification.subfamily.tsv", "subfamily.tsv"), ("final/Ft.SPL.target_plus_reference.reference_classification.confidence.tsv", "confidence.tsv")), ("output.assignment.tsv", "output.subfamily.tsv", "reference_classification_summary.tsv"), "Normalize real reference-based subfamily classification tables."),
        PubCase(6, "synteny_collinearity", "interspecies_synteny", "run_synteny_collinearity.sh", _sh(SCRIPT_SYNTENY), (("interspecies_synteny/analysis/Ft.SPL.selected_target.family.pep.fa", "query.fa"), ("interspecies_synteny/analysis/Ft.SPL.selected_ref.family.pep.fa", "reference.fa"), ("interspecies_synteny/analysis/Ft.At.blast", "links.blast"), ("interspecies_synteny/analysis/Ft.At.collinearity", "links.collinearity")), ("output.blast", "output.collinearity", "synteny_summary.tsv"), "Preserve real BLAST and collinearity evidence with query/reference roles."),
        PubCase(7, "kaks_pairwise", "kaks", "run_kaks_pairwise.sh", _sh(SCRIPT_KAKS), (("final/Ft.SPL.Ft_to_At.kaks_results.csv", "kaks_results.csv"),), ("output.kaks_results.csv", "kaks_summary.csv"), "Normalize real pairwise Ka/Ks result table."),
        PubCase(8, "pep_properties", "pep", "run_pep_properties.sh", _sh(SCRIPT_PEP), (("final/Ft.SPL.curated_members.pep.fa", "input.fa"), ("final/properties_data.tsv", "properties.tsv"), ("pep/analysis/tm_summary.tsv", "subcellular.tsv")), ("output.properties.tsv", "pep_properties_summary.tsv"), "Preserve real protein property and topology summary tables."),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--negative-controls", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--job-id", default=DEFAULT_JOB_ID)
    ap.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT)
    ap.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ap.add_argument("--converter", default="archetype_spec", choices=["archetype_spec", "deepseek_agent", "deepseek_api", "local_rule", "rule"])
    ap.add_argument("--runtime-env", default="test4py311")
    ap.add_argument("--stop-on-failure", action="store_true")
    args = ap.parse_args()
    if not any((args.prepare, args.run, args.negative_controls, args.report, args.all)):
        args.all = True
    out = args.output_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.prepare or args.all:
        prepare(args.job_root.resolve(), out)
    if args.run or args.all:
        run_batch(out, args.converter, args.runtime_env, args.stop_on_failure)
    if args.negative_controls or args.all:
        run_negative_controls(out, args.runtime_env)
    if args.report or args.all:
        write_report(out, args.job_id, args.job_root.resolve())
    write_manifest(out)
    return 0


def prepare(job_root: Path, out: Path) -> None:
    if not job_root.exists():
        raise FileNotFoundError(f"job root not found: {job_root}")
    packages = out / "packages"
    logs = out / "logs"
    packages.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases(job_root):
        root = packages / case.dir_name
        raw_code, raw_data, expected = root / "raw_code", root / "raw_test_data", root / "expected_outputs"
        for p in (raw_code, raw_data, expected, root / "_raw_run_work"):
            clean_dir(p)
        script = raw_code / case.script_name
        script.write_text(case.script_content, encoding="utf-8")
        script.chmod(0o755)
        write_json(raw_code / "bbav5_extension_hint.json", extension_hint(case))
        copied = []
        for rel, dest in case.source_files:
            src = job_root / rel
            if not src.exists():
                raise FileNotFoundError(f"missing source for {case.archetype_id}: {src}")
            shutil.copy2(src, raw_data / dest)
            copied.append({"source": str(src), "dest": dest, "size": src.stat().st_size, "sha256": sha256(src)})
        raw = run_raw(root, case, logs / f"{case.dir_name}.raw.log")
        ok = raw["success"] and all((expected / x).is_file() and (expected / x).stat().st_size > 0 for x in case.required_outputs)
        rec = {"archetype_id": case.archetype_id, "stage": case.stage, "case_dir": str(root), "source_files": copied, "raw_code_ok": raw["success"], "expected_output_ok": ok, "required_outputs": list(case.required_outputs), "raw_run": raw}
        rows.append(rec)
        write_json(root / "prepare_record.json", rec)
        (root / "README.md").write_text(case_readme(case, copied), encoding="utf-8")
    write_json(out / "prepare_manifest.json", {"schema_version": "extension_publication_prepare_v1", "created_at": now(), "job_root": str(job_root), "case_count": len(rows), "prepared": sum(1 for r in rows if r["expected_output_ok"]), "cases": rows})


def run_raw(root: Path, case: PubCase, log_path: Path) -> dict[str, Any]:
    work, expected = root / "_raw_run_work", root / "expected_outputs"
    copy_tree(root / "raw_test_data", work)
    copy_tree(root / "raw_code", work)
    before = file_set(work)
    proc = subprocess.run(["bash", case.script_name], cwd=work, capture_output=True, text=True, timeout=240)
    after = file_set(work)
    new_files = sorted(after - before)
    for rel in new_files:
        src = work / rel
        if src.is_file():
            (expected / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, expected / rel)
    log_path.write_text(f"$ bash {case.script_name}\n\n[stdout]\n{proc.stdout}\n\n[stderr]\n{proc.stderr}\n", encoding="utf-8")
    return {"success": proc.returncode == 0, "returncode": proc.returncode, "new_files": new_files, "stderr_tail": proc.stderr[-2000:]}


def run_batch(out: Path, converter: str, runtime_env: str, stop_on_failure: bool) -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager
    effective = "deepseek_api" if converter in {"archetype_spec", "deepseek_agent"} else converter
    os.environ["BBAV5_EXTENSION_CONVERTER"] = effective
    if converter == "archetype_spec":
        import gfa_core.extension_stage.agent as agent_module
        agent_module.convert_with_deepseek_api = lambda **kw: local_conversion_spec(kw["display_name"], kw["runtime_env"])
        agent_module.repair_build_with_deepseek_api = lambda **kw: local_conversion_spec(str(kw.get("display_name") or ""), str(kw.get("runtime_env") or runtime_env))
    run_root = out / "runtime" / converter
    for p in (run_root / "extension_stages", run_root / "workspace", run_root / "input", out / "results" / converter):
        clean_dir(p)
    manager = ExtensionStageManager(PROJECT_ROOT, store_root=run_root / "extension_stages", workspace_root=run_root / "workspace", input_root=run_root / "input")
    agent = ExtensionStageAgent(manager)
    rows = []
    for case in cases(DEFAULT_JOB_ROOT):
        row = run_one(agent, out, case, converter, runtime_env)
        rows.append(row)
        if stop_on_failure and not row_success(row):
            break
    summary = {"schema_version": "extension_publication_batch_result_v1", "created_at": now(), "converter": converter, "runtime_env": runtime_env, "case_count": len(rows), "passed": sum(1 for r in rows if row_success(r)), "failed": sum(1 for r in rows if not row_success(r)), "rows": rows}
    res = out / "results" / converter
    write_json(res / "summary.json", summary)
    write_csv(res / "summary.csv", rows)
    (res / "summary.md").write_text(summary_md(summary), encoding="utf-8")


def run_one(agent: Any, out: Path, case: PubCase, converter: str, runtime_env: str) -> dict[str, Any]:
    case_root = out / "packages" / case.dir_name
    res_dir = out / "results" / converter / "per_archetype" / case.dir_name
    clean_dir(res_dir)
    row = {
        "archetype_id": case.archetype_id,
        "stage": case.stage,
        "converter": converter,
        "raw_code_ok": (case_root / "prepare_record.json").exists(),
        "expected_output_ok": expected_ok(case_root, case),
        "build_success": False,
        "extension_id": "",
        "trial_success": False,
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_success": False,
        "output_count": 0,
        "api_attempt_count": 0,
        "build_api_attempt_count": 0,
        "trial_repair_attempt_count": 0,
        "validation_repair_attempt_count": 0,
        "publish_repair_attempt_count": 0,
        "total_api_repair_attempt_count": 0,
        "final_validation_status": "",
        "final_publish_status": "",
        "last_repair_phase": "",
        "last_repair_error": "",
        "sanitizer_action_count": 0,
        "error": "",
    }
    try:
        build = agent.create_from_uploads(display_name=f"publication_{case.archetype_id}", command=f"bash {case.script_name}", runtime_env=runtime_env, code_files=upload_files(case_root / "raw_code"), data_files=upload_files(case_root / "raw_test_data"), description=case.description)
        write_json(res_dir / "build_response.json", build)
        ext = build.get("extension") or {}
        eid = str(ext.get("extension_id") or "")
        row.update({"build_success": bool(build.get("success") and eid), "extension_id": eid, "api_attempt_count": len(ext.get("build_attempts") or []), "build_api_attempt_count": len(ext.get("build_attempts") or []), "sanitizer_action_count": len(ext.get("sanitizer_actions") or [])})
        trial = agent.trial_run(eid)
        write_json(res_dir / "trial_run.json", trial)
        if not trial.get("success"):
            repair = agent.repair_from_context(eid, phase="trial", run_context=trial.get("run") or {}, max_attempts=20, auto_publish=False)
            write_json(res_dir / "trial_repair_response.json", repair)
            row["trial_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                trial = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
            else:
                row["last_repair_phase"] = "trial"
                row["last_repair_error"] = str(repair.get("last_error") or "")
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len((trial.get("run") or {}).get("outputs") or [])
        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(eid, outputs=outputs, nl_triggers=[case.archetype_id, case.stage])
        write_json(res_dir / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        val = agent.validate(eid)
        write_json(res_dir / "validate_response.json", val)
        run = val.get("run") or {}
        gate = run.get("template_gate") or {}
        golden = run.get("golden_comparison") or {}
        row["validate_success"] = bool(val.get("success"))
        row["golden_status"] = str(golden.get("status") or "")
        row["template_gate_status"] = str(gate.get("status") or "")
        row["final_validation_status"] = str(run.get("status") or "")
        if not val.get("success"):
            repair = agent.repair_from_context(eid, phase="validation", run_context=run, max_attempts=20, auto_publish=False)
            write_json(res_dir / "validation_repair_response.json", repair)
            row["validation_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                val = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
                run = val.get("run") or {}
                gate = run.get("template_gate") or {}
                golden = run.get("golden_comparison") or {}
                row["validate_success"] = True
                row["golden_status"] = str(golden.get("status") or "")
                row["template_gate_status"] = str(gate.get("status") or "")
                row["final_validation_status"] = str(run.get("status") or "")
            else:
                row["last_repair_phase"] = "validation"
                row["last_repair_error"] = str(repair.get("last_error") or "")
        try:
            pub = agent.publish(eid)
        except Exception as pub_exc:
            publish_context = {
                "repair_phase": "publish_gate",
                "status": "failed",
                "error": str(pub_exc),
                "publish_checks": (agent.load(eid).get("publish_checks") or {}) if hasattr(agent, "load") else {},
                "last_validation": run,
                "golden_comparison": golden,
                "template_gate": gate,
            }
            repair = agent.repair_from_context(eid, phase="publish_gate", run_context=publish_context, max_attempts=20, auto_publish=True)
            write_json(res_dir / "publish_repair_response.json", repair)
            row["publish_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success") and repair.get("published"):
                pub = {"success": True, "extension": repair.get("extension") or {}}
            else:
                row["last_repair_phase"] = "publish_gate"
                row["last_repair_error"] = str(repair.get("last_error") or str(pub_exc))
                raise pub_exc
        write_json(res_dir / "publish_response.json", pub)
        row["publish_success"] = bool(pub.get("success"))
        row["final_publish_status"] = str((pub.get("extension") or {}).get("status") or "")
    except Exception as exc:
        row["error"] = str(exc)
        write_json(res_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc(), "row": row})
    row["total_api_repair_attempt_count"] = int(row.get("trial_repair_attempt_count") or 0) + int(row.get("validation_repair_attempt_count") or 0) + int(row.get("publish_repair_attempt_count") or 0)
    write_json(res_dir / "result_row.json", row)
    return row


def run_negative_controls(out: Path, runtime_env: str) -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager
    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    import gfa_core.extension_stage.agent as agent_module
    neg_root = out / "negative_cases"
    clean_dir(neg_root)
    base = out / "packages" / "01_sequence_extraction"

    def spec_for(scenario: str) -> dict[str, Any]:
        spec = local_conversion_spec("publication_sequence_extraction", runtime_env)
        if scenario == "unknown_archetype":
            spec["archetype_id"] = "unknown_publication_archetype"
        elif scenario == "missing_output_contract":
            spec["output_contract"] = [{"name_pattern": "not_present.xyz", "type": "file", "primary": True, "required": True, "allow_empty": False, "semantic_role": "wrong_output", "compare_mode": "auto", "required_for_golden": False}]
            spec["outputs"] = {"directory": "outputs", "patterns": ["not_present.xyz"]}
            spec["comparator_plan"] = {"mode": "pipeline_golden_diff", "steps": []}
        elif scenario == "wrong_input_role":
            spec["input_roles"] = {"input_file": {"role": "generic_input_file", "required": True}}
            spec["inputs"] = {"input_file": {"required": True}}
        return spec

    scenarios = [
        {"name": "missing_id_file", "mutate": lambda d: (d / "raw_test_data" / "ids.id").unlink(), "spec": "normal"},
        {"name": "unknown_archetype", "mutate": lambda d: None, "spec": "unknown_archetype"},
        {"name": "missing_output_contract", "mutate": lambda d: None, "spec": "missing_output_contract"},
        {"name": "wrong_input_role", "mutate": lambda d: None, "spec": "wrong_input_role"},
    ]
    manager = ExtensionStageManager(PROJECT_ROOT, store_root=neg_root / "extension_stages", workspace_root=neg_root / "workspace", input_root=neg_root / "input")
    agent = ExtensionStageAgent(manager)
    rows = []
    for item in scenarios:
        name = item["name"]
        d = neg_root / name / "package"
        copy_tree(base, d)
        item["mutate"](d)
        for path in sorted((d / "raw_code").glob("*.sh")):
            path.chmod(0o755)
        scenario_spec = str(item["spec"])
        agent_module.convert_with_deepseek_api = lambda **kw: local_conversion_spec(kw["display_name"], kw["runtime_env"]) if scenario_spec == "normal" else spec_for(scenario_spec)
        agent_module.repair_build_with_deepseek_api = lambda **kw: local_conversion_spec(str(kw.get("display_name") or ""), str(kw.get("runtime_env") or runtime_env)) if scenario_spec == "normal" else spec_for(scenario_spec)
        row = {"scenario": name, "blocked": False, "phase": "", "error": "", "published": False}
        try:
            build = agent.create_from_uploads(display_name=f"publication_sequence_extraction_{name}", command="bash run_sequence_extraction.sh", runtime_env=runtime_env, code_files=upload_files(d / "raw_code"), data_files=upload_files(d / "raw_test_data"))
            eid = (build.get("extension") or {}).get("extension_id")
            trial = agent.trial_run(eid)
            agent.confirm(eid, outputs=(trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or [], nl_triggers=["sequence_extraction"])
            agent.validate(eid)
            published = agent.publish(eid)
            row["published"] = bool(published.get("success"))
        except Exception as exc:
            row.update({"blocked": True, "phase": "build/trial/validate/publish", "error": str(exc)})
        rows.append(row)
    write_json(neg_root / "negative_control_summary.json", {"schema_version": "extension_publication_negative_controls_v1", "created_at": now(), "case_count": len(rows), "blocked": sum(1 for r in rows if r["blocked"] and not r["published"]), "rows": rows})
    write_csv(neg_root / "negative_control_summary.csv", rows)


def extension_hint(case: PubCase) -> dict[str, Any]:
    roles = {
        "sequence_extraction": {"input_pep_fasta": {"role": "target_pep_fasta", "required": True}, "input_id_file": {"role": "target_id_list", "required": True}},
        "hmmer_domain": {"input_fasta": {"role": "target_pep_fasta", "required": True}, "input_file": {"role": "generic_input_file", "required": True}},
        "motif_meme": {"input_fasta": {"role": "target_pep_fasta", "required": True}},
        "phylogeny_tree": {"input_fasta": {"role": "target_pep_fasta", "required": True}},
        "reference_classification": {"input_file": {"role": "generic_input_file", "required": True}},
        "synteny_collinearity": {"input_query_fasta": {"role": "target_pep_fasta", "required": True}, "input_reference_fasta": {"role": "reference_pep_fasta", "required": True}, "input_file": {"role": "generic_input_file", "required": True}},
        "kaks_pairwise": {"input_file": {"role": "generic_input_file", "required": True}},
        "pep_properties": {"input_fasta": {"role": "target_pep_fasta", "required": True}, "input_file": {"role": "generic_input_file", "required": False}},
    }[case.archetype_id]
    semantic_roles = {
        "sequence_extraction": {"extracted.fa": "extracted_fasta", "extracted.id": "id_list"},
        "hmmer_domain": {"filtered.domtblout": "hmmer_domtblout"},
        "motif_meme": {"meme.xml": "meme_xml", "meme.txt": "meme_txt"},
        "phylogeny_tree": {"output.msa.fa": "multiple_sequence_alignment", "output.nwk": "newick_tree"},
        "reference_classification": {"output.assignment.tsv": "assignment_table", "output.subfamily.tsv": "subfamily_table", "output.confidence.tsv": "confidence_table"},
        "synteny_collinearity": {"output.blast": "blast_outfmt6", "output.collinearity": "collinearity"},
        "kaks_pairwise": {"output.kaks_results.csv": "kaks_results"},
        "pep_properties": {"output.properties.tsv": "protein_properties", "output.subcellular.tsv": "subcellular_table"},
    }.get(case.archetype_id, {})
    contracts = [{"name_pattern": name, "type": out_type(name), "primary": i == 0, "required": True, "allow_empty": False, "semantic_role": semantic_roles.get(name, case.archetype_id + "_output"), "compare_mode": "auto", "required_for_golden": True} for i, name in enumerate(case.required_outputs)]
    return {"schema_version": "bbav5_publication_extension_hint_v1", "archetype_id": case.archetype_id, "representative_stage": case.stage, "recommended_original_command": f"bash {case.script_name}", "analysis_type": analysis_type(case.archetype_id), "tool_family": "python", "input_roles": roles, "output_contract": contracts, "comparator_plan": {"mode": "pipeline_golden_diff", "default_compare": "structured", "steps": [{"name_pattern": x, "compare_mode": "auto", "required": True} for x in case.required_outputs]}, "requires_user_confirmation": False}


def local_conversion_spec(display_name: str, runtime_env: str) -> dict[str, Any]:
    case = case_from_display(display_name)
    hint = extension_hint(case)
    return {"template_type": "publication_archetype_spec", "archetype_id": case.archetype_id, "original_command": f"bash {case.script_name}", "command_template": "bash {code_dir}/" + case.script_name + " --input-dir {input_dir} --output-dir {output_dir}", "semantic_command_template": "", "wrapper_command_template": "bash {code_dir}/" + case.script_name + " --input-dir {input_dir} --output-dir {output_dir}", "analysis_type": hint["analysis_type"], "tool_family": hint["tool_family"], "input_roles": hint["input_roles"], "inputs": {k: {"required": v.get("required", True)} for k, v in hint["input_roles"].items()}, "parameters": {}, "outputs": {"directory": "outputs", "patterns": list(case.required_outputs)}, "output_contract": hint["output_contract"], "comparator_plan": hint["comparator_plan"], "requires_user_confirmation": False, "notes": "Publication experiment deterministic archetype spec; validates project-derived executable code and real job data.", "converter": {"type": "archetype_spec", "mode": "publication_reproducible", "runtime_env": runtime_env}}


def write_report(out: Path, job_id: str, job_root: Path) -> None:
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    summaries = {}
    for conv in ("archetype_spec", "deepseek_agent", "deepseek_api"):
        p = out / "results" / conv / "summary.json"
        if p.exists():
            summaries[conv] = json.loads(p.read_text(encoding="utf-8"))
    neg = {}
    if (out / "negative_cases" / "negative_control_summary.json").exists():
        neg = json.loads((out / "negative_cases" / "negative_control_summary.json").read_text(encoding="utf-8"))
    write_json(out / "publication_metrics.json", {"schema_version": "extension_publication_metrics_v1", "created_at": now(), "job_id": job_id, "job_root": str(job_root), "summaries": summaries, "negative_controls": neg})
    for conv, data in summaries.items():
        write_csv(out / "tables" / f"table_{conv}_results.csv", data.get("rows") or [])
    if neg:
        write_csv(out / "tables" / "table_negative_controls.csv", neg.get("rows") or [])
    write_figures(out, summaries, neg)
    (out / "summary_report.html").write_text(render_html(job_id, job_root, summaries, neg), encoding="utf-8")


def write_figures(out: Path, summaries: dict[str, Any], neg: dict[str, Any]) -> None:
    rows = [(conv, int(data.get("passed") or 0), int(data.get("failed") or 0)) for conv, data in summaries.items()] or [("no_run", 0, 0)]
    width, height, x, barw, gap = 720, 220, 80, 70, 80
    maxv = max([p + f for _, p, f in rows] + [1])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"><style>.t{{font:13px Arial}}.h{{font:700 14px Arial}}</style><text x="20" y="25" class="h">Extension-stage publication experiment pass/fail counts</text>']
    for i, (name, p, f) in enumerate(rows):
        bx = x + i * (barw + gap)
        ph = int(140 * p / maxv)
        fh = int(140 * f / maxv)
        parts.append(f'<rect x="{bx}" y="{170 - ph}" width="{barw}" height="{ph}" fill="#2f7d55"/><rect x="{bx}" y="{170 - ph - fh}" width="{barw}" height="{fh}" fill="#b33a3a"/><text x="{bx + barw/2}" y="190" text-anchor="middle" class="t">{html.escape(name)}</text><text x="{bx + barw/2}" y="{160 - ph - fh}" text-anchor="middle" class="t">P{p}/F{f}</text>')
    parts.append("</svg>")
    (out / "figures" / "figure_pass_fail.svg").write_text("\n".join(parts), encoding="utf-8")


def render_html(job_id: str, job_root: Path, summaries: dict[str, Any], neg: dict[str, Any]) -> str:
    cards = []
    for conv, data in summaries.items():
        cards.append(f"<div class='metric'><b>{data.get('passed')}/{data.get('case_count')}</b><span>{html.escape(conv)} passed</span></div>")
    if neg:
        cards.append(f"<div class='metric'><b>{neg.get('blocked')}/{neg.get('case_count')}</b><span>negative controls blocked</span></div>")
    rows = "".join(f"<tr><td>{html.escape(conv)}</td><td>{d.get('case_count')}</td><td>{d.get('passed')}</td><td>{d.get('failed')}</td></tr>" for conv, d in summaries.items())
    negrows = "".join(f"<tr><td>{html.escape(str(r.get('scenario','')))}</td><td>{r.get('blocked')}</td><td>{r.get('published')}</td><td>{html.escape(str(r.get('error',''))[:240])}</td></tr>" for r in (neg.get("rows") or []))
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>拓展分析阶段发表级完整实验结果</title><style>body{{font:14px/1.65 Arial,'Microsoft YaHei',sans-serif;color:#18212f;margin:0}}main{{max-width:1160px;margin:auto;padding:28px}}h1{{font-size:28px}}h2{{border-left:5px solid #2666a8;padding-left:10px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #d7dde7;padding:8px;vertical-align:top}}th{{background:#edf2f8}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.metric{{border:1px solid #d7dde7;background:#f7f9fc;padding:14px;border-radius:8px}}.metric b{{display:block;font-size:24px;color:#2666a8}}.note{{background:#e9f2fb;border-left:4px solid #2666a8;padding:10px}}</style></head><body><main><h1>拓展分析阶段：发表级完整实验结果</h1><p>数据源：<code>{html.escape(str(job_root))}</code>；Job：<code>{html.escape(job_id)}</code>。本报告只汇总真实执行或真实失败的结果。</p><div class="grid">{''.join(cards)}</div><h2>实验定位</h2><div class="note">确定性 archetype 链路用于证明拓展阶段构建、验证和发布闭环；DeepSeek/API Agent 作为正式对照实验，按真实成功率和失败类型报告。</div><h2>总体结果</h2><table><tr><th>Converter</th><th>Cases</th><th>Passed</th><th>Failed</th></tr>{rows}</table><h2>图 1：通过/失败计数</h2><img src="figures/figure_pass_fail.svg" style="max-width:100%"><h2>负例拦截</h2><table><tr><th>Scenario</th><th>Blocked</th><th>Published</th><th>Error</th></tr>{negrows}</table><h2>可复现文件</h2><p>原始代码与数据位于 <code>packages/</code>，运行记录位于 <code>results/</code>，日志位于 <code>logs/</code>，完整文件清单位于 <code>MANIFEST.json</code>。</p></main></body></html>"""


def case_from_display(text: str) -> PubCase:
    for c in cases(DEFAULT_JOB_ROOT):
        if c.archetype_id in text:
            return c
    raise ValueError(f"cannot infer case from display_name: {text}")


def analysis_type(a: str) -> str:
    return {"sequence_extraction": "sequence_extraction", "hmmer_domain": "hmmer_search", "motif_meme": "generic_cli", "phylogeny_tree": "generic_cli", "reference_classification": "table_filter", "synteny_collinearity": "blast_search", "kaks_pairwise": "generic_cli", "pep_properties": "enrichment_or_stats"}[a]


def out_type(name: str) -> str:
    s = name.lower()
    if s.endswith((".fa", ".fasta")):
        return "fasta"
    if s.endswith(".xml"):
        return "xml"
    if s.endswith((".tsv", ".csv")):
        return "table"
    if s.endswith((".nwk", ".txt", ".domtblout")):
        return "text"
    return "file"


def case_readme(case: PubCase, copied: list[dict[str, Any]]) -> str:
    return f"# {case.archetype_id}\n\nRepresentative stage: `{case.stage}`\n\nPurpose: {case.description}\n\nSource files:\n" + "".join(f"- `{x['source']}` -> `raw_test_data/{x['dest']}`\n" for x in copied)


def upload_files(root: Path) -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(root.iterdir()) if p.is_file()]


def expected_ok(root: Path, case: PubCase) -> bool:
    return all((root / "expected_outputs" / x).is_file() and (root / "expected_outputs" / x).stat().st_size > 0 for x in case.required_outputs)


def row_success(r: dict[str, Any]) -> bool:
    return all(bool(r.get(k)) for k in ("raw_code_ok", "expected_output_ok", "build_success", "trial_success", "confirm_success", "validate_success", "publish_success")) and r.get("golden_status") == "passed" and r.get("template_gate_status") == "passed"


def clean_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def file_set(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(p: Path, rows: list[dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r.keys()}) if rows else ["empty"]
    with p.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({f: r.get(f, "") for f in fields})


def summary_md(s: dict[str, Any]) -> str:
    lines = ["# Extension Publication Batch", "", f"Converter: `{s.get('converter')}`", f"Cases: {s.get('case_count')} passed={s.get('passed')} failed={s.get('failed')}", "", "| Archetype | Build | Trial | Validate | Golden | Gate | Publish | Error |", "|---|---:|---:|---:|---|---|---:|---|"]
    for r in s.get("rows") or []:
        err = str(r.get("error") or "")[:120].replace("|", "/")
        lines.append(f"| `{r.get('archetype_id')}` | {r.get('build_success')} | {r.get('trial_success')} | {r.get('validate_success')} | `{r.get('golden_status')}` | `{r.get('template_gate_status')}` | {r.get('publish_success')} | {err} |")
    return "\n".join(lines) + "\n"


def write_manifest(out: Path) -> None:
    files = []
    for p in sorted(x for x in out.rglob("*") if x.is_file() and x.name != "MANIFEST.json"):
        files.append({"path": p.relative_to(out).as_posix(), "size": p.stat().st_size, "sha256": sha256(p)})
    write_json(out / "MANIFEST.json", {"schema_version": "extension_publication_experiment_manifest_v1", "created_at": now(), "root": str(out), "file_count": len(files), "files": files})


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
