const BASE = "/api/addons/cloud-sync";

export interface SyncMappingConfig {
  drive: string;
  // Relative to the drive root; "" is the whole drive.
  path: string;
  remote: string;
}

export interface SyncConfigBody {
  schedule: string | null;
  timezone: string | null;
  // A number, except when the field holds something that is not one; the
  // backend then refuses it with `invalid_type`.
  max_delete: number | string;
  mappings: SyncMappingConfig[];
}

export interface DriveChoice {
  name: string;
  enabled: boolean;
}

export interface ConfigResponse {
  source: "none" | "saved" | "invalid";
  error: string | null;
  config: SyncConfigBody & { max_delete: number };
  drives: DriveChoice[];
  remotes: string[];
  remotes_error: string | null;
  next_sync_at: string | null;
}

export interface ConfigError {
  mapping: number | null;
  other: number | null;
  field: string;
  code: string;
  message: string;
}

export type SaveOutcome =
  | { kind: "saved"; response: ConfigResponse }
  | { kind: "refused"; errors: ConfigError[] }
  | { kind: "failed" };

/** Throws on any answer but a 200, and on a network failure. */
export async function fetchConfig(): Promise<ConfigResponse> {
  const res = await fetch(`${BASE}/config`, { credentials: "include" });
  if (!res.ok) throw new Error(`Error: ${res.status}`);
  return res.json();
}

export async function saveConfig(body: SyncConfigBody): Promise<SaveOutcome> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/config`, {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return { kind: "failed" };
  }
  if (res.status === 200) {
    return { kind: "saved", response: await res.json() };
  }
  if (res.status === 422) {
    const data = await res.json().catch(() => null);
    if (data && Array.isArray(data.errors)) {
      return { kind: "refused", errors: data.errors };
    }
  }
  return { kind: "failed" };
}
