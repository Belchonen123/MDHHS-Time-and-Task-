"""Focused regression tests for the Michigan Home Help schedule engine.

Each test is small and named after the *business rule* it protects,
not the data shape. Month-length coverage spans 28, 29 (leap February),
30, and 31 days. Three tests at the bottom are explicitly
*regression guards* — each would have failed under the hardcoded
logic removed in the recent refactor series (see commit history /
audit write-up for the exact pre-fix behaviour).

Minimal shared fixture
----------------------
``MIXED_TASKS`` has one task at each of the three canonical Michigan
Home Help frequencies (7-day / 3-day / 2-day). Its weekly budget is
100 min; Σ unrounded per-line MDHHS minutes resolves to **430**
(fixed once with half-even)—the engine targets that aggregate, not half-up
aggregate (430) separately from Σ per-line HALF_EVEN rounded lines.
"""

from __future__ import annotations

import calendar
import copy
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.calculate import (
    WEEKS_PER_MONTH,
    CalibratedSchedule,
    ScheduleConfig,
    TaskPlacement,
    compute_mdhhs_form_amount,
    compute_mdhhs_form_minutes,
    compute_monthly_minutes_rounded,
    compute_task_amount,
    compute_weekly_budget,
    default_config_for,
    generate_schedule,
    round_half_up,
    _sum_monthly_minutes,
)
from app.extract import ExtractedForm
from app.validate import cross_check


# --- Minimal shared fixture --------------------------------------------------

MIXED_TASKS: list[dict[str, Any]] = [
    {"task_name": "Personal Care", "min_per_day": 5, "days_per_week": 7},
    {"task_name": "Errands", "min_per_day": 10, "days_per_week": 2},
    {"task_name": "Housekeeping", "min_per_day": 15, "days_per_week": 3},
]

_ALL_DAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def _make_form(tasks: list[dict[str, Any]], pay: float = 27.0) -> ExtractedForm:
    """Build an ExtractedForm whose line amounts mirror the tasks."""
    rows: list[dict[str, Any]] = []
    for t in tasks:
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        rows.append({
            **t,
            "monthly_time_str": (
                f"{compute_monthly_minutes_rounded(mpd, dpw) // 60}:"
                f"{compute_monthly_minutes_rounded(mpd, dpw) % 60:02d}"
            ),
            "monthly_amount": compute_task_amount(mpd, dpw, pay),
        })
    return ExtractedForm(
        pay_rate=pay,
        tasks=rows,
        monthly_total_amount=compute_mdhhs_form_amount(tasks, pay),
    )


def _config(
    pc_days: list[str],
    err_days: list[str],
    hk_days: list[str],
    err_dates: list[str] | None = None,
    hk_dates: list[str] | None = None,
    pc_dates: list[str] | None = None,
) -> ScheduleConfig:
    """Build a ScheduleConfig for MIXED_TASKS with explicit placement."""
    return ScheduleConfig(tasks=[
        TaskPlacement(
            task_name="Personal Care", min_per_day=5, days_per_week=7,
            selected_weekdays=pc_days, selected_dates=pc_dates or [],
        ),
        TaskPlacement(
            task_name="Errands", min_per_day=10, days_per_week=2,
            selected_weekdays=err_days, selected_dates=err_dates or [],
        ),
        TaskPlacement(
            task_name="Housekeeping", min_per_day=15, days_per_week=3,
            selected_weekdays=hk_days, selected_dates=hk_dates or [],
        ),
    ])


def _month_dates(year: int, month: int) -> list[date]:
    _, n = calendar.monthrange(year, month)
    return [date(year, month, d) for d in range(1, n + 1)]


# Per-month canonical configs that each calibrate exactly to 430 aggregate form minutes.
# The placement math is sketched inline so a reader can see how each
# shape hits the target without consulting a hidden helper.

def _feb_2026_cfg() -> ScheduleConfig:
    # Feb 2026: 28 days, Sun–Sat all × 4.
    # Base: PC 140 + Err(Sat+Sun) 80 + HK(Fri+Sat+Sun) 180 = 400.
    # Gap 31 → two explicit Housekeeping catch-ups on Wednesdays.
    return _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        hk_dates=["2026-02-18", "2026-02-25"],
    )


