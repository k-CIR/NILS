"""SQLAlchemy model and DTO for the `project` entity.

Registers on the shared app-DB `Base` (`cohorts.models.Base`) so that
`cohorts.project_id` can carry a real, enforced foreign key to
`project.project_id` (see the facility-vault-discovery plan's DB
Placement section for why this must be the same physical database as
`cohorts`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cohorts.models import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    """Cross-facility project, resolved/created from the mapping CSV's
    `cir_project` column. `code` is the stable, unique project identifier;
    `name` is an optional, later-editable display label."""

    __tablename__ = "project"

    project_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False
    )


class ProjectDTO(BaseModel):
    """API-facing DTO for a project row."""

    model_config = ConfigDict(from_attributes=True)

    project_id: int
    code: str
    name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
