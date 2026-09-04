"""Discovery scan: reload the mapping CSV, then walk the `mrc`/`natmeg`
vault folders and upsert `facility_discoveries` rows.

Runs synchronously in-request for v1 (no job/queue infra) -- same
"manual only" simplicity as the CSV reload. Never touches
`project`/`cohort`/`subject`/`subject_cohorts`; that happens later, only on
confirm (`facility_discovery.confirm`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .mapping_importer import MappingCsvError, reload_mappings
from .models import FacilityDiscovery, FacilitySubjectMapping, ScanSummaryDTO

logger = logging.getLogger(__name__)


class ScanConfigError(Exception):
    """Raised when required scan configuration (paths) is missing/invalid."""


def _mrc_id_from_folder(name: str) -> str:
    return name[len("sub-"):] if name.startswith("sub-") else name


def _natmeg_id_from_folder(name: str) -> str:
    return name[len("sub-"):] if name.startswith("sub-") else name


def _upsert_discovery(
    session: Session,
    *,
    facility: str,
    facility_id_value: str,
    session_number: Optional[str],
    scan_date: Optional[str],
    cir_id: str,
    cir_project: str,
    folder_path: str,
    mapping_row_id: int,
    summary: ScanSummaryDTO,
) -> None:
    existing = session.scalar(
        select(FacilityDiscovery).where(
            FacilityDiscovery.facility == facility,
            FacilityDiscovery.facility_id_value == facility_id_value,
            FacilityDiscovery.session_number == session_number,
        )
    )
    if existing is None:
        session.add(
            FacilityDiscovery(
                facility=facility,
                facility_id_value=facility_id_value,
                session_number=session_number,
                scan_date=scan_date,
                cir_id=cir_id,
                cir_project=cir_project,
                folder_path=folder_path,
                status="pending",
                mapping_row_id=mapping_row_id,
                discovered_at=datetime.now(timezone.utc),
            )
        )
        summary.matched_new += 1
        return

    if existing.status == "pending":
        # Refresh folder_path/scan_date/cir_id/cir_project/mapping_row_id --
        # the sheet or vault may have changed since the last scan -- but
        # never resurrect/overwrite a confirmed or rejected discovery.
        existing.scan_date = scan_date
        existing.cir_id = cir_id
        existing.cir_project = cir_project
        existing.folder_path = folder_path
        existing.mapping_row_id = mapping_row_id
        summary.matched_already_pending += 1
    elif existing.status == "confirmed":
        summary.matched_already_confirmed += 1
    else:
        summary.matched_already_rejected += 1


def _scan_mrc(session: Session, mrc_root: Path, summary: ScanSummaryDTO) -> None:
    if not mrc_root.exists():
        logger.warning("mrc vault root does not exist, skipping mrc pass: %s", mrc_root)
        return

    for entry in sorted(mrc_root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("sub-"):
            continue
        mrc_id = _mrc_id_from_folder(entry.name)

        rows = list(
            session.scalars(
                select(FacilitySubjectMapping).where(
                    FacilitySubjectMapping.mrc_id == mrc_id,
                )
            )
        )
        if not rows:
            summary.unmatched_folders += 1
            continue

        for row in rows:
            if not row.cir_id or not row.cir_project:
                summary.unmatched_folders += 1
                continue
            _upsert_discovery(
                session,
                facility="mrc",
                facility_id_value=mrc_id,
                session_number=row.session_number,
                scan_date=row.scan_date,
                cir_id=row.cir_id,
                cir_project=row.cir_project,
                folder_path=str(entry),
                mapping_row_id=row.id,
                summary=summary,
            )


def _scan_natmeg(session: Session, natmeg_root: Path, summary: ScanSummaryDTO) -> None:
    if not natmeg_root.exists():
        logger.warning("natmeg vault root does not exist, skipping natmeg pass: %s", natmeg_root)
        return

    # <natmeg_root>/<project>/raw/sub-<id>/<date> is the observed real layout
    # (confirmed against actual vault data), but some deployments may insert
    # an extra <acquisition> directory between `raw` and `sub-<id>`
    # (<natmeg_root>/<project>/raw/<acquisition>/sub-<id>/<date>). `**`
    # matches zero-or-more intermediate directories, so this glob finds
    # `sub-*` dirs under either layout without having to guess which one a
    # given vault uses.
    for sub_dir in sorted(natmeg_root.glob("*/raw/**/sub-*")):
        if not sub_dir.is_dir():
            continue
        natmeg_id = _natmeg_id_from_folder(sub_dir.name)

        for date_dir in sorted(sub_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            scan_date = date_dir.name

            # Primary: exact (natmeg_id, scan_date) match.
            exact_rows = list(
                session.scalars(
                    select(FacilitySubjectMapping).where(
                        FacilitySubjectMapping.natmeg_id == natmeg_id,
                        FacilitySubjectMapping.scan_date == scan_date,
                    )
                )
            )
            rows = exact_rows
            if not rows:
                # Fallback: natmeg_id-only match, only when unambiguous
                # (exactly one candidate row) -- logged as a lower-confidence
                # match.
                id_only_rows = list(
                    session.scalars(
                        select(FacilitySubjectMapping).where(
                            FacilitySubjectMapping.natmeg_id == natmeg_id,
                        )
                    )
                )
                if len(id_only_rows) == 1:
                    logger.info(
                        "natmeg discovery: no exact scan_date match for natmeg_id=%s date=%s; "
                        "falling back to the single natmeg_id match (lower confidence)",
                        natmeg_id, scan_date,
                    )
                    rows = id_only_rows

            if not rows:
                summary.unmatched_folders += 1
                continue

            for row in rows:
                if not row.cir_id or not row.cir_project:
                    summary.unmatched_folders += 1
                    continue
                _upsert_discovery(
                    session,
                    facility="natmeg",
                    facility_id_value=natmeg_id,
                    session_number=row.session_number,
                    scan_date=scan_date,
                    cir_id=row.cir_id,
                    cir_project=row.cir_project,
                    folder_path=str(date_dir),
                    mapping_row_id=row.id,
                    summary=summary,
                )


def run_discovery_scan(
    session: Session,
    *,
    mapping_csv_path: Path,
    vault_root: Path,
) -> ScanSummaryDTO:
    """Reload the mapping CSV, then run the `mrc` and `natmeg` scan passes.

    Raises `MappingCsvError` if the CSV is missing/unreadable/malformed
    (transactional -- caller's transaction is expected to be rolled back,
    leaving `facility_subject_mappings`/`facility_discoveries` untouched).
    """
    summary = ScanSummaryDTO()
    summary.mappings_loaded = reload_mappings(session, mapping_csv_path)

    _scan_mrc(session, vault_root / "mrc", summary)
    _scan_natmeg(session, vault_root / "natmeg", summary)

    session.flush()
    return summary
