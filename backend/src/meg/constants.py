"""Shared filename-parsing regex patterns for the MEG parallel track.

Vendored from `cir-utils/tabs/meg-bids/bidsify/constants.py` (see
`backend/src/meg/parsing.py` for full provenance). Only the naming-convention
regex patterns are kept here; the CIR/NatMEG-specific hardcoded absolute
paths that lived alongside them in the source repo (calibration/crosstalk
file locations, derivatives subfolder name) were dropped -- NILS exposes
equivalent settings as config fields instead (see
`meg.config.MegIngestConfig` / `meg.config.MegBidsConfig`).
"""

from __future__ import annotations

NOISE_PATTERNS = ["empty", "noise", "Empty"]
HEADPOS_PATTERNS = ["headpos", "headshape"]
OPM_EXCEPTION_PATTERNS = ["HPIbefore", "HPIafter", "HPImiddle", "HPIpre", "HPIpost"]
PROC_PATTERNS = ["tsss", "sss", r"corr\d+", r"ds\d+", "mc", "avgHead"]
