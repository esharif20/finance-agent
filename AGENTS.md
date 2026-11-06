# AGENTS.md

Engineering rules for this project are still being designed (see `/grill-with-docs` output). Until they land: no real financial data in commits, fixtures, issues or logs.

## Frontend work: required skills

Any task that touches the frontend (components, screens, styling, charts, motion, the demo landing page) must load these skills first:

- `frontend-design:frontend-design`: aesthetic direction, avoiding templated defaults.
- `ui-ux-pro-max`: UX guidelines, accessibility, chart and colour choices.
- `vercel-plugin:shadcn` (or `shadcn`): adding and composing shadcn/ui components.
- `design-taste-frontend`: anti-slop house style; also its gallery research when choosing a direction.
- `nateherk-design:scroll-craft`: scroll-driven motion. Use it for the public demo landing page; app screens keep motion restrained.

Motion: use `motion/react` (Motion, formerly Framer Motion), following `design-taste-frontend`'s `references/motion.md`. Every animation respects `prefers-reduced-motion`.

Browser verification: after any UI change, check it in a real browser with the Playwright CLI (installed globally; the WebKit browser is downloaded, Chromium is not). Screenshot light and dark, and confirm figures on screen match the API.

Visual reference: `~/.claude/skill-library/awesome-design-md/design-md/revolut/DESIGN.md`. The goal is a strong Revolut feel with the project's own twist. Follow its structure (type scale, spacing, radii, pill buttons, dark/light surfaces), not its brand: no Revolut name, logo, exact primary colour or the commercial Aeonik Pro font. Revolut is one of the user's banks, and a look-alike would read as impersonation in a public demo.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `esharif20/finance-agent` (public), via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
