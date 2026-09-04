"""Unit tests for `facility_discovery.dicom_peek.peek_patient_id`."""

from __future__ import annotations

from pathlib import Path

import pytest
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset

from facility_discovery.dicom_peek import (
    NoDicomFileFoundError,
    PatientIdNotFoundError,
    peek_patient_id,
)


def _write_dicom(path: Path, *, patient_id: str | None) -> None:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
    meta.MediaStorageSOPInstanceUID = "1.2.3.4.5.6.7.8.9"
    meta.TransferSyntaxUID = "1.2.840.10008.1.2.1"

    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    if patient_id is not None:
        ds.PatientID = patient_id
    ds.PatientName = "Test^Patient"
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.save_as(str(path), enforce_file_format=True)


def test_peek_patient_id_finds_tag_in_folder(tmp_path):
    folder = tmp_path / "sub-MRC001"
    folder.mkdir()
    _write_dicom(folder / "img001.dcm", patient_id="REALPATID001")

    assert peek_patient_id(folder) == "REALPATID001"


def test_peek_patient_id_accepts_single_file_path(tmp_path):
    folder = tmp_path / "sub-MRC001"
    folder.mkdir()
    file_path = folder / "img001.dcm"
    _write_dicom(file_path, patient_id="REALPATID001")

    assert peek_patient_id(file_path) == "REALPATID001"


def test_peek_patient_id_skips_non_dicom_files_first(tmp_path):
    folder = tmp_path / "sub-MRC001"
    folder.mkdir()
    (folder / "readme.txt").write_text("not a dicom file")
    _write_dicom(folder / "img001.dcm", patient_id="REALPATID001")

    assert peek_patient_id(folder) == "REALPATID001"


def test_peek_patient_id_raises_no_dicom_file_found_for_empty_folder(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()

    with pytest.raises(NoDicomFileFoundError):
        peek_patient_id(folder)


def test_peek_patient_id_raises_patient_id_not_found_for_non_dicom_file(tmp_path):
    # `pydicom.dcmread(force=True)` tolerates non-DICOM bytes and yields an
    # empty dataset rather than raising, so a folder containing only
    # non-DICOM files surfaces as "no PatientID tag", not "unreadable".
    folder = tmp_path / "sub-MRC001"
    folder.mkdir()
    (folder / "notes.txt").write_text("nothing dicom-like here")

    with pytest.raises(PatientIdNotFoundError):
        peek_patient_id(folder)


def test_peek_patient_id_raises_patient_id_not_found(tmp_path):
    folder = tmp_path / "sub-MRC001"
    folder.mkdir()
    _write_dicom(folder / "img001.dcm", patient_id=None)

    with pytest.raises(PatientIdNotFoundError):
        peek_patient_id(folder)


def test_peek_patient_id_searches_nested_subdirectories(tmp_path):
    folder = tmp_path / "sub-MRC001"
    nested = folder / "series1"
    nested.mkdir(parents=True)
    _write_dicom(nested / "img001.dcm", patient_id="NESTEDPATID")

    assert peek_patient_id(folder) == "NESTEDPATID"
