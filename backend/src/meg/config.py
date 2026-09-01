"""Configuration models for the MEG parallel processing track.

Phase 1 covers three stages: `meg_ingest`, `meg_scan`, and `meg_bids`. These
models validate the per-stage config that is stored (camelCase, via each
stage's pipeline-step `config` JSON, see
`nils_dataset_pipeline.ordering.get_default_stage_config`) and later built
into one of these snake_case models by the stage's API route handler, the
same way `extract.config.ExtractionConfig` is built from `merged_config` in
`api/routes/cohorts.py::_run_extract_stage`.

`meg_maxfilter` and `meg_qc` are deferred follow-up stages (see the MEG
parallel track plan) and have no config models yet.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MegBidsOverwriteMode(str, Enum):
    SKIP = "skip"
    OVERWRITE = "overwrite"


class MegIngestConfig(BaseModel):
    """Configuration for the `meg_ingest` stage.

    Discovers and stages raw FIF files (and split sets) into the cohort
    workspace, optionally copying calibration/crosstalk files alongside them.
    """

    source_path: str = Field(
        default="",
        description="Root directory to scan for raw FIF recordings (defaults to the cohort source path).",
    )
    copy_calibration_files: bool = Field(
        default=True,
        description="Copy MaxFilter calibration (sss_cal) files when present alongside source recordings.",
    )
    copy_crosstalk_files: bool = Field(
        default=True,
        description="Copy MaxFilter crosstalk (ct_sparse) files when present alongside source recordings.",
    )
    preserve_split_files: bool = Field(
        default=True,
        description="Keep multi-part split FIF sets (e.g. _raw.fif, _raw-1.fif, ...) together during staging.",
    )
    copy_workers: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Number of concurrent file-copy workers.",
    )


class MegSubjectResolutionConfig(BaseModel):
    """Subject identity resolution shared by MEG stages that need it.

    Mirrors the id-type-then-CSV-fallback resolution strategy already used
    by DICOM extraction (see `extract.config.ExtractionConfig.subject_id_type_id`):
    primary resolution is an `id_types`-scoped lookup against
    `subject_other_identifiers` via `subject_id_type_id`; the fallback is an
    uploaded CSV mapping (recording/session label -> subject_code).
    """

    subject_id_type_id: Optional[int] = Field(
        default=None,
        description="id_types.id_type_id used to resolve subject identity via subject_other_identifiers. "
        "None falls back to the CSV mapping (or the raw FIF subject code) below.",
    )
    subject_csv_mapping_path: Optional[str] = Field(
        default=None,
        description="Path to an uploaded CSV mapping recording/session identifiers to subject_code, "
        "used when subject_id_type_id lookup does not resolve a subject.",
    )


class MegScanConfig(MegSubjectResolutionConfig):
    """Configuration for the `meg_scan` stage.

    Reads FIF headers only (`mne.io.read_info()`), resolves subject identity,
    upserts one `study` row per logical MEG session, and (re)generates the
    conversion table consumed by `meg_bids`.
    """

    naming_convention: str = Field(
        default="natmeg",
        description="Filename/task/run/acquisition parsing convention to apply (see vendored cir-utils parsers).",
    )
    require_calibration_files: bool = Field(
        default=False,
        description="Fail scan (route recording to ingest_conflicts) if calibration/crosstalk files are missing.",
    )


class MegBidsConfig(BaseModel):
    """Configuration for the `meg_bids` stage.

    Converts staged FIF recordings into a BIDS dataset via `mne-bids`,
    reading conversion-table rows produced by `meg_scan`.
    """

    bids_root_name: str = Field(
        default="bids-meg",
        description="Name of the BIDS output directory, created under the cohort workspace.",
    )
    overwrite_mode: MegBidsOverwriteMode = Field(
        default=MegBidsOverwriteMode.SKIP,
        description="Whether to skip or overwrite existing BIDS output for a recording that was already converted.",
    )
    dataset_description_name: str = Field(
        default="",
        description="Name written into dataset_description.json (defaults to the cohort name).",
    )
    convert_workers: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Number of concurrent mne-bids conversion workers.",
    )
