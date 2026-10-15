"""DeepSeek-backed, evidence-locked gene-family summary reports.

The LLM is used only to turn verified local results into readable Chinese.  All
numbers, result boundaries and figure names come from local manifests and the
existing evidence interpreter.  The report, prompt evidence and API metadata
are persisted separately so a generated claim can be audited later.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from gfa_core.mas.agents.interpretation_evidence import (
    build_evidence_report,
    extract_stage_evidence,
)
from gfa_core.mas.llm.base import LLMMessage
from gfa_core.mas.llm.providers.deepseek import DeepSeekProvider


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FRAMEWORK = PROJECT_ROOT / "prompts" / "gene_family_summary_report_framework_zh.md"
IMAGE_SUFFIXES = {".png", ".pdf", ".svg", ".jpg", ".jpeg"}
STAGE_HINTS = [
    "reference_classification",
    "subfamily_confirm",
    "interspecies_synteny",
    "phylogeny",
    "identify",
    "motif",
    "domain",
    "structure",
    "combo",
    "promoter",
    "tfbs",
    "synteny",
    "karyotype",
    "ortholog",
    "kaks",
    "pep",
]


class DeepSeekSummaryError(RuntimeError):
    """Raised when an evidence-locked report cannot be generated."""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_deepseek_settings(*, model_override: str = "") -> Dict[str, str]:
    values: Dict[str, str] = {}
    for env_path in (
        PROJECT_ROOT / ".env.v15_deepseek",
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "config" / ".env",
    ):
        for key, value in _load_env_file(env_path).items():
            values.setdefault(key, value)
    api_key = (
        os.environ.get("DEEPSEEK_API_KEY", "").strip()
        or os.environ.get("NEW_API_KEY", "").strip()
        or values.get("DEEPSEEK_API_KEY", "").strip()
        or values.get("NEW_API_KEY", "").strip()
    )
    if not api_key:
        raise DeepSeekSummaryError("DeepSeek API key is not configured (DEEPSEEK_API_KEY or NEW_API_KEY).")
    base_url = (
        os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        or os.environ.get("NEW_API_BASE_URL", "").strip()
        or values.get("DEEPSEEK_BASE_URL", "").strip()
        or values.get("NEW_API_BASE_URL", "").strip()
        or "https://api.deepseek.com"
    )
    model = (
        model_override.strip()
        or os.environ.get("DEEPSEEK_MODEL", "").strip()
        or os.environ.get("NEW_API_MODEL", "").strip()
        or values.get("DEEPSEEK_MODEL", "").strip()
        or values.get("NEW_API_MODEL", "").strip()
        or "deepseek-chat"
    )
    return {"api_key": api_key, "base_url": base_url, "model": model}


def select_primary_scope(summaries: Iterable[Dict[str, Any]], requested: str = "") -> str:
    if str(requested or "").strip():
        return str(requested).strip()
    counts: Dict[str, int] = defaultdict(int)
    for item in summaries:
        scope = str(item.get("scope_id") or "").strip()
        stage = str(item.get("stage") or "").strip()
        if scope and stage not in {"summary", "genome_prepare", "config_autofill"}:
            counts[scope] += 1
    if not counts:
        return ""
    return max(
        counts,
        key=lambda scope: (
            int("target_plus_reference" in scope),
            int("subfamily_evidence" not in scope),
            counts[scope],
            scope,
        ),
    )


def _infer_stage(name: str, role: str = "") -> str:
    text = f"{role} {name}".lower()
    if "tfbs" in text or "motif_source" in text or "gene_motif" in text:
        return "tfbs"
    if "interspecies" in text:
        return "interspecies_synteny"
    if "circular_tree" in text or ".tree." in text or text.endswith("tree.png"):
        return "phylogeny"
    if "protparam" in text or "tmhmm" in text or "pep_" in text:
        return "pep"
    for stage in STAGE_HINTS:
        if stage in text:
            return stage
    return "other"


def _logical_figure_key(path: Path) -> str:
    return path.stem.lower()


def collect_figure_inventory(
    final_dir: Path,
    summaries: Iterable[Dict[str, Any]],
    *,
    scope_id: str = "",
) -> List[Dict[str, Any]]:
    artifact_map: Dict[str, Dict[str, str]] = {}
    for item in summaries:
        item_scope = str(item.get("scope_id") or "")
        if scope_id and item_scope and item_scope != scope_id:
            # Foundational target-only figures are not reused in a reference scope.
            continue
        stage = str(item.get("stage") or "")
        for artifact in item.get("artifact_rows") or []:
            raw = str(artifact.get("path") or "")
            if not raw:
                continue
            path = Path(raw)
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            artifact_map[path.name] = {
                "stage": stage or _infer_stage(path.name, str(artifact.get("artifact") or "")),
                "role": str(artifact.get("artifact") or ""),
            }

    candidates: List[Path] = []
    if final_dir.is_dir():
        candidates.extend(
            path for path in final_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and ".bak" not in path.name.lower()
        )

    grouped: Dict[str, List[Path]] = defaultdict(list)
    for path in candidates:
        grouped[_logical_figure_key(path)].append(path)

    inventory: List[Dict[str, Any]] = []
    seen_content: Dict[str, str] = {}
    preference = {".png": 0, ".jpg": 1, ".jpeg": 1, ".svg": 2, ".pdf": 3}
    for _, paths in sorted(grouped.items()):
        paths.sort(key=lambda p: (preference.get(p.suffix.lower(), 9), p.name))
        preferred = paths[0]
        try:
            digest = hashlib.sha256(preferred.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        if digest and digest in seen_content:
            continue
        if digest:
            seen_content[digest] = preferred.name
        mapped = artifact_map.get(preferred.name, {})
        role = mapped.get("role", "")
        stage = mapped.get("stage", "") or _infer_stage(preferred.name, role)
        inventory.append({
            "figure_id": f"FIG-{len(inventory) + 1:03d}",
            "file": preferred.name,
            "stage": stage,
            "artifact_role": role,
            "available_formats": sorted({p.suffix.lower().lstrip(".") for p in paths}),
            "marker": f"[[FIG:{preferred.name}]]",
        })
    return inventory


def _foundation_identify(final_dir: Path, prefix: str) -> Dict[str, Any]:
    candidates: List[Path] = []
    exact = final_dir / f"{prefix}.identify.manifest.json"
    if exact.is_file():
        candidates.append(exact)
    candidates.extend(
        path for path in final_dir.glob(f"{prefix}*.identify.manifest.json")
        if path not in candidates
    )
    for path in candidates:
        manifest = _read_json(path)
        if manifest.get("stage") == "identify" and manifest.get("status") in {"success", "partial_success"}:
            bundle = extract_stage_evidence("identify", manifest)
            return {
                "manifest": path.name,
                "scope_id": manifest.get("scope_id", ""),
                "status": manifest.get("status", ""),
                "metrics": manifest.get("metrics", {}),
                "parsed": bundle.get("parsed", {}),
                "evidence": bundle.get("evidence", []),
                "risks": bundle.get("risks", []),
            }
    return {}


def _compact_report(report: Dict[str, Any]) -> Dict[str, Any]:
    evidence_cards = report.get("evidence_cards") or []
    return {
        "metadata": report.get("metadata", {}),
        "source_summary": report.get("source_summary", {}),
        "stage_flow": report.get("stage_flow", {}),
        "key_findings": report.get("key_findings", []),
        "structured_result_interpretations": report.get("structured_result_interpretations", {}),
        "grouped_result_interpretations": report.get("grouped_result_interpretations", {}),
        "scientific_discoveries": report.get("scientific_discoveries", []),
        "risk_flags": report.get("risk_flags", []),
        "stage_reviews": report.get("stage_reviews", []),
        "publication_claims": report.get("publication_claims", []),
        "major_claims": report.get("major_claims", []),
        "next_steps": report.get("next_steps", []),
        "evidence_cards": evidence_cards[:100],
    }


def build_summary_evidence(
    *,
    final_dir: Path,
    prefix: str,
    job_id: str,
    summaries: List[Dict[str, Any]],
    scope_id: str,
) -> Dict[str, Any]:
    report = build_evidence_report(
        final_dir,
        prefix,
        job_id=job_id,
        scope_id=scope_id,
        analysis_route="target_plus_reference" if "target_plus_reference" in scope_id else "",
    )
    if not report:
        raise DeepSeekSummaryError(f"No evidence report could be built for scope: {scope_id or 'default'}")
    evidence = _compact_report(report)
    included = list((evidence.get("metadata") or {}).get("included_stages") or [])
    identify = _foundation_identify(final_dir, prefix)
    if identify and "identify" not in included:
        evidence["foundation_identify"] = identify
        included.insert(0, "identify")
        identify_metrics = identify.get("metrics") or {}
        final_members = identify_metrics.get("final_members")
        candidate_members = identify_metrics.get("candidate_members")
        hmm_members = identify_metrics.get("hmm_members")
        blast_members = identify_metrics.get("blast_members")
        evidence.setdefault("structured_result_interpretations", {})["identify"] = {
            "stage": "identify",
            "stage_name": "基因家族成员鉴定",
            "status": identify.get("status") or "success",
            "interpretation_level": "publishable_result",
            "result_interpretation": (
                f"目标物种全蛋白集共有 {identify_metrics.get('total_proteins', 'N/A')} 条蛋白；"
                f"HMM 搜索得到 {hmm_members} 个命中，BLAST 搜索记录 {blast_members} 个命中，"
                f"联合筛选形成 {candidate_members} 个候选，最终确认 {final_members} 个家族成员。"
                "HMM、BLAST、候选集和最终集的关系必须按本地记录描述，不能自行写成取交集或取并集。"
            ),
            "evidence_basis": identify.get("evidence") or [],
            "limitations": [
                "鉴定证据来自目标物种 target_only 基础清单，并作为 target_plus_reference 后续分析的成员基础。",
                "鉴定成功不能直接证明所有成员具有相同功能。",
            ],
            "boundary": "不得把全基因组蛋白数写成家族成员数，也不得猜测联合筛选的集合运算方式。",
        }
        metadata = evidence["metadata"]
        metadata["included_stages"] = included
        metadata["included_stages_for_deepseek"] = included
        missing = list(evidence["metadata"].get("missing_expected_stages") or [])
        missing = [x for x in missing if x != "identify"]
        metadata["missing_expected_stages"] = missing
        metadata["missing_expected_stages_for_deepseek"] = missing
        expected = list(metadata.get("expected_stages") or [])
        metadata["completed_expected_stages"] = [stage for stage in expected if stage in included]
        metadata["stage_count"] = len(included)
        metadata["is_final"] = bool(expected) and not missing
        stage_flow = evidence.setdefault("stage_flow", {})
        stages = list(stage_flow.get("stages") or [])
        stages.insert(0, {
            "id": "identify",
            "name": "基因家族成员鉴定",
            "status": identify.get("status") or "success",
            "key_result": f"最终确认 {final_members} 个成员",
        })
        stage_flow["stages"] = stages
        stage_flow["visualization"] = " -> ".join(item.get("id", "") for item in stages)
        source_summary = evidence.setdefault("source_summary", {})
        source_summary["manifest_count"] = int(source_summary.get("manifest_count") or 0) + 1
    evidence["figures"] = collect_figure_inventory(final_dir, summaries, scope_id=scope_id)
    return evidence


def _system_prompt() -> str:
    return """你是植物基因家族分析结果报告作者。你只能使用用户提供的本地证据，不能补造数字、成员、分组、功能或文献。
