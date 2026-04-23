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

### 2. Create sync configuration

```bash
cp addons/cloud-sync/sync-config.json.example addons/cloud-sync/sync-config.json
```

Edit `sync-config.json` with your drive-to-remote mappings:

```json
{
  "schedule": "0 */6 * * *",
  "mappings": [
    {
      "drive": "Family Videos",
      "remote": "gdrive:litloft/family"
    },
    {
      "drive": "TV Shows",
      "remote": "s3:my-bucket/tv"
    }
  ]
}
```

### 3. Docker (recommended)

The Litloft Dockerfiles automatically discover addons placed in `addons/`. rclone installation, Python dependencies, frontend source copying, and page route generation are all handled during the build -- no manual setup required.

Mount the rclone config and sync config into the container via `docker-compose.override.yml`:

```yaml
services:
  backend:
    volumes:
      - ./rclone.conf:/root/.config/rclone/rclone.conf:ro
      - ./addons/cloud-sync/sync-config.json:/app/addons/cloud-sync/sync-config.json:ro
```

Then rebuild:

```bash
docker compose up -d --build
```

The addon will be available at `/addons/cloud-sync`.

### 4. Local development (optional)

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

### `sync-config.json`

| Field | Type | Required | Description |
|---|---|---|---|
| `schedule` | `string` | No | Cron expression (5-field). Omit to disable auto-sync. |
| `mappings` | `array` | Yes | Drive-to-remote mapping list. |
| `mappings[].drive` | `string` | Yes | Local drive name (must match a drive in `drives.json`). |
| `mappings[].remote` | `string` | Yes | rclone remote in `remote_name:path` format. |

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
| `GET` | `/status` | Status of all drives and next scheduled sync |
| `POST` | `/{drive}/start` | Start sync for a drive |
| `POST` | `/{drive}/cancel` | Cancel an in-progress sync |
| `GET` | `/{drive}/log` | Fetch sync log (plain text) |

### WebSocket events

| Event | Payload | Description |
|---|---|---|
| `sync:progress` | `{drive, progress}` | Live transfer progress (every 1s) |
| `sync:complete` | `{drive, result}` | Sync finished successfully |
| `sync:error` | `{drive, error}` | Sync failed |

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

The `drive` value in `sync-config.json` must exactly match a drive name defined in Litloft's `drives.json`.

## License

MIT
