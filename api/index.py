"""Vercel's Python Function for the public demo (vercel.json): every /api/*
request reaches `finance_agent.demo.app:app`. FA_DEMO and FA_DEMO_SECRET are
set in the Vercel project, never in the repo (docs/DEPLOY.md). The bundle is
read-only, so the Personas built into demo-data/personas are copied on a cold
start into FA_DEMO_DIR, which defaults to /tmp on Vercel."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # the bundled source, installed or not
os.environ.setdefault("FA_DEMO_SEED", str(ROOT / "demo-data" / "personas"))

from finance_agent.demo.app import app  # noqa: E402

__all__ = ["app"]
