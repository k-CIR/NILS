"""SQLAlchemy models and DTOs for cohort tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    Float,
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from nils_dataset_pipeline.models import NilsDatasetPipelineStep


class Base(DeclarativeBase):
    pass


class Cohort(Base):
    __tablename__ = "cohorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    anonymization_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Cohort data modality/type: "imaging" (DICOM MR/CT/PET, current default path)
    # or "meg" (MEG parallel track). Fixed at creation time; determines which
    # pipeline stage list is initialized for the cohort. See
    # nils_dataset_pipeline/ordering.py for the modality-aware stage lists.
    modality: Mapped[str] = mapped_column(String(20), default='imaging', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(50), default='idle', nullable=False)
    total_subjects: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_series: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Pipeline state is stored in nils_dataset_pipeline_steps table
    pipeline_steps: Mapped[list["NilsDatasetPipelineStep"]] = relationship(
        "NilsDatasetPipelineStep",
        back_populates="cohort",
        cascade="all, delete-orphan",
        order_by="NilsDatasetPipelineStep.sort_order",
    )


class CohortDTO(BaseModel):
    """Data transfer object for cohort API responses."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    source_path: str
    description: Optional[str] = None
    tags: list[str] = []
    anonymization_enabled: bool = False
    modality: str = 'imaging'
    created_at: datetime
    updated_at: datetime
    status: str = 'idle'
    total_subjects: int = 0
    total_sessions: int = 0
    total_series: int = 0
    completion_percentage: int = 0
    # stages is populated from pipeline_steps by the service layer
    stages: list[dict] = []


class CreateCohortPayload(BaseModel):
    """Payload for creating a new cohort."""
    name: str
    source_path: str
    description: Optional[str] = None
    tags: list[str] = []
    anonymization_enabled: bool = False
    anonymize_config: Optional[dict] = None
    # "imaging" (default, current DICOM MR/CT/PET path) or "meg". Only applied
    # when creating a brand-new cohort; ignored on update of an existing
    # cohort since changing modality after pipeline initialization would
    # invalidate already-created pipeline steps.
    modality: str = 'imaging'


# =============================================================================
# Per-cohort classification keyword overrides
# =============================================================================


