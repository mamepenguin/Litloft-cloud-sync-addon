"""Validation of a `PUT /config` body.

Every violation is collected, so the settings section can mark every field at once.
The rules that need the environment (drives, folders, rclone remotes) are applied by
the service on the rows this module accepted.
"""
from dataclasses import dataclass, field

from .schemas import (
    SyncConfig,
    SyncMapping,
    is_valid_cron,
    is_valid_timezone,
    normalize_mapping_path,
    remote_is_root,
    remotes_overlap,
)

_TOP_LEVEL_KEYS = ("schedule", "timezone", "max_delete", "mappings")
_ROW_KEYS = ("drive", "path", "remote")


@dataclass(frozen=True)
class ConfigError:
    field: str
    code: str
    message: str
    mapping: int | None = None
    other: int | None = None

    def as_dict(self) -> dict:
        return {
            "mapping": self.mapping,
            "other": self.other,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class CheckedRow:
    """A row whose shape is sound, with what the pure rules found of it."""

    index: int
    drive: str
    path: str | None  # normalized; None when the path rule refused it
    remote: str
    remote_ok: bool  # format and root rules passed


@dataclass
class BodyCheck:
    errors: list[ConfigError] = field(default_factory=list)
    rows: list[CheckedRow] = field(default_factory=list)
    schedule: str | None = None
    timezone: str | None = None
    max_delete: int | None = None
    shape_ok: bool = True

    def to_config(self) -> SyncConfig:
        return SyncConfig(
            schedule=self.schedule,
            timezone=self.timezone,
            max_delete=self.max_delete,
            mappings=[
                SyncMapping(drive=r.drive, path=r.path or "", remote=r.remote)
                for r in self.rows
            ],
        )


def check_body(raw: object) -> BodyCheck:
    out = BodyCheck()
    if not isinstance(raw, dict):
        out.shape_ok = False
        out.errors.append(ConfigError("body", "invalid_body", "The body must be a JSON object"))
        return out

    for key in _TOP_LEVEL_KEYS:
        if key not in raw:
            out.shape_ok = False
            out.errors.append(ConfigError(key, "missing_field", f"{key} is required"))

    _check_schedule(raw, out)
    _check_timezone(raw, out)
    _check_max_delete(raw, out)
    if "mappings" in raw:
        _check_mappings(raw["mappings"], out)
    return out


def _check_schedule(raw: dict, out: BodyCheck) -> None:
    if "schedule" not in raw:
        return
    value = raw["schedule"]
    if value is not None and not isinstance(value, str):
        out.shape_ok = False
        out.errors.append(ConfigError("schedule", "invalid_type", "schedule must be a string or null"))
    elif value is not None and not is_valid_cron(value):
        out.errors.append(ConfigError(
            "schedule", "invalid_cron", f"Not a 5-field cron expression: {value!r}",
        ))
    else:
        out.schedule = value


def _check_timezone(raw: dict, out: BodyCheck) -> None:
    if "timezone" not in raw:
        return
    value = raw["timezone"]
    if value is not None and not isinstance(value, str):
        out.shape_ok = False
        out.errors.append(ConfigError("timezone", "invalid_type", "timezone must be a string or null"))
    elif value is not None and not is_valid_timezone(value):
        out.errors.append(ConfigError("timezone", "invalid_timezone", f"Unknown time zone: {value!r}"))
    else:
        out.timezone = value


def _check_max_delete(raw: dict, out: BodyCheck) -> None:
    if "max_delete" not in raw:
        return
    value = raw["max_delete"]
    if not isinstance(value, int) or isinstance(value, bool):
        out.shape_ok = False
        out.errors.append(ConfigError("max_delete", "invalid_type", "max_delete must be an integer"))
    elif value < 1:
        out.errors.append(ConfigError("max_delete", "max_delete_below_one", "max_delete must be at least 1"))
    else:
        out.max_delete = value


def _check_mappings(value: object, out: BodyCheck) -> None:
    if not isinstance(value, list):
        out.shape_ok = False
        out.errors.append(ConfigError("mappings", "invalid_type", "mappings must be a list"))
        return
    for index, item in enumerate(value):
        row = _check_row(index, item, out)
        if row is not None:
            out.rows.append(row)
    _check_cross_rows(out)


def _check_row(index: int, item: object, out: BodyCheck) -> CheckedRow | None:
    if not isinstance(item, dict):
        out.shape_ok = False
        out.errors.append(ConfigError("mappings", "invalid_type", "A mapping must be an object", index))
        return None
    shape_errors = []
    for key in _ROW_KEYS:
        if key not in item:
            shape_errors.append(ConfigError(key, "missing_field", f"{key} is required", index))
        elif not isinstance(item[key], str):
            shape_errors.append(ConfigError(key, "invalid_type", f"{key} must be a string", index))
    if shape_errors:
        out.shape_ok = False
        out.errors.extend(shape_errors)
        return None

    try:
        path: str | None = normalize_mapping_path(item["path"])
    except ValueError as exc:
        path = None
        out.errors.append(ConfigError("path", "invalid_path", str(exc), index))

    remote = item["remote"]
    remote_ok = False
    if remote.startswith("-") or ":" not in remote:
        out.errors.append(ConfigError(
            "remote", "remote_format",
            f"Remote must look like 'name:path' and not start with '-': {remote!r}", index,
        ))
    elif remote_is_root(remote):
        out.errors.append(ConfigError(
            "remote", "remote_root", f"Remote must name a folder, not the root: {remote!r}", index,
        ))
    else:
        remote_ok = True
    return CheckedRow(index, item["drive"], path, remote, remote_ok)


def _check_cross_rows(out: BodyCheck) -> None:
    rows = out.rows
    for j, later in enumerate(rows):
        earlier_dup = next(
            (r for r in rows[:j]
             if later.path is not None and (r.drive, r.path) == (later.drive, later.path)),
            None,
        )
        if earlier_dup is not None:
            out.errors.append(ConfigError(
                "mappings", "duplicate_mapping",
                f"Mappings {earlier_dup.index + 1} and {later.index + 1} name the same folder",
                later.index, earlier_dup.index,
            ))
        earlier_overlap = next(
            (r for r in rows[:j]
             if later.remote_ok and r.remote_ok and remotes_overlap(r.remote, later.remote)),
            None,
        )
        if earlier_overlap is not None:
            out.errors.append(ConfigError(
                "mappings", "remotes_overlap",
                f"Remotes overlap: {earlier_overlap.remote!r} and {later.remote!r}",
                later.index, earlier_overlap.index,
            ))
