export interface SyncProgress {
  bytes_transferred: number;
  total_bytes: number;
  speed: number;
  eta: number;
  percent: number;
  transfers: number;
  total_transfers: number;
}

export interface SyncResult {
  transferred_files: number;
  transferred_bytes: number;
  errors: number;
  elapsed_seconds: number;
}

export interface SyncDriveStatus {
  drive: string;
  // Relative to the drive root; "" is the whole drive.
  path: string;
  remote: string;
  status: "idle" | "syncing" | "error" | "disabled";
  last_synced_at: string | null;
  last_result: SyncResult | null;
  progress: SyncProgress | null;
  error_message?: string;
  error_kind?: string;
}

export interface SyncStatusResponse {
  drives: SyncDriveStatus[];
  schedule: string | null;
  // IANA zone the schedule runs in; null is UTC.
  timezone: string | null;
  next_sync_at: string | null;
}

const BASE = "/api/addons/cloud-sync";

function mappingUrl(drive: string, path: string, action: string): string {
  const url = `${BASE}/${encodeURIComponent(drive)}/${action}`;
  return path ? `${url}?path=${encodeURIComponent(path)}` : url;
}

/** A string key for one (drive, path) mapping that no two mappings share. */
export function mappingKey(drive: string, path: string): string {
  return JSON.stringify([drive, path]);
}

export async function fetchSyncStatus(): Promise<SyncStatusResponse> {
  const res = await fetch(`${BASE}/status`, { credentials: "include" });
  if (!res.ok) return { drives: [], schedule: null, timezone: null, next_sync_at: null };
  return res.json();
}

export async function startSync(
  drive: string,
  path: string,
): Promise<{ status: string; drive: string; path: string }> {
  const res = await fetch(mappingUrl(drive, path, "start"), {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
  return res.json();
}

export async function cancelSync(drive: string, path: string): Promise<void> {
  const res = await fetch(mappingUrl(drive, path, "cancel"), {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
}

export async function fetchSyncLog(drive: string, path: string): Promise<string> {
  const res = await fetch(mappingUrl(drive, path, "log"), {
    credentials: "include",
  });
  if (!res.ok) return "";
  return res.text();
}