class CohortClassificationOverride(Base):
    """Per-cohort keyword deltas applied on top of global detection YAML.

    A row exists ONLY for buckets the user has edited. Empty table => cohort
    behaves identically to the global defaults. This keeps the merge cheap
    and lets global YAML improvements flow through automatically.

    The merge contract is:
        effective(bucket) = (global(bucket) + added, dedup preserving order) \\ removed
    """

    __tablename__ = "cohort_classification_overrides"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id", "axis", "bucket_path",
            name="uq_cohort_override_bucket",
        ),
        Index("ix_cohort_overrides_cohort", "cohort_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cohort_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Axis identifier: "base" | "construct" | "contrast" | "modifier" |
    # "technique" | "acceleration" | "provenance" | "body_part"
    axis: Mapped[str] = mapped_column(String(32), nullable=False)
    # Dotted path into the YAML doc, e.g. "bases.T1w.keywords" or
    # "negative_keywords" (top-level lists use a bare key).
    bucket_path: Mapped[str] = mapped_column(String(256), nullable=False)
    # User-added keywords (appended to defaults).
    added: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # User-removed defaults (subtracted from the union).
    removed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class BucketOverrideDTO(BaseModel):
    """Raw stored override for one bucket."""
    model_config = ConfigDict(from_attributes=True)

    axis: str
    bucket_path: str
    added: list[str] = []
    removed: list[str] = []
    updated_at: Optional[datetime] = None


class BucketViewDTO(BaseModel):
    """Frontend-facing view of a single editable bucket."""
    axis: str
    bucket_path: str
    display_name: str
    group_label: Optional[str] = None
    description: Optional[str] = None
    defaults: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    effective: list[str] = []


class AxisViewDTO(BaseModel):
    """Group of buckets for a single classification axis."""
    axis: str
    label: str
    description: Optional[str] = None
    buckets: list[BucketViewDTO] = []


class CohortKeywordConfigDTO(BaseModel):
    """Full keyword configuration for a cohort (defaults + overrides merged)."""
    cohort_id: int
    axes: list[AxisViewDTO] = []


class BucketUpdatePayload(BaseModel):
    """Payload for PUT bucket override."""
    axis: str
    bucket_path: str
    added: list[str] = []
    removed: list[str] = []


# =============================================================================
# Per-cohort Main Acquisition QC state
# =============================================================================


class CohortMainQCAck(Base):
    """Per-(cohort, subject, session_date, axis) acknowledgement of a Main QC pick.

    Independent of ``cohort_main_qc_state.current_picks`` so that
    acknowledgements survive a cohort-wide ``Apply`` (which rewrites
    ``current_picks`` from scratch). The picks JSON is the algorithm's
    output; this table is the user's "I reviewed it and the algo was
    right" assertion.

    Sessions are keyed by ``(subject_id, study_date)`` because PACS often
    splits a single visit into multiple ``StudyInstanceUID``s (e.g. brain
    study + spine study); those should appear as one session in the QC
    heatmap.

    ``Reset to auto`` for a single (subject, date, axis) clears the row.
    """

    __tablename__ = "cohort_main_qc_ack"

    cohort_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[str] = mapped_column(String(32), primary_key=True)
    axis: Mapped[str] = mapped_column(String(16), primary_key=True)  # "t1w" | "flair"
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class CohortMainQCState(Base):
    """Per-cohort snapshot of the cohort-wide Main Acquisition QC.

    Stores the **current** result and the most recent **previous** result
    (single-level undo). The "picks" jsonb is dense enough to fully reconstruct
    the heatmap and to replay token writes on Restore Previous, without
    re-running the algorithm.
    """

    __tablename__ = "cohort_main_qc_state"

    cohort_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # --- Current snapshot ---
    current_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    current_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_picks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # --- Previous snapshot (nullable until 2nd Apply) ---
    previous_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    previous_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    previous_picks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    previous_profile: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class MainQCSummaryDTO(BaseModel):
    """Per-axis summary counts."""
    green: int = 0
    amber: int = 0
    red: int = 0
    needs_check: int = 0
    total_sessions: int = 0


class MainQCCandidateDTO(BaseModel):
    """One candidate stack within a session's auto-pick."""
    stack_id: int
    score: float
    technique: Optional[str] = None
    modifier_csv: Optional[str] = None
    construct_csv: Optional[str] = None
    dim: Optional[str] = None  # "2D" | "3D" | None
    slices: Optional[int] = None
    fov_x_mm: Optional[float] = None
    post_contrast: Optional[int] = None
    provenance: Optional[str] = None
    is_main: bool = False
    in_winning_family: bool = False
    is_canonical_in_family: bool = False
    # Image addressing for the cohort Main QC modal preview. Optional so old
    # snapshots persisted before this feature continue to validate.
    series_instance_uid: Optional[str] = None
    stack_index: int = 0
    # Display fields for the modal tile (also persisted so old snapshots remain
    # readable; they will be back-filled on the next Apply / restore_previous).
    orientation: Optional[str] = None    # "Axial" | "Coronal" | "Sagittal" | None
    base: Optional[str] = None           # e.g. "T1w" | "T2w_FLAIR" | None
    fov_y_mm: Optional[float] = None
    pixsp_row_mm: Optional[float] = None
    pixsp_col_mm: Optional[float] = None
    slice_thickness_mm: Optional[float] = None  # uses spacing_between_slices when present


class MainQCSessionContentDTO(BaseModel):
    """Compact content descriptor for legend filtering — derived from the
    winning bundle only (one row per pick)."""
    technique: Optional[str] = None       # e.g. "MPRAGE"
    dim: Optional[str] = None             # "2D" | "3D" | None
    family: Optional[str] = None          # "dixon" | "waterexc" | "plain"
    slice_bucket: Optional[str] = None    # "hi" | "std" | "lo"
    slices: Optional[int] = None          # winning bundle's slices_count


class MainQCSessionPickDTO(BaseModel):
    """One session's auto-pick result for one axis.

    A session is identified by ``(subject_id, session_date)`` — PACS often
    splits a single calendar visit into multiple ``StudyInstanceUID``s
    (e.g. brain study + spine study), so a session can span multiple
    ``study_id``s. ``study_ids`` lists them all; ``primary_study_id`` is
    the study that owns the winning stack (or the lowest study_id when
    no stack wins) and is used for the modal preview / single-study
    fallbacks.
    """
    # ``(subject_id, session_date)`` is the canonical session key.
    subject_id: int
    session_date: Optional[str] = None
    subject_code: str
    axis: str  # "t1w" | "flair"
    # All studies under this session, in ascending order.
    study_ids: list[int] = []
    # Primary study for image previews and any path that needs a single canonical
    # StudyInstanceUID (modal preview, error messages, etc.).
    primary_study_id: int = 0
    winning_stack_ids: list[int] = []
    score: Optional[float] = None
    needs_check: bool = False
    needs_check_reasons: list[str] = []
    candidate_summary: list[MainQCCandidateDTO] = []
    # Compact descriptor for legend chip filtering (None when no eligible pick).
    content: Optional[MainQCSessionContentDTO] = None
    # Map of id_type_name → identifier value for this subject.
    subject_other_ids: dict[str, str] = {}
    # User has explicitly reviewed this pick and confirmed it ("In NILS we
    # trust"). Stored independently in cohort_main_qc_ack so it survives a
    # cohort Apply that rewrites current_picks. Cleared by Reset to auto.
    acknowledged: bool = False


class MainQCStateDTO(BaseModel):
    """Top-level result for the GET endpoint."""
    cohort_id: int
    has_current: bool = False
    has_previous: bool = False
    current_run_at: Optional[datetime] = None
    previous_run_at: Optional[datetime] = None
    summary: dict[str, MainQCSummaryDTO] = {}  # {"t1w": ..., "flair": ...}
    picks: list[MainQCSessionPickDTO] = []
    profile: dict = {}
    # Identifier types available for this cohort, e.g. ["code", "PID", "MRN"].
    # Always starts with "code" (the canonical subject_code).
    available_id_types: list[str] = ["code"]


class MainQCSessionPickPayload(BaseModel):
    """Payload for POST /main-qc/session-pick.

    Sessions are identified by ``(subject_id, session_date)`` rather than
    ``study_id`` because a single calendar visit can span multiple studies
    (brain + spine, etc.).
    """
    subject_id: int
    session_date: str
    axis: str
    stack_ids: list[int] = []
    note: Optional[str] = None


class MainQCSessionResetPayload(BaseModel):
    """Payload for POST /main-qc/session-reset."""
    subject_id: int
    session_date: str
    axis: str


class MainQCSessionAcknowledgePayload(BaseModel):
    """Payload for POST /main-qc/session-acknowledge."""
    subject_id: int
    session_date: str
    axis: str


# =============================================================================
# Per-cohort Body Part QC state (V1)
# =============================================================================


class CohortBodyPartQCState(Base):
    """Per-cohort snapshot of cohort-wide Body Part QC.

    Holds:
      - User-defined categories (default: Brain, Brain-Neck, Spine, Chest)
      - The approved labeled training set (≤1 slice per stack)
      - Trained classifier metadata (artifact saved on disk)
      - Current/previous prediction snapshots for single-level undo
      - Pre-QC `body_part` per stack captured at Apply time (diff fields in
        ``current_picks`` JSON), so the UI can show a clear diff between QC
        predictions and the prior (keyword-derived or empty) label.

    Mirrors :class:`CohortMainQCState`.
    """

    __tablename__ = "cohort_body_part_qc_state"

    cohort_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # --- Setup ---
    categories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # training_samples = [
    #   {"stack_id": int, "slice_index": int, "label": str,
    #    "orientation": "axial|sagittal|coronal", "approved_at": iso}
    # ]
    training_samples: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- Trained classifier (artifact on disk; meta in JSON) ---
    # Legacy per-cohort classifier. When ``selected_model_id`` is set,
    # the service reads from the ``body_part_model`` registry instead.
    classifier_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    classifier_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    classifier_meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # --- Global model registry reference ---
    selected_model_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("body_part_model.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- Current snapshot ---
    current_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    current_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # current_picks = [
    #   {"study_id": int, "subject_id": int, "subject_code": str,
    #    "session_date": str|None,
    #    "stacks": [
    #       {"stack_id": int, "label": str, "confidence": float,
    #        "probs": {label: float}, "is_override": bool, "needs_check": bool,
    #        "previous_label": str|None, "prior_source": str|None, "changed": bool,
    #        "series_instance_uid": str, "technique": str|None,
    #        "orientation": str|None}, ...
    #    ],
    #    "session_combo": [str], "session_combo_key": str,
    #    "session_prev_combo_key": str, "session_changed": bool,
    #    "stacks_changed": int, "low_conf_count": int, "needs_check": bool}
    # ]
    current_picks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # --- Previous snapshot (single-level undo) ---
    previous_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    previous_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    previous_picks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    previous_profile: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # --- Stage / commit gate (Milestone C) ---
    # ``stage_status`` tracks whether ``current_picks`` has been written
    # to the metadata DB yet. Apply now stages without writing; user
    # explicitly Commits to push labels to ``series_classification_cache``.
    #
    # Values:
    #   none       — never applied; nothing to commit.
    #   staged     — current_picks is fresh; metadata DB is stale.
    #   committed  — current_picks fully reflected in metadata DB.
    #   dirty      — metadata DB matches a prior snapshot but the user
    #                edited (override / reset) since; recommit needed.
    stage_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="none", server_default="none",
    )
    last_committed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # SHA-256 of the (stack_id, label, is_override) tuples at last commit.
    # Used to detect drift between current_picks and what's in the metadata
    # DB without scanning the metadata DB every time.
    last_commit_signature: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
    )


