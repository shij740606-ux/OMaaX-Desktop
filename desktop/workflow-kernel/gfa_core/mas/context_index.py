"""Project/job context index for bbav5 orchestration.

This module is intentionally read-only.  It builds a structured view of a
workspace job before planning, execution, diagnosis, or repair decide what to
do.  The goal is to stop each agent from independently guessing files from
prefixes or extensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import re

from gfa_core.project_paths import PROJECT_ROOT, WORKSPACE_ROOT
from gfa_core.gene_family_scope import find_config_file, read_config_workbook, read_scope
from gfa_core.mas.stage_catalog import STAGE_INFO
from gfa_core.mas.stage_capabilities import get_stage_capability
from gfa_core.mas.tools.stage_status import get_stage_status


FASTA_EXTS = {".fa", ".faa", ".fasta", ".fas", ".pep"}
TABLE_EXTS = {".txt", ".tsv", ".csv", ".id", ".ids", ".list"}


def _safe_stat(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
    except OSError:
        return {"exists": False, "size": None, "mtime": None}


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _split_prefix(prefix: str) -> tuple[str, str]:
    prefix = str(prefix or "").strip()
    if not prefix:
        return "", ""
    parts = [part.strip() for part in prefix.split(".") if part.strip()]
    if len(parts) >= 3 and parts[0] in {"Target", "Reference", "Comparative", "Ref"}:
        # Legacy per-species identify outputs used Target.{Species}.{Family}.
        if not any(token.startswith(("target-", "ref-", "comp-")) or token in {"target_only", "target_plus_reference", "all_selected", "subfamily_prediction"} for token in parts[2:]):
            return parts[1], parts[2]
    if len(parts) >= 2 and parts[0] in {"Multi", "Target", "Reference", "Comparative", "Ref"}:
        return "", parts[1]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", parts[0]


def _single_string(value: Any) -> str:
    return str(value or "").strip()


def _first_list_value(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = _single_string(item)
            if text:
                return text
    return ""


def _manifest_stage_context(manifest: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("stage_context", "scope", "scope_data"):
        value = manifest.get(key)
        if isinstance(value, dict):
            return value
    params = manifest.get("parameters")
    if isinstance(params, dict):
        value = params.get("stage_context") or params.get("scope")
        if isinstance(value, dict):
            return value
    return {}


def _infer_file_kind(path: Path) -> str:
    name = path.name.lower()
    suffixes = "".join(path.suffixes).lower()
    stem = path.stem.lower()

    if name.endswith((".manifest.json", ".scope.json")):
        return "metadata"
    if path.suffix.lower() in {".yaml", ".yml"}:
        return "yaml"
    if (
        name.endswith(".subfamily")
        or "geneid.subfamily" in name
        or "confirmed_subfamily" in name
        or "reference_classification.subfamily" in name
    ):
        return "table"
    if name.endswith((".gff3", ".gff", ".gtf")):
        return "gff"
    if any(token in name for token in ["genome", "genomic", "chromosome", "chr"]) and suffixes.endswith((".fa", ".fasta", ".fa.gz", ".fasta.gz")):
        return "genome_fasta"
    if any(token in name for token in ["pep", "protein", "proteome", "faa"]) and suffixes.endswith((".fa", ".fasta", ".faa", ".pep")):
        return "protein_fasta"
    if any(token in name for token in ["cds", "transcript", "mrna"]) and suffixes.endswith((".fa", ".fasta")):
        return "cds_fasta"
    if path.suffix.lower() in FASTA_EXTS or suffixes.endswith((".fa.gz", ".fasta.gz", ".faa.gz")):
        return "fasta"
    if any(token in stem for token in ["count_matrix", "fpkm", "tpm", "expression"]):
        return "expression_matrix"
    if path.suffix.lower() in TABLE_EXTS:
        if any(token in name for token in ["interproscan", "domain_review", "domain_check"]):
            return "table"
        if path.suffix.lower() in {".id", ".ids", ".list"} or name.endswith((".members.id", ".final_members.id", ".final_gfmembers.id")):
            return "id_list"
        return "table"
    if path.suffix.lower() in {".pdf", ".png", ".svg"}:
        return "figure"
    if path.suffix.lower() in {".nwk", ".tree", ".newick", ".treefile"} or name.endswith((".tree.nwk", ".iqtree.treefile", ".phylogeny.treefile")):
        return "tree"
    return "file"


def _infer_species_family(path: Path, manifest: Optional[Dict[str, Any]] = None) -> tuple[str, str]:
    if manifest:
        context = _manifest_stage_context(manifest)
        species = ""
        family = _single_string(manifest.get("family") or manifest.get("gene_family") or context.get("family"))
        for prefix_key in ("canonical_member_prefix", "output_prefix", "prefix"):
            prefix = _single_string(manifest.get(prefix_key) or context.get(prefix_key))
            if not prefix:
                continue
            prefix_species, prefix_family = _split_prefix(prefix)
            if prefix_species and not species:
                species = prefix_species
            if prefix_family and not family:
                family = prefix_family
            if species and family:
                break
        selected = manifest.get("selected_species") or manifest.get("target_species") or context.get("selected_species") or context.get("target_species") or []
        if not species and isinstance(selected, list) and len(selected) == 1:
            species = str(selected[0]).strip()
        return species, family

    name = path.name
    match = re.match(r"^([A-Za-z0-9_-]+)_final\.gff3$", name)
    if match:
        return match.group(1), ""
    match = re.match(r"^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.(pep|protein|cds|genome)", name, re.I)
    if match:
        return match.group(1), match.group(2)
    match = re.match(r"^([A-Za-z0-9_-]+)\.(pep|protein|cds|genome|genomic|gff3?|faa|fna|fa|fasta)(?:\.|$)", name, re.I)
    if match:
        return match.group(1), ""
    match = re.match(r"^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.", name)
    if match:
        return match.group(1), match.group(2)
    return "", ""


def _iter_files(root: Path, limit: int = 5000) -> Iterable[Path]:
    if not root.exists():
        return []
    count = 0
    for path in root.rglob("*"):
        if path.is_file():
            yield path
            count += 1
            if count >= limit:
                break


@dataclass
class ContextFile:
    path: str
    rel_path: str
    source: str
    kind: str
    species: str = ""
    family: str = ""
    exists: bool = True
    size: Optional[int] = None
    mtime: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContextArtifact:
    stage: str
    key: str
    path: str
    kind: str
    exists: bool
    source_manifest: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectContextIndex:
    job_id: str
    workspace: str
    input_dir: str
    final_dir: str
    logs_dir: str
    config_path: str = ""
    family: str = ""
    target_species: List[str] = field(default_factory=list)
    reference_species: List[str] = field(default_factory=list)
    comparative_species: List[str] = field(default_factory=list)
    scope_files: List[Dict[str, Any]] = field(default_factory=list)
    files: List[ContextFile] = field(default_factory=list)
    manifests: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[ContextArtifact] = field(default_factory=list)
    stage_status: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["files"] = [f.to_dict() for f in self.files]
        data["artifacts"] = [a.to_dict() for a in self.artifacts]
        return data

    def files_by_kind(self, kind: str) -> List[ContextFile]:
        return [f for f in self.files if f.kind == kind]

    def artifacts_by_stage(self, stage: str) -> List[ContextArtifact]:
        return [a for a in self.artifacts if a.stage == stage]


class ProjectContextIndexer:
    """Build a structured, read-only index for one job."""

    def __init__(
        self,
        project_root: Path | str = PROJECT_ROOT,
        *,
        allow_legacy_manifest_migration: bool = True,
    ):
        self.project_root = Path(project_root)
        self.workspace_root = self.project_root / "workspace"
        self.allow_legacy_manifest_migration = allow_legacy_manifest_migration

    def build(
        self,
        job_id: str,
        prefix: str = "",
        *,
        include_stage_status: bool = True,
    ) -> ProjectContextIndex:
        workspace = self.workspace_root / job_id
        canonical_input_dir = self.project_root / "input" / job_id
        input_dir = canonical_input_dir if canonical_input_dir.exists() else workspace / "input"
        final_dir = workspace / "final"
        logs_dir = workspace / "logs"
        prefix_species, prefix_family = _split_prefix(prefix)

        index = ProjectContextIndex(
            job_id=job_id,
            workspace=str(workspace),
            input_dir=str(input_dir),
            final_dir=str(final_dir),
            logs_dir=str(logs_dir),
            family=prefix_family,
            target_species=[prefix_species] if prefix_species and prefix_species.lower() not in {"multi", "target", "ref", "reference", "comparative"} else [],
        )

        self._load_config(index, input_dir, final_dir)
        self._load_scope_files(index, final_dir)
        self._scan_files(index, input_dir, "input")
        self._scan_files(index, final_dir, "final")
        self._scan_stage_dirs(index, workspace)
        self._load_manifests(index, final_dir)
        if include_stage_status:
            self._load_stage_status(index, job_id, prefix or self._best_prefix(index))
        self._scan_logs(index, logs_dir)
        self._derive_warnings(index)
        return index

    def _load_config(self, index: ProjectContextIndex, input_dir: Path, final_dir: Path) -> None:
        config = find_config_file(input_dir)
        if not config:
            for candidate in [
                final_dir / "config.xlsx",
                final_dir / "config.autofill.draft.xlsx",
                final_dir / "config_autofill.xlsx",
            ]:
                if candidate.exists():
                    config = candidate
                    break
        if not config:
            return
        index.config_path = str(config)
        try:
            rows, analysis = read_config_workbook(config)
        except Exception as exc:
            index.warnings.append({"code": "config_read_failed", "message": str(exc), "path": str(config)})
            return

        index.family = analysis.get("Family", "") or analysis.get("family", "") or index.family
        index.target_species = self._merge(index.target_species, self._csv(analysis.get("Target_Species", "")))
        index.reference_species = self._merge(index.reference_species, self._csv(analysis.get("Reference_Species", "")))
        index.comparative_species = self._merge(index.comparative_species, self._csv(analysis.get("Comparative_Species", "")))

        for row in rows:
            species = str(row.get("Species_Code") or row.get("species") or row.get("Species") or "").strip()
            family = str(row.get("Gene_Family") or row.get("family") or row.get("Family") or index.family or "").strip()
            role = str(row.get("Species_Role") or row.get("role") or row.get("Role") or "").strip().lower()
            if species and role in {"target", "targets"}:
                index.target_species = self._merge(index.target_species, [species])
            elif species and role in {"reference", "ref"}:
                index.reference_species = self._merge(index.reference_species, [species])
            elif species and role in {"comparative", "comp"}:
                index.comparative_species = self._merge(index.comparative_species, [species])
            typed_files: list[tuple[Any, str]] = []
            file_type = str(row.get("File_Type") or row.get("file_type") or "").strip().lower()
            original_name = row.get("Original_File_Name") or row.get("File_Name") or row.get("filename")
            kind_by_file_type = {
                "pep": "protein_fasta",
                "protein": "protein_fasta",
                "all_pep": "protein_fasta",
                "family_pep": "protein_fasta",
                "cds": "cds_fasta",
                "family_cds": "cds_fasta",
                "gff": "gff",
                "gff3": "gff",
                "gtf": "gff",
                "genome": "genome_fasta",
            }
            if original_name and file_type in kind_by_file_type:
                typed_files.append((original_name, kind_by_file_type[file_type]))
            for key, kind in [
                ("protein", "protein_fasta"),
                ("pep", "protein_fasta"),
                ("cds", "cds_fasta"),
                ("gff", "gff"),
                ("gff3", "gff"),
                ("genome", "genome_fasta"),
            ]:
                raw = row.get(key) or row.get(key.upper()) or row.get(key.capitalize())
                if raw:
                    typed_files.append((raw, kind))
            seen_config_paths: set[str] = set()
            for raw, kind in typed_files:
                path = Path(str(raw)).expanduser()
                if not path.is_absolute():
                    path = input_dir / path
                path_key = f"{kind}:{path}"
                if path_key in seen_config_paths:
                    continue
                seen_config_paths.add(path_key)
                stat = _safe_stat(path)
                index.files.append(ContextFile(
                    path=str(path),
                    rel_path=str(path.relative_to(index.workspace)) if stat["exists"] and self._is_relative_to(path, Path(index.workspace)) else str(path),
                    source="config",
                    kind=kind,
                    species=species,
                    family=family,
                    exists=stat["exists"],
                    size=stat["size"],
                    mtime=stat["mtime"],
                ))

    def _load_scope_files(self, index: ProjectContextIndex, final_dir: Path) -> None:
        for path in sorted(final_dir.glob("*.scope.json")):
            data = _read_json(path)
            if not data:
                continue
            try:
                scope_data = read_scope(path)
            except Exception:
                scope_data = data
            index.scope_files.append({
                "path": str(path),
                "scope_id": scope_data.get("scope_id", ""),
                "scope_mode": scope_data.get("scope_mode", ""),
                "family": scope_data.get("family", ""),
                "target_species": scope_data.get("target_species", []),
                "reference_species": scope_data.get("reference_species", []),
                "comparative_species": scope_data.get("comparative_species", []),
                "selected_species": scope_data.get("selected_species", []),
            })
            if scope_data.get("family") and not index.family:
                index.family = str(scope_data["family"])
            index.target_species = self._merge(index.target_species, scope_data.get("target_species") or [])
            index.reference_species = self._merge(index.reference_species, scope_data.get("reference_species") or [])
            index.comparative_species = self._merge(index.comparative_species, scope_data.get("comparative_species") or [])

    def _scan_files(self, index: ProjectContextIndex, root: Path, source: str) -> None:
        workspace = Path(index.workspace)
        for path in _iter_files(root):
            if path.name.endswith(".manifest.json"):
                continue
            kind = _infer_file_kind(path)
            species, family = _infer_species_family(path)
            stat = _safe_stat(path)
            index.files.append(ContextFile(
                path=str(path),
                rel_path=str(path.relative_to(workspace)) if self._is_relative_to(path, workspace) else str(path),
                source=source,
                kind=kind,
                species=species,
                family=family,
                exists=stat["exists"],
                size=stat["size"],
                mtime=stat["mtime"],
            ))

    def _scan_stage_dirs(self, index: ProjectContextIndex, workspace: Path) -> None:
        skip = {"input", "final", "logs"}
        for child in sorted(workspace.iterdir()) if workspace.exists() else []:
            if not child.is_dir() or child.name in skip:
                continue
            for path in _iter_files(child, limit=1000):
                kind = _infer_file_kind(path)
                species, family = _infer_species_family(path)
                stat = _safe_stat(path)
                index.files.append(ContextFile(
                    path=str(path),
                    rel_path=str(path.relative_to(workspace)) if self._is_relative_to(path, workspace) else str(path),
                    source=f"stage:{child.name}",
                    kind=kind,
                    species=species,
                    family=family,
                    exists=stat["exists"],
                    size=stat["size"],
                    mtime=stat["mtime"],
                ))

    def _load_manifests(self, index: ProjectContextIndex, final_dir: Path) -> None:
        for path in sorted(final_dir.glob("*.manifest.json")):
            data = _read_json(path)
            if not data:
                index.warnings.append({"code": "manifest_read_failed", "path": str(path)})
                continue
            stage = str(data.get("stage") or "")
            prefix = str(data.get("prefix") or "")
            context = _manifest_stage_context(data)
            species, family = _infer_species_family(path, data)
            if family and not index.family:
                index.family = family
            for key in ("target_species", "reference_species", "comparative_species", "selected_species"):
                values = data.get(key) or context.get(key)
                if not isinstance(values, list):
                    continue
                if key == "target_species":
                    index.target_species = self._merge(index.target_species, values)
                elif key == "reference_species":
                    index.reference_species = self._merge(index.reference_species, values)
                elif key == "comparative_species":
                    index.comparative_species = self._merge(index.comparative_species, values)

            params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
            index.manifests.append({
                "path": str(path),
                "stage": stage,
                "status": data.get("status", ""),
                "prefix": prefix,
                "output_prefix": data.get("output_prefix", ""),
                "canonical_member_prefix": data.get("canonical_member_prefix", ""),
                "legacy_output_prefix": data.get("legacy_output_prefix", ""),
                "member_set_id": data.get("member_set_id", ""),
                "result_group": data.get("result_group", ""),
                "analysis_route": data.get("analysis_route", ""),
                "scope_id": data.get("scope_id", ""),
                "scope_mode": data.get("scope_mode", ""),
                "family": family,
                "species": species,
                "target_species": data.get("target_species") or context.get("target_species") or [],
                "reference_species": data.get("reference_species") or context.get("reference_species") or [],
                "comparative_species": data.get("comparative_species") or context.get("comparative_species") or [],
                "selected_species": data.get("selected_species") or context.get("selected_species") or [],
                "stage_context": context,
                "parameters": params,
            })
            self._index_manifest_artifacts(index, path, data, stage, species, family)

    def _index_manifest_artifacts(
        self,
        index: ProjectContextIndex,
        manifest_path: Path,
        manifest: Dict[str, Any],
        stage: str,
        species: str,
        family: str,
    ) -> None:
        artifacts = manifest.get("artifacts") or {}
        if isinstance(artifacts, dict):
            iterable = artifacts.items()
        elif isinstance(artifacts, list):
            iterable = [(str(item.get("artifact") or item.get("name") or idx), item) for idx, item in enumerate(artifacts) if isinstance(item, dict)]
        else:
            return

        workspace = Path(index.workspace)
        for key, artifact in iterable:
            if not isinstance(artifact, dict):
                continue
            path = self._artifact_path_from_manifest(artifact, workspace, Path(index.input_dir))
            if path is None:
                continue
            kind = next(
                (
                    value.strip()
                    for value in (artifact.get("type"), artifact.get("kind"))
                    if isinstance(value, str) and value.strip()
                ),
                "",
            )
            if not kind:
                role = _single_string(
                    artifact.get("role") or artifact.get("artifact") or key
                )
                capability = get_stage_capability(stage)
                product = next(
                    (item for item in capability.provides if item.role == role),
                    None,
                ) if capability else None
                kind = product.kind if product else ""
            if not kind:
                kind = _infer_file_kind(path)
            exists = path.exists()
            artifact_species = _single_string(artifact.get("species") or artifact.get("target_species") or species)
            artifact_family = _single_string(artifact.get("family") or family)
            artifact_prefix = _single_string(artifact.get("canonical_prefix") or artifact.get("prefix") or artifact.get("output_prefix"))
            if artifact_prefix:
                prefix_species, prefix_family = _split_prefix(artifact_prefix)
                artifact_species = artifact_species or prefix_species
                artifact_family = artifact_family or prefix_family
            index.artifacts.append(ContextArtifact(
                stage=stage,
                key=str(key),
                path=str(path),
                kind=kind,
                exists=exists,
                source_manifest=str(manifest_path),
            ))
            stat = _safe_stat(path)
            index.files.append(ContextFile(
                path=str(path),
                rel_path=self._rel_path_for_display(path, workspace),
                source=f"manifest:{stage}",
                kind=kind,
                species=artifact_species,
                family=artifact_family,
                exists=stat["exists"],
                size=stat["size"],
                mtime=stat["mtime"],
            ))

    def _load_stage_status(self, index: ProjectContextIndex, job_id: str, prefix: str) -> None:
        if not job_id or not prefix:
            return
        for stage in sorted(STAGE_INFO.keys()):
            try:
                report = get_stage_status(
                    job_id=job_id,
                    prefix=prefix,
                    stage=stage,
                    workspace_root=self.workspace_root,
                    allow_legacy_migration=self.allow_legacy_manifest_migration,
                )
                if report.status != "missing" or report.manifest_path:
                    index.stage_status[stage] = report.to_dict()
            except Exception as exc:
                index.stage_status[stage] = {"stage": stage, "status": "error", "errors": [str(exc)]}

    def _scan_logs(self, index: ProjectContextIndex, logs_dir: Path) -> None:
        for path in sorted(logs_dir.glob("*.log"))[-50:] if logs_dir.exists() else []:
            stat = _safe_stat(path)
            index.logs.append({
                "path": str(path),
                "name": path.name,
                "size": stat["size"],
                "mtime": stat["mtime"],
            })

    def _derive_warnings(self, index: ProjectContextIndex) -> None:
        if not index.config_path:
            index.warnings.append({"code": "config_missing", "message": "No config.xlsx/config workbook detected in input directory."})

        for artifact in index.artifacts:
            if not artifact.exists:
                index.warnings.append({
                    "code": "artifact_missing",
                    "stage": artifact.stage,
                    "key": artifact.key,
                    "path": artifact.path,
                })

        for file in index.files:
            if file.exists and file.size == 0:
                index.warnings.append({
                    "code": "empty_file",
                    "kind": file.kind,
                    "path": file.path,
                })

    def _best_prefix(self, index: ProjectContextIndex) -> str:
        prefixes = [m.get("prefix", "") for m in index.manifests if m.get("prefix")]
        if prefixes:
            return prefixes[-1]
        if index.family and len(index.target_species) == 1:
            return f"{index.target_species[0]}.{index.family}"
        if index.family:
            return f"Multi.{index.family}"
        return ""

    @staticmethod
    def _csv(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [x.strip() for x in re.split(r"[,;，；\s]+", str(value or "")) if x.strip()]

    @staticmethod
    def _merge(current: List[str], new_values: Iterable[Any]) -> List[str]:
        seen = set()
        merged: List[str] = []
        for value in list(current) + [str(v).strip() for v in new_values if str(v).strip()]:
            if value not in seen:
                seen.add(value)
                merged.append(value)
        return merged

    @staticmethod
    def _artifact_path_from_manifest(artifact: Dict[str, Any], workspace: Path, input_dir: Path) -> Optional[Path]:
        """Map stale manifest paths back onto the current job roots when possible."""
        raw_values = [
            artifact.get("relpath_or_hint"),
            artifact.get("path"),
            artifact.get("full_path"),
            artifact.get("file"),
        ]
        candidates: List[Path] = []
        for raw in raw_values:
            text = str(raw or "").strip()
            if not text:
                continue
            raw_path = Path(text).expanduser()
            if raw_path.is_absolute():
                candidates.append(raw_path)
            else:
                candidates.extend([
                    workspace / raw_path,
                    input_dir / raw_path,
                    workspace / "final" / raw_path,
                ])

        for candidate in candidates:
            mapped = ProjectContextIndexer._map_to_current_root(candidate, workspace, input_dir)
            if mapped.exists():
                return mapped
        for candidate in candidates:
            mapped = ProjectContextIndexer._map_to_current_root(candidate, workspace, input_dir)
            if ProjectContextIndexer._is_relative_to(mapped, workspace) or ProjectContextIndexer._is_relative_to(mapped, input_dir):
                return mapped
        return candidates[0] if candidates else None

    @staticmethod
    def _map_to_current_root(path: Path, workspace: Path, input_dir: Path) -> Path:
        for root in (workspace, input_dir):
            if ProjectContextIndexer._is_relative_to(path, root):
                try:
                    return root / path.resolve().relative_to(root.resolve())
                except Exception:
                    return path
        return path

    @staticmethod
    def _rel_path_for_display(path: Path, workspace: Path) -> str:
        try:
            return str(path.relative_to(workspace))
        except Exception:
            pass
        try:
            return str(path.resolve().relative_to(workspace.resolve()))
        except Exception:
            return str(path)

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except Exception:
            return False


def build_project_context(
    job_id: str,
    prefix: str = "",
    project_root: Path | str = PROJECT_ROOT,
    *,
    allow_legacy_manifest_migration: bool = True,
    include_stage_status: bool = True,
) -> ProjectContextIndex:
    return ProjectContextIndexer(
        project_root,
        allow_legacy_manifest_migration=allow_legacy_manifest_migration,
    ).build(
        job_id=job_id,
        prefix=prefix,
        include_stage_status=include_stage_status,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build read-only bbav5 project context index")
    parser.add_argument("job_id")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    index = build_project_context(args.job_id, prefix=args.prefix, project_root=args.project_root)
    data = index.to_dict()
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
