# Installation

This guide covers how to install and run NILS on your system. There are two
installation paths:

- **Option A: Docker** &mdash; recommended for production and remote (Karolinska)
  deployments. This is the original, stable path.
- **Option B: Native / Docker-free** &mdash; recommended for local development on
  macOS. Runs Postgres 16, the backend, and the frontend directly on the host
  with no containers. Perfect for fast iteration.

---

## Option A: Docker (recommended for production / remote deployment)

### Prerequisites

Before installing NILS, ensure you have:

- **Docker** (version 20.10 or later)
- **Docker Compose** (version 2.0 or later)
- **Git** (for cloning the repository)

#### Verifying Prerequisites

```bash
# Check Docker version
docker --version
# Docker version 24.0.0 or later

# Check Docker Compose version
docker compose version
# Docker Compose version v2.20.0 or later

# Check Git
git --version
```

### Installation Steps

#### 1. Clone the Repository

```bash
git clone https://github.com/NeuroGranberg/NILS.git
cd NILS
```

#### 2. Configure Environment

Copy the example environment file and adjust settings if needed:

```bash
cp .env.example .env
```

The default configuration works for most setups. See [Configuration](configuration.md) for customization options.

#### 3. Start NILS

```bash
./scripts/manage.sh start
```

This command will:

1. Pull required Docker images
2. Build the application containers
3. Initialize the databases
4. Start all services

!!! info "First Start"
    The first start may take several minutes as Docker downloads and builds images.

#### 4. Access the Interface

Once started, open your browser and navigate to:

```
http://localhost:5173
```

### Stopping NILS

To stop all services:

```bash
./scripts/manage.sh stop
```

### Updating NILS

To update to the latest version:

```bash
git pull
./scripts/manage.sh stop
./scripts/manage.sh start
```

### Troubleshooting (Docker)

#### Port Conflicts

If port 5173 is already in use, you can modify the port in your `.env` file:

```bash
FRONTEND_PORT=8080
```

#### Permission Issues

On Linux, you may need to add your user to the docker group:

```bash
sudo usermod -aG docker $USER
# Log out and back in for changes to take effect
```

#### Container Issues

To clean up and start fresh:

```bash
./scripts/manage.sh stop --clean
./scripts/manage.sh start
```

---

## Option B: Native / Docker-free (recommended for local development on macOS)

The native path runs everything directly on your host machine &mdash; a single
local Postgres 16 cluster, the backend (uvicorn with hot-reload), and the
frontend (Vite dev server) &mdash; with no Docker or container dependencies.

### Prerequisites

#### macOS

```bash
brew install postgresql@16 dcm2niix
```

- **Node.js** 20+ &mdash; check with `node --version`
- **Python** 3.11+ &mdash; check with `python3 --version`

The script creates and manages `backend/.venv` automatically &mdash; you don't
need to create it manually.

#### Linux (Ubuntu/Debian)

```bash
sudo apt install postgresql-16
```

Build `dcm2niix` from source using the CMake recipe in `backend/Dockerfile`:

```bash
cmake -DUSE_JPEGLS=ON -DUSE_OPENJPEG=ON ..
```

!!! warning "dcm2niix build flags matter"
    The `-DUSE_JPEGLS=ON` and `-DUSE_OPENJPEG=ON` flags enable JPEG-LS and
    OpenJPEG support in `dcm2niix`. Without these, some compressed DICOM series
    may decode incorrectly, affecting the accuracy of DICOM extraction and
    series classification. Always build with both flags enabled.

### Starting NILS (native)

