import type { ConfigError } from "../settingsApi";

/** A refusal's errors, sorted by where the section shows them. */
export interface FieldErrors {
  // Top-level fields: schedule, timezone, max_delete, body.
  top: Record<string, ConfigError[]>;
  // Errors about two rows, and errors about the list as a whole.
  list: ConfigError[];
  // Errors about one row, keyed by row index.
  rows: Record<number, ConfigError[]>;
}

export const NO_ERRORS: FieldErrors = { top: {}, list: [], rows: {} };

export function sortErrors(errors: ConfigError[]): FieldErrors {
  const out: FieldErrors = { top: {}, list: [], rows: {} };
  for (const error of errors) {
    if (error.other !== null || (error.mapping === null && error.field === "mappings")) {
      out.list.push(error);
    } else if (error.mapping !== null) {
      out.rows[error.mapping] = [...(out.rows[error.mapping] ?? []), error];
    } else {
      out.top[error.field] = [...(out.top[error.field] ?? []), error];
    }
  }
  return out;
}
