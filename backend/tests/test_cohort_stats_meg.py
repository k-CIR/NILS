"""Tests for MEG contribution to cohort stats (plan task 8b).

MEG cohorts never populate series/series_stack (see plan Decisions), so
``get_cohort_stats``/``get_all_cohort_stats`` must also count
``meg_acquisition`` rows when computing ``total_series``, and imaging
cohorts (which never populate ``meg_acquisition``) must be unaffected.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine

from cohorts.stats import get_all_cohort_stats, get_cohort_stats
from metadata_db import schema


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    schema.Base.metadata.create_all(engine)
    return engine


def _seed(engine, *, with_meg: bool, with_stacks: bool) -> None:
    with engine.begin() as conn:
        conn.execute(
            schema.Cohort.__table__.insert(),
            {"cohort_id": 1, "name": "megcohort", "owner": "tester", "path": "/tmp/megcohort"},
        )
        conn.execute(
            schema.Subject.__table__.insert(),
            {"subject_id": 1, "subject_code": "SUBJ001"},
        )
        conn.execute(
            schema.SubjectCohort.__table__.insert(),
            {"subject_id": 1, "cohort_id": 1},
        )
        conn.execute(
            schema.Study.__table__.insert(),
            {
                "study_id": 1,
                "study_instance_uid": "meg-study-uid-1",
                "study_date": date(2025, 6, 1),
                "modality": "MEG",
                "subject_id": 1,
            },
        )

        if with_meg:
            conn.execute(
                schema.MegAcquisition.__table__.insert(),
                [
                    {
                        "meg_acquisition_id": 1,
                        "study_id": 1,
                        "subject_id": 1,
                        "fif_file_path": "/data/sub-0001_task-rest_meg.fif",
                    },
                    {
                        "meg_acquisition_id": 2,
                        "study_id": 1,
                        "subject_id": 1,
                        "fif_file_path": "/data/sub-0001_task-motor_meg.fif",
                    },
                ],
            )

        if with_stacks:
            conn.execute(
                schema.Series.__table__.insert(),
                {
                    "series_id": 1,
                    "series_instance_uid": "series-uid-1",
                    "modality": "MR",
                    "study_id": 1,
                    "subject_id": 1,
                },
            )
            conn.execute(
                schema.SeriesStack.__table__.insert(),
                {
                    "series_stack_id": 1,
                    "series_id": 1,
                    "stack_modality": "MR",
                    "stack_index": 0,
                },
            )


class TestGetCohortStatsMeg:
    def test_meg_acquisitions_count_toward_total_series(self):
        engine = _make_engine()
        _seed(engine, with_meg=True, with_stacks=False)

        stats = get_cohort_stats("megcohort", engine=engine)

        assert stats["total_subjects"] == 1
        assert stats["total_sessions"] == 1
        assert stats["total_series"] == 2

    def test_imaging_cohort_without_meg_is_unaffected(self):
        engine = _make_engine()
        _seed(engine, with_meg=False, with_stacks=True)

        stats = get_cohort_stats("megcohort", engine=engine)

        assert stats["total_series"] == 1

    def test_stacks_and_meg_acquisitions_combine(self):
        engine = _make_engine()
        _seed(engine, with_meg=True, with_stacks=True)

        stats = get_cohort_stats("megcohort", engine=engine)

        assert stats["total_series"] == 3


class TestGetAllCohortStatsMeg:
    def test_meg_acquisitions_count_toward_total_series(self):
        engine = _make_engine()
        _seed(engine, with_meg=True, with_stacks=False)

        stats = get_all_cohort_stats(engine=engine)

        assert stats["megcohort"]["total_series"] == 2
