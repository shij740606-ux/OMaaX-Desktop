"""Bridge saved literature methodology plans into bbav5 job state.

This module deliberately does not execute stages. It binds a reviewed plan to a
job, checks whether user data/config are present, and prepares params for the
existing MAS planner.
"""

from __future__ import annotations

import csv
import copy
import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


WHOLE_GENOME_EXTENSIONS = ('.fa', '.fasta', '.faa', '.fna', '.fas', '.pep', '.cds')
GFF_EXTENSIONS = ('.gff', '.gff3', '.gtf')
FIXED_METHODOLOGY_STAGE_ORDER = [
    'genome_prepare',
    'identify',
    'phylogeny',
    'motif',
    'domain',
    'structure',
    'promoter',
    'combo',
    'reference_classification',
    'subfamily_confirm',
    'synteny',
    'interspecies_synteny',
    'karyotype',
    'ppi',
    'expression',
    'pep',
    'rnaseq_qc',
    'rnaseq_align',
    'rnaseq_quant',
    'rnaseq_sample_qc',
    'rnaseq_de',
    'rnaseq_go_enrich',
    'rnaseq_kegg_enrich',
    'rnaseq_fgsea',
    'rnaseq_wgcna',
]
METHODOLOGY_RAW_RNASEQ_STAGES = {
    'rnaseq_qc', 'rnaseq_align', 'rnaseq_quant', 'rnaseq_sample_qc', 'rnaseq_de',
    'rnaseq_go_enrich', 'rnaseq_kegg_enrich', 'rnaseq_fgsea', 'rnaseq_wgcna',
}
METHODOLOGY_COMPLETION_PARADIGM_VERSION = 'methodology_evidence_completion.v2'
COMMON_PLANT_SPECIES_ALIASES = {
    'A. thaliana': 'Arabidopsis thaliana',
    'B. vulgaris': 'Beta vulgaris',
    'G. max': 'Glycine max',
    'O. sativa': 'Oryza sativa',
    'S. lycopersicum': 'Solanum lycopersicum',
    'V. vinifera': 'Vitis vinifera',
    'Z. mays': 'Zea mays',
}
COMMON_SPECIES_NAME_ALIASES = {
    'Fagopyrum tataricum': ['tartary buckwheat'],
    'Arabidopsis thaliana': ['arabidopsis'],
    'Oryza sativa': ['rice'],
}
METHODOLOGY_STAGE_ALIASES = {
    'gene_family_identification': 'identify',
    'identification': 'identify',
    'identify': 'identify',
    'phylogenetic_analysis': 'phylogeny',
    'phylogeny': 'phylogeny',
    'motif_analysis': 'motif',
    'motif': 'motif',
    'domain_analysis': 'domain',
    'conserved_domain': 'domain',
    'domain': 'domain',
    'gene_structure': 'structure',
    'intron_exon_structure': 'structure',
    'structure': 'structure',
    'promoter_analysis': 'promoter',
    'cis_element': 'promoter',
    'synteny': 'synteny',
    'collinearity': 'synteny',
    'chromosome_location': 'karyotype',
    'chromosomal_location': 'karyotype',
    'karyotype': 'karyotype',
    'expression_analysis': 'expression',
    'expression': 'expression',
    'protein_properties': 'pep',
    'pep': 'pep',
    'ppi': 'ppi',
}
ACCESSION_PATTERN = re.compile(r'\b(?:GSE|GSM|SRP|SRR|SRX|SRS|PRJNA|PRJEB|ERP|ERR|ERX|ERS|DRP|DRR|DRX|DRS)\d+\b', re.I)
REFERENCE_DB_PATTERN = re.compile(r'\b(?:UniProt|TAIR|Pfam|InterPro|PlantTFDB|Ensembl|Phytozome)\b', re.I)
FIGURE_TABLE_PATTERN = re.compile(r'\b((?:Supplementary\s+)?(?:Fig(?:ure)?|Table)\.?\s+[A-Za-z]?\d+[A-Za-z0-9.-]*)\s*[:. ]\s*([^.\n]{0,220})', re.I)
ACCESSION_CONTEXT_PATTERN = re.compile(r'(GEO|SRA|BioProject|ENA|NCBI|RNA[- ]?seq|transcriptome|expression|raw\s+data|downloaded|deposited|accession)', re.I)
RAW_RNASEQ_CONTEXT_PATTERN = re.compile(r'(RNA[- ]?seq|raw\s+data|SRA|BioProject|transcriptome|sequencing)', re.I)
EXPRESSION_MATRIX_CONTEXT_PATTERN = re.compile(r'(expression\s+matrix|FPKM|TPM|RPKM|counts?\s+matrix|normalized\s+expression)', re.I)


@dataclass(frozen=True)
class EvidenceCandidate:
    object_type: str
    value: str
    role: str = ''
    target_field: str = ''
    source_file: str = ''
    source_field: str = ''
    evidence_text: str = ''
    confidence_rule: str = ''
    guardrail_status: str = 'accepted'

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def load_methodology_plan(path: str | Path) -> dict[str, Any]:
    plan_path = Path(path)
    if not plan_path.exists():
        raise FileNotFoundError(f'Methodology plan not found: {plan_path}')
    data = json.loads(plan_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Methodology plan must be a JSON object: {plan_path}')
    return data


def bound_methodology_plan_path(input_root: Path, job_id: str) -> Path:
    return Path(input_root) / job_id / 'methodology_plan.json'


def bind_methodology_plan(
    plan: dict[str, Any],
    *,
    job_id: str,
    input_root: Path,
    workspace_root: Path,
    source: str = '',
) -> dict[str, Any]:
    if not job_id:
        raise ValueError('job_id is required')
    input_dir = Path(input_root) / job_id
    final_dir = Path(workspace_root) / job_id / 'final'
    input_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    plan = complete_methodology_plan_for_execution(plan, source=source)
    plan_path = input_dir / 'methodology_plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    binding = {
        'job_id': job_id,
        'plan_id': plan.get('plan_id', ''),
        'paper_id': plan.get('paper_id', ''),
        'gene_family': plan.get('gene_family', ''),
        'source': source,
        'plan_path': str(plan_path),
        'bound_at': datetime.now().isoformat(timespec='seconds'),
        'schema_version': plan.get('schema_version', ''),
    }
    binding_path = final_dir / 'methodology_plan.binding.json'
    binding_path.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'success': True, 'job_id': job_id, 'plan_path': str(plan_path), 'binding_path': str(binding_path), 'binding': binding}


