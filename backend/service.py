import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

import app.config as config
from app.services.ws import manager

from .schemas import (
    SyncConfig,
    SyncDriveStatus,
    SyncMapping,
    SyncProgress,
    SyncResult,
    SyncStatusResponse,
    normalize_mapping_path,
)
from .settings import ConfigError, check_body

logger = logging.getLogger(__name__)

LOG_DIR = config.DATA_DIR / "cloud-sync-logs"
MAX_LOG_SIZE = 1_048_576  # 1MB
LISTREMOTES_TIMEOUT = 10.0
SCHEMA_VERSION = 1

# The key under drives.json "addons", and the name the registry knows this
# addon by: the checkout directory, hyphen included.
ADDON_NAME = "cloud-sync"


# A mapping is identified by its drive and its normalized path ("" = the drive root).
Key = tuple[str, str]


class PolicyBlocked(Exception):
    """The drive's addon policy is off, or could not be read."""


class MappingNotFound(ValueError):
    """No mapping in the configuration has this drive and path."""


class DriveNotFound(ValueError):
    """The mapping's drive is not in drives.json."""


class ConfigRefused(Exception):
    """A PUT /config body broke at least one rule; nothing was changed."""

    def __init__(self, errors: list[ConfigError]) -> None:
        super().__init__(f"{len(errors)} configuration errors")
        self.errors = errors


@dataclass(frozen=True)
class LoadedConfig:
    source: str  # none | saved | invalid
    config: SyncConfig
    error: str | None = None


def config_path() -> Path:
    return Path(config.DATA_DIR) / "addons" / ADDON_NAME / "sync-config.json"


def _zone(name: str | None):
    return ZoneInfo(name) if name else UTC


def next_sync_at(cfg: SyncConfig) -> str | None:
    if not cfg.schedule:
        return None
    now = datetime.now(_zone(cfg.timezone))
    return croniter(cfg.schedule, now).get_next(datetime).isoformat()