任务是把机器结果写成普通研究人员能直接看懂的中文报告。少用专业词；首次出现的专业词必须用一句日常语言解释。
每张图必须逐图解读，但不能假装看到了证据包未提供的像素细节。若只有文件名和阶段事实，应明确写“结合该阶段统计进行解释，具体成员级图形细节需打开原图复核”。
所有关联性、预测性结果都必须保留边界，不得使用“证明、决定、必然、直接调控”等过强措辞。
风险清单优先级高于一般解释。高风险阶段不得写成可靠生物学结论。
输出只允许完整 Markdown 正文，不要输出代码围栏、JSON、写作说明或思考过程。"""


def _user_prompt(*, framework: str, evidence: Dict[str, Any], job_id: str, prefix: str, scope_id: str) -> str:
    figures = evidence.get("figures") or []
    return f"""请为真实任务生成最终中文报告。

任务编号：{job_id}
分析前缀：{prefix}
分析范围：{scope_id or '默认范围'}

硬性要求：
1. 按“鉴定→系统发育与分类→motif→domain→基因结构→组合图→启动子/TFBS→种内与种间共线性→染色体分布→ortholog→PEP→综合结论”的顺序组织。
2. 首页给出普通读者能看懂的一页式摘要，并列出完成阶段、缺失阶段和最重要风险。
3. 对 figures 中的每个逻辑图写一个小节，并原样保留对应 marker，例如 [[FIG:xxx.png]]。共有 {len(figures)} 个逻辑图；同一图的 PDF/PNG 不重复解释。
4. 每个图小节写：图用来回答什么、从本任务事实能看出什么、通俗解释、不能说明什么。没有足够的成员级数值时必须直说，不能编造。
5. 核型阶段存在高风险时，必须明确说明不能据图得出染色体分布结论。
6. 种内共线性没有检测到家族成员基因对时，不能写成片段复制驱动家族扩张。
7. 最终成员数以 identify 事实为准；全基因组背景数量不能写成家族成员数。
8. 报告末尾给出候选成员筛选框架、主要限制、下一步验证建议和“读者最需要记住的五句话”。
9. 不要保留任何 {{{{占位符}}}}、TODO 或空表格；没有数据的内容直接写“本任务未提供”。
10. 语言以短句为主，结论先行。专业词后立即补通俗解释。

