# Series stack table

| Variable | MR | CT | PET | MEG | BIDS |
|---|---:|---:|---:|---:|---|
| `series_stack.stack_modality` | ✓ | ✓ | ✓ | — | `filename` |
| `series_stack.stack_index` | ✓ | ✓ | ✓ | — | `filename` |
| `series_stack.stack_key` | ✓ | ✓ | ✓ | — | `filename` |
| `series_stack.stack_inversion_time` | ✓ | — | — | — | `filename+sidecar` |
| `series_stack.stack_echo_time` | ✓ | — | — | — | `filename+sidecar` |
| `series_stack.stack_echo_numbers` | ✓ | — | — | — | `filename` |
| `series_stack.stack_echo_train_length` | ✓ | — | — | — | — |
| `series_stack.stack_repetition_time` | ✓ | — | — | — | `sidecar` |
| `series_stack.stack_flip_angle` | ✓ | — | — | — | `sidecar` |
| `series_stack.stack_receive_coil_name` | ✓ | — | — | — | `sidecar` |
| `series_stack.stack_image_orientation` | ✓ | ✓ | ✓ | — | — |
| `series_stack.stack_orientation_confidence` | ✓ | ✓ | ✓ | — | — |
| `series_stack.stack_image_type` | ✓ | ✓ | ✓ | — | `filename` |
| `series_stack.stack_xray_exposure` | — | ✓ | — | — | — |
| `series_stack.stack_kvp` | — | ✓ | — | — | — |
| `series_stack.stack_tube_current` | — | ✓ | — | — | — |
| `series_stack.stack_pet_bed_index` | — | — | ✓ | — | — |
| `series_stack.stack_pet_frame_type` | — | — | ✓ | — | — |
| `series_stack.stack_n_instances` | ✓ | ✓ | ✓ | — | — |
