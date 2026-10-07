import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import { slotComponents } from "./slots";

// SPEC-ADDON-007: the Cloud Sync section of /admin/settings, mounted the way the
// admin-settings-sections slot mounts it.

interface Mapping {
  drive: string;
  path: string;
  remote: string;
}

interface Config {
  schedule: string | null;
  timezone: string | null;
  max_delete: number;
  mappings: Mapping[];
}

interface ConfigResponse {
  source: "none" | "saved" | "invalid";
  error: string | null;
  config: Config;
  drives: { name: string; enabled: boolean }[];
  remotes: string[];
  remotes_error: string | null;
  next_sync_at: string | null;
}

const EMPTY: Config = { schedule: null, timezone: null, max_delete: 200, mappings: [] };

function loaded(config: Config, extra: Partial<ConfigResponse> = {}): ConfigResponse {
  return {
    source: "saved",
    error: null,
    config,
    drives: [
      { name: "動画", enabled: true },
      { name: "Photos", enabled: false },
    ],
    remotes: ["gdrive:", "other:", "x:"],
    remotes_error: null,
    next_sync_at: null,
    ...extra,
  };
}

type Reply = { status: number; body: unknown } | "network-error";

let getReplies: Reply[];
let putReply: ((sent: Config) => Reply) | null;
const puts: Config[] = [];
let getCount = 0;
const fetchMock = vi.fn();

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function respond(reply: Reply): Response {
  if (reply === "network-error") throw new TypeError("Failed to fetch");
  return new Response(JSON.stringify(reply.body), {
    status: reply.status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  getReplies = [];
  putReply = null;
  puts.length = 0;
  getCount = 0;
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = urlOf(input);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    if (!url.includes("/api/addons/cloud-sync/config")) {
      return respond({ status: 404, body: { detail: "Not Found" } });
    }
    if (method === "PUT") {
      const sent = JSON.parse(String(init?.body)) as Config;
      puts.push(sent);
      const reply = putReply
        ? putReply(sent)
        : { status: 200, body: loaded(pick(sent)) };
      return respond(reply);
    }
    getCount += 1;
    const reply = getReplies.length > 1 ? getReplies.shift()! : getReplies[0];
    return respond(reply);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function pick(sent: Config): Config {
  return {
    schedule: sent.schedule,
    timezone: sent.timezone,
    max_delete: sent.max_delete,
    mappings: sent.mappings.map((m) => ({ drive: m.drive, path: m.path, remote: m.remote })),
  };
}

function renderSection() {
  const Section = slotComponents["cloud-sync-settings"];
  expect(Section, "slots.ts registers cloud-sync-settings").toBeDefined();
  return render(
    <Suspense fallback={null}>
      <Section />
    </Suspense>,
  );
}

async function openWith(response: ConfigResponse) {
  getReplies = [{ status: 200, body: response }];
  renderSection();
  return screen.findByRole("button", { name: "Save" });
}

async function saveWithoutEdits(response: ConfigResponse): Promise<Config> {
  const save = await openWith(response);
  fireEvent.click(save);
  await waitFor(() => expect(puts).toHaveLength(1));
  return pick(puts[0]);
}

function timeZoneSelect(zone: string): HTMLSelectElement {
  const matches = screen
    .getAllByDisplayValue(zone)
    .filter((el): el is HTMLSelectElement => el instanceof HTMLSelectElement);
  expect(matches).toHaveLength(1);
  return matches[0];
}

describe("SPEC-ADDON-007 Cloud Sync settings section: saving without edits (I9)", () => {
  const cases: [string, Config, Config][] = [
    [
      "a full configuration",
      {
        schedule: "30 3 * * *",
        timezone: "Asia/Tokyo",
        max_delete: 50,
        mappings: [
          { drive: "動画", path: "仕事/録画", remote: "gdrive:litloft/録画" },
          { drive: "Photos", path: "", remote: "other:x/y" },
          { drive: "動画", path: "a", remote: "x:/a" },
        ],
      },
      {
        schedule: "30 3 * * *",
        timezone: "Asia/Tokyo",
        max_delete: 50,
        mappings: [
          { drive: "動画", path: "仕事/録画", remote: "gdrive:litloft/録画" },
          { drive: "Photos", path: "", remote: "other:x/y" },
          { drive: "動画", path: "a", remote: "x:/a" },
        ],
      },
    ],
    [
      "a null time zone, saved as UTC",
      { ...EMPTY, schedule: "0 3 * * *", timezone: null },
      { ...EMPTY, schedule: "0 3 * * *", timezone: "UTC" },
    ],
    [
      "a schedule that is off",
      { ...EMPTY, schedule: null, timezone: "Asia/Tokyo" },
      { ...EMPTY, schedule: null, timezone: "Asia/Tokyo" },
    ],
    [
      "a drive and a remote that are not listed",
      {
        ...EMPTY,
        mappings: [
          { drive: "Gone", path: "x", remote: "old:keep/this" },
          { drive: "動画", path: "", remote: "gdrive:a:b" },
        ],
      },
      {
        ...EMPTY,
        timezone: "UTC",
        mappings: [
          { drive: "Gone", path: "x", remote: "old:keep/this" },
          { drive: "動画", path: "", remote: "gdrive:a:b" },
        ],
      },
    ],
    ["a zone alias", { ...EMPTY, timezone: "Etc/UTC" }, { ...EMPTY, timezone: "Etc/UTC" }],
    [
      "another zone alias",
      { ...EMPTY, timezone: "Asia/Calcutta" },
      { ...EMPTY, timezone: "Asia/Calcutta" },
    ],
  ];

  it.each(cases)("writes back %s unchanged", async (_label, stored, expected) => {
    const sent = await saveWithoutEdits(loaded(stored));

    expect(sent).toEqual(expected);
  });

  it.each([
    "0 3 * * *",
    "30 23 * * *",
    "0 */6 * * *",
    "0 */1 * * *",
    "0 */12 * * *",
    "0 9 * * 1",
    "0 0 * * 0",
    "45 18 * * 6",
    "0 */5 * * *",
    "5 */6 * * *",
    "00 3 * * *",
    "0 03 * * *",
    "0 3 * * 7",
    "0 3 * * 1,3",
    "0 3 * * MON",
    "0 3 1 * *",
    "*/15 * * * *",
    "0 3 * * 1-5",
  ])("writes back the schedule %j exactly", async (schedule) => {
    const sent = await saveWithoutEdits(loaded({ ...EMPTY, schedule, timezone: "UTC" }));

    expect(sent.schedule).toBe(schedule);
  });
});

describe("SPEC-ADDON-007 Cloud Sync settings section: time zone control", () => {
  it("lists UTC first, then every zone the browser supports", async () => {
    await openWith(loaded({ ...EMPTY, timezone: "Asia/Tokyo" }));

    const select = timeZoneSelect("Asia/Tokyo");
    const values = Array.from(select.options).map((o) => o.value);
    expect(values[0]).toBe("UTC");
    for (const zone of Intl.supportedValuesOf("timeZone")) {
      expect(values).toContain(zone);
    }
  });

  it("keeps a stored zone that is not among the options selected", async () => {
    await openWith(loaded({ ...EMPTY, timezone: "Etc/UTC" }));

    expect(timeZoneSelect("Etc/UTC").value).toBe("Etc/UTC");
  });

  it.each(["none", "invalid"] as const)(
    "starts from the browser's zone when the source is %s",
    async (source) => {
      const real = Intl.DateTimeFormat.prototype.resolvedOptions;
      vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (
        this: Intl.DateTimeFormat,
      ) {
        return { ...real.call(this), timeZone: "America/New_York" };
      });

      const sent = await saveWithoutEdits(
        loaded(EMPTY, { source, error: source === "invalid" ? "schema_version must be 1" : null }),
      );

      expect(sent).toEqual({ ...EMPTY, timezone: "America/New_York" });
    },
  );

  it("falls back to UTC when the browser's zone is not among the options", async () => {
    const real = Intl.DateTimeFormat.prototype.resolvedOptions;
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (
      this: Intl.DateTimeFormat,
    ) {
      return { ...real.call(this), timeZone: "Mars/Olympus" };
    });

    const sent = await saveWithoutEdits(loaded(EMPTY, { source: "none" }));

    expect(sent.timezone).toBe("UTC");
  });
});

describe("SPEC-ADDON-007 Cloud Sync settings section: drive and remote selects", () => {
  it("offers a drive whose policy is off, marked as such", async () => {
    await openWith(
      loaded({ ...EMPTY, mappings: [{ drive: "動画", path: "", remote: "gdrive:a" }] }),
    );

    const off = screen
      .getAllByRole("option")
      .filter((o) => (o as HTMLOptionElement).value === "Photos");
    expect(off.length).toBeGreaterThan(0);
    for (const option of off) {
      expect(option.textContent).toContain("(cloud sync off)");
    }
  });

  it("keeps an unlisted drive and remote selected, marked not found", async () => {
    await openWith(
      loaded({ ...EMPTY, mappings: [{ drive: "Gone", path: "", remote: "old:path" }] }),
    );

    const options = screen.getAllByRole("option") as HTMLOptionElement[];
    const drive = options.find((o) => o.value === "Gone");
    const remote = options.find((o) => o.value === "old:");
    expect(drive?.selected).toBe(true);
    expect(drive?.textContent).toMatch(/not found/i);
    expect(remote?.selected).toBe(true);
    expect(remote?.textContent).toMatch(/not found/i);
  });

  it("splits a stored remote at its first colon", async () => {
    await openWith(
      loaded({ ...EMPTY, mappings: [{ drive: "動画", path: "", remote: "gdrive:a:b/c" }] }),
    );

    const remote = (screen.getAllByRole("option") as HTMLOptionElement[]).find(
      (o) => o.value === "gdrive:" && o.selected,
    );
    expect(remote).toBeDefined();
    expect(screen.getByDisplayValue("a:b/c")).toBeInstanceOf(HTMLInputElement);
  });

  it("says the container sees no remotes when there are none", async () => {
    await openWith(loaded(EMPTY, { remotes: [], remotes_error: "rclone: not found" }));

    expect(document.body.textContent).toMatch(/No rclone remotes are visible to Litloft/);
  });
});

describe("SPEC-ADDON-007 Cloud Sync settings section: loading", () => {
  it.each([
    ["a 500", { status: 500, body: { detail: "boom" } }],
    ["a 403", { status: 403, body: { detail: "Admin access required" } }],
    ["a 502", { status: 502, body: {} }],
    ["a 504", { status: 504, body: {} }],
    ["a network failure", "network-error"],
  ] as [string, Reply][])("shows the load error with Retry and no form on %s", async (_l, reply) => {
    getReplies = [reply, { status: 200, body: loaded(EMPTY) }];
    renderSection();

    await screen.findByText(/Could not load the Cloud Sync settings/);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await screen.findByRole("button", { name: "Save" });
    expect(getCount).toBe(2);
    expect(screen.queryByText(/Could not load the Cloud Sync settings/)).toBeNull();
  });

  it("shows why the saved settings could not be read, with an empty form", async () => {
    const save = await openWith(
      loaded(EMPTY, { source: "invalid", error: "schema_version must be 1" }),
    );

    expect(document.body.textContent).toMatch(
      /The saved settings could not be read: schema_version must be 1\.? ?Saving will replace them\./,
    );

    fireEvent.click(save);
    await waitFor(() => expect(puts).toHaveLength(1));
    const sent = pick(puts[0]);
    expect(sent.mappings).toEqual([]);
    expect(sent.schedule).toBeNull();
  });
});

describe("SPEC-ADDON-007 Cloud Sync settings section: saving", () => {
  const stored: Config = {
    schedule: "0 3 * * *",
    timezone: "UTC",
    max_delete: 137,
    mappings: [{ drive: "動画", path: "", remote: "gdrive:a" }],
  };

  async function editMaxDelete(to: string) {
    await openWith(loaded(stored));
    fireEvent.change(screen.getByDisplayValue("137"), { target: { value: to } });
  }

  it("sends the edit, renders from the response and says Saved until the next edit", async () => {
    putReply = (sent) => ({ status: 200, body: loaded({ ...pick(sent), max_delete: 71 }) });
    await editMaxDelete("50");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Saved");
    expect(puts.map(pick)).toEqual([{ ...stored, max_delete: 50 }]);
    expect(screen.getByDisplayValue("71")).toBeInstanceOf(HTMLInputElement);
    expect(getCount).toBe(1);

    fireEvent.change(screen.getByDisplayValue("71"), { target: { value: "72" } });
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it.each([
    ["a 500", { status: 500, body: { detail: "write failed" } }],
    ["a 403", { status: 403, body: { detail: "Admin access required" } }],
    ["a 502", { status: 502, body: {} }],
    ["a network failure", "network-error"],
  ] as [string, Reply][])("keeps the edits and says it could not save on %s", async (_l, reply) => {
    putReply = () => reply;
    await editMaxDelete("50");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText(/Could not save the settings/);
    expect(screen.getByDisplayValue("50")).toBeInstanceOf(HTMLInputElement);
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("explains a refusal because the remotes could not be listed and keeps the edits", async () => {
    putReply = () => ({
      status: 422,
      body: {
        errors: [
          {
            mapping: null,
            other: null,
            field: "mappings",
            code: "remotes_unavailable",
            message: "rclone listremotes failed",
          },
        ],
      },
    });
    await editMaxDelete("50");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });

    await screen.findByText(
      /Could not list the rclone remotes, so the mappings could not be checked\./,
    );
    expect(screen.getByDisplayValue("50")).toBeInstanceOf(HTMLInputElement);
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("shows its own text for a refusal, not the backend's message", async () => {
    putReply = () => ({
      status: 422,
      body: {
        errors: [
          {
            mapping: null,
            other: null,
            field: "max_delete",
            code: "max_delete_below_one",
            message: "backend-only-wording-xyz",
          },
        ],
      },
    });
    await editMaxDelete("0");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("Saved")).toBeNull());
    expect(document.body.textContent).not.toContain("backend-only-wording-xyz");
    expect(screen.getByDisplayValue("0")).toBeInstanceOf(HTMLInputElement);
  });
});
