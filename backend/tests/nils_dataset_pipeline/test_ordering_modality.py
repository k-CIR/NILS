"""Unit tests for modality-aware pipeline ordering (`nils_dataset_pipeline.ordering`).

Covers the MEG parallel track's Validation Plan item: "Unit tests for
modality-aware pipeline ordering and default config selection." These are
pure-function tests with no database dependency.
"""

from nils_dataset_pipeline.ordering import (
    MEG_PIPELINE_STAGES,
    PIPELINE_STAGES,
    get_default_stage_config,
    get_pipeline_items,
    get_stage_config,
    get_stage_ids,
    get_step_ids_for_stage,
    is_multi_step_stage,
)


class TestStageIdsByModality:
    def test_imaging_default_matches_current_dicom_order(self):
        # No modality argument: existing callers must keep getting today's
        # DICOM order unchanged.
        assert get_stage_ids() == ["anonymize", "extract", "sort", "bids"]

    def test_imaging_explicit_matches_default(self):
        assert get_stage_ids(modality="imaging") == get_stage_ids()

    def test_meg_modality_returns_meg_stage_family(self):
        assert get_stage_ids(modality="meg") == ["meg_ingest", "meg_scan", "meg_bids"]

    def test_meg_and_imaging_stage_ids_are_disjoint(self):
        imaging_ids = set(get_stage_ids(modality="imaging"))
        meg_ids = set(get_stage_ids(modality="meg"))
        assert imaging_ids.isdisjoint(meg_ids)

    def test_unrecognized_modality_falls_back_to_imaging(self):
        # Blank/unknown modality must not silently produce an empty or
        # MEG stage list for pre-existing cohorts with no modality set.
        assert get_stage_ids(modality="") == get_stage_ids(modality="imaging")
        assert get_stage_ids(modality="unknown") == get_stage_ids(modality="imaging")

    def test_modality_matching_is_case_and_whitespace_insensitive(self):
        assert get_stage_ids(modality="MEG") == get_stage_ids(modality="meg")
        assert get_stage_ids(modality=" meg ") == get_stage_ids(modality="meg")


class TestPipelineItemsByModality:
    def test_meg_pipeline_items_ignore_anonymization_enabled(self):
        # MEG cohorts have no anonymize stage; the flag must be a no-op.
        with_anon = get_pipeline_items(anonymization_enabled=True, modality="meg")
        without_anon = get_pipeline_items(anonymization_enabled=False, modality="meg")
        assert with_anon == without_anon
        assert [item["stage_id"] for item in with_anon] == ["meg_ingest", "meg_scan", "meg_bids"]
        assert all(item["stage_id"] != "anonymize" for item in with_anon)

    def test_meg_pipeline_items_have_sequential_sort_order(self):
        items = get_pipeline_items(modality="meg")
        assert [item["sort_order"] for item in items] == list(range(len(items)))

    def test_meg_stages_have_no_steps(self):
        # Phase 1 MEG stages are all simple (single-step) stages.
        for stage in MEG_PIPELINE_STAGES:
            assert stage["steps"] is None
        for stage_id in get_stage_ids(modality="meg"):
            assert is_multi_step_stage(stage_id, modality="meg") is False
            assert get_step_ids_for_stage(stage_id, modality="meg") == []

    def test_imaging_pipeline_items_unaffected_by_meg_addition(self):
        # Regression guard: adding MEG_PIPELINE_STAGES must not change the
        # imaging pipeline's stage order. Some DICOM stages (e.g. "sort")
        # are multi-step and flatten into several items, so de-duplicate
        # consecutive stage ids while preserving order.
        items = get_pipeline_items(anonymization_enabled=True)
        seen_stage_ids = list(dict.fromkeys(item["stage_id"] for item in items))
        assert seen_stage_ids == ["anonymize", "extract", "sort", "bids"]
        assert all("meg" not in item["stage_id"] for item in items)


class TestStageConfigLookupByModality:
    def test_get_stage_config_returns_meg_stage_under_meg_modality(self):
        config = get_stage_config("meg_scan", modality="meg")
        assert config is not None
        assert config["id"] == "meg_scan"

    def test_get_stage_config_returns_none_for_meg_stage_under_imaging_modality(self):
        # meg_scan must not be reachable from the imaging stage family.
        assert get_stage_config("meg_scan", modality="imaging") is None

    def test_get_stage_config_returns_none_for_dicom_stage_under_meg_modality(self):
        assert get_stage_config("extract", modality="meg") is None

    def test_all_meg_stage_ids_match_pipeline_stages_constant(self):
        assert get_stage_ids(modality="meg") == [stage["id"] for stage in MEG_PIPELINE_STAGES]

    def test_all_imaging_stage_ids_match_pipeline_stages_constant(self):
        assert get_stage_ids(modality="imaging") == [stage["id"] for stage in PIPELINE_STAGES]


class TestDefaultStageConfigByModality:
    def test_meg_ingest_default_config(self):
        config = get_default_stage_config("meg_ingest", source_path="/data/cohort")
        assert config == {
            "sourcePath": "/data/cohort",
            "copyCalibrationFiles": True,
            "copyCrosstalkFiles": True,
            "preserveSplitFiles": True,
            "copyWorkers": 4,
        }

    def test_meg_scan_default_config(self):
        config = get_default_stage_config("meg_scan")
        assert config == {
            "subjectIdTypeId": None,
            "subjectCsvMappingPath": None,
            "namingConvention": "natmeg",
            "requireCalibrationFiles": False,
        }

    def test_meg_bids_default_config_uses_cohort_name(self):
        config = get_default_stage_config("meg_bids", cohort_name="STOPMS")
        assert config == {
            "bidsRootName": "bids-meg",
            "overwriteMode": "skip",
            "datasetDescriptionName": "STOPMS",
            "convertWorkers": 4,
        }

    def test_default_stage_config_for_dicom_stages_unaffected(self):
        # Regression guard: adding MEG branches must not alter existing
        # DICOM stage default-config behavior (anonymize is checked first
        # in the if/elif chain but must still resolve correctly).
        config = get_default_stage_config("anonymize", cohort_name="STOPMS", source_path="/data/stopms")
        assert config is not None
        assert "patient_id" in config
