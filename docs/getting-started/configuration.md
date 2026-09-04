# Configuration

NILS can be configured through environment variables and configuration files.

## Environment Variables

Configuration is managed through the `.env` file in the project root.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `API_URL` | `http://backend:8000` | Backend API URL (internal) |
| `DATABASE_URL` | `postgresql://...` | Main database connection |
| `METADATA_DATABASE_URL` | `postgresql://...` | Metadata database connection |
| `APP_ACCESS_TOKEN` | - | Access token for API authentication |

### Network Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `BIND_ADDRESS` | `127.0.0.1` | Address to bind services |
| `FRONTEND_PORT` | `5173` | Frontend web interface port |
| `BACKEND_PORT` | `8000` | Backend API port |

### Data Directories

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_MOUNT` | `/data` | Path to mount external data |
| `DB_DATA_DIR` | `./resource/db` | PostgreSQL data directory |
| `BACKUP_DIR` | `./resource/backups` | Backup storage location |

### Facility Vault Discovery (optional)

Only required to use the facility discovery scan/confirm endpoints
(auto-detecting `mrc`/`natmeg` facility subjects/sessions already on disk,
then confirming them into `project`/`cohort`/`subject` records). All
server-side paths, no secrets. None of these have a default value -- each
must be explicitly set to enable the corresponding endpoint(s); where a value
says "directory auto-created if missing", that describes what happens on
disk once the path is configured, not a fallback for leaving the variable
unset. See [Cohort](../cohort/index.md) docs for the discovery review
workflow.

| Variable | Default | Description |
|----------|---------|-------------|
| `FACILITY_VAULT_ROOT` | - | Root of the shared vault layout: `<root>/mrc/sub-<id>`, `<root>/natmeg/<project>/raw/sub-<id>/<date>` (an optional extra `<acquisition>` directory between `raw` and `sub-<id>` is also supported). Required for `POST /api/facility-discovery/scan`. |
| `FACILITY_MAPPING_CSV_PATH` | - | Facility-maintained cross-facility subject/project mapping CSV; fully reloaded on every scan (no upload endpoint in v1). Required for `POST /api/facility-discovery/scan`. |
| `MRC_STAGING_ROOT` | - | Per-project symlink staging root for confirmed `mrc` subjects (directory auto-created if missing). Required to confirm an `mrc` discovery; not used by `natmeg`. |
| `FACILITY_SUBJECT_CODE_CSV_ROOT` | - | Directory of generated per-cohort subject-code override CSVs (directory auto-created if missing). NILS writes one CSV per cohort here mapping the raw facility identifier to the CIR subject code, wired into the existing extract/meg_scan CSV-override mechanism so extraction uses the CIR ID instead of the raw facility identifier -- this is NILS's own generated output, distinct from `FACILITY_MAPPING_CSV_PATH`. Required to confirm a discovery of either facility. |

## Example Configuration

```bash
# .env file

# API Configuration
API_URL=http://backend:8000
APP_ACCESS_TOKEN=your-secure-token-here

# Database
DATABASE_URL=postgresql://nils:nils@db:5432/nils
METADATA_DATABASE_URL=postgresql://nils:nils@db_metadata:5432/metadata

# Network (localhost only by default)
BIND_ADDRESS=127.0.0.1
FRONTEND_PORT=5173
BACKEND_PORT=8000

# Data paths
DATA_MOUNT=/path/to/your/dicom/data
```

## Network Access

By default, NILS binds to `127.0.0.1` (localhost only). To allow network access:

```bash
# Enable network access
./scripts/manage.sh start --forward
```

!!! warning "Security"
    Enabling network access exposes NILS to your network. Ensure proper firewall rules and authentication are in place.

## Database Configuration

NILS uses two PostgreSQL databases:

1. **Main Database** - Application data (cohorts, jobs, settings)
2. **Metadata Database** - DICOM metadata and classification results

Both databases are managed by Docker and persist data in the configured directories.

### Backup and Restore

```bash
# Create backup
./scripts/manage.sh backup

# Restore from backup
./scripts/manage.sh restore backup-file.sql
```

## Classification Rules

Classification rules are configured via YAML files in `backend/src/classification/detection_yaml/`:

- `base-detection.yaml` - Core sequence types
- `technique-detection.yaml` - Acquisition techniques
- `modifier-detection.yaml` - Sequence modifiers
- `contrast-detection.yaml` - Contrast agent detection

See [Detection Infrastructure](../classification/foundations.md) for detailed configuration.

## Resource Limits

Docker Compose applies resource limits to prevent runaway processes:

```yaml
# docker-compose.yml
services:
  frontend:
    deploy:
      resources:
        limits:
          memory: 2G
```

Adjust these limits based on your system resources.
