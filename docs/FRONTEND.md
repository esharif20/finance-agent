# Frontend

Status: draft for review. Design direction: `docs/design/DIRECTIONS.md`.

## Stack

Vite, React, TypeScript, Tailwind, shadcn/ui (charts on Recharts), TanStack Query, Motion (`motion/react`). FastAPI serves the built app; no separate frontend server outside development.

## Contract with the backend

TypeScript types are generated from FastAPI's OpenAPI schema with `openapi-typescript`. CI fails if the generated types are out of date. All money arrives as minor units plus currency and is formatted in one place.

## Screens

| Milestone | Screen |
| --- | --- |
| M2 | Overview: month in/out/net with the pound strip, spending donut (top 5 coloured, rest grey), Account balances with "Matches ledger / Find it" |
| M2 | Category drill-down: top Merchants, monthly trend, Transactions |
| M2 | Transactions: filter, search, inline Category change via chips (saved as a Correction) |
| M3 | Review Queue, Transfers |
| M4 | Assistant panel with confirm cards and "answered by" indicator |
| M5 | Insights, weekly briefing, regret review, "How well I know you" |
| M7 | Public landing page at `/`, demo app at `/app` |

## Design rules

- Pocket base with the pound strip and "Matches ledger" language; Manrope with tabular figures; light and true-black dark modes.
- Strong Revolut feel, own brand: no Revolut name, logo, `#494fdf` or Aeonik Pro.
- Privacy mode: `H` blurs every amount (and hides it from screen readers); on by default in the demo.
- Motion: subtle in the app (count-up, chart draw-in, drill-down transitions); scroll storytelling only on the landing page; all motion respects `prefers-reduced-motion`.
- Responsive web only; no native app.

## Tests

Vitest and Testing Library for components. One Playwright smoke test: run `fa`, open the Overview, assert the totals match the API. Visual checks with the Playwright CLI (WebKit) in light and dark after UI changes.
