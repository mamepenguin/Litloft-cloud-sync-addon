import { Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/Button";
import type { ConfigError, DriveChoice } from "../settingsApi";
import type { MappingDraft } from "../settingsDraft";
import ErrorText from "./ErrorText";
import { FIELD_CLASS, LABEL_CLASS } from "./styles";

interface Props {
  index: number;
  mapping: MappingDraft;
  drives: DriveChoice[];
  remotes: string[];
  errors: ConfigError[] | undefined;
  onChange: (next: MappingDraft) => void;
  onRemove: () => void;
}

function byField(errors: ConfigError[] | undefined, field: string): ConfigError[] {
  return (errors ?? []).filter((e) => e.field === field);
}

export default function MappingRow({
  index,
  mapping,
  drives,
  remotes,
  errors,
  onChange,
  onRemove,
}: Props) {
  const t = useTranslations("cloudSyncSettings");
  const set = (patch: Partial<MappingDraft>) => onChange({ ...mapping, ...patch });
  // A stored value missing from today's lists stays selected, so saving
  // without touching the row never moves it to another drive or remote.
  const driveListed = drives.some((d) => d.name === mapping.drive);
  const remoteListed = remotes.includes(mapping.remoteName);

  return (
    <li className="rounded-xl border border-bg-border p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-text-primary">
          {t("row", { row: index + 1 })}
        </span>
        <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
          <Trash2 size={14} aria-hidden="true" />
          {t("removeMapping")}
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block min-w-0 text-sm">
          <span className={LABEL_CLASS}>{t("drive")}</span>
          <select
            value={mapping.drive}
            onChange={(e) => set({ drive: e.target.value })}
            className={FIELD_CLASS}
          >
            {!driveListed && (
              <option value={mapping.drive}>{t("notFound", { name: mapping.drive })}</option>
            )}
            {drives.map((d) => (
              <option key={d.name} value={d.name}>
                {d.enabled ? d.name : t("driveOff", { name: d.name })}
              </option>
            ))}
          </select>
          <ErrorText errors={byField(errors, "drive")} />
        </label>
        <label className="block min-w-0 text-sm">
          <span className={LABEL_CLASS}>{t("folder")}</span>
          <input
            type="text"
            value={mapping.path}
            placeholder={t("folderPlaceholder")}
            onChange={(e) => set({ path: e.target.value })}
            className={FIELD_CLASS}
          />
          <ErrorText errors={byField(errors, "path")} />
        </label>
        <label className="block min-w-0 text-sm">
          <span className={LABEL_CLASS}>{t("remote")}</span>
          <select
            value={mapping.remoteName}
            onChange={(e) => set({ remoteName: e.target.value })}
            className={FIELD_CLASS}
          >
            {!remoteListed && (
              <option value={mapping.remoteName}>
                {mapping.remoteName ? t("notFound", { name: mapping.remoteName }) : "—"}
              </option>
            )}
            {remotes.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="block min-w-0 text-sm">
          <span className={LABEL_CLASS}>{t("remotePath")}</span>
          <input
            type="text"
            value={mapping.remotePath}
            placeholder="litloft/photos"
            onChange={(e) => set({ remotePath: e.target.value })}
            className={FIELD_CLASS}
          />
        </label>
      </div>
      <ErrorText errors={[...byField(errors, "remote"), ...byField(errors, "mappings")]} />
    </li>
  );
}
