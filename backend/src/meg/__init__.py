"""MEG (magnetoencephalography) parallel processing track.

Phase 1 scope: meg_ingest -> meg_scan -> meg_bids. See
.kilo/plans/1787297037644-meg-parallel-track-plan.md for the full design.

Several modules in this package (`parsing`, `copy_utils`, `conversion_table`,
`bids_writer`) are vendored and adapted from two internal sibling
repositories (`cir-utils/tabs/meg-bids/bidsify/` and
`SESHAT/seshat/stages/copy.py`); see each module's docstring for full
provenance and the specific adaptations made.
"""

from .bids_writer import create_dataset_description, write_bids_dataset  # noqa: F401
from .config import (  # noqa: F401
    MegBidsConfig,
    MegBidsOverwriteMode,
    MegIngestConfig,
    MegScanConfig,
    MegSubjectResolutionConfig,
)
from .conversion_table import CONVERSION_COLUMNS  # noqa: F401
from .copy_utils import check_fif, check_match, copy_data, copy_file_or_dir, copy_squid_databases  # noqa: F401
from .parsing import (  # noqa: F401
    bids_path_from_rawname,
    extract_info_from_filename,
    file_contains,
    get_split_file_parts,
)
