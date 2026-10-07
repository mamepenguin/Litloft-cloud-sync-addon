# Cloud Sync

A [Litloft](https://github.com/mamepenguin/video-share) addon that backs up your drives to cloud storage using [rclone](https://rclone.org/).

Supports any rclone-compatible provider: Google Drive, AWS S3, Backblaze B2, Dropbox, OneDrive, SFTP, and [many more](https://rclone.org/overview/).

## Features

- **Scheduled sync** -- Set a cron expression and drives sync automatically
- **Real-time progress** -- Live progress bar, speed, and ETA via WebSocket
- **Multi-drive** -- Each drive maps to its own cloud remote; syncs run concurrently
- **Auth detection** -- Recognizes expired OAuth tokens and shows re-authentication steps
- **Sync logs** -- Per-drive logs capped at 1 MB, viewable from the UI
- **Manual control** -- Start, cancel, or retry syncs from the dashboard

## Screenshots

<!-- TODO: add screenshots -->

## Requirements

- [Litloft](https://github.com/mamepenguin/video-share)
- rclone configured on the host with at least one remote (`rclone config`)

## Installation

### 1. Place the addon

```bash
# From the Litloft root directory
git clone https://github.com/mamepenguin/cloud-sync.git addons/cloud-sync
```

### 2. Docker (recommended)

The Litloft Dockerfiles automatically discover addons placed in `addons/`. rclone installation, Python dependencies, frontend source copying, and page route generation are all handled during the build -- no manual setup required.

Mount the rclone config into the container via `docker-compose.override.yml`:

```yaml
services:
  backend:
    volumes:
      - ~/.config/rclone:/root/.config/rclone:ro
```

Then rebuild:

```bash
docker compose up -d --build
```

Then open **Settings** (`/admin/settings`) and set up Cloud Sync there.

### 3. Local development (optional)

Run the setup script to create symlinks for backend and frontend:

```bash
# From the Litloft root directory
./setup-addons.sh
```

Install dependencies manually:

```bash
# rclone
brew install rclone        # macOS
# apt-get install rclone   # Debian/Ubuntu

# Python dependencies
pip install -r addons/cloud-sync/backend/requirements.txt
```

## Configuration

Cloud Sync is configured in **Settings** (`/admin/settings`, System tab, **Cloud Sync**). The settings are stored in `data/addons/cloud-sync/sync-config.json` and take effect when saved; no restart is needed.

| Setting | Description |
|---|---|
| Schedule | Off, daily, every few hours, weekly, or a 5-field cron expression. |
| Time zone | The zone the schedule runs in. |
| Max deletions per sync | Most files one sync may delete from the remote (rclone `--max-delete`). Default `200`. |
| Mappings | A drive, optionally a folder inside it, and an rclone remote with a folder on it. |

The remote list shows the remotes `rclone listremotes` finds in the mounted rclone config. A save is refused, and nothing changes, if a drive or folder does not exist, a remote is not listed, a remote names no folder (mirroring into a remote's root would delete everything else there), two mappings name the same folder, or two remotes are the same place or one inside the other.

### Cron examples

| Expression | Meaning |
|---|---|
| `0 */6 * * *` | Every 6 hours |
| `0 3 * * *` | Daily at 3:00 AM |
| `0 0 * * 0` | Weekly on Sunday at midnight |
| `*/30 * * * *` | Every 30 minutes |

## API

Base path: `/api/addons/cloud-sync`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/config` | Stored settings, drives, rclone remotes |
| `PUT` | `/config` | Replace the settings (`422` with every error when refused) |
| `GET` | `/status` | Status of every mapping and next scheduled sync |
| `POST` | `/{drive}/start?path=` | Start sync for a mapping (`path` omitted = whole drive) |
| `POST` | `/{drive}/cancel?path=` | Cancel an in-progress sync |
| `GET` | `/{drive}/log?path=` | Fetch sync log (plain text) |

### WebSocket events

| Event | Payload | Description |
|---|---|---|
| `sync:progress` | `{drive, path, bytes_transferred, total_bytes, speed, eta, percent, transfers, total_transfers}` | Live transfer progress (every 1s) |
| `sync:complete` | `{drive, path, transferred_files, transferred_bytes, errors, elapsed_seconds}` | Sync finished successfully |
| `sync:error` | `{drive, path, message, kind}` | Sync failed |

## Troubleshooting

### "Authentication expired" error

rclone's OAuth token has expired. On the Docker host, run:

```bash
rclone config reconnect <remote_name>:
```

Then restart the container.

### rclone not found

Make sure `install.sh` ran during the Docker build and rclone is in the container's PATH.

### Drive not found (404)

The mapping's drive is no longer in `drives.json`. Choose another drive for it in Settings.

## License

MIT
