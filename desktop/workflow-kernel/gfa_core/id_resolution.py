"""Offline parsing of species-scoped, job-local identifier alias evidence."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_LEGACY_COLUMNS = frozenset(
    {
        "species_code",
        "protein_id",
        "pep_id",
        "cds_id",
        "gene_id",
        "cds_header",
    }
)
_ACCESSION_COLUMNS = (
    ("protein_id", "protein_accession"),
    ("pep_id", "peptide_id"),
    ("cds_id", "cds_id"),
)
_GENE_ID_PATTERN = re.compile(r"\[db_xref=GeneID:(\d+)\]")
_VERSION_PATTERN = re.compile(r"^(.+)\.(\d+)$")
_NAMESPACE_PRIORITY = {
    "protein_accession": 0,
    "peptide_id": 0,
    "cds_id": 0,
    "ncbi_gene_id": 1,
    "gene_symbol": 2,
}


class IdResolutionError(ValueError):
    """Structured failure raised for invalid or ambiguous alias evidence."""

    def __init__(self, message: str, *, code: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True, slots=True)
class IdAliasCandidate:
    value: str
    namespace: str
    method: str
    evidence_source: str


@dataclass(frozen=True, slots=True)
class _RowEvidence:
    candidates: tuple[IdAliasCandidate, ...]
    gene_ids: frozenset[str]
    gene_symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class _EntityEvidence:
    candidates: tuple[IdAliasCandidate, ...]
    gene_ids: frozenset[str]
    gene_symbols: frozenset[str]


class ReferenceAliasIndex:
    """An in-memory index built only from one local, species-scoped TSV."""

    def __init__(
        self,
        exact: dict[str, _EntityEvidence],
        versionless: dict[str, tuple[str, ...]],
        *,
        source_path: str = "",
        declared_species: str = "",
    ) -> None:
        self._exact = exact
        self._versionless = versionless
        self._source_path = source_path
        self._declared_species = declared_species

    @classmethod
    def from_tsv(cls, path: Path, species: str) -> ReferenceAliasIndex:
        source = Path(path)
        declared_species = str(species).strip()
        if not declared_species:
            raise IdResolutionError(
                "Declared species is empty",
                code="invalid_species",
                path=str(source),
            )
        if not source.is_file():
            raise IdResolutionError(
                f"Alias evidence file does not exist: {source}",
                code="missing_file",
                path=str(source),
            )

        rows_by_accession: dict[str, list[_RowEvidence]] = {}
        try:
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                fieldnames = reader.fieldnames or []
                missing = sorted(_LEGACY_COLUMNS.difference(fieldnames))
                if missing or len(fieldnames) != len(set(fieldnames)):
                    raise IdResolutionError(
                        "Alias evidence has unusable required columns",
                        code="invalid_columns",
                        path=str(source),
                        missing_columns=missing,
                        columns=fieldnames,
                    )

                explicit_gene_id_column = "ncbi_gene_id" in fieldnames
                populated_rows = 0
                for row_number, raw_row in enumerate(reader, start=2):
                    if None in raw_row:
                        raise IdResolutionError(
                            f"Alias evidence row {row_number} has extra columns",
                            code="invalid_row",
                            path=str(source),
                            row_number=row_number,
                        )
                    row = {
                        name: str(raw_row.get(name) or "").strip()
                        for name in fieldnames
                    }
                    if not any(row.values()):
                        continue
                    populated_rows += 1

                    observed_species = row["species_code"]
                    if observed_species != declared_species:
                        raise IdResolutionError(
                            f"Alias evidence species mismatch at row {row_number}: "
                            f"declared {declared_species!r}, observed {observed_species!r}",
                            code="species_mismatch",
                            path=str(source),
                            row_number=row_number,
                            declared_species=declared_species,
                            observed_species=observed_species,
                        )

                    accessions = tuple(
                        dict.fromkeys(
                            row[column]
                            for column, _namespace in _ACCESSION_COLUMNS
                            if row[column]
                        )
                    )
                    if not accessions:
                        raise IdResolutionError(
                            f"Alias evidence row {row_number} has no accession",
                            code="invalid_row",
                            path=str(source),
                            row_number=row_number,
                        )

                    candidates = [
                        IdAliasCandidate(
                            value=row[column],
                            namespace=namespace,
                            method=f"tsv_column:{column}",
                            evidence_source=str(source),
                        )
                        for column, namespace in _ACCESSION_COLUMNS
                        if row[column]
                    ]
                    numeric_gene_id = cls._numeric_gene_id(
                        row,
                        explicit_column=explicit_gene_id_column,
                        source=source,
                        row_number=row_number,
                    )
                    if numeric_gene_id:
                        candidates.append(
                            IdAliasCandidate(
                                value=numeric_gene_id,
                                namespace="ncbi_gene_id",
                                method=(
                                    "tsv_column:ncbi_gene_id"
                                    if explicit_gene_id_column
                                    else "cds_header:db_xref"
                                ),
                                evidence_source=str(source),
                            )
                        )
                    gene_symbol = row["gene_id"]
                    if gene_symbol:
                        candidates.append(
                            IdAliasCandidate(
                                value=gene_symbol,
                                namespace="gene_symbol",
                                method="tsv_column:gene_id",
                                evidence_source=str(source),
                            )
                        )

                    evidence = _RowEvidence(
                        candidates=tuple(candidates),
                        gene_ids=frozenset({numeric_gene_id} if numeric_gene_id else ()),
                        gene_symbols=frozenset({gene_symbol} if gene_symbol else ()),
                    )
                    for accession in accessions:
                        rows_by_accession.setdefault(accession, []).append(evidence)
        except IdResolutionError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise IdResolutionError(
                f"Cannot read alias evidence file {source}: {exc}",
                code="unreadable_file",
                path=str(source),
            ) from exc

        if not populated_rows or not rows_by_accession:
            raise IdResolutionError(
                "Alias evidence file has no usable rows",
                code="empty_evidence",
                path=str(source),
            )

        exact = {
            accession: cls._merge_exact_evidence(accession, evidence_rows, source)
            for accession, evidence_rows in rows_by_accession.items()
        }
        versionless: dict[str, list[str]] = {}
        for accession in exact:
            match = _VERSION_PATTERN.fullmatch(accession)
            if match:
                versionless.setdefault(match.group(1), []).append(accession)
        return cls(
            exact=exact,
            versionless={
                base: tuple(
                    sorted(
                        ([base] if base in exact else []) + accessions,
                        key=cls._version_sort_key,
                    )
                )
                for base, accessions in versionless.items()
            },
            source_path=str(source),
            declared_species=declared_species,
        )

    def candidates(self, raw_id: str) -> list[IdAliasCandidate]:
        query = str(raw_id or "").strip()
        if not query:
            return []

        exact = self._exact.get(query)
        if exact is not None and _VERSION_PATTERN.fullmatch(query):
            return self._ordered_candidates(exact.candidates, primary=query)

        matching_accessions = self._versionless.get(query, ())
        if not matching_accessions:
            return (
                self._ordered_candidates(exact.candidates, primary=query)
                if exact is not None
                else []
            )
        entities = [self._exact[accession] for accession in matching_accessions]
        if len(entities) > 1 and not self._entities_converge(entities):
            raise IdResolutionError(
                f"Versionless alias lookup is ambiguous for {query!r}",
                code="ambiguous_versionless",
                raw_id=query,
                matching_accessions=list(matching_accessions),
                gene_ids=sorted(
                    {gene_id for entity in entities for gene_id in entity.gene_ids}
                ),
                gene_symbols=sorted(
                    {
                        gene_symbol
                        for entity in entities
                        for gene_symbol in entity.gene_symbols
                    }
                ),
                source_path=self._source_path,
                declared_species=self._declared_species,
            )
        return self._ordered_candidates(
            candidate
            for entity in entities
            for candidate in entity.candidates
        )

    @staticmethod
    def _numeric_gene_id(
        row: dict[str, str],
        *,
        explicit_column: bool,
        source: Path,
        row_number: int,
    ) -> str:
        if explicit_column:
            value = row.get("ncbi_gene_id", "")
            if value and not value.isdigit():
                raise IdResolutionError(
                    f"Invalid ncbi_gene_id at row {row_number}: {value!r}",
                    code="invalid_gene_id",
                    path=str(source),
                    row_number=row_number,
                    value=value,
                )
            return value

        matches = set(_GENE_ID_PATTERN.findall(row["cds_header"]))
        if len(matches) > 1:
            raise IdResolutionError(
                f"Ambiguous GeneID evidence at row {row_number}",
                code="ambiguous_evidence",
                path=str(source),
                row_number=row_number,
                gene_ids=sorted(matches),
            )
        return next(iter(matches), "")

    @classmethod
    def _merge_exact_evidence(
        cls,
        accession: str,
        rows: list[_RowEvidence],
        source: Path,
    ) -> _EntityEvidence:
        gene_ids = frozenset(
            gene_id for row in rows for gene_id in row.gene_ids
        )
        gene_symbols = frozenset(
            gene_symbol for row in rows for gene_symbol in row.gene_symbols
        )
        if len(gene_ids) > 1 or (not gene_ids and len(gene_symbols) > 1):
            raise IdResolutionError(
                f"Ambiguous alias evidence for exact accession {accession!r}",
                code="ambiguous_evidence",
                path=str(source),
                accession=accession,
                gene_ids=sorted(gene_ids),
                gene_symbols=sorted(gene_symbols),
            )
        return _EntityEvidence(
            candidates=tuple(
                cls._ordered_candidates(
                    candidate for row in rows for candidate in row.candidates
                )
            ),
            gene_ids=gene_ids,
            gene_symbols=gene_symbols,
        )

    @staticmethod
    def _entities_converge(entities: list[_EntityEvidence]) -> bool:
        gene_ids_by_entity = [entity.gene_ids for entity in entities]
        if all(len(gene_ids) == 1 for gene_ids in gene_ids_by_entity):
            return len(set().union(*gene_ids_by_entity)) == 1

        all_gene_ids = set().union(*gene_ids_by_entity)
        if len(all_gene_ids) > 1:
            return False
        symbols_by_entity = [entity.gene_symbols for entity in entities]
        return (
            all(len(symbols) == 1 for symbols in symbols_by_entity)
            and len(set().union(*symbols_by_entity)) == 1
        )

    @staticmethod
    def _ordered_candidates(
        candidates: Iterable[IdAliasCandidate],
        *,
        primary: str = "",
    ) -> list[IdAliasCandidate]:
        indexed = list(enumerate(candidates))
        indexed.sort(
            key=lambda item: (
                -1 if item[1].value == primary else _NAMESPACE_PRIORITY[item[1].namespace],
                item[0],
            )
        )
        result: list[IdAliasCandidate] = []
        seen_values: set[str] = set()
        for _index, candidate in indexed:
            if candidate.value in seen_values:
                continue
            seen_values.add(candidate.value)
            result.append(candidate)
        return result

    @staticmethod
    def _version_sort_key(accession: str) -> tuple[str, int, str]:
        match = _VERSION_PATTERN.fullmatch(accession)
        if not match:
            return accession, -1, accession
        return match.group(1), int(match.group(2)), accession
