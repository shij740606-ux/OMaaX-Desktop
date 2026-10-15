"""Deterministic data model for the formal gene-family report."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class MissingMetric:
    """A metric that is genuinely absent from every declared source."""

    label: str
    checked_sources: tuple[str, ...]

    def render(self) -> str:
        checked = "、".join(self.checked_sources)
        return f"{self.label}：源结果未提供此指标（已检查：{checked}）。"


@dataclass(frozen=True)
class Sentence:
    """One immutable report sentence decided by deterministic code."""

    id: str
    text: str
    kind: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportSection:
    section_id: str
    stage: str | None
    facts: tuple[Sentence, ...] = ()
    observations: tuple[Sentence, ...] = ()
    interpretations: tuple[Sentence, ...] = ()
    limitations: tuple[Sentence, ...] = ()
    recommendations: tuple[Sentence, ...] = ()

    @property
    def sentences(self) -> tuple[Sentence, ...]:
        return (
            self.facts
            + self.observations
            + self.interpretations
            + self.limitations
            + self.recommendations
        )


@dataclass(frozen=True)
class ReportData:
    prefix: str
    planned: tuple[str, ...]
    statuses: Mapping[str, str]
    included: tuple[str, ...]
    unsupported_stages: tuple[str, ...]
    report_status: str
    sections: tuple[ReportSection, ...]


STAGE_SECTION_IDS = {
    "identify": "stage.identify",
    "phylogeny": "stage.phylogeny",
    "reference_classification": "stage.reference_classification",
    "motif": "stage.motif",
    "domain": "stage.domain",
    "structure": "stage.structure",
    "combo": "stage.combo",
    "promoter": "stage.promoter",
    "tfbs": "stage.tfbs",
    "synteny": "stage.synteny",
    "karyotype": "stage.karyotype",
    "interspecies_synteny": "stage.interspecies_synteny",
    "pep": "stage.pep",
}


_REQUIRED_METRICS = {
    "identify": (("member_count", "最终成员数"),),
    "phylogeny": (
        ("sequences_count", "序列数"),
        ("msa_length", "多序列比对长度"),
    ),
    "motif": (
        ("motif_count", "Motif 类别数"),
        ("total_sites", "Motif 总位点数"),
        ("motif_width_range", "Motif 宽度范围"),
    ),
    "pep": (
        ("protein_count", "蛋白数"),
        ("tasks_success", "成功子任务数"),
        ("tasks_total", "子任务总数"),
    ),
}


_ARTIFACT_METRIC_ROLES = {
    ("identify", "member_count"): ("identified_members", "identified_members_id", "member_ids"),
    ("pep", "protein_count"): ("pep_properties", "properties_table"),
}


def _manifest_source(manifest: Mapping[str, Any], field: str) -> str:
    path = str(manifest.get("_manifest_path") or "stage manifest")
    return f"{Path(path).name}#metrics.{field}"


def _artifact_path(final_dir: Path, artifact: Any) -> Path | None:
    if not isinstance(artifact, Mapping):
        return None
    raw = artifact.get("full_path") or artifact.get("path")
    if not raw:
        return None
    path = Path(str(raw))
    if path.is_file():
        return path
    candidates = [final_dir / path.name]
    if path.is_absolute():
        job_index = next((index for index, part in enumerate(path.parts) if part.startswith("job_")), None)
        if job_index is not None:
            candidates.append(final_dir.parent.joinpath(*path.parts[job_index + 1 :]))
    else:
        candidates.append(final_dir.parent / path)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _artifact_by_suffix(final_dir: Path, manifest: Mapping[str, Any], suffix: str) -> Path | None:
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    for role in sorted(artifacts):
        if str(role).endswith(suffix):
            path = _artifact_path(final_dir, artifacts[role])
            if path is not None:
                return path
    return None


def _count_named_records(path: Path) -> int:
    suffix = path.suffix.lower()
    try:
        if suffix in {".fa", ".faa", ".fasta", ".pep"}:
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.startswith(">"))
        if suffix in {".tsv", ".csv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            with path.open("r", encoding="utf-8", newline="") as handle:
                return sum(1 for _ in csv.DictReader(handle, delimiter=delimiter))
        if suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                return len(value)
            if isinstance(value, Mapping):
                for key in ("members", "proteins", "records"):
                    if isinstance(value.get(key), list):
                        return len(value[key])
                raise ValueError("JSON does not contain a declared record list")
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"无法解析声明的报告数据源 {path}: {exc}") from exc


def _metric(
    final_dir: Path,
    stage: str,
    manifest: Mapping[str, Any],
    field: str,
    label: str,
) -> tuple[Any | MissingMetric, tuple[str, ...]]:
    metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), Mapping) else {}
    manifest_source = _manifest_source(manifest, field)
    if stage == "identify" and field == "member_count":
        for alias in ("member_count", "identified_members_count", "final_members"):
            if metrics.get(alias) is not None:
                source = _manifest_source(manifest, alias)
                return metrics[alias], (source,)
    elif field == "motif_width_range":
        motifs = metrics.get("motifs")
        if isinstance(motifs, Sequence) and not isinstance(motifs, (str, bytes)):
            widths = [item.get("width") for item in motifs if isinstance(item, Mapping) and item.get("width") is not None]
            if widths:
                try:
                    numeric = [int(value) for value in widths]
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"无法解析 {manifest_source.replace(field, 'motifs[].width')}") from exc
                source = _manifest_source(manifest, "motifs[].width")
                return (min(numeric), max(numeric)), (source,)
    elif field in metrics and metrics[field] is not None:
        return metrics[field], (manifest_source,)

    checked = [manifest_source]
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    for role in _ARTIFACT_METRIC_ROLES.get((stage, field), ()):
        checked.append(f"artifact:{role}")
        path = _artifact_path(final_dir, artifacts.get(role))
        if path is not None:
            return _count_named_records(path), (str(path),)
    return MissingMetric(label, tuple(checked)), tuple(checked)


def _gene_aliases(gene_id: str) -> tuple[str, ...]:
    value = str(gene_id).strip()
    aliases = {value, re.sub(r"\.T\d+$", "", value), re.sub(r"\.\d+$", "", value)}
    return tuple(alias for alias in aliases if alias)


def _parse_interspecies_artifacts(
    final_dir: Path,
    manifest: Mapping[str, Any],
) -> tuple[int, int, tuple[str, ...]] | None:
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    mapping_path = _artifact_path(final_dir, artifacts.get("gene_to_species"))
    collinearity_path = _artifact_by_suffix(final_dir, manifest, "_collinearity")
    if mapping_path is None or collinearity_path is None:
        return None
    groups: dict[str, str] = {}
    try:
        with mapping_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                gene_id = str(row.get("gene_id") or "").strip()
                group = str(row.get("group") or "").strip().lower()
                if gene_id and group in {"target", "ref"}:
                    for alias in _gene_aliases(gene_id):
                        groups[alias] = group
        alignment_blocks = 0
        target_ref_pairs = 0
        with collinearity_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("## Alignment"):
                    alignment_blocks += 1
                    continue
                match = re.search(r":\s*(\S+)\s+(\S+)", line) if line.startswith(" ") else None
                if not match:
                    continue
                pair_groups = {
                    groups[alias]
                    for gene_id in match.groups()
                    for alias in _gene_aliases(gene_id)
                    if alias in groups
                }
                if pair_groups == {"target", "ref"}:
                    target_ref_pairs += 1
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"无法解析种间共线性命名产物：{mapping_path}；{collinearity_path}: {exc}") from exc
    return alignment_blocks, target_ref_pairs, (str(collinearity_path), str(mapping_path))


def _sentence(sentence_id: str, text: str, kind: str, *evidence_ids: str) -> Sentence:
    return Sentence(sentence_id, text, kind, tuple(evidence_ids))


def _metrics(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    value = manifest.get("metrics")
    return value if isinstance(value, Mapping) else {}


def _artifacts(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    value = manifest.get("artifacts")
    return value if isinstance(value, Mapping) else {}


def _artifact_for_roles(
    final_dir: Path,
    manifest: Mapping[str, Any],
    *roles: str,
) -> tuple[Path | None, str | None]:
    artifacts = _artifacts(manifest)
    for role in roles:
        path = _artifact_path(final_dir, artifacts.get(role))
        if path is not None:
            return path, role
    for role, artifact in artifacts.items():
        if any(token in str(role) for token in roles):
            path = _artifact_path(final_dir, artifact)
            if path is not None:
                return path, str(role)
    return None, None


def _relative_artifact(final_dir: Path, manifest: Mapping[str, Any], *roles: str) -> tuple[str, str] | None:
    artifacts = _artifacts(manifest)
    for role in roles:
        artifact = artifacts.get(role)
        path = _artifact_path(final_dir, artifact)
        if path is not None:
            try:
                relative = path.relative_to(final_dir.parent)
            except ValueError:
                relative = Path("final") / path.name
            return relative.as_posix(), f"artifact:{role}"
        if isinstance(artifact, Mapping):
            raw = artifact.get("path")
            if raw and not Path(str(raw)).is_absolute() and ".." not in Path(str(raw)).parts:
                return Path(str(raw)).as_posix(), f"artifact:{role}"
    return None


def _tabular_rows(path: Path) -> list[dict[str, str]]:
    try:
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not lines:
            return []
        return [dict(row) for row in csv.DictReader(lines, delimiter="\t")]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"无法解析命名表格 {path}: {exc}") from exc


def _element_counts(path: Path) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                count = int(parts[0])
            except ValueError:
                continue
            result.append((parts[1].strip(), count))
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法解析启动子元件计数 {path}: {exc}") from exc
    return sorted(result, key=lambda item: (-item[1], item[0]))


def _key_artifact_sentence(
    final_dir: Path,
    manifest: Mapping[str, Any],
    section_id: str,
    label: str,
    *roles: str,
) -> Sentence | None:
    artifact = _relative_artifact(final_dir, manifest, *roles)
    if artifact is None:
        return None
    path, evidence = artifact
    return _sentence(f"{section_id}.artifact.{roles[0]}", f"关键产物：{label} `{path}`。", "fact", evidence)


def _stage_section(
    final_dir: Path,
    prefix: str,
    stage: str,
    status: str,
    manifest: Mapping[str, Any],
) -> ReportSection:
    section_id = STAGE_SECTION_IDS[stage]
    facts: list[Sentence] = []
    observations: list[Sentence] = []
    interpretations: list[Sentence] = []
    limitations: list[Sentence] = []
    recommendations: list[Sentence] = []

    if status != "success" or str(manifest.get("status") or "") != "success":
        facts.append(_sentence(f"{section_id}.status", f"阶段状态：`{status}`。", "fact", _manifest_source(manifest, "status")))
        limitations.append(
            _sentence(
                f"{section_id}.not_completed",
                "该阶段未成功完成，因此本报告不生成结果推断。",
                "limitation",
            )
        )
        return ReportSection(section_id, stage, tuple(facts), limitations=tuple(limitations))

    metrics = _metrics(manifest)
    if stage == "identify":
        member_count, sources = _metric(final_dir, stage, manifest, "member_count", "最终成员数")
        family = prefix.split(".", 1)[1] if "." in prefix else prefix
        facts.append(_sentence(f"{section_id}.object", "分析对象：目标物种完整蛋白集中的候选成员。", "fact"))
        if isinstance(member_count, MissingMetric):
            facts.append(_sentence(f"{section_id}.missing.member_count", member_count.render(), "fact", *sources))
        else:
            hmm = metrics.get("hmm_members")
            blast = metrics.get("blast_members")
            search = f"；HMM 与 BLAST 各支持 {hmm} 和 {blast} 个候选" if hmm is not None and blast is not None else ""
            observations.append(_sentence(
                f"{section_id}.observation.members",
                f"目标物种最终成员数：{member_count}。本阶段形成 {member_count} 个 {family}家族成员{search}。",
                "observation", *sources,
            ))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：最终名单是后续阶段的目标物种成员集合；搜索方法的一致数量支持集合稳定，但不等同于实验功能确认。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：成员边界仍受蛋白注释、搜索阈值和转录本选择规则影响。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "目标成员 FASTA", "identified_members", "target_Ft_identified_members_pep")
    elif stage == "phylogeny":
        sequences = metrics.get("sequences_count")
        msa_length = metrics.get("msa_length")
        species = metrics.get("scope_species_counts") if isinstance(metrics.get("scope_species_counts"), Mapping) else {}
        object_text = (
            f"分析对象：目标物种 {species.get('Ft')} 条与参考物种 {species.get('At')} 条蛋白合并的 {sequences} 条序列。"
            if species
            else f"分析对象：目标与参考成员合并集合中的 {sequences} 条蛋白序列。"
        )
        facts.append(_sentence(f"{section_id}.object", object_text, "fact", _manifest_source(manifest, "scope_species_counts")))
        observations.append(_sentence(f"{section_id}.observation", f"序列数：{sequences}；多序列比对长度：{msa_length}。目标与参考成员共 {sequences} 条序列进入比对，本阶段使用 {sequences} 条序列，比对长度为 {msa_length}。", "observation", _manifest_source(manifest, "sequences_count"), _manifest_source(manifest, "msa_length")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：该树用于比较序列关系并辅助分组；它分析的是目标加参考集合，不应与 24 个目标成员的鉴定集合混称。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：源 Manifest 未汇总各主要分支的支持值，不能据此报告小组稳定性等级。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "关系树", "tree")
    elif stage == "reference_classification":
        target_count = int(metrics.get("target_gene_count") or 0)
        all_rows = int(metrics.get("subfamily_rows") or 0)
        assignment_path, assignment_role = _artifact_for_roles(final_dir, manifest, "assignment_table")
        rows = _tabular_rows(assignment_path) if assignment_path else []
        counts: dict[str, int] = {}
        subfamilies: set[str] = set()
        for row in rows:
            confidence = str(row.get("confidence") or "").lower()
            if confidence:
                counts[confidence] = counts.get(confidence, 0) + 1
            label = str(row.get("assigned_subfamily") or "").strip()
            if label:
                subfamilies.add(label)
        facts.append(_sentence(f"{section_id}.object", f"分析对象：{target_count} 个目标成员与 {max(0, all_rows - target_count)} 个参考成员的联合分类。", "fact"))
        if rows:
            observations.append(_sentence(f"{section_id}.observation", f"分类结果：{len(rows)} 个目标成员分入 {len(subfamilies)} 个小组；高信心 {counts.get('high', 0)} 个、中信心 {counts.get('medium', 0)} 个、低信心 {counts.get('low', 0)} 个。", "observation", f"artifact:{assignment_role}"))
        else:
            missing = MissingMetric("分类信心分布", ("artifact:assignment_table",))
            facts.append(_sentence(f"{section_id}.missing", missing.render(), "fact", *missing.checked_sources))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：参考成员为目标成员提供小组标签，其中低信心标签只能作为后续分组线索。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：分类依赖参考成员覆盖和树拓扑；低信心成员不宜用于强分组结论。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "分类明细", "assignment_table")
    elif stage == "motif":
        motifs = metrics.get("motifs") if isinstance(metrics.get("motifs"), Sequence) else []
        widths = [int(item["width"]) for item in motifs if isinstance(item, Mapping) and item.get("width") is not None]
        count = metrics.get("motif_count")
        total_sites = metrics.get("total_sites")
        sequence_count = metrics.get("sequence_count") or metrics.get("protein_record_count")
        if sequence_count is None:
            facts.append(_sentence(f"{section_id}.object", "分析对象：当前 Motif Manifest 声明的蛋白集合。", "fact"))
            missing = MissingMetric("Motif 输入序列数", (_manifest_source(manifest, "sequence_count"),))
            facts.append(_sentence(f"{section_id}.missing.sequence_count", missing.render(), "fact", *missing.checked_sources))
        else:
            facts.append(_sentence(f"{section_id}.object", f"分析对象：目标与参考成员合并集合中的 {sequence_count} 条蛋白序列。", "fact"))
        if widths:
            observations.append(_sentence(f"{section_id}.observation", f"共识别 {count} 个 motif，总位点数为 {total_sites}，宽度范围为 {min(widths)}–{max(widths)}。", "observation", _manifest_source(manifest, "motifs")))
        else:
            missing = MissingMetric("Motif 宽度范围", (_manifest_source(manifest, "motifs[].width"),))
            facts.append(_sentence(f"{section_id}.missing", missing.render(), "fact", *missing.checked_sources))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：这些统计描述短片段的类别、总命中与长度范围；总位点数不能替代逐成员覆盖率。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：源结果未汇总同组排列一致性或异常成员，不能从总数推断特定小组功能分化。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "Motif 排列图", "motif_plot_png")
    elif stage == "domain":
        total = metrics.get("proteins_total")
        hit = metrics.get("proteins_with_domains")
        top_domains = metrics.get("top_domains") if isinstance(metrics.get("top_domains"), Sequence) else []
        top_text = "、".join(f"{item[0]} {item[1]} 次" for item in top_domains if isinstance(item, Sequence) and len(item) >= 2)
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标与参考成员合并集合中的 {total} 条蛋白。", "fact"))
        observations.append(_sentence(f"{section_id}.observation", f"Domain 覆盖为 {hit}/{total} 条蛋白；主要命中为 {top_text}。", "observation", _manifest_source(manifest, "proteins_with_domains"), _manifest_source(manifest, "top_domains")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：全部蛋白均有 Domain 命中，SBP 的普遍命中支持该合并集合保留家族典型区域；Ig_SPL7 只出现在部分命中记录中。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：命中次数不等于独立蛋白数，Ig_SPL7 的 12 次命中不能直接解释为 12 个功能不同成员。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "Domain 图", "domain_plot_png")
    elif stage == "structure":
        total = metrics.get("genes_total")
        covered = metrics.get("genes_with_structure")
        outliers = metrics.get("long_gene_outliers") if isinstance(metrics.get("long_gene_outliers"), Sequence) else []
        outlier = next((item for item in outliers if isinstance(item, Mapping)), None)
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标与参考合并集合的 {total} 个成员；结构注释实际匹配 {covered} 个目标成员。", "fact"))
        detail = f"；长基因异常 1 个，为 {outlier.get('gene_id')}（{outlier.get('length_bp')} bp，{outlier.get('cds_count')} 个 CDS）" if outlier else ""
        observations.append(_sentence(f"{section_id}.observation", f"结构覆盖为 {covered}/{total}{detail}。", "observation", _manifest_source(manifest, "genes_with_structure"), _manifest_source(manifest, "long_gene_outliers")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：24/40 的覆盖差异主要反映参考成员未纳入当前目标物种 GFF3；超长基因需要回到原始注释核对。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：未匹配的参考成员不能作为目标物种结构缺失；单个长基因也不能单独证明功能变化。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "结构明细", "structure_tsv")
    elif stage == "combo":
        parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), Mapping) else {}
        panels = [str(value).capitalize() for value in parameters.get("panels", [])]
        panel_text = "、".join(panels) if panels else "Motif、Domain、Structure"
        facts.append(_sentence(f"{section_id}.object", "分析对象：目标与参考成员合并集合的关系树及其多类结构证据。", "fact"))
        observations.append(_sentence(f"{section_id}.observation", f"组合图已将关系树与 {panel_text} 三类面板汇总到同一图中。", "observation", _manifest_source(manifest, "parameters.panels")))
        facts.append(_sentence(f"{section_id}.missing", "组合证据一致性数值：源结果未提供此指标（已检查：Combo Manifest、combo_plot_png、combo_plot_pdf）。", "fact"))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：该图适合逐行核对多类证据，但当前命名源没有可机器追溯的一致性评分。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：没有逐成员一致性表时，不指定稳定小组、异常成员或候选基因。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "组合图", "combo_plot_png")
    elif stage == "promoter":
        genes = metrics.get("genes_with_elements")
        total = metrics.get("total_elements")
        unique = metrics.get("unique_elements")
        counts_path, counts_role = _artifact_for_roles(final_dir, manifest, "element_counts")
        counts = _element_counts(counts_path) if counts_path else []
        category_path, category_role = _artifact_for_roles(final_dir, manifest, "position_distribution_summary", "promoter_category_summary")
        categories = _tabular_rows(category_path) if category_path else []
        enrichment_path, enrichment_role = _artifact_for_roles(final_dir, manifest, "enrichment_analysis")
        enrichment_rows = _tabular_rows(enrichment_path) if enrichment_path else []
        co_path, co_role = _artifact_for_roles(final_dir, manifest, "cooccurrence_analysis")
        co_rows = _tabular_rows(co_path) if co_path else []
        complexity_path, complexity_role = _artifact_for_roles(final_dir, manifest, "complexity_analysis")
        complexity_rows = _tabular_rows(complexity_path) if complexity_path else []
        facts.append(_sentence(f"{section_id}.object", f"分析对象：检测到顺式元件的 {genes} 个目标基因启动子。", "fact"))
        top_elements = "、".join(f"{name} {count} 个" for name, count in counts[:3]) or "源结果未提供主要元件排行"
        top_category = categories[0] if categories else {}
        category_name = top_category.get("big7") or top_category.get("Category")
        category_count = top_category.get("element_instances") or top_category.get("Count")
        category_text = ""
        if category_name and category_count and total:
            category_text = f"；主要类别为 {category_name} {category_count} 个（{int(category_count) / int(total) * 100:.1f}%）"
        enrichment_top = enrichment_rows[0] if enrichment_rows else {}
        enrichment_text = ""
        if enrichment_top:
            enrichment_text = f"；最高列示富集比为小组 {enrichment_top.get('Subfamily')} 的 {enrichment_top.get('Element')}（{enrichment_top.get('EnrichmentRatio')}）"
        co_top = co_rows[0] if co_rows else {}
        co_text = ""
        if co_top:
            co_text = f"；最常见共现为 {co_top.get('Element1')}—{co_top.get('Element2')}（{co_top.get('Co-occurrenceCount')} 个基因）"
        complex_top = complexity_rows[0] if complexity_rows else {}
        complex_text = ""
        if complex_top:
            complex_text = f"；复杂度最高的候选为 {complex_top.get('Gene')}（得分 {complex_top.get('ComplexityScore')}）"
        observations.append(_sentence(f"{section_id}.observation", f"{genes} 个目标基因共记录 {total} 个元件、{unique} 种类型；主要元件为 {top_elements}{category_text}{enrichment_text}{co_text}{complex_text}。", "observation", *(f"artifact:{role}" for role in (counts_role, category_role, enrichment_role, co_role, complexity_role) if role)))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：类别比例、共现和复杂度描述的是上游序列预测模式；富集比可用于定位值得复核的小组—元件组合。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：仅 23 个目标基因有元件记录，且序列片段出现、共现或高复杂度都不能证明真实调控。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "启动子汇总图", "viz_Ft.SPL_promoter_v6_combined", "promoter_v6_combined")
    elif stage == "tfbs":
        promoters = metrics.get("family_promoter_count")
        sites = metrics.get("family_site_count")
        motif_count = metrics.get("family_motif_count")
        background = metrics.get("background_promoter_count")
        summary_path, summary_role = _artifact_for_roles(final_dir, manifest, "motif_summary")
        rows = _tabular_rows(summary_path) if summary_path else []
        rows.sort(key=lambda row: int(float(row.get("family_sites") or 0)), reverse=True)
        top = rows[0] if rows else {}
        facts.append(_sentence(f"{section_id}.object", f"分析对象：{promoters} 条目标成员启动子；Motif 模型来自 {metrics.get('motif_database', '声明数据库')}。", "fact"))
        top_text = f"；最高频 Motif {top.get('motif_id')} 有 {top.get('family_sites')} 个位点，覆盖 {top.get('family_genes_with_site')} 个基因" if top else ""
        observations.append(_sentence(f"{section_id}.observation", f"{promoters} 条目标启动子共得到 {sites} 个预测位点、{motif_count} 个有命中的 Motif{top_text}。", "observation", _manifest_source(manifest, "family_site_count"), *(tuple([f"artifact:{summary_role}"]) if summary_role else ())))
        if int(background or 0) == 0:
            facts.append(_sentence(f"{section_id}.no_background", "背景启动子为 0，不能据此进行家族相对背景的富集判断。", "fact", _manifest_source(manifest, "background_promoter_count")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：高频位点表示序列扫描中出现较多，只能作为潜在结合线索，不能写成转录因子已调控目标基因。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：Motif 使用最近分类物种投射且缺少背景集合，统计富集和物种特异调控均无法确认。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "Motif 汇总表", "motif_summary")
    elif stage == "synteny":
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标物种全基因组 {metrics.get('gene_count')} 个基因，而非仅家族成员。", "fact"))
        observations.append(_sentence(f"{section_id}.observation", f"全基因组结果记录 {metrics.get('synteny_blocks')} 个共线性区块、{metrics.get('gene_pairs')} 个基因对，并绘制 {metrics.get('circos_chromosomes_plotted')} 条染色体。", "observation", _manifest_source(manifest, "synteny_blocks"), _manifest_source(manifest, "gene_pairs")))
        facts.append(_sentence(f"{section_id}.missing", "源结果未提供家族专属复制基因对（已检查：Synteny Manifest、collinearity artifact、dup_class_log artifact）。", "fact"))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：这些数字刻画全基因组共线性背景，不能直接归因于 SPL 家族扩张。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：缺少家族成员专属 links 或复制类型表，本节不判断大片段复制或串联复制孰为家族主要来源。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "Circos 图", "circos_png")
    elif stage == "karyotype":
        species = metrics.get("species") if isinstance(metrics.get("species"), Mapping) else {}
        item = next(iter(species.values()), {}) if species else metrics
        genes = item.get("gene_count") if isinstance(item, Mapping) else None
        chromosomes = item.get("chromosome_count") if isinstance(item, Mapping) else None
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标物种的 {genes} 个家族成员。", "fact"))
        observations.append(_sentence(f"{section_id}.observation", f"核型分析将{genes}个家族成员定位到{chromosomes}条染色体。", "observation", _manifest_source(manifest, "species.*.gene_count"), _manifest_source(manifest, "species.*.chromosome_count")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：染色体位置图确认了目标成员的基因组分布范围，可与家族专属复制证据联合阅读。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：Manifest 未汇总每条染色体的成员数，不能仅凭 24/8 判断分布均匀性或成簇关系。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "核型图", "Ft_karyotype_png")
    elif stage == "interspecies_synteny":
        parsed = _parse_interspecies_artifacts(final_dir, manifest)
        target = metrics.get("target_family_count")
        reference = metrics.get("ref_family_count")
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标物种 {target} 个与参考物种 {reference} 个家族成员，并结合两物种全基因组共线性。", "fact"))
        if parsed is None:
            missing = MissingMetric(
                "种间共线性区块与家族成员配对",
                ("artifact:gene_to_species", "artifact:*_collinearity"),
            )
            facts.append(
                _sentence(
                    f"{section_id}.missing.collinearity_summary",
                    missing.render(),
                    "fact",
                    *missing.checked_sources,
                )
            )
        else:
            alignment_blocks, target_ref_pairs, sources = parsed
            observations.append(
                _sentence(
                    f"{section_id}.observation.summary",
                    f"共线性区块数：{alignment_blocks}（全基因组 block {alignment_blocks} 个）；"
                    f"家族成员目标—参考配对数：{target_ref_pairs}（目标-参考家族 {target_ref_pairs} 对）。",
                    "observation",
                    *sources,
                )
            )
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：全基因组区块提供跨物种背景，2 个目标—参考家族配对是当前命名产物中可直接追溯的家族关系。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：共线性配对只能支持保留关系线索，不能直接移植参考物种功能或断言一一对应。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "标记版种间共线性图", "synteny_marked_png")
    elif stage == "pep":
        protein_count = metrics.get("protein_count")
        success = metrics.get("tasks_success")
        total_tasks = metrics.get("tasks_total")
        counts = metrics.get("subcellular_counts") if isinstance(metrics.get("subcellular_counts"), Mapping) else {}
        facts.append(_sentence(f"{section_id}.object", f"分析对象：目标与参考成员合并集合中的 {protein_count} 条蛋白。", "fact"))
        localization = ""
        if counts:
            localization = f"；亚细胞定位预测为 Nucleus {counts.get('Nucleus', 0)}、Cytoplasm {counts.get('Cytoplasm', 0)}、Chloroplast {counts.get('Chloroplast', 0)}"
        else:
            missing = MissingMetric("亚细胞定位分类计数", (_manifest_source(manifest, "subcellular_counts"),))
            facts.append(_sentence(f"{section_id}.missing.localization", missing.render(), "fact", *missing.checked_sources))
        observations.append(_sentence(f"{section_id}.observation", f"蛋白数：{protein_count}；子任务成功数：{success}/{total_tasks}。本阶段覆盖 {protein_count} 个蛋白，子任务成功数为 {success}/{total_tasks}{localization}。", "observation", _manifest_source(manifest, "protein_count"), _manifest_source(manifest, "subcellular_counts")))
        interpretations.append(_sentence(f"{section_id}.interpretation", "解释：核定位预测占多数，与转录因子研究方向相容，但这里只能作为定位线索。", "interpretation"))
        limitations.append(_sentence(f"{section_id}.limitation", "限制：理化性质、跨膜与亚细胞定位均为计算预测；当前汇总不能替代逐蛋白实验验证。", "limitation"))
        artifact = _key_artifact_sentence(final_dir, manifest, section_id, "亚细胞定位表", "subcellular_data")
    else:
        artifact = None

    if artifact is not None:
        facts.append(artifact)
    return ReportSection(
        section_id,
        stage,
        tuple(facts),
        tuple(observations),
        tuple(interpretations),
        tuple(limitations),
        tuple(recommendations),
    )


def build_report_data(
    final_dir: Path | str,
    prefix: str,
    planned: Sequence[str],
    statuses: Mapping[str, str],
    manifests: Mapping[str, Mapping[str, Any]],
) -> ReportData:
    """Build the only deterministic data source used by both render modes."""

    final_path = Path(final_dir)
    unsupported_stages = tuple(stage for stage in planned if stage not in STAGE_SECTION_IDS)
    included = tuple(
        stage
        for stage in planned
        if stage in STAGE_SECTION_IDS
        and statuses.get(stage) == "success"
        and str((manifests.get(stage) or {}).get("status") or "") == "success"
    )
    report_status = "partial" if len(included) != len(planned) else "final"
    success_count = len(included)
    failed_count = len(planned) - success_count
    stage_sections = [
        _stage_section(final_path, prefix, stage, str(statuses.get(stage) or "missing"), manifests.get(stage) or {})
        for stage in planned
        if stage in STAGE_SECTION_IDS
    ]
    stage_sections_by_name = {section.stage: section for section in stage_sections}
    summary_texts = [
        f"本次按用户计划检查 {len(planned)} 个阶段，其中 {success_count} 个成功结果纳入正文。",
        f"报告状态为{'最终报告' if report_status == 'final' else '部分报告'}，未纳入阶段数为 {failed_count}。",
    ]
    for stage in (
        "identify", "phylogeny", "reference_classification", "domain", "structure",
        "promoter", "karyotype", "pep",
    ):
        section = stage_sections_by_name.get(stage)
        if section and section.observations and len(summary_texts) < 9:
            summary_texts.append(section.observations[0].text.replace("。", "；").rstrip("；") + "。")
    for stage in ("identify", "phylogeny"):
        section = stage_sections_by_name.get(stage)
        if section:
            for sentence in section.facts:
                if sentence.text.startswith("分析对象：") and len(summary_texts) < 5:
                    summary_texts.append(sentence.text)
    while len(summary_texts) < 5:
        summary_texts.append("正文只纳入计划内成功阶段，并在各节分别声明实际分析对象。")
    summary_texts.append("本摘要只组合下文可追溯的阶段事实，不据此指定候选基因或推断功能因果。")
    summary_facts = tuple(
        _sentence(f"common.front.summary.{index}", text, "fact")
        for index, text in enumerate(summary_texts[:10], start=1)
    )
    completion_facts = [
        _sentence(
            "common.completeness.counts",
            f"本次计划 {len(planned)} 个阶段，纳入解读 {success_count} 个，未纳入 {failed_count} 个。",
            "fact",
        )
    ]
    for stage in planned:
        supported = stage in STAGE_SECTION_IDS
        completion_facts.append(
            _sentence(
                f"common.completeness.stage.{stage}",
                f"计划阶段 `{stage}`：源状态 `{statuses.get(stage, 'missing')}`；"
                f"模板支持：{'是' if supported else '不支持'}；"
                f"纳入解读：{'是' if stage in included else '否'}。",
                "fact",
            )
        )
    sections: list[ReportSection] = [
        ReportSection(
            "common.front",
            None,
            facts=summary_facts,
        ),
        ReportSection(
            "common.route",
            None,
            facts=(
                _sentence("common.route.stages", f"本报告按计划阶段顺序解读：{'、'.join(planned)}。", "fact"),
            ),
        ),
        ReportSection(
            "common.completeness",
            None,
            facts=tuple(completion_facts),
        ),
    ]
    sections.extend(stage_sections)
    # The integrated conclusion has a fixed evidence contract. Do not take the
    # first arbitrary observations because plan ordering can displace one of
    # the three core facts requested for delivery.
    integrated_observations = [
        stage_sections_by_name[stage].observations[0]
        for stage in ("identify", "phylogeny", "reference_classification")
        if stage in stage_sections_by_name and stage_sections_by_name[stage].observations
    ]
    sections.extend(
        [
            ReportSection(
                "common.integrated",
                None,
                facts=tuple(
                    _sentence(
                        f"common.integrated.fact.{index}",
                        sentence.text,
                        "fact",
                        *sentence.evidence_ids,
                    )
                    for index, sentence in enumerate(integrated_observations, start=1)
                ),
                interpretations=(),
            ),
            ReportSection(
                "common.limitations",
                None,
                limitations=(
                    _sentence(
                        "common.limitations.general",
                        "基因组与注释质量、分析阈值和计算预测边界都会影响结果，缺少实验信息时不能确定具体生物学功能。",
                        "limitation",
                    ),
                ),
            ),
            ReportSection(
                "common.recommendations",
                None,
                recommendations=(
                    _sentence(
                        "common.recommendations.general",
                        "优先核对异常成员的序列与注释，并用表达、定位或功能实验验证由至少两类独立证据支持的候选。",
                        "recommendation",
                    ),
                ),
            ),
            ReportSection(
                "common.checklist",
                None,
                recommendations=(
                    _sentence(
                        "common.checklist.traceability",
                        "交付前应确认每项结论均能追溯到当前身份的 Manifest 或命名结果文件。",
                        "recommendation",
                    ),
                ),
            ),
        ]
    )
    return ReportData(
        prefix,
        tuple(planned),
        dict(statuses),
        included,
        unsupported_stages,
        report_status,
        tuple(sections),
    )
