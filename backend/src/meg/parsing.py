"""MEG raw-filename parsing and BIDS-path derivation.

Vendored and adapted from `cir-utils/tabs/meg-bids/bidsify/parsing.py`
(local checkout: `/Users/andreas.gerhardsson/Sites/cir-utils`), with
`file_contains()` inlined from the same repo's `bidsify/utils.py`. No
LICENSE file was found in the source repo; it is an internal sibling
project by the same author/organization as NILS.

Adaptations vs. the source:
  - `bids_path_from_rawname()` takes an explicit `bids_root: str` instead of
    a CIR-schema config dict (`config.get('BIDS', '')`).
  - The participant/session-remapping branch (`pmap` argument, driven by a
    CIR-specific `Original_subjID_name`/`New_subjID_name`/
    `Original_session_name`/`New_session_name` config schema) was dropped
    entirely -- it does not match NILS's subject-resolution design
    (`meg.config.MegSubjectResolutionConfig`: `subject_id_type_id` +
    optional CSV mapping). The `meg_scan` stage is responsible for
    resolving final subject/session identity and overwriting the
    filename-derived values on the returned `BIDSPath` before it reaches
    `bids_writer.write_bids_dataset()`.
  - Derivatives-subfolder handling (`DERIVATIVES_SUBFOLDER`, routing
    processed/derivative files to a separate BIDS root) was dropped: phase
    1 of the MEG track only ingests raw scans.
  - `print()` calls replaced with `logger` calls.
"""

from __future__ import annotations

import logging
import re
from os.path import basename, dirname, exists, join

import mne
from mne_bids import BIDSPath

from .constants import HEADPOS_PATTERNS, NOISE_PATTERNS, OPM_EXCEPTION_PATTERNS, PROC_PATTERNS

logger = logging.getLogger(__name__)

mne.set_log_level("WARNING")


def file_contains(file: str, pattern: list[str]) -> bool:
    """Check if a filename contains any of the specified regex patterns."""
    return bool(re.compile("|".join(pattern)).search(file))


def extract_info_from_filename(file_name: str) -> dict:
    """Parse a MEG filename to extract standardized metadata components.

    Recognizes the NatMEG-style naming convention (`NatMEG_<id>` or
    `sub-<id>` prefixes, `-N.fif` split suffixes, `tsss`/`sss`/etc.
    processing tags, `trans`/`headpos`/`headshape` special suffixes, and
    OPM/hedscan filename exceptions).
    """
    suffix = ""
    desc = ""
    split = ""

    participant = re.search(r"(NatMEG_|sub-)(\d+)", file_name).group(2)

    if len(participant) >= 4:
        # Keep 4+ digit numbers as is (e.g., 0953)
        pass
    else:
        # Normalize 1-3 digit numbers: 1-99 -> 012, 100-999 -> 121
        num = int(participant)
        if num < 100:
            participant = f"{num:03d}"
        else:
            participant = str(num)

    extension = "." + re.search(r"\.(.*)", basename(file_name)).group(1)
    datatypes = list(
        set(
            [r.lower() for r in re.findall(r"(meg|raw|opm|eeg|behav)", basename(file_name), re.IGNORECASE)]
            + ["opm" if "kaptah" in file_name else ""]
        )
    )
    suffix = "meg" if any(item in datatypes for item in ["raw", "meg"]) else ""
    datatypes = [d for d in datatypes if d != ""]

    proc = re.findall("|".join(PROC_PATTERNS), basename(file_name))

    if file_contains(basename(file_name), ["trans"]):
        desc = "trans"
        suffix = "meg"

    if file_contains(file_name, HEADPOS_PATTERNS):
        suffix = "headshape"

    split_match = re.search(r"(\-\d+\.fif)", basename(file_name))
    split = split_match.group(1).strip(".fif") if split_match else ""

    exclude_from_task = "|".join(
        ["NatMEG_"]
        + ["sub-"]
        + ["proc"]
        + datatypes
        + [participant]
        + [extension]
        + [suffix]
        + HEADPOS_PATTERNS
        + proc
        + [split]
        + ["\\+"]
        + ["\\-"]
        + [desc]
    )

    if file_contains(file_name, OPM_EXCEPTION_PATTERNS):
        datatypes.append("opm")

    if "opm" in datatypes or "kaptah" in file_name:
        exclude_from_task = "|".join(
            ["NatMEG_"]
            + ["sub-"]
            + ["proc-"]
            + datatypes
            + [participant]
            + [extension]
            + proc
            + [split]
            + ["\\+"]
            + ["\\-"]
            + ["file"]
            + [desc]
            + [r"\d{8}_", r"\d{6}_"]
        )
        if not file_contains(file_name, OPM_EXCEPTION_PATTERNS):
            exclude_from_task += "|hpi|ds"

        task = re.sub(exclude_from_task, "", basename(file_name), flags=re.IGNORECASE)
        proc = re.findall("|".join(PROC_PATTERNS + ["hpi", "ds"]), basename(file_name))
    else:
        task = re.sub(exclude_from_task, "", basename(file_name), flags=re.IGNORECASE)

    task_parts = [t for t in task.split("_") if t]
    if len(task_parts) > 1:
        task = "".join([t.title() for t in task_parts])
    else:
        task = task_parts[0]

    if file_contains(task, NOISE_PATTERNS):
        match = re.search("before|after", task.lower())
        task = f"Noise{match.group().title()}" if match else "Noise"

    return {
        "filename": file_name,
        "participant": participant,
        "task": task,
        "split": split,
        "processing": proc,
        "description": desc,
        "datatypes": datatypes,
        "suffix": suffix,
        "extension": extension,
    }


