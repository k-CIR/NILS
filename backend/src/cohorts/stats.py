"""Functions to compute cohort statistics from the metadata database."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


def _compute_stats_for_cohort_id(conn: Connection, cohort_id: int) -> dict[str, int]:
    """Compute subject/session/series counts for one `subject_cohorts.cohort_id`
    value. Extracted so both the metadata-DB-native id (imaging cohorts, see
    `extract.writer._ensure_cohort`) and the app-DB `cohorts.id` value (MEG
    cohorts, see `get_cohort_stats`'s fallback) can be queried identically."""
    # Count subjects in this cohort
    subjects_result = conn.execute(
        text("""
            SELECT COUNT(DISTINCT subject_id) 
            FROM subject_cohorts 
            WHERE cohort_id = :cohort_id
        """),
        {"cohort_id": cohort_id},
    ).fetchone()

    total_subjects = subjects_result[0] if subjects_result else 0

    # Count sessions for subjects in this cohort. A "session" is one
    # calendar visit per subject — keyed by ``(subject_id, study_date)``
    # — because PACS often splits one visit into multiple
    # StudyInstanceUIDs (e.g. brain study + spine study), and those
    # should be counted as one session, not two.
    sessions_result = conn.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT s.subject_id, s.study_date
                FROM study s
                INNER JOIN subject_cohorts sc ON s.subject_id = sc.subject_id
                WHERE sc.cohort_id = :cohort_id
                  AND s.study_date IS NOT NULL
            ) t
        """),
        {"cohort_id": cohort_id},
    ).fetchone()

    total_sessions = sessions_result[0] if sessions_result else 0

    # Count stacks (series_stack) for subjects in this cohort
    stacks_result = conn.execute(
        text("""
            SELECT COUNT(DISTINCT ss.series_stack_id)
            FROM series_stack ss
            INNER JOIN series s ON ss.series_id = s.series_id
            INNER JOIN study st ON s.study_id = st.study_id
            INNER JOIN subject_cohorts sc ON st.subject_id = sc.subject_id
            WHERE sc.cohort_id = :cohort_id
        """),
        {"cohort_id": cohort_id},
    ).fetchone()

    total_stacks = stacks_result[0] if stacks_result else 0

    # Count MEG acquisitions for subjects in this cohort. MEG cohorts
    # never populate series/series_stack (see plan Decisions), so their
    # "series" figure comes entirely from meg_acquisition instead;
    # imaging cohorts never populate meg_acquisition, so summing both
    # counts is safe for either modality.
    meg_result = conn.execute(
        text("""
            SELECT COUNT(*)
            FROM meg_acquisition ma
            INNER JOIN subject_cohorts sc ON ma.subject_id = sc.subject_id
            WHERE sc.cohort_id = :cohort_id
        """),
        {"cohort_id": cohort_id},
    ).fetchone()

    total_meg_acquisitions = meg_result[0] if meg_result else 0

    return {
        "total_subjects": total_subjects,
        "total_sessions": total_sessions,
        # Mapping stacks + MEG acquisition counts to total_series field
        "total_series": total_stacks + total_meg_acquisitions,
    }


def get_cohort_stats(
    cohort_name: str, *, engine: Engine, fallback_cohort_id: int | None = None
) -> dict[str, int]:
    """
    Get subject and session counts for a cohort from the metadata database.

    `subject_cohorts.cohort_id` is populated with two different id spaces
    depending on which pipeline wrote it: the DICOM `extract` stage
    lazily find-or-creates its own row in this metadata database's `cohort`
    table (`extract.writer._ensure_cohort`) and uses *that* table's own
    `cohort_id`, while the MEG `meg_scan` stage and facility-discovery
    confirm flow both use the app database's `cohorts.id` directly and never
    create a matching `cohort` row here. Resolve by name first (the
    long-standing imaging convention); if that yields no subjects, fall back
    to treating `fallback_cohort_id` (the app-DB id, when the caller has it)
    as the `subject_cohorts.cohort_id` directly -- this is what makes MEG/
    facility-discovery cohorts report real counts instead of always 0.

    Args:
        cohort_name: The normalized cohort name to look up
        engine: SQLAlchemy engine for the metadata database
        fallback_cohort_id: The app-DB `cohorts.id`, used as a fallback
            `subject_cohorts.cohort_id` when no name-matched row exists (or
            that row has no linked subjects)

    Returns:
        Dictionary with 'total_subjects', 'total_sessions', and
        'total_series' counts
    """
    with engine.connect() as conn:
        # Get cohort_id from metadata database
        cohort_result = conn.execute(
            text("SELECT cohort_id FROM cohort WHERE LOWER(name) = LOWER(:name)"),
            {"name": cohort_name},
        ).fetchone()

        stats = (
            _compute_stats_for_cohort_id(conn, cohort_result[0])
            if cohort_result
            else {"total_subjects": 0, "total_sessions": 0, "total_series": 0}
        )

        if stats["total_subjects"] == 0 and fallback_cohort_id is not None:
            if not cohort_result or cohort_result[0] != fallback_cohort_id:
                fallback_stats = _compute_stats_for_cohort_id(conn, fallback_cohort_id)
                if fallback_stats["total_subjects"] > 0:
                    return fallback_stats

        return stats


def get_all_cohort_stats(
    *, engine: Engine, fallback_cohort_ids: dict[str, int] | None = None
) -> dict[str, dict[str, int]]:
    """
    Get subject, session, and stack counts for all cohorts.

    Performance optimized: Uses scalar subqueries instead of multiple LEFT JOINs
    to avoid Cartesian product explosion. Each subquery filters by cohort_id
    and aggregates independently, preventing row multiplication.

    Args:
        engine: SQLAlchemy engine for the metadata database
        fallback_cohort_ids: Optional mapping of lowercased cohort name to the
            app-DB `cohorts.id`, used the same way as `get_cohort_stats`'s
            `fallback_cohort_id` for any cohort whose name-matched stats come
            back with 0 subjects (see `get_cohort_stats`'s docstring for why
            MEG/facility-discovery cohorts need this). Only queried for that
            small subset, so the bulk query above stays the fast path.

    Returns:
        Dictionary mapping cohort names (lowercase) to stats dicts
    """
    with engine.connect() as conn:
        # Optimized query using scalar subqueries instead of JOIN + COUNT(DISTINCT)
        # This avoids Cartesian products when cohorts have many series/stacks.
        # ``session_count`` is the number of distinct (subject_id, study_date)
        # pairs in the cohort — see :func:`get_cohort_stats` for the rationale.
        results = conn.execute(
            text("""
                SELECT
                    c.name,
                    c.cohort_id,
                    (SELECT COUNT(*)
                     FROM subject_cohorts sc
                     WHERE sc.cohort_id = c.cohort_id) as subject_count,
                    (SELECT COUNT(*) FROM (
                        SELECT DISTINCT st.subject_id, st.study_date
                        FROM study st
                        JOIN subject_cohorts sc2 ON st.subject_id = sc2.subject_id
                        WHERE sc2.cohort_id = c.cohort_id
                          AND st.study_date IS NOT NULL
                     ) t) as session_count,
                    (SELECT COUNT(*)
                     FROM series_stack ss
                     JOIN series s ON ss.series_id = s.series_id
                     JOIN study st2 ON s.study_id = st2.study_id
                     JOIN subject_cohorts sc3 ON st2.subject_id = sc3.subject_id
                     WHERE sc3.cohort_id = c.cohort_id) as stack_count,
                    (SELECT COUNT(*)
                     FROM meg_acquisition ma
                     JOIN subject_cohorts sc4 ON ma.subject_id = sc4.subject_id
                     WHERE sc4.cohort_id = c.cohort_id) as meg_acquisition_count
                FROM cohort c
            """)
        ).fetchall()

        stats = {}
        matched_ids_by_name: dict[str, int] = {}
        for row in results:
            cohort_name = row[0].lower() if row[0] else ""
            matched_ids_by_name[cohort_name] = row[1]
            stats[cohort_name] = {
                "total_subjects": row[2] or 0,
                "total_sessions": row[3] or 0,
                # Stacks (DICOM) + MEG acquisitions combined into one "series" figure.
                "total_series": (row[4] or 0) + (row[5] or 0),
            }

        if fallback_cohort_ids:
            for cohort_name, fallback_id in fallback_cohort_ids.items():
                current = stats.get(cohort_name)
                if current is not None and current["total_subjects"] > 0:
                    continue
                if matched_ids_by_name.get(cohort_name) == fallback_id:
                    continue
                fallback_stats = _compute_stats_for_cohort_id(conn, fallback_id)
                if fallback_stats["total_subjects"] > 0:
                    stats[cohort_name] = fallback_stats

        return stats