以下是报告结构参考。它决定覆盖范围和表达方式，不代表其中的示例数字是本任务事实：

--- 框架开始 ---
{framework}
--- 框架结束 ---

以下 JSON 是本任务唯一允许使用的事实来源：

--- 本地证据开始 ---
{json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}
--- 本地证据结束 ---
"""


def _strip_fence(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:markdown|md)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _validate_report(report: str, *, job_id: str, figures: List[Dict[str, Any]]) -> Dict[str, Any]:
    markers = [str(item.get("marker") or "") for item in figures]
    missing = [marker for marker in markers if marker and marker not in report]
    heading_count = len(re.findall(r"(?m)^##+\s+", report))
    placeholders = sorted(set(re.findall(r"\{\{[^{}]+\}\}", report)))
    return {
        "character_count": len(report),
        "heading_count": heading_count,
        "job_id_present": job_id in report,
        "figure_count": len(markers),
        "covered_figure_count": len(markers) - len(missing),
        "missing_figure_markers": missing,
        "placeholders": placeholders,
        "passed": bool(len(report) >= 4000 and heading_count >= 8 and job_id in report and not missing and not placeholders),
    }


def _supplement_prompt(missing: List[str], evidence: Dict[str, Any]) -> str:
    wanted = [item for item in evidence.get("figures") or [] if item.get("marker") in set(missing)]
    return f"""上一版报告漏掉了部分图。只输出一个 Markdown 章节，标题为“## 补充图表解读”。
