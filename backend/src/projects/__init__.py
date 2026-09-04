"""Project entity: cross-facility project grouping for cohorts.

A `project` is resolved/created from the facility mapping CSV's
`cir_project` column (see `facility_discovery`) and is the real FK target
for `cohorts.project_id`. Lives in the app DB (same `Base`/engine as
`cohorts`) so the FK is real and enforced.
"""

from .models import Project, ProjectDTO

__all__ = ["Project", "ProjectDTO"]
