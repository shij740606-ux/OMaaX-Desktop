# RNA-seq Result Report Contract v1

## Stage

- Stage id: `rnaseq_report`
- Script: `apps/cli/run_rnaseq_report.py`
- Scope: one exact `prefix + rnaseq_scope_id` evidence snapshot
- Purpose: materialize a formal, reproducible RNA-seq Markdown result report without depending on frontend session state.

## Inputs

- At least one RNA-seq/annotation manifest with status `success` or `partial_success` under `final/`.
- All selected evidence must match the requested prefix and RNA-seq scope. Legacy unscoped QC/alignment/quantification/annotation manifests may be treated as experiment-global evidence.
- `expected_stages` is optional and records the stages selected for the current batch. It controls coverage only; it must not become a static execution dependency.
- An existing configured `LLMManager` provider is optional. No provider or model name is required by this stage.

If no successful RNA-seq evidence exists, the stage must return `暂无可生成的 RNA-seq 结果` and must not create an empty report.

## Required Outputs

- `final/{prefix}.rnaseq_report.md`
- `final/{prefix}.rnaseq_report.evidence.json`
- `final/{prefix}.rnaseq_report.model.json`
- `final/{prefix}.rnaseq_report.manifest.json`

The manifest stage status is `success` whenever these outputs are generated. Content completeness is recorded separately as `metrics.coverage_status = complete|partial`.

## Generation Semantics

- `generate`: always rebuild from the current manifest snapshot.
- `open`: reuse a fresh report; regenerate first when the source/request fingerprint changed.
- The deterministic Markdown body is always complete enough to read without an API.
- Optional model output is a labeled language-enhancement section only. Model failure must retain the deterministic report and be recorded in `.model.json`.
- `rnaseq_report` manifests are excluded from their own evidence scan.

## Boundary With Deep Interpretation

Both features may call the pure `gfa_core.rnaseq_evidence` snapshot builder. The formal report must not call `/api/agent/interpret`, read its session cache, or replace the frontend deep-interpretation view.

## Validation

- Every manifest artifact path exists and its digest is recorded.
- Evidence contains only the selected prefix/scope.
- `coverage_status=complete` only when every expected stage has status `success`.
- Missing, failed, or `partial_success` expected stages yield `coverage_status=partial` while the report manifest remains `success`.
