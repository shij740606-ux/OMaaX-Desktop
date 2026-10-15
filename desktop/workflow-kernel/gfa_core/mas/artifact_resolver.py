"""Semantic artifact resolver for bbav5 planning and diagnosis.

The resolver maps a stage capability need such as ``target_proteome`` or
``family_members_pep`` to concrete files from ProjectContextIndex.  It is
read-only and returns a decision trace so callers can explain why a file was
selected or what alternatives were available.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import re

from gfa_core.mas.context_index import ContextArtifact, ContextFile, ProjectContextIndex, build_project_context
from gfa_core.mas.stage_capabilities import ArtifactNeed, get_stage_capability


@dataclass
class ResolveCandidate:
    path: str
    kind: str
    source: str
    role: str = ""
    species: str = ""
    family: str = ""
    exists: bool = True
    score: int = 0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolveDecision:
    stage: str
    role: str
    kind: str
    resolved: bool
    selected: Optional[ResolveCandidate] = None
    candidates: List[ResolveCandidate] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["selected"] = self.selected.to_dict() if self.selected else None
        data["candidates"] = [item.to_dict() for item in self.candidates]
        return data


class ArtifactResolver:
    def __init__(self, context: ProjectContextIndex):
        self.context = context

    def resolve_stage(self, stage: str, species: str = "", family: str = "") -> Dict[str, ResolveDecision]:
        capability = get_stage_capability(stage)
        if not capability:
            return {}
        return {
            need.role: self.resolve_need(stage, need, species=species, family=family)
            for need in capability.consumes
        }

    def resolve_need(
        self,
        stage: str,
        need: ArtifactNeed,
        species: str = "",
        family: str = "",
        explicit_path: str = "",
    ) -> ResolveDecision:
        decision = ResolveDecision(stage=stage, role=need.role, kind=need.kind, resolved=False)
        species = species or self._default_species(need)
        family = family or self.context.family

        if explicit_path:
            path = Path(explicit_path).expanduser()
            decision.candidates.append(ResolveCandidate(
                path=str(path),
                kind=need.kind,
                source="explicit",
                role=need.role,
                species=species,
                family=family,
                exists=path.exists(),
                score=1000 if path.exists() else 0,
                reason="explicit user parameter",
            ))

        decision.candidates.extend(self._candidates_from_artifacts(need, species, family))
        decision.candidates.extend(self._candidates_from_manifests(need, species, family))
        decision.candidates.extend(self._candidates_from_files(need, species, family))
        decision.candidates.extend(self._canonical_candidates(need, species, family))

        # Prefer existing candidates and then higher scores.  Keep missing
        # candidates in the trace so the UI can explain attempted names.
        decision.candidates = self._dedupe(decision.candidates)
        decision.candidates.sort(key=lambda c: (c.exists, c.score), reverse=True)
        if decision.candidates and decision.candidates[0].exists:
            decision.selected = decision.candidates[0]
            decision.resolved = True
            decision.messages.append(f"resolved {need.role} from {decision.selected.source}: {decision.selected.path}")
        elif need.required:
            decision.messages.append(f"required artifact not resolved: {need.role} ({need.kind})")
        else:
            decision.messages.append(f"optional artifact not resolved: {need.role} ({need.kind})")
        return decision

    def _default_species(self, need: ArtifactNeed) -> str:
        if "reference" in need.source_roles and self.context.reference_species:
            return self.context.reference_species[0]
        if "comparative" in need.source_roles and self.context.comparative_species:
            return self.context.comparative_species[0]
        if self.context.target_species and len(self.context.target_species) == 1:
            return self.context.target_species[0]
        return ""

    def _candidates_from_artifacts(self, need: ArtifactNeed, species: str, family: str) -> List[ResolveCandidate]:
        candidates: List[ResolveCandidate] = []
        for artifact in self.context.artifacts:
            if not self._kind_matches(need.kind, artifact.kind):
                continue
            if not self._role_accepts_path(need.role, artifact.key, artifact.path):
                continue
            if need.role == "gene_subfamily" and artifact.stage not in {"reference_classification", "subfamily_confirm"}:
                continue
            if need.role == "reference_subfamily" and not self._reference_subfamily_candidate_ok(artifact.path, artifact.key):
                continue
            if need.role == "target_gff" and not species and self.context.target_species:
                if not any(
                    self._path_mentions_species(artifact.path, target) or self._path_mentions_species(artifact.key, target)
                    for target in self.context.target_species
                ):
                    continue
            if need.role == "target_proteome" and not species and self.context.target_species:
                if not any(
                    self._path_mentions_species(artifact.path, target) or self._path_mentions_species(artifact.key, target)
                    for target in self.context.target_species
                ):
                    continue
            if species and need.role in {"family_members_pep", "target_proteome", "target_gff", "target_genome", "reference_family_pep", "family_members_id", "gene_subfamily", "reference_subfamily", "tree"}:
                if not self._path_mentions_species(artifact.path, species) and not self._path_mentions_species(artifact.key, species):
                    continue
            score = 500
            if self._artifact_key_matches(need.role, artifact.key):
                score += 200
            if self._stage_preference(need.role, artifact.stage):
                score += self._stage_preference(need.role, artifact.stage)
            if family and family.lower() in artifact.path.lower():
                score += 30
            if species and self._path_mentions_species(artifact.path, species):
                score += 50
            if need.role == "target_gff" and self.context.target_species:
                score += self._species_set_score(artifact.path, artifact.key, self.context.target_species, preferred_first=True)
            if need.role == "reference_subfamily":
                score += self._reference_subfamily_score(artifact.path, artifact.key, species, family)
            if need.role == "tree":
                score += self._artifact_scope_score(artifact.path, artifact.source_manifest)
            candidates.append(ResolveCandidate(
                path=artifact.path,
                kind=artifact.kind,
                source=f"artifact:{artifact.stage}:{artifact.key}",
                role=need.role,
                species=species,
                family=family,
                exists=artifact.exists,
                score=score,
                reason="manifest artifact",
            ))
        return candidates

    def _candidates_from_manifests(self, need: ArtifactNeed, species: str = "", family: str = "") -> List[ResolveCandidate]:
        if need.role == "family_members_pep":
            candidates: List[ResolveCandidate] = []
            for manifest in self.context.manifests:
                if str(manifest.get("stage") or "") != "phylogeny":
                    continue
                params = manifest.get("parameters") or {}
                if not isinstance(params, dict):
                    continue
                fasta_text = str(params.get("scope_input_fasta") or "")
                if not fasta_text:
                    continue
                path = Path(fasta_text)
                if not path.is_absolute():
                    path = Path(self.context.workspace) / path
                scope_data = manifest.get("scope") or manifest.get("stage_context") or {}
                if not isinstance(scope_data, dict):
                    scope_data = {}
                scope_targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
                scope_selected = [str(x) for x in scope_data.get("selected_species") or [] if str(x).strip()]
                manifest_species = scope_targets or scope_selected
                if species:
                    if manifest_species and species not in manifest_species:
                        continue
                    if not manifest_species and not self._path_mentions_species(str(path), species):
                        continue
                elif self.context.target_species:
                    if manifest_species and not any(target in manifest_species for target in self.context.target_species):
                        continue
                    if not manifest_species and not any(
                        self._path_mentions_species(str(path), target) for target in self.context.target_species
                    ):
                        continue
                score = 760
                if str(manifest.get("scope_id") or ""):
                    score += 260
                if str(manifest.get("status") or "") in {"success", "partial_success"}:
                    score += 120
                scope_mode = str(scope_data.get("scope_mode") or manifest.get("scope_mode") or "")
                if species and manifest_species == [species]:
                    score += 140
                elif species and species in manifest_species:
                    score += 40
                if scope_mode == "target_only":
                    score += 80
                if species and self._path_mentions_species(str(path), species):
                    score += 80
                if family and family.lower() in path.name.lower():
                    score += 40
                candidates.append(ResolveCandidate(
                    path=str(path),
                    kind="protein_fasta",
                    source=f"manifest:{manifest.get('stage', '')}:scope_input_fasta",
                    role=need.role,
                    species=species,
                    family=self.context.family,
                    exists=path.exists(),
                    score=score,
                    reason="phylogeny scoped input fasta",
                ))
            if candidates:
                return candidates

        if need.kind != "manifest" and need.role != "stage_manifests":
            return []
        candidates: List[ResolveCandidate] = []
        for manifest in self.context.manifests:
            path = manifest.get("path", "")
            if not path:
                continue
            status = str(manifest.get("status") or "")
            score = 400 if status in {"success", "partial_success"} else 100
            candidates.append(ResolveCandidate(
                path=path,
                kind="manifest",
                source=f"manifest:{manifest.get('stage', '')}",
                role=need.role,
                exists=Path(path).exists(),
                score=score,
                reason="stage manifest index",
            ))
        return candidates

    def _candidates_from_files(self, need: ArtifactNeed, species: str, family: str) -> List[ResolveCandidate]:
        candidates: List[ResolveCandidate] = []
        for file in self.context.files:
            if not self._kind_matches(need.kind, file.kind):
                continue
            if not self._role_accepts_path(need.role, "", file.path):
                continue
            if need.role == "reference_subfamily" and not self._reference_subfamily_candidate_ok(file.path, ""):
                continue
            if need.role == "target_gff" and not species and self.context.target_species:
                if file.species:
                    if file.species not in self.context.target_species:
                        continue
                elif not any(self._path_mentions_species(file.path, target) for target in self.context.target_species):
                    continue
            if need.role == "target_proteome" and not species and self.context.target_species:
                if file.species:
                    if file.species not in self.context.target_species:
                        continue
                elif not any(self._path_mentions_species(file.path, target) for target in self.context.target_species):
                    continue
            if species and need.role in {"family_members_pep", "target_proteome", "target_gff", "target_genome", "reference_family_pep", "family_members_id", "gene_subfamily", "reference_subfamily", "tree"}:
                if file.species and file.species != species:
                    continue
                if not file.species and not self._path_mentions_species(file.path, species):
                    continue
            score = 100
            if family and (file.family == family or family.lower() in Path(file.path).name.lower()):
                score += 40
            if species and (file.species == species or self._path_mentions_species(file.path, species)):
                score += 60
            if file.source == "final":
                score += 60
            elif file.source.startswith("manifest:"):
                score += 80
            elif file.source == "config":
                score += 70
            elif file.source == "input":
                score += 40
            if self._filename_matches_role(need.role, Path(file.path).name):
                score += 80
            if need.role == "target_gff" and self.context.target_species:
                score += self._species_set_score(file.path, "", self.context.target_species, preferred_first=True)
            if need.role == "reference_subfamily":
                score += self._reference_subfamily_score(file.path, "", species, family)
            if need.role == "tree":
                score += self._artifact_scope_score(file.path, "")
            candidates.append(ResolveCandidate(
                path=file.path,
                kind=file.kind,
                source=file.source,
                role=need.role,
                species=file.species or species,
                family=file.family or family,
                exists=file.exists,
                score=score,
                reason="context file scan",
            ))
        return candidates

    def _canonical_candidates(self, need: ArtifactNeed, species: str, family: str) -> List[ResolveCandidate]:
        final_dir = Path(self.context.final_dir)
        paths: List[Path] = []
        prefix = f"{species}.{family}" if species and family else family or species

        if need.role in {"family_members_pep", "target_proteome"} and prefix:
            paths.extend([
                final_dir / f"{prefix}.final_members.pep.fa",
                final_dir / f"{prefix}.pep.fa",
                final_dir / f"{prefix}.protein.fa",
            ])
        if need.role == "reference_family_pep" and species and family:
            paths.extend([
                final_dir / f"{species}.{family}.pep.fa",
                final_dir / f"{species}.{family}.pep.fasta",
                final_dir / f"{species}.{family}.ncbi_fetch.protein.fa",
            ])
        if need.role == "family_members_id" and prefix:
            paths.append(final_dir / f"{prefix}.final_members.id")
        if need.role == "tree" and prefix:
            expected_prefix = str(getattr(self.context, "expected_output_prefix", "") or "")
            tree_prefixes = [prefix]
            if expected_prefix and expected_prefix not in tree_prefixes:
                tree_prefixes.insert(0, expected_prefix)
            for tree_prefix in tree_prefixes:
                paths.extend([
                    final_dir / f"{tree_prefix}.tree.nwk",
                    final_dir / f"{tree_prefix}.phylogeny.treefile",
                    final_dir / f"{tree_prefix}.iqtree.treefile",
                ])
        if need.role == "target_gff" and species:
            paths.extend([
                final_dir / f"{species}_final.gff3",
                Path(self.context.input_dir) / f"{species}_final.gff3",
            ])
        if need.role == "target_genome" and species:
            paths.extend([
                final_dir / f"{species}.genome.fasta",
                final_dir / f"{species}.genome.fa",
                Path(self.context.input_dir) / f"{species}.genome.fasta",
                Path(self.context.input_dir) / f"{species}.genome.fa",
            ])
        if need.role == "plant_tf_motif_database" or need.kind == "meme":
            project_root = Path(__file__).resolve().parents[2]
            paths.extend([
                project_root / "data" / "tfbs" / "jaspar2026" / "combined" / "JASPAR2026_CORE_plants_non-redundant.meme",
            ])
        if need.role == "gene_subfamily" and prefix:
            expected_prefix = str(getattr(self.context, "expected_output_prefix", "") or "")
            subfamily_prefixes = [prefix]
            if expected_prefix and expected_prefix not in subfamily_prefixes:
                subfamily_prefixes.insert(0, expected_prefix)
            for subfamily_prefix in subfamily_prefixes:
                paths.extend([
                    final_dir / f"{subfamily_prefix}.confirmed_subfamily.tsv",
                    final_dir / f"{subfamily_prefix}.geneID.subfamily",
                    final_dir / f"{subfamily_prefix}.reference_classification.subfamily.tsv",
                    Path(self.context.input_dir) / f"{subfamily_prefix}.confirmed_subfamily.tsv",
                    Path(self.context.input_dir) / f"{subfamily_prefix}.geneID.subfamily",
                ])
        if need.role == "motif_result" and prefix:
            paths.extend([
                final_dir / f"{prefix}.motif.tsv",
                final_dir / f"{prefix}.motif.txt",
                final_dir / f"{prefix}.meme.tsv",
            ])
        if need.role == "domain_result" and prefix:
            paths.extend([
                final_dir / f"{prefix}.domain.tsv",
                final_dir / f"{prefix}.domain.txt",
            ])
        if need.role == "structure_result" and prefix:
            paths.extend([
                final_dir / f"{prefix}.structure.gff.tsv",
                final_dir / f"{prefix}.structure.tsv",
                final_dir / f"{prefix}.gene_structure.tsv",
            ])
        if need.role == "reference_subfamily":
            ref_species = species or (self.context.reference_species[0] if self.context.reference_species else "")
            if ref_species and family:
                paths.extend([
                    Path(self.context.input_dir) / f"{ref_species}.{family}.subfamily",
                    Path(self.context.input_dir) / f"{ref_species}.{family}.geneID.subfamily",
                    final_dir / f"{ref_species}.{family}.subfamily",
                    final_dir / f"{ref_species}.{family}.geneID.subfamily",
                ])
            if prefix:
                paths.append(final_dir / f"{prefix}.geneID.subfamily")
        if need.role == "pfam_database" or need.kind == "hmm_database":
            paths.extend([
                Path(__file__).resolve().parents[2] / "data" / "hmm" / "Pfam-A.hmm",
            ])
        if need.role == "hmm_profile" or need.kind == "hmm":
            paths.extend([
                Path(self.context.input_dir) / f"{family}.hmm",
                Path(self.context.input_dir) / f"{family}.hmmprofile",
            ])

        return [
            ResolveCandidate(
                path=str(path),
                kind=need.kind,
                source="canonical",
                role=need.role,
                species=species,
                family=family,
                exists=path.exists(),
                score=300 if path.exists() else 10,
                reason="canonical filename",
            )
            for path in paths
        ]

    @staticmethod
    def _kind_matches(required: str, actual: str) -> bool:
        if required == actual:
            return True
        aliases = {
            "fasta": {"protein_fasta", "cds_fasta", "genome_fasta", "alignment_fasta"},
            "protein_fasta": {"fasta"},
            "table": {"id_list", "expression_matrix"},
            "manifest": {"metadata"},
            "tree": {"newick"},
        }
        return actual in aliases.get(required, set())

    @staticmethod
    def _artifact_key_matches(role: str, key: str) -> bool:
        key = key.lower()
        role = role.lower()
        patterns = {
            "family_members_pep": ["final_members", "final_proteins", "proteins"],
            "target_proteome": ["prepared_protein", "protein", "pep"],
            "target_genome": ["genome", "fasta", "fa"],
            "reference_family_pep": ["reference", "protein", "pep"],
            "family_members_id": ["final_members_id", "gene_ids", "ids"],
            "tree": ["tree"],
            "target_gff": ["gff", "gff3"],
            "plant_tf_motif_database": ["jaspar", "planttfdb", "motif", "meme"],
            "gene_subfamily": ["subfamily"],
            "reference_subfamily": ["subfamily"],
            "motif_result": ["motif", "meme"],
            "domain_result": ["domain", "pfam"],
            "structure_result": ["structure", "gff"],
        }
        return any(token in key for token in patterns.get(role, [role]))

    @staticmethod
    def _stage_preference(role: str, stage: str) -> int:
        prefs = {
            "target_proteome": {"genome_prepare": 160, "genome_prepare_v2": 160, "identify": 60},
            "target_genome": {"genome_prepare": 160, "genome_prepare_v2": 160},
            "family_members_pep": {"identify": 180, "phylogeny": 120},
            "family_members_id": {"identify": 180},
            "tree": {"phylogeny": 180},
            "gene_subfamily": {"subfamily_confirm": 240, "reference_classification": 180},
            "reference_subfamily": {"reference_classification": 80},
            "target_gff": {"genome_prepare": 160, "genome_prepare_v2": 160},
            "motif_result": {"motif": 180},
            "domain_result": {"domain": 180},
            "structure_result": {"structure": 180},
        }
        return prefs.get(role, {}).get(stage, 0)

    @staticmethod
    def _filename_matches_role(role: str, name: str) -> bool:
        lowered = name.lower()
        patterns = {
            "target_proteome": ["pep", "protein", "proteome"],
            "target_genome": ["genome", ".fna", ".fa", ".fasta"],
            "family_members_pep": ["final_members.pep", "final_gfmembers.pep"],
            "reference_family_pep": ["ncbi_fetch.protein", ".pep.fa", ".pep.fasta"],
            "family_members_id": ["final_members.id", "final_gfmembers.id"],
            "tree": [".tree.nwk"],
            "target_gff": ["_final.gff3", ".gff3", ".gff"],
            "plant_tf_motif_database": ["jaspar", "planttfdb", ".meme"],
            "gene_subfamily": ["subfamily"],
            "reference_subfamily": [".subfamily", "geneid.subfamily", "confirmed_subfamily"],
            "motif_result": ["motif.tsv", "motif.txt", "meme.tsv"],
            "domain_result": ["domain.tsv", "domain.txt"],
            "structure_result": ["structure.gff.tsv", "structure.tsv", "gene_structure.tsv"],
        }
        return any(token in lowered for token in patterns.get(role, [role.lower()]))

    @staticmethod
    def _role_accepts_path(role: str, key: str, path: str) -> bool:
        text = f"{key} {Path(path).name}".lower()
        if role == "wgs_sample_config":
            return Path(path).name.lower() == "wgs_samples.yaml"
        if role == "family_members_pep":
            return any(token in text for token in ["final_members", "final_gfmembers", "scope_input", "input.pep.clean"])
        if role == "family_members_id":
            if any(token in text for token in ["interproscan", "domain_review", "domain_check", ".tsv", ".csv"]):
                return False
            return any(token in text for token in ["final_members.id", "final_gfmembers.id", "members.id", "curated_members.id"])
        if role == "target_proteome":
            return not any(token in text for token in ["final_members", "final_gfmembers", "ref_family", "ref_combined", "_ref_", "ref_pep", "ncbi_fetch.protein", "known_family"])
        if role == "reference_family_pep":
            if any(token in text for token in ["final_members", "final_gfmembers", ".msa.", "alignment", "tree"]):
                return False
            return any(token in text for token in ["ref_family", "known_family", "ncbi_fetch.protein", ".pep.fa", ".pep.fasta", ".protein.fa", ".faa"])
        if role == "tree":
            return any(token in text for token in [".tree.nwk", ".treefile", ".newick", ".nwk"])
        if role == "motif_result":
            return any(token in text for token in ["motif.tsv", "motif.txt", "meme.tsv"])
        if role == "domain_result":
            return any(token in text for token in ["domain.tsv", "domain.txt", "pfam.tsv"]) and "domain_review" not in text
        if role == "structure_result":
            return any(token in text for token in ["structure.gff.tsv", "structure.tsv", "gene_structure.tsv"])
        if role == "gene_subfamily":
            return ArtifactResolver._gene_subfamily_candidate_ok(path, key)
        if role == "reference_subfamily":
            return ArtifactResolver._reference_subfamily_candidate_ok(path, key)
        return True


    def _artifact_scope_score(self, path: str, source_manifest: str = "") -> int:
        """Score candidates against the request scope carried on ProjectContextIndex."""
        expected_mode = str(getattr(self.context, "expected_scope_mode", "") or "").strip()
        expected_route = str(getattr(self.context, "expected_analysis_route", "") or "").strip()
        if not expected_mode and not expected_route:
            return 0
        score = 0
        text = Path(path).name.lower()
        manifest = {}
        if source_manifest:
            try:
                import json
                manifest = json.loads(Path(source_manifest).read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        manifest_mode = str(manifest.get("scope_mode") or (manifest.get("parameters") or {}).get("scope_mode") or "").strip()
        manifest_route = str(manifest.get("analysis_route") or (manifest.get("parameters") or {}).get("analysis_route") or "").strip()
        if expected_mode:
            if manifest_mode == expected_mode:
                score += 260
            elif manifest_mode:
                score -= 220
            if expected_mode == "target_only":
                if "target_only" in text:
                    score += 220
                if "target_plus_reference" in text or "ref-" in text:
                    score -= 240
            elif expected_mode and expected_mode in text:
                score += 180
        targets = [str(item).strip().lower() for item in getattr(self.context, "target_species", []) if str(item).strip()]
        if len(targets) == 1 and "target-" in text:
            match = re.search(r"target-([a-z0-9_-]+)", text)
            if match:
                encoded = [item for item in match.group(1).split("-") if item]
                if targets[0] in encoded and len(encoded) == 1:
                    score += 220
                elif targets[0] in encoded:
                    score -= 120
                else:
                    score -= 260
        if expected_route:
            if manifest_route == expected_route:
                score += 180
            elif manifest_route:
                score -= 160
        return score

    @staticmethod
    def _species_set_score(path: str, key: str, species_list: List[str], preferred_first: bool = False) -> int:
        text_path = path
        text_key = key or ""
        for idx, species in enumerate(species_list):
            if ArtifactResolver._path_mentions_species(text_path, species) or ArtifactResolver._path_mentions_species(text_key, species):
                return 140 + (20 if preferred_first and idx == 0 else 0)
        return 0

    @staticmethod
    def _reference_subfamily_candidate_ok(path: str, key: str = "") -> bool:
        text = f"{key} {Path(path).name}".lower()
        bad_tokens = [
            "domain_review",
            "interproscan",
            "domain_check",
            "hmmer",
            "blast",
            "combined.ids",
            "gene_mrna_cds.ids",
            "final_members.id",
            "members.id",
        ]
        if any(token in text for token in bad_tokens):
            return False
        if "reference_classification" in text or "target-" in text or ".ref-" in text:
            return False
        good_tokens = [
            ".subfamily",
            "geneid.subfamily",
            "confirmed_subfamily",
        ]
        return any(token in text for token in good_tokens)

    @staticmethod
    def _gene_subfamily_candidate_ok(path: str, key: str = "") -> bool:
        text = f"{key} {Path(path).name}".lower()
        name = Path(path).name.lower()
        bad_tokens = [
            "candidate_members",
            "curated_members",
            "final_members",
            "final_gfmembers",
            "members.id",
            "subfamily_evidence",
            "_evidence",
            "domain_review",
            "interproscan",
            "domain_check",
            "motif.tsv",
            "motif.txt",
            "domain.tsv",
            "domain.txt",
            "structure.tsv",
            "structure.gff.tsv",
            "gene_structure.tsv",
            "combined.ids",
            "gene_mrna_cds.ids",
            "conservation_profile",
            "msa_conserved",
            "results",
            ".msa.",
            ".tree.",
        ]
        if any(token in text for token in bad_tokens):
            return False
        if "reference_classification.subfamily" in name:
            return True
        if "geneid.subfamily" in name:
            return True
        if "confirmed_subfamily" in name:
            return True
        return False

    @staticmethod
    def _reference_subfamily_score(path: str, key: str, species: str, family: str) -> int:
        name = Path(path).name.lower()
        score = 0
        if name.endswith(".subfamily"):
            score += 260
        if "geneid.subfamily" in name:
            score += 220
        if "confirmed_subfamily" in name:
            score += 240
        if "reference_classification.subfamily" in name:
            score += 200
        if species and ArtifactResolver._path_mentions_species(path, species):
            score += 120
        if family and family.lower() in name:
            score += 60
        if key and "reference" in key.lower():
            score += 80
        return score

    @staticmethod
    def _path_mentions_species(path: str, species: str) -> bool:
        if not species:
            return False
        name = Path(path).name.lower()
        species_lower = species.lower()
        return bool(re.search(rf"(^|[._-]){re.escape(species_lower)}([._-]|$)", name))

    @staticmethod
    def _dedupe(candidates: Iterable[ResolveCandidate]) -> List[ResolveCandidate]:
        best: Dict[str, ResolveCandidate] = {}
        for candidate in candidates:
            old = best.get(candidate.path)
            if old is None or candidate.score > old.score:
                best[candidate.path] = candidate
        return list(best.values())


def resolve_stage_artifacts(job_id: str, stage: str, prefix: str = "", species: str = "", family: str = "") -> Dict[str, Dict[str, Any]]:
    context = build_project_context(job_id=job_id, prefix=prefix)
    resolver = ArtifactResolver(context)
    return {role: decision.to_dict() for role, decision in resolver.resolve_stage(stage, species=species, family=family).items()}


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Resolve bbav5 semantic stage artifacts")
    parser.add_argument("job_id")
    parser.add_argument("stage")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--species", default="")
    parser.add_argument("--family", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = resolve_stage_artifacts(args.job_id, args.stage, prefix=args.prefix, species=args.species, family=args.family)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
