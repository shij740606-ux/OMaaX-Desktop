"""Generate the formal report for one gene-family analysis plan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from gfa_core.gene_family_report_data import (
    ReportData,
    ReportSection,
    build_report_data,
)
from gfa_core.gene_family_report_html import (
    REPORT_RENDERER_VERSION,
    collect_stage_figures,
    markdown_to_html,
    render_stage_figures,
)
from gfa_core.mas.agents.interpretation_evidence import load_stage_manifests


REPORT_STAGE = "gene_family_report"
REPORT_TEMPLATE_VERSION = "gene_family_report_template_v1"
EXCLUDED_PLAN_STAGES = {"summary", REPORT_STAGE, "subfamily_confirm"}
TEMPLATE_PATH = Path(__file__).with_name("templates") / "gene_family_report_v1.md"
CONNECTORS = {
    "also": "此外，",
    "however": "不过，",
    "therefore": "因此，",
}


class NoSuccessfulGeneFamilyEvidence(RuntimeError):
    """Raised when the requested plan has no successful result to report."""


@dataclass(frozen=True)
class GeneFamilyReportResult:
    report_path: Path
    manifest_path: Path
    report_status: str
    generation_mode: str
    included_stages: tuple[str, ...]
    reused: bool = False
    html_path: Path | None = None


def _normalized_stages(stages: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for raw in stages or ():
        stage = str(raw).strip()
        if stage and stage not in EXCLUDED_PLAN_STAGES and stage not in result:
            result.append(stage)
    return result


def _parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return parsed


def _api_payload(report_data: ReportData) -> dict[str, Any]:
    return {
        "sections": [
            {
                "section_id": section.section_id,
                "sentences": [
                    {"sentence_id": sentence.id, "text": sentence.text}
                    for sentence in section.sentences
                ],
            }
            for section in report_data.sections
        ],
        "allowed_connectors": list(CONNECTORS),
    }


def _generate_api_narrative(payload: dict, stages: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    from gfa_core.mas.config import config as mas_config
    from gfa_core.mas.llm.base import LLMMessage
    from gfa_core.mas.llm.manager import LLMManager

    prompt = (
        "仅编排以下不可改写句子。输出 JSON 字段只能是 sections；每节只能含 section_id、paragraphs，"
        "每段只能含 sentence_ids、connector_ids。必须保留所有章节和句子且各出现一次，"
        "句子不得跨章节；connector_ids 只能来自 allowed_connectors。计划阶段为："
        f"{','.join(stages)}。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    response = LLMManager(config=mas_config.load_llm_config()).call(
        [
            LLMMessage(role="system", content="你是证据约束的生物信息学报告编辑器，只返回 JSON。"),
            LLMMessage(role="user", content=prompt),
        ],
        track_agent_name="GeneFamilyReport",
    )
    return _parse_json_object(response.content), {
        "status": "generated",
        "provider": response.provider,
        "model": response.model,
    }


def _default_arrangement(report_data: ReportData) -> dict[str, Any]:
    return {
        "sections": [
            {
                "section_id": section.section_id,
                "paragraphs": [
                    {"sentence_ids": [sentence.id], "connector_ids": []}
                    for sentence in section.sentences
                ],
            }
            for section in report_data.sections
        ]
    }


def _validate_arrangement(arrangement: Any, report_data: ReportData) -> None:
    if not isinstance(arrangement, dict) or set(arrangement) != {"sections"}:
        raise ValueError("API response has unknown or missing root fields")
    sections = arrangement["sections"]
    if not isinstance(sections, list):
        raise ValueError("API sections must be a list")
    expected = {section.section_id: {sentence.id for sentence in section.sentences} for section in report_data.sections}
    seen_sections: set[str] = set()
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"section_id", "paragraphs"}:
            raise ValueError("API section has unknown or missing fields")
        section_id = section["section_id"]
        if not isinstance(section_id, str) or section_id not in expected or section_id in seen_sections:
            raise ValueError("API changed or repeated a report section")
        seen_sections.add(section_id)
        paragraphs = section["paragraphs"]
        if not isinstance(paragraphs, list):
            raise ValueError("API paragraphs must be a list")
        seen_sentences: list[str] = []
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict) or set(paragraph) != {"sentence_ids", "connector_ids"}:
                raise ValueError("API paragraph has unknown or missing fields")
            sentence_ids = paragraph["sentence_ids"]
            connector_ids = paragraph["connector_ids"]
            if not isinstance(sentence_ids, list) or not sentence_ids or any(not isinstance(value, str) for value in sentence_ids):
                raise ValueError("API sentence_ids must be a non-empty string list")
            if not isinstance(connector_ids, list) or len(connector_ids) != len(sentence_ids) - 1:
                raise ValueError("API connector count does not match its paragraph")
            if any(not isinstance(value, str) or value not in CONNECTORS for value in connector_ids):
                raise ValueError("API used an unknown connector")
            if any(value not in expected[section_id] for value in sentence_ids):
                raise ValueError("API used an unknown or cross-section sentence")
            seen_sentences.extend(sentence_ids)
        if len(seen_sentences) != len(set(seen_sentences)) or set(seen_sentences) != expected[section_id]:
            raise ValueError("API omitted or repeated a required sentence")
    if seen_sections != set(expected):
        raise ValueError("API omitted a report section")


def _template_blocks() -> list[tuple[str, str]]:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    marker = re.compile(r"(?m)^<!-- GFA_SECTION ([a-z0-9_.]+) -->\s*$")
    matches = list(marker.finditer(template))
    if not matches:
        raise ValueError(f"报告模板没有章节边界：{TEMPLATE_PATH}")
    return [
        (
            match.group(1),
            template[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(template)].strip(),
        )
        for index, match in enumerate(matches)
    ]


def _arranged_content(section: ReportSection, arranged: Mapping[str, Any]) -> str:
    sentences = {sentence.id: sentence.text for sentence in section.sentences}
    paragraphs: list[str] = []
    for paragraph in arranged["paragraphs"]:
        sentence_ids = paragraph["sentence_ids"]
        text = sentences[sentence_ids[0]]
        for connector_id, sentence_id in zip(paragraph["connector_ids"], sentence_ids[1:]):
            text += f" {CONNECTORS[connector_id]}{sentences[sentence_id]}"
        paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _render_completion_table(report_data: ReportData) -> str:
    supported_stages = {section.stage for section in report_data.sections if section.stage}
    included = set(report_data.included)
    status_labels = {"success": "成功", "partial_success": "部分完成", "failed": "失败", "missing": "未完成"}
    lines = [
        f"本次计划 {len(report_data.planned)} 个阶段，纳入解读 {len(report_data.included)} 个，未纳入 {len(report_data.planned) - len(report_data.included)} 个。",
        "",
        "| 分析阶段 | 运行状态 | 模板支持 | 纳入报告 |",
        "|---|---|---|---|",
    ]
    for stage in report_data.planned:
        status = str(report_data.statuses.get(stage, "missing"))
        lines.append(
            f"| `{stage}` | {status_labels.get(status, status)} | "
            f"{'是' if stage in supported_stages else '否'} | {'是' if stage in included else '否'} |"
        )
    return "\n".join(lines)


def _render_markdown(
    report_data: ReportData,
    *,
    job_id: str,
    generation_mode: str,
    arrangement: Mapping[str, Any],
    figures_by_stage: Mapping[str, list[dict[str, str]]],
) -> str:
    section_data = {section.section_id: section for section in report_data.sections}
    arranged = {section["section_id"]: section for section in arrangement["sections"]}
    rendered: list[str] = []
    for section_id, block in _template_blocks():
        if section_id not in section_data:
            continue
        content = (
            _render_completion_table(report_data)
            if section_id == "common.completeness"
            else _arranged_content(section_data[section_id], arranged[section_id])
        )
        stage = section_id.removeprefix("stage.") if section_id.startswith("stage.") else ""
        if stage and figures_by_stage.get(stage):
            content += render_stage_figures(stage, figures_by_stage[stage])
        rendered.append(block.replace("{{SECTION_CONTENT}}", content))
    markdown = "\n\n".join(rendered)
    fallback_notice = (
        "> 生成模式：候补脚本。模型 API 不可用或输出越过语言编排边界；报告事实仍来自确定性结果数据。"
        if generation_mode == "fallback_script"
        else ""
    )
    replacements = {
        "{{PREFIX}}": report_data.prefix,
        "{{JOB_ID}}": job_id or "未指定",
        "{{GENERATION_MODE}}": "API 编排" if generation_mode == "api" else "候补脚本",
        "{{GENERATED_AT}}": datetime.now().isoformat(timespec="seconds"),
        "{{FALLBACK_NOTICE}}": fallback_notice,
    }
    for placeholder, value in replacements.items():
        markdown = markdown.replace(placeholder, value)
    if "{{" in markdown or "【填写】" in markdown:
        raise ValueError("报告模板仍含未替换占位符")
    return markdown.rstrip() + "\n"


def generate_gene_family_report(
    workspace: Path | str,
    prefix: str,
    *,
    expected_stages: Iterable[str] | None = None,
    stage_statuses: Mapping[str, str] | None = None,
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
    rnaseq_scope_id: str = "",
    use_model: bool = True,
    action: str = "generate",
    job_id: str = "",
) -> GeneFamilyReportResult:
    if action not in {"generate", "open"}:
        raise ValueError("action must be 'generate' or 'open'")
    final_dir = Path(workspace).expanduser().resolve() / "final"
    report_path = final_dir / f"{prefix}.gene_family_report.md"
    html_path = final_dir / f"{prefix}.gene_family_report.html"
    manifest_path = final_dir / f"{prefix}.gene_family_report.manifest.json"
    if action == "open" and report_path.is_file() and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not html_path.is_file():
            html_path.write_text(markdown_to_html(report_path.read_text(encoding="utf-8"), report_path, f"{prefix} 基因家族分析结果解读报告"), encoding="utf-8")
        manifest.setdefault("artifacts", {})["gene_family_report_html"] = {"path": f"final/{html_path.name}", "full_path": str(html_path), "type": "html"}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        metrics = manifest.get("metrics") or {}
        return GeneFamilyReportResult(
            report_path,
            manifest_path,
            str(metrics.get("report_status") or "partial"),
            str(metrics.get("generation_mode") or "fallback_script"),
            tuple(metrics.get("included_stages") or []),
            reused=True,
            html_path=html_path,
        )

    planned = _normalized_stages(expected_stages)
    if not planned:
        raise ValueError("expected_stages is required when generating a gene-family report")
    manifests = load_stage_manifests(
        final_dir,
        prefix,
        scope_id=scope_id,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
        rnaseq_scope_id=rnaseq_scope_id,
    )
    statuses = {str(key): str(value) for key, value in (stage_statuses or {}).items()}
    for stage in planned:
        statuses.setdefault(stage, str((manifests.get(stage) or {}).get("status") or "missing"))
    report_data = build_report_data(final_dir, prefix, planned, statuses, manifests)
    if not report_data.included:
        raise NoSuccessfulGeneFamilyEvidence("暂无可生成的基因家族分析结果")

    arrangement = _default_arrangement(report_data)
    generation_mode = "fallback_script"
    model_metadata: dict[str, Any] = {"status": "disabled"}
    if use_model:
        try:
            candidate, model_metadata = _generate_api_narrative(_api_payload(report_data), list(report_data.included))
            _validate_arrangement(candidate, report_data)
            arrangement = candidate
            generation_mode = "api"
        except Exception:
            model_metadata = {
                "status": "fallback",
                "reason": "api_unavailable_or_boundary_violation",
            }

    figures_by_stage = collect_stage_figures(final_dir, report_data.included, manifests)
    markdown = _render_markdown(
        report_data,
        job_id=job_id,
        generation_mode=generation_mode,
        arrangement=arrangement,
        figures_by_stage=figures_by_stage,
    )
    final_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown, report_path, f"{prefix} 基因家族分析结果解读报告"), encoding="utf-8")
    manifest = {
        "manifest_version": "1.0",
        "stage": REPORT_STAGE,
        "status": "success",
        "prefix": prefix,
        "job_id": job_id,
        "scope_id": scope_id,
        "analysis_route": analysis_route,
        "member_set_id": member_set_id,
        "rnaseq_scope_id": rnaseq_scope_id,
        "parameters": {"expected_stages": planned, "stage_statuses": {stage: statuses[stage] for stage in planned}},
        "metrics": {
            "report_status": report_data.report_status,
            "generation_mode": generation_mode,
            "included_stages": list(report_data.included),
            "unsupported_stages": list(report_data.unsupported_stages),
            "failed_or_missing_stages": [stage for stage in planned if stage not in report_data.included],
            "report_renderer_version": REPORT_RENDERER_VERSION,
            "embedded_figure_count": sum(len(items) for items in figures_by_stage.values()),
        },
        "model": model_metadata,
        "artifacts": {
            "gene_family_report": {
                "path": f"final/{report_path.name}",
                "full_path": str(report_path),
                "type": "markdown",
            },
            "gene_family_report_html": {
                "path": f"final/{html_path.name}", "full_path": str(html_path), "type": "html",
            },
            "gene_family_report_manifest": {
                "path": f"final/{manifest_path.name}",
                "full_path": str(manifest_path),
                "type": "manifest",
            },
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return GeneFamilyReportResult(
        report_path,
        manifest_path,
        report_data.report_status,
        generation_mode,
        report_data.included,
        html_path=html_path,
    )
