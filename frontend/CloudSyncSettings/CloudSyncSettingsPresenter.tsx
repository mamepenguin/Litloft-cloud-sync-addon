import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/Button";
import type { ConfigResponse } from "../settingsApi";
import { zoneOptions, type MappingDraft, type SettingsDraft } from "../settingsDraft";
import ErrorText from "./ErrorText";
import type { FieldErrors } from "./fieldErrors";
import MappingRow from "./MappingRow";
import ScheduleFields from "./ScheduleFields";
import { FIELD_CLASS, LABEL_CLASS } from "./styles";

const DOCS_URL =
  "https://github.com/mamepenguin/video-share/blob/develop/docs/addons/cloud-sync.md";

interface Props {
  locale: string;
  loadState: "loading" | "failed" | "ready";
  data: ConfigResponse | null;
  draft: SettingsDraft | null;
  errors: FieldErrors;
  saving: boolean;
  saved: boolean;
  saveFailed: boolean;
  onRetry: () => void;
  onChange: (next: SettingsDraft) => void;
  onAddMapping: () => void;
  onSave: () => void;
}

const SECTION_CLASS = "rounded-xl border border-bg-border bg-bg-card p-4";

export default function CloudSyncSettingsPresenter(props: Props) {
  const t = useTranslations("cloudSyncSettings");
  const { loadState, data, draft } = props;

  const heading = (
    <>
      <h2 className="mb-2 text-base font-semibold text-text-primary">{t("title")}</h2>
      <p className="mb-4 text-xs text-text-muted">{t("description")}</p>
    </>
  );

  if (loadState === "loading" && draft === null) {
    return (
      <section className={SECTION_CLASS}>
        {heading}
        <p className="text-sm text-text-muted">{t("loading")}</p>
      </section>
    );
  }
  if (loadState === "failed" || data === null || draft === null) {
    return (
      <section className={SECTION_CLASS}>
        {heading}
        <p className="text-sm text-danger">{t("loadFailed")}</p>
        <Button type="button" variant="secondary" className="mt-3" onClick={props.onRetry}>
          {t("retry")}
        </Button>
      </section>
    );
  }

  const zones = zoneOptions();
  if (!zones.includes(draft.timezone)) zones.push(draft.timezone);
  const setMapping = (index: number, next: MappingDraft) =>
    props.onChange({
      ...draft,
      mappings: draft.mappings.map((m, i) => (i === index ? next : m)),
    });
  const removeMapping = (index: number) =>
    props.onChange({ ...draft, mappings: draft.mappings.filter((_, i) => i !== index) });

  return (
    <section className={SECTION_CLASS}>
      {heading}

      {data.source === "invalid" && (
        <p className="mb-4 rounded-lg border border-accent-amber bg-bg-elevated p-3 text-sm text-text-primary">
          {t("invalidSaved", { error: data.error ?? "" })}
        </p>
      )}
      {data.remotes.length === 0 && (
        <div className="mb-4 rounded-lg border border-accent-amber bg-bg-elevated p-3 text-sm text-text-primary">
          <p>{t("noRemotes")}</p>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-block text-xs underline"
          >
            {t("docsLink")}
          </a>
        </div>
      )}

      <div className="space-y-6">
        <ScheduleFields
          locale={props.locale}
          schedule={draft.schedule}
          timezone={draft.timezone}
          zones={zones}
          nextSyncAt={data.next_sync_at}
          scheduleErrors={props.errors.top.schedule}
          timezoneErrors={props.errors.top.timezone}
          onSchedule={(schedule) => props.onChange({ ...draft, schedule })}
          onTimezone={(timezone) => props.onChange({ ...draft, timezone })}
        />

        <label className="block text-sm">
          <span className={LABEL_CLASS}>{t("maxDelete")}</span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.maxDelete}
            onChange={(e) => props.onChange({ ...draft, maxDelete: e.target.value })}
            className={FIELD_CLASS}
          />
          <span className="mt-1 block text-xs text-text-muted">{t("maxDeleteHelp")}</span>
          <ErrorText errors={props.errors.top.max_delete} />
        </label>

        <div>
          <h3 className="text-sm font-semibold text-text-primary">{t("mappings")}</h3>
          <p className="mb-2 text-xs text-text-muted">{t("drivesRestartNote")}</p>
          <ErrorText errors={props.errors.list} />
          {draft.mappings.length === 0 ? (
            <p className="text-sm text-text-muted">{t("noMappings")}</p>
          ) : (
            <ol className="space-y-3">
              {draft.mappings.map((mapping, i) => (
                <MappingRow
                  key={i}
                  index={i}
                  mapping={mapping}
                  drives={data.drives}
                  remotes={data.remotes}
                  errors={props.errors.rows[i]}
                  onChange={(next) => setMapping(i, next)}
                  onRemove={() => removeMapping(i)}
                />
              ))}
            </ol>
          )}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-3"
            onClick={props.onAddMapping}
          >
            <Plus size={14} aria-hidden="true" />
            {t("addMapping")}
          </Button>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Button type="button" variant="primary" onClick={props.onSave} disabled={props.saving}>
          {props.saving ? t("saving") : t("save")}
        </Button>
        {props.saved && <span className="text-xs text-accent-teal">{t("saved")}</span>}
        {props.saveFailed && <span className="text-xs text-danger">{t("saveFailed")}</span>}
      </div>
      <ErrorText errors={props.errors.top.body} />
    </section>
  );
}
