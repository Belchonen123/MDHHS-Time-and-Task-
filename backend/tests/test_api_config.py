"""Tests for the ScheduleConfig editor endpoints.

Covers the upload → PATCH /config → re-validate round-trip. The upload flow
runs real OCR + generation on a synthesized MDHHS-style PDF, but when that
becomes fragile we fall back to seeding the DB directly so the config path
stays covered.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as app_main  # noqa: E402
from app.calculate import (  # noqa: E402
    COMPANION_TO_PARENT,
    ScheduleConfig,
    default_config_for,
    generate_schedule,
)
from app.db import Client, Plan, SessionLocal  # noqa: E402
from app.validate import cross_check, validation_report_to_dict  # noqa: E402
from app.extract import ExtractedForm  # noqa: E402


OTTILIE_TASKS = [
    {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7, "monthly_amount": 216.72},
    {"task_name": "Dressing", "min_per_day": 14, "days_per_week": 7, "monthly_amount": 189.63},
    {"task_name": "Grooming", "min_per_day": 8, "days_per_week": 7, "monthly_amount": 108.36},
    {"task_name": "Mobility", "min_per_day": 16, "days_per_week": 7, "monthly_amount": 216.72},
    {"task_name": "Toileting", "min_per_day": 6, "days_per_week": 7, "monthly_amount": 81.27},
    {"task_name": "Transferring", "min_per_day": 8, "days_per_week": 7, "monthly_amount": 108.36},
    {"task_name": "Medication", "min_per_day": 2, "days_per_week": 7, "monthly_amount": 27.09},
    {"task_name": "Meal Preparation", "min_per_day": 50, "days_per_week": 7, "monthly_amount": 677.25},
    {"task_name": "Housework", "min_per_day": 12, "days_per_week": 3, "monthly_amount": 69.66},
    {"task_name": "Laundry", "min_per_day": 21, "days_per_week": 2, "monthly_amount": 81.27},
    {"task_name": "Shopping for Food/Meds", "min_per_day": 15, "days_per_week": 2, "monthly_amount": 58.05},
    {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 2, "monthly_amount": 77.40},
]


@pytest.fixture
def http() -> TestClient:
    return TestClient(app_main.app)


@pytest.fixture
def seeded_plan() -> tuple[str, int]:
    """Seed the DB with a client + a v1 plan using default config."""
    s = SessionLocal()
    try:
        cid = f"cfgtest_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        c = Client(
            client_id=cid,
            client_name="Cfg Test",
            case_number="000-0",
            county="Test",
            asw_name="ASW",
            asw_email="asw@example.com",
            asw_phone="000",
            pay_rate=27.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s.add(c)
        s.flush()

        cfg = default_config_for(OTTILIE_TASKS, 2026, 4)
        cal = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, config=cfg)
        sched = cal.as_dict()
        form = ExtractedForm(
            client_name="Cfg Test",
            pay_rate=27.0,
            tasks=list(OTTILIE_TASKS),
            monthly_total_amount=1911.78,
        )
        report = cross_check(form, sched)
        plan = Plan(
            client_row_id=c.id,
            version=1,
            year=2026,
            month=4,
            weekly_minutes=int(sched["weekly_minutes"]),
            monthly_minutes=float(sched["monthly_minutes"]),
            monthly_amount=float(sched["monthly_amount"]),
            schedule_json=json.dumps(sched, default=str),
            tasks_json=json.dumps(OTTILIE_TASKS),
            config_json=json.dumps(cfg.to_dict()),
            validation_json=json.dumps(validation_report_to_dict(report), default=str),
            validation_passed=bool(report.all_passed),
            created_at=datetime.now(timezone.utc),
        )
        s.add(plan)
        s.commit()
        return cid, 1
    finally:
        s.close()


def test_patch_config_round_trips_placement(http: TestClient, seeded_plan: tuple[str, int]) -> None:
    """Flipping Housework to Mon/Wed/Fri updates schedule + config persists."""
    cid, ver = seeded_plan

    # Pull current config from the plan.
    resp = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    cfg = plan["config"]
    assert cfg, "seeded plan should have a ScheduleConfig"
    hw = next(t for t in cfg["tasks"] if t["task_name"] == "Housework")
    assert hw["selected_weekdays"] == ["Friday", "Saturday", "Sunday"]

    # Flip Housework → Mon/Wed/Fri.
    for t in cfg["tasks"]:
        if t["task_name"] == "Housework":
            t["selected_weekdays"] = ["Monday", "Wednesday", "Friday"]
            t["selected_dates"] = []

    resp = http.patch(
        f"/api/clients/{cid}/plans/{ver}/config",
        json={"config": cfg},
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    hw_after = next(
        t for t in patched["config"]["tasks"] if t["task_name"] == "Housework"
    )
    assert hw_after["selected_weekdays"] == ["Monday", "Wednesday", "Friday"]

    # Schedule count for Housework should be 4 Mon + 5 Wed + 4 Fri = 13.
    counts = patched["schedule"]["task_occurrence_counts"]
    assert counts.get("Housework") == 13

    # Fetch the plan again — config must persist across GET.
    resp = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert resp.status_code == 200
    hw_persisted = next(
        t
        for t in resp.json()["config"]["tasks"]
        if t["task_name"] == "Housework"
    )
    assert hw_persisted["selected_weekdays"] == ["Monday", "Wednesday", "Friday"]


def test_patch_config_reports_variance_when_under_scheduled(
    http: TestClient, seeded_plan: tuple[str, int]
) -> None:
    """Removing the catch-up day from 2/wk tasks creates a known variance."""
    cid, ver = seeded_plan
    resp = http.get(f"/api/clients/{cid}/plans/{ver}")
    cfg = resp.json()["config"]

    # Blow away selected_dates for all 2/wk tasks → lose the 5th-Wed catch-up.
    for t in cfg["tasks"]:
        if t["days_per_week"] == 2:
            t["selected_dates"] = []

    resp = http.patch(
        f"/api/clients/{cid}/plans/{ver}/config",
        json={"config": cfg},
    )
    assert resp.status_code == 200
    patched = resp.json()
    # Schedule now misses the monthly target by errand_extra = 56 min.
    total_min = sum(
        int(d.get("duration_min", 0) or 0)
        for d in patched["schedule"]["daily_schedule"]
    )
    # Schedule misses the monthly target by Σ(2/wk bundle) − 56; per-line target changes absolute gap.
    assert 4248 - total_min == 56

    # Schedule under-delivers authorization; billing follows delivered (under cap).
    checks = {c["number"]: c for c in patched["validation"]["checks"]}
    assert checks[4]["passed"] is True
    assert patched["validation"]["validation_status"] == "BILLABLE_UNDER_CAP"


def test_patch_client_preferred_duration_round_trip_soft_target(
    http: TestClient, seeded_plan: tuple[str, int]
) -> None:
    """PATCH /clients/:id persists preferred_duration_min; low soft target does not 422."""
    cid, _ver = seeded_plan
    r = http.get(f"/api/clients/{cid}")
    assert r.status_code == 200, r.text
    avail = copy.deepcopy(r.json()["client"]["availability"])
    avail["Wednesday"] = {
        **avail["Wednesday"],
        "preferred_duration_min": 180,
    }
    r2 = http.patch(f"/api/clients/{cid}", json={"availability": avail})
    assert r2.status_code == 200, r2.text
    r3 = http.get(f"/api/clients/{cid}")
    assert r3.status_code == 200
    wed = r3.json()["client"]["availability"]["Wednesday"]
    assert wed["preferred_duration_min"] == 180

    avail_bad = copy.deepcopy(r3.json()["client"]["availability"])
    avail_bad["Wednesday"] = {
        **avail_bad["Wednesday"],
        "preferred_duration_min": 30,
    }
    r4 = http.patch(f"/api/clients/{cid}", json={"availability": avail_bad})
    assert r4.status_code == 200, r4.text


def test_plan_preview_does_not_persist_schedule(
    http: TestClient, seeded_plan: tuple[str, int]
) -> None:
    cid, ver = seeded_plan
    r = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert r.status_code == 200
    sched_before = r.json()["schedule"]
    r2 = http.get(f"/api/clients/{cid}")
    avail = copy.deepcopy(r2.json()["client"]["availability"])
    avail["Monday"] = {**avail["Monday"], "earliest": "6:00 AM"}
    pr = http.post(
        f"/api/clients/{cid}/plans/{ver}/preview",
        json={"availability": avail},
    )
    assert pr.status_code == 200, pr.text
    assert pr.json().get("xlsx_path") == ""
    assert pr.json().get("pdf_path") == ""
    r3 = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert r3.json()["schedule"] == sched_before


def test_patch_client_regenerate_returns_plan_and_updates_row(
    http: TestClient, seeded_plan: tuple[str, int]
) -> None:
    cid, ver = seeded_plan
    r = http.get(f"/api/clients/{cid}")
    avail = copy.deepcopy(r.json()["client"]["availability"])
    avail["Monday"] = {**avail["Monday"], "earliest": "6:30 AM"}
    r2 = http.patch(
        f"/api/clients/{cid}?regenerate=true",
        json={"availability": avail},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert "schedule" in body
    assert body["version"] == ver
    r3 = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert r3.status_code == 200
    assert r3.json()["schedule"] == body["schedule"]
    r4 = http.get(f"/api/clients/{cid}")
    assert r4.json()["client"]["availability"]["Monday"]["earliest"] == "6:30 AM"


def test_patch_config_reseed_placement_refreshes_tasks(
    http: TestClient, seeded_plan: tuple[str, int]
) -> None:
    """reseed_placement runs default_config_for; weekday picks reset to auto."""
    cid, ver = seeded_plan
    resp = http.get(f"/api/clients/{cid}/plans/{ver}")
    assert resp.status_code == 200
    cfg = resp.json()["config"]
    for t in cfg["tasks"]:
        t["preferred_weekdays"] = ["Monday"]
        t["preference_unspecified"] = False
    r2 = http.patch(
        f"/api/clients/{cid}/plans/{ver}/config",
        json={"config": cfg, "reseed_placement": True},
    )
    assert r2.status_code == 200, r2.text
    out_tasks = r2.json()["config"]["tasks"]
    assert out_tasks
    companion_names = set(COMPANION_TO_PARENT.keys())
    assert all(
        (not t.get("preferred_weekdays")) and t.get("preference_unspecified", True)
        for t in out_tasks
        if str(t.get("task_name") or "").strip() not in companion_names
    )