def bind_methodology_plan_from_path(
    source_path: str | Path,
    *,
    job_id: str,
    input_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    plan = load_methodology_plan(source_path)
    result = bind_methodology_plan(plan, job_id=job_id, input_root=input_root, workspace_root=workspace_root, source=str(source_path))
    return result


def methodology_agent_params(plan: dict[str, Any]) -> dict[str, Any]:
    plan = complete_methodology_plan_for_execution(plan)
    prefill = plan.get('bbav5_prefill') if isinstance(plan.get('bbav5_prefill'), dict) else {}
    route_stages = []
    for route in plan.get('execution_routes') or []:
        if isinstance(route, dict) and isinstance(route.get('stage_order'), list):
            route_stages = [str(item) for item in route.get('stage_order') if str(item)]
            break
    stages = _fixed_methodology_stage_order(route_stages)
    gene_family = prefill.get('geneFamily') or plan.get('gene_family') or _infer_gene_family_from_plan(plan)
    params = {
        'source': 'methodology',
        'methodology': True,
        'methodologyPlanId': plan.get('plan_id', ''),
        'sourcePaperId': plan.get('paper_id', ''),
        'geneFamily': gene_family,
        'targetSpecies': _list(prefill.get('targetSpecies')),
        'referenceSpecies': _list(prefill.get('referenceSpecies')),
        'comparativeSpecies': _list(prefill.get('comparativeSpecies')),
        'identifyBlastReferenceSpecies': _list(prefill.get('identifyBlastReferenceSpecies')),
        'primaryReferenceSpecies': _list(prefill.get('primaryReferenceSpecies')),
        'requiredPfams': prefill.get('requiredPfams') or _infer_required_pfams_from_plan(plan),
        'scopeMode': prefill.get('scopeMode') or _scope_mode(prefill),
        'requestedMethodologyStages': stages,
    }
    params['refSpeciesList'] = _unique([*params['referenceSpecies'], *params['comparativeSpecies']])
    params['methodology_workflow_contract_version'] = 'methodology_fixed_workflow.v1'
    params['requiresDataPreparation'] = True
    params['requiresSubfamilyClassification'] = True
    if params['targetSpecies']:
        first = params['targetSpecies'][0]
        family = params['geneFamily']
        if first and family:
            params['prefix'] = f'{first}.{family}' if len(params['targetSpecies']) == 1 else f'Target.{family}'
    return params


def _fixed_methodology_stage_order(route_stages: list[str]) -> list[str]:
    route_set = {stage for stage in route_stages if stage and stage != 'external_or_manual'}
    ordered = []
    for stage in FIXED_METHODOLOGY_STAGE_ORDER:
        if stage in route_set:
            ordered.append(stage)
    return _unique(ordered)


def complete_methodology_plan_for_execution(plan: dict[str, Any], *, source: str = '') -> dict[str, Any]:
    """Complete a methodology plan through one evidence-to-execution paradigm."""
    if not isinstance(plan, dict):
        return plan
    original = copy.deepcopy(plan)
    candidates = _collect_methodology_evidence_candidates(plan, source=source)
    completed = complete_methodology_plan_species(plan, source=source)
    completed = _complete_methodology_required_pfams(completed)
    completed = _complete_methodology_stage_order(completed)
    completed = _complete_methodology_dataset_gates(completed, candidates)
    completed = _complete_methodology_expression_requirements(completed, candidates)
    completed = _complete_methodology_reproduction_targets(completed, candidates)
    _attach_methodology_completion_audit(completed, original, candidates, source=source)
    return completed


def _collect_methodology_evidence_candidates(plan: dict[str, Any], *, source: str = '') -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    candidates.extend(_candidate_records_from_plan(plan))
    for batch_dir in _methodology_batch_dirs(plan, source=source):
        candidates.extend(_candidate_records_from_batch_dir(batch_dir))
    return _unique_candidates(candidates)


def _candidate_records_from_plan(plan: dict[str, Any]) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    for record in _species_records_from_prefill(plan):
        candidates.append(EvidenceCandidate(
            object_type='species',
            value=_canonical_latin_species(record.get('species_name')),
            role=_normalize_species_role(record.get('species_role')),
            target_field='bbav5_prefill',
            source_field='bbav5_prefill',
            confidence_rule='prefill_species',
        ))
    for record in _species_records_from_requirements(plan.get('data_requirements')):
        candidates.append(EvidenceCandidate(
            object_type='species',
            value=_canonical_latin_species(record.get('species_name')),
            role=_normalize_species_role(record.get('species_role')),
            target_field='data_requirements',
            source_field='data_requirements',
            confidence_rule='existing_requirement_species',
        ))
    for record in _species_records_from_plan_field(plan.get('species_summary')):
        candidates.append(EvidenceCandidate(
            object_type='species',
            value=_canonical_latin_species(record.get('species_name')),
            role=_normalize_species_role(record.get('species_role')),
            target_field='bbav5_prefill',
            source_field='species_summary',
            confidence_rule='structured_species_summary',
        ))

    text = _plan_species_text(plan)
    candidates.extend(_text_evidence_candidates(text, source_field='plan_text'))
    candidates.extend(_candidate_stage_records_from_plan_value(plan))
    return [candidate for candidate in candidates if candidate.value]


def _candidate_stage_records_from_plan_value(value: Any, *, source_field: str = 'plan') -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []

    def walk(item: Any, field: str = source_field, context: str = '') -> None:
        if isinstance(item, dict):
            text = _row_text(item)
            for key, child in item.items():
                if key in {'bbav5_stage', 'mapped_bbav5_stage', 'bbav5_stage_id', 'normalized_stage', 'stage'}:
                    stage = _normalize_methodology_stage(child)
                    if stage:
                        candidates.append(EvidenceCandidate(
                            object_type='stage',
                            value=stage,
                            target_field='execution_routes.stage_order',
                            source_field=key,
                            evidence_text=(text or context)[:500],
                            confidence_rule='structured_stage_field',
                        ))
                walk(child, str(key), text or context)
        elif isinstance(item, list):
            for child in item:
                walk(child, field, context)

    walk(value)
    return candidates


def _candidate_records_from_batch_dir(batch_dir: Path) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []
    for csv_name in ('species_summary.csv', 'review_template.csv', 'summary.csv', 'methodology_parameter_audit.csv'):
        path = batch_dir / csv_name
        if not path.exists():
            continue
        try:
            with path.open('r', encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    candidates.extend(_candidate_records_from_row(row, source_file=str(path), source_field=csv_name))
        except Exception:
            continue
    for json_name in ('observations.json', 'alignment.json'):
        path = batch_dir / json_name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        candidates.extend(_candidate_records_from_json_value(data, source_file=str(path)))
    return candidates


def _candidate_records_from_row(row: dict[str, Any], *, source_file: str, source_field: str) -> list[EvidenceCandidate]:
    text = _row_text(row)
    candidates = _text_evidence_candidates(text, source_file=source_file, source_field=source_field)
    species = row.get('species_name') or row.get('species')
    if species:
        candidates.append(EvidenceCandidate(
            object_type='species',
            value=_canonical_latin_species(species),
            role=_normalize_species_role(row.get('species_role') or row.get('role')),
            target_field='bbav5_prefill',
            source_file=source_file,
            source_field='species_name',
            evidence_text=str(row.get('evidence_sentence') or text)[:500],
            confidence_rule='structured_species_summary',
        ))
    for key in ('bbav5_stage', 'mapped_bbav5_stage', 'bbav5_stage_id', 'normalized_stage', 'stage'):
        stage = _normalize_methodology_stage(row.get(key))
        if stage:
            candidates.append(EvidenceCandidate(
                object_type='stage',
                value=stage,
                target_field='execution_routes.stage_order',
                source_file=source_file,
                source_field=key,
                evidence_text=text[:500],
                confidence_rule='structured_stage_field',
            ))
    return candidates


def _candidate_records_from_json_value(value: Any, *, source_file: str) -> list[EvidenceCandidate]:
    candidates: list[EvidenceCandidate] = []

    def walk(item: Any, source_field: str = '') -> None:
        if isinstance(item, dict):
            text = _row_text(item)
            candidates.extend(_text_evidence_candidates(text, source_file=source_file, source_field=source_field or 'json_object'))
            species = item.get('species_name') or item.get('species')
            if species:
                candidates.append(EvidenceCandidate(
                    object_type='species',
                    value=_canonical_latin_species(species),
                    role=_normalize_species_role(item.get('species_role') or item.get('role')),
                    target_field='bbav5_prefill',
                    source_file=source_file,
                    source_field='species_name',
                    evidence_text=str(item.get('evidence_sentence') or text)[:500],
                    confidence_rule='structured_json_species',
                ))
            for key, child in item.items():
                if key in {'bbav5_stage', 'mapped_bbav5_stage', 'bbav5_stage_id', 'normalized_stage', 'stage'}:
                    stage = _normalize_methodology_stage(child)
                    if stage:
                        candidates.append(EvidenceCandidate(
                            object_type='stage',
                            value=stage,
                            target_field='execution_routes.stage_order',
                            source_file=source_file,
                            source_field=key,
                            evidence_text=text[:500],
                            confidence_rule='structured_stage_field',
                        ))
                walk(child, str(key))
        elif isinstance(item, list):
            for child in item:
                walk(child, source_field)

    walk(value)
    return candidates


def _text_evidence_candidates(text: str, *, source_file: str = '', source_field: str = '') -> list[EvidenceCandidate]:
    value = str(text or '')
    if not value:
        return []
    candidates: list[EvidenceCandidate] = []
    for record in _species_records_from_text(value, default_role='reference_species'):
        candidates.append(EvidenceCandidate(
            object_type='species',
            value=_canonical_latin_species(record.get('species_name')),
            role='reference',
            target_field='bbav5_prefill.referenceSpecies',
            source_file=source_file,
            source_field=source_field,
            evidence_text=_evidence_excerpt(value, record.get('species_name')),
            confidence_rule='alias_or_full_name_in_method_evidence',
        ))
    for pfam in re.findall(r'PF\d{5}', value):
        candidates.append(EvidenceCandidate(
            object_type='pfam',
            value=pfam,
            target_field='bbav5_prefill.requiredPfams',
            source_file=source_file,
            source_field=source_field,
            evidence_text=_evidence_excerpt(value, pfam),
            confidence_rule='pfam_evidence_completion',
        ))
    for accession in ACCESSION_PATTERN.findall(value):
        excerpt = _sentence_excerpt(value, accession)
        accession_role = _classify_accession_context(excerpt)
        guardrail = 'accepted' if accession_role in {'raw_rnaseq_accession', 'expression_matrix_accession'} else accession_role
        target_field = 'manual_gates' if accession_role == 'raw_rnaseq_accession' else 'data_requirements' if accession_role == 'expression_matrix_accession' else 'methodology_completion_audit'
        candidates.append(EvidenceCandidate(
            object_type='dataset_accession',
            value=accession.upper(),
            role=accession_role,
            target_field=target_field,
            source_file=source_file,
            source_field=source_field,
            evidence_text=excerpt,
            confidence_rule='accession_with_method_context',
            guardrail_status=guardrail,
        ))
    for match in REFERENCE_DB_PATTERN.finditer(value):
        db_name = match.group(0)
        candidates.append(EvidenceCandidate(
            object_type='reference_database',
            value=db_name,
            role='reference_database_accession',
            target_field='methodology_completion_audit',
            source_file=source_file,
            source_field=source_field,
            evidence_text=_sentence_excerpt(value, db_name),
            confidence_rule='reference_database_source',
            guardrail_status='audit_only_reference_database',
        ))
    for label, title in FIGURE_TABLE_PATTERN.findall(value):
        target_type = 'table' if re.search(r'Table', label, re.I) else 'figure'
        candidates.append(EvidenceCandidate(
            object_type='figure_table_target',
            value=_normalize_figure_table_label(label),
            role=target_type,
            target_field='reproduction_targets',
            source_file=source_file,
            source_field=source_field,
            evidence_text=(title or '').strip()[:500],
            confidence_rule='caption_or_figure_table_label',
        ))
    return candidates


def _classify_accession_context(excerpt: str) -> str:
    value = str(excerpt or '')
    if EXPRESSION_MATRIX_CONTEXT_PATTERN.search(value):
        return 'expression_matrix_accession'
    if RAW_RNASEQ_CONTEXT_PATTERN.search(value):
        return 'raw_rnaseq_accession'
    if ACCESSION_CONTEXT_PATTERN.search(value):
        return 'rejected_ambiguous_accession_context'
    return 'rejected_no_method_data_context'


def _row_text(row: dict[str, Any]) -> str:
    preferred = []
    for key in ('evidence_sentence', 'raw_method_text', 'caption', 'title', 'input_data', 'output_data', 'parameters'):
        value = row.get(key)
        if value:
            preferred.append(str(value))
    if preferred:
        return '\n'.join(preferred)
    return '\n'.join(str(value) for value in row.values() if value)


def complete_methodology_plan_species(plan: dict[str, Any], *, source: str = '') -> dict[str, Any]:
    """Fill executable species lists from reviewed methodology evidence.

    This is intentionally a bridge-side safety net. It only promotes species
    that appear in structured species_summary rows or in method/figure evidence,
    and it ignores species that occur only in abbreviation-definition clauses.
    """
    if not isinstance(plan, dict):
        return plan
    completed = copy.deepcopy(plan)
    records = _methodology_species_records(completed, source=source)
    if not records:
        return completed

    role_groups = _resolve_methodology_species_role_groups(completed, records, source=source)
    prefill = completed.get('bbav5_prefill')
    if not isinstance(prefill, dict):
        prefill = {}
        completed['bbav5_prefill'] = prefill

    targets = role_groups['targets']
    references = role_groups['references']
    comparatives = role_groups['comparatives']
    target_keys = {_species_key(item) for item in targets}
    reference_keys = {_species_key(item) for item in references}
    comparative_keys = {_species_key(item) for item in comparatives}

    prefill['targetSpecies'] = targets
    prefill['referenceSpecies'] = references
    prefill['comparativeSpecies'] = comparatives
    if references and not _list(prefill.get('primaryReferenceSpecies')):
        prefill['primaryReferenceSpecies'] = [references[0]]
    if references and not _list(prefill.get('identifyBlastReferenceSpecies')):
        prefill['identifyBlastReferenceSpecies'] = [references[0]]
    prefill['scopeMode'] = prefill.get('scopeMode') or _scope_mode(prefill)

    completed['data_requirements'] = _complete_species_requirements(
        completed.get('data_requirements'),
        targets=targets,
        references=references,
        comparatives=comparatives,
    )
    return completed


def _complete_methodology_required_pfams(plan: dict[str, Any]) -> dict[str, Any]:
    completed = copy.deepcopy(plan)
    prefill = completed.get('bbav5_prefill')
    if not isinstance(prefill, dict):
        prefill = {}
        completed['bbav5_prefill'] = prefill
    if not str(prefill.get('requiredPfams') or '').strip():
        inferred = _infer_required_pfams_from_plan(completed) or _infer_required_pfams_from_batch_dirs(completed)
        if inferred:
            prefill['requiredPfams'] = inferred
    return completed


def _complete_methodology_stage_order(plan: dict[str, Any]) -> dict[str, Any]:
    completed = copy.deepcopy(plan)
    inferred = _infer_stage_order_from_plan(completed)
    if not inferred:
        return completed
    routes = completed.get('execution_routes')
    if not isinstance(routes, list) or not routes:
        completed['execution_routes'] = [{'route_id': 'methodology_default', 'stage_order': inferred}]
        return completed
    for route in routes:
        if not isinstance(route, dict):
            continue
        current = route.get('stage_order') if isinstance(route.get('stage_order'), list) else []
        route['stage_order'] = _fixed_methodology_stage_order([*current, *inferred])
        return completed
    completed['execution_routes'].append({'route_id': 'methodology_default', 'stage_order': inferred})
    return completed


def _complete_methodology_dataset_gates(plan: dict[str, Any], candidates: list[EvidenceCandidate]) -> dict[str, Any]:
    completed = copy.deepcopy(plan)
    gates = [copy.deepcopy(gate) for gate in completed.get('manual_gates') or [] if isinstance(gate, dict)]
    seen = {str(gate.get('accession') or gate.get('value') or '').upper() for gate in gates}
    for candidate in candidates:
        if candidate.object_type != 'dataset_accession' or candidate.guardrail_status != 'accepted' or candidate.role != 'raw_rnaseq_accession':
            continue
        if candidate.value in seen:
            continue
        gates.append({
            'gate_type': 'dataset_accession',
            'accession': candidate.value,
            'dataset_kind': candidate.role,
            'status': 'requires_user_data_or_download_confirmation',
            'source_file': candidate.source_file,
            'source_field': candidate.source_field,
            'evidence_text': candidate.evidence_text,
            'reason': 'methodology_mentions_external_dataset_accession',
        })
        seen.add(candidate.value)
    if gates:
        completed['manual_gates'] = gates
    return completed


def _complete_methodology_expression_requirements(plan: dict[str, Any], candidates: list[EvidenceCandidate]) -> dict[str, Any]:
    completed = copy.deepcopy(plan)
    requirements = [copy.deepcopy(req) for req in completed.get('data_requirements') or [] if isinstance(req, dict)]
    seen = {
        (str(req.get('requirement_type') or ''), str(req.get('accession') or req.get('value') or '').upper())
        for req in requirements
    }
    for candidate in candidates:
        if candidate.object_type != 'dataset_accession' or candidate.guardrail_status != 'accepted' or candidate.role != 'expression_matrix_accession':
            continue
        key = ('expression_matrix', candidate.value)
        if key in seen:
            continue
        requirements.append({
            'requirement_type': 'expression_matrix',
            'accession': candidate.value,
            'bbav5_role': 'expression_data',
            'required_files': ['expression_matrix'],
            'source': 'methodology_expression_accession_completion',
            'source_file': candidate.source_file,
            'source_field': candidate.source_field,
            'evidence_text': candidate.evidence_text,
        })
        seen.add(key)
    if requirements:
        completed['data_requirements'] = requirements
    return completed


def _complete_methodology_reproduction_targets(plan: dict[str, Any], candidates: list[EvidenceCandidate]) -> dict[str, Any]:
    completed = copy.deepcopy(plan)
    targets = [copy.deepcopy(target) for target in completed.get('reproduction_targets') or [] if isinstance(target, dict)]
    seen = {_normalize_figure_table_label(target.get('label') or target.get('value')).lower() for target in targets}
    for candidate in candidates:
        if candidate.object_type != 'figure_table_target' or candidate.guardrail_status != 'accepted':
            continue
        key = _normalize_figure_table_label(candidate.value).lower()
        if not key or key in seen:
            continue
        targets.append({
            'target_type': candidate.role or 'figure_table',
            'label': candidate.value,
            'title': candidate.evidence_text,
            **_figure_table_coverage(candidate, plan),
            'source_file': candidate.source_file,
            'source_field': candidate.source_field,
            'status': 'planning_target_only',
            'reason': 'figure_or_table_caption_detected',
        })
        seen.add(key)
    if targets:
        completed['reproduction_targets'] = targets
    return completed


def _figure_table_coverage(candidate: EvidenceCandidate, plan: dict[str, Any]) -> dict[str, str]:
    suggested = _infer_stage_from_figure_table_text(f'{candidate.value} {candidate.evidence_text}')
    stages = set(_first_route_stages(plan) or _infer_stage_order_from_plan(plan))
    if suggested and suggested in stages:
        return {
            'suggested_stage': suggested,
            'coverage_status': 'covered_by_stage',
            'coverage_reason': 'caption_keywords_match_existing_methodology_stage',
        }
    if suggested:
        return {
            'suggested_stage': suggested,
            'coverage_status': 'needs_method_inference_or_default_tool',
            'coverage_reason': 'caption_suggests_stage_without_methodology_stage_evidence',
        }
    return {
        'suggested_stage': '',
        'coverage_status': 'unclassified_target',
        'coverage_reason': 'caption_has_no_supported_stage_keyword',
    }


def _infer_stage_from_figure_table_text(text: str) -> str:
    value = str(text or '').lower()
    keyword_map = [
        ('expression', 'expression'),
        ('heatmap', 'expression'),
        ('phylogen', 'phylogeny'),
        ('tree', 'phylogeny'),
        ('motif', 'motif'),
        ('domain', 'domain'),
        ('structure', 'structure'),
        ('intron', 'structure'),
        ('exon', 'structure'),
        ('promoter', 'promoter'),
        ('cis', 'promoter'),
        ('synten', 'synteny'),
        ('collinear', 'synteny'),
        ('chromosome', 'karyotype'),
        ('location', 'karyotype'),
        ('ppi', 'ppi'),
        ('protein interaction', 'ppi'),
    ]
    for keyword, stage in keyword_map:
        if keyword in value:
            return stage
    return ''


def _infer_stage_order_from_plan(plan: dict[str, Any]) -> list[str]:
    stages: list[str] = []

    def add(value: Any) -> None:
        stage = _normalize_methodology_stage(value)
        if stage:
            stages.append(stage)

    def walk(value: Any, key: str = '') -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_key in {'bbav5_stage_id', 'bbav5_stage', 'mapped_bbav5_stage', 'stage', 'normalized_stage'}:
                    add(child_value)
                walk(child_value, str(child_key))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    for section in ('stages', 'method_components', 'evidence', 'observations', 'species_summary'):
        walk(plan.get(section), section)
    stages.extend(_infer_stage_order_from_batch_dirs(plan))
    return _fixed_methodology_stage_order(_unique(stages))


def _infer_stage_order_from_batch_dirs(plan: dict[str, Any]) -> list[str]:
    stages: list[str] = []
    for batch_dir in _methodology_batch_dirs(plan):
        for csv_name in ('review_template.csv', 'species_summary.csv', 'summary.csv'):
            path = batch_dir / csv_name
            if not path.exists():
                continue
            try:
                with path.open('r', encoding='utf-8', newline='') as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        for key in ('bbav5_stage', 'mapped_bbav5_stage', 'bbav5_stage_id', 'normalized_stage', 'stage'):
                            stage = _normalize_methodology_stage(row.get(key))
                            if stage:
                                stages.append(stage)
            except Exception:
                continue
        for json_name in ('observations.json', 'alignment.json'):
            path = batch_dir / json_name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            stages.extend(_infer_stage_order_from_json_value(data))
    return _fixed_methodology_stage_order(_unique(stages))


def _infer_stage_order_from_json_value(value: Any) -> list[str]:
    stages: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {'bbav5_stage', 'mapped_bbav5_stage', 'bbav5_stage_id', 'normalized_stage', 'stage'}:
                    stage = _normalize_methodology_stage(child)
                    if stage:
                        stages.append(stage)
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return _fixed_methodology_stage_order(_unique(stages))


def _infer_required_pfams_from_batch_dirs(plan: dict[str, Any]) -> str:
    pfams: list[str] = []
    for batch_dir in _methodology_batch_dirs(plan):
        for name in ('review_template.csv', 'observations.json', 'alignment.json', 'methodology_parameter_audit.csv'):
            path = batch_dir / name
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            for hit in re.findall(r'PF\d{5}', text):
                if hit not in pfams:
                    pfams.append(hit)
    return ','.join(pfams)


def _normalize_methodology_stage(value: Any) -> str:
    token = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    if not token:
        return ''
    stage = METHODOLOGY_STAGE_ALIASES.get(token) or token
    return stage if stage in FIXED_METHODOLOGY_STAGE_ORDER else ''


def _attach_methodology_completion_audit(
    completed: dict[str, Any],
    original: dict[str, Any],
    candidates: list[EvidenceCandidate],
    *,
    source: str = '',
) -> None:
    if not isinstance(completed, dict):
        return
    before_prefill = original.get('bbav5_prefill') if isinstance(original.get('bbav5_prefill'), dict) else {}
    after_prefill = completed.get('bbav5_prefill') if isinstance(completed.get('bbav5_prefill'), dict) else {}
    previous_audit = original.get('methodology_completion_audit') if isinstance(original.get('methodology_completion_audit'), dict) else {}
    previous_records = [copy.deepcopy(record) for record in previous_audit.get('records') or [] if isinstance(record, dict)]
    new_records: list[dict[str, Any]] = []

    def added_list(field: str, rule: str) -> None:
        before = {_species_key(item) for item in _list(before_prefill.get(field))}
        for item in _list(after_prefill.get(field)):
            if _species_key(item) not in before:
                new_records.append(_audit_record('species', item, field, rule, candidates))

    added_list('targetSpecies', 'role_aware_species_completion')
    added_list('referenceSpecies', 'role_aware_species_completion')
    added_list('comparativeSpecies', 'role_aware_species_completion')
    if str(after_prefill.get('requiredPfams') or '').strip() and not str(before_prefill.get('requiredPfams') or '').strip():
        new_records.append(_audit_record('domain_requirement', after_prefill.get('requiredPfams'), 'requiredPfams', 'pfam_evidence_completion', candidates))

    before_stages = _first_route_stages(original)
    for stage in _first_route_stages(completed):
        if stage not in before_stages:
            new_records.append(_audit_record('stage', stage, 'execution_routes.stage_order', 'stage_evidence_completion', candidates))

    before_gates = {str(gate.get('accession') or '').upper() for gate in original.get('manual_gates') or [] if isinstance(gate, dict)}
    for gate in completed.get('manual_gates') or []:
        if not isinstance(gate, dict):
            continue
        accession = str(gate.get('accession') or '').upper()
        if accession and accession not in before_gates:
            new_records.append(_audit_record('dataset_accession', accession, 'manual_gates', 'accession_manual_gate_completion', candidates))

    before_expression = {
        str(req.get('accession') or '').upper()
        for req in original.get('data_requirements') or []
        if isinstance(req, dict) and str(req.get('requirement_type') or '') == 'expression_matrix'
    }
    for req in completed.get('data_requirements') or []:
        if not isinstance(req, dict) or str(req.get('requirement_type') or '') != 'expression_matrix':
            continue
        accession = str(req.get('accession') or '').upper()
        if accession and accession not in before_expression:
            new_records.append(_audit_record('dataset_accession', accession, 'data_requirements', 'expression_matrix_requirement_completion', candidates))

    before_targets = {_normalize_figure_table_label(target.get('label') or '').lower() for target in original.get('reproduction_targets') or [] if isinstance(target, dict)}
    for target in completed.get('reproduction_targets') or []:
        if not isinstance(target, dict):
            continue
        label = str(target.get('label') or '')
        if label and _normalize_figure_table_label(label).lower() not in before_targets:
            new_records.append(_audit_record('figure_table_target', label, 'reproduction_targets', 'figure_table_target_completion', candidates))

    records = _merge_audit_records(previous_records, new_records)
    accepted_candidates = [candidate.to_record() for candidate in candidates if candidate.guardrail_status == 'accepted']
    rejected_candidates = [candidate.to_record() for candidate in candidates if candidate.guardrail_status != 'accepted']
    completion_run_count = int(previous_audit.get('completion_run_count') or 0) + 1

    completed['methodology_completion_audit'] = {
        'paradigm_version': METHODOLOGY_COMPLETION_PARADIGM_VERSION,
        'source': source,
        'principle': 'evidence_candidate_to_role_to_requirement_with_false_positive_guards',
        'applied_rules': _unique([str(record['rule']) for record in records]),
        'records': records,
        'accepted_candidates': accepted_candidates,
        'rejected_candidates': rejected_candidates,
        'candidate_count': len(candidates),
        'accepted_candidate_count': len(accepted_candidates),
        'rejected_candidate_count': len(rejected_candidates),
        'candidate_types': _unique([candidate.object_type for candidate in candidates]),
        'records_preserved_from_previous_completion': len(previous_records),
        'completion_run_count': completion_run_count,
        'guardrails': [
            'do_not_promote_abbreviation_definition_only_species',
            'do_not_guess_latin_binomials_without_structured_or_alias_evidence',
            'do_not_promote_accession_without_method_data_context',
            'figure_table_targets_do_not_force_execution_stage_without_method_evidence',
            'preserve_existing_user_or_rag_prefill_values',
        ],
    }


def _merge_audit_records(previous: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in [*previous, *new]:
        key = (
            str(record.get('object_type') or ''),
            str(record.get('target_field') or record.get('field') or ''),
            _normalize_figure_table_label(record.get('value')) if str(record.get('object_type') or '') == 'figure_table_target' else str(record.get('value') or ''),
            str(record.get('rule') or ''),
        )
        if key in seen:
            continue
        result.append(record)
        seen.add(key)
    return result


def _methodology_completion_summary(plan: dict[str, Any]) -> dict[str, Any]:
    audit = plan.get('methodology_completion_audit') if isinstance(plan.get('methodology_completion_audit'), dict) else {}
    targets = [target for target in plan.get('reproduction_targets') or [] if isinstance(target, dict)]
    return {
        'paradigm_version': audit.get('paradigm_version') or '',
        'completed_record_count': len(audit.get('records') or []),
        'accepted_candidate_count': int(audit.get('accepted_candidate_count') or 0),
        'rejected_candidate_count': int(audit.get('rejected_candidate_count') or 0),
        'manual_gate_count': len([gate for gate in plan.get('manual_gates') or [] if isinstance(gate, dict)]),
        'data_requirement_count': len([req for req in plan.get('data_requirements') or [] if isinstance(req, dict)]),
        'reproduction_target_count': len(targets),
        'covered_reproduction_target_count': len([target for target in targets if target.get('coverage_status') == 'covered_by_stage']),
        'completion_run_count': int(audit.get('completion_run_count') or 0),
    }


def _audit_record(
    object_type: str,
    value: Any,
    target_field: str,
    rule: str,
    candidates: list[EvidenceCandidate],
) -> dict[str, Any]:
    value_text = str(value or '')
    candidate = _best_candidate(object_type, value_text, candidates)
    record = {
        'object_type': object_type,
        'field': target_field,
        'target_field': target_field,
        'value': value,
        'rule': rule,
        'source_file': candidate.source_file if candidate else '',
        'source_field': candidate.source_field if candidate else '',
        'evidence_text': candidate.evidence_text if candidate else '',
        'confidence_rule': candidate.confidence_rule if candidate else rule,
        'guardrail_status': candidate.guardrail_status if candidate else 'accepted',
    }
    return record


def _best_candidate(object_type: str, value: str, candidates: list[EvidenceCandidate]) -> EvidenceCandidate | None:
    aliases = {
        'domain_requirement': {'pfam', 'domain_requirement'},
        'species': {'species'},
        'stage': {'stage'},
        'dataset_accession': {'dataset_accession'},
        'figure_table_target': {'figure_table_target'},
    }.get(object_type, {object_type})
    normalized_value = _normalize_candidate_value(value)
    for candidate in candidates:
        if candidate.object_type not in aliases or candidate.guardrail_status != 'accepted':
            continue
        if candidate.object_type == 'pfam' and candidate.value and candidate.value in value:
            return candidate
        if _normalize_candidate_value(candidate.value) == normalized_value:
            return candidate
    for candidate in candidates:
        if candidate.object_type in aliases and candidate.guardrail_status == 'accepted':
            return candidate
    return None


def _normalize_candidate_value(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _normalize_figure_table_label(value: Any) -> str:
    label = re.sub(r'\s+', ' ', str(value or '').strip()).rstrip('.')
    label = re.sub(r'^(?:Fig)\.?\s+', 'Figure ', label, flags=re.I)
    label = re.sub(r'^(?:Tab)\.?\s+', 'Table ', label, flags=re.I)
    return label


def _first_route_stages(plan: dict[str, Any]) -> list[str]:
    for route in plan.get('execution_routes') or []:
        if isinstance(route, dict) and isinstance(route.get('stage_order'), list):
            return [str(item) for item in route.get('stage_order') if str(item)]
    return []


def _methodology_species_records(plan: dict[str, Any], *, source: str = '') -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    records.extend(_species_records_from_prefill(plan))
    records.extend(_species_records_from_requirements(plan.get('data_requirements')))
    records.extend(_species_records_from_plan_field(plan.get('species_summary')))
    batch_dirs = _methodology_batch_dirs(plan, source=source)
    for batch_dir in batch_dirs:
        records.extend(_species_records_from_species_summary_csv(batch_dir / 'species_summary.csv'))
        records.extend(_species_records_from_text(_batch_species_text(batch_dir), default_role='reference_species'))
    if not batch_dirs:
        records.extend(_species_records_from_text(_plan_species_text(plan), default_role='reference_species'))
    return _unique_species_records(records)


def _resolve_methodology_species_role_groups(
    plan: dict[str, Any],
    records: list[dict[str, str]],
    *,
    source: str = '',
) -> dict[str, list[str]]:
    prefill = plan.get('bbav5_prefill') if isinstance(plan.get('bbav5_prefill'), dict) else {}
    prefill_targets = [_canonical_latin_species(item) for item in _list(prefill.get('targetSpecies'))]
    prefill_targets = [item for item in prefill_targets if item]
    strong_targets = _strong_target_species_from_methodology_evidence(plan, source=source)
    target_keys = {_species_key(item) for item in strong_targets}

    if strong_targets:
        targets = strong_targets
    elif len(prefill_targets) == 1:
        targets = prefill_targets
        target_keys = {_species_key(item) for item in targets}
    else:
        targets = []
        for record in records:
            species = _canonical_latin_species(record.get('species_name'))
            if species and _normalize_species_role(record.get('species_role')) == 'target' and _species_key(species) not in target_keys:
                targets.append(species)
                target_keys.add(_species_key(species))

    references: list[str] = []
    reference_keys: set[str] = set()
    comparatives: list[str] = []
    comparative_keys: set[str] = set()

    for record in records:
        species = _canonical_latin_species(record.get('species_name'))
        if not species:
            continue
        key = _species_key(species)
        if key in target_keys:
            continue
        role = _normalize_species_role(record.get('species_role'))
        if role == 'comparative':
            if key not in comparative_keys and key not in reference_keys:
                comparatives.append(species)
                comparative_keys.add(key)
        elif role == 'reference':
            if key not in reference_keys and key not in comparative_keys:
                references.append(species)
                reference_keys.add(key)

    return {'targets': targets, 'references': references, 'comparatives': comparatives}


def _strong_target_species_from_methodology_evidence(plan: dict[str, Any], *, source: str = '') -> list[str]:
    targets: list[str] = []

    def add(species: Any) -> None:
        canonical = _canonical_latin_species(species)
        if canonical and _species_key(canonical) not in {_species_key(item) for item in targets}:
            targets.append(canonical)

    for row in plan.get('species_summary') or []:
        if not isinstance(row, dict):
            continue
        if _normalize_species_role(row.get('species_role') or row.get('role')) != 'target':
            continue
        species = row.get('species_name') or row.get('species')
        evidence = str(row.get('evidence_sentence') or '')
        if _is_supported_target_species_evidence(species, evidence):
            add(species)

    for batch_dir in _methodology_batch_dirs(plan, source=source):
        path = batch_dir / 'species_summary.csv'
        if not path.exists():
            continue
        try:
            with path.open('r', encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if _normalize_species_role(row.get('species_role')) != 'target':
                        continue
                    if _is_supported_target_species_evidence(row.get('species_name'), row.get('evidence_sentence')):
                        add(row.get('species_name'))
        except Exception:
            continue

    return targets


def _is_supported_target_species_evidence(species: Any, evidence: Any) -> bool:
    canonical = _canonical_latin_species(species)
    text = str(evidence or '')
    if not canonical or not text:
        return False
    if _species_mention_is_abbreviation_definition_only(canonical, text):
        return False
    if not _species_evidence_mentions_species(canonical, text):
        return False
    return _species_has_target_context(canonical, text)


def _species_has_target_context(species: str, text: str) -> bool:
    stripped = _strip_species_abbreviation_definitions(str(text or ''))
    lower = stripped.lower()
    aliases = _species_context_aliases(species)
    for alias in aliases:
        alias_lower = alias.lower()
        if alias_lower not in lower:
            continue
        for match in re.finditer(re.escape(alias_lower), lower):
            start = max(0, match.start() - 120)
            end = min(len(lower), match.end() + 120)
            window = lower[start:end]
            if 'genome-wide' in window:
                return True
            if re.search(r'(identification|identified|screened).{0,80}(genes?|proteins?)', window) and alias_lower in window:
                return True
            if re.search(r'(genes?|proteins?).{0,80}(identification|identified|screened)', window) and alias_lower in window:
                return True
            if f'{alias_lower} genome' in window or f'{alias_lower} genes' in window or f'{alias_lower} proteins' in window:
                return True
            if re.search(rf'(from|in)\s+(the\s+)?{re.escape(alias_lower)}\s+genome', window):
                return True
    return False


def _text_species_record_has_method_context(species: str, role: str, text: str) -> bool:
    if _normalize_species_role(role) != 'reference':
        return True
    return _species_has_reference_context(species, text)


def _species_has_reference_context(species: str, text: str) -> bool:
    stripped = _strip_species_abbreviation_definitions(str(text or ''))
    lower = stripped.lower()
    aliases = _species_context_aliases(species)
    for alias in aliases:
        alias_lower = alias.lower()
        if alias_lower not in lower:
            continue
        for match in re.finditer(re.escape(alias_lower), lower):
            start = max(0, match.start() - 180)
            end = min(len(lower), match.end() + 180)
            window = lower[start:end]
            if re.search(r'(phylogen|tree|n-j|neighbor|aligned|alignment|clustal|mega|uniprot|tair|protein sequences?|amino acid sequences?|reference proteins?|included|comparing|multiple species|blast)', window):
                return True
    return False


def _species_context_aliases(species: str) -> list[str]:
    canonical = _canonical_latin_species(species)
    aliases = [canonical] if canonical else []
    aliases.extend(COMMON_SPECIES_NAME_ALIASES.get(canonical, []))
    for alias, alias_species in COMMON_PLANT_SPECIES_ALIASES.items():
        if _species_key(alias_species) == _species_key(canonical):
            aliases.append(alias)
    return _unique([alias for alias in aliases if alias])


def _species_records_from_prefill(plan: dict[str, Any]) -> list[dict[str, str]]:
    prefill = plan.get('bbav5_prefill') if isinstance(plan.get('bbav5_prefill'), dict) else {}
    records: list[dict[str, str]] = []
    for species in _list(prefill.get('targetSpecies')):
        records.append({'species_name': species, 'species_role': 'target_species'})
    for species in _list(prefill.get('referenceSpecies')):
        records.append({'species_name': species, 'species_role': 'reference_species'})
    for species in _list(prefill.get('comparativeSpecies')):
        records.append({'species_name': species, 'species_role': 'comparative_species'})
    for species in _list(prefill.get('identifyBlastReferenceSpecies')):
        records.append({'species_name': species, 'species_role': 'reference_species'})
    return records


def _species_records_from_requirements(requirements: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if not isinstance(requirements, list):
        return records
    for req in requirements:
        if not isinstance(req, dict):
            continue
        species = req.get('species_name')
        if species:
            records.append({'species_name': str(species), 'species_role': str(req.get('bbav5_role') or 'reference_species')})
    return records


def _species_records_from_plan_field(value: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if not isinstance(value, list):
        return records
    for row in value:
        if not isinstance(row, dict):
            continue
        species = row.get('species_name') or row.get('species')
        if species:
            records.append({'species_name': str(species), 'species_role': str(row.get('species_role') or row.get('role') or 'reference_species')})
    return records


def _methodology_batch_dirs(plan: dict[str, Any], *, source: str = '') -> list[Path]:
    candidates: list[Path] = []
    for value in (plan.get('source_batch_dir'), plan.get('batch_dir'), plan.get('methodology_batch_dir'), source):
        if not value:
            continue
        path = Path(str(value)).expanduser()
        if path.is_file():
            path = path.parent
        if path.exists() and path.is_dir():
            candidates.append(path)
    return _unique_paths(candidates)


def _species_records_from_species_summary_csv(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    if not path.exists():
        return records
    try:
        with path.open('r', encoding='utf-8', newline='') as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                species = row.get('species_name')
                canonical = _canonical_latin_species(species)
                if not canonical:
                    continue
                evidence = row.get('evidence_sentence') or ''
                if evidence and not _species_evidence_mentions_species(canonical, evidence):
                    continue
                if _species_mention_is_abbreviation_definition_only(canonical, evidence):
                    continue
                records.append({'species_name': canonical, 'species_role': row.get('species_role') or 'reference_species'})
    except Exception:
        return records
    return records


def _batch_species_text(batch_dir: Path) -> str:
    chunks: list[str] = []
    for name in ('review_template.csv', 'observations.json', 'alignment.json'):
        path = batch_dir / name
        if path.exists() and path.is_file():
            try:
                chunks.append(path.read_text(encoding='utf-8', errors='ignore'))
            except Exception:
                continue
    return '\n'.join(chunks)


def _plan_species_text(plan: dict[str, Any]) -> str:
    chunks: list[str] = []

    def walk(value: Any, key: str = '') -> None:
        if key in {'bbav5_prefill', 'data_requirements', 'methodology_completion_audit', 'reproduction_targets'}:
            return
        if isinstance(value, str):
            if key not in {'plan_id', 'paper_id', 'source_batch_dir', 'batch_dir', 'methodology_batch_dir'}:
                chunks.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))

    walk(plan)
    return '\n'.join(chunks)


def _species_records_from_text(text: str, *, default_role: str) -> list[dict[str, str]]:
    value = str(text or '')
    if not value:
        return []
    aliases = {**COMMON_PLANT_SPECIES_ALIASES, **_species_alias_definitions(value)}
    evidence_text = _strip_species_abbreviation_definitions(value)
    records: list[dict[str, str]] = []
    for alias, species in aliases.items():
        if _contains_species_alias(evidence_text, alias) and _text_species_record_has_method_context(species, default_role, evidence_text):
            records.append({'species_name': species, 'species_role': default_role})
    for species in _unique(list(aliases.values())):
        if _contains_species_alias(evidence_text, species) and _text_species_record_has_method_context(species, default_role, evidence_text):
            records.append({'species_name': species, 'species_role': default_role})
    return records


def _species_alias_definitions(text: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    pattern = re.compile(r'\b([A-Z]\.\s*[a-z][A-Za-z-]*)\s*:\s*([A-Z][a-z]+(?:\s+[a-z][a-z.-]+)+)')
    for alias, species in pattern.findall(str(text or '')):
        canonical = _canonical_latin_species(species)
        if canonical:
            aliases[re.sub(r'\s+', ' ', alias.strip())] = canonical
    return aliases


def _strip_species_abbreviation_definitions(text: str) -> str:
    return re.sub(
        r'\b[A-Z]\.\s*[a-z][A-Za-z-]*\s*:\s*[A-Z][a-z]+(?:\s+[a-z][a-z.-]+)+(?:\s+[A-Za-z().-]+)*[;,.]?',
        ' ',
        str(text or ''),
    )


def _species_evidence_mentions_species(species: str, text: str) -> bool:
    canonical = _canonical_latin_species(species)
    if not canonical:
        return False
    stripped = _strip_species_abbreviation_definitions(str(text or ''))
    if _contains_species_alias(stripped, canonical):
        return True
    for alias, alias_species in COMMON_PLANT_SPECIES_ALIASES.items():
        if _species_key(alias_species) == _species_key(canonical) and _contains_species_alias(stripped, alias):
            return True
    for alias in COMMON_SPECIES_NAME_ALIASES.get(canonical, []):
        if re.search(rf'(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])', stripped, re.I):
            return True
    return False


def _species_mention_is_abbreviation_definition_only(species: str, text: str) -> bool:
    value = str(text or '')
    if len(re.findall(r'\b[A-Z]\.\s*[a-z][A-Za-z-]*\s*:', value)) < 2:
        return False
    return not _species_evidence_mentions_species(species, value)


def _contains_species_alias(text: str, alias: str) -> bool:
    compact = re.escape(re.sub(r'\s+', ' ', str(alias or '').strip()))
    compact = compact.replace(r'\ ', r'\s*')
    return bool(re.search(rf'(?<![A-Za-z0-9]){compact}(?![A-Za-z0-9])', str(text or '')))


def _canonical_latin_species(value: Any) -> str:
    species = re.sub(r'\s+', ' ', str(value or '').strip().strip(' .,:;()[]{}'))
    species = re.sub(r'\s+L\.?$', '', species).strip()
    if not species:
        return ''
    if re.match(r'^[A-Z]\.\s*[a-z][a-z.-]+$', species):
        return COMMON_PLANT_SPECIES_ALIASES.get(re.sub(r'\s+', ' ', species), '')
    if not re.match(r'^[A-Z][a-z]+(?:\s+[a-z][a-z.-]+)+$', species):
        return ''
    words = species.split()
    if len(words) < 2:
        return ''
    return f'{words[0]} {words[1]}'


def _normalize_species_role(value: Any) -> str:
    role = str(value or '').strip().lower()
    if 'target' in role or role in {'target', 'query'}:
        return 'target'
    if 'comparative' in role or 'comparison' in role:
        return 'comparative'
    return 'reference'


def _complete_species_requirements(
    requirements: Any,
    *,
    targets: list[str],
    references: list[str],
    comparatives: list[str],
) -> list[dict[str, Any]]:
    result = [
        copy.deepcopy(req)
        for req in requirements
        if isinstance(req, dict) and str(req.get('requirement_type') or '') != 'whole_genome_annotation'
    ] if isinstance(requirements, list) else []
    existing = {(_species_key(req.get('species_name')), str(req.get('requirement_type') or '')) for req in result}

    def add(species: str, role: str) -> None:
        key = (_species_key(species), 'whole_genome_annotation')
        if not species or key in existing:
            return
        result.append({
            'species_name': species,
            'bbav5_role': role,
            'requirement_type': 'whole_genome_annotation',
            'required_files': ['gff', 'genome_or_cds_or_pep'],
            'source': 'methodology_species_completion',
        })
        existing.add(key)

    for species in targets:
        add(species, 'target')
    for species in references:
        add(species, 'reference')
    for species in comparatives:
        add(species, 'comparative')
    return result


def _unique_species_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        species = _canonical_latin_species(record.get('species_name'))
        role = _normalize_species_role(record.get('species_role'))
        if not species:
            continue
        key = (_species_key(species), role)
        if key in seen:
            continue
        result.append({'species_name': species, 'species_role': role})
        seen.add(key)
    return result


def _unique_candidates(candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
    result: list[EvidenceCandidate] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for candidate in candidates:
        if not candidate.value:
            continue
        key = (
            candidate.object_type,
            _normalize_candidate_value(candidate.value),
            candidate.role,
            candidate.source_file,
            candidate.source_field,
        )
        if key in seen:
            continue
        result.append(candidate)
        seen.add(key)
    return result


def _evidence_excerpt(text: str, needle: Any, *, window: int = 240) -> str:
    value = str(text or '')
    query = str(needle or '')
    if not value:
        return ''
    if not query:
        return value[:window].strip()
    match = re.search(re.escape(query), value, re.I)
    if not match:
        return value[:window].strip()
    start = max(0, match.start() - window // 3)
    end = min(len(value), match.end() + (window - (match.end() - start)))
    return re.sub(r'\s+', ' ', value[start:end]).strip()


def _sentence_excerpt(text: str, needle: Any) -> str:
    value = str(text or '')
    query = str(needle or '')
    match = re.search(re.escape(query), value, re.I) if query else None
    if not match:
        return _evidence_excerpt(value, query)
    start_candidates = [value.rfind(mark, 0, match.start()) for mark in ('.', '\n', ';')]
    start = max(start_candidates)
    start = 0 if start < 0 else start + 1
    end_candidates = [idx for idx in (value.find(mark, match.end()) for mark in ('.', '\n', ';')) if idx >= 0]
    end = min(end_candidates) if end_candidates else min(len(value), match.end() + 180)
    return re.sub(r'\s+', ' ', value[start:end]).strip()


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key not in seen:
            result.append(path)
            seen.add(key)
    return result


def _species_key(value: Any) -> str:
    return _normalize_species_alias(str(value or ''))


def is_methodology_execution_intent(text: str) -> bool:
    value = str(text or '').strip()
    if not value:
        return False
    has_action = bool(re.search(r'(按|按照|使用|继续|执行|运行|复现|run|execute|continue|reproduce|use)', value, re.I))
    has_methodology = bool(re.search(r'(方法学|文献方法|已保存(?:的)?(?:方法学)?方案|保存(?:的)?方案|文献方案|methodology|literature\s+method|saved\s+plan)', value, re.I))
    return has_action and has_methodology


def _infer_gene_family_from_plan(plan: dict[str, Any]) -> str:
    for section in ('evidence', 'stages', 'method_components'):
        for row in plan.get(section) or []:
            if not isinstance(row, dict):
                continue
            if row.get('family'):
                return _normalize_gene_family_token(row.get('family'))
            text = ' '.join(str(value or '') for value in row.values())
            family = _infer_gene_family_from_text(text)
            if family:
                return family
    for key in ('paper_id', 'plan_id', 'source_batch_dir'):
        value = str(plan.get(key) or '')
        for match in re.finditer(r'(?<![A-Za-z0-9])(?:[A-Z][a-z]?\.)?([A-Z][A-Za-z0-9_-]{2,})(?![A-Za-z0-9])', value):
            family = _normalize_gene_family_token(match.group(1))
            if family and family.lower() not in {'plan', 'batch', 'methodology', 'job'}:
                return family
    return ''


def _normalize_gene_family_token(value: Any) -> str:
    token = str(value or '').strip()
    if not token:
        return ''
    token = token.strip(' .,:;()[]{}')
    species_prefixed = re.match(r'^([A-Z][A-Za-z]{0,2})[-_]([A-Za-z0-9][A-Za-z0-9_-]{2,})$', token)
    if species_prefixed:
        token = species_prefixed.group(2)
    token = re.sub(r'[^A-Za-z0-9]+', '', token)
    if token.lower() == 'hdzip':
        return 'HDZIP'
    return token


def _infer_gene_family_from_text(text: str) -> str:
    value = str(text or '')
    if re.search(r'\bHD[-\s]?Zip\b', value, re.I):
        return 'HDZIP'
    for pattern in (
        r'\b([A-Z][A-Za-z0-9]*(?:[- ][A-Z][A-Za-z0-9]*)?)\s+(?:gene\s+family|protein\s+family|protein\s+sequences?|proteins?)\b',
        r'\b([A-Z][A-Za-z0-9]*(?:[- ][A-Z][A-Za-z0-9]*)?)\s+family\b',
    ):
        for match in re.finditer(pattern, value):
            family = _normalize_gene_family_token(match.group(1))
            if family and family.lower() not in {'known', 'candidate', 'target', 'reference'}:
                return family
    return ''


def _infer_required_pfams_from_plan(plan: dict[str, Any]) -> str:
    pfams: list[str] = []
    def add_from(value: Any) -> None:
        if isinstance(value, str):
            for hit in re.findall(r'PF\d{5}', value):
                if hit not in pfams:
                    pfams.append(hit)
        elif isinstance(value, list):
            for item in value:
                add_from(item)
        elif isinstance(value, dict):
            for item in value.values():
                add_from(item)
    add_from(plan.get('method_components'))
    add_from(plan.get('stages'))
    add_from(plan.get('evidence'))
    return ','.join(pfams)


def methodology_plan_readiness(
    plan: dict[str, Any],
    *,
    job_id: str,
    input_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    plan = complete_methodology_plan_for_execution(plan)
    input_dir = Path(input_root) / job_id if job_id else Path(input_root)
    final_dir = Path(workspace_root) / job_id / 'final' if job_id else Path(workspace_root)
    requirements = plan.get('data_requirements') if isinstance(plan.get('data_requirements'), list) else []
    statuses = []
    missing = []
    for req in requirements:
        if not isinstance(req, dict):
            continue
        status = _requirement_status(req, input_dir, final_dir)
        statuses.append(status)
        if status['status'] != 'ready':
            missing.append(status)
    agent_params = methodology_agent_params(plan)
    suggested_stages = []
    if any(item.get('requirement_type') == 'whole_genome_annotation' for item in missing):
        suggested_stages.append('genome_prepare')
    requested = agent_params.get('requestedMethodologyStages') or []
    for stage in requested:
        if stage not in suggested_stages:
            suggested_stages.append(stage)
    config_path = input_dir / 'config.xlsx'
    samples_path = input_dir / 'samples.yaml'
    return {
        'success': True,
        'job_id': job_id,
        'plan_id': plan.get('plan_id', ''),
        'ready': len(missing) == 0 and config_path.exists(),
        'config_exists': config_path.exists(),
        'samples_exists': samples_path.exists(),
        'requirements': statuses,
        'missing_requirements': missing,
        'agent_params': agent_params,
        'suggested_stages': suggested_stages,
        'manual_gates': plan.get('manual_gates') or [],
        'completion_audit': plan.get('methodology_completion_audit') or {},
        'completion_summary': _methodology_completion_summary(plan),
        'reproduction_targets': plan.get('reproduction_targets') or [],
        'message': '方法学方案数据已就绪' if len(missing) == 0 and config_path.exists() else '方法学方案仍需补齐数据或确认 config.xlsx',
    }


def load_bound_methodology_plan(input_root: Path, job_id: str) -> dict[str, Any] | None:
    if not job_id:
        return None
    path = bound_methodology_plan_path(input_root, job_id)
    if not path.exists():
        return None
    return load_methodology_plan(path)


def _requirement_status(req: dict[str, Any], input_dir: Path, final_dir: Path) -> dict[str, Any]:
    species = str(req.get('species_name') or '')
    species_for_files = _canonical_species_for_files(species, input_dir)
    req_type = str(req.get('requirement_type') or '')
    if req_type == 'whole_genome_annotation':
        has_gff = _has_matching_file(input_dir, species_for_files, GFF_EXTENSIONS) or _has_matching_file(final_dir, species_for_files, GFF_EXTENSIONS)
        has_seq = _has_matching_file(input_dir, species_for_files, WHOLE_GENOME_EXTENSIONS) or _has_matching_file(final_dir, species_for_files, WHOLE_GENOME_EXTENSIONS)
        missing = []
        if not has_gff:
            missing.append('gff')
        if not has_seq:
            missing.append('genome_or_cds_or_pep')
        return {
            **req,
            'status': 'ready' if not missing else 'missing',
            'missing_files': missing,
            'matched': {'gff': has_gff, 'sequence': has_seq},
            'resolved_species_code': species_for_files,
        }
    if req_type == 'known_family_protein':
        has_family = _has_matching_file(input_dir, species_for_files, WHOLE_GENOME_EXTENSIONS, family_hint=True) or _has_matching_file(final_dir, species_for_files, WHOLE_GENOME_EXTENSIONS, family_hint=True)
        return {
            **req,
            'status': 'ready' if has_family else 'missing',
            'missing_files': [] if has_family else ['family_pep_fasta'],
            'matched': {'family_pep_fasta': has_family},
            'resolved_species_code': species_for_files,
        }
    return {**req, 'status': 'unknown', 'missing_files': list(req.get('required_files') or []), 'resolved_species_code': species_for_files}


def _canonical_species_for_files(species: str, input_dir: Path) -> str:
    value = str(species or '').strip()
    if not value:
        return value
    aliases = _species_aliases_from_config(input_dir)
    normalized = _normalize_species_alias(value)
    return aliases.get(normalized) or value


def _species_aliases_from_config(input_dir: Path) -> dict[str, str]:
    config_path = Path(input_dir) / 'config.xlsx'
    if not config_path.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_excel(config_path).fillna('')
    except Exception:
        return {}
    aliases: dict[str, str] = {}

    def add(alias: Any, code: str) -> None:
        key = _normalize_species_alias(str(alias or ''))
        if key and code:
            aliases[key] = code

    for _, row in df.iterrows():
        code = str(row.get('Species_Code') or row.get('Species') or '').strip()
        if not code:
            continue
        add(code, code)
        for key in ('Species', 'Species_Name', 'Scientific_Name', 'Latin_Name', 'species_name', 'scientific_name', 'latin_name'):
            add(row.get(key), code)
        filename = str(row.get('Original_File_Name') or '').strip()
        if filename:
            stem = Path(filename).name
            stem = re.sub(r'\.(?:gz|zip|fa|fasta|faa|fna|ffn|gff3?|gtf|cds|pep|all|chr|toplevel|dna|protein)+$', '', stem, flags=re.I)
            parts = [part for part in re.split(r'[._-]+', stem) if part]
            if len(parts) >= 2:
                add(' '.join(parts[:2]), code)
                add('_'.join(parts[:2]), code)
    return aliases


def _normalize_species_alias(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', ' ', str(value or '')).strip().lower()


def _has_matching_file(root: Path, species: str, extensions: tuple[str, ...], *, family_hint: bool = False) -> bool:
    if not root.exists():
        return False
    tokens = _species_tokens(species)
    for path in root.rglob('*'):
        if not path.is_file() or path.name.startswith('.'):
            continue
        lower = path.name.lower()
        if not lower.endswith(extensions):
            continue
        if tokens and not any(token in lower for token in tokens):
            if family_hint and any(word in lower for word in ('family', 'known', 'ref', 'reference')):
                return True
            continue
        return True
    return False


def _species_tokens(species: str) -> list[str]:
    words = [part for part in re.split(r'[^A-Za-z0-9]+', species.lower()) if part]
    tokens = set(words)
    if len(words) >= 2:
        tokens.add(words[0][0] + words[1])
        tokens.add(words[0][:2] + words[1][:2])
    return [token for token in tokens if len(token) >= 2]


def _scope_mode(prefill: dict[str, Any]) -> str:
    refs = _list(prefill.get('referenceSpecies'))
    comps = _list(prefill.get('comparativeSpecies'))
    if refs and comps:
        return 'target_plus_reference_plus_comparative'
    if refs:
        return 'target_plus_reference'
    if comps:
        return 'target_plus_comparative'
    return 'target_only'


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = re.split(r'[,;，；|]+', value)
    else:
        values = []
    return _unique([str(item).strip() for item in values if str(item).strip()])


def _unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
