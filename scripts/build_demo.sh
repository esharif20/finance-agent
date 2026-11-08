#!/bin/sh
# Build the public demo locally (ADR 0016): the web build in web/dist-demo and
# the Persona ledgers in FA_DEMO_DIR, which then serves both:
#
#   FA_DEMO=1 FA_DEMO_DIR=/abs/dir FA_DEMO_SECRET=<32+ chars> scripts/build_demo.sh
#   FA_DEMO=1 FA_DEMO_DIR=... FA_DEMO_SECRET=... uv run uvicorn finance_agent.demo.app:app
#
# Vercel runs the same two steps (vercel.json); there the static files are served
# from web/dist-demo and only /api/* reaches the Python Function. The Personas
# go to demo-data/personas, which the Function bundles read-only and copies to
# /tmp on a cold start (api/index.py, docs/DEPLOY.md).
set -eu
cd "$(dirname "$0")/.."
if [ -n "${VERCEL:-}" ]; then
  FA_DEMO_DIR="$PWD/demo-data"
  export FA_DEMO_DIR
  if ! command -v uv >/dev/null; then
    python3 -m pip install -q --user uv
    PATH="$(python3 -m site --user-base)/bin:$PATH"
  fi
fi
: "${FA_DEMO_DIR:?set FA_DEMO_DIR to an absolute directory for the demo}"

pnpm --dir web run build:demo
# /app is the app's own page: uvicorn's static mount finds it as app/index.html
mkdir -p web/dist-demo/app
cp web/dist-demo/index.html web/dist-demo/app/index.html

uv run python scripts/build_demo_db.py
rm -rf "$FA_DEMO_DIR/web"
cp -R web/dist-demo "$FA_DEMO_DIR/web"
