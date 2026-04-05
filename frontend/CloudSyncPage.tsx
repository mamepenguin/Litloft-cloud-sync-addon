"use client";

import { useCallback, useContext, useEffect, useState } from "react";
import { Cloud } from "lucide-react";
import { WebSocketContext } from "@/components/WebSocketProvider";
import { fetchSyncStatus, type SyncDriveStatus, type SyncProgress } from "./api";
import SyncDriveCard from "./SyncDriveCard";

interface SyncProgressEvent {
  drive: string;
  bytes_transferred: number;
  total_bytes: number;
  speed: number;
  eta: number;
  percent: number;
  transfers: number;
  total_transfers: number;
}

interface SyncCompleteEvent {
  drive: string;
  transferred_files: number;
  transferred_bytes: number;
  errors: number;
  elapsed_seconds: number;
}

interface SyncErrorEvent {
  drive: string;
  message: string;
}

export default function CloudSyncPage() {
  const { lastEvent } = useContext(WebSocketContext);
  const [drives, setDrives] = useState<SyncDriveStatus[]>([]);
  const [progressMap, setProgressMap] = useState<
    Record<string, SyncProgress>
  >({});
  const [loading, setLoading] = useState(true);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchSyncStatus();
      setDrives(data);
    } catch {
      // Silently fail, user can refresh
    }
  }, []);

  useEffect(() => {
    loadStatus().finally(() => setLoading(false));
  }, [loadStatus]);

  useEffect(() => {
    if (!lastEvent) return;
    const { event, data } = lastEvent;

    switch (event) {
      case "sync:progress": {
        const d = data as unknown as SyncProgressEvent;
        setProgressMap((prev) => ({
          ...prev,
          [d.drive]: {
            bytes_transferred: d.bytes_transferred,
            total_bytes: d.total_bytes,
            speed: d.speed,
            eta: d.eta,
            percent: d.percent,
            transfers: d.transfers,
            total_transfers: d.total_transfers,
          },
        }));
        setDrives((prev) =>
          prev.map((drive) =>
            drive.drive === d.drive && drive.status !== "syncing"
              ? { ...drive, status: "syncing" as const }
              : drive,
          ),
        );
        break;
      }
      case "sync:complete": {
        const d = data as unknown as SyncCompleteEvent;
        setProgressMap((prev) => {
          const next = { ...prev };
          delete next[d.drive];
          return next;
        });
        setDrives((prev) =>
          prev.map((drive) =>
            drive.drive === d.drive
              ? {
                  ...drive,
                  status: "idle" as const,
                  last_synced_at: new Date().toISOString(),
                  last_result: {
                    transferred_files: d.transferred_files,
                    transferred_bytes: d.transferred_bytes,
                    errors: d.errors,
                    elapsed_seconds: d.elapsed_seconds,
                  },
                  error_message: undefined,
                }
              : drive,
          ),
        );
        break;
      }
      case "sync:error": {
        const d = data as unknown as SyncErrorEvent;
        setProgressMap((prev) => {
          const next = { ...prev };
          delete next[d.drive];
          return next;
        });
        setDrives((prev) =>
          prev.map((drive) =>
            drive.drive === d.drive
              ? {
                  ...drive,
                  status: "error" as const,
                  error_message: d.message,
                }
              : drive,
          ),
        );
        break;
      }
    }
  }, [lastEvent]);

  const handleSyncStarted = useCallback(() => {
    // Refresh status to pick up the syncing state
    loadStatus();
  }, [loadStatus]);

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <h1 className="mb-6 text-2xl font-bold text-text-primary">
          Cloud Sync
        </h1>
        <div className="py-12 text-center text-text-muted">Loading...</div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-6 text-2xl font-bold text-text-primary">Cloud Sync</h1>

      {drives.length === 0 ? (
        <div className="flex flex-col items-center gap-4 py-16 text-text-muted">
          <Cloud size={48} strokeWidth={1.5} />
          <div className="text-center">
            <p className="text-lg font-medium">No drives configured</p>
            <p className="mt-1 text-sm">
              Add drive mappings to{" "}
              <code className="rounded bg-bg-elevated px-1.5 py-0.5 text-xs">
                sync-config.json
              </code>{" "}
              to get started.
            </p>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2">
          {drives.map((drive) => (
            <SyncDriveCard
              key={drive.drive}
              drive={drive}
              progress={progressMap[drive.drive] ?? null}
              onSyncStarted={handleSyncStarted}
            />
          ))}
        </div>
      )}
    </div>
  );
}
