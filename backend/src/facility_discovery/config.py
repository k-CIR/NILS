"""Environment configuration for facility-vault discovery.

Three new server-side path env vars (no secrets):
- ``FACILITY_MAPPING_CSV_PATH``: the facility-maintained mapping CSV (task 2).
- ``MRC_STAGING_ROOT``: per-project symlink staging root for `mrc` (task 8/9
  of the plan's Decisions).
- ``FACILITY_SUBJECT_CODE_CSV_ROOT``: directory of generated per-cohort
  subject-code override CSVs (Subject Code Injection Mechanism).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class FacilityDiscoverySettings(BaseModel):
    mapping_csv_path: Optional[Path] = None
    mrc_staging_root: Optional[Path] = None
    subject_code_csv_root: Optional[Path] = None
    # Vault root layout: <vault_root>/mrc/sub-<id>, <vault_root>/natmeg/<project>/raw/...
    vault_root: Optional[Path] = None


@lru_cache
def get_settings() -> FacilityDiscoverySettings:
    mapping_csv = os.getenv("FACILITY_MAPPING_CSV_PATH")
    staging_root = os.getenv("MRC_STAGING_ROOT")
    csv_root = os.getenv("FACILITY_SUBJECT_CODE_CSV_ROOT")
    vault_root = os.getenv("FACILITY_VAULT_ROOT")
    return FacilityDiscoverySettings(
        mapping_csv_path=Path(mapping_csv).expanduser() if mapping_csv else None,
        mrc_staging_root=Path(staging_root).expanduser() if staging_root else None,
        subject_code_csv_root=Path(csv_root).expanduser() if csv_root else None,
        vault_root=Path(vault_root).expanduser() if vault_root else None,
    )
