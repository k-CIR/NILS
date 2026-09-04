"""DICOM `PatientID` header peek for `mrc` discovery confirm.

`PatientID` may not equal the `mrc_id` folder name and may be unreliable at
this facility, so confirm must read the *actual* tag value the extract
stage will later read for that folder, rather than trusting the folder name
or the mapping CSV. Reuses the same `pydicom.dcmread(..., force=True,
stop_before_pixels=True)` pattern already used by `extract/worker.py` and
`extract/process_pool.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pydicom


class NoDicomFileFoundError(Exception):
    """No readable DICOM file exists under the candidate folder."""


class PatientIdNotFoundError(Exception):
    """A DICOM file was read but it has no `PatientID` tag."""


def _iter_candidate_files(folder: Path):
    if folder.is_file():
        yield folder
        return
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            yield path


def peek_patient_id(folder: Path) -> str:
    """Return the `PatientID` tag value from one representative DICOM file
    under `folder`.

    Tries files in sorted order until one is readable as DICOM and has a
    non-empty `PatientID`; raises `NoDicomFileFoundError` if no file under
    the folder can be read as DICOM at all, or `PatientIdNotFoundError` if
    DICOM files were read but none carried a `PatientID` tag. Confirm must
    surface these as a clear failure rather than silently falling back to
    the folder name (see the plan's Failure Modes To Handle).
    """
    any_dicom_read = False
    for candidate in _iter_candidate_files(folder):
        try:
            dataset = pydicom.dcmread(str(candidate), force=True, stop_before_pixels=True)
        except Exception:
            continue
        any_dicom_read = True
        patient_id: Optional[str] = getattr(dataset, "PatientID", None)
        if patient_id:
            return str(patient_id).strip()

    if not any_dicom_read:
        raise NoDicomFileFoundError(f"No readable DICOM file found under {folder}")
    raise PatientIdNotFoundError(f"No DICOM file under {folder} has a PatientID tag")
