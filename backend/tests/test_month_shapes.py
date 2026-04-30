"""Month-shape regression tests for generate_schedule + reconciliation.

Covers three calendar shapes — 28-, 30-, and 31-day months — with
mixed task frequencies (7/wk, 3/wk, 2/wk) and *explicit* catch-up
dates (``selected_dates``) carrying the calibration gap. The point of
these tests is to pin the three invariants that must hold for every
config-driven schedule:

1. **Row invariant.** On every day in ``daily_schedule``,
   ``duration_min == Σ tasks.values()``. This is a structural
   property of ``generate_schedule`` — no hidden minutes, no phantom
   durations.

2. **Occurrence invariant.** For every task, the number of days it
   appears in ``daily_schedule`` equals the number of month dates
   whose weekday is in ``selected_weekdays`` *or* whose ISO date is in
   ``selected_dates`` — i.e. occurrence counts come straight from
   config, not frequency heuristics.

3. **Grand-total variance = 0.** ``Σ duration_min`` over the month
   equals :func:`compute_mdhhs_form_minutes` (Σ unrounded per-line
   ``mpd × dpw × 4.3``, rounded once), not ``Σ`` of per-line-rounded
   minutes and not ``round_half_up(weekly_budget × 4.3)``.
   This is the headline reconciliation guarantee (validate.py Check 5
   sub-row "TOTAL"), re-asserted here at the schedule layer so a regression
   shows up at the closest possible blast radius.

To make (3) clean across arbitrary month shapes we choose tasks whose
per-line monthly total is an exact integer (avoiding pathological .5 lines
where possible) and hand-pick ``selected_dates`` per month shape to close
the calibration gap. The 31-day case also *drops* a default weekday
to avoid overshooting — demonstrating that selected_weekdays is a
user decision, not a dpw-driven template.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.calculate import (
    CalibratedSchedule,
    ScheduleConfig,
    TaskPlacement,
    compute_mdhhs_form_amount,
    compute_mdhhs_form_minutes,
    compute_monthly_minutes_rounded,
    compute_task_amount,
    generate_schedule,
    round_half_up,
    _placement_runs_on_date,
)
from app.extract import ExtractedForm
from app.validate import cross_check


# ---------------------------------------------------------------------------
# Mixed-frequency task set used for every month shape below.
# weekly_budget = 35 + 20 + 45 = 100  →  aggregate round_half_up(100×4.3)=430;
# Σ unrounded per-line mpd×dpw×4.3 = 430.0 → form total 430 (half-even once);
# Σ per-line ROUND_HALF_EVEN minute lines = 150+86+194 = 430 as well for this set.
# ---------------------------------------------------------------------------
MIXED_TASKS: list[dict[str, Any]] = [
    {"task_name": "Personal Care", "min_per_day": 5, "days_per_week": 7},
    {"task_name": "Errands", "min_per_day": 10, "days_per_week": 2},
    {"task_name": "Housekeeping", "min_per_day": 15, "days_per_week": 3},
]

_WEEK_FULL = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _fmt_monthly_mdhhs(mpd: int, dpw: int) -> str:
    # MDHHS line times: whole minutes per line (6064-P half-even), not raw float.
    rmin = compute_monthly_minutes_rounded(mpd, dpw)
    return f"{rmin // 60}:{rmin % 60:02d}"


def _make_extracted_form(tasks: list[dict[str, Any]], pay: float) -> ExtractedForm:
    """Build a form whose line amounts mirror the tasks (for Check 5 reconciliation)."""
    rows: list[dict[str, Any]] = []
    for t in tasks:
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        rows.append(
            {
                **t,
                "monthly_time_str": _fmt_monthly_mdhhs(mpd, dpw),
                "monthly_amount": compute_task_amount(mpd, dpw, pay),
            }
        )
    total = compute_mdhhs_form_amount(tasks, pay)
    return ExtractedForm(pay_rate=pay, tasks=rows, monthly_total_amount=total)


def _expected_occurrence(
    placement: TaskPlacement,
    month_dates: list[date],
) -> int:
    """Count month dates placed by config for a single task."""
    return sum(1 for d in month_dates if _placement_runs_on_date(placement, d))


def _month_dates(year: int, month: int) -> list[date]:
    import calendar

    _, nd = calendar.monthrange(year, month)
    return [date(year, month, d) for d in range(1, nd + 1)]


def _assert_row_invariant(cs: CalibratedSchedule) -> None:
    """Every daily row: duration_min == Σ task minutes."""
    for e in cs.daily_schedule:
        assert e.duration_min == sum(e.tasks.values()), (
            f"{e.date}: duration {e.duration_min} ≠ Σ tasks {sum(e.tasks.values())} "
            f"({e.tasks!r})"
        )


def _assert_occurrences_match_config(
    cs: CalibratedSchedule,
    cfg: ScheduleConfig,
    month_dates: list[date],
) -> None:
    """task_occurrence_counts equals the config-projected count per task."""
    counts = cs.as_dict()["task_occurrence_counts"]
    # Also recount from daily_schedule directly — they must agree.
    rebuilt: dict[str, int] = {}
    for e in cs.daily_schedule:
        for nm, mins in e.tasks.items():
            if mins > 0:
                rebuilt[nm] = rebuilt.get(nm, 0) + 1
    for p in cfg.tasks:
        expected = _expected_occurrence(p, month_dates)
        assert counts.get(p.task_name, 0) == expected, (
            f"{p.task_name}: declared count {counts.get(p.task_name, 0)} "
            f"≠ config-projected {expected}"
        )
        assert rebuilt.get(p.task_name, 0) == expected, (
            f"{p.task_name}: rebuilt count {rebuilt.get(p.task_name, 0)} "
            f"≠ config-projected {expected}"
        )


def _assert_grand_total_variance_zero(
    cs: CalibratedSchedule,
    tasks: list[dict[str, Any]],
) -> None:
    """Σ daily.duration_min == compute_mdhhs_form_minutes(tasks) — exact."""
    target = compute_mdhhs_form_minutes(tasks)
    actual = sum(d.duration_min for d in cs.daily_schedule)
    assert actual == target, f"grand total {actual} ≠ target {target}"
    assert cs.mdhhs_monthly_minutes == target
    # Reconciliation: all math checks pass at exact cap when totals hit authorization.
    form = _make_extracted_form(tasks, cs.pay_rate)
    report = cross_check(form, cs.as_dict())
    assert report.all_passed
    assert report.validation_status == "BILLABLE_EXACT"


# ---------------------------------------------------------------------------
# 28-day month — February 2026.
# Feb 1 2026 is a Sunday; 28 days = exactly 4 of each weekday.
# Base (no catchup):
#   Personal Care 5×28 = 140
#   Errands       10×(4 Sat + 4 Sun)  = 80
#   Housekeeping  15×(4 Fri + 4 Sat + 4 Sun) = 180
#   Σ = 400; target 430 → gap 30 (form total = Σ unrounded, half-even once).
# Close via two explicit Housekeeping catch-ups on Wednesdays (residual top-up
# may absorb the last minute if the mechanical total is 430).
# ---------------------------------------------------------------------------
def test_28_day_month_feb_2026_mixed_frequencies() -> None:
    year, month = 2026, 2
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Personal Care",
                min_per_day=5,
                days_per_week=7,
                selected_weekdays=list(_WEEK_FULL),
            ),
            TaskPlacement(
                task_name="Errands",
                min_per_day=10,
                days_per_week=2,
                selected_weekdays=["Saturday", "Sunday"],
            ),
            TaskPlacement(
                task_name="Housekeeping",
                min_per_day=15,
                days_per_week=3,
                selected_weekdays=["Friday", "Saturday", "Sunday"],
                selected_dates=["2026-02-18", "2026-02-25"],
            ),
        ]
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, year, month, config=cfg)

    md = _month_dates(year, month)
    assert len(md) == 28

    # Personal Care runs every day; Errands on 8 weekend days; Housekeeping on
    # 12 Fri/Sat/Sun + 2 Wed catch-ups = 14 days. Every one of the 28 days
    # has at least Personal Care → 28 rows in daily_schedule.
    assert len(cs.daily_schedule) == 28

    _assert_row_invariant(cs)
    _assert_occurrences_match_config(cs, cfg, md)
    _assert_grand_total_variance_zero(cs, MIXED_TASKS)

    # Spot-check the catch-up dates carry Housekeeping + Personal Care (no Errands).
    by_date = {e.date: e for e in cs.daily_schedule}
    for iso in ("2026-02-18", "2026-02-25"):
        e = by_date[date.fromisoformat(iso)]
        assert set(e.tasks) == {"Personal Care", "Housekeeping"}
        assert e.duration_min == 5 + 15


# ---------------------------------------------------------------------------
# 30-day month — April 2026.
# Apr 1 2026 is a Wednesday. Counts: Mon=4, Tue=4, Wed=5, Thu=5, Fri=4, Sat=4,
# Sun=4; Σ = 30. Base = 150 + 80 + 180 = 410; gap 20 → two Errands Wed
# catch-ups. Placement explicitly lists those weekdays; no reliance on
# default weekend-weighted templates.
# ---------------------------------------------------------------------------
def test_30_day_month_apr_2026_mixed_frequencies() -> None:
    year, month = 2026, 4
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Personal Care",
                min_per_day=5,
                days_per_week=7,
                selected_weekdays=list(_WEEK_FULL),
            ),
            TaskPlacement(
                task_name="Errands",
                min_per_day=10,
                days_per_week=2,
                selected_weekdays=["Saturday", "Sunday"],
                selected_dates=["2026-04-22", "2026-04-29"],
            ),
            TaskPlacement(
                task_name="Housekeeping",
                min_per_day=15,
                days_per_week=3,
                selected_weekdays=["Friday", "Saturday", "Sunday"],
            ),
        ]
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, year, month, config=cfg)

    md = _month_dates(year, month)
    assert len(md) == 30
    assert len(cs.daily_schedule) == 30

    _assert_row_invariant(cs)
    _assert_occurrences_match_config(cs, cfg, md)
    _assert_grand_total_variance_zero(cs, MIXED_TASKS)

    # Catch-up days host Errands + Personal Care only (no Housekeeping).
    by_date = {e.date: e for e in cs.daily_schedule}
    for iso in ("2026-04-22", "2026-04-29"):
        e = by_date[date.fromisoformat(iso)]
        assert set(e.tasks) == {"Personal Care", "Errands"}
        # Residual top-up may add 1 min to the larger of the two line items on this day.
        assert e.duration_min in (15, 16)


# ---------------------------------------------------------------------------
# 31-day month — May 2026.
# May 1 2026 is a Friday. Counts: Fri=5, Sat=5, Sun=5, Mon=4, Tue=4, Wed=4,
# Thu=4; Σ = 31. The weekend-weighted "default" placement would overshoot
# here (155 + 100 + 225 = 480 > 430). We therefore *drop* Friday from
# Housekeeping's selected_weekdays — only Sat+Sun — and reintroduce a
# single Wednesday as an explicit catch-up. An Errands catch-up closes
# the remaining 10-minute gap. This shape demonstrates that
# selected_weekdays is authoritative, independent of days_per_week.
#
# Base with trimmed Housekeeping:
#   Personal Care  5 × 31 = 155
#   Errands       10 × 10 = 100
#   Housekeeping  15 × 10 = 150
#   Σ = 405; target 430 → gap 25 = one Housekeeping + one Errands.
# ---------------------------------------------------------------------------
def test_31_day_month_may_2026_mixed_frequencies() -> None:
    year, month = 2026, 5
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Personal Care",
                min_per_day=5,
                days_per_week=7,
                selected_weekdays=list(_WEEK_FULL),
            ),
            TaskPlacement(
                task_name="Errands",
                min_per_day=10,
                days_per_week=2,
                selected_weekdays=["Saturday", "Sunday"],
                selected_dates=["2026-05-13"],
            ),
            TaskPlacement(
                task_name="Housekeeping",
                min_per_day=15,
                days_per_week=3,
                selected_weekdays=["Saturday", "Sunday"],
                selected_dates=["2026-05-06"],
            ),
        ]
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, year, month, config=cfg)

    md = _month_dates(year, month)
    assert len(md) == 31
    assert len(cs.daily_schedule) == 31

    _assert_row_invariant(cs)
    _assert_occurrences_match_config(cs, cfg, md)
    _assert_grand_total_variance_zero(cs, MIXED_TASKS)

    # Occurrence counts reflect the trimmed Housekeeping shape:
    # 10 weekend + 1 explicit Wed catch-up = 11 Housekeeping days.
    counts = cs.as_dict()["task_occurrence_counts"]
    assert counts["Housekeeping"] == 11
    assert counts["Errands"] == 11  # 10 weekend + 1 catch-up
    assert counts["Personal Care"] == 31


# ---------------------------------------------------------------------------
# Cross-cutting test: full reconciliation passes end-to-end for every
# month shape. Exercises Check 5's per-task sub-rows AND the 5a TOTAL row.
# ---------------------------------------------------------------------------
def test_reconciliation_total_variance_zero_across_month_shapes() -> None:
    """Regression: no month shape may drift the Check 5 TOTAL sub-check."""
    scenarios: list[tuple[int, int, ScheduleConfig]] = [
        (
            2026,
            2,
            ScheduleConfig(
                tasks=[
                    TaskPlacement("Personal Care", 5, 7, list(_WEEK_FULL)),
                    TaskPlacement(
                        "Errands", 10, 2, ["Saturday", "Sunday"]
                    ),
                    TaskPlacement(
                        "Housekeeping",
                        15,
                        3,
                        ["Friday", "Saturday", "Sunday"],
                        selected_dates=["2026-02-18", "2026-02-25"],
                    ),
                ]
            ),
        ),
        (
            2026,
            4,
            ScheduleConfig(
                tasks=[
                    TaskPlacement("Personal Care", 5, 7, list(_WEEK_FULL)),
                    TaskPlacement(
                        "Errands",
                        10,
                        2,
                        ["Saturday", "Sunday"],
                        selected_dates=["2026-04-22", "2026-04-29"],
                    ),
                    TaskPlacement(
                        "Housekeeping",
                        15,
                        3,
                        ["Friday", "Saturday", "Sunday"],
                    ),
                ]
            ),
        ),
        (
            2026,
            5,
            ScheduleConfig(
                tasks=[
                    TaskPlacement("Personal Care", 5, 7, list(_WEEK_FULL)),
                    TaskPlacement(
                        "Errands",
                        10,
                        2,
                        ["Saturday", "Sunday"],
                        selected_dates=["2026-05-13"],
                    ),
                    TaskPlacement(
                        "Housekeeping",
                        15,
                        3,
                        ["Saturday", "Sunday"],
                        selected_dates=["2026-05-06"],
                    ),
                ]
            ),
        ),
    ]
    for year, month, cfg in scenarios:
        cs = generate_schedule(MIXED_TASKS, 27.0, year, month, config=cfg)
        form = _make_extracted_form(MIXED_TASKS, 27.0)
        report = cross_check(form, cs.as_dict())

        def _chk(n: int):
            return next(c for c in report.checks if c.number == n)

        # Check 7 — per-task drift (informational) + occurrence floor.
        c7 = _chk(7)
        assert "floor" in c7.sub_checks[-1].task_name.lower()
        assert c7.sub_checks[-1].passed, f"{year}-{month}: floor check: {c7.actual}"

        c11 = _chk(11)
        for sub in c11.sub_checks:
            assert sub.variance == 0, (
                f"{year}-{month}: occurrence variance for {sub.task_name!r} "
                f"is {sub.variance:+d} (expected={sub.auth_min}, "
                f"actual={sub.scheduled_min})"
            )
        assert c11.passed, f"{year}-{month}: check 11 failed: {c11.actual}"

        c12 = _chk(12)
        assert c12.passed, f"{year}-{month}: check 12 failed: {c12.detail}"


# ---------------------------------------------------------------------------
# Explicit catch-up dates survive the config → schedule → dict round trip.
# ---------------------------------------------------------------------------
def test_selected_dates_round_trip_through_as_dict() -> None:
    """``selected_dates`` set on a task reach ``as_dict()['config']`` verbatim."""
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement("Personal Care", 5, 7, list(_WEEK_FULL)),
            TaskPlacement("Errands", 10, 2, ["Saturday", "Sunday"]),
            TaskPlacement(
                "Housekeeping",
                15,
                3,
                ["Friday", "Saturday", "Sunday"],
                selected_dates=["2026-02-18", "2026-02-25"],
            ),
        ]
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 2, config=cfg)
    d = cs.as_dict()
    hk = next(t for t in d["config"]["tasks"] if t["task_name"] == "Housekeeping")
    assert hk["selected_dates"] == ["2026-02-18", "2026-02-25"]
    assert hk["selected_weekdays"] == ["Friday", "Saturday", "Sunday"]


def test_overshoot_31_day_months_billable_caps() -> None:
    """31-day months may deliver above authorization; billable caps min(del, auth)."""
    daily_specs = [("D1", 16), ("D2", 14), ("D3", 8), ("D4", 6), ("D5", 6), ("D6", 25), ("D7", 2)]
    weekly_specs = [("W1", 14), ("W2", 12), ("W3", 35), ("W4", 20)]
    tasks: list[dict[str, Any]] = []
    for name, mpd in daily_specs:
        tasks.append({"task_name": name, "min_per_day": mpd, "days_per_week": 7})
    for name, mpd in weekly_specs:
        tasks.append({"task_name": name, "min_per_day": mpd, "days_per_week": 1})

    target = compute_mdhhs_form_minutes(tasks)
    months_31 = [(y, m) for y in (2025, 2026, 2027) for m in (1, 3, 5, 7, 8, 10, 12)]
    for y, m in months_31:
        cs = generate_schedule(tasks, 27.0, y, m, config=None)
        assert cs.mdhhs_monthly_minutes == target
        assert cs.billable_minutes == min(cs.delivered_minutes, target)
        assert cs.billable_minutes <= target