必须逐项原样保留 marker，并为每项写：图的用途、本地证据允许的解释、不能得出的结论。
不得补造图中具体成员、颜色、分组或数值。

缺失图：
{json.dumps(wanted, ensure_ascii=False)}

可用阶段解释和风险：
{json.dumps({'structured': evidence.get('structured_result_interpretations', {}), 'risks': evidence.get('risk_flags', [])}, ensure_ascii=False)}
"""


def _items_for_stages(items: Iterable[Any], stages: set[str]) -> List[Any]:
    selected: List[Any] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_stages = item.get("stages") or [item.get("stage")]
        if isinstance(item_stages, str):
            item_stages = [item_stages]
        if stages.intersection(str(stage) for stage in item_stages if stage):
            selected.append(item)
    return selected


def _group_prompt(
    *,
    title: str,
    stages: List[str],
    evidence: Dict[str, Any],
    figures: List[Dict[str, Any]],
) -> str:
    stage_set = set(stages)
    structured = evidence.get("structured_result_interpretations") or {}
    payload = {
        "stages": stages,
        "structured_result_interpretations": {
            stage: structured.get(stage) for stage in stages if structured.get(stage)
        },
        "key_findings": _items_for_stages(evidence.get("key_findings") or [], stage_set),
        "risk_flags": _items_for_stages(evidence.get("risk_flags") or [], stage_set),
        "stage_reviews": _items_for_stages(evidence.get("stage_reviews") or [], stage_set),
        "evidence_cards": _items_for_stages(evidence.get("evidence_cards") or [], stage_set)[:40],
        "figures": figures,
    }
    if "identify" in stage_set and evidence.get("foundation_identify"):
        payload["foundation_identify"] = evidence["foundation_identify"]
    return f"""请只撰写报告章节“## {title}”，不要写整篇报告，也不要重复首页信息。
覆盖阶段：{', '.join(stages)}。

要求：
- 先用一段通俗文字说明这些阶段得到了什么，再按图逐项解释。
- 共有 {len(figures)} 个逻辑图。每张图必须建立三级标题，并原样保留 marker。
- 每个图写“图的用途—本任务可确认的结果—通俗解释—结论边界”。
- 没有成员级或像素级事实时，必须写需要打开原图复核，不能猜颜色、成员和具体分组。
- 风险提示优先。高风险结果必须明确降级。
- 本章节最后写一个“阶段小结”，只总结本地证据。
- 只输出 Markdown 章节正文。

