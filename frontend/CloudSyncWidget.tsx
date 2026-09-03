"use client";

import { useCallback, useContext, useEffect, useState } from "react";
import { Clock, Cloud } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { WebSocketContext } from "@/components/WebSocketProvider";
import { fetchSyncStatus, type SyncDriveStatus, type SyncProgress } from "./api";
import SyncDriveCard from "./SyncDriveCard";

type Translate = ReturnType<typeof useTranslations>;

function describeCron(t: Translate, expr: string): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [min, hour, , , ] = parts;
  if (hour.startsWith("*/")) {
    return t("everyHours", { count: parseInt(hour.slice(2), 10) });
  }
  if (min.startsWith("*/")) {
    return t("everyMinutes", { count: parseInt(min.slice(2), 10) });
  }
  if (hour !== "*" && min !== "*") {
    return t("dailyAt", {
      time: `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`,
    });
  }
  return expr;
}

function formatNextSync(t: Translate, locale: string, isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return t("syncShortly");
  if (diffMin < 60) return t("syncInMinutes", { count: diffMin });
  const diffHour = Math.round(diffMs / 3600000);
  if (diffHour < 24) return t("syncInHours", { count: diffHour });
  // Beyond a day out, an absolute date. Ordering is the locale's to decide —
  // `${month}/${day}` reads as May 6th to a Japanese reader and June 5th to an
  // American one, and neither is told which was meant.
  return new Intl.DateTimeFormat(locale, {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

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
  // Absent on an event from an older backend; assigning it unconditionally
  // below is what stops a previous failure's kind from surviving into this one.
  kind?: string | null;
}

export default function CloudSyncWidget() {
  const t = useTranslations("cloudSync");
  const locale = useLocale();
  const { lastEvent } = useContext(WebSocketContext);
  const [drives, setDrives] = useState<SyncDriveStatus[]>([]);
  const [schedule, setSchedule] = useState<string | null>(null);
  const [nextSyncAt, setNextSyncAt] = useState<string | null>(null);
  const [progressMap, setProgressMap] = useState<
    Record<string, SyncProgress>
  >({});
  const [loading, setLoading] = useState(true);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchSyncStatus();
      setDrives(data.drives);
      setSchedule(data.schedule);
      setNextSyncAt(data.next_sync_at);
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
                  error_kind: undefined,
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
                  error_kind: d.kind ?? undefined,
                }
              : drive,
          ),
        );
        break;
      }
    }
  }, [lastEvent]);

  const handleSyncStarted = useCallback(() => {
    loadStatus();
  }, [loadStatus]);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase text-text-muted">
          {t("title")}
        </h2>
        {schedule && (
          <div className="flex items-center gap-1.5 text-xs text-text-muted">
            <Clock size={12} />
            <span>{describeCron(t, schedule)}</span>
            {nextSyncAt && (
              <span className="text-text-muted/60">
                &middot; {t("nextSync", { when: formatNextSync(t, locale, nextSyncAt) })}
              </span>
            )}
          </div>
        )}
      </div>

      {loading ? (
        <div className="py-8 text-center text-sm text-text-muted">{t("loading")}</div>
      ) : drives.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-bg-border bg-bg-card py-10 text-text-muted">
          <Cloud size={36} strokeWidth={1.5} />
          <div className="text-center">
            <p className="text-sm font-medium">{t("noDrivesTitle")}</p>
            <p className="mt-1 text-xs">
              {t.rich("noDrivesDescription", {
                code: (chunks) => (
                  <code className="rounded-lg bg-bg-elevated px-1.5 py-0.5 text-xs">
                    {chunks}
                  </code>
                ),
              })}
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
    </section>
  );
}
