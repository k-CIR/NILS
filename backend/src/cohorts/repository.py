"""Data access helpers for cohort persistence."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Cohort


def create_cohort(
    session: Session,
    *,
    name: str,
    source_path: str,
    description: Optional[str] = None,
    tags: list[str] = None,
    anonymization_enabled: bool = False,
    modality: str = 'imaging',
) -> Cohort:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cohort = Cohort(
        name=name,
        source_path=source_path,
        description=description,
        tags=tags or [],
        anonymization_enabled=anonymization_enabled,
        modality=modality,
        created_at=now,
        updated_at=now,
        status='idle',
        total_subjects=0,
        total_sessions=0,
        total_series=0,
        completion_percentage=0,
    )
    session.add(cohort)
    session.flush()
    return cohort


def get_cohort(session: Session, cohort_id: int) -> Optional[Cohort]:
    stmt = select(Cohort).where(Cohort.id == cohort_id)
    return session.scalar(stmt)


def list_cohorts(session: Session) -> list[Cohort]:
    stmt = select(Cohort).order_by(Cohort.created_at.desc())
    return list(session.scalars(stmt))





def get_cohort_by_name(session: Session, name: str) -> Optional[Cohort]:
    stmt = select(Cohort).where(Cohort.name == name)
    return session.scalar(stmt)

