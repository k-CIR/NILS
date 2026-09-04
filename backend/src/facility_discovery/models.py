"""SQLAlchemy models + DTOs for facility-vault discovery.

Both tables register on the shared app-DB `Base` (`cohorts.models.Base`) --
see the facility-vault-discovery plan's DB Placement section. `cohort_id`
on `FacilityDiscovery` is a real FK (same physical DB as `cohorts`);
`subject_id` is a plain integer with **no** declared FK, following the
exact `metadata_db.schema.IngestConflict.cohort_id` convention, since
`subject` lives in the separate metadata-DB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from cohorts.models import Base

# Facility identifiers recognized by discovery. `bmic` (a third,
# PET/radioligand facility) is explicitly out of scope for discovery/scan
# logic, but its mapping-CSV columns are still stored (read but ignored).
FACILITIES = ("mrc", "natmeg")

DISCOVERY_STATUSES = ("pending", "confirmed", "rejected")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FacilitySubjectMapping(Base):
    """One imported row of the facility-maintained cross-facility mapping CSV.

    Columns mirror the CSV 1:1. Fully reloaded (delete+reinsert) on every
    discovery scan -- see `mapping_importer.reload_mappings()`. Importing
    does not create/resolve `project` rows; that happens later during the
    scan/confirm steps so import stays a pure, side-effect-free reload of
    the raw sheet.

    `bmic_id`/`bmic_radioligande_factor`/`sub_id` are stored as-is for
    traceability but never read by discovery matching logic.
    """

    __tablename__ = "facility_subject_mappings"
    __table_args__ = (
        Index("ix_facility_subject_mappings_mrc_id", "mrc_id"),
        Index("ix_facility_subject_mappings_natmeg_id", "natmeg_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cir_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cir_project: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    session_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    scan_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cir_facility: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    natmeg_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    mrc_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    bmic_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    bmic_radioligande_factor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tester_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tester_kiid: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    persnr_check: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sub_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    export_time: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class FacilityDiscovery(Base):
    """Human-review queue row for one discovered facility subject/session.

    Natural key (enforced at the application layer, mirrored here as a DB
    unique constraint for defense-in-depth): `(facility, facility_id_value,
    session_number)`. Re-running the scan updates `folder_path`/`scan_date`
    on the existing `pending` row for that key instead of inserting a
    duplicate; `confirmed`/`rejected` rows are left untouched by re-scans.
    """

    __tablename__ = "facility_discoveries"
    __table_args__ = (
        UniqueConstraint(
            "facility", "facility_id_value", "session_number",
            name="uq_facility_discovery_natural_key",
        ),
        Index("ix_facility_discoveries_status", "status"),
        Index("ix_facility_discoveries_facility", "facility"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "mrc" | "natmeg"
    facility: Mapped[str] = mapped_column(String(20), nullable=False)
    # literal mrc_id / natmeg_id folder-derived value from the mapping CSV.
    facility_id_value: Mapped[str] = mapped_column(String(200), nullable=False)
    session_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    scan_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cir_id: Mapped[str] = mapped_column(String(200), nullable=False)
    cir_project: Mapped[str] = mapped_column(String(200), nullable=False)
    # Absolute path of the discovered sub-<id> folder (mrc) or <date> session
    # directory (natmeg).
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    # "pending" | "confirmed" | "rejected"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    cohort_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("cohorts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # metadata-DB subject.subject_id -- plain integer, NO FK (cross-database
    # reference; see IngestConflict.cohort_id for the established
    # precedent). Populated only after confirm creates/finds the subject row.
    subject_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mapping_row_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("facility_subject_mappings.id", ondelete="SET NULL"),
        nullable=True,
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


class FacilityDiscoveryDTO(BaseModel):
    """API-facing DTO for one discovery row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    facility: str
    facility_id_value: str
    session_number: Optional[str] = None
    scan_date: Optional[str] = None
    cir_id: str
    cir_project: str
    folder_path: str
    status: str
    cohort_id: Optional[int] = None
    subject_id: Optional[int] = None
    mapping_row_id: Optional[int] = None
    discovered_at: datetime
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class ScanSummaryDTO(BaseModel):
    """Summary counters returned by `POST /api/facility-discovery/scan`."""

    mappings_loaded: int = 0
    matched_new: int = 0
    matched_already_pending: int = 0
    matched_already_confirmed: int = 0
    matched_already_rejected: int = 0
    unmatched_folders: int = 0
