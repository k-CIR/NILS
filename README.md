# NILS - Neuroimaging Intelligent Linked System

<p align="center">
  <img src="frontend/public/nils-icon.svg" alt="NILS Logo" width="120">
</p>

<p align="center">
  <strong>A comprehensive system for DICOM classification, sorting, anonymization, and BIDS export</strong>
</p>

<p align="center">
  <b>Developed at <a href="https://ki.se">Karolinska Institutet</a></b><br>
  Department of Clinical Neuroscience, Neuroradiology
</p>

<p align="center">
  <a href="https://neurogranberg.github.io/NILS/">
    <img src="https://img.shields.io/badge/docs-neurogranberg.github.io-blue" alt="Documentation">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3">
  </a>
  <a href="CHANGELOG.md">
    <img src="https://img.shields.io/badge/version-0.5.3-green.svg" alt="Version">
  </a>
</p>

---

## Features

### Six-Axis Classification System

NILS classifies MRI series using six orthogonal axes, each backed by its own YAML-driven detector:

| Axis | Description | Examples |
|------|-------------|----------|
| **Base** | Contrast weighting | T1w, T2w, PD, DWI, BOLD, SWI |
| **Technique** | Pulse sequence family | MPRAGE, TSE, FLASH, EPI, GRASE |
| **Modifier** | Acquisition enhancements | FLAIR, FatSat, MT, IR, PhaseContrast |
| **Construct** | Derived/map type | ADC, FA, T1Map, QSM, CBF, MyelinMap |
| **Provenance** | Processing pipeline | SyMRI, SWIRecon, DTIRecon, EPIMix |
| **Acceleration** | Parallel imaging | GRAPPA, SMS, CAIPIRINHA, CompressedSensing |

Specialized branch pipelines handle multi-output acquisitions (SWI, SyMRI, EPIMix/NeuroMix, MP2RAGE) — provenance detection runs first and routes to the correct branch.

### Complete Pipeline

- **Extraction** — Import DICOM metadata with adaptive batching and per-instance stack creation
- **Sorting** — 4-step pipeline: Checkup → Stack Fingerprint → Classification → Completion
- **QC Review** — Draft-based quality control with Cornerstone.js DICOM viewer and rules engine
- **Anonymization** — De-identify with configurable ID strategies and compression
- **BIDS Export** — Self-describing filenames, cross-cohort resolution, field strength filtering

### Clinical Data Management

