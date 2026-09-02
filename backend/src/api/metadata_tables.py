"""Helpers for metadata table exploration endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy import Column

from metadata_db import schema


@dataclass(frozen=True)
class TableDefinition:
    name: str
    label: str
    columns: Sequence[Column]
    default_order: Sequence[Column]


_TABLES: dict[str, TableDefinition] = {}


def _register(model, *, label: str, default_order: Sequence[str] | None = None) -> None:
    table = model.__table__
    ordered = []
    if default_order:
        for column_name in default_order:
            column = table.c.get(column_name)
            if column is not None:
                ordered.append(column)
    definition = TableDefinition(
        name=table.name,
        label=label,
        columns=tuple(table.c),
        default_order=tuple(ordered),
    )
    _TABLES[table.name] = definition


_TABLE_DEFINITIONS: Sequence[tuple[type, str, Sequence[str] | None]] = (
    (schema.Subject, "Subjects", ("subject_id",)),
    (schema.SubjectOtherIdentifier, "Subject Other Identifiers", ("subject_id",)),
    (schema.Cohort, "Cohorts", ("cohort_id",)),
    (schema.SubjectCohort, "Subject Cohorts", ("subject_cohort_id",)),
    (schema.IdType, "Identifier Types", ("id_type_id",)),
    (schema.ObservationType, "Observation Types", ("observation_type_id",)),
    (schema.Event, "Events", ("event_id",)),
    (schema.Disease, "Diseases", ("disease_id",)),
    (schema.DiseaseType, "Disease Types", ("disease_type_id",)),
    (schema.SubjectDisease, "Subject Diseases", ("subject_disease_id",)),
    (schema.SubjectDiseaseType, "Subject Disease Types", ("subject_disease_type_id",)),
    (schema.NumericMeasure, "Numeric Measures", ("measure_id",)),
    (schema.TextMeasure, "Text Measures", ("measure_id",)),
    (schema.BooleanMeasure, "Boolean Measures", ("measure_id",)),
    (schema.JsonMeasure, "JSON Measures", ("measure_id",)),
    (schema.Study, "Studies", ("study_id",)),
    (schema.Series, "Series", ("series_id",)),
    (schema.SeriesStack, "Series Stacks", ("series_stack_id",)),
    (schema.StackFingerprint, "Stack Fingerprints", ("fingerprint_id",)),
    (schema.MRISeriesDetails, "MRI Series Details", ("series_id",)),
    (schema.CTSeriesDetails, "CT Series Details", ("series_id",)),
    (schema.PETSeriesDetails, "PET Series Details", ("series_id",)),
    (schema.SeriesClassificationCache, "Series Classification Cache", ("series_stack_id",)),
    (schema.Instance, "Instances", ("instance_id",)),
    (schema.MegAcquisition, "MEG Acquisitions", ("meg_acquisition_id",)),
    (schema.MegChannel, "MEG Channels", ("meg_channel_id",)),
    (schema.IngestConflict, "Ingest Conflicts", ("id",)),
    (schema.SchemaVersion, "Schema Versions", ("id",)),
)

for model, label, order in _TABLE_DEFINITIONS:
    _register(model, label=label, default_order=list(order) if order else None)


def list_tables() -> Iterable[TableDefinition]:
    return _TABLES.values()


def get_table(name: str) -> TableDefinition:
    definition = _TABLES.get(name)
    if definition is None:
        raise KeyError(name)
    return definition
