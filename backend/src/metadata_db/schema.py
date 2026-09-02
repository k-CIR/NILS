"""ORM models for the metadata database core tables."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, Double, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Subject(Base):
    __tablename__ = "subject"

    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    patient_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    patient_sex: Mapped[str | None] = mapped_column(Text, nullable=True)
    ethnic_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    occupation: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_patient_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class Cohort(Base):
    __tablename__ = "cohort"

    cohort_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class SubjectCohort(Base):
    __tablename__ = "subject_cohorts"
    __table_args__ = (UniqueConstraint("subject_id", "cohort_id", name="uq_subject_cohort"),)

    subject_cohort_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cohort_id: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class IdType(Base):
    __tablename__ = "id_types"
    __table_args__ = (UniqueConstraint("id_type_name", name="uq_id_types_name"),)

    id_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_type_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SubjectOtherIdentifier(Base):
    __tablename__ = "subject_other_identifiers"

    subject_other_identifier_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    id_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    other_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class ObservationType(Base):
    __tablename__ = "observation_types"

    observation_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        UniqueConstraint("subject_id", "observation_type_id", "event_date", name="uq_event_subject_type_date"),
    )

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class Disease(Base):
    __tablename__ = "diseases"

    disease_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disease_name: Mapped[str] = mapped_column(Text, nullable=False)
    disease_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class DiseaseType(Base):
    __tablename__ = "disease_types"

    disease_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disease_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class SubjectDisease(Base):
    __tablename__ = "subject_diseases"
    __table_args__ = (UniqueConstraint("subject_id", "disease_id", name="uq_subject_disease"),)

    subject_disease_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    disease_id: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnosis_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    family_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    onset_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnosis_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class SubjectDiseaseType(Base):
    __tablename__ = "subject_disease_types"

    subject_disease_type_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_disease_id: Mapped[int] = mapped_column(Integer, nullable=False)
    disease_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    assignment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    transition_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class NumericMeasure(Base):
    __tablename__ = "numeric_measures"

    measure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    numeric_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class TextMeasure(Base):
    __tablename__ = "text_measures"

    measure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class BooleanMeasure(Base):
    __tablename__ = "boolean_measures"

    measure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    boolean_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class JsonMeasure(Base):
    __tablename__ = "json_measures"

    measure_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    json_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_system: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class Study(Base):
    __tablename__ = "study"
    study_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    study_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    study_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    study_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    study_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    modality: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer_model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    station_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    institution_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subject.subject_id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_control: Mapped[str | None] = mapped_column(Text, nullable=True)


class Series(Base):
    __tablename__ = "series"
    series_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    frame_of_reference_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_class_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_storage_sop_instance_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    sop_class_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_version_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    series_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    image_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    protocol_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_part_examined: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanning_sequence: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_options: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    slice_thickness: Mapped[float | None] = mapped_column(Float, nullable=True)
    spacing_between_slices: Mapped[float | None] = mapped_column(Float, nullable=True)
    images_in_acquisition: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_orientation_patient: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_position_patient: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    contrast_bolus_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    contrast_bolus_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    contrast_bolus_total_dose: Mapped[float | None] = mapped_column(Float, nullable=True)
    contrast_bolus_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    contrast_bolus_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    contrast_flow_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    contrast_flow_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("study.study_id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subject.subject_id", ondelete="CASCADE"), nullable=False
    )
    quality_control: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_compliance: Mapped[str | None] = mapped_column(Text, nullable=True)


class MRISeriesDetails(Base):
    __tablename__ = "mri_series_details"
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.series_id", ondelete="CASCADE"), primary_key=True
    )
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mr_acquisition_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    angio_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    repetition_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    echo_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    inversion_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    inversion_times: Mapped[str | None] = mapped_column(Text, nullable=True)
    flip_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    phase_contrast: Mapped[str | None] = mapped_column(Text, nullable=True)
    number_of_averages: Mapped[float | None] = mapped_column(Float, nullable=True)
    imaging_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    imaged_nucleus: Mapped[str | None] = mapped_column(Text, nullable=True)
    echo_numbers: Mapped[str | None] = mapped_column(Text, nullable=True)
    magnetic_field_strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    number_of_phase_encoding_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    echo_train_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent_sampling: Mapped[float | None] = mapped_column(Float, nullable=True)
    percent_phase_field_of_view: Mapped[float | None] = mapped_column(Float, nullable=True)
    pixel_bandwidth: Mapped[str | None] = mapped_column(Text, nullable=True)
    receive_coil_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    transmit_coil_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    acquisition_matrix: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase_encoding_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    sar: Mapped[float | None] = mapped_column(Float, nullable=True)
    dbdt: Mapped[str | None] = mapped_column(Text, nullable=True)
    b1rms: Mapped[str | None] = mapped_column(Text, nullable=True)
    temporal_position_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    number_of_temporal_positions: Mapped[str | None] = mapped_column(Text, nullable=True)
    temporal_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    diffusion_b_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    diffusion_gradient_orientation: Mapped[str | None] = mapped_column(Text, nullable=True)
    diffusion_directionality: Mapped[str | None] = mapped_column(Text, nullable=True)
    parallel_acquisition_technique: Mapped[str | None] = mapped_column(Text, nullable=True)
    parallel_reduction_factor_in_plane: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DWI private tag raw values (manufacturer-specific, populated during extraction)
    dwi_siemens_b_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwi_siemens_directionality: Mapped[str | None] = mapped_column(Text, nullable=True)
    dwi_siemens_pe_dir_positive: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwi_ge_b_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwi_ge_n_directions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dwi_philips_b_value: Mapped[float | None] = mapped_column(Float, nullable=True)


class CTSeriesDetails(Base):
    __tablename__ = "ct_series_details"
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.series_id", ondelete="CASCADE"), primary_key=True
    )
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kvp: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_collection_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    reconstruction_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    gantry_detector_tilt: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    rotation_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    exposure_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_ray_tube_current: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure: Mapped[float | None] = mapped_column(Float, nullable=True)
    filter_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    focal_spots: Mapped[str | None] = mapped_column(Text, nullable=True)
    convolution_kernel: Mapped[str | None] = mapped_column(Text, nullable=True)
    revolution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    single_collimation_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_collimation_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_feed_per_rotation: Mapped[float | None] = mapped_column(Float, nullable=True)
    spiral_pitch_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure_modulation_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctdi_vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctdi_phantom_type_code_sequence: Mapped[str | None] = mapped_column(Text, nullable=True)
    calcium_scoring_mass_factor_device: Mapped[float | None] = mapped_column(Float, nullable=True)
    calcium_scoring_mass_factor_patient: Mapped[float | None] = mapped_column(Float, nullable=True)


class PETSeriesDetails(Base):
    __tablename__ = "pet_series_details"
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.series_id", ondelete="CASCADE"), primary_key=True
    )
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    radiopharmaceutical: Mapped[str | None] = mapped_column(Text, nullable=True)
    radionuclide_total_dose: Mapped[float | None] = mapped_column(Float, nullable=True)
    radionuclide_half_life: Mapped[float | None] = mapped_column(Float, nullable=True)
    radionuclide_positron_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    radiopharmaceutical_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    radiopharmaceutical_stop_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    radiopharmaceutical_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    radiopharmaceutical_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    decay_correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    decay_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    reconstruction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    scatter_correction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    attenuation_correction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    randoms_correction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    dose_calibration_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    activity_concentration_scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    suv_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    suvbw: Mapped[float | None] = mapped_column(Float, nullable=True)
    suvlbm: Mapped[float | None] = mapped_column(Float, nullable=True)
    suvbsa: Mapped[float | None] = mapped_column(Float, nullable=True)
    counts_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    units: Mapped[str | None] = mapped_column(Text, nullable=True)
    frame_reference_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_frame_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    patient_gantry_relationship_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    slice_progression_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    units_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    counts_included: Mapped[str | None] = mapped_column(Text, nullable=True)


class Instance(Base):
    __tablename__ = "instance"
    instance_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # series_id is nullable to support instance-first insertion pattern:
    # 1. Insert instance with series_id=NULL
    # 2. Create parent records (subject/study/series) only for inserted instances
    # 3. Update instance with correct series_id
    # This prevents orphan parent records when instance insert fails (duplicate SOP)
    series_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("series.series_id", ondelete="CASCADE"), nullable=True
    )
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False)
    sop_instance_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    instance_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acquisition_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    content_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    slice_location: Mapped[float | None] = mapped_column(Float, nullable=True)
    pixel_spacing: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bits_allocated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bits_stored: Mapped[int | None] = mapped_column(Integer, nullable=True)
    high_bit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pixel_representation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_center: Mapped[str | None] = mapped_column(Text, nullable=True)
    window_width: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescale_intercept: Mapped[float | None] = mapped_column(Float, nullable=True)
    rescale_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    number_of_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lossy_image_compression: Mapped[str | None] = mapped_column(Text, nullable=True)
    derivation_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_syntax_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    dicom_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_control: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to series_stack (set during extraction, after parent creation)
    series_stack_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("series_stack.series_stack_id", ondelete="SET NULL"), nullable=True
    )


class SeriesStack(Base):
    """Represents a homogeneous stack of instances within a SeriesInstanceUID."""
    __tablename__ = "series_stack"
    __table_args__ = (
        UniqueConstraint("series_id", "stack_index", name="uq_series_stack_index"),
    )

    series_stack_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series.series_id", ondelete="CASCADE"), nullable=False
    )
    stack_modality: Mapped[str] = mapped_column(Text, nullable=False)
    stack_index: Mapped[int] = mapped_column(Integer, nullable=False)
    stack_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # MR stack-defining fields
    stack_inversion_time: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_echo_time: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_echo_numbers: Mapped[str | None] = mapped_column(Text, nullable=True)  # Backslash-separated
    stack_echo_train_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stack_repetition_time: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_flip_angle: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_receive_coil_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_image_orientation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Orientation confidence score (0.0-1.0, computed from image_orientation_patient)
    # Lower values indicate oblique orientations requiring manual review
    stack_orientation_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    stack_image_type: Mapped[str | None] = mapped_column(Text, nullable=True)

    # CT stack-defining fields
    stack_xray_exposure: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_kvp: Mapped[float | None] = mapped_column(Double, nullable=True)
    stack_tube_current: Mapped[float | None] = mapped_column(Double, nullable=True)

    # PET stack-defining fields
    stack_pet_bed_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stack_pet_frame_type: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Aggregates
    stack_n_instances: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StackFingerprint(Base):
    """
    A flattened, feature-rich representation of a SeriesStack designed for 
    classification algorithms. 
    
    It normalizes specific physics parameters across modalities and aggregates 
    text fields into a single searchable blob.
    """
    __tablename__ = "stack_fingerprint"

    fingerprint_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # 1:1 relationship with SeriesStack
    series_stack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("series_stack.series_stack_id", ondelete="CASCADE"), 
        nullable=False, unique=True
    )

    # --- GENERAL METADATA ---
    # Comes from series_stack table
    modality: Mapped[str] = mapped_column(String(16), nullable=False)
    # Comes from study table, normalized via fuzzy matching to: GE, SIEMENS, PHILIPS, CANON, FUJI, HITACHI
    manufacturer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from study table
    manufacturer_model: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- TEXT FEATURES ---
    # Comes from series table
    stack_sequence_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Concatenation of: SeriesDescription + ProtocolName + SeriesComments + ImageComments + BodyPartExamined
    # Lowercased and stripped of special characters for easy NLP/Keyword lookup.
    text_search_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Concat info for contrast: contrast_bolus_agent, contrast_bolus_route, etc.
    contrast_search_blob: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- GEOMETRY & DIMENSIONS ---
    # Comes from series_stack table (already categorical: Axial/Coronal/Sagittal)
    stack_orientation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Computed from instance pixel_spacing * columns (mm)
    fov_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Computed from instance pixel_spacing * rows (mm)
    fov_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Computed: max(fov_x, fov_y) / min(fov_x, fov_y)
    aspect_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- GENERAL SERIES FEATURES ---
    # Comes from series_stack table
    image_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from series table
    scanning_sequence: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_variant: Mapped[str | None] = mapped_column(Text, nullable=True)
    scan_options: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- MR SPECIFIC FEATURES ---
    # All from series_stack table, normalized to milliseconds (ms)
    mr_te: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_tr: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_ti: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_flip_angle: Mapped[float | None] = mapped_column(Float, nullable=True)
    mr_echo_train_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mr_echo_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from mri_series_details table, normalized to "2D" or "3D"
    mr_acquisition_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Comes from mri_series_details table, normalized to "Y" or "N"
    mr_angio_flag: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # Comes from mri_series_details table
    mr_b1rms: Mapped[str | None] = mapped_column(Text, nullable=True)
    mr_diffusion_b_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    mr_parallel_acquisition_technique: Mapped[str | None] = mapped_column(Text, nullable=True)
    mr_temporal_position_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from mri_series_details table, normalized to "Y" or "N"
    mr_phase_contrast: Mapped[str | None] = mapped_column(String(1), nullable=True)

    # --- CT SPECIFIC FEATURES ---
    # Comes from series_stack table
    ct_kvp: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Comes from ct_series_details table
    ct_exposure_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Comes from series_stack table
    ct_tube_current: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Comes from ct_series_details table
    ct_convolution_kernel: Mapped[str | None] = mapped_column(Text, nullable=True)
    ct_revolution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    ct_filter_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Derived: True if calcium_scoring_mass_factor_patient is not NULL
    ct_is_calcium_score: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- PET SPECIFIC FEATURES ---
    # Comes from series_stack table
    pet_bed_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pet_frame_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from pet_series_details table, normalized via fuzzy matching
    pet_tracer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Comes from pet_series_details table
    pet_reconstruction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_suv_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_units: Mapped[str | None] = mapped_column(String(32), nullable=True)  # BQML, CNTS, GML
    pet_units_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_series_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_corrected_image: Mapped[str | None] = mapped_column(Text, nullable=True)  # DECY, ATTN, SCAT, etc.
    pet_counts_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Derived: True if attenuation_correction_method is not NULL
    pet_is_attenuation_corrected: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pet_radionuclide_total_dose: Mapped[float | None] = mapped_column(Float, nullable=True)
    pet_radionuclide_half_life: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Number of slices in the stack - comes from series_stack table
    stack_n_instances: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class SeriesClassificationCache(Base):
    __tablename__ = "series_classification_cache"

    series_stack_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_instance_uid: Mapped[str] = mapped_column(Text, nullable=False)
    dicom_origin_cohort: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_string: Mapped[str | None] = mapped_column(Text, nullable=True)
    unique_series_under_string: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fov_x_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    fov_y_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    aspect_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    slices_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pixsp_row_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pixsp_col_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Through-plane resolution. Populated by Sort Step 3 from
    # COALESCE(series.spacing_between_slices, series.slice_thickness).
    slice_thickness_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    orientation_patient: Mapped[str | None] = mapped_column(Text, nullable=True)
    echo_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    directory_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    base: Mapped[str | None] = mapped_column(Text, nullable=True)
    modifier_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    technique: Mapped[str | None] = mapped_column(Text, nullable=True)
    construct_csv: Mapped[str | None] = mapped_column(Text, nullable=True)  # Renamed from derived_csv
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)  # Single value, not CSV
    acceleration_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_contrast: Mapped[int | None] = mapped_column(Integer, nullable=True)
    localizer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spinal_cord: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_part: Mapped[str | None] = mapped_column(Text, nullable=True)
    study_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_review_required: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_review_reasons_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DWI enrichment — computed per-stack during Sort Step 4
    dwi_b_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    dwi_b_values_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    dwi_pe_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    dwi_n_directions: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IngestConflict(Base):
    __tablename__ = "ingest_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cohort_id: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class MegAcquisition(Base):
    """A single MEG recording (raw .fif file, with split continuations merged).

    Plays the role `series`/`*_series_details` play for DICOM modalities, but
    MEG sessions reuse the shared `study` table (with `modality="MEG"`)
    instead of `series`/`instance`/`series_stack`/`stack_fingerprint`, since
    those DICOM-shaped tables don't map cleanly onto MEG acquisitions (see
    plan Decisions). Column set follows
    `variable_tables/12-proposed-meg-fields.md` plus the infra columns
    (FKs, upsert key, timestamps) every other modality-details table needs.

    `meg_epoch` (also proposed in that doc) is intentionally NOT created as a
    table yet: phase 1 stages only handle continuous raw data, so there is no
    producer of epoch-level rows until `meg_qc`/`meg_maxfilter` land.
    """

    __tablename__ = "meg_acquisition"
    __table_args__ = (
        # Upsert key for meg_scan re-runs (mirrors series_instance_uid unique
        # on Series/`*SeriesDetails`) — MEG has no native DICOM-style UID, so
        # (study_id, fif_file_path) identifies "the same recording" instead.
        UniqueConstraint("study_id", "fif_file_path", name="uq_meg_acquisition_study_fif_path"),
    )

    meg_acquisition_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("study.study_id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subject.subject_id", ondelete="CASCADE"), nullable=False
    )

    # Raw session label as parsed/resolved before subject/session identity
    # resolution (see meg.parsing.bids_path_from_rawname's session_label arg).
    session_label: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source file
    fif_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Number of split (`-1.fif`, `-2.fif`, ...) continuation files merged into
    # this single logical acquisition (see meg.parsing.get_split_file_parts).
    split_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # BIDS entities parsed from the source filename (see meg.parsing), stored
    # so the meg_bids stage can reconstruct the exact BIDSPath without
    # re-parsing the raw filename. Named bids_* (not e.g. `acquisition`) to
    # avoid colliding with the DICOM sense of "acquisition" used elsewhere.
    bids_task: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_run: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_acq_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_processing_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_datatype: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Recording metadata (proposed MEG fields — see variable_tables/12-proposed-meg-fields.md)
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    device: Mapped[str | None] = mapped_column(Text, nullable=True)
    sampling_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    highpass_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowpass_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Comma-separated list of notch frequencies (MNE reports these as an array).
    notch_filter_hz: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Written back by the meg_bids stage (task 9): conversion status
    # ("run"/"check"/"processed"/"skip"/"missing"/"error", see
    # meg.conversion_table.CONVERSION_COLUMNS's "status" column) plus the
    # resulting BIDS location.
    bids_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    bids_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=_utc_now_iso,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
    )


class MegChannel(Base):
    """A single channel (sensor) within a MEG acquisition. See channels.tsv in BIDS."""

    __tablename__ = "meg_channel"
    __table_args__ = (
        UniqueConstraint("meg_acquisition_id", "channel_name", name="uq_meg_channel_acquisition_name"),
    )

    meg_channel_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meg_acquisition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("meg_acquisition.meg_acquisition_id", ondelete="CASCADE"), nullable=False
    )
    channel_name: Mapped[str] = mapped_column(Text, nullable=False)
    channel_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bad: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    location_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_z: Mapped[float | None] = mapped_column(Float, nullable=True)