```bash
# 1. Clone (if you haven't already)
git clone https://github.com/NeuroGranberg/NILS.git
cd NILS

# 2. Configure environment
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Edit `backend/.env` and set `DATA_ROOT` (or `DATA_ROOTS`) to point at your
real local DICOM directories. Similarly, set `VITE_DATA_ROOT` in
`frontend/.env.local` to the same path. These are native filesystem paths
&mdash; no volume-mount translation needed.

```bash
# 3. Start everything
./scripts/manage-native.sh start --data /absolute/path/to/your/dicom/data
```

This single command:

1. Initializes a native Postgres 16 cluster in `resource/db-native/`
2. Creates both `neurotoolkit` and `neurotoolkit_metadata` databases
   (no manual SQL imports &mdash; the app bootstraps its own schema on first
   startup)
3. Creates `backend/.venv` and installs the backend package in editable mode
4. Starts the backend via **uvicorn** with hot-reload
5. Runs `npm install` if needed and starts the **Vite** dev frontend

Once started, open your browser to:

```
http://localhost:5173
```

### Data Directories

Pass one or more local DICOM directories with `--data` (repeatable):

```bash
./scripts/manage-native.sh start \
  --data /srv/dicom/ct \
  --data /srv/dicom/mr
```

These paths are passed directly as environment variables (`DATA_ROOT` /
`DATA_ROOTS` / `VITE_DATA_ROOT`). Unlike Docker, there are no volume mounts
to configure.

### Ports

All ports are auto-discovered by scanning for free ports (default starting
points: Postgres 5432, backend 8010, frontend 5173). The actual assignments
are printed at startup.

### Options

| Option | Description |
|--------|-------------|
| `--data PATH` | Point at one or more local DICOM directories (repeatable) |
| `--forward` | Bind backend/frontend to `0.0.0.0` instead of `127.0.0.1` |
| `--clean` | With `start`: drop and recreate the Postgres data directory before starting. With `stop`: tear down the data directory after stopping. Also clears Python cache. |
| `--db-dir PATH` | Override the Postgres data directory (default: `resource/db-native/`) |
| `--with-worker` | Also start the optional body-part-qc-worker (CPU-only on macOS; off by default) |

### The `--forward` flag and host firewall

Unlike Docker's container network isolation, native mode runs processes
directly on the host. When you use `--forward`, the backend and frontend
bind to `0.0.0.0` and become accessible from your network (e.g., Tailscale).
**Your host firewall now controls what is actually reachable** &mdash; there
is no additional container-level network sandbox.

### Stopping NILS (native)

```bash
./scripts/manage-native.sh stop
```

To also wipe the local Postgres data directory:

```bash
./scripts/manage-native.sh stop --clean
```

!!! info "`--clean` is local-only"
    The `--clean` flag removes `resource/db-native/` (your local Postgres
    cluster). It **never touches** your DICOM source data paths passed via
    `--data`.

### Status

```bash
./scripts/manage-native.sh status
```

Shows the running/stopped state of Postgres, the backend, the frontend, and
the optional worker, along with the Postgres port.

### Testing

```bash
# Run all frontend tests
./scripts/manage-native.sh test-frontend

# Run all backend tests
./scripts/manage-native.sh test-backend
```

Tests run directly (vitest / pytest) with no Docker involvement. Backend tests
automatically start a native Postgres instance, run the test suite, and shut it
down afterwards.

### Logs

Logs are written to `resource/run/`:

| File | Service |
|------|---------|
| `resource/run/backend.log` | Backend (uvicorn) |
| `resource/run/frontend.log` | Frontend (Vite) |
| `resource/run/postgres.log` | Postgres |
| `resource/run/worker.log` | Body-part QC worker (if started) |

PID files are stored alongside the logs in the same directory.

### When to use which

| Scenario | Recommended path |
|----------|------------------|
| Fast local development & iteration | **Native** &mdash; instant startup, hot-reload, no image builds |
| Production / remote (Karolinska) deployment | **Docker** &mdash; container isolation, reproducible environment |
| Full stack testing of container behavior | **Docker** &mdash; matches production setup |
| Quick test of a single feature | **Native** &mdash; start in seconds, no Docker daemon needed |

---

## Next Steps (both options)

- Continue to [Quick Start](quick-start.md) to import your first dataset
- See [Configuration](configuration.md) for advanced settings