class StackEmbeddingCache(Base):
    """Cache of frozen-encoder embeddings for stacks/slices.

    Keyed by (series_stack_id, slice_index, encoder_name, encoder_version).
    Embedding bytes are little-endian float32 (np.float32.tobytes()).
    Lives in the application DB because embeddings are derived QC artifacts,
    not metadata extracted from DICOMs.
    """

    __tablename__ = "stack_embedding_cache"
    __table_args__ = (
        Index("ix_stack_embedding_cache_stack", "series_stack_id"),
    )

    series_stack_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slice_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    encoder_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    encoder_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# Global Body Part QC — Sample Pool + Model Registry
# ---------------------------------------------------------------------------


class BodyPartGlobalSample(Base):
    """Global pool of labeled samples contributed from any cohort.

    Training reads from this pool (with optional label remapping) rather
    than from per-cohort ``training_samples``. Provenance is tracked via
    ``source_cohort_name``.
    """

    __tablename__ = "body_part_global_sample"
    __table_args__ = (
        UniqueConstraint(
            "stack_id", "slice_index",
            name="uq_global_sample_stack_slice",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stack_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slice_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    orientation: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown",
    )
    source_cohort_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contributed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class BodyPartModel(Base):
    """Registry of trained body-part classifiers.

    Each model is a .joblib artifact trained from the global sample pool
    (optionally with label remapping). Cohorts select a model by FK;
    one model can be marked ``is_default``.
    """

    __tablename__ = "body_part_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The output class labels this model predicts.
    classes: Mapped[list] = mapped_column(JSON, nullable=False)
    # Mapping applied at train time, e.g. {"Brain-Neck": "Brain"}.
    label_remap: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Full classifier_meta (encoder_chain, pca, C, tune_report, etc.)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ---------------------------------------------------------------------------
# Pydantic DTOs — Body Part QC
# ---------------------------------------------------------------------------


class BodyPartCandidateDTO(BaseModel):
    """One zero-shot candidate slice for the seeding UI."""
    stack_id: int
    slice_index: int
    orientation: str  # "axial" | "sagittal" | "coronal"
    zs_prob: float
    margin: float
    top2_label: Optional[str] = None
    series_instance_uid: str
    study_id: int
    subject_code: str
    session_date: Optional[str] = None
    thumbnail_url: str
    source: str = "zero_shot"  # "keyword_prior" | "zero_shot"
    keyword_label: Optional[str] = None  # underlying scc.body_part value


class BodyPartTrainingSampleDTO(BaseModel):
    """One approved labeled sample in the training set."""
    stack_id: int
    slice_index: int
    label: str
    orientation: str
    approved_at: datetime
    # Optional: populated by ``GET .../body-part-qc/samples`` so the
    # frontend can render a grid without any extra round-trip. The
    # in-memory ``training_samples`` JSON does NOT persist this field.
    thumbnail_url: Optional[str] = None
    subject_code: Optional[str] = None


class BodyPartStackPickDTO(BaseModel):
    """One stack's QC prediction within a session, including diff fields."""
    stack_id: int
    label: Optional[str] = None
    confidence: Optional[float] = None
    probs: dict[str, float] = {}
    is_override: bool = False
    needs_check: bool = False
    series_instance_uid: Optional[str] = None
    technique: Optional[str] = None
    orientation: Optional[str] = None
    # --- diff fields (captured at Apply time) ---
    previous_label: Optional[str] = None
    prior_source: Optional[str] = None  # 'text_keyword' | 'qc_v1' | 'manual' | None
    changed: bool = False
    # --- override conflict (Milestone B) ---
    # Set when an existing override survived re-Apply but the new model
    # strongly disagrees. UI shows a "conflict" badge + an "Accept new"
    # mini-button. ``None`` when there is no conflict.
    override_conflict: Optional[dict] = None  # {"label": str, "prob": float}


class BodyPartSessionPickDTO(BaseModel):
    """One session's QC predictions, with session-level rollups + diff.

    Sessions are keyed by ``(subject_id, session_date)``. ``study_ids`` lists
    every ``StudyInstanceUID`` that contributes stacks to this session;
    ``primary_study_id`` is one canonical study (the one owning the most
    stacks, or ``min(study_ids)``) used for any path that needs a single
    StudyInstanceUID (e.g. opening the per-session viewer).
    """
    subject_id: int
    session_date: Optional[str] = None
    subject_code: str
    study_ids: list[int] = []
    primary_study_id: int = 0
    stacks: list[BodyPartStackPickDTO] = []
    session_combo: list[str] = []
    session_combo_key: str = ""
    # --- session-level diff ---
    session_prev_combo_key: str = ""
    session_changed: bool = False
    stacks_changed: int = 0
    low_conf_count: int = 0
    needs_check: bool = False
    subject_other_ids: dict[str, str] = {}


class BodyPartSummaryDTO(BaseModel):
    """Cohort-level rollup, including diff aggregates."""
    total_sessions: int = 0
    by_combo: dict[str, int] = {}  # "Brain" -> 250, "Brain+Spine" -> 14
    needs_check: int = 0
    # --- diff aggregates ---
    total_stacks: int = 0
    stacks_changed: int = 0
    sessions_changed: int = 0
    # change_matrix[prior_label or "(none)"][new_label] = count
    change_matrix: dict[str, dict[str, int]] = {}
    # Stacks where a prior override survived re-Apply but the new model
    # strongly disagrees (Milestone B).
    override_conflicts_count: int = 0


class BodyPartStateDTO(BaseModel):
    """Top-level state for ``GET /api/cohorts/{id}/body-part-qc``."""
    cohort_id: int
    has_current: bool = False
    has_previous: bool = False
    current_run_at: Optional[datetime] = None
    previous_run_at: Optional[datetime] = None
    categories: list[str] = []
    # training_summary[category] = {"axial": N, "sagittal": N, "coronal": N, "total": N}
    training_summary: dict[str, dict[str, int]] = {}
    classifier_meta: Optional[dict] = None
    summary: BodyPartSummaryDTO = BodyPartSummaryDTO()
    picks: list[BodyPartSessionPickDTO] = []
    profile: dict = {}
    available_id_types: list[str] = ["code"]
    # --- Milestone C: stage / commit gate ---
    # ``stage_status``:
    #   none       — no Apply on record.
    #   staged     — Apply ran; metadata DB still reflects the prior commit
    #                (or never written). Pending review/commit.
    #   committed  — current_picks fully written to series_classification_cache.
    #   dirty      — metadata DB matches a prior commit but the user has
    #                edited (override / reset) since; recommit needed.
    stage_status: str = "none"
    last_committed_at: Optional[datetime] = None
    pending_changes_count: int = 0  # stacks where committed != current
    # --- Global model registry ---
    selected_model_id: Optional[int] = None
    selected_model_name: Optional[str] = None


class BodyPartChangeRowDTO(BaseModel):
    """One row in the Changes pane.

    ``study_id`` here is the actual ``StudyInstanceUID`` that owns the stack
    (used for traceability + image paths). The session this row belongs to
    is identified by ``(subject_id, session_date)``.
    """
    study_id: int
    subject_id: int
    stack_id: int
    subject_code: str
    session_date: Optional[str] = None
    series_description: Optional[str] = None
    technique: Optional[str] = None
    orientation: Optional[str] = None
    previous_label: Optional[str] = None
    new_label: str
    prior_source: Optional[str] = None
    confidence: float
    needs_check: bool
    is_override: bool
    thumbnail_url: str
    middle_slice_url: Optional[str] = None
    # Milestone B — populated when an override survived re-Apply but the
    # new model strongly disagrees. Frontend renders a "conflict" badge.
    override_conflict: Optional[dict] = None  # {"label": str, "prob": float}


class BodyPartChangesPageDTO(BaseModel):
    """Paginated response for ``GET /api/cohorts/{id}/body-part-qc/changes``."""
    total: int
    offset: int
    limit: int
    rows: list[BodyPartChangeRowDTO] = []


# ---------------------------------------------------------------------------
# Body Part QC — Request payloads
# ---------------------------------------------------------------------------


class BodyPartCategoriesPayload(BaseModel):
    """Payload for ``PUT .../body-part-qc/categories``."""
    categories: list[str]


class BodyPartSeedPayload(BaseModel):
    """Payload for ``POST .../body-part-qc/seed``."""
    category: str
    n_target: int = 100
    n_keyword_prior: Optional[int] = None  # default min(50, n_target//2)
    n_zero_shot: Optional[int] = None      # default n_target - n_keyword_prior


class BodyPartSampleOp(BaseModel):
    """One operation on the training set."""
    op: str  # "approve" | "remove" | "replace" | "move"
    stack_id: int
    slice_index: int
    label: Optional[str] = None       # for approve/move (current label)
    new_label: Optional[str] = None   # for move (target label)


class BodyPartSamplesPayload(BaseModel):
    """Payload for ``POST .../body-part-qc/samples``."""
    ops: list[BodyPartSampleOp]


class BodyPartOverridePayload(BaseModel):
    """Payload for ``POST .../body-part-qc/session-override``.

    Sessions are identified by ``(subject_id, session_date)``. ``stack_id``
    pinpoints the specific stack within that session to override.
    """
    subject_id: int
    session_date: str
    stack_id: int
    label: str
    note: Optional[str] = None


class BodyPartSessionResetPayload(BaseModel):
    """Payload for ``POST .../body-part-qc/session-reset``."""
    subject_id: int
    session_date: str


class BodyPartCommitPayload(BaseModel):
    """Payload for ``POST .../body-part-qc/commit``.

    All fields optional — empty body triggers full commit (back-compat).
    Filters are combined with AND when multiple are provided.
    ``stack_ids`` takes precedence over all other filters.
    """
    stack_ids: Optional[list[int]] = None
    min_confidence: Optional[float] = None
    from_label: Optional[str] = None
    to_label: Optional[str] = None


class BodyPartDestagePayload(BaseModel):
    """Payload for ``POST .../body-part-qc/destage``."""
    stack_ids: list[int]