class SyncManager:
    def __init__(self) -> None:
        self._processes: dict[Key, asyncio.subprocess.Process] = {}
        # Held from start_sync until the run ends, so a second start during the
        # awaited source check cannot launch another rclone for the same mapping.
        # Insertion-ordered, so runs whose mapping left the file are listed in
        # the order they were reserved.
        self._running: dict[Key, None] = {}
        # A cancel that arrives before rclone has a process to terminate.
        self._cancelled: set[Key] = set()
        self._status: dict[Key, SyncDriveStatus] = {}
        self._config: SyncConfig | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._save_lock = asyncio.Lock()

    @staticmethod
    def read_config() -> LoadedConfig:
        path = config_path()
        if not path.exists():
            return LoadedConfig("none", SyncConfig(mappings=[]))
        try:
            with path.open(encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("The file must hold a JSON object")
            version = raw.get("schema_version")
            if type(version) is not int or version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported schema_version: {version!r}")
            return LoadedConfig("saved", SyncConfig(**raw))
        except (OSError, ValueError, TypeError) as exc:
            logger.error(
                "Could not read %s, so nothing syncs. Save the Cloud Sync "
                "settings again to replace it: %s",
                path,
                exc,
            )
            return LoadedConfig("invalid", SyncConfig(mappings=[]), str(exc))

    def _load_config(self) -> SyncConfig:
        self._config = self.read_config().config
        return self._config

    def _get_mapping(self, drive_name: str, path: str) -> SyncMapping | None:
        cfg = self._load_config()
        for mapping in cfg.mappings:
            if (mapping.drive, mapping.path) == (drive_name, path):
                return mapping
        return None

    @staticmethod
    def _key(drive_name: str, path: object) -> Key | None:
        try:
            return drive_name, normalize_mapping_path(path)
        except ValueError:
            return None

    def _ensure_log_dir(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_log_name(drive_name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-\u3000-\u9fff\uf900-\ufaff]", "_", drive_name)

    @classmethod
    def _log_path(cls, key: Key) -> Path:
        drive_name, path = key
        safe = cls._safe_log_name(drive_name)
        if not path:
            return LOG_DIR / f"{safe}.log"
        # The sanitizer turns "." into "_", so this can never equal a
        # whole-drive mapping's name.
        digest = hashlib.sha256(
            drive_name.encode("utf-8") + b"\0" + path.encode("utf-8")
        ).hexdigest()[:16]
        return LOG_DIR / f"{safe}.{digest}.log"

    @staticmethod
    def _policy_allows(drive_name: str) -> bool:
        """False when the drive's policy is off or cannot be read.

        Nothing here has a request in front of it to fall back on, so a lookup
        that fails must not be read as "on".
        """
        try:
            return config.is_addon_feature_enabled(drive_name, ADDON_NAME, "index")
        except Exception:
            logger.warning(
                "Could not read the addon policy for %s; not syncing it",
                drive_name,
                exc_info=True,
            )
            return False

    @staticmethod
    def _source_usable(source: Path, drive_root: Path) -> bool:
        # A symlink inside the drive may point anywhere; the drive is the
        # boundary, so what it resolves to must stay under the drive's root.
        real_root = os.path.realpath(drive_root)
        real_source = os.path.realpath(source)
        if os.path.commonpath([real_root, real_source]) != real_root:
            return False
        # An unmounted drive leaves an empty directory behind, and rclone sync
        # from a source with no files empties the remote. os.walk skips what it
        # cannot read, so a folder it cannot list counts as having no files.
        return any(files for _root, _dirs, files in os.walk(source))

    async def start_sync(self, drive_name: str, path: object = "") -> None:
        key = self._key(drive_name, path)
        cfg = self._load_config()
        mapping = next(
            (m for m in cfg.mappings if key is not None and (m.drive, m.path) == key),
            None,
        )
        if key is None or mapping is None:
            raise MappingNotFound(
                f"Mapping not found in sync config: {drive_name} {path!r}"
            )

        try:
            drive_path = config.get_drive_path(drive_name)
        except ValueError:
            raise DriveNotFound(f"Drive not found: {drive_name}")

        if not self._policy_allows(drive_name):
            raise PolicyBlocked(f"Cloud sync is off for drive: {drive_name}")

        if key in self._running:
            raise RuntimeError(f"Sync already in progress for: {key}")

        previous = self._status.get(key)
        self._status[key] = SyncDriveStatus(
            drive=drive_name,
            path=mapping.path,
            remote=mapping.remote,
            status="syncing",
            last_synced_at=previous.last_synced_at if previous else None,
            last_result=previous.last_result if previous else None,
            progress=SyncProgress(),
        )

        self._running[key] = None
        # The remote and the cap are fixed here: a save while the run checks
        # its source must not re-target it.
        asyncio.create_task(
            self._run_rclone(key, drive_path, mapping.remote, cfg.max_delete)
        )

    async def cancel_sync(self, drive_name: str, path: object = "") -> bool:
        # Looked up among the runs, not in the file, so a run stays cancellable
        # after its mapping is edited out of the file or the file breaks.
        key = self._key(drive_name, path)
        if key is None or key not in self._running:
            return False
        proc = self._processes.get(key)
        if proc is None:
            self._cancelled.add(key)
            return True
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        return True

    async def _run_rclone(
        self, key: Key, drive_path: Path, remote: str, max_delete: int
    ) -> None:
        start_time = time.monotonic()
        _, path = key
        source = drive_path / path if path else drive_path

        try:
            self._ensure_log_dir()
            log_path = self._log_path(key)
            if not await asyncio.to_thread(self._source_usable, source, drive_path):
                await self._handle_error(
                    key,
                    "The folder is missing or empty, so nothing was synced. "
                    "Check that the drive is mounted and the folder exists.",
                    kind="source_empty",
                )
                return

            if key in self._cancelled:
                await self._handle_error(key, "Cancelled before anything was synced.")
                return

            proc = await asyncio.create_subprocess_exec(
                "rclone", "sync",
                str(source),
                remote,
                "--max-delete", str(max_delete),
                "--stats", "1s",
                "--stats-log-level", "NOTICE",
                "--use-json-log",
                "--log-level", "INFO",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._processes[key] = proc
            if key in self._cancelled:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

            log_lines = await self._parse_rclone_output(key, proc, log_path)

            await proc.wait()
            elapsed = time.monotonic() - start_time

            result = self._build_result(key, elapsed)
            await self._handle_completion(key, proc.returncode, result, log_lines)

        except Exception as exc:
            logger.exception("rclone failed for %s", key)
            await self._handle_error(key, str(exc))
        finally:
            self._processes.pop(key, None)
            self._running.pop(key, None)
            self._cancelled.discard(key)

    async def _parse_rclone_output(
        self,
        key: Key,
        proc: asyncio.subprocess.Process,
        log_path: Path,
    ) -> list[bytes]:
        assert proc.stderr is not None
        all_lines: list[bytes] = []
        log_lines: list[bytes] = []
        total_log_bytes = 0

        async for raw_line in proc.stderr:
            all_lines.append(raw_line)

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if total_log_bytes < MAX_LOG_SIZE:
                    log_lines.append(raw_line)
                    total_log_bytes += len(raw_line)
                continue

            if "stats" not in entry:
                # Non-stats entries (transfers, errors, etc.) go to log
                if total_log_bytes < MAX_LOG_SIZE:
                    log_lines.append(raw_line)
                    total_log_bytes += len(raw_line)
                continue

            stats = entry["stats"]
            progress = SyncProgress(
                bytes_transferred=stats.get("bytes", 0),
                total_bytes=stats.get("totalBytes", 0),
                speed=stats.get("speed", 0.0),
                eta=stats.get("eta"),
                percent=self._calc_percent(
                    stats.get("bytes", 0), stats.get("totalBytes", 0)
                ),
                transfers=stats.get("transfers", 0),
                total_transfers=stats.get("totalTransfers", 0),
            )

            self._update_progress(key, progress)

            await manager.broadcast("sync:progress", {
                "drive": key[0],
                "path": key[1],
                "bytes_transferred": progress.bytes_transferred,
                "total_bytes": progress.total_bytes,
                "speed": progress.speed,
                "eta": progress.eta,
                "percent": progress.percent,
                "transfers": progress.transfers,
                "total_transfers": progress.total_transfers,
            })

        self._write_log(log_path, log_lines)
        return all_lines

    def _update_progress(self, key: Key, progress: SyncProgress) -> None:
        current = self._status.get(key)
        if current is None:
            return
        self._status[key] = SyncDriveStatus(
            drive=current.drive,
            path=current.path,
            remote=current.remote,
            status=current.status,
            last_synced_at=current.last_synced_at,
            last_result=current.last_result,
            progress=progress,
        )

    def _build_result(self, key: Key, elapsed: float) -> SyncResult:
        current = self._status.get(key)
        progress = current.progress if current else None
        return SyncResult(
            transferred_files=progress.transfers if progress else 0,
            transferred_bytes=progress.bytes_transferred if progress else 0,
            errors=0,
            elapsed_seconds=round(elapsed, 1),
        )

    _AUTH_ERROR_PATTERNS = (
        "oauth2: token expired",
        "oauth2: cannot fetch token",
        "authError",
        "invalid_grant",
        "token has been expired or revoked",
        "failed to refresh token",
        "NoCredentialProviders",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "AccessDenied",
    )

    # rclone exits 7 for this, as it does for any fatal error, so the exit
    # code cannot tell it apart; the error line is the only marker.
    _DELETE_LIMIT_PATTERN = "--max-delete threshold reached"

    @classmethod
    def _classify_error(cls, log_lines: list[bytes]) -> str | None:
        """Classify a failed run from rclone's log output."""
        lines = [raw.decode("utf-8", errors="replace") for raw in log_lines]
        if any(cls._DELETE_LIMIT_PATTERN in line for line in lines):
            return "delete_limit"
        for line in lines:
            for pattern in cls._AUTH_ERROR_PATTERNS:
                if pattern in line:
                    return "auth_expired"
        return None

    async def _handle_completion(
        self,
        key: Key,
        returncode: int | None,
        result: SyncResult,
        log_lines: list[bytes],
    ) -> None:
        drive_name, path = key
        now = datetime.now(UTC).isoformat()
        current = self._status.get(key)
        remote = current.remote if current else ""

        if returncode == 0:
            self._status[key] = SyncDriveStatus(
                drive=drive_name,
                path=path,
                remote=remote,
                status="idle",
                last_synced_at=now,
                last_result=result,
            )
            await manager.broadcast("sync:complete", {
                "drive": drive_name,
                "path": path,
                "transferred_files": result.transferred_files,
                "transferred_bytes": result.transferred_bytes,
                "errors": result.errors,
                "elapsed_seconds": result.elapsed_seconds,
            })
            await self._on_sync_complete(key, result)
        else:
            error_kind = self._classify_error(log_lines)
            if error_kind == "auth_expired":
                error_msg = (
                    "Authentication expired. "
                    "Run 'rclone config reconnect <remote>:' on the host "
                    "and restart the container."
                )
            elif error_kind == "delete_limit":
                error_msg = (
                    "Stopped: the sync would delete more files than max_delete "
                    "allows. Check that the drive is mounted, then raise "
                    "Max deletions per sync in the Cloud Sync settings if the "
                    "deletions are expected."
                )
            else:
                error_msg = f"rclone exited with code {returncode}"
            self._status[key] = SyncDriveStatus(
                drive=drive_name,
                path=path,
                remote=remote,
                status="error",
                last_synced_at=now,
                last_result=result,
                error_message=error_msg,
                error_kind=error_kind,
            )
            await manager.broadcast("sync:error", {
                "drive": drive_name,
                "path": path,
                "message": error_msg,
                "kind": error_kind,
            })

    async def _handle_error(
        self, key: Key, message: str, kind: str | None = None
    ) -> None:
        drive_name, path = key
        current = self._status.get(key)
        remote = current.remote if current else ""
        self._status[key] = SyncDriveStatus(
            drive=drive_name,
            path=path,
            remote=remote,
            status="error",
            last_synced_at=current.last_synced_at if current else None,
            last_result=current.last_result if current else None,
            error_message=message,
            error_kind=kind,
        )
        await manager.broadcast("sync:error", {
            "drive": drive_name,
            "path": path,
            "message": message,
            "kind": kind,
        })

    async def _on_sync_complete(self, key: Key, result: SyncResult) -> None:
        """Sync completion hook. Extension point for future features.

        For example, if cloud-to-local sync is added later:
        - Trigger a drive scan to register new files in the DB
        """

    def get_status(self) -> SyncStatusResponse:
        cfg = self._load_config()
        drives: list[SyncDriveStatus] = []
        for mapping in cfg.mappings:
            key = (mapping.drive, mapping.path)
            status = self._status.get(key)
            if status is not None and status.status == "syncing":
                drives.append(status)
                continue
            # A result recorded against another remote says nothing about
            # this one.
            if status is not None and status.remote != mapping.remote:
                status = None
            if not self._policy_allows(mapping.drive):
                drives.append(SyncDriveStatus(
                    drive=mapping.drive,
                    path=mapping.path,
                    remote=mapping.remote,
                    status="disabled",
                ))
            elif status is not None:
                drives.append(status)
            else:
                drives.append(SyncDriveStatus(
                    drive=mapping.drive,
                    path=mapping.path,
                    remote=mapping.remote,
                ))
        in_file = {(m.drive, m.path) for m in cfg.mappings}
        for key in self._running:
            status = self._status.get(key)
            if key not in in_file and status is not None:
                drives.append(status)
        return SyncStatusResponse(
            drives=drives,
            schedule=cfg.schedule,
            timezone=cfg.timezone,
            next_sync_at=next_sync_at(cfg),
        )

    def get_log(self, drive_name: str, path: object = "") -> str:
        key = self._key(drive_name, path)
        if key is None or (key not in self._running and self._get_mapping(*key) is None):
            raise MappingNotFound(
                f"Mapping not found in sync config: {drive_name} {path!r}"
            )
        log_path = self._log_path(key)
        if not log_path.exists():
            return ""
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("Failed to read log for %s: %s", key, exc)
            return ""

    @staticmethod
    def _calc_percent(transferred: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return round((transferred / total) * 100, 1)

    @staticmethod
    def _write_log(log_path: Path, lines: list[bytes]) -> None:
        try:
            with open(log_path, "wb") as f:
                for line in lines:
                    f.write(line)
        except OSError as exc:
            logger.error("Failed to write log to %s: %s", log_path, exc)

    # ── Settings ───────────────────────────────────────────────

    async def list_remotes(self) -> tuple[list[str], str | None]:
        """The remotes `rclone listremotes` prints, or why there are none."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "listremotes",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return [], f"rclone could not be run: {exc}"
        try:
            out, err = await asyncio.wait_for(proc.communicate(), LISTREMOTES_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return [], f"rclone listremotes did not answer within {LISTREMOTES_TIMEOUT:.0f} s"
        if proc.returncode != 0:
            detail = err.decode("utf-8", errors="replace").strip()
            return [], detail or f"rclone listremotes exited with code {proc.returncode}"
        lines = out.decode("utf-8", errors="replace").splitlines()
        return [line.strip() for line in lines if line.strip()], None

    def drive_choices(self) -> list[dict]:
        """Raises when drives.json cannot be read."""
        return [
            {"name": d["name"], "enabled": self._policy_allows(d["name"])}
            for d in config.load_drives()
        ]

    @staticmethod
    def _folder_inside_drive(drive_root: Path, path: str) -> bool:
        real_root = os.path.realpath(drive_root)
        real = os.path.realpath(drive_root / path if path else drive_root)
        if os.path.commonpath([real_root, real]) != real_root:
            return False
        return os.path.isdir(real)

    async def _environment_errors(
        self,
        rows,
        drive_names: set[str],
        remotes: list[str],
        remotes_error: str | None,
    ) -> list[ConfigError]:
        errors: list[ConfigError] = []
        for row in rows:
            if row.drive not in drive_names:
                errors.append(ConfigError(
                    "drive", "unknown_drive", f"Drive not found: {row.drive!r}", row.index,
                ))
            elif row.path is not None:
                root = config.get_drive_path(row.drive)
                if not await asyncio.to_thread(self._folder_inside_drive, root, row.path):
                    errors.append(ConfigError(
                        "path", "folder_not_found",
                        f"No folder {row.path!r} inside drive {row.drive!r}", row.index,
                    ))
        if rows and remotes_error is not None:
            errors.append(ConfigError(
                "mappings", "remotes_unavailable",
                f"Could not list the rclone remotes: {remotes_error}",
            ))
            return errors
        for row in rows:
            name = row.remote.partition(":")[0] + ":"
            if row.remote_ok and name not in remotes:
                errors.append(ConfigError(
                    "remote", "remote_not_listed",
                    f"rclone has no remote named {name!r}", row.index,
                ))
        return errors

    @staticmethod
    def _write_config(cfg: SyncConfig) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, **cfg.model_dump()}
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sync-config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def save_config(
        self, raw: object
    ) -> tuple[SyncConfig, list[str], str | None]:
        """Validate and store a PUT /config body, then apply it.

        Raises ConfigRefused when a rule fails, and lets a drives.json or write
        failure propagate; in every failure the file and the scheduler are
        left as they were.
        """
        async with self._save_lock:
            drive_names = {d["name"] for d in config.load_drives()}
            remotes, remotes_error = await self.list_remotes()
            checked = check_body(raw)
            errors = list(checked.errors)
            errors += await self._environment_errors(
                checked.rows, drive_names, remotes, remotes_error
            )
            if errors:
                logger.info(
                    "Refused Cloud Sync settings: %s", [e.as_dict() for e in errors]
                )
                raise ConfigRefused(errors)
            cfg = checked.to_config()
            await asyncio.to_thread(self._write_config, cfg)
            await self.replace_scheduler(cfg)
            return cfg, remotes, remotes_error

    # ── Scheduler ──────────────────────────────────────────────

    def start_scheduler(self, cfg: SyncConfig | None = None) -> None:
        """Replace the running loop with one for ``cfg`` (the stored file by default)."""
        self.stop_scheduler()
        if cfg is None:
            cfg = self._load_config()
        if not cfg.schedule:
            logger.info("No schedule configured, skipping scheduler")
            return
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(cfg.schedule, cfg.timezone)
        )
        logger.info(
            "Scheduler started with schedule %s (%s)", cfg.schedule, cfg.timezone or "UTC"
        )

    async def replace_scheduler(self, cfg: SyncConfig | None = None) -> None:
        """Start the loop for ``cfg`` once the previous loop has finished."""
        old = self._scheduler_task
        self.stop_scheduler()
        if old is not None:
            await asyncio.gather(old, return_exceptions=True)
        self.start_scheduler(cfg)

    def stop_scheduler(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Scheduler stopped")

    async def _scheduler_loop(self, cron_expr: str, tz_name: str | None) -> None:
        zone = _zone(tz_name)
        try:
            while True:
                now = datetime.now(zone)
                next_dt = croniter(cron_expr, now).get_next(datetime)
                delay = (next_dt - now).total_seconds()
                logger.info(
                    "Next scheduled sync at %s (in %.0fs)",
                    next_dt.isoformat(),
                    delay,
                )
                await asyncio.sleep(delay)
                await self._run_scheduled_sync()
        except asyncio.CancelledError:
            logger.info("Scheduler loop cancelled")

    async def _run_scheduled_sync(self) -> None:
        cfg = self._load_config()
        for mapping in cfg.mappings:
            key = (mapping.drive, mapping.path)
            if key in self._running:
                logger.info("Skipping scheduled sync for %s (already syncing)", key)
                continue
            try:
                await self.start_sync(mapping.drive, mapping.path)
                logger.info("Scheduled sync started for %s", key)
            except PolicyBlocked:
                logger.info("Skipping scheduled sync for %s (policy off)", key)
            except (ValueError, RuntimeError) as exc:
                logger.warning("Scheduled sync failed to start for %s: %s", key, exc)


sync_manager = SyncManager()