def get_split_file_parts(file_path) -> str | list[str]:
    """Get all parts of a potentially split .fif file (MNE `-N.fif` convention).

    Returns the single file path if no splits are found, or a list of file
    paths (base file first) if split parts exist on disk.
    """
    file_path_str = str(file_path)

    if not exists(file_path_str):
        return file_path_str

    parts = []
    base_path = re.sub(r"-\d+\.fif$", ".fif", file_path_str)

    if exists(base_path) and base_path != file_path_str:
        parts.append(base_path)
    else:
        parts.append(file_path_str)

    base_without_ext = base_path.replace(".fif", "")
    i = 1
    while True:
        split_file = f"{base_without_ext}-{i}.fif"
        if exists(split_file):
            parts.append(split_file)
            i += 1
        else:
            break

    return parts[0] if len(parts) == 1 else parts


def bids_path_from_rawname(
    file_name: str,
    session_label: str,
    bids_root: str,
    read_info: bool = True,
) -> tuple[BIDSPath | None, dict | None]:
    """Derive a `BIDSPath` from a raw MEG filename.

    Subject/session values are taken as parsed from the filename /
    `session_label`; the `meg_scan` stage is expected to overwrite them with
    the resolved identity (see module docstring) before this path is used
    to actually write BIDS data.
    """
    if not exists(file_name):
        logger.warning("Not exists: %s", file_name)
        return None, None

    info_dict = extract_info_from_filename(file_name)

    task = info_dict.get("task")
    subject = info_dict.get("participant")
    if not task or not subject:
        logger.warning("Missing required fields in %s", file_name)
        return None, info_dict

    acquisition = basename(dirname(file_name))

    proc = "+".join(info_dict.get("processing", []))
    split = info_dict.get("split")
    run = info_dict.get("run", "")
    desc = info_dict.get("description")
    extension = info_dict.get("extension")
    suffix = info_dict.get("suffix")

    subj_out = subject
    session_out = str(session_label).replace("ses-", "")
    session_out = session_out.lstrip("0").zfill(2) if len(session_out) > 1 else session_out.zfill(2)

    datatype = "meg"
    if read_info and not file_contains(basename(file_name), HEADPOS_PATTERNS + ["trans"]):
        try:
            info = mne.io.read_info(file_name, verbose="error")
            ch_types = set(info.get_channel_types())

            if "mag" in ch_types:
                datatype = "meg"
                extension = ".fif"
            elif "eeg" in ch_types:
                datatype = "eeg"
                extension = ""
                suffix = "eeg"
        except Exception as exc:  # noqa: BLE001 - mirrors vendored behavior
            logger.warning("Error reading file %s: %s", file_name, exc)

    try:
        bids_path = BIDSPath(
            root=bids_root,
            subject=subj_out,
            session=session_out,
            task=task,
            acquisition=acquisition,
            processing=None if proc == "" else proc,
            run=None if run == "" else str(run).zfill(2),
            datatype=datatype,
            description=None if desc == "" else desc,
            extension=None if extension == "" else extension,
            suffix=None if suffix == "" else suffix,
        )
    except ValueError as exc:
        logger.warning("Error creating BIDSPath for %s: %s", file_name, exc)
        return None, info_dict

    return bids_path, info_dict
