"use client";

import { useCallback, useState } from "react";
import {
  Cloud,
  RefreshCw,
  XCircle,
  CheckCircle,
  AlertTriangle,
  FileText,
  Loader2,
} from "lucide-react";
import { formatFileSize } from "@/lib/format";
import type { SyncDriveStatus, SyncProgress } from "./api";
import { startSync, cancelSync, fetchSyncLog } from "./api";

interface SyncDriveCardProps {
  drive: SyncDriveStatus;
  progress: SyncProgress | null;
  onSyncStarted: () => void;
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  if (mins < 60) return `${mins}m${secs > 0 ? `${secs}s` : ""}`;
  const hours = Math.floor(mins / 60);
  const remainMins = mins % 60;
  return `${hours}h${remainMins > 0 ? `${remainMins}m` : ""}`;
}

function formatEta(seconds: number): string {
  if (seconds <= 0) return "--";
  return formatElapsed(seconds);
}

function formatSpeed(bytesPerSec: number): string {
  return `${formatFileSize(bytesPerSec)}/s`;
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMs / 3600000);
  const diffDay = Math.floor(diffMs / 86400000);

  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}min ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;

  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return y === now.getFullYear() ? `${m}/${d}` : `${y}/${m}/${d}`;
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-primary">
      <div
        className="h-full rounded-full bg-accent-cta transition-all duration-300"
        style={{ width: `${Math.min(percent, 100)}%` }}
      />
    </div>
  );
}

