"""Vercel's Python Function for the public demo (vercel.json): every /api/*
request reaches `finance_agent.demo.app:app`. FA_DEMO, FA_DEMO_DIR and
FA_DEMO_SECRET are set in the Vercel project, never in the repo."""

from finance_agent.demo.app import app

__all__ = ["app"]