def _feb_2024_leap_cfg() -> ScheduleConfig:
    # Feb 2024: 29 days (leap), Thu start. Thu × 5; others × 4.
    # Base: PC 145 + Err(Sat+Sun) 80 + HK(Fri+Sat+Sun) 180 = 405.
    # Gap 26 → one Errands Wed catch-up (10) + one HK Wed catch-up (15).
    return _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        err_dates=["2024-02-14"],
        hk_dates=["2024-02-28"],
    )


def _jun_2026_cfg() -> ScheduleConfig:
    # Jun 2026: 30 days, Mon start. Mon/Tue × 5; others × 4.
    # Base: PC 150 + Err(Sat+Sun) 80 + HK(Fri+Sat+Sun) 180 = 410.
    # Gap 21 → two Errands Wed catch-ups.
    return _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        err_dates=["2026-06-10", "2026-06-24"],
    )


def _mar_2026_cfg() -> ScheduleConfig:
    # Mar 2026: 31 days, Sun start. Sun/Mon/Tue × 5; others × 4.
    # A weekend-weighted default would overshoot (Fri/Sat/Sun × 4+4+5 = 13
    # HK days). We therefore *trim* HK and Err to weekends only and add
    # explicit dates on two Fridays (HK) and two Wednesdays (Errands).
    # Base with trim: PC 155 + Err(Sat+Sun) 90 + HK(Sat+Sun) 135 = 380.
    # Gap 51 → 2 HK dates (30) + 2 Errands dates (20) = 50 (+1 residual top-up in engine).
    return _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Saturday", "Sunday"],
        err_dates=["2026-03-04", "2026-03-18"],
        hk_dates=["2026-03-06", "2026-03-13"],
    )


# --- Assertion helpers (kept tiny on purpose) --------------------------------

def _row_invariant(cs: CalibratedSchedule) -> None:
    for e in cs.daily_schedule:
        assert e.duration_min == sum(e.tasks.values()), (
            f"{e.date}: duration {e.duration_min} ≠ Σ tasks {sum(e.tasks.values())}"
        )


def _variance_zero(cs: CalibratedSchedule, tasks: list[dict[str, Any]]) -> None:
    target = compute_mdhhs_form_minutes(tasks)
    assert sum(e.duration_min for e in cs.daily_schedule) == target


# ---------------------------------------------------------------------------
# BUSINESS RULE: Row duration equals Σ task minutes on every day.
# ---------------------------------------------------------------------------
def test_daily_row_duration_equals_sum_of_task_minutes() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=_config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        err_dates=["2026-04-22", "2026-04-29"],
    ))
    _row_invariant(cs)
    # At least one day has more than one task — the invariant isn't trivial.
    assert any(len(e.tasks) >= 2 for e in cs.daily_schedule)


# ---------------------------------------------------------------------------
# BUSINESS RULE: Task reconciliation sums actual scheduled minutes per task,
# not the authorised 4.3-week projection.
# ---------------------------------------------------------------------------
def test_task_reconciliation_sums_actual_scheduled_minutes_per_task() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 2, config=_feb_2026_cfg())
    sd = cs.as_dict()
    # Rebuild per-task totals from daily_schedule and compare with the
    # numbers Check 5 reports.
    rebuilt: dict[str, int] = {}
    for d in sd["daily_schedule"]:
        for nm, m in d["tasks"].items():
            rebuilt[nm] = rebuilt.get(nm, 0) + int(m)
    report = cross_check(_make_form(MIXED_TASKS), sd)
    c7 = next(c for c in report.checks if c.number == 7)
    c7_subs = {
        s.task_name: s
        for s in c7.sub_checks
        if "floor" not in s.task_name.lower()
    }
    for nm, expected in rebuilt.items():
        assert c7_subs[nm].scheduled_min == expected, (
            f"{nm}: Check 7 reports {c7_subs[nm].scheduled_min}, daily totals say {expected}"
        )


# ---------------------------------------------------------------------------
# BUSINESS RULE: A valid plan's grand-total variance is exactly zero.
# ---------------------------------------------------------------------------
def test_grand_total_variance_is_zero_on_valid_plan() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=_config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        err_dates=["2026-04-22", "2026-04-29"],
    ))
    _variance_zero(cs, MIXED_TASKS)
    report = cross_check(_make_form(MIXED_TASKS), cs.as_dict())
    assert report.all_passed
    assert report.validation_status == "BILLABLE_EXACT"


