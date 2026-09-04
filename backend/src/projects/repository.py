"""Data access helpers for `project` persistence."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project


def get_project(session: Session, project_id: int) -> Optional[Project]:
    return session.get(Project, project_id)


def get_project_by_code(session: Session, code: str) -> Optional[Project]:
    stmt = select(Project).where(Project.code == code)
    return session.scalar(stmt)


def find_or_create_project(session: Session, code: str, name: Optional[str] = None) -> Project:
    """Find a project by `code`, creating it if it doesn't exist yet.

    `code` is the facility mapping CSV's `cir_project` value. Idempotent:
    repeated calls with the same code return the same row (find-or-create).
    """
    normalized_code = (code or "").strip()
    if not normalized_code:
        raise ValueError("Project code cannot be blank")

    existing = get_project_by_code(session, normalized_code)
    if existing is not None:
        return existing

    project = Project(code=normalized_code, name=name)
    session.add(project)
    session.flush()
    return project


def list_projects(session: Session) -> list[Project]:
    stmt = select(Project).order_by(Project.code)
    return list(session.scalars(stmt))
