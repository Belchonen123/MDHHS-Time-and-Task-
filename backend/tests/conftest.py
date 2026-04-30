"""Shared test fixtures — most importantly, a one-time DB migration run so
tests that seed directly via SQLAlchemy (rather than going through the
FastAPI lifespan) don't bomb out on missing columns added by recent
migrations.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db


def pytest_configure(config):  # noqa: ANN001, D401, ARG001 — pytest hook signature
    init_db()
