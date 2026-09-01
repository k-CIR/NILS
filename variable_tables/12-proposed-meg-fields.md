# Proposed MEG-only fields missing from current schema

| Proposed Variable | MR | CT | PET | MEG | BIDS |
|---|---:|---:|---:|---:|---|
| `meg_acquisition.fif_file_path` | — | — | — | ✓ | `filename` |
| `meg_acquisition.acquisition_date` | — | — | — | ✓ | `sub` |
| `meg_acquisition.device` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.sampling_frequency` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.n_channels` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.duration_seconds` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.highpass_hz` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.lowpass_hz` | — | — | — | ✓ | `sidecar` |
| `meg_acquisition.notch_filter_hz` | — | — | — | ✓ | `sidecar` |
| `meg_channel.channel_name` | — | — | — | ✓ | `channels.tsv` |
| `meg_channel.channel_type` | — | — | — | ✓ | `channels.tsv` |
| `meg_channel.unit` | — | — | — | ✓ | `channels.tsv` |
| `meg_channel.is_bad` | — | — | — | ✓ | `channels.tsv` |
| `meg_channel.location_x` | — | — | — | ✓ | `coordsystem/channels` |
| `meg_channel.location_y` | — | — | — | ✓ | `coordsystem/channels` |
| `meg_channel.location_z` | — | — | — | ✓ | `coordsystem/channels` |
| `meg_epoch.t_min` | — | — | — | ✓ | `events/epochs` |
| `meg_epoch.t_max` | — | — | — | ✓ | `events/epochs` |
| `meg_epoch.n_epochs` | — | — | — | ✓ | `events/epochs` |
