"""Facility vault discovery + project mapping.

Auto-detects subjects/sessions already present on disk for the `mrc`
(MRI) and `natmeg` (MEG) facilities under a shared vault layout, resolves
their cross-facility subject identity and project via a facility-maintained
mapping CSV, and stages them in `facility_discoveries` for human review
before any `project`/`cohort`/`subject` linkage is created.

See ``.kilo/plans/1788447839971-facility-vault-discovery-project-mapping-plan.md``
for the full design.
"""

from .models import FacilityDiscovery, FacilityDiscoveryDTO, FacilitySubjectMapping

__all__ = [
    "FacilityDiscovery",
    "FacilityDiscoveryDTO",
    "FacilitySubjectMapping",
]
