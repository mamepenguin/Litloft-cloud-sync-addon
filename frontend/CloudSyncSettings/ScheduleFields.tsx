import { useTranslations } from "next-intl";
import { HOUR_STEPS, type ScheduleDraft, type ScheduleMode } from "../scheduleForm";
import type { ConfigError } from "../settingsApi";
import ErrorText from "./ErrorText";
import { FIELD_CLASS, LABEL_CLASS } from "./styles";

const MODES: ScheduleMode[] = ["off", "daily", "hours", "weekly", "custom"];
const MODE_LABEL: Record<ScheduleMode, string> = {
  off: "modeOff",
  daily: "modeDaily",
  hours: "modeHours",
  weekly: "modeWeekly",
  custom: "modeCustom",
};

function weekdayName(locale: string, day: number): string {
  // 2023-01-01 was a Sunday, which cron counts as 0.
  return new Intl.DateTimeFormat(locale, { weekday: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(2023, 0, 1 + day)),
  );
}

interface Props {
  locale: string;
  schedule: ScheduleDraft;
  timezone: string;
  zones: string[];
  nextSyncAt: string | null;
  scheduleErrors: ConfigError[] | undefined;
  timezoneErrors: ConfigError[] | undefined;
  onSchedule: (next: ScheduleDraft) => void;
  onTimezone: (zone: string) => void;
}

export default function ScheduleFields({
  locale,
  schedule,
  timezone,
  zones,
  nextSyncAt,
  scheduleErrors,
  timezoneErrors,
  onSchedule,
  onTimezone,
}: Props) {
  const t = useTranslations("cloudSyncSettings");
  const set = (patch: Partial<ScheduleDraft>) => onSchedule({ ...schedule, ...patch });

  return (
    <div className="space-y-3">
      <label className="block text-sm">
        <span className={LABEL_CLASS}>{t("schedule")}</span>
        <select
          value={schedule.mode}
          onChange={(e) => set({ mode: e.target.value as ScheduleMode })}
          className={FIELD_CLASS}
        >
          {MODES.map((mode) => (
            <option key={mode} value={mode}>
              {t(MODE_LABEL[mode])}
            </option>
          ))}
        </select>
      </label>

      {schedule.mode === "hours" && (
        <select
          aria-label={t("modeHours")}
          value={schedule.everyHours}
          onChange={(e) => set({ everyHours: Number(e.target.value) })}
          className={FIELD_CLASS}
        >
          {HOUR_STEPS.map((n) => (
            <option key={n} value={n}>
              {t("everyHours", { count: n })}
            </option>
          ))}
        </select>
      )}

      {(schedule.mode === "daily" || schedule.mode === "weekly") && (
        <div className="flex flex-wrap gap-3">
          {schedule.mode === "weekly" && (
            <label className="block min-w-0 flex-1 text-sm">
              <span className={LABEL_CLASS}>{t("weekday")}</span>
              <select
                value={schedule.weekday}
                onChange={(e) => set({ weekday: Number(e.target.value) })}
                className={FIELD_CLASS}
              >
                {[0, 1, 2, 3, 4, 5, 6].map((day) => (
                  <option key={day} value={day}>
                    {weekdayName(locale, day)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="block min-w-0 flex-1 text-sm">
            <span className={LABEL_CLASS}>{t("time")}</span>
            <input
              type="time"
              value={schedule.time}
              onChange={(e) => set({ time: e.target.value })}
              className={FIELD_CLASS}
            />
          </label>
        </div>
      )}

      {schedule.mode === "custom" && (
        <label className="block text-sm">
          <span className={LABEL_CLASS}>{t("cron")}</span>
          <input
            type="text"
            value={schedule.custom}
            onChange={(e) => set({ custom: e.target.value })}
            placeholder="0 3 * * *"
            className={`${FIELD_CLASS} font-mono`}
          />
        </label>
      )}
      <ErrorText errors={scheduleErrors} />

      <label className="block text-sm">
        <span className={LABEL_CLASS}>{t("timezone")}</span>
        <select
          value={timezone}
          onChange={(e) => onTimezone(e.target.value)}
          className={FIELD_CLASS}
        >
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      </label>
      <ErrorText errors={timezoneErrors} />

      {nextSyncAt && (
        <p className="text-xs text-text-muted">
          {t("nextSync", {
            when: new Intl.DateTimeFormat(locale, {
              dateStyle: "medium",
              timeStyle: "short",
            }).format(new Date(nextSyncAt)),
          })}
        </p>
      )}
    </div>
  );
}