export default function SyncDriveCard({
  drive,
  progress,
  onSyncStarted,
}: SyncDriveCardProps) {
  const [actionLoading, setActionLoading] = useState(false);
  const [logContent, setLogContent] = useState<string | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logOpen, setLogOpen] = useState(false);

  const activeProgress = progress ?? drive.progress;
  const effectiveStatus = drive.status;

  const handleStart = useCallback(async () => {
    setActionLoading(true);
    try {
      await startSync(drive.drive);
      onSyncStarted();
    } catch {
      // Error will surface through WebSocket or next status fetch
    } finally {
      setActionLoading(false);
    }
  }, [drive.drive, onSyncStarted]);

  const handleCancel = useCallback(async () => {
    setActionLoading(true);
    try {
      await cancelSync(drive.drive);
    } catch {
      // Ignore cancel errors
    } finally {
      setActionLoading(false);
    }
  }, [drive.drive]);

  const handleToggleLog = useCallback(async () => {
    if (logOpen) {
      setLogOpen(false);
      return;
    }
    setLogLoading(true);
    try {
      const log = await fetchSyncLog(drive.drive);
      setLogContent(log);
      setLogOpen(true);
    } catch {
      setLogContent("Failed to load log.");
      setLogOpen(true);
    } finally {
      setLogLoading(false);
    }
  }, [drive.drive, logOpen]);

  return (
    <div className="rounded-lg border border-bg-border bg-bg-card p-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-bg-elevated">
            <Cloud size={20} className="text-accent-cta" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-text-primary">
              {drive.drive}
            </h3>
            <p className="truncate text-xs text-text-muted">{drive.remote}</p>
          </div>
        </div>
        <StatusBadge status={effectiveStatus} />
      </div>

      {/* Idle State */}
      {effectiveStatus === "idle" && (
        <div className="mt-4 space-y-3">
          {drive.last_result && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
              {drive.last_result.transferred_files === 0 ? (
                <span>Already up to date</span>
              ) : (
                <>
                  <span>
                    {drive.last_result.transferred_files} files transferred
                  </span>
                  <span>
                    {formatFileSize(drive.last_result.transferred_bytes)}
                  </span>
                </>
              )}
              <span>{formatElapsed(drive.last_result.elapsed_seconds)}</span>
              {drive.last_result.errors > 0 && (
                <span className="text-danger">
                  {drive.last_result.errors} errors
                </span>
              )}
            </div>
          )}
          {drive.last_synced_at && (
            <p className="text-xs text-text-muted">
              Last synced: {formatRelativeTime(drive.last_synced_at)}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={handleStart}
              disabled={actionLoading}
              className="flex items-center gap-1.5 rounded-lg bg-accent-cta px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-cta/80 disabled:opacity-50"
            >
              {actionLoading ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              Sync Now
            </button>
            <button
              onClick={handleToggleLog}
              disabled={logLoading}
              className="flex items-center gap-1.5 rounded-lg border border-bg-border px-3 py-2 text-sm text-text-muted transition-colors hover:bg-bg-elevated disabled:opacity-50"
            >
              <FileText size={14} />
              Log
            </button>
          </div>
        </div>
      )}

      {/* Syncing State */}
      {effectiveStatus === "syncing" && (
        <div className="mt-4 space-y-3">
          {activeProgress && (
            <>
              <ProgressBar percent={activeProgress.percent} />
              <div className="flex flex-wrap justify-between gap-2 text-xs text-text-muted">
                <span>
                  {activeProgress.percent.toFixed(1)}%
                  {activeProgress.total_transfers > 0 &&
                    ` (${activeProgress.transfers}/${activeProgress.total_transfers} files)`}
                </span>
                <span>
                  {[
                    activeProgress.speed > 0 &&
                      formatSpeed(activeProgress.speed),
                    activeProgress.eta > 0 &&
                      `ETA ${formatEta(activeProgress.eta)}`,
                  ]
                    .filter(Boolean)
                    .join(" - ")}
                </span>
              </div>
              <div className="text-xs text-text-muted">
                {formatFileSize(activeProgress.bytes_transferred)} /{" "}
                {formatFileSize(activeProgress.total_bytes)}
              </div>
            </>
          )}
          {!activeProgress && (
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <Loader2 size={14} className="animate-spin" />
              <span>Starting sync...</span>
            </div>
          )}
          <button
            onClick={handleCancel}
            disabled={actionLoading}
            className="flex items-center gap-1.5 rounded-lg border border-danger/30 px-4 py-2 text-sm text-danger transition-colors hover:bg-danger/10 disabled:opacity-50"
          >
            {actionLoading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <XCircle size={14} />
            )}
            Cancel
          </button>
        </div>
      )}

      {/* Error State */}
      {effectiveStatus === "error" && (
        <div className="mt-4 space-y-3">
          {drive.error_message && (
            <div className={`rounded-lg p-3 text-xs ${
              drive.error_message.includes("Authentication expired")
                ? "bg-accent-amber/10 text-accent-amber"
                : "bg-danger/10 text-danger"
            }`}>
              {drive.error_message.includes("Authentication expired") && (
                <div className="mb-1.5 flex items-center gap-1.5 font-semibold">
                  <AlertTriangle size={14} />
                  Re-authentication Required
                </div>
              )}
              {drive.error_message}
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={handleStart}
              disabled={actionLoading}
              className="flex items-center gap-1.5 rounded-lg bg-accent-cta px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-cta/80 disabled:opacity-50"
            >
              {actionLoading ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <RefreshCw size={14} />
              )}
              Retry
            </button>
            <button
              onClick={handleToggleLog}
              disabled={logLoading}
              className="flex items-center gap-1.5 rounded-lg border border-bg-border px-3 py-2 text-sm text-text-muted transition-colors hover:bg-bg-elevated disabled:opacity-50"
            >
              <FileText size={14} />
              Log
            </button>
          </div>
        </div>
      )}

      {/* Log Panel */}
      {logOpen && (
        <div className="mt-4">
          <pre className="max-h-64 overflow-auto rounded-lg bg-bg-primary p-3 text-xs text-text-muted">
            {logContent || "No log available."}
          </pre>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: SyncDriveStatus["status"] }) {
  switch (status) {
    case "idle":
      return (
        <span className="flex items-center gap-1 rounded-full bg-accent-teal/10 px-2.5 py-1 text-xs text-accent-teal">
          <CheckCircle size={12} />
          Idle
        </span>
      );
    case "syncing":
      return (
        <span className="flex items-center gap-1 rounded-full bg-accent-cta/10 px-2.5 py-1 text-xs text-accent-cta">
          <Loader2 size={12} className="animate-spin" />
          Syncing
        </span>
      );
    case "error":
      return (
        <span className="flex items-center gap-1 rounded-full bg-danger/10 px-2.5 py-1 text-xs text-danger">
          <AlertTriangle size={12} />
          Error
        </span>
      );
  }
}
