"""Pipeline stage and step ordering configuration.

This is the SINGLE SOURCE OF TRUTH for pipeline structure.
To add a new stage or step, modify this file only.

The pipeline consists of stages, where some stages (like 'sort') have
multiple sub-steps. Simple stages have steps=None.

Example:
    To add a new sorting step 'fingerprint':
    1. Add {"id": "fingerprint", "title": "Fingerprinting", "description": "..."} 
       to the sort stage's steps list
    2. That's it! The database and API will automatically include it.
"""

from __future__ import annotations

from typing import TypedDict, Optional


class StepConfig(TypedDict):
    """Configuration for a single step within a multi-step stage."""
    id: str
    title: str
    description: str


class StageConfig(TypedDict):
    """Configuration for a pipeline stage."""
    id: str
    title: str
    description: str
    steps: Optional[list[StepConfig]]  # None for simple stages


# =============================================================================
# PIPELINE STRUCTURE - ORDER MATTERS
# =============================================================================

PIPELINE_STAGES: list[StageConfig] = [
    {
        "id": "anonymize",
        "title": "Anonymization",
        "description": "Remove PHI from DICOM headers with configurable strategies.",
        "steps": None,  # Simple stage (no sub-steps)
    },
    {
        "id": "extract",
        "title": "Metadata Extraction",
        "description": "Parse DICOM metadata into the staging catalog for downstream sorting.",
        "steps": None,
    },
    {
        "id": "sort",
        "title": "Sorting",
        "description": "Label, group, and QC imaging sequences using curated heuristics.",
        "steps": [
            {
                "id": "checkup",
                "title": "Checkup",
                "description": "Verify cohort scope and data integrity",
            },
            {
                "id": "stack_fingerprint",
                "title": "Stack Fingerprint",
                "description": "Build classification features for each stack",
            },
            {
                "id": "classification",
                "title": "Classification",
                "description": "Classify each stack using detection rules",
            },
            {
                "id": "completion",
                "title": "Completion",
                "description": "Fill gaps and flag for review",
            },
            # Future steps - uncomment when implemented:
            # {
            #     "id": "deduplicate",
            #     "title": "Deduplication",
            #     "description": "Identify and handle duplicate series",
            # },
        ],
    },
    {
        "id": "bids",
        "title": "BIDS Export",
        "description": "Organize DICOM or NIfTI outputs into BIDS or flat layouts.",
        "steps": None,
    },
]


# =============================================================================
# MEG PIPELINE (phase 1: meg_ingest -> meg_scan -> meg_bids)
#
# A separate, parallel stage list for MEG cohorts. MEG cohorts never use
# PIPELINE_STAGES above (anonymize/extract/sort/bids are DICOM-centric).
# `meg_maxfilter` and `meg_qc` are deferred follow-up stages: their IDs and
# ordering position are reserved here (commented out) but not active yet.
# =============================================================================

MEG_PIPELINE_STAGES: list[StageConfig] = [
    {
        "id": "meg_ingest",
        "title": "MEG Ingest",
        "description": "Discover and stage raw FIF files (and split sets) into the cohort workspace.",
        "steps": None,
    },
    {
        "id": "meg_scan",
        "title": "MEG Scan",
        "description": "Read FIF headers, resolve subject identity, and build the BIDS conversion table.",
        "steps": None,
    },
    {
        "id": "meg_bids",
        "title": "MEG BIDS Export",
        "description": "Convert staged FIF recordings into a BIDS dataset via mne-bids.",
        "steps": None,
    },
    # Future stages - uncomment when implemented:
    # {
    #     "id": "meg_maxfilter",
    #     "title": "MEG MaxFilter",
    #     "description": "Apply MaxFilter (SSS/tSSS) processing to staged recordings.",
    #     "steps": None,
    # },
    # {
    #     "id": "meg_qc",
    #     "title": "MEG QC",
    #     "description": "Run MEG-specific validation/QC reporting.",
    #     "steps": None,
    # },
]


def _stages_for_modality(modality: str) -> list[StageConfig]:
    """Return the stage list for a cohort modality.

    Defaults to the imaging (DICOM MR/CT/PET) stage list for any
    unrecognized/blank modality, which preserves existing behaviour for
    every cohort created before the modality column existed.
    """
    if (modality or "imaging").strip().lower() == "meg":
        return MEG_PIPELINE_STAGES
    return PIPELINE_STAGES


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_pipeline_items(anonymization_enabled: bool = True, modality: str = "imaging") -> list[dict]:
    """Get flattened list of all stages/steps with sort_order.
    
    Args:
        anonymization_enabled: Whether to include the anonymize stage.
            Ignored for MEG cohorts, which have no anonymize stage.
        modality: Cohort modality ("imaging" or "meg"). Defaults to
            "imaging" so every existing caller keeps producing today's
            DICOM order unchanged.
        
    Returns:
        List of dicts with stage_id, step_id, title, description, sort_order.
        For simple stages, step_id is None.
    """
    items = []
    order = 0
    
    for stage in _stages_for_modality(modality):
        # Skip anonymize if not enabled
        if stage["id"] == "anonymize" and not anonymization_enabled:
            continue
            
        if stage["steps"]:
            # Multi-step stage - add each step as a separate item
            for step in stage["steps"]:
                items.append({
                    "stage_id": stage["id"],
                    "step_id": step["id"],
                    "title": step["title"],
                    "description": step["description"],
                    "sort_order": order,
                })
                order += 1
        else:
            # Simple stage - add the stage itself
            items.append({
                "stage_id": stage["id"],
                "step_id": None,
                "title": stage["title"],
                "description": stage["description"],
                "sort_order": order,
            })
            order += 1
            
    return items


