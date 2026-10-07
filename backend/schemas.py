from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_mapping_path(raw: object) -> str:
    """The folder a mapping names, relative to the drive root; "" is the root.

    Empty and "." segments are dropped so that spellings of one folder compare
    equal. Nothing else is rewritten: the path is only ever opened, and the host
    filesystem resolves case and Unicode normalization itself.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"Mapping path must be a string, got {raw!r}")
    if "\0" in raw or raw.startswith("/"):
        raise ValueError(f"Mapping path must be relative to the drive: {raw!r}")
    segments = [s for s in raw.split("/") if s not in ("", ".")]
    if ".." in segments:
        raise ValueError(f"Mapping path must not contain '..': {raw!r}")
    return "/".join(segments)


def _remote_place(remote: str) -> tuple[str, tuple[str, ...]]:
    name, _, path = remote.partition(":")
    return name, tuple(s for s in path.split("/") if s)


def remote_is_root(remote: str) -> bool:
    return not _remote_place(remote)[1]


def is_valid_cron(expr: str) -> bool:
    # croniter also accepts a sixth (seconds) field.
    return len(expr.split()) == 5 and croniter.is_valid(expr)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def remotes_overlap(a: str, b: str) -> bool:
    name_a, path_a = _remote_place(a)
    name_b, path_b = _remote_place(b)
    if name_a != name_b:
        return False
    shorter = min(len(path_a), len(path_b))
    return path_a[:shorter] == path_b[:shorter]


class SyncMapping(BaseModel):
    drive: str
    path: str = ""
    remote: str

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, v: object) -> str:
        return normalize_mapping_path(v)

    @field_validator("remote")
    @classmethod
    def validate_remote(cls, v: str) -> str:
        if v.startswith("-"):
            raise ValueError("Remote must not start with a dash")
        if ":" not in v:
            raise ValueError("Remote must contain ':' (e.g., 'myremote:path')")
        # A mirror into the remote's root deletes everything else stored there.
        if remote_is_root(v):
            raise ValueError(f"Remote must name a folder, not the root: {v!r}")
        return v


class SyncConfig(BaseModel):
    schedule: str | None = None
    # IANA name the schedule is evaluated in; None is UTC.
    timezone: str | None = None
    # Passed to rclone as --max-delete: a cap on deletes per run, not a check
    # made before the first one.
    max_delete: int = Field(default=200, ge=1, strict=True)
    mappings: list[SyncMapping]

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_cron(v):
            raise ValueError(f"Schedule is not a 5-field cron expression: {v!r}")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_timezone(v):
            raise ValueError(f"Unknown time zone: {v!r}")
        return v

    # Two mirrors writing to one place delete each other's files, so a file that
    # sets that up is refused as a whole rather than synced in part.
    @model_validator(mode="after")
    def validate_mappings_are_distinct(self) -> "SyncConfig":
        seen: set[tuple[str, str]] = set()
        for m in self.mappings:
            if (m.drive, m.path) in seen:
                raise ValueError(
                    f"Duplicate mapping: drive {m.drive!r}, path {m.path!r}"
                )
            seen.add((m.drive, m.path))
        for i, a in enumerate(self.mappings):
            for b in self.mappings[i + 1:]:
                if remotes_overlap(a.remote, b.remote):
                    raise ValueError(
                        f"Remotes overlap: {a.remote!r} and {b.remote!r}"
                    )
        return self


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
    path: str = ""
    remote: str
    status: str = "idle"  # idle | syncing | error | disabled
    last_synced_at: str | None = None
    last_result: SyncResult | None = None
    progress: SyncProgress | None = None
    error_message: str | None = None
    # What went wrong, as a value rather than as prose. The UI needs to tell an
    # expired token from any other rclone failure so it can offer the recovery
    # step, and reading that back out of ``error_message`` would tie the check
    # to the English wording.
    error_kind: str | None = None


class SyncStatusResponse(BaseModel):
    drives: list[SyncDriveStatus]
    schedule: str | None = None
    timezone: str | None = None
    next_sync_at: str | None = None
