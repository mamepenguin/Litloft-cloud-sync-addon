/**
 * The schedule as the settings form edits it, and back to the cron the
 * backend stores.
 *
 * A stored cron is shown as a preset only when that preset writes back the
 * very same string; anything else stays Custom, so opening the form and
 * saving never rewrites a schedule.
 */

export type ScheduleMode = "off" | "daily" | "hours" | "weekly" | "custom";

export const HOUR_STEPS = [1, 2, 3, 4, 6, 8, 12] as const;

export interface ScheduleDraft {
  mode: ScheduleMode;
  // "HH:MM", for daily and weekly.
  time: string;
  everyHours: number;
  // 0 = Sunday, as cron counts.
  weekday: number;
  custom: string;
}

const DEFAULT_DRAFT: ScheduleDraft = {
  mode: "off",
  time: "03:00",
  everyHours: 6,
  weekday: 0,
  custom: "",
};

function hhmm(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function parseTime(time: string): [number, number] {
  const [h, m] = time.split(":").map((part) => parseInt(part, 10));
  return [Number.isFinite(h) ? h : 0, Number.isFinite(m) ? m : 0];
}

export function scheduleToCron(draft: ScheduleDraft): string | null {
  switch (draft.mode) {
    case "off":
      return null;
    case "daily": {
      const [h, m] = parseTime(draft.time);
      return `${m} ${h} * * *`;
    }
    case "hours":
      return `0 */${draft.everyHours} * * *`;
    case "weekly": {
      const [h, m] = parseTime(draft.time);
      return `${m} ${h} * * ${draft.weekday}`;
    }
    case "custom":
      return draft.custom;
  }
}

function asPreset(cron: string): ScheduleDraft | null {
  const daily = /^(\d+) (\d+) \* \* \*$/.exec(cron);
  if (daily) {
    const [m, h] = [Number(daily[1]), Number(daily[2])];
    if (h < 24 && m < 60) return { ...DEFAULT_DRAFT, mode: "daily", time: hhmm(h, m) };
  }
  const hours = /^0 \*\/(\d+) \* \* \*$/.exec(cron);
  if (hours) {
    const n = Number(hours[1]);
    if ((HOUR_STEPS as readonly number[]).includes(n)) {
      return { ...DEFAULT_DRAFT, mode: "hours", everyHours: n };
    }
  }
  const weekly = /^(\d+) (\d+) \* \* ([0-6])$/.exec(cron);
  if (weekly) {
    const [m, h, d] = [Number(weekly[1]), Number(weekly[2]), Number(weekly[3])];
    if (h < 24 && m < 60) {
      return { ...DEFAULT_DRAFT, mode: "weekly", time: hhmm(h, m), weekday: d };
    }
  }
  return null;
}

export function cronToSchedule(cron: string | null): ScheduleDraft {
  if (cron === null) return DEFAULT_DRAFT;
  const preset = asPreset(cron);
  if (preset && scheduleToCron(preset) === cron) return preset;
  return { ...DEFAULT_DRAFT, mode: "custom", custom: cron };
}
