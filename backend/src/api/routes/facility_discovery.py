"""Facility vault discovery API routes.

- `POST /api/facility-discovery/scan`: manual trigger, full CSV reload +
  directory scan (task 4).
- `GET /api/facility-discovery`: list pending/confirmed/rejected discoveries
  (task 5).
- `POST /api/facility-discovery/{id}/confirm`: linkage-creation path (task 5).
- `POST /api/facility-discovery/{id}/reject`: mark rejected, no side effects.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from db.session import session_scope
from facility_discovery.config import get_settings
from facility_discovery.confirm import ConfirmError, confirm_discovery, reject_discovery
from facility_discovery.mapping_importer import MappingCsvError
from facility_discovery.models import (
    DISCOVERY_STATUSES,
    FACILITIES,
    FacilityDiscovery,
    FacilityDiscoveryDTO,
    ScanSummaryDTO,
)
from facility_discovery.scanner import run_discovery_scan

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/facility-discovery", tags=["facility-discovery"])


@router.post("/scan", response_model=ScanSummaryDTO)
def scan_endpoint() -> ScanSummaryDTO:
    """Manually trigger a discovery scan: full mapping-CSV reload + vault walk."""
    settings = get_settings()
    if settings.mapping_csv_path is None:
        raise HTTPException(status_code=400, detail="FACILITY_MAPPING_CSV_PATH is not configured")
    if settings.vault_root is None:
        raise HTTPException(status_code=400, detail="FACILITY_VAULT_ROOT is not configured")
    if not settings.mapping_csv_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Mapping CSV not found: {settings.mapping_csv_path}"
        )

    try:
        with session_scope() as session:
            summary = run_discovery_scan(
                session,
                mapping_csv_path=settings.mapping_csv_path,
                vault_root=settings.vault_root,
            )
    except MappingCsvError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return summary


@router.get("", response_model=list[FacilityDiscoveryDTO])
def list_discoveries_endpoint(
    status: Optional[str] = Query(default=None),
    facility: Optional[str] = Query(default=None),
) -> list[FacilityDiscoveryDTO]:
    """List discoveries, optionally filtered by `status`/`facility`. Defaults
    to all statuses (frontend defaults its own view to `pending`)."""
    if status is not None and status not in DISCOVERY_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
    if facility is not None and facility not in FACILITIES:
        raise HTTPException(status_code=400, detail=f"Unknown facility: {facility}")

    with session_scope() as session:
        stmt = select(FacilityDiscovery).order_by(FacilityDiscovery.discovered_at.desc())
        if status is not None:
            stmt = stmt.where(FacilityDiscovery.status == status)
        if facility is not None:
            stmt = stmt.where(FacilityDiscovery.facility == facility)
        rows = list(session.scalars(stmt))
        return [FacilityDiscoveryDTO.model_validate(row) for row in rows]


@router.post("/{discovery_id}/confirm", response_model=FacilityDiscoveryDTO)
def confirm_endpoint(discovery_id: int) -> FacilityDiscoveryDTO:
    try:
        result = confirm_discovery(discovery_id)
    except ConfirmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FacilityDiscoveryDTO.model_validate(result.discovery)


@router.post("/{discovery_id}/reject", response_model=FacilityDiscoveryDTO)
def reject_endpoint(discovery_id: int) -> FacilityDiscoveryDTO:
    try:
        discovery = reject_discovery(discovery_id)
    except ConfirmError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FacilityDiscoveryDTO.model_validate(discovery)
