"""Core FIF header scanning logic for the `meg_scan` stage.

``scan_fif_header()`` reads a single raw FIF file's header metadata via
``mne.io.read_info()`` (the ``preload=False`` fast path — no full recording
loaded into memory) and returns a ``FifHeader`` dataclass. It also resolves
the file's split-chain and counts continuation files by calling
`meg.parsing.get_split_file_parts`.

``run_meg_scan()`` is the top-level orchestrator: it discovers FIF files
under a cohort's raw root (via ``discover_fif_files`` from ``meg.ingest``),
scans each one, and passes results to the caller-provided ``processor``
callback (typically ``extractor.Extractor.scan_recording``).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import mne
import numpy as np

from jobs.control import JobControl

from .config import MegScanConfig
from .ingest import discover_fif_files
from .models import ChannelInfo, FifHeader, MegScanResult
from .parsing import bids_path_from_rawname, extract_info_from_filename, get_split_file_parts

logger = logging.getLogger(__name__)

mne.set_log_level("WARNING")

# MNE channel-unit -> string mapping (subset of FIFF unit codes)
_FIFF_UNIT_NAMES: dict[int, str] = {
    0: "unitless",
    103: "Hz",
    109: "V",
    112: "T",
    201: "T/m",
    301: "Am",
    302: "T/m",
}


def _unit_mul_prefix(unit_mul: int) -> str:
    _PREFIX = {-6: "f", -3: "m", 0: "", 3: "k"}
    return _PREFIX.get(unit_mul, f"e{unit_mul}")


def _resolve_session_label(file_path: str, header: FifHeader) -> str:
    """Derive a session label from the parent directory structure.

    This uses the same convention as ``bids_path_from_rawname``'s
    ``session_label`` argument: the parent directory basename, stripped of
    any ``ses-`` prefix. If the directory itself is ``meg`` (the standard
    BIDS datatype directory), walk up one more level.

    Returns an empty string if no meaningful label can be derived.
    """
    p = Path(file_path)
    parent = p.parent.name
    if parent.lower() == "meg":
        parent = p.parent.parent.name
    session = parent.replace("ses-", "")
    return session


def scan_fif_header(file_path: str, naming_convention: str = "natmeg") -> FifHeader:
    """Read a single raw FIF file header and return a ``FifHeader``.

    This is a header-only scan: ``mne.io.read_info()`` reads the measurement
    info without loading the full recording into memory.

    Parameters
    ----------
    file_path:
        Absolute path to a ``.fif`` file.
    naming_convention:
        Parsing convention label (passed through to
        ``bids_path_from_rawname``). Defaults to ``"natmeg"``.

    Returns
    -------
    FifHeader
        Populated with header metadata and an empty ``channels`` list (call
        ``populate_channels()`` separately if per-channel data is needed).

    Raises
    ------
    FileNotFoundError
        If ``file_path`` does not exist.
    ValueError
        If the file is not a valid FIF or cannot be read by MNE.
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"FIF file not found: {file_path}")

    # Parse filename first (this can raise for completely unparseable files)
    info_dict = extract_info_from_filename(file_path)

    # Determine split count from on-disk split files
    split_parts = get_split_file_parts(file_path)
    split_count = 0
    if isinstance(split_parts, list):
        split_count = len(split_parts) - 1  # base file excluded

    # Read MNE info (header only, preload=False)
    info = mne.io.read_info(file_path, verbose="error")

    # Measurement date
    meas_date: Optional[date] = None
    if info.get("meas_date") is not None:
        md = info["meas_date"]
        if isinstance(md, tuple):
            meas_date = date.fromtimestamp(md[0])
        elif isinstance(md, datetime):
            meas_date = md.date()

    # Recording duration — from the raw file's n_samples
    # read_info doesn't expose n_times directly; read_raw_fif does.
    # For header-only we approximate from the FIFF data. We'll read raw
    # with preload=False and grab n_times.
    n_times = 0
    sfreq = float(info.get("sfreq") or 0.0)
    try:
        raw = mne.io.read_raw_fif(file_path, preload=False, verbose="error")
        n_times = raw.n_times
    except Exception as exc:
        logger.warning("Could not read raw length for %s: %s", file_path, exc)
        n_times = 0

    duration = n_times / sfreq if sfreq > 0 else 0.0

    # Device info
    device: Optional[str] = None
    manufacturer: Optional[str] = None
    model_name: Optional[str] = None
    dev_info = info.get("device_info")
    if dev_info is not None:
        device = str(dev_info)
        manufacturer = getattr(dev_info, "type", None) or info.get("description")
        model_name = getattr(dev_info, "model", None)
    else:
        desc = info.get("description") or info.get("experimenter", "")
        if desc:
            device = str(desc)

    # Study description / experimenter
    study_description = info.get("description") or info.get("experimenter") or None

    # Institution name
    institution_name = None
    dig = info.get("dig")
    if dig is not None and hasattr(dig, "__iter__"):
        for pt in dig:
            if hasattr(pt, "ident") and pt.get("ident") is not None and "institution" in str(pt).lower():
                institution_name = str(pt)
                break

    # High/low pass filters
    highpass = float(info.get("highpass") or 0.0)
    lowpass = float(info.get("lowpass") or 0.0)

    # Line frequency -> notch string
    line_freq = info.get("line_freq")
    notch_str: Optional[str] = None
    if line_freq is not None:
        notch_str = str(float(line_freq))

    # Bad channels
    bads: list[str] = info.get("bads") or []

    # Build the header model
    session_label = _resolve_session_label(file_path, None)  # type: ignore[arg-type]

    header = FifHeader(
        fif_file_path=str(p.absolute()),
        split_count=split_count,
        participant_from=info_dict.get("participant", ""),
        session_from=session_label,
        task=info_dict.get("task", ""),
        run=info_dict.get("run", ""),
        acquisition=info_dict.get("acquisition", ""),
        processing=info_dict.get("processing", ""),
        description=info_dict.get("description", ""),
        extension=info_dict.get("extension", ".fif"),
        suffix=info_dict.get("suffix", "meg"),
        datatype="meg",
        acquisition_date=meas_date,
        device=device,
        sampling_frequency=sfreq,
        n_channels=info.get("nchan", 0),
        duration_seconds=duration,
        highpass_hz=highpass,
        lowpass_hz=lowpass,
        line_freq=line_freq,
        study_description=study_description,
        manufacturer=manufacturer,
        manufacturer_model_name=model_name,
        station_name=info.get("experimenter") or None,
        institution_name=institution_name,
        bads=bads,
    )

    # Populate channel info from the info object
    ch_types_available = True
    try:
        info.get_channel_types()
    except Exception:
        ch_types_available = False

    for idx in range(info.get("nchan", 0)):
        try:
            ch = info["chs"][idx]
        except IndexError:
            break

        ch_name = ch.get("ch_name", f"CH{idx:04d}")

        try:
            ch_type = mne.channel_type(info, idx)
        except Exception:
            ch_type = "unknown"

        # Unit
        raw_unit = ch.get("unit")
        raw_unit_mul = ch.get("unit_mul", 0)
        unit_str = _FIFF_UNIT_NAMES.get(raw_unit, f"unit_{raw_unit}")
        prefix = _unit_mul_prefix(raw_unit_mul)
        if prefix:
            unit_str = f"{prefix}{unit_str}"

        # Location. `ch["loc"]` is a fixed-length (12,) numpy array (never a
        # Python list/None in practice), with unavailable coordinates stored
        # as NaN rather than None — check length/NaN explicitly instead of
        # truthiness-testing the array itself (which raises ValueError for
        # any array with more than one element).
        loc = ch.get("loc")

        def _loc_component(index: int) -> Optional[float]:
            if loc is None or len(loc) <= index:
                return None
            value = loc[index]
            if value is None:
                return None
            try:
                if bool(np.isnan(value)):
                    return None
            except TypeError:
                pass
            return float(value)

        loc_x = _loc_component(0)
        loc_y = _loc_component(1)
        loc_z = _loc_component(2)

        is_bad = ch_name in bads

        header.channels.append(
            ChannelInfo(
                channel_name=ch_name,
                channel_type=ch_type,
                unit=unit_str,
                is_bad=is_bad,
                location_x=loc_x,
                location_y=loc_y,
                location_z=loc_z,
            )
        )

    return header