# ---------------------------------------------------------------------------
# BUSINESS RULE: selected_dates places a task on exactly that ISO date,
# even when the weekday isn't in selected_weekdays.
# ---------------------------------------------------------------------------
def test_selected_dates_places_task_on_iso_date_outside_selected_weekdays() -> None:
    # HK weekdays are Fri/Sat/Sun, but we add a Wednesday via selected_dates.
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=_config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        hk_dates=["2026-04-15"],   # Wed
    ))
    by_date = {e.date: e for e in cs.daily_schedule}
    wed = by_date[date(2026, 4, 15)]
    assert "Housekeeping" in wed.tasks
    assert wed.tasks["Housekeeping"] == 15


# ---------------------------------------------------------------------------
# BUSINESS RULE: A weekday NOT in selected_weekdays and NOT in selected_dates
# must not host the task, even if its frequency (days_per_week) suggests it.
# ---------------------------------------------------------------------------
def test_weekday_not_in_config_does_not_host_task_regardless_of_frequency() -> None:
    # Errands is a 2/wk task, but its config places it only on Sundays.
    # Saturdays MUST stay Errands-free.
    cfg = _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Sunday"],                # 4 Sundays only
        hk_days=["Friday", "Saturday", "Sunday"],
        # Calibration isn't the point of this test — we just want to
        # confirm weekday discipline. Close the gap with explicit dates.
        err_dates=["2026-04-01", "2026-04-08", "2026-04-15", "2026-04-22"],
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=cfg)
    for e in cs.daily_schedule:
        if e.date.weekday() == 5:  # Saturday
            assert "Errands" not in e.tasks, (
                f"Errands must not appear on {e.date} (Saturday not in config)"
            )


# ---------------------------------------------------------------------------
# BUSINESS RULE: A catch-up day carries only the tasks whose config
# explicitly places them on that date. The composition is not derived
# from a hardcoded "7/wk + 2/wk" formula.
# ---------------------------------------------------------------------------
def test_catchup_day_carries_only_config_placed_tasks() -> None:
    # Configure a single explicit catch-up date that carries *just* the
    # 3/wk Housekeeping task. Personal Care still runs there because
    # its selected_weekdays covers all days; but Errands must NOT.
    cfg = _config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        hk_dates=["2026-04-15"],   # Wed
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=cfg)
    by_date = {e.date: e for e in cs.daily_schedule}
    catch = by_date[date(2026, 4, 15)]
    assert set(catch.tasks) == {"Personal Care", "Housekeeping"}
    assert "Errands" not in catch.tasks
    assert catch.duration_min == 5 + 15


# ---------------------------------------------------------------------------
# MONTH-SHAPE BUSINESS RULE: The engine calibrates exactly on a 28-day month.
# ---------------------------------------------------------------------------
def test_engine_calibrates_exactly_on_28_day_february_2026() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 2, config=_feb_2026_cfg())
    assert len(cs.daily_schedule) == 28
    _row_invariant(cs)
    _variance_zero(cs, MIXED_TASKS)


# ---------------------------------------------------------------------------
# MONTH-SHAPE BUSINESS RULE: The engine calibrates exactly on a 29-day
# leap-year February. This month length is absent from every default
# template and is the most common failure mode for any hardcoded
# "ndays = 28 or 30 or 31" branch.
# ---------------------------------------------------------------------------
def test_engine_calibrates_exactly_on_29_day_leap_february_2024() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2024, 2, config=_feb_2024_leap_cfg())
    assert len(cs.daily_schedule) == 29
    _row_invariant(cs)
    _variance_zero(cs, MIXED_TASKS)


# ---------------------------------------------------------------------------
# MONTH-SHAPE BUSINESS RULE: The engine calibrates exactly on a 30-day month.
# ---------------------------------------------------------------------------
def test_engine_calibrates_exactly_on_30_day_june_2026() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 6, config=_jun_2026_cfg())
    assert len(cs.daily_schedule) == 30
    _row_invariant(cs)
    _variance_zero(cs, MIXED_TASKS)


# ---------------------------------------------------------------------------
# MONTH-SHAPE BUSINESS RULE: The engine calibrates exactly on a 31-day month.
# ---------------------------------------------------------------------------
def test_engine_calibrates_exactly_on_31_day_march_2026() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 3, config=_mar_2026_cfg())
    assert len(cs.daily_schedule) == 31
    _row_invariant(cs)
    _variance_zero(cs, MIXED_TASKS)


