import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NavTabs } from "./NavTabs";

describe("NavTabs badge", () => {
  it("shows a count and says what it counts", () => {
    render(
      <NavTabs
        tabs={[
          { id: "review", label: "Review", badge: 3 },
          { id: "overview", label: "Overview", badge: 0 },
        ]}
        tab="overview"
        className=""
        item=""
      />,
    );
    expect(screen.getByRole("link", { name: /Review.*3 to review/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
  });
});
