import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.auth import require_admin

from .schemas import SyncConfig, SyncStatusResponse, normalize_mapping_path
from .service import (
    ConfigRefused,
    DriveNotFound,
    MappingNotFound,
    PolicyBlocked,
    next_sync_at,
    sync_manager,
)

logger = logging.getLogger(__name__)

ADDON_META = {
    "label": "Cloud Sync",
    "description": "Back up drives to cloud storage via rclone.",
    "scope": "global",
    "slots": {
        "dashboard-widgets": [
            {"id": "cloud-sync", "label": "Cloud Sync", "priority": 10},
        ],
        "admin-settings-sections": [
            {"id": "cloud-sync-settings", "label": "Cloud Sync", "priority": 10},
        ],
    },
}

router = APIRouter(
    prefix="/api/addons/cloud-sync",
    tags=["cloud-sync"],
    dependencies=[Depends(require_admin)],
)


async def on_startup() -> None:
    await sync_manager.replace_scheduler()
    logger.info("Cloud Sync addon initialized")


def _config_body(
    source: str,
    error: str | None,
    cfg: SyncConfig,
    drives: list[dict],
    remotes: list[str],
    remotes_error: str | None,
) -> dict:
    return {
        "source": source,
        "error": error,
        "config": cfg.model_dump(),
        "drives": drives,
        "remotes": remotes,
        "remotes_error": remotes_error,
        "next_sync_at": next_sync_at(cfg),
    }


@router.get("/config")
async def get_config() -> dict:
    loaded = sync_manager.read_config()
    try:
        drives = sync_manager.drive_choices()
    except Exception:
        logger.exception("Could not read drives.json for the Cloud Sync settings")
        raise HTTPException(status_code=500, detail="Could not read drives.json")
    remotes, remotes_error = await sync_manager.list_remotes()
    return _config_body(
        loaded.source, loaded.error, loaded.config, drives, remotes, remotes_error
    )


@router.put("/config")
async def put_config(request: Request):
    try:
        raw = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        raw = None
    try:
        drives = sync_manager.drive_choices()
        cfg, remotes, remotes_error = await sync_manager.save_config(raw)
    except ConfigRefused as refused:
        return JSONResponse(
            status_code=422,
            content={"errors": [e.as_dict() for e in refused.errors]},
        )
    except Exception:
        logger.exception("Could not save the Cloud Sync settings")
        raise HTTPException(status_code=500, detail="Could not save the settings")
    return _config_body("saved", None, cfg, drives, remotes, remotes_error)


@router.get("/status", response_model=SyncStatusResponse)
async def get_status() -> SyncStatusResponse:
    return sync_manager.get_status()


@router.post("/{drive}/start")
async def start_sync(drive: str, path: str = "") -> dict:
    try:
        await sync_manager.start_sync(drive, path)
    except PolicyBlocked:
        raise HTTPException(
            status_code=403,
            detail="Cloud sync is turned off for this drive",
        )
    except DriveNotFound:
        raise HTTPException(status_code=404, detail="Drive not found")
    except MappingNotFound:
        raise HTTPException(status_code=404, detail="Mapping not found in sync config")
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Sync already in progress")
    return {"status": "started", "drive": drive, "path": normalize_mapping_path(path)}


@router.post("/{drive}/cancel")
async def cancel_sync(drive: str, path: str = "") -> dict:
    success = await sync_manager.cancel_sync(drive, path)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="No sync in progress for this mapping",
        )
    return {"status": "cancelled", "drive": drive, "path": normalize_mapping_path(path)}


@router.get("/{drive}/log", response_class=PlainTextResponse)
async def get_log(drive: str, path: str = "") -> str:
    try:
        return sync_manager.get_log(drive, path)
    except MappingNotFound:
        raise HTTPException(status_code=404, detail="Mapping not found in sync config")
