# Series table

> `series.*` is DICOM-series based, so MEG is not relevant in the current schema.

| Variable | MR | CT | PET | MEG | BIDS |
|---|---:|---:|---:|---:|---|
| `series.series_instance_uid` | ✓ | ✓ | ✓ | — | — |
| `series.frame_of_reference_uid` | ✓ | ✓ | ✓ | — | — |
| `series.implementation_class_uid` | ✓ | ✓ | ✓ | — | — |
| `series.media_storage_sop_instance_uid` | ✓ | ✓ | ✓ | — | — |
| `series.sop_class_uid` | ✓ | ✓ | ✓ | — | — |
| `series.implementation_version_name` | ✓ | ✓ | ✓ | — | — |
| `series.series_date` | ✓ | ✓ | ✓ | — | — |
| `series.series_time` | ✓ | ✓ | ✓ | — | — |
| `series.modality` | ✓ | ✓ | ✓ | — | `filename` |
| `series.image_type` | ✓ | ✓ | ✓ | — | `filename` |
| `series.sequence_name` | ✓ | — | — | — | — |
| `series.protocol_name` | ✓ | ✓ | ✓ | — | `custom` |
| `series.series_description` | ✓ | ✓ | ✓ | — | `custom` |
| `series.body_part_examined` | ✓ | ✓ | ✓ | — | `filename` |
| `series.scanning_sequence` | ✓ | — | — | — | — |
| `series.sequence_variant` | ✓ | — | — | — | — |
| `series.scan_options` | ✓ | — | — | — | — |
| `series.series_comments` | ✓ | ✓ | ✓ | — | — |
| `series.slice_thickness` | ✓ | ✓ | ✓ | — | — |
| `series.spacing_between_slices` | ✓ | ✓ | ✓ | — | — |
| `series.images_in_acquisition` | ✓ | ✓ | ✓ | — | — |
| `series.image_orientation_patient` | ✓ | ✓ | ✓ | — | — |
| `series.image_position_patient` | ✓ | ✓ | ✓ | — | — |
| `series.patient_position` | ✓ | ✓ | ✓ | — | — |
| `series.contrast_bolus_agent` | ✓ | ✓ | — | — | `filename` |
| `series.contrast_bolus_route` | ✓ | ✓ | — | — | — |
| `series.contrast_bolus_total_dose` | ✓ | ✓ | — | — | — |
| `series.contrast_bolus_start_time` | ✓ | ✓ | — | — | — |
| `series.contrast_bolus_volume` | ✓ | ✓ | — | — | — |
| `series.contrast_flow_rate` | ✓ | ✓ | — | — | — |
| `series.contrast_flow_duration` | ✓ | ✓ | — | — | — |
| `series.quality_control` | ✓ | ✓ | ✓ | — | — |
| `series.processing_status` | ✓ | ✓ | ✓ | — | — |
| `series.acquisition_compliance` | ✓ | ✓ | ✓ | — | — |
