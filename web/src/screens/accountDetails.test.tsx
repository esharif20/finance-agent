import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "@/api/schema";
import { AccountDetails } from "./AccountView";

type Account = components["schemas"]["AccountOut"];
const hsbc: Account = {
  id: 3,
  bank: "HSBC",
  masked_number: "HSBC",
  name: "All HSBC accounts (the export has no account number)",
  kind: "current",
  currency: "GBP",
  bank_balance: null,
  ledger_balance: null,
  gaps: [],
  art: null,
  last_import: null,
  source: "csv",
};

describe("AccountDetails", () => {
  it("says the bank gives no balance instead of showing a figure or a match", () => {
    render(<AccountDetails account={hsbc} />);
    expect(screen.getByRole("group", { name: "Bank balance" })).toHaveTextContent(
      "Balance not provided by this bank",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent("Matches ledger");
    expect(document.body).not.toHaveTextContent("£");
  });
});