本地证据：
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
"""


def _overview_prompt(*, evidence: Dict[str, Any], job_id: str, prefix: str, scope_id: str) -> str:
    payload = {
        "metadata": evidence.get("metadata", {}),
        "source_summary": evidence.get("source_summary", {}),
        "stage_flow": evidence.get("stage_flow", {}),
        "foundation_identify": evidence.get("foundation_identify", {}),
        "risk_flags": evidence.get("risk_flags", []),
    }
    return f"""请撰写基因家族结果报告的开头部分，只输出以下三个二级章节：
## 一、项目与数据范围
## 二、一页式结论摘要
## 三、流程完成情况与可信度

任务编号：{job_id}
前缀：{prefix}
范围：{scope_id}

使用短句和通俗中文。摘要写5—8条最重要结论。必须写清最终成员数、完成阶段、缺失阶段、主要风险和结论边界。
禁止补造证据；不能把全基因组背景数写成家族成员数。只输出 Markdown 章节正文。

本地证据：
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
"""


def _conclusion_prompt(*, evidence: Dict[str, Any]) -> str:
    payload = {
        "scientific_discoveries": evidence.get("scientific_discoveries", []),
        "publication_claims": evidence.get("publication_claims", []),
        "major_claims": evidence.get("major_claims", []),
        "risk_flags": evidence.get("risk_flags", []),
        "next_steps": evidence.get("next_steps", []),
        "foundation_identify": evidence.get("foundation_identify", {}),
    }
    return f"""请撰写基因家族结果报告的结尾，只输出以下四个二级章节：
## 十、候选成员与多证据综合
## 十一、主要限制
## 十二、下一步验证建议
## 十三、读者最需要记住的五句话

候选成员没有足够成员级证据时，不要强行编排名；改为给出可执行筛选规则和待补证据。
把预测和相关结果写成线索，不得写成已证明功能。高风险问题必须进入主要限制并排在修复建议前面。
只输出 Markdown 章节正文。