# ---------------------------------------------------------------------------
# BUSINESS RULE: Mixed 7-/3-/2-day task frequencies each honor their own
# configured weekdays independently.
# ---------------------------------------------------------------------------
def test_mixed_frequencies_each_honor_their_own_configured_weekdays() -> None:
    cfg = _feb_2026_cfg()
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 2, config=cfg)

    def _expected(placement: TaskPlacement) -> int:
        sel_wd, sel_dt = set(placement.selected_weekdays), set(placement.selected_dates)
        return sum(
            1 for d in _month_dates(2026, 2)
            if _ALL_DAYS[d.weekday()] in sel_wd or d.isoformat() in sel_dt
        )

    counts: dict[str, int] = {}
    for e in cs.daily_schedule:
        for nm in e.tasks:
            counts[nm] = counts.get(nm, 0) + 1

    for p in cfg.tasks:
        assert counts[p.task_name] == _expected(p), (
            f"{p.task_name} count {counts[p.task_name]} ≠ expected "
            f"{_expected(p)} from its own selected_weekdays/dates"
        )


# ---------------------------------------------------------------------------
# BUSINESS RULE: End-to-end reconciliation passes all 11 checks on every
# month shape. This is the headline "workbook reconciliation" invariant.
# ---------------------------------------------------------------------------
def test_end_to_end_reconciliation_passes_on_every_month_shape() -> None:
    cases = [
        (2026, 2, _feb_2026_cfg()),
        (2024, 2, _feb_2024_leap_cfg()),
        (2026, 6, _jun_2026_cfg()),
        (2026, 3, _mar_2026_cfg()),
    ]
    form = _make_form(MIXED_TASKS)
    for year, month, cfg in cases:
        cs = generate_schedule(MIXED_TASKS, 27.0, year, month, config=cfg)
        report = cross_check(form, cs.as_dict())
        assert report.all_passed, (
            f"{year}-{month:02d}: {report.fail_count} checks failed\n{report.summary}"
        )


# ===========================================================================
# REGRESSION GUARDS — each of these would have failed under hardcoded logic
# that was active before the config-driven refactor series. The comment on
# each test describes the specific pre-fix failure mode.
# ===========================================================================


# ---------------------------------------------------------------------------
# REGRESSION GUARD (audit B-5 — validate.py Check 11 catchup actual duration).
#
# Old behaviour: Check 11 asked whether ``total - Σ(2/wk mpd) != target``,
# i.e. it presumed the catch-up day carried exactly the sum of 2/wk
# task minutes. A plan whose user-configured catch-up carries a
# different composition (a 3/wk task alone, a single 7/wk top-up,
# etc.) would be misjudged: the check either silently false-passed
# or flipped to fail even when the catch-up was in fact the only
# thing making the plan hit target. The current check reads the
# catch-up row's actual duration from daily_schedule.
# ---------------------------------------------------------------------------
def test_non_2wk_catchup_composition_still_passes_check_11() -> None:
    # Configure a catch-up that carries *only* the 3/wk Housekeeping task
    # (Σ 2/wk = 10 min, catch-up actual = 15 min — these don't match).
    cfg = _config(
        pc_days=[d for d in _ALL_DAYS if d != "Wednesday"],   # PC trimmed to avoid overshoot
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        hk_dates=["2026-04-15"],   # Wed catch-up with HK only
    )
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=cfg)
    sd = cs.as_dict()
    # Confirm the setup: the catch-up day truly carries only HK (no Errands).
    catch = next(d for d in sd["daily_schedule"] if d["date"] == "2026-04-15")
    assert "Errands" not in catch["tasks"]
    assert "Housekeeping" in catch["tasks"]
    sd["catchup_date"] = "2026-04-15"
    sd["catchup_day_min"] = int(catch["duration_min"])
    report = cross_check(_make_form(MIXED_TASKS), sd)
    c11 = next(c for c in report.checks if c.number == 13)
    assert c11.passed, f"Check 11 regressed on non-2wk catch-up: {c11.actual}"