def run_meg_scan(
    config: MegScanConfig,
    raw_root: Path,
    processor: Callable[[FifHeader], None],
    progress_callback: Optional[Callable[[dict], None]] = None,
    control: Optional[JobControl] = None,
    job_id: Optional[int] = None,
) -> MegScanResult:
    """Discover FIF files under ``raw_root``, scan each header, and run
    ``processor`` for each successfully scanned file.

    This is the top-level orchestrator for the ``meg_scan`` stage. It
    mirrors the structure of ``meg.ingest.run_meg_ingest``: discover,
    iterate, call a processing callback per file.

    Parameters
    ----------
    config:
        Stage configuration.
    raw_root:
        Root directory containing staged FIF files (output of ``meg_ingest``).
    processor:
        Callback invoked with each successfully scanned ``FifHeader``.
        This is where the caller (typically ``extractor.MegExtractor``)
        persists the scan result to the database.
    progress_callback:
        Optional hook for progress/status events.
    control:
        Optional ``JobControl`` for cooperative cancel/pause support, checked
        once per discovered file (mirrors ``run_meg_ingest``).
    job_id:
        Job id passed through to ``control.checkpoint_blocking`` for
        ``JobCancelledError`` reporting.

    Returns
    -------
    MegScanResult
        Summary counters. Note this only tracks discovery/skip/failure
        counts; DB-write counters (studies/acquisitions/channels inserted or
        updated) live on the ``processor``'s own state (e.g.
        ``MegExtractor.result``) since ``run_meg_scan`` has no visibility
        into what the processor callback actually persisted.
    """
    result = MegScanResult()

    def _emit(payload: dict) -> None:
        if callable(progress_callback):
            try:
                progress_callback(payload)
            except Exception:
                pass

    if not raw_root.exists():
        logger.warning("MEG raw root does not exist: %s", raw_root)
        return result

    discovered = discover_fif_files(raw_root, preserve_split_files=True)
    result.fif_files_discovered = len(discovered)

    _emit({
        "stage": "discovered",
        "message": f"Discovered {result.fif_files_discovered} FIF file(s)",
        "total": result.fif_files_discovered,
    })

    for index, fif_path in enumerate(discovered):
        if control is not None:
            control.checkpoint_blocking(job_id)

        file_path_str = str(fif_path)

        # Skip split continuation files (-1.fif, -2.fif, ...) — we only
        # scan the primary file of each split set.
        import re
        if re.search(r"-\d+\.fif$", file_path_str):
            result.fif_files_skipped += 1
            continue

        _emit({
            "stage": "scanning",
            "message": f"Scanning {index + 1}/{result.fif_files_discovered}",
            "total": result.fif_files_discovered,
            "processed": index - result.fif_files_skipped,
            "errors": result.fif_files_failed,
            "current_file": str(fif_path.relative_to(raw_root) if fif_path.is_relative_to(raw_root) else fif_path.name),
        })

        try:
            header = scan_fif_header(file_path_str, naming_convention=config.naming_convention)
        except Exception as exc:
            logger.error("Failed to scan %s: %s", file_path_str, exc)
            result.fif_files_failed += 1
            result.failures.append({
                "source": file_path_str,
                "reason": str(exc),
                "exception": exc.__class__.__name__,
            })
            continue

        try:
            processor(header)
        except Exception as exc:
            logger.error("Failed to process %s: %s", file_path_str, exc)
            result.fif_files_failed += 1
            result.failures.append({
                "source": file_path_str,
                "reason": str(exc),
                "exception": exc.__class__.__name__,
            })
            continue

    _emit({
        "stage": "done",
        "message": "MEG scan completed",
        "total": result.fif_files_discovered,
        "processed": result.fif_files_discovered - result.fif_files_skipped - result.fif_files_failed,
        "errors": result.fif_files_failed,
        "metrics": result.as_metrics(),
    })

    return result