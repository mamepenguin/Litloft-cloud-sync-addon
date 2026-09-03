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
  remote: string;
  status: "idle" | "syncing" | "error";
  last_synced_at: string | null;
  last_result: SyncResult | null;
  progress: SyncProgress | null;
  error_message?: string;
  error_kind?: string;
}

export interface SyncStatusResponse {
  drives: SyncDriveStatus[];
  schedule: string | null;
  next_sync_at: string | null;
}

const BASE = "/api/addons/cloud-sync";

export async function fetchSyncStatus(): Promise<SyncStatusResponse> {
  const res = await fetch(`${BASE}/status`, { credentials: "include" });
  if (!res.ok) return { drives: [], schedule: null, next_sync_at: null };
  return res.json();
}

export async function startSync(
  drive: string,
): Promise<{ status: string; drive: string }> {
  const res = await fetch(`${BASE}/${encodeURIComponent(drive)}/start`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
  return res.json();
}

export async function cancelSync(drive: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(drive)}/cancel`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
}

export async function fetchSyncLog(drive: string): Promise<string> {
  const res = await fetch(`${BASE}/${encodeURIComponent(drive)}/log`, {
    credentials: "include",
  });
  if (!res.ok) return "";
  return res.text();
}