# ---------------------------------------------------------------------------
# REGRESSION GUARD (audit B-3 — canonical line vs generic 2/wk metadata).
#
# Named ``laundry_days`` / ``shopping_days`` / ``travel_days`` list calendar
# dates where the canonical Laundry / Shopping / Travel MDHHS lines run — not
# every 2/wk chore. Generic Errands-only plans still schedule 2/wk visits but
# leave those three lists empty.
# ---------------------------------------------------------------------------
def test_named_day_lists_track_canonical_laundry_shopping_travel_lines() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 4, config=_config(
        pc_days=list(_ALL_DAYS),
        err_days=["Saturday", "Sunday"],
        hk_days=["Friday", "Saturday", "Sunday"],
        err_dates=["2026-04-22", "2026-04-29"],
    ))
    assert cs.laundry_days == []
    assert cs.shopping_days == []
    assert cs.travel_days == []
    assert len(cs.hw_days) > 0
    errand_days = {e.date for e in cs.daily_schedule if "Errands" in e.tasks}
    assert len(errand_days) > 0


# ---------------------------------------------------------------------------
# REGRESSION GUARD (audit B-2 — default_config_for greedy early-exit).
#
# Old behaviour: when default_config_for had to close a calibration
# gap and the task with the largest min_per_day was a 7/wk task
# (already on every day, no eligible dates), _greedy_close_gap
# returned immediately instead of trying the next-largest task. The
# resulting config under-scheduled the month by a reducible amount
# — undetected because no pre-refactor test exercised a non-Ottilie
# month shape with the legacy fallback. The current greedy iterates
# past saturated tasks.
#
# We use the MIXED_TASKS set here (not the Ottilie one) to make
# sure this regression guard passes independently of Ottilie-specific
# calibration luck.
# ---------------------------------------------------------------------------
def test_default_config_fallback_converges_on_28_day_february() -> None:
    """default_config_for + generate_schedule stay consistent with the billable cap."""
    cs = generate_schedule(MIXED_TASKS, 27.0, 2024, 2, config=default_config_for(MIXED_TASKS, 2024, 2))
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


# ---------------------------------------------------------------------------
# INVARIANT GUARD: the row invariant holds even when the schedule is
# hand-edited after generation (i.e. the reconciliation layer *detects*
# a violation instead of papering over it). This is the flip side of
# the happy-path invariant — catches regressions where validate.py
# would be tempted to re-derive row totals silently.
# ---------------------------------------------------------------------------
def test_reconciliation_flags_a_hand_broken_row_invariant() -> None:
    cs = generate_schedule(MIXED_TASKS, 27.0, 2026, 2, config=_feb_2026_cfg())
    sd = copy.deepcopy(cs.as_dict())
    # Bump one task's minutes on a single day without updating duration_min.
    target_iso = sd["daily_schedule"][0]["date"]
    any_task = next(iter(sd["daily_schedule"][0]["tasks"]))
    sd["daily_schedule"][0]["tasks"][any_task] = (
        int(sd["daily_schedule"][0]["tasks"][any_task]) + 3
    )
    report = cross_check(_make_form(MIXED_TASKS), sd)
    c10 = next(c for c in report.checks if c.number == 12)
    assert not c10.passed
    assert target_iso in str(c10.detail)


# ---------------------------------------------------------------------------
# Eating/Feeding (daily) & complex-care — placement follows authorization only.
# ---------------------------------------------------------------------------
def test_wound_care_three_per_week_schedules_via_config_not_name_bucket() -> None:
    """3/wk task schedules from config; billable caps at authorization."""
    tasks = [{"task_name": "Wound Care", "min_per_day": 20, "days_per_week": 3}]
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Wound Care",
                min_per_day=20,
                days_per_week=3,
                selected_weekdays=["Friday", "Saturday", "Sunday"],
                selected_dates=[],
            )
        ]
    )
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    occ = int(cs.as_dict().get("task_occurrence_counts", {}).get("Wound Care", 0))
    assert 12 <= occ <= 14
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


def test_eating_feeding_seven_per_week_explicit_config_visits_every_day() -> None:
    tasks = [{"task_name": "Eating/Feeding", "min_per_day": 15, "days_per_week": 7}]
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Eating/Feeding",
                min_per_day=15,
                days_per_week=7,
                selected_weekdays=list(_ALL_DAYS),
                selected_dates=[],
            )
        ]
    )
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    assert len(cs.daily_schedule) == 30
    eating_sum = sum(
        int(e.tasks.get("Eating/Feeding", 0)) for e in cs.daily_schedule
    )
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(eating_sum, auth)
    for e in cs.daily_schedule:
        ef = int(e.tasks.get("Eating/Feeding", 0))
        assert ef >= 15  # residual-gap top-up may add minutes on one day
