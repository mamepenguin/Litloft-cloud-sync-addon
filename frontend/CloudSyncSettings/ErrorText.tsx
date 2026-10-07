import { useTranslations } from "next-intl";
import type { ConfigError } from "../settingsApi";

const KNOWN_CODES = new Set([
  "invalid_body",
  "missing_field",
  "invalid_type",
  "invalid_cron",
  "invalid_timezone",
  "max_delete_below_one",
  "invalid_path",
  "duplicate_mapping",
  "remote_format",
  "remote_root",
  "remotes_overlap",
  "unknown_drive",
  "folder_not_found",
  "remote_not_listed",
  "remotes_unavailable",
]);

/** The section's own wording for each refusal; the backend's message is for logs. */
export default function ErrorText({ errors }: { errors: ConfigError[] | undefined }) {
  const t = useTranslations("cloudSyncSettings");
  if (!errors || errors.length === 0) return null;
  return (
    <>
      {errors.map((error, i) => (
        <p key={`${error.code}-${i}`} className="mt-1 text-xs text-danger">
          {KNOWN_CODES.has(error.code)
            ? t(`errors.${error.code}`, {
                row: (error.mapping ?? 0) + 1,
                other: (error.other ?? 0) + 1,
              })
            : t("saveFailed")}
        </p>
      ))}
    </>
  );
}
