"""Dataclass models for the `meg_scan` stage: FIF header extraction, subject
resolution outcomes, and conversion- table row builders.

These models serve as the typed contract between `scanner.py` (which reads
FIF headers via `mne.io.read_info()` or `mne.io.read_raw_fif(..., preload=False)`)
and `extractor.py` (which upserts the metadata into the NILS metadata DB).

They mirror the approach used by `extract.writer.py`'s `InstancePayload` (see
`extract/worker.py`) but are MEG-specific and synchronous.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Optional


@dataclass
class FifHeader:
    """Metadata extracted from a single raw FIF file header ONLY (no full load).

    Populated by `scanner.scan_fif_header()`, which reads via
    ``mne.io.read_info()`` (or ``read_raw_fif(preload=False).info``) without
    loading the full recording into memory — matching the plan's constraint
    "Read MEG headers with ``mne.io.read_info()`` only; avoid loading full
    recordings".
    """

    # --- Source identity ---
    fif_file_path: str
    """Absolute path to the primary .fif file on disk."""

    split_count: int = 0
    """Number of split continuation files (-1.fif, -2.fif, ...)."""

    # --- Subject / session (filename-derived, provisional) ---
    participant_from: str = ""
    """FIF-filename-derived participant label (e.g. "0953")."""

    session_from: str = ""
    """Session label from the parent directory name (e.g. "02" or "20250101")."""

    task: str = ""
    """BIDS task name parsed from the filename (e.g. "RestingState")."""

    run: str = ""
    """Run number parsed from the filename, if any."""

    acquisition: str = ""
    """Acquisition label (parent dir basename, e.g. "triux" or "hedscan")."""

    processing: str = ""
    """Processing label (e.g. "tsss" / "sss" / "mc"), dash-joined."""

    description: str = ""
    """Description tag (e.g. "trans" / "headshape")."""

    extension: str = ".fif"
    """File extension."""

    suffix: str = "meg"
    """BIDS suffix (e.g. "meg", "eeg", "headshape")."""

    datatype: str = "meg"
    """BIDS datatype (e.g. "meg", "eeg")."""

    # --- Recording metadata (from FIF header info) ---
    acquisition_date: Optional[date] = None
    """Measurement date (from meas_date)."""

    device: Optional[str] = None
    """Manufacturer / system name (from info['device_info'] or
    info['description'])."""

    sampling_frequency: float = 0.0
    """Sampling frequency in Hz (info['sfreq'])."""

    n_channels: int = 0
    """Number of channels (info['nchan'])."""

    duration_seconds: float = 0.0
    """Recording duration in seconds (n_times / sfreq)."""

    highpass_hz: float = 0.0
    """High-pass filter cutoff (info['highpass'])."""

    lowpass_hz: float = 0.0
    """Low-pass filter cutoff (info['lowpass'])."""

    line_freq: Optional[float] = None
    """Power-line frequency (info['line_freq']), stored as notch_filter_hz."""

    # --- Study-level metadata (from FIF header) ---
    study_description: Optional[str] = None
    """Description / experiment name (info['description'] or
    info['experimenter'])."""

    manufacturer: Optional[str] = None
    """System manufacturer. Populated from device_info or description."""

    manufacturer_model_name: Optional[str] = None
    """System model name. May not be available in all FIF files."""

    station_name: Optional[str] = None
    """Scanner / system station name (rarely present in MEG FIF)."""

    institution_name: Optional[str] = None
    """Institution where data was acquired. Rarely populated in FIF files."""

    # --- Per-channel data (lazily populated) ---
    channels: list[ChannelInfo] = field(default_factory=list)
    """Per-channel metadata extracted from the FIF header."""

    # --- Bad channels ---
    bads: list[str] = field(default_factory=list)
    """Channel names marked as bad in info['bads']."""


@dataclass
class ChannelInfo:
    """Metadata for a single MEG channel as extracted from the FIF header."""

    channel_name: str
    channel_type: str
    unit: Optional[str] = None
    is_bad: bool = False
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    location_z: Optional[float] = None


@dataclass
class SubjectResolution:
    """Outcome of subject-identity resolution for one MEG session.

    Mirrors `extract.subject_mapping.SubjectResolution` but adds the
    resolved NILS `subject_id` so the extractor layer can use it directly
    without a second lookup.
    """

    subject_id: int
    subject_code: str
    source: str  # "identifier_lookup" | "csv" | "hash_fallback"
    session_label: str
    session_date: date


@dataclass
class MegScanResult:
    """Aggregated counters and diagnostics for one `meg_scan` run."""

    fif_files_discovered: int = 0
    fif_files_skipped: int = 0
    fif_files_failed: int = 0
    subjects_inserted: int = 0
    studies_inserted: int = 0
    studies_updated: int = 0
    acquisitions_inserted: int = 0
    acquisitions_updated: int = 0
    channels_inserted: int = 0
    channels_updated: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_metrics(self) -> dict:
        return {
            "fif_files_discovered": self.fif_files_discovered,
            "fif_files_skipped": self.fif_files_skipped,
            "fif_files_failed": self.fif_files_failed,
            "subjects_inserted": self.subjects_inserted,
            "studies_inserted": self.studies_inserted,
            "studies_updated": self.studies_updated,
            "acquisitions_inserted": self.acquisitions_inserted,
            "acquisitions_updated": self.acquisitions_updated,
            "channels_inserted": self.channels_inserted,
            "channels_updated": self.channels_updated,
            "failure_count": len(self.failures),
        }


def synthesize_study_uid(
    subject_code: str,
    session_label: str,
    acquisition_date: date,
) -> str:
    """Generate a deterministic, stable study_instance_uid for a MEG session.

    MEG has no native DICOM UID to put in ``study.study_instance_uid`` (which
    has a ``nullable=False, unique=True`` constraint). This synthesizes one
    via UUID v5 (namespace=DNS) over the tuple
    ``(subject_code, session_label, acquisition_date.isoformat())`` so that
    repeated ``meg_scan`` runs for the same session always produce the same
    UID, and thus the same ``study`` row is upserted instead of duplicated.

    This matches the plan's requirement at line 85 of the spec:
    "Synthesize a stable, deterministic value (e.g. a uuid5/hash over
    subject_code, session label, and acquisition date)."
    """
    raw = f"MEG::{subject_code}::{session_label}::{acquisition_date.isoformat()}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, raw))


def _fif_unit_to_str(unit: int, unit_mul: int = 0) -> str:
    """Convert MNE integer unit + unit_mul to a string label.

    See mne/_fiff/constants.py for the FIFF unit constants.
    """
    # Common FIFF unit codes
    _MAP: dict[int, str] = {
        0: "unitless",
        1: "m",
        2: "kg",
        3: "s",
        4: "A",
        5: "K",
        6: "mol",
        7: "cd",
        101: "m/s",
        102: "m/s²",
        103: "Hz",
        104: "N",
        105: "J",
        106: "W",
        107: "Pa",
        108: "C",
        109: "V",
        110: "F",
        111: "ohm",
        112: "T",
        113: "Wb",
        114: "H",
        201: "T/m",
        202: "T/m²",
        301: "Am/m²",
        302: "T s",
    }
    base = _MAP.get(unit, f"FIFF_UNIT_{unit}")
    if unit_mul == -3:
        return f"m{base}" if base in ("m",) else base
    elif unit_mul == -6:
        return f"µ{base}" if base in ("V", "T", "A") else base
    elif unit_mul == 3:
        return f"k{base}" if base in ("m", "g") else base
    return base