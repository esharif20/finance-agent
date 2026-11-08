# AGENTS.md

Read `GLOSSARY.md` and the relevant `docs/adr/` before working. Design docs: `docs/ARCHITECTURE.md`, `docs/AI_DESIGN.md`, `docs/THREAT_MODEL.md`, `docs/DATA_POLICY.md`, `docs/ML_EVALUATIONS.md`, `docs/FRONTEND.md`, `docs/ROADMAP.md`.

## Hard rules

- **No real financial data** in commits, fixtures, issues, PRs, logs or traces. Fixtures live only in `tests/fixtures/synthetic/` and start with `# synthetic`.
- **Money is `Decimal` or integer minor units.** Never `float` in `ledger`, `engine` or `api`.
- **The LLM never computes a figure.** Every figure in an Assistant reply comes from a tool result (ADR 0007).
- **No write paths for the Assistant** except Proposals that the user confirms.
- **No network egress** except `localhost` Ollama, the privacy gateway when the user opts in (Gemini at `generativelanguage.googleapis.com` only, paid-tier key only, ADR 0016), and one pinned, read-only SimpleFIN aggregator host reached only by the `ingest` SimpleFIN client (ADR 0013). No LangSmith or third-party tracing.
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

### Speed rules

- CI is split into parallel jobs (`lint`, `python-tests`, `evals`, `web`, `smoke (webkit)`). The `checks` job aggregates them and is what branch protection requires.
- Agents don't run the full suite locally. They run targeted tests, `ruff check .` and pyright; CI is the gate.
- Agents wait for CI in the foreground. If the harness backgrounds a long watch, keep polling with short `gh pr checks <n>` calls, and never end the turn on a background job.
- A PR that is only behind main, with no conflicts: `gh pr update-branch <n>` then `gh pr merge <n> --auto --merge`, instead of merging main in by hand. GitHub's merge queue isn't available for user-owned repos.
- Tickets that need a migration in the same feature area can be bundled into one implementer and one verifier, with sequential migration numbers. Only one migration agent runs at a time.

## Model per ticket and verification

`/to-tickets` labels each ticket with one model:

- `model:opus`: money calculations, Ledger invariants, security, privacy gateway, ML evaluation design, anything that changes an ADR, core logic.
- `model:sonnet`: well-specified endpoints, importers following an existing pattern, UI components, tests.
- `model:haiku`: only tiny tasks such as docs, config and renames.

When running `/implement-spec`, start each implementer subagent with the model on its ticket's label, in its own worktree. Most tickets, `model:opus` included, merge on green CI after the orchestrator has read the diff.

A separate verifier is the exception, not the rule. A fresh Opus verifier subagent (no access to the implementer's reasoning) runs `/code-review` and adds at least one adversarial test (a property test or a malicious input) only for:

- security and privacy: egress, the privacy gateway, prompt injection, secrets, data exposure;
- data loss: deleting or rewriting user data, and migrations that change existing rows;
- money invariants: anything that could change a Ledger total.

Those tickets merge only when the verifier approves and CI passes. During milestone work on an integration branch, one verifier reviews the whole wave's combined diff instead of one per ticket. A ticket that fails review twice moves up one model.

## Parallel work

Tickets that don't depend on each other run at the same time, each in its own worktree, and every one still passes the same quality gates.

- **Pick parallel tickets that don't overlap.** Two tickets run together only if they have no blocking link and their expected files don't overlap. In particular, at most one of them may touch each of these:
  - a new Alembic migration;
  - `ledger/__init__.py`;
  - the same screen component.

  Generated files (`web/openapi.json`, `web/src/api/schema.d.ts`) are never hand-merged; regenerate them with `pnpm --dir web run gen`.
- **At most 3 implementers or verifiers at once.** This keeps the 16 GB Mac responsive.
- **During development, run only the tests your change touches.** Run the full CI-equivalent exactly once, before opening the PR, with `scripts/check-all.sh`. It holds a lock shared by every worktree, so only one full suite runs at a time.
- **Merges are serial.** One merger at a time merges into the integration branch. Each branch first merges the current tip, and its PR merges only on green CI against that tip. A `model:opus` ticket still needs its verifier's approval before it merges.

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
