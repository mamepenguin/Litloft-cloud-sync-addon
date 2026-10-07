import { cronToSchedule, scheduleToCron, type ScheduleDraft } from "./scheduleForm";
import type { ConfigResponse, SyncConfigBody } from "./settingsApi";

export interface MappingDraft {
  drive: string;
  path: string;
  // "name:" as `rclone listremotes` prints it, colon included.
  remoteName: string;
  // Everything after the first colon, exactly as written.
  remotePath: string;
}

export interface SettingsDraft {
  schedule: ScheduleDraft;
  timezone: string;
  maxDelete: string;
  mappings: MappingDraft[];
}

export function splitRemote(remote: string): [string, string] {
  const colon = remote.indexOf(":");
  if (colon < 0) return ["", remote];
  return [remote.slice(0, colon + 1), remote.slice(colon + 1)];
}

export function zoneOptions(): string[] {
  const supported =
    typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return ["UTC", ...supported.filter((zone) => zone !== "UTC")];
}

function browserZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

// A saved file with no zone has been running in UTC, so the form keeps it
// there; only a configuration that was never saved starts from the browser.
function initialZone(data: ConfigResponse): string {
  if (data.source === "saved") return data.config.timezone ?? "UTC";
  const zone = browserZone();
  return zone && zoneOptions().includes(zone) ? zone : "UTC";
}

export function toDraft(data: ConfigResponse): SettingsDraft {
  return {
    schedule: cronToSchedule(data.config.schedule),
    timezone: initialZone(data),
    maxDelete: String(data.config.max_delete),
    mappings: data.config.mappings.map((m) => {
      const [remoteName, remotePath] = splitRemote(m.remote);
      return { drive: m.drive, path: m.path, remoteName, remotePath };
    }),
  };
}

export function toBody(draft: SettingsDraft): SyncConfigBody {
  const trimmed = draft.maxDelete.trim();
  return {
    schedule: scheduleToCron(draft.schedule),
    timezone: draft.timezone,
    max_delete: /^-?\d+$/.test(trimmed) ? Number(trimmed) : draft.maxDelete,
    mappings: draft.mappings.map((m) => ({
      drive: m.drive,
      path: m.path,
      remote: m.remoteName + m.remotePath,
    })),
  };
}

export function emptyMapping(data: ConfigResponse): MappingDraft {
  return {
    drive: data.drives[0]?.name ?? "",
    path: "",
    remoteName: data.remotes[0] ?? "",
    remotePath: "",
  };
}
