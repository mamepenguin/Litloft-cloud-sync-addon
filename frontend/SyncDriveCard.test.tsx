import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import SyncDriveCard from "./SyncDriveCard";
import type { SyncDriveStatus } from "./api";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    startSync: vi.fn(),
    cancelSync: vi.fn(),
    fetchSyncLog: vi.fn(),
  };
});

function errored(overrides: Partial<SyncDriveStatus> = {}): SyncDriveStatus {
  return {
    drive: "photos",
    remote: "gdrive:photos",
    status: "error",
    last_synced_at: null,
    last_result: null,
    progress: null,
    ...overrides,
  };
}

function renderCard(drive: SyncDriveStatus) {
  return render(
    <SyncDriveCard drive={drive} progress={null} onSyncStarted={() => {}} />,
  );
}

beforeEach(() => vi.clearAllMocks());

// The card offers a recovery step for an expired credential and shows the
// backend's message for anything else. It used to tell the two apart by
// testing that message for the words "Authentication expired", which tied the
// panel to one English wording; it reads `error_kind` now.
describe("SyncDriveCard error panel", () => {
  it("offers the recovery step when the credential expired", () => {
    renderCard(
      errored({
        error_kind: "auth_expired",
        error_message: "Authentication expired. Run 'rclone config reconnect …'",
      }),
    );

    expect(screen.getByText("Re-authentication Required")).toBeInTheDocument();
    expect(screen.getByText(/rclone config reconnect/)).toBeInTheDocument();
  });

  it("shows the backend's own message for any other failure", () => {
    renderCard(
      errored({ error_message: "rclone exited with code 1" }),
    );

    expect(screen.getByText("rclone exited with code 1")).toBeInTheDocument();
    expect(screen.queryByText("Re-authentication Required")).toBeNull();
  });

  it("does not read the kind out of the message text", () => {
    // A failure whose message merely mentions the phrase is not the auth case.
    // Only the kind decides, which is why reading the message cannot.
    renderCard(
      errored({
        error_message: "sync aborted after Authentication expired earlier today",
      }),
    );

    expect(screen.queryByText("Re-authentication Required")).toBeNull();
  });

  it("falls back to the message when the kind is unknown to it", () => {
    renderCard(
      errored({ error_kind: "quota_exceeded", error_message: "quota exceeded" }),
    );

    expect(screen.getByText("quota exceeded")).toBeInTheDocument();
    expect(screen.queryByText("Re-authentication Required")).toBeNull();
  });
});

// The relative labels were translated but the absolute fallback past a week
// was not: it hand-built `${month}/${day}`, which reads month-first in en and
// day-first elsewhere with nothing to say which was meant (ADM-8 follow-up).
describe("SyncDriveCard absolute timestamps", () => {
  function syncedAt(iso: string): SyncDriveStatus {
    return {
      drive: "photos",
      remote: "gdrive:photos",
      status: "idle",
      last_synced_at: iso,
      last_result: null,
      progress: null,
    };
  }

  it("formats a date older than a week through the locale", () => {
    const old = new Date(Date.now() - 40 * 86400000);
    renderCard(syncedAt(old.toISOString()));

    const expected = new Intl.DateTimeFormat("en", {
      month: "numeric",
      day: "numeric",
    }).format(old);
    expect(screen.getByText(new RegExp(expected.replace("/", "\\/")))).toBeInTheDocument();
  });

  it("includes the year only when it is not the current one", () => {
    const longAgo = new Date("2019-03-04T10:00:00Z");
    renderCard(syncedAt(longAgo.toISOString()));

    const expected = new Intl.DateTimeFormat("en", {
      year: "numeric",
      month: "numeric",
      day: "numeric",
    }).format(longAgo);
    expect(screen.getByText(new RegExp(expected.replace(/\//g, "\\/")))).toBeInTheDocument();
  });

  it("still uses the relative wording inside the week", () => {
    renderCard(syncedAt(new Date(Date.now() - 2 * 86400000).toISOString()));

    expect(screen.getByText(/2d ago/)).toBeInTheDocument();
  });
});
