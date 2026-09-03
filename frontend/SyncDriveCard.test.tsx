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
    // A failure whose message merely mentions the phrase is not the auth case;
    // only the kind decides. This is what the substring test got wrong.
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
