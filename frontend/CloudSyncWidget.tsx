"use client";

import { useCallback, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { Clock, Cloud, Settings } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { WebSocketContext } from "@/components/WebSocketProvider";
import {
  fetchSyncStatus,
  mappingKey,
  type SyncDriveStatus,
  type SyncProgress,
} from "./api";
import SyncDriveCard from "./SyncDriveCard";

type Translate = ReturnType<typeof useTranslations>;

const SETTINGS_HREF = "/admin/settings";

function weekdayName(locale: string, day: number): string {
  // 2023-01-01 was a Sunday, which cron counts as 0.
  return new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(2023, 0, 1 + day)),
  );
}

function describeCron(
  t: Translate,
  locale: string,
  expr: string,
  timezone: string | null,
): string {
  const zone = timezone ?? "UTC";
  const parts = expr.trim().split(/\s+/);
  const custom = t("customSchedule", { expr, zone });
  if (parts.length !== 5) return custom;
  const [min, hour, dom, month, dow] = parts;
  const numeric = (v: string) => /^\d+$/.test(v);
  const time = () => `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (/^\*\/\d+$/.test(min) && hour === "*" && dom === "*" && month === "*" && dow === "*") {
    return t("everyMinutes", { count: parseInt(min.slice(2), 10) });
  }
  if (numeric(min) && /^\*\/\d+$/.test(hour) && dom === "*" && month === "*" && dow === "*") {
    return t("everyHours", { count: parseInt(hour.slice(2), 10) });
  }
  if (numeric(min) && numeric(hour) && dom === "*" && month === "*") {
    if (dow === "*") return t("dailyAt", { time: time(), zone });
    if (/^[0-6]$/.test(dow)) {
      return t("weeklyAt", { weekday: weekdayName(locale, Number(dow)), time: time(), zone });
    }
  }
  return custom;
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
  path: string;
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
  path: string;
  transferred_files: number;
  transferred_bytes: number;
  errors: number;
  elapsed_seconds: number;
}

interface SyncErrorEvent {
  drive: string;
  path: string;
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
  const [timezone, setTimezone] = useState<string | null>(null);
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
      setTimezone(data.timezone ?? null);
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
          [mappingKey(d.drive, d.path)]: {
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
            drive.drive === d.drive && drive.path === d.path && drive.status !== "syncing"
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
          delete next[mappingKey(d.drive, d.path)];
          return next;
        });
        setDrives((prev) =>
          prev.map((drive) =>
            drive.drive === d.drive && drive.path === d.path
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
        // A run whose mapping was removed from the settings leaves /status
        // when it ends.
        loadStatus();
        break;
      }
      case "sync:error": {
        const d = data as unknown as SyncErrorEvent;
        setProgressMap((prev) => {
          const next = { ...prev };
          delete next[mappingKey(d.drive, d.path)];
          return next;
        });
        setDrives((prev) =>
          prev.map((drive) =>
            drive.drive === d.drive && drive.path === d.path
              ? {
                  ...drive,
                  status: "error" as const,
                  error_message: d.message,
                  error_kind: d.kind ?? undefined,
                }
              : drive,
          ),
        );
        loadStatus();
        break;
      }
    }
  }, [lastEvent, loadStatus]);

  const handleSyncStarted = useCallback(() => {
    loadStatus();
  }, [loadStatus]);

  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold text-text-muted">
          {t("title")}
        </h2>
        <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
        {schedule && (
          <div className="flex items-center gap-1.5 text-xs text-text-muted">
            <Clock size={12} />
            <span>{describeCron(t, locale, schedule, timezone)}</span>
            {nextSyncAt && (
              <span className="text-text-muted/60">
                &middot; {t("nextSync", { when: formatNextSync(t, locale, nextSyncAt) })}
              </span>
            )}
          </div>
        )}
          <Link
            href={SETTINGS_HREF}
            className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary"
          >
            <Settings size={12} aria-hidden="true" />
            {t("settings")}
          </Link>
        </div>
      </div>

      {loading ? (
        <div className="py-8 text-center text-sm text-text-muted">{t("loading")}</div>
      ) : drives.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-bg-border bg-bg-card py-10 text-text-muted">
          <Cloud size={36} strokeWidth={1.5} />
          <div className="text-center">
            <p className="text-sm font-medium">{t("notSetUp")}</p>
            <Link href={SETTINGS_HREF} className="mt-1 inline-block text-xs underline">
              {t("settings")}
            </Link>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-1 md:grid-cols-2">
          {drives.map((drive) => (
            <SyncDriveCard
              key={mappingKey(drive.drive, drive.path)}
              drive={drive}
              progress={progressMap[mappingKey(drive.drive, drive.path)] ?? null}
              onSyncStarted={handleSyncStarted}
            />
          ))}
        </div>
      )}
    </section>
  );
}
