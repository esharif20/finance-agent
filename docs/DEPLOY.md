# Deploying the public demo to Vercel

The demo (ADR 0016) is one Vercel project on `*.vercel.app`: the static web build
from `web/dist-demo` plus one Python Function, `api/index.py`, which serves
`/api/*` from `finance_agent.demo.app`. It uses synthetic Personas only. Vercel
holds **no** cloud model key and no real data.

## What `vercel.json` does

- `buildCommand` runs `scripts/build_demo.sh`. It builds the web demo, then
  builds the Persona ledgers into `demo-data/personas/` (it sets
  `FA_DEMO_DIR=$PWD/demo-data` itself when `VERCEL` is set).
- The Function bundles `src/finance_agent/**` and `demo-data/personas/**`,
  and leaves out tests, evals, web, docs, `.venv` and `node_modules`. The
  Python dependencies come from `pyproject.toml`. scipy, pandas, statsmodels
  and numpy make up most of it, about 300 MB uncompressed, under Vercel's
  500 MB Python limit. Set `VERCEL_ANALYZE_BUILD_OUTPUT=1` to see the size in
  the build log.
- At runtime the bundle is read-only. `FA_DEMO_DIR` defaults to
  `/tmp/fa-demo` when `VERCEL` is set, and on a cold start the bundled Personas
  are copied there (`guard.seed`, from `FA_DEMO_SEED`, which `api/index.py`
  points at the bundle). Visitor ledgers live in that instance's `/tmp`, so a
  recycled instance starts its visitors fresh. That's acceptable for a demo.

## Environment variables

| Name | Value | Notes |
|---|---|---|
| `FA_DEMO` | `1` | Without it the demo refuses to start (deny by default). |
| `FA_DEMO_SECRET` | 64 random hex characters | Signs the session cookie. If it's missing or under 32 characters, the build and the Function refuse to start. Generate it per deployment, never commit it. |

Don't set `FA_DEMO_DIR` or `FA_DEMO_SEED` on Vercel; the defaults above are the
safe ones. Both variables are needed at build time too, because the build runs
the same start-up guard.

## Commands

Run these from the repo root, logged in with `vercel login` and on the right
team (`vercel whoami`).

```sh
# 1. Link the repo to a project (creates .vercel/project.json, git-ignored)
vercel link --yes --project finance-agent-demo

# 2. Set the environment, for production and preview. The secret goes straight
#    from openssl into Vercel: it is never printed, saved or committed.
for env in production preview; do
  printf 1 | vercel env add FA_DEMO "$env"
  openssl rand -hex 32 | vercel env add FA_DEMO_SECRET "$env" --sensitive
done
vercel env ls

# 3. Deploy: a preview first, then production
vercel deploy
vercel deploy --prod
```

To rotate the secret, which signs every visitor out:

```sh
vercel env rm FA_DEMO_SECRET production --yes
openssl rand -hex 32 | vercel env add FA_DEMO_SECRET production --sensitive
vercel deploy --prod
```

## After deploying

Use `vercel curl` against a preview (deployment protection stays on) or open
the production URL. Check the landing page, the `/app` banner, Reset and
Shuffle, figures matching `/api/summary`, and that no request goes to
`/api/card-art`. Then point the #299 smoke test at the production URL; it's
read-only. `vercel logs <url>` shows the Function's logs. Run
`gitleaks detect` before any push.
