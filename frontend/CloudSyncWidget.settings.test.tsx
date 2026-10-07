import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { WebSocketContext } from "@/components/WebSocketProvider";
import type { WebSocketEvent } from "@/types";
import CloudSyncWidget from "./CloudSyncWidget";
import SyncDriveCard from "./SyncDriveCard";

// SPEC-ADDON-007: the dashboard card's way to the settings section, its schedule
// line, and its reload when a run ends.

function entry(drive: string, path: string, remote: string, status = "idle") {
  return {
    drive,
    path,
    remote,
    status,
    last_synced_at: null,
    last_result: null,
    progress: null,
    error_message: null,
    error_kind: null,
  };
}

interface StatusBody {
  drives: ReturnType<typeof entry>[];
  schedule: string | null;
  timezone: string | null;
  next_sync_at: string | null;
}

let statusBody: StatusBody;
let statusFetches = 0;
const fetchMock = vi.fn();

beforeEach(() => {
  statusFetches = 0;
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (raw.includes("/status")) statusFetches += 1;
    const body = raw.includes("/status") ? statusBody : {};
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

let push: (event: WebSocketEvent) => void = () => {};

function Harness() {
  const [lastEvent, setLastEvent] = useState<WebSocketEvent | null>(null);
  push = (event) => setLastEvent(event);
  return (
    <WebSocketContext.Provider value={{ lastEvent, connected: true }}>
      <CloudSyncWidget />
    </WebSocketContext.Provider>
  );
}

async function renderWith(body: Partial<StatusBody>) {
  statusBody = { drives: [], schedule: null, timezone: null, next_sync_at: null, ...body };
  render(<Harness />);
  await waitFor(() => expect(statusFetches).toBeGreaterThan(0));
  for (const d of statusBody.drives) {
    await screen.findByText(d.remote);
  }
}

function links(): HTMLAnchorElement[] {
  return Array.from(document.querySelectorAll("a"));
}

describe("SPEC-ADDON-007 CloudSyncWidget settings link", () => {
  it("links to the settings section when mappings exist", async () => {
    await renderWith({ drives: [entry("動画", "", "gd:root")] });

    const link = await screen.findByRole("link", { name: "Settings" });
    expect(link.getAttribute("href")).toBe("/admin/settings");
  });

  it("says Cloud Sync is not set up yet, with the same link, when there are no mappings", async () => {
    await renderWith({ drives: [] });

    await screen.findByText(/Cloud Sync is not set up yet\./);
    expect(links().some((a) => a.getAttribute("href") === "/admin/settings")).toBe(true);
    expect(document.body.textContent).not.toContain("sync-config.json");
  });
});

describe("SPEC-ADDON-007 CloudSyncWidget schedule line", () => {
  it.each([
    ["30 3 * * *", "Asia/Tokyo", /Daily at 03:30 \(Asia\/Tokyo\)/],
    ["0 3 * * *", null, /Daily at 03:00 \(UTC\)/],
    ["0 9 * * 1", null, /Weekly on Mon(day)? at 09:00 \(UTC\)/],
    ["15 22 * * 6", "Asia/Tokyo", /Weekly on Sat(urday)? at 22:15 \(Asia\/Tokyo\)/],
    ["0 3 1 * *", "Asia/Tokyo", /0 3 1 \* \* \(Asia\/Tokyo\)/],
    ["0 3 * * 1,3", null, /0 3 \* \* 1,3 \(UTC\)/],
  ])("describes %j in %s", async (schedule, timezone, expected) => {
    await renderWith({ drives: [entry("動画", "", "gd:root")], schedule, timezone });

    await waitFor(() => expect(document.body.textContent).toMatch(expected));
  });

  it("does not call a weekly schedule daily", async () => {
    await renderWith({ drives: [entry("動画", "", "gd:root")], schedule: "0 9 * * 1", timezone: "UTC" });

    await waitFor(() => expect(document.body.textContent).toMatch(/Weekly on/));
    expect(document.body.textContent).not.toMatch(/Daily at/);
  });
});

describe("SPEC-ADDON-007 CloudSyncWidget reload on a run's end", () => {
  it.each(["sync:complete", "sync:error"])(
    "reloads /status on %s, so a card whose mapping was removed disappears",
    async (event) => {
      await renderWith({ drives: [entry("動画", "a", "gd:a", "syncing"), entry("動画", "", "gd:root")] });
      const before = statusFetches;
      statusBody = { ...statusBody, drives: [entry("動画", "", "gd:root")] };

      act(() =>
        push({
          event,
          data: {
            drive: "動画",
            path: "a",
            message: "done",
            kind: null,
            transferred_files: 0,
            transferred_bytes: 0,
            errors: 0,
            elapsed_seconds: 1,
          },
        } as WebSocketEvent),
      );

      await waitFor(() => expect(statusFetches).toBeGreaterThan(before));
      await waitFor(() => expect(screen.queryByText("gd:a")).toBeNull());
      expect(screen.getByText("gd:root")).toBeInTheDocument();
    },
  );
});

describe("SPEC-ADDON-007 SyncDriveCard delete_limit remedy", () => {
  it("points to the settings section instead of sync-config.json", () => {
    render(
      <SyncDriveCard
        drive={{
          drive: "photos",
          path: "",
          remote: "gdrive:photos",
          status: "error",
          last_synced_at: null,
          last_result: null,
          progress: null,
          error_kind: "delete_limit",
          error_message: "raw backend text",
        }}
        progress={null}
        onSyncStarted={() => {}}
      />,
    );

    expect(screen.getByText("Too many deletions")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("sync-config.json");
    expect(text).toMatch(/settings/i);
  });
});
