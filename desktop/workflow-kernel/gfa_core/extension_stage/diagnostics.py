"""Structured diagnostics for extension-stage runs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.semantic_validators import summarize_golden_failure
from gfa_core.extension_stage.spec_v2 import infer_analysis_type


def diagnose_extension_run(run: dict[str, Any], extension: dict[str, Any], *, run_dir: Path | None = None) -> dict[str, Any]:
    analysis_type = str(extension.get("analysis_type") or infer_analysis_type(extension))
    report: dict[str, Any] = {
        "analysis_type": analysis_type,
        "blocking": False,
        "code": "ok",
        "user_message": "",
        "evidence": {},
        "recommendations": [],
    }
    template_gate = run.get("template_gate") or extension.get("template_gate") or {}
    if template_gate and template_gate.get("status") not in {"", "pending", "passed", "legacy_unchecked", None}:
        report["blocking"] = True
        report["code"] = "template_gate_failed"
        report["evidence"]["template_gate"] = template_gate
        report["user_message"] = "拓展分析阶段模板门禁失败：" + "；".join(str(x) for x in (template_gate.get("errors") or ["未知模板门禁错误"]))
        report["recommendations"].append("确认 archetype_id 是否属于已登记模板，并补齐 input_roles/output_contract/golden comparison。")
        return report
    golden = run.get("golden_comparison") or extension.get("golden_comparison") or {}
    if golden and golden.get("status") not in {"", "passed", None}:
        golden_report = summarize_golden_failure(golden)
        report.update(golden_report)
        return report
    if run.get("status") == "success" and not _has_empty_outputs(run):
        return report
    if _diagnose_seqtk_runtime(report, run, extension, run_dir):
        report["blocking"] = run.get("status") != "success"
        return report
    if analysis_type == "sequence_extraction":
        _diagnose_sequence_extraction(report, run, run_dir)
    elif analysis_type == "blast_search":
        _diagnose_blast(report, run)
    elif analysis_type == "hmmer_search":
        _diagnose_hmmer(report, run, run_dir)
    else:
        _diagnose_generic(report, run)
    if not report["user_message"]:
        _diagnose_generic(report, run)
    report["blocking"] = run.get("status") != "success"
    return report


def _diagnose_seqtk_runtime(report: dict[str, Any], run: dict[str, Any], extension: dict[str, Any], run_dir: Path | None) -> bool:
    error = str(run.get("error") or "")
    command = str(run.get("command") or run.get("rendered_command") or "")
    original_command = str((extension.get("entrypoint") or {}).get("original_command") or "")
    semantic_command = str(extension.get("semantic_command_template") or (extension.get("entrypoint") or {}).get("semantic_command_template") or "")
    text = "\n".join([error, command, original_command, semantic_command]).lower()
    if "seqtk" not in text:
        return False
    if "failed to open the input file/stream" not in text and "no such file" not in text and "cannot open" not in text:
        return False

    expected_inputs = _seqtk_input_names(original_command) or _seqtk_input_names(semantic_command)
    uploaded_names = _uploaded_input_names(extension)
    raw_input_names = _raw_test_data_names(extension, run_dir)
    available = sorted(set(uploaded_names + raw_input_names))
    missing = [name for name in expected_inputs if name and name not in available and "{" not in name and not name.startswith("/")]
    role_mismatches = _semantic_role_mismatches(run, extension)

    report["code"] = "seqtk_missing_or_misbound_input"
    report["evidence"]["seqtk"] = {
        "original_command": original_command,
        "rendered_command": command,
        "expected_inputs": expected_inputs,
        "uploaded_inputs": available,
        "missing_inputs": missing,
        "role_mismatches": role_mismatches,
        "raw_error": error,
    }
    if missing:
        report["user_message"] = (
            "序列提取失败：原始命令需要输入文件 " + "、".join(missing) +
            "，但本次上传/测试数据中没有这些文件。当前检测到的文件：" +
            ("、".join(available) if available else "未检测到输入文件") + "。"
            "请上传缺失的序列文件，或把原始脚本里的文件名改成实际存在且类型正确的文件。"
        )
    elif role_mismatches:
        first = role_mismatches[0]
        report["user_message"] = (
            f"序列提取失败：输入类型绑定不一致。参数 {first.get('placeholder')} 需要 {first.get('expected_type')} 文件，"
            f"但当前绑定到 {first.get('file')}。请上传/选择正确类型的 FASTA 文件。"
        )
    else:
        report["user_message"] = (
            "序列提取失败：seqtk 无法打开输入文件。请检查原始命令中的 FASTA/ID 文件名是否与上传文件完全一致，"
            "以及文件是否为空或路径是否写错。当前检测到的文件：" + ("、".join(available) if available else "未检测到输入文件") + "。"
        )
    report["recommendations"].append("优先检查原始脚本中 seqtk subseq 后面的两个输入文件名：第一个应为序列 FASTA，第二个应为 ID 列表。")
    return True


def _seqtk_input_names(command: str) -> list[str]:
    match = re.search(r"seqtk\s+subseq\s+([^\s]+)\s+([^\s>]+)", str(command or ""))
    if not match:
        return []
    names = []
    for raw in (match.group(1), match.group(2)):
        value = raw.strip().strip("'\"")
        if value and not value.startswith("-"):
            names.append(Path(value).name)
    return names


def _uploaded_input_names(extension: dict[str, Any]) -> list[str]:
    names = []
    for item in extension.get("uploaded_inputs") or extension.get("detected_inputs") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or Path(str(item.get("path") or "")).name).strip()
            if name:
                names.append(name)
    return names


def _raw_test_data_names(extension: dict[str, Any], run_dir: Path | None) -> list[str]:
    roots: list[Path] = []
    paths = extension.get("paths") or {}
    root_value = str(paths.get("root") or "")
    if root_value:
        roots.append(Path(root_value) / str(paths.get("raw_test_data") or "raw_test_data"))
    if run_dir:
        for parent in [run_dir, *run_dir.parents]:
            candidate = parent / "raw_test_data"
            roots.append(candidate)
    names: list[str] = []
    for root in roots:
        if root.exists():
            names.extend(path.name for path in root.iterdir() if path.is_file())
            break
    return names


def _semantic_role_mismatches(run: dict[str, Any], extension: dict[str, Any]) -> list[dict[str, str]]:
    command = str(run.get("command") or run.get("rendered_command") or "")
    mismatches: list[dict[str, str]] = []
    checks = {
        "input_cds_fasta": ("CDS FASTA", ("cds",), ("pep", "protein", "faa")),
        "input_pep_fasta": ("protein/PEP FASTA", ("pep", "protein", "faa"), ("cds",)),
    }
    for placeholder, (expected, positive, negative) in checks.items():
        flag = "--" + placeholder.replace("_", "-")
        match = re.search(re.escape(flag) + r"\s+([^\s]+)", command)
        if not match:
            continue
        name = Path(match.group(1).strip("'\"")).name.lower()
        if any(token in name for token in negative) and not any(token in name for token in positive):
            mismatches.append({"placeholder": placeholder, "expected_type": expected, "file": Path(match.group(1)).name})
    return mismatches


def _diagnose_sequence_extraction(report: dict[str, Any], run: dict[str, Any], run_dir: Path | None) -> None:
    bindings = _binding_placeholders(run)
    fasta = _path_from_binding(bindings, "input_pep_fasta", "input_cds_fasta", "input_fasta")
    ids = _path_from_binding(bindings, "input_id_file", "input_file")
    if (not fasta or not ids) and run_dir:
        command = str(run.get("command") or "")
        seqtk = re.search(r"seqtk\s+subseq\s+(\S+)\s+(\S+)", command)
        if seqtk:
            fasta = fasta or Path(seqtk.group(1).strip("'\""))
            ids = ids or Path(seqtk.group(2).strip("'\""))
    evidence = _id_fasta_match_evidence(ids, fasta)
    if evidence:
        report["code"] = "id_fasta_mismatch" if evidence.get("exact_matches", 0) == 0 else "empty_sequence_extraction"
        report["evidence"]["id_fasta_match"] = evidence
        report["recommendations"].append("检查 ID 列表是否使用 FASTA header 中的 transcript ID；如 ID 是 gene-level，需要使用 gene 字段匹配或转换为 transcript ID。")
        report["user_message"] = (
            f"序列提取输出为空。ID 文件 {evidence.get('id_file')} 有 {evidence.get('id_count')} 个非空 ID，"
            f"FASTA {evidence.get('fasta_file')} 有 {evidence.get('fasta_header_count')} 条序列；"
            f"header 精确匹配 {evidence.get('exact_matches')} 个，gene/transcript 级匹配 {evidence.get('gene_like_matches')} 个。"
        )
    else:
        report["code"] = "empty_sequence_extraction"
        report["user_message"] = "序列提取输出为空，但无法定位 ID/FASTA 绑定；请检查 manifest 中的 binding_report 和 rendered command。"


def _diagnose_blast(report: dict[str, Any], run: dict[str, Any]) -> None:
    bindings = _binding_placeholders(run)
    query = _path_from_binding(bindings, "input_query_fasta", "input_fasta", "input_pep_fasta")
    ref = _path_from_binding(bindings, "input_reference_fasta")
    evidence = {
        "query": str(query or ""),
        "reference": str(ref or ""),
        "query_exists": bool(query and query.exists()),
        "reference_exists": bool(ref and ref.exists()),
        "command": run.get("command", ""),
    }
    report["code"] = "blast_binding_or_output_failure"
    report["evidence"]["blast"] = evidence
    if query and ref:
        report["user_message"] = f"BLASTP 执行未生成有效输出；当前 query={query.name}，reference/database={ref.name}。请检查用户语义方向、数据库构建和输出过滤阈值。"
    else:
        report["user_message"] = "BLASTP 执行未生成有效输出，且 query/reference 绑定不完整；不能靠 FASTA 大小猜测方向。"
    report["recommendations"].append("确认自然语言中的 A 对 B 比对是否解析为 A=query、B=reference，并检查 command/output prefix 是否一致。")


def _diagnose_hmmer(report: dict[str, Any], run: dict[str, Any], run_dir: Path | None) -> None:
    outputs = run.get("outputs") or []
    domtbl = next((out for out in outputs if str(out.get("name", "")).endswith(".domtblout")), None)
    evidence: dict[str, Any] = {"outputs": [{"name": o.get("name"), "size": o.get("size"), "is_empty": o.get("is_empty")} for o in outputs]}
    if domtbl and run_dir:
        path = run_dir / str(domtbl.get("path") or "")
        evidence["domtbl_non_comment_rows"] = _count_non_comment_rows(path)
    missing = run.get("misplaced_outputs") or []
    if missing:
        report["code"] = "misplaced_outputs"
        evidence["misplaced_outputs"] = missing
        report["user_message"] = "HMMER 输出写到了 outputs/ 之外，说明 wrapper 中输出目录变量或 shell 参数展开错误。"
        report["recommendations"].append("检查 run_extension.sh 是否使用 ${OUTDIR:-...} 而不是字面量 {OUTDIR:-...}，并确保所有输出路径位于 --output-dir 下。")
    elif evidence.get("domtbl_non_comment_rows", 0) == 0:
        report["code"] = "hmmer_no_hits_or_threshold"
        report["user_message"] = "HMMER 原始输出没有可用命中或过滤后为空；需要区分无命中与阈值过严。"
        report["recommendations"].append("检查 hmmsearch 输出中的 hit 数量、E-value 阈值和 awk 过滤列是否正确。")
    else:
        report["code"] = "hmmer_postprocess_empty"
        report["user_message"] = "HMMER 有原始输出，但后处理 FASTA/HMM 重建结果为空；通常是坐标列、ID 提取或过滤阈值问题。"
    report["evidence"]["hmmer"] = evidence


def _diagnose_generic(report: dict[str, Any], run: dict[str, Any]) -> None:
    report["code"] = str((run.get("failure_classification") or {}).get("code") or "extension_run_failed")
    report["evidence"]["error"] = run.get("error", "")
    report["user_message"] = str(run.get("error") or "拓展分析阶段执行失败；请查看 extension_run.json、stdout.log 和 stderr.log。")


def _id_fasta_match_evidence(id_path: Path | None, fasta_path: Path | None) -> dict[str, Any]:
    if not id_path or not fasta_path or not id_path.exists() or not fasta_path.exists():
        return {}
    ids = [line.strip().split()[0] for line in id_path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
    ids_set = set(ids)
    headers: list[str] = []
    gene_tokens: set[str] = set()
    with fasta_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(">"):
                continue
            header = line[1:].strip()
            token = header.split()[0]
            headers.append(token)
            gene_tokens.add(token)
            if "." in token:
                gene_tokens.add(token.split(".", 1)[0])
            for match in re.finditer(r"(?:gene|gene_id|locus):?([A-Za-z0-9_.-]+)", header):
                gene_tokens.add(match.group(1))
    header_set = set(headers)
    exact = len(ids_set & header_set)
    gene_like = len(ids_set & gene_tokens)
    return {
        "id_file": id_path.name,
        "fasta_file": fasta_path.name,
        "id_count": len(ids),
        "unique_id_count": len(ids_set),
        "fasta_header_count": len(headers),
        "exact_matches": exact,
        "gene_like_matches": gene_like,
        "sample_unmatched_ids": [x for x in ids[:20] if x not in header_set][:10],
    }


def _binding_placeholders(run: dict[str, Any]) -> dict[str, str]:
    params = run.get("parameters") or {}
    for key in ("binding_report", "config_input_bindings"):
        report = run.get(key) or params.get(key)
        if isinstance(report, dict):
            placeholders = report.get("placeholder_to_file") or report.get("placeholders") or {}
            if isinstance(placeholders, dict):
                return {str(k): str(v) for k, v in placeholders.items() if str(v)}
    return {}


def _path_from_binding(bindings: dict[str, str], *keys: str) -> Path | None:
    for key in keys:
        value = bindings.get(key)
        if value:
            return Path(value)
    return None


def _has_empty_outputs(run: dict[str, Any]) -> bool:
    outputs = run.get("outputs") or []
    return bool(outputs) and any(bool(out.get("is_empty")) or int(out.get("size") or 0) == 0 for out in outputs)


def _count_non_comment_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip() and not line.startswith("#"):
            count += 1
    return count
