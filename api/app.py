"""
Vercel serverless entry point for the full Brainee Trades API.

Vercel routes all /api/* requests here.
The FastAPI app defined in backend/main.py handles all routing internally.

Deploy notes:
  - On Vercel, /tmp is the only writable dir — data_loader uses it for cache.
  - WebSocket (/api/alerts/ws) is not supported on Vercel; frontend falls back
    to polling /api/alerts/poll automatically.
  - Backtest maxDuration is set to 60 s in vercel.json (Pro: 300 s).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ── make the repo root importable ────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Mark environment so data_loader picks /tmp as cache dir
os.environ.setdefault("VERCEL", "1")

# ── import the full FastAPI application ──────────────────────────────────────
from backend.main import app  # noqa: E402, F401

# Vercel's @vercel/python builder looks for a module-level variable named 'app'
# that implements the ASGI interface.  The import above is sufficient.
