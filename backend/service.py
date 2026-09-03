import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

import app.config as config
from app.services.ws import manager

from .schemas import (
    SyncConfig,
    SyncDriveStatus,
    SyncProgress,
    SyncResult,
    SyncStatusResponse,
)

logger = logging.getLogger(__name__)

_RESOLVED_DIR = Path(__file__).resolve().parent
LOG_DIR = config.DATA_DIR / "cloud-sync-logs"
MAX_LOG_SIZE = 1_048_576  # 1MB


class SyncManager:
    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._status: dict[str, SyncDriveStatus] = {}
        self._config: SyncConfig | None = None
        self._scheduler_task: asyncio.Task[None] | None = None

    @staticmethod
    def _find_config() -> Path | None:
        for candidate in [
            _RESOLVED_DIR / "sync-config.json",
            _RESOLVED_DIR.parent / "sync-config.json",
        ]:
            if candidate.exists():
                return candidate
        return None

    def _load_config(self) -> SyncConfig:
        CONFIG_PATH = self._find_config()
        if CONFIG_PATH is None:
            logger.warning("sync-config.json not found near %s", _RESOLVED_DIR)
            self._config = SyncConfig(mappings=[])
            return self._config
        try:
            with open(CONFIG_PATH) as f:
                raw = json.load(f)
            self._config = SyncConfig(**raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to parse sync-config.json: %s", exc)
            self._config = SyncConfig(mappings=[])
        return self._config

    def _get_mapping(self, drive_name: str) -> tuple[str, str] | None:
        cfg = self._load_config()
        for mapping in cfg.mappings:
            if mapping.drive == drive_name:
                return mapping.drive, mapping.remote
        return None

    def _ensure_log_dir(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_log_name(drive_name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-\u3000-\u9fff\uf900-\ufaff]", "_", drive_name)

    def _get_drive_status(self, drive_name: str, remote: str) -> SyncDriveStatus:
        if drive_name not in self._status:
            self._status[drive_name] = SyncDriveStatus(
                drive=drive_name,
                remote=remote,
            )
        return self._status[drive_name]

    async def start_sync(self, drive_name: str) -> None:
        mapping = self._get_mapping(drive_name)
        if mapping is None:
            raise ValueError(f"Drive not found in sync config: {drive_name}")

        try:
            drive_path = config.get_drive_path(drive_name)
        except ValueError:
            raise ValueError(f"Drive not found: {drive_name}")

        if drive_name in self._processes:
            raise RuntimeError(f"Sync already in progress for: {drive_name}")

        _, remote = mapping
        status = self._get_drive_status(drive_name, remote)
        self._status[drive_name] = SyncDriveStatus(
            drive=status.drive,
            remote=status.remote,
            status="syncing",
            last_synced_at=status.last_synced_at,
            last_result=status.last_result,
            progress=SyncProgress(),
        )

        asyncio.create_task(self._run_rclone(drive_name, drive_path, remote))

    async def cancel_sync(self, drive_name: str) -> bool:
        proc = self._processes.get(drive_name)
        if proc is None:
            return False
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        return True

    async def _run_rclone(
        self, drive_name: str, drive_path: Path, remote: str
    ) -> None:
        self._ensure_log_dir()
        log_path = LOG_DIR / f"{self._safe_log_name(drive_name)}.log"
        start_time = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "sync",
                str(drive_path),
                remote,
                "--stats", "1s",
                "--stats-log-level", "NOTICE",
                "--use-json-log",
                "--log-level", "INFO",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._processes[drive_name] = proc

            log_lines = await self._parse_rclone_output(
                drive_name, proc, log_path
            )

            await proc.wait()
            elapsed = time.monotonic() - start_time

            result = self._build_result(drive_name, elapsed)
            await self._handle_completion(
                drive_name, proc.returncode, result, log_lines
            )

        except Exception as exc:
            elapsed = time.monotonic() - start_time
            logger.exception("rclone failed for drive %s", drive_name)
            await self._handle_error(drive_name, str(exc))
        finally:
            self._processes.pop(drive_name, None)

    async def _parse_rclone_output(
        self,
        drive_name: str,
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

            self._update_progress(drive_name, progress)

            await manager.broadcast("sync:progress", {
                "drive": drive_name,
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

    def _update_progress(self, drive_name: str, progress: SyncProgress) -> None:
        current = self._status.get(drive_name)
        if current is None:
            return
        self._status[drive_name] = SyncDriveStatus(
            drive=current.drive,
            remote=current.remote,
            status=current.status,
            last_synced_at=current.last_synced_at,
            last_result=current.last_result,
            progress=progress,
        )

    def _build_result(self, drive_name: str, elapsed: float) -> SyncResult:
        current = self._status.get(drive_name)
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

    @classmethod
    def _classify_error(cls, log_lines: list[bytes]) -> str | None:
        """Detect auth errors from rclone log output."""
        for raw_line in log_lines:
            line = raw_line.decode("utf-8", errors="replace")
            for pattern in cls._AUTH_ERROR_PATTERNS:
                if pattern in line:
                    return "auth_expired"
        return None

    async def _handle_completion(
        self,
        drive_name: str,
        returncode: int | None,
        result: SyncResult,
        log_lines: list[bytes],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        current = self._status.get(drive_name)
        remote = current.remote if current else ""

        if returncode == 0:
            self._status[drive_name] = SyncDriveStatus(
                drive=drive_name,
                remote=remote,
                status="idle",
                last_synced_at=now,
                last_result=result,
            )
            await manager.broadcast("sync:complete", {
                "drive": drive_name,
                "transferred_files": result.transferred_files,
                "transferred_bytes": result.transferred_bytes,
                "errors": result.errors,
                "elapsed_seconds": result.elapsed_seconds,
            })
            await self._on_sync_complete(drive_name, result)
        else:
            error_kind = self._classify_error(log_lines)
            if error_kind == "auth_expired":
                error_msg = (
                    "Authentication expired. "
                    "Run 'rclone config reconnect <remote>:' on the host "
                    "and restart the container."
                )
            else:
                error_msg = f"rclone exited with code {returncode}"
            self._status[drive_name] = SyncDriveStatus(
                drive=drive_name,
                remote=remote,
                status="error",
                last_synced_at=now,
                last_result=result,
                error_message=error_msg,
                error_kind=error_kind,
            )
            await manager.broadcast("sync:error", {
                "drive": drive_name,
                "message": error_msg,
                "kind": error_kind,
            })

    async def _handle_error(self, drive_name: str, message: str) -> None:
        current = self._status.get(drive_name)
        remote = current.remote if current else ""
        self._status[drive_name] = SyncDriveStatus(
            drive=drive_name,
            remote=remote,
            status="error",
            last_synced_at=current.last_synced_at if current else None,
            last_result=current.last_result if current else None,
            error_message=message,
            error_kind=None,
        )
        await manager.broadcast("sync:error", {
            "drive": drive_name,
            "message": message,
            "kind": None,
        })

    async def _on_sync_complete(
        self, drive_name: str, result: SyncResult
    ) -> None:
        """Sync completion hook. Extension point for future features.

        For example, if cloud-to-local sync is added later:
        - Trigger a drive scan to register new files in the DB
        """

    def _get_next_sync_at(self) -> str | None:
        cfg = self._load_config()
        if not cfg.schedule:
            return None
        try:
            cron = croniter(cfg.schedule, datetime.now(UTC))
            next_dt = cron.get_next(datetime)
            return next_dt.isoformat()
        except (ValueError, KeyError):
            return None

    def get_status(self) -> SyncStatusResponse:
        cfg = self._load_config()
        drives: list[SyncDriveStatus] = []
        for mapping in cfg.mappings:
            status = self._status.get(mapping.drive)
            if status is not None:
                drives.append(status)
            else:
                drives.append(SyncDriveStatus(
                    drive=mapping.drive,
                    remote=mapping.remote,
                ))
        return SyncStatusResponse(
            drives=drives,
            schedule=cfg.schedule,
            next_sync_at=self._get_next_sync_at(),
        )

    def get_log(self, drive_name: str) -> str:
        log_path = LOG_DIR / f"{self._safe_log_name(drive_name)}.log"
        if not log_path.exists():
            return ""
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("Failed to read log for %s: %s", drive_name, exc)
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

    # ── Scheduler ──────────────────────────────────────────────

    def start_scheduler(self) -> None:
        cfg = self._load_config()
        if not cfg.schedule:
            logger.info("No schedule configured, skipping scheduler")
            return
        if not croniter.is_valid(cfg.schedule):
            logger.error("Invalid cron expression: %s", cfg.schedule)
            return
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(cfg.schedule)
        )
        logger.info("Scheduler started with schedule: %s", cfg.schedule)

    def stop_scheduler(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            self._scheduler_task = None
            logger.info("Scheduler stopped")

    async def _scheduler_loop(self, cron_expr: str) -> None:
        try:
            while True:
                now = datetime.now(UTC)
                cron = croniter(cron_expr, now)
                next_dt = cron.get_next(datetime)
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
            drive_name = mapping.drive
            if drive_name in self._processes:
                logger.info(
                    "Skipping scheduled sync for %s (already syncing)",
                    drive_name,
                )
                continue
            try:
                await self.start_sync(drive_name)
                logger.info("Scheduled sync started for %s", drive_name)
            except (ValueError, RuntimeError) as exc:
                logger.warning(
                    "Scheduled sync failed to start for %s: %s",
                    drive_name,
                    exc,
                )


sync_manager = SyncManager()
