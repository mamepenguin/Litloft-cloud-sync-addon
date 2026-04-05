from pydantic import BaseModel, field_validator


class SyncMapping(BaseModel):
    drive: str
    remote: str

    @field_validator("remote")
    @classmethod
    def validate_remote(cls, v: str) -> str:
        if v.startswith("-"):
            raise ValueError("Remote must not start with a dash")
        if ":" not in v:
            raise ValueError("Remote must contain ':' (e.g., 'myremote:path')")
        return v


class SyncConfig(BaseModel):
    mappings: list[SyncMapping]


class SyncResult(BaseModel):
    transferred_files: int = 0
    transferred_bytes: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0


class SyncProgress(BaseModel):
    bytes_transferred: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    eta: int | None = None
    percent: float = 0.0
    transfers: int = 0
    total_transfers: int = 0


class SyncDriveStatus(BaseModel):
    drive: str
    remote: str
    status: str = "idle"  # idle | syncing | error
    last_synced_at: str | None = None
    last_result: SyncResult | None = None
    progress: SyncProgress | None = None
    error_message: str | None = None


class SyncStatusResponse(BaseModel):
    drives: list[SyncDriveStatus]
