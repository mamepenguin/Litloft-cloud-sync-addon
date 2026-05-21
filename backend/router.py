import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.auth import require_admin

from .schemas import SyncStatusResponse
from .service import sync_manager

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
async def start_sync(drive: str) -> dict:
    try:
        await sync_manager.start_sync(drive)
    except ValueError:
        raise HTTPException(status_code=404, detail="Drive not found in sync config")
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Sync already in progress")
    return {"status": "started", "drive": drive}


@router.post("/{drive}/cancel")
async def cancel_sync(drive: str) -> dict:
    success = await sync_manager.cancel_sync(drive)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="No sync in progress for this drive",
        )
    return {"status": "cancelled", "drive": drive}


@router.get("/{drive}/log", response_class=PlainTextResponse)
async def get_log(drive: str) -> str:
    mapping = sync_manager._get_mapping(drive)
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail="Drive not found in sync config",
        )
    return sync_manager.get_log(drive)
