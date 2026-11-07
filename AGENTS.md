# AGENTS.md

Read `GLOSSARY.md` and the relevant `docs/adr/` before working. Design docs: `docs/ARCHITECTURE.md`, `docs/AI_DESIGN.md`, `docs/THREAT_MODEL.md`, `docs/DATA_POLICY.md`, `docs/ML_EVALUATIONS.md`, `docs/FRONTEND.md`, `docs/ROADMAP.md`.

## Hard rules

- **No real financial data** in commits, fixtures, issues, PRs, logs or traces. Fixtures live only in `tests/fixtures/synthetic/` and start with `# synthetic`.
- **Money is `Decimal` or integer minor units.** Never `float` in `ledger`, `engine` or `api`.
- **The LLM never computes a figure.** Every figure in an Assistant reply comes from a tool result (ADR 0007).
- **No write paths for the Assistant** except Proposals that the user confirms.
- **No network egress** except `localhost` Ollama, and the privacy gateway when the user opts in. No LangSmith or third-party tracing.
- **Logs never contain** amounts, descriptions, account fields or Memory text.
- **No payments or transfers**, ever.
- Respect module boundaries in `docs/ARCHITECTURE.md`; the import-linter contract enforces them.

## How to build

- Python via `uv` only (`uv run`, `uv add`). Lint `ruff`, types `pyright`, tests `pytest` + Hypothesis.
- Test-first (`/tdd`). Financial invariants get property tests.
- Intelligence work writes its evaluation first: dataset, metric, baseline (`docs/ML_EVALUATIONS.md`).
- Small vertical slices; one ticket at a time unless running `/implement-spec`.
- No new infrastructure, service or dependency without a reason in the ticket or an ADR.
- Every PR must pass CI (ruff, pyright, pytest, gitleaks, fixture guard, OpenAPI type sync) before merging; `main` is protected.

## Model per ticket and verification

`/to-tickets` labels each ticket with one model:

- `model:opus`: money calculations, Ledger invariants, security, privacy gateway, ML evaluation design, anything that changes an ADR, core logic.
- `model:sonnet`: well-specified endpoints, importers following an existing pattern, UI components, tests.
- `model:haiku`: only tiny tasks such as docs, config and renames.

When running `/implement-spec`, start each implementer subagent with the model on its ticket's label, in its own worktree. For `model:opus` tickets only, a fresh Opus verifier subagent (no access to the implementer's reasoning) runs `/code-review` and adds at least one adversarial test (a property test or a malicious input); the ticket merges only when the verifier approves and CI passes. `model:sonnet` and `model:haiku` tickets merge on CI. A ticket that fails review twice moves up one model.

## Frontend work: required skills

Any task that touches the frontend (components, screens, styling, charts, motion, the demo landing page) must load these skills first:

- `frontend-design:frontend-design`: aesthetic direction, avoiding templated defaults.
- `ui-ux-pro-max`: UX guidelines, accessibility, chart and colour choices.
- `vercel-plugin:shadcn` (or `shadcn`): adding and composing shadcn/ui components.
- `design-taste-frontend`: anti-slop house style; also its gallery research when choosing a direction.
- `nateherk-design:scroll-craft`: scroll-driven motion. Use it for the public demo landing page; app screens keep motion restrained.
- `impeccable`: design audit, critique and polish passes (`/impeccable audit`, `/impeccable polish`) before calling a screen done.
- `emil-design-eng`: interaction and animation polish (easing, durations, springs, the small details). Its rules sit under the reduced-motion rule below.

Motion: use `motion/react` (Motion, formerly Framer Motion), following `design-taste-frontend`'s `references/motion.md`. Every animation respects `prefers-reduced-motion`.

Browser verification: after any UI change, check it in a real browser with the Playwright CLI (installed globally; the WebKit browser is downloaded, Chromium is not). Screenshot light and dark, and confirm figures on screen match the API.

Visual reference: `~/.claude/skill-library/awesome-design-md/design-md/revolut/DESIGN.md`. The goal is a strong Revolut feel with the project's own twist. Follow its structure (type scale, spacing, radii, pill buttons, dark/light surfaces), not its brand: no Revolut name, logo, exact primary colour or the commercial Aeonik Pro font. Revolut is one of the user's banks, and a look-alike would read as impersonation in a public demo.

Real bank names and logos appear only in the local app, from the user's own images in `card-art/` under the data directory, served by `/api/card-art/{key}` (#58). They never go in the repo, fixtures, screenshots in the repo or the public demo; the code draws only a generic card and the art arrives as a runtime texture.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `esharif20/finance-agent` (public), via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