def get_stage_ids(modality: str = "imaging") -> list[str]:
    """Get list of stage IDs in order."""
    return [s["id"] for s in _stages_for_modality(modality)]


def get_step_ids_for_stage(stage_id: str, modality: str = "imaging") -> list[str]:
    """Get step IDs for a multi-step stage, or empty list for simple stages."""
    for stage in _stages_for_modality(modality):
        if stage["id"] == stage_id and stage["steps"]:
            return [s["id"] for s in stage["steps"]]
    return []


def get_stage_config(stage_id: str, modality: str = "imaging") -> StageConfig | None:
    """Get configuration for a specific stage."""
    for stage in _stages_for_modality(modality):
        if stage["id"] == stage_id:
            return stage
    return None


def is_multi_step_stage(stage_id: str, modality: str = "imaging") -> bool:
    """Check if a stage has multiple steps."""
    for stage in _stages_for_modality(modality):
        if stage["id"] == stage_id:
            return stage["steps"] is not None
    return False


def get_next_step_in_stage(stage_id: str, current_step_id: str, modality: str = "imaging") -> str | None:
    """Get the next step ID within a stage, or None if at last step."""
    step_ids = get_step_ids_for_stage(stage_id, modality)
    try:
        idx = step_ids.index(current_step_id)
        if idx + 1 < len(step_ids):
            return step_ids[idx + 1]
    except ValueError:
        pass
    return None


def get_previous_step_in_stage(stage_id: str, current_step_id: str, modality: str = "imaging") -> str | None:
    """Get the previous step ID within a stage, or None if at first step."""
    step_ids = get_step_ids_for_stage(stage_id, modality)
    try:
        idx = step_ids.index(current_step_id)
        if idx > 0:
            return step_ids[idx - 1]
    except ValueError:
        pass
    return None


# =============================================================================
# DEFAULT CONFIGURATIONS
# =============================================================================


def get_default_stage_config(
    stage_id: str,
    cohort_name: str = "",
    source_path: str = "",
    modality: str = "imaging",
) -> dict:
    """Get default configuration for a stage.
    
    Args:
        stage_id: The stage identifier.
        cohort_name: Name of the cohort (used for some default values).
        source_path: Source data path (used for some default values).
        modality: Cohort modality ("imaging" or "meg"). Only affects which
            stage_id branches are reachable; imaging stage IDs and MEG
            stage IDs never collide, so this is mostly informational.
        
    Returns:
        Default configuration dictionary for the stage.
    """
    if stage_id == "meg_ingest":
        return {
            "sourcePath": source_path,
            "copyCalibrationFiles": True,
            "copyCrosstalkFiles": True,
            "preserveSplitFiles": True,
            "copyWorkers": 4,
        }
    elif stage_id == "meg_scan":
        return {
            "subjectIdTypeId": None,          # None -> use subject_code; else an id_types.id_type_id
            "subjectCsvMappingPath": None,     # fallback CSV mapping, uploaded per-cohort
            "namingConvention": "natmeg",
            "requireCalibrationFiles": False,
        }
    elif stage_id == "meg_bids":
        return {
            "bidsRootName": "bids-meg",
            "overwriteMode": "skip",           # skip | overwrite
            "datasetDescriptionName": cohort_name,
            "convertWorkers": 4,
        }
    elif stage_id == "anonymize":
        return {
            "patient_id": {
                "enabled": True,
                "strategy": "folder",
                "folder": {
                    "strategy": "depth",
                    "depth_after_root": 1,
                    "regex": "(.+)",
                    "fallback_template": "XXXX",
                },
            },
            "study_dates": {
                "enabled": False,
                "snap_to_six_months": True,
                "minimum_offset_months": 0,
            },
            "audit_export": {
                "enabled": True,
                "format": "encrypted_excel",
                "filename": f"{cohort_name}_audit.xlsx",
                "excel_password": "neuroimaging2025",
            },
            "anonymize_categories": ["Patient_Information"],
            "concurrent_processes": 32,
            "worker_threads": 32,
        }
    elif stage_id == "extract":
        return {
            "process_pool_workers": 8,
            "scan_thread_workers": 8,
            "folder_thread_workers": 8,
        }
    elif stage_id == "sort":
        return {
            "profile": "standard",
            "apply_llm_assist": False,
            "allow_manual_overrides": True,
            "selectedModalities": ["MR", "CT", "PT"],
            "skipClassified": True,
            "forceReprocess": False,
        }
    elif stage_id == "bids":
        return {
            "outputModes": ["dcm"],          # list: can include "dcm" and one nifti variant
            "outputMode": "dcm",             # legacy single selection
            "layout": "bids",                # bids | flat
            "overwriteMode": "skip",         # skip | clean | overwrite | prompt (legacy)
            "includeIntents": ["anat", "dwi", "func", "fmap", "perf"],
            "includeProvenance": ["SyMRI", "SWIRecon", "EPIMix"],
            "excludeProvenance": [],         # empty means no exclusions by default
            "groupSyMRI": True,
            "copyWorkers": 8,
            "convertWorkers": 8,
            "bidsDcmRootName": "bids-dcm",
            "bidsNiftiRootName": "bids-nifti",
            "flatDcmRootName": "flat-dcm",
            "flatNiftiRootName": "flat-nifti",
            "subjectIdentifierSource": "subject_code",  # "subject_code" or id_type_id number
        }
    return {}
