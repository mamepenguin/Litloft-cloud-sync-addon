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

Edit `sync-config.json` with your mappings. A mapping mirrors a whole drive, or with `path` one folder inside it:

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
      "path": "Documentaries/2026",
      "remote": "s3:my-bucket/tv-docs"
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
| `max_delete` | `integer` | No | Most files one sync may delete from the remote (rclone `--max-delete`). Default `200`. |
| `mappings` | `array` | Yes | Mapping list. |
| `mappings[].drive` | `string` | Yes | Local drive name (must match a drive in `drives.json`). |
| `mappings[].path` | `string` | No | Folder inside the drive to mirror, relative to the drive root. Omit for the whole drive. |
| `mappings[].remote` | `string` | Yes | rclone remote in `remote_name:path` format. |

A drive may have several mappings for different folders. A file with two mappings of the same drive and folder, a `path` that starts with `/` or contains `..`, or two remotes that are the same place or one inside the other is rejected as a whole, and nothing syncs.

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

The `drive` value in `sync-config.json` must exactly match a drive name defined in Litloft's `drives.json`.

## License

MIT
