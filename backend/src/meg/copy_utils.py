"""FIF file integrity checking and copy helpers for the `meg_ingest` stage.

Vendored and adapted from `SESHAT/seshat/stages/copy.py` (local checkout:
`/Users/andreas.gerhardsson/Sites/SESHAT`). No LICENSE file was found in the
source repo; it is an internal sibling project by the same author/
organization as NILS.

Adaptations vs. the source:
  - `get_split_file_parts()` was a near-duplicate of the function of the
    same name in `cir-utils/tabs/meg-bids/bidsify/parsing.py`. Rather than
    vendor both divergent copies, this module imports the single canonical
    implementation from `meg.parsing` and uses it here too.
  - `copy_squid_databases()` took the calibration/crosstalk *source* paths
    from hardcoded module-level globals (`calibration = '/neuro/databases/
    sss/sss_cal.dat'`, `crosstalk = '/neuro/databases/ctc/ct_sparse.fif'`).
    Both source paths are now explicit function parameters (alongside the
    destination paths, which the source already took as parameters) --
    see `meg.config.MegIngestConfig`.
  - `print()` calls replaced with `logger` calls.
  - Directory-discovery (`make_process_list`, tied to a hardcoded NatMEG /
    Sinuhe / Kaptah directory layout), job orchestration
    (`process_file_worker`, `copy_files_to_raw`, `estimate_job_duration`,
    `update_copy_report`) and the CLI entrypoint were dropped -- the
    `meg_ingest` stage (a later task) reimplements discovery against NILS's
    own cohort `source_path` and job-progress-streaming conventions instead
    of this repo's tqdm/print/JSON-report pattern.
"""

from __future__ import annotations

import filecmp
import logging
import os
from os.path import basename, dirname, exists, getmtime, getsize, isdir
from shutil import copy2, copytree

from mne._fiff.write import _get_split_size
from mne.io import read_info, read_raw

from .constants import HEADPOS_PATTERNS, PROC_PATTERNS
from .parsing import file_contains, get_split_file_parts

logger = logging.getLogger(__name__)


def check_fif(file_path: str) -> dict:
    """Check basic properties of a candidate .fif file."""
    is_fif = file_contains(basename(file_path), [r"\.fif$", r"\.fif"])
    is_large = getsize(file_path) > _get_split_size("2GB")
    is_fif_spec = file_contains(basename(file_path), HEADPOS_PATTERNS + ["ave.fif", "config.fif"])
    is_split = file_contains(basename(file_path), [r"-\d+.fif"] + [r"-\d+_" + p for p in PROC_PATTERNS])
    return {
        "is_fif": is_fif,
        "is_large": is_large,
        "is_fif_spec": is_fif_spec,
        "is_split": is_split,
    }


def check_match(source: str, destination, size_tolerance_bytes: int = 4096, check_info: bool = False):
    """Check whether `source` has already been transferred correctly to `destination`.

    For .fif files, compares MNE-readable metadata (when `check_info=True`)
    and total size across split parts. For other files/directories, falls
    back to size and `filecmp` comparison.
    """
    match = False
    info_match = True

    if isinstance(destination, list):
        destination = destination[0]

    if not exists(destination):
        match = False
    else:
        if isdir(source):
            match = filecmp.dircmp(source, destination).funny_files == []

        source_size = getsize(source)

        if check_fif(source)["is_fif"]:
            if check_info:
                info_src = read_info(source, verbose="error")
                info_dst = read_info(destination, verbose="error")

                metadata_checks = {
                    "meas_id_version": info_src["meas_id"]["version"] == info_dst["meas_id"]["version"],
                    "secs": info_src["meas_id"]["secs"] == info_dst["meas_id"]["secs"],
                    "date": info_src["meas_date"] == info_dst["meas_date"],
                    "sfreq": info_src["sfreq"] == info_dst["sfreq"],
                    "nchan": info_src["nchan"] == info_dst["nchan"],
                }
                info_match = all(metadata_checks.values())

            dst_parts = get_split_file_parts(destination)
            if isinstance(dst_parts, list):
                dest_size = sum(getsize(p) for p in dst_parts)
            else:
                dest_size = getsize(destination)
        else:
            dest_size = getsize(destination)

        if source_size > 100 * 1024 * 1024:  # 100MB
            tolerance = max(size_tolerance_bytes, int(source_size * 0.001))  # 0.1%
        else:
            tolerance = size_tolerance_bytes

        size_diff = abs(source_size - dest_size)
        match_size = size_diff <= tolerance
        destination_newer = getmtime(source) < getmtime(destination) + 10  # within 10 seconds

        match = all([match_size, destination_newer, info_match])

    return match, source, destination


def copy_file_or_dir(source: str, destination: str) -> None:
    """Copy a file or directory tree from source to destination."""
    if isdir(source):
        copytree(source, destination)
    else:
        copy2(source, destination)


def copy_data(source: str, destination: str):
    """Copy a MEG file to `destination`, using MNE to split large .fif files.

    Returns `(match, source, destination, message, existing_file, new_file,
    failed_file)`. Skips the copy (`existing_file=1`) if `check_match()`
    reports the destination already matches the source.
    """
    existing_file = 0
    new_file = 0
    failed_file = 0
    match = False
    message = ""

    is_match, _src, _dst = check_match(source, destination)

    if is_match:
        match = True
        source = get_split_file_parts(source)
        destination = get_split_file_parts(destination)
        message = "Copied"
        existing_file = 1
    else:
        os.makedirs(dirname(destination), exist_ok=True)

        is_fif, fif_large, fif_special, is_split = check_fif(source).values()
        use_mne_read_raw = all([is_fif, fif_large, not fif_special, not is_split])

        if use_mne_read_raw:
            try:
                raw = read_raw(source, allow_maxshield=True, verbose="error")
                raw.save(destination, overwrite=True, verbose="error")
                destination = get_split_file_parts(destination)
                message = "Copied (split if > 2GB)"
                match = True
                new_file = 1
            except Exception as exc:  # noqa: BLE001 - mirrors vendored behavior
                try:
                    copy_file_or_dir(source, destination)
                    match = True
                    message = "Fail (MNE failed)"
                    new_file = 1
                    logger.warning("MNE read/save failed for %s, fell back to raw copy: %s", source, exc)
                except Exception as exc2:  # noqa: BLE001
                    match = False
                    message = f"Fail {exc2}"
                    failed_file = 1
                    logger.error("Copy failed for %s: %s", source, exc2)
        else:
            copy_file_or_dir(source, destination)
            match = True
            message = "Copied"
            new_file = 1

    return match, source, destination, message, existing_file, new_file, failed_file


def copy_squid_databases(
    calibration_source: str | None,
    calibration_dest: str | None,
    crosstalk_source: str | None,
    crosstalk_dest: str | None,
) -> None:
    """Copy the calibration/crosstalk reference files alongside a raw acquisition.

    Both source and destination paths are explicit parameters (unlike the
    vendored source, which hardcoded the source paths as module globals).
    """
    if calibration_source and calibration_dest and exists(calibration_source):
        if not exists(dirname(calibration_dest)):
            os.makedirs(dirname(calibration_dest), exist_ok=True)
        copy_data(calibration_source, calibration_dest)
    else:
        logger.warning("Calibration file %s does not exist.", calibration_source)

    if crosstalk_source and crosstalk_dest and exists(crosstalk_source):
        if not exists(dirname(crosstalk_dest)):
            os.makedirs(dirname(crosstalk_dest), exist_ok=True)
        copy_data(crosstalk_source, crosstalk_dest)
    else:
        logger.warning("Crosstalk file %s does not exist.", crosstalk_source)
