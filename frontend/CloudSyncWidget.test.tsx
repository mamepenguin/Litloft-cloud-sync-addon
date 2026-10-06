import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { WebSocketContext } from "@/components/WebSocketProvider";
import type { WebSocketEvent } from "@/types";
import CloudSyncWidget from "./CloudSyncWidget";

// SPEC-ADDON-003 (I8): the dashboard applies an event to the card of the
// (drive, path) it names, and to no other card.

interface Entry {
  drive: string;
  path: string;
  remote: string;
  status: string;
}

function entry(drive: string, path: string, remote: string, status = "syncing") {
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

let statusBody: { drives: Entry[]; schedule: null; next_sync_at: null };
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
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

/** The largest element holding this remote and no other card's remote. */
function cardOf(remote: string, allRemotes: string[]): HTMLElement {
  let node: HTMLElement = screen.getByText(remote);
  const others = allRemotes.filter((r) => r !== remote);
  while (node.parentElement) {
    const text = node.parentElement.textContent ?? "";
    if (others.some((r) => text.includes(r))) break;
    node = node.parentElement;
  }
  return node;
}

function progressEvent(drive: string, path: string): WebSocketEvent {
  return {
    event: "sync:progress",
    data: {
      drive,
      path,
      bytes_transferred: 50,
      total_bytes: 100,
      speed: 10,
      eta: 5,
      percent: 50,
      transfers: 3,
      total_transfers: 7,
    },
  };
}

async function renderWith(drives: Entry[]) {
  statusBody = { drives, schedule: null, next_sync_at: null };
  render(<Harness />);
  for (const d of drives) {
    await screen.findByText(d.remote);
  }
}

describe("SPEC-ADDON-003 CloudSyncWidget", () => {
  it("renders one card per mapping, in status order, titled with its path", async () => {
    const drives = [
      entry("動画", "b", "gd:b", "idle"),
      entry("動画", "a", "gd:a", "idle"),
      entry("動画", "", "gd:root", "idle"),
    ];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    const order = remotes.map((r) => screen.getByText(r));
    for (let i = 1; i < order.length; i++) {
      expect(
        order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(cardOf("gd:b", remotes).textContent).toContain("動画 / b");
    expect(cardOf("gd:a", remotes).textContent).toContain("動画 / a");
    expect(cardOf("gd:root", remotes).textContent).not.toContain("動画 /");
  });

  it("marks only the named mapping as syncing when its progress arrives", async () => {
    const drives = [entry("動画", "", "gd:root", "idle"), entry("動画", "a", "gd:a", "idle")];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    act(() => push(progressEvent("動画", "a")));

    expect(cardOf("gd:a", remotes).textContent).toMatch(/Syncing/);
    expect(cardOf("gd:root", remotes).textContent).not.toMatch(/Syncing/);
  });

  it("applies a completion only to the mapping it names, within one drive", async () => {
    const drives = [entry("動画", "", "gd:root"), entry("動画", "a", "gd:a")];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    act(() =>
      push({
        event: "sync:complete",
        data: {
          drive: "動画",
          path: "a",
          transferred_files: 1,
          transferred_bytes: 1,
          errors: 0,
          elapsed_seconds: 1,
        },
      }),
    );

    expect(cardOf("gd:a", remotes).textContent).not.toMatch(/Syncing/);
    expect(cardOf("gd:root", remotes).textContent).toMatch(/Syncing/);
  });

  it.each(["sync:complete", "sync:error"])(
    "%s for one mapping leaves its sibling's progress in place",
    async (event) => {
      const drives = [entry("動画", "", "gd:root"), entry("動画", "a", "gd:a")];
      const remotes = drives.map((d) => d.remote);
      await renderWith(drives);

      act(() => push(progressEvent("動画", "")));
      act(() =>
        push({
          event,
          data: {
            drive: "動画",
            path: "a",
            message: "failed",
            kind: null,
            transferred_files: 0,
            transferred_bytes: 0,
            errors: 0,
            elapsed_seconds: 1,
          },
        } as WebSocketEvent),
      );

      expect(cardOf("gd:root", remotes).textContent).toMatch(/3\/7 files/);
    },
  );

  it("applies progress only to the mapping it names, within one drive", async () => {
    const drives = [entry("動画", "", "gd:root"), entry("動画", "a", "gd:a")];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    act(() => push(progressEvent("動画", "a")));

    expect(cardOf("gd:a", remotes).textContent).toMatch(/3\/7 files/);
    expect(cardOf("gd:root", remotes).textContent).not.toMatch(/3\/7 files/);
  });

  it("applies an error only to the mapping it names, within one drive", async () => {
    const drives = [entry("動画", "", "gd:root"), entry("動画", "a", "gd:a")];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    act(() =>
      push({
        event: "sync:error",
        data: { drive: "動画", path: "a", kind: "auth_expired", error: "token expired" },
      }),
    );

    expect(cardOf("gd:a", remotes).textContent).toMatch(/Retry/);
    expect(cardOf("gd:root", remotes).textContent).not.toMatch(/Retry/);
    expect(cardOf("gd:root", remotes).textContent).toMatch(/Syncing/);
  });

  it("does not apply an event that names a path no card has", async () => {
    const drives = [entry("動画", "", "gd:root"), entry("動画", "a", "gd:a")];
    const remotes = drives.map((d) => d.remote);
    await renderWith(drives);

    act(() => push(progressEvent("動画", "elsewhere")));

    for (const r of remotes) {
      expect(cardOf(r, remotes).textContent).not.toMatch(/3\/7 files/);
    }
  });

  // A key built by joining drive and path with a separator either can contain
  // confuses (a|b, c) with (a, b|c).
  it.each(["|", ":", "::", "-", "_", " ", ",", "#", "@", "+", "."])(
    "keeps two mappings apart when joining them with %j would not",
    async (sep) => {
      const errors: unknown[][] = [];
      vi.spyOn(console, "error").mockImplementation((...args) => {
        errors.push(args);
      });
      const drives = [
        entry(`x${sep}y`, "z", "gd:first"),
        entry("x", `y${sep}z`, "gd:second"),
        entry("x", "", "gd:whole"),
      ];
      const remotes = drives.map((d) => d.remote);
      await renderWith(drives);

      act(() => push(progressEvent("x", `y${sep}z`)));

      expect(cardOf("gd:second", remotes).textContent).toMatch(/3\/7 files/);
      expect(cardOf("gd:first", remotes).textContent).not.toMatch(/3\/7 files/);
      expect(cardOf("gd:whole", remotes).textContent).not.toMatch(/3\/7 files/);
      expect(errors.map((e) => String(e[0])).join("\n")).not.toMatch(/same key/);
    },
  );
});
