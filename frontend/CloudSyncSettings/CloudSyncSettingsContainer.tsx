"use client";

import { useCallback, useEffect, useState } from "react";
import { useLocale } from "next-intl";
import { fetchConfig, saveConfig, type ConfigResponse } from "../settingsApi";
import { emptyMapping, toBody, toDraft, type SettingsDraft } from "../settingsDraft";
import CloudSyncSettingsPresenter from "./CloudSyncSettingsPresenter";
import { NO_ERRORS, sortErrors, type FieldErrors } from "./fieldErrors";

type LoadState = "loading" | "failed" | "ready";

export default function CloudSyncSettingsContainer() {
  const locale = useLocale();
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>(NO_ERRORS);

  const load = useCallback(async () => {
    setLoadState("loading");
    try {
      const response = await fetchConfig();
      setData(response);
      setDraft(toDraft(response));
      setLoadState("ready");
    } catch {
      setLoadState("failed");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const edit = useCallback((next: SettingsDraft) => {
    setDraft(next);
    setSaved(false);
  }, []);

  const save = useCallback(async () => {
    if (draft === null) return;
    setSaving(true);
    setSaved(false);
    setSaveFailed(false);
    const outcome = await saveConfig(toBody(draft));
    setSaving(false);
    if (outcome.kind === "saved") {
      setData(outcome.response);
      setDraft(toDraft(outcome.response));
      setErrors(NO_ERRORS);
      setSaved(true);
    } else if (outcome.kind === "refused") {
      setErrors(sortErrors(outcome.errors));
    } else {
      setSaveFailed(true);
    }
  }, [draft]);

  const addMapping = useCallback(() => {
    if (draft === null || data === null) return;
    edit({ ...draft, mappings: [...draft.mappings, emptyMapping(data)] });
  }, [data, draft, edit]);

  return (
    <CloudSyncSettingsPresenter
      locale={locale}
      loadState={loadState}
      data={data}
      draft={draft}
      errors={errors}
      saving={saving}
      saved={saved}
      saveFailed={saveFailed}
      onRetry={load}
      onChange={edit}
      onAddMapping={addMapping}
      onSave={save}
    />
  );
}