本地证据：
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
"""


def _call_section(
    provider: DeepSeekProvider,
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[str, Dict[str, Any]]:
    response = provider.call(
        [LLMMessage(role="system", content=_system_prompt()), LLMMessage(role="user", content=prompt)],
        temperature=0.12,
        max_tokens=max_tokens,
        disable_thinking=True,
    )
    text = _strip_fence(response.content)
    if len(text) < 300 or not re.search(r"(?m)^##\s+", text):
        raise DeepSeekSummaryError(
            f"DeepSeek returned an incomplete section (finish_reason={response.finish_reason}, chars={len(text)}): {text[:500]}"
        )
    return text, {
        "provider": response.provider,
        "model": response.model,
        "usage": response.usage or {},
        "finish_reason": response.finish_reason,
    }


def generate_deepseek_summary(
    *,
    final_dir: Path,
    prefix: str,
    job_id: str,
    summaries: List[Dict[str, Any]],
    scope_id: str = "",
    framework_path: Optional[Path] = None,
    model: str = "",
    max_tokens: int = 16000,
) -> Dict[str, Any]:
    final_dir = final_dir.resolve()
    effective_scope = select_primary_scope(summaries, scope_id)
    framework_file = (framework_path or DEFAULT_FRAMEWORK).expanduser().resolve()
    if not framework_file.is_file():
        raise DeepSeekSummaryError(f"Report framework not found: {framework_file}")
    evidence = build_summary_evidence(
        final_dir=final_dir,
        prefix=prefix,
        job_id=job_id,
        summaries=summaries,
        scope_id=effective_scope,
    )
    settings = load_deepseek_settings(model_override=model)
    provider = DeepSeekProvider(
        api_key=settings["api_key"],
        model=settings["model"],
        temperature=0.15,
        max_tokens=max_tokens,
        api_base=settings["base_url"],
    )
    started = time.time()
    framework = framework_file.read_text(encoding="utf-8", errors="ignore")
    framework_outline = "\n".join(
        line for line in framework.splitlines() if line.startswith("## ")
    )
    section_records: List[Dict[str, Any]] = []
    overview, call_meta = _call_section(
        provider,
        _overview_prompt(
            evidence=evidence,
            job_id=job_id,
            prefix=prefix,
            scope_id=effective_scope,
        ) + "\n\n报告框架章节纲要：\n" + framework_outline,
        max_tokens=min(max_tokens, 4500),
    )
    section_records.append({"section": "overview", **call_meta})

    figures = evidence.get("figures") or []
    group_specs = [
        (
            "四、成员鉴定、系统发育与分类",
            ["identify", "phylogeny", "reference_classification", "subfamily_confirm"],
        ),
        (
            "五、保守片段、功能区域、基因结构与组合证据",
            ["motif", "domain", "structure", "combo"],
        ),
        (
            "六、启动子与 TFBS 调控线索",
            ["promoter", "tfbs"],
        ),
        (
            "七、基因组分布、共线性与跨物种关系",
            ["synteny", "interspecies_synteny", "karyotype", "ortholog", "kaks"],
        ),
        (
            "八、候选蛋白 PEP 特征",
            ["pep"],
        ),
        (
            "九、其他结果图",
            ["other"],
        ),
    ]
    sections = [overview]
    covered_stages: set[str] = set()
    for title, stages in group_specs:
        group_figures = [item for item in figures if str(item.get("stage") or "other") in set(stages)]
        structured = evidence.get("structured_result_interpretations") or {}
        has_stage_evidence = any(structured.get(stage) for stage in stages) or (
            "identify" in stages and evidence.get("foundation_identify")
        )
        if not group_figures and not has_stage_evidence:
            continue
        section_text, call_meta = _call_section(
            provider,
            _group_prompt(title=title, stages=stages, evidence=evidence, figures=group_figures),
            max_tokens=min(max_tokens, 6500),
        )
        sections.append(section_text)
        section_records.append({
            "section": title,
            "stages": stages,
            "figure_count": len(group_figures),
            **call_meta,
        })
        covered_stages.update(stages)

    conclusion, call_meta = _call_section(
        provider,
        _conclusion_prompt(evidence=evidence),
        max_tokens=min(max_tokens, 5000),
    )
    sections.append(conclusion)
    section_records.append({"section": "conclusion", **call_meta})
    report = (
        f"# {prefix} 基因家族分析结果通俗解读报告\n\n"
        f"任务编号：`{job_id}`  \n"
        f"分析范围：`{effective_scope or 'default'}`  \n"
        f"生成方式：DeepSeek API 分段生成；本地证据锁定  \n\n"
        + "\n\n".join(sections)
    )
    validation = _validate_report(report, job_id=job_id, figures=evidence.get("figures") or [])
    supplement_usage: Dict[str, Any] = {}
    if validation["missing_figure_markers"]:
        supplement = provider.call(
            [
                LLMMessage(role="system", content=_system_prompt()),
                LLMMessage(role="user", content=_supplement_prompt(validation["missing_figure_markers"], evidence)),
            ],
            temperature=0.1,
            max_tokens=min(max_tokens, 8000),
            disable_thinking=True,
        )
        supplement_text = _strip_fence(supplement.content)
        if supplement_text:
            report = report.rstrip() + "\n\n" + supplement_text + "\n"
        supplement_usage = supplement.usage or {}
        section_records.append({
            "section": "missing_figure_supplement",
            "usage": supplement_usage,
            "finish_reason": supplement.finish_reason,
        })
        validation = _validate_report(report, job_id=job_id, figures=evidence.get("figures") or [])
    if not validation["passed"]:
        raise DeepSeekSummaryError(
            "DeepSeek report failed validation: "
            + json.dumps(validation, ensure_ascii=False)
        )

    scope_token = re.sub(r"[^0-9A-Za-z_.-]+", "-", effective_scope).strip(".-")
    name_root = f"{prefix}.{scope_token}" if scope_token else prefix
    report_path = final_dir / f"{name_root}.summary_report.md"
    evidence_path = final_dir / f"{name_root}.summary.evidence.json"
    metadata_path = final_dir / f"{name_root}.summary.deepseek.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report.rstrip() + "\n", encoding="utf-8")
    metadata = {
        "schema": "deepseek_summary_generation_v1",
        "status": "success",
        "generated_at": datetime.now().isoformat(),
        "job_id": job_id,
        "prefix": prefix,
        "scope_id": effective_scope,
        "provider": "deepseek",
        "model": settings["model"],
        "base_url": settings["base_url"],
        "duration_seconds": round(time.time() - started, 3),
        "section_calls": section_records,
        "supplement_usage": supplement_usage,
        "framework": str(framework_file),
        "evidence_file": str(evidence_path),
        "report_file": str(report_path),
        "validation": validation,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "scope_id": effective_scope,
        "report_path": report_path,
        "evidence_path": evidence_path,
        "metadata_path": metadata_path,
        "metadata": metadata,
    }
