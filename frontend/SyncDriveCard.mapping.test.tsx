import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SyncDriveCard from "./SyncDriveCard";
import type { SyncDriveStatus } from "./api";
import en from "./messages/en.json";
import ja from "./messages/ja.json";

// SPEC-ADDON-003: a card stands for one (drive, path) mapping.

type Status = SyncDriveStatus & { path: string };

function mapping(overrides: Partial<Status> = {}): Status {
  return {
    drive: "動画",
    path: "仕事/録画",
    remote: "gdrive:litloft/録画",
    status: "idle",
    last_synced_at: null,
    last_result: null,
    progress: null,
    ...overrides,
  } as Status;
}

function renderCard(drive: Status) {
  return render(
    <SyncDriveCard drive={drive} progress={null} onSyncStarted={() => {}} />,
  );
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () =>
    new Response(JSON.stringify({ status: "started", drive: "動画", path: "", log: "" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function requested(): { method: string; pathname: string; query: URLSearchParams }[] {
  return fetchMock.mock.calls.map(([input, init]) => {
    const raw = typeof input === "string" ? input : (input as Request).url;
    const url = new URL(raw, "http://litloft.test");
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    return { method, pathname: decodeURIComponent(url.pathname), query: url.searchParams };
  });
}

function textIs(expected: string) {
  return (_: string, element: Element | null) => {
    if (!element) return false;
    const norm = (s: string | null) => (s ?? "").replace(/\s+/g, " ").trim();
    if (norm(element.textContent) !== expected) return false;
    return Array.from(element.children).every((c) => norm(c.textContent) !== expected);
  };
}

describe("SPEC-ADDON-003 card title", () => {
  it("adds a folder mapping's path after the drive name, and nothing for the whole drive", () => {
    const { unmount } = renderCard(mapping());
    expect(screen.getByText(textIs("動画 / 仕事/録画"))).toBeInTheDocument();
    unmount();

    renderCard(mapping({ path: "" }));
    expect(screen.getByText(textIs("動画"))).toBeInTheDocument();
    expect(screen.queryByText(/動画 \//)).toBeNull();
  });
});

describe("SPEC-ADDON-003 card controls act on that mapping only", () => {
  const awkward = "a&b#c+d?e %f=g/録画";

  it("Sync Now starts the card's mapping, with the path percent-encoded", async () => {
    renderCard(mapping({ path: awkward }));

    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [call] = requested().filter((r) => r.method === "POST");
    expect(call.pathname).toBe("/api/addons/cloud-sync/動画/start");
    expect(call.query.get("path")).toBe(awkward);
  });

  it("Sync Now on a whole-drive card sends no folder, and a folder card sends its own", async () => {
    const { unmount } = renderCard(mapping({ path: "" }));
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    unmount();

    renderCard(mapping({ path: "a" }));
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    const [whole, folder] = requested().filter((r) => r.method === "POST");
    expect(whole.pathname).toBe("/api/addons/cloud-sync/動画/start");
    expect(whole.query.get("path") ?? "").toBe("");
    expect(folder.query.get("path")).toBe("a");
  });

  it("Retry starts the card's mapping", async () => {
    renderCard(mapping({ status: "error", error_message: "rclone exited with code 1" }));

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [call] = requested().filter((r) => r.method === "POST");
    expect(call.pathname).toBe("/api/addons/cloud-sync/動画/start");
    expect(call.query.get("path")).toBe("仕事/録画");
  });

  it("Cancel cancels the card's mapping", async () => {
    renderCard(mapping({ status: "syncing" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [call] = requested().filter((r) => r.method === "POST");
    expect(call.pathname).toBe("/api/addons/cloud-sync/動画/cancel");
    expect(call.query.get("path")).toBe("仕事/録画");
  });

  it("Log reads the card's mapping's log", async () => {
    renderCard(mapping({ path: awkward }));

    fireEvent.click(screen.getByRole("button", { name: "Log" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [call] = requested();
    expect(call.pathname).toBe("/api/addons/cloud-sync/動画/log");
    expect(call.query.get("path")).toBe(awkward);
  });

});

describe("SPEC-ADDON-003 source_empty text", () => {
  const enTitle = "Folder is missing or empty";
  const enBody =
    "Nothing was synced. Check that the drive is mounted and that the folder exists, then try again.";
  const jaTitle = "フォルダがないか、空です";
  const jaBody =
    "何も同期していません。ドライブがマウントされていて、フォルダがあるか確認してから、もう一度試してください。";

  it.each([
    ["folder", "仕事/録画"],
    ["whole drive", ""],
  ])("a %s mapping shows the catalogue title and body, without the path", (_, path) => {
    renderCard(
      mapping({ path, status: "error", error_kind: "source_empty", error_message: "raw text" }),
    );

    expect(screen.getByText(enTitle)).toBeInTheDocument();
    expect(screen.getByText(enBody)).toBeInTheDocument();
    expect(screen.queryByText("raw text")).toBeNull();
  });

  it("the Japanese catalogue carries the same entries under the same keys", () => {
    const enTable = (en as { cloudSync: Record<string, string> }).cloudSync;
    const jaTable = (ja as { cloudSync: Record<string, string> }).cloudSync;
    const keyOf = (value: string) => Object.keys(enTable).find((k) => enTable[k] === value);

    const titleKey = keyOf(enTitle);
    const bodyKey = keyOf(enBody);

    expect(titleKey).toBeDefined();
    expect(bodyKey).toBeDefined();
    expect(jaTable[titleKey as string]).toBe(jaTitle);
    expect(jaTable[bodyKey as string]).toBe(jaBody);
  });
});
