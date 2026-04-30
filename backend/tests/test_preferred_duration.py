"""Soft preferred_duration_min targets and weekly authorization preflight."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calculate import (  # noqa: E402
    authorization_exceeds_weekly_worker_capacity,
    default_worker_availability,
    generate_schedule,
)


def test_authorization_within_capacity_returns_none() -> None:
    avail = default_worker_availability()
    assert authorization_exceeds_weekly_worker_capacity(avail, None) is None


def test_authorization_exceeds_weekly_capacity_shape() -> None:
    avail = default_worker_availability()
    # Narrow every day to 60 minutes so weekly sum is far below typical auth.
    for d in avail:
        avail[d]["earliest"] = "7:00 AM"
        avail[d]["latest"] = "8:00 AM"
        avail[d]["visit_day_longer"] = False
    tasks = [
        {"task_name": "Meal Prep", "min_per_day": 120, "days_per_week": 7},
    ]
    err = authorization_exceeds_weekly_worker_capacity(avail, tasks)
    assert err is not None
    assert err["code"] == "AUTHORIZATION_EXCEEDS_WEEKLY_CAPACITY"


def test_preferred_duration_above_capacity_does_not_422_in_schedule() -> None:
    """Excess preferred_duration_min is clamped for balancing; schedule still builds."""
    avail = default_worker_availability()
    avail["Monday"]["preferred_duration_min"] = 99999
    tasks = [
        {"task_name": "A", "min_per_day": 30, "days_per_week": 3},
    ]
    assert authorization_exceeds_weekly_worker_capacity(avail, tasks) is None
    cs = generate_schedule(
        tasks,
        20.0,
        2026,
        4,
        {"weekday_start": "7:00 AM", "weekend_start": "12:00 PM"},
        config=None,
        worker_availability=avail,
    )
    assert cs.config is not None
    logs = cs.config.weekday_override_log
    assert isinstance(logs, list)
    mon = next((x for x in logs if x.get("weekday") == "Monday"), None)
    assert mon is not None
    assert mon.get("preferred_duration") == 99999
    assert "exceeds" in str(mon.get("reason") or "").lower()