- CSV import for subjects, events, diseases, observation types, and identifiers
- Longitudinal tracking: link imaging sessions to diagnoses and clinical timelines
- Preview/validate before applying any import

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- 4GB RAM minimum (8GB recommended)
- Prefer no Docker? See the [Native Setup](#native-docker-free-setup) section below.

### Start NILS

```bash
# Clone the repository
git clone https://github.com/NeuroGranberg/NILS.git
cd NILS

# Start services with your DICOM data
./scripts/manage.sh start --data /path/to/your/dicom/data

# Access the web interface
open http://localhost:5173
```

### Network Options

| Mode | Command | Access |
|------|---------|--------|
| **Default** | `start` | Localhost only (secure) |
| External | `start --forward` | Network/Tailscale accessible |

---

## Documentation

Full documentation available at: **[neurogranberg.github.io/NILS](https://neurogranberg.github.io/NILS/)**

- [**Concepts**](https://neurogranberg.github.io/NILS/concepts/) - Core data models and terminology
- [**Cohort Operations**](https://neurogranberg.github.io/NILS/cohort/) - Extraction, Sorting, Anonymization, Export
- [**Classification**](https://neurogranberg.github.io/NILS/classification/) - The six-axis detection system
- [**QC & Viewer**](https://neurogranberg.github.io/NILS/qc/) - Quality control and image review

---

## Usage

### Options

| Option | Description |
|--------|-------------|
| `--data PATH` | Mount DICOM directory (can specify multiple) |
| `--forward` | Expose ports externally (default: localhost only) |
| `--clean` | Remove containers and volumes before starting |
| `--db-dir PATH` | Override database storage directory |
| `--podman` | Use Podman instead of Docker (adds `:Z` SELinux labels) |

### Examples

```bash
# Start with localhost access (default - secure)
./scripts/manage.sh start --data /srv/dicom

# Mount multiple data directories
./scripts/manage.sh start \
  --data /srv/dicom/ct \
  --data /srv/dicom/mr

# Clean start with network access
./scripts/manage.sh start --clean --forward --data /srv/dicom

# Stop services
./scripts/manage.sh stop
```

### Remote Access

**SSH tunnel (recommended for default mode):**
```bash
ssh -L 5173:localhost:5173 user@server
# Then open http://localhost:5173 locally
```

**Tailscale (with `--forward` mode):**
```
http://your-server.ts.net:5173
```

---

## Configuration

Environment variables in `.env`:

| Variable | Description |
|----------|-------------|
| `APP_ACCESS_TOKEN` | Secret key for login protection |
| `DB_DATA_DIR` | Database storage directory |
| `METADATA_DB_DATA_DIR` | Metadata database directory |

---

## Architecture

```
┌───────────────────────────────────────────────────────┐
│                   Docker Network                       │
│  ┌──────────┐  ┌───────────┐  ┌───────────────────┐  │
│  │    db    │  │ metadata  │  │     backend       │  │
│  │ postgres │  │    db     │  │  FastAPI + Async  │  │
│  └──────────┘  └───────────┘  └───────────────────┘  │
│       │              │                ▲               │
│       │              │       ┌────────┴────────┐     │
│       └──────────────┘       │    frontend     │     │
│     Dual Database System     │  Vite + React   │     │
│  (app state + DICOM metadata)│  + Cornerstone  │     │
│                              └─────────────────┘     │
└───────────────────────────────────────────────────────┘
```

---

## Testing

```bash
# Frontend unit tests
./scripts/manage.sh test-frontend

# Backend unit tests
./scripts/manage.sh test-backend
```

---

## Native (Docker-free) Setup

The native path runs everything directly on your host &mdash; no Docker
required. It is intended **for local development only**. Production and remote
(Karolinska) deployments should continue using the Docker path.

### Prerequisites

**macOS:**
```bash
brew install postgresql@16 dcm2niix
```
**Linux (Ubuntu/Debian):**
```bash
sudo apt install postgresql-16
# Build dcm2niix from source (see backend/Dockerfile for the CMake recipe)
cmake -DUSE_JPEGLS=ON -DUSE_OPENJPEG=ON ..
```

Also ensure **Node.js 20+** and **Python 3.11+** are available. The script
creates `backend/.venv` automatically.

### Commands

| Command | Description |
|---------|-------------|
| `start` | Start native Postgres, backend, and frontend |
| `stop` | Stop all native services |
| `status` | Show running/stopped state and ports |
| `test-backend` | Run backend tests (pytest) directly |
| `test-frontend` | Run frontend tests (vitest) directly |

### Options

| Option | Description |
|--------|-------------|
| `--data PATH` | Local DICOM directory (repeatable) |
| `--forward` | Bind to `0.0.0.0` (host firewall matters directly) |
| `--clean` | Wipe Postgres data directory (`resource/db-native/`); never touches `--data` paths |
| `--db-dir PATH` | Override Postgres data directory (default: `resource/db-native/`) |
| `--with-worker` | Start the optional body-part-qc-worker (CPU-only on macOS; off by default) |

### Quick start

```bash
./scripts/manage-native.sh start --data /absolute/path/to/dicom/data
# open http://localhost:5173

./scripts/manage-native.sh stop
```

### Configuration

Copy and edit the native-specific env files:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Both the backend and frontend are configured as regular host processes &mdash;
no volume-mount translation, no container networking.

---

## Citation

If you use NILS in your research, please cite:

> Chamyani, N. (2025-2026). NILS - Neuroimaging Intelligent Linked System.
> Karolinska Institutet, Department of Clinical Neuroscience.
> https://github.com/NeuroGranberg/NILS

---

Karolinska Institutet, Department of Clinical Neuroscience, Neuroradiology
