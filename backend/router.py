import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth import require_admin

from .schemas import SyncStatusResponse, normalize_mapping_path
from .service import DriveNotFound, MappingNotFound, PolicyBlocked, sync_manager

logger = logging.getLogger(__name__)

ADDON_META = {
    "label": "Cloud Sync",
    "description": "Back up drives to cloud storage via rclone.",
    "scope": "global",
    "slots": {
        "dashboard-widgets": [
            {"id": "cloud-sync", "label": "Cloud Sync", "priority": 10},
        ],
    },
}

router = APIRouter(
    prefix="/api/addons/cloud-sync",
    tags=["cloud-sync"],
    dependencies=[Depends(require_admin)],
)


async def on_startup() -> None:
    sync_manager.start_scheduler()
    logger.info("Cloud Sync addon initialized")


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
