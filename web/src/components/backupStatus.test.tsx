import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/api/schema";
import { BackupStatus } from "./BackupStatus";

type Status = components["schemas"]["SyncStatusOut"];

const base: Status = {
  last_sync_at: null,
  last_failure_at: null,
  failure_class: null,
  failures_in_a_row: 0,
  consent_expires_on: null,
  consent_days_left: null,
  consent_warning: false,
  last_backup_at: null,
  backup_warning: false,
};
const NOW = new Date("2026-09-30T12:00:00Z");

function show(status: Status) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(status), { status: 200 })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BackupStatus now={NOW} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("BackupStatus", () => {
  it("says when the last backup ran", async () => {
    show({ ...base, last_backup_at: "2026-09-30T09:00:00+00:00" });
    expect(await screen.findByText(/Backed up 3 hours ago/)).toBeInTheDocument();
  });

  it("warns, in words, when the last copy is over 7 days old", async () => {
    show({ ...base, last_backup_at: "2026-09-21T12:00:00+00:00", backup_warning: true });
    const warning = await screen.findByRole("status");
    expect(warning).toHaveTextContent("Last backup 9 days ago. Connect your backup drive.");
  });

  it("warns when a drive is registered but nothing was ever backed up", async () => {
    show({ ...base, backup_warning: true });
    expect(await screen.findByRole("status")).toHaveTextContent("No backup yet.");
  });

  it("shows nothing when backups are not set up", async () => {
    const { container } = show(base);
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
  });
});
