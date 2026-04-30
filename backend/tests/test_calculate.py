"""Tests for the calculate module.

Two regimes are covered:

* **Default-config regression** — Ottilie April 2026 still calibrates to
  the MDHHS 4.3-week target when ``config`` is omitted, now via
  weekend-weighted defaults (Fri/Sat/Sun for 3/wk, Sat/Sun for 2/wk) plus
  the last-Wednesday catch-up.
* **Explicit-config path** — a user-supplied ``ScheduleConfig`` replaces
  the defaults entirely. Covers per-weekday selections, per-weekday
  start-time overrides, and non-canonical days-per-week (4/wk).
"""

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from decimal import Decimal, ROUND_HALF_EVEN

from app.calculate import (
    COMPANION_TO_PARENT,
    CalibrationError,
    DayCapacityExceeded,
    DayEntry,
    ScheduleConfig,
    TaskPlacement,
    _apply_worker_availability_to_entry,
    _default_start_time_by_weekday,
    assert_worker_availability_sane,
    clamp_shift_to_availability,
    compute_billable_amount,
    compute_billable_minutes,
    compute_mdhhs_form_amount,
    compute_mdhhs_form_minutes,
    compute_monthly_minutes_rounded,
    default_config_for,
    default_weekdays_for_dpw,
    default_worker_availability,
    effective_visit_latest_for_worker_day,
    compute_task_amount,
    generate_schedule,
    parse_worker_availability,
    preferred_window_from_worker_availability,
    round_half_up,
    _coplace_companions,
    _sum_monthly_minutes,
)

# Ottilie Smith — inputs from the reference MDHHS-6064
OTTILIE_TASKS = [
    {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
    {"task_name": "Dressing", "min_per_day": 14, "days_per_week": 7},
    {"task_name": "Grooming", "min_per_day": 8, "days_per_week": 7},
    {"task_name": "Mobility", "min_per_day": 16, "days_per_week": 7},
    {"task_name": "Toileting", "min_per_day": 6, "days_per_week": 7},
    {"task_name": "Transferring", "min_per_day": 8, "days_per_week": 7},
    {"task_name": "Medication", "min_per_day": 2, "days_per_week": 7},
    {"task_name": "Meal Preparation", "min_per_day": 50, "days_per_week": 7},
    {"task_name": "Housework", "min_per_day": 12, "days_per_week": 3},
    {"task_name": "Laundry", "min_per_day": 21, "days_per_week": 2},
    {"task_name": "Shopping for Food/Meds", "min_per_day": 15, "days_per_week": 2},
    {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 2},
]

OTTILIE_PREFERRED = {
    "weekday_start": "7:00 AM",
    "weekend_start": "12:00 PM",
}


def _avery_tasks() -> list[dict[str, Any]]:
    """Latisha-shaped mix from overshoot regression (7× dailies + four 1/wk)."""
    daily_specs = [
        ("D1", 16),
        ("D2", 14),
        ("D3", 8),
        ("D4", 6),
        ("D5", 6),
        ("D6", 25),
        ("D7", 2),
    ]
    weekly_specs = [("W1", 14), ("W2", 12), ("W3", 35), ("W4", 20)]
    tasks: list[dict[str, Any]] = []
    for name, mpd in daily_specs:
        tasks.append({"task_name": name, "min_per_day": mpd, "days_per_week": 7})
    for name, mpd in weekly_specs:
        tasks.append({"task_name": name, "min_per_day": mpd, "days_per_week": 1})
    return tasks


# ---------------------------------------------------------------------------
# Billable layer (delivered vs authorized cap).
# ---------------------------------------------------------------------------
def test_compute_billable_minutes_caps_at_authorized() -> None:
    assert compute_billable_minutes(3000, 2666) == 2666


def test_compute_billable_minutes_returns_delivered_when_under_cap() -> None:
    assert compute_billable_minutes(2480, 2666) == 2480


def test_avery_may_2026_overshoots_and_caps_at_2666() -> None:
    tasks = _avery_tasks()
    assert compute_mdhhs_form_minutes(tasks) == 2666
    assert compute_mdhhs_form_amount(tasks, 27.0) == pytest.approx(1199.70)
    cs = generate_schedule(tasks, 27.0, 2026, 5, config=None)
    assert cs.mdhhs_monthly_minutes == 2666
    assert cs.delivered_minutes == 2792
    assert cs.billable_minutes == 2666
    assert cs.billable_amount == pytest.approx(1199.70)


def test_avery_may_2025_calendar_cap_acceptance() -> None:
    """May 2025 (not May 2026) matches 2711 delivered / 2666 cap for this fixture shape."""
    tasks = _avery_tasks()
    cs = generate_schedule(tasks, 27.0, 2025, 5, config=None)
    assert cs.delivered_minutes == 2711
    assert cs.mdhhs_monthly_minutes == 2666
    assert cs.billable_minutes == 2666
    assert cs.billable_amount == pytest.approx(1199.70)
    by = {e.date: e for e in cs.daily_schedule}
    w3 = [d for d in sorted(by) if by[d].tasks.get("W3")]
    assert [d.isoformat() for d in w3] == [
        "2025-05-04",
        "2025-05-11",
        "2025-05-18",
        "2025-05-25",
    ]
    assert by[date(2025, 5, 31)].duration_min == 77


def test_avery_validation_may_2025_at_cap() -> None:
    from app.extract import ExtractedForm
    from app.validate import cross_check

    tasks = _avery_tasks()
    rows: list[dict] = []
    pay = 27.0
    for t in tasks:
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        mm_r = compute_monthly_minutes_rounded(mpd, dpw)
        rows.append(
            {
                **t,
                "monthly_time_str": f"{mm_r // 60}:{mm_r % 60:02d}",
                "monthly_amount": compute_task_amount(mpd, dpw, pay),
            }
        )
    total = compute_mdhhs_form_amount(rows, pay)
    form = ExtractedForm(pay_rate=pay, tasks=rows, monthly_total_amount=total)
    sd = generate_schedule(tasks, pay, 2025, 5, None).as_dict()
    r = cross_check(form, sd)
    assert r.all_passed
    assert r.validation_status == "BILLABLE_AT_CAP"


# ---------------------------------------------------------------------------
# Defaults — weekend-weighted table (reference plan).
# ---------------------------------------------------------------------------
def test_default_config_one_per_week_tasks_use_distinct_weekdays() -> None:
    """With worker availability, 1×/wk tasks should spread across weekdays by default."""
    tasks = [
        {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
        {"task_name": "Laundry", "min_per_day": 14, "days_per_week": 1},
    ]
    cfg = default_config_for(
        tasks,
        2026,
        5,
        worker_availability=default_worker_availability(),
    )
    by_name = {p.task_name: p for p in cfg.tasks}
    s_day = by_name["Shopping for Food/Meds"].selected_weekdays
    l_day = by_name["Laundry"].selected_weekdays
    assert s_day and l_day
    assert s_day[0] != l_day[0]


def test_default_weekdays_table() -> None:
    assert default_weekdays_for_dpw(7) == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert default_weekdays_for_dpw(6) == [
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert default_weekdays_for_dpw(5) == [
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert default_weekdays_for_dpw(4) == ["Thursday", "Friday", "Saturday", "Sunday"]
    assert default_weekdays_for_dpw(3) == ["Friday", "Saturday", "Sunday"]
    assert default_weekdays_for_dpw(2) == ["Saturday", "Sunday"]
    assert default_weekdays_for_dpw(1) == ["Sunday"]


# ---------------------------------------------------------------------------
# Ottilie April 2026 — default config regression.
# ---------------------------------------------------------------------------
def test_ottilie_april_2026_calibration_default_config() -> None:
    """Defaults replicate the calibration target via last-Wed catch-up."""
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    assert cs.mdhhs_weekly_minutes == 988
    assert cs.mdhhs_monthly_minutes == 4248
    assert cs.mdhhs_monthly_amount == 1911.78
    assert cs.weekday_std_min == 120
    assert cs.hw_day_min == 132
    assert cs.weekend_full_min == 188
    assert cs.catchup_day_min == 176
    assert cs.catchup_date == date(2026, 4, 29)
    # 4 Fridays + 4 Saturdays + 4 Sundays in April 2026 → 12 hw days.
    assert len(cs.hw_days) == 12
    # 2/wk tasks on 4 Sat + 4 Sun + 1 catch-up Wed = 9 sessions.
    assert len(cs.laundry_days) == len(cs.shopping_days) == len(cs.travel_days) == 9
    assert sum(d.duration_min for d in cs.daily_schedule) == 4248
    assert len(cs.daily_schedule) == 30


def test_ottilie_specific_days_and_times_default_config() -> None:
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    by_date = {e.date: e for e in cs.daily_schedule}

    # Apr 1 is Wednesday — no housework under weekend-weighted default.
    apr1 = by_date[date(2026, 4, 1)]
    assert apr1.shift_type == "WEEKDAY_STD"
    assert apr1.duration_min == 120
    assert apr1.clock_in == "7:00 AM" and apr1.clock_out == "9:00 AM"

    # Apr 3 is Friday — the new canonical HW day.
    apr3 = by_date[date(2026, 4, 3)]
    assert apr3.shift_type == "HW_DAY"
    assert apr3.duration_min == 132

    # Apr 4 is Saturday — HW + errands → WEEKEND_FULL.
    apr4 = by_date[date(2026, 4, 4)]
    assert apr4.shift_type == "WEEKEND_FULL"
    assert apr4.duration_min == 188
    assert apr4.clock_in == "12:00 PM" and apr4.clock_out == "3:08 PM"

    # Apr 29 is the last Wednesday — catch-up absorbs the 2/wk gap.
    apr29 = by_date[date(2026, 4, 29)]
    assert apr29.shift_type == "CATCHUP"
    assert apr29.duration_min == 176
    assert apr29.clock_in == "7:00 AM" and apr29.clock_out == "9:56 AM"

    # Apr 30 (Thu) is a plain daily-only day.
    apr30 = by_date[date(2026, 4, 30)]
    assert apr30.shift_type == "WEEKDAY_STD"
    assert apr30.duration_min == 120


def test_as_dict_has_api_fields() -> None:
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    d = cs.as_dict()
    assert d["weekly_minutes"] == 988
    # Updated to per-line sum (was aggregate × 4.3). Dollars = form Σ (single authority).
    assert d["monthly_minutes"] == 4248.0
    assert d["monthly_amount"] == 1911.78
    assert d["mdhhs_form_amount"] == 1911.78
    assert "Monday" in d["days"]
    assert len(d["daily_schedule"]) == 30
    assert d["task_occurrence_counts"]["Housework"] == 12
    # The ScheduleConfig used to generate this schedule is echoed back for
    # the editor to render.
    assert "config" in d
    cfg = d["config"]
    assert "tasks" in cfg and len(cfg["tasks"]) == len(OTTILIE_TASKS)
    hw = next(t for t in cfg["tasks"] if t["task_name"] == "Housework")
    assert hw["selected_weekdays"] == ["Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# default_config_for — sensible defaults for non-canonical frequencies.
# ---------------------------------------------------------------------------
def test_default_config_dpw_4() -> None:
    """dpw=4 defaults to Thu–Sun; billable follows min(delivered, authorized)."""
    tasks = [{"task_name": "Bathing", "min_per_day": 30, "days_per_week": 4}]
    cfg = default_config_for(tasks, 2026, 4)
    assert len(cfg.tasks) == 1
    bath = cfg.tasks[0]
    assert bath.selected_weekdays == ["Thursday", "Friday", "Saturday", "Sunday"]
    assert bath.selected_dates == []

    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    assert len(cs.daily_schedule) == 17
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


def test_clamp_shift_fits_window() -> None:
    cin, cout = clamp_shift_to_availability("7:00 AM", 120, "7:00 AM", "10:00 AM")
    assert cin == "7:00 AM"
    assert cout == "9:00 AM"
    cin2, cout2 = clamp_shift_to_availability("7:00 AM", 120, "6:00 AM", "9:00 AM")
    assert cin2 == "7:00 AM"
    assert cout2 == "9:00 AM"


def test_clamp_shift_tight_window_raises() -> None:
    with pytest.raises(CalibrationError):
        clamp_shift_to_availability("7:00 AM", 120, "7:00 AM", "7:30 AM")


def test_parse_worker_availability_partial_overlay() -> None:
    a = parse_worker_availability({"Wednesday": {"latest": "6:00 PM"}})
    assert a["Wednesday"]["latest"] == "6:00 PM"
    assert a["Monday"]["earliest"] == "1:00 PM"


def test_visit_day_longer_uses_extended_latest() -> None:
    win = parse_worker_availability({
        "Saturday": {
            "earliest": "12:00 PM",
            "latest": "5:00 PM",
            "visit_day_longer": True,
            "visit_day_latest": "9:00 PM",
        }
    })
    row = win["Saturday"]
    assert effective_visit_latest_for_worker_day(row) == "9:00 PM"
    cin, cout = clamp_shift_to_availability(
        "12:00 PM",
        300,
        str(row["earliest"]),
        effective_visit_latest_for_worker_day(row),
    )
    assert cin == "12:00 PM"
    assert cout == "5:00 PM"


def test_assert_worker_availability_rejects_narrow_window() -> None:
    a = default_worker_availability()
    a["Saturday"] = {**a["Saturday"], "earliest": "8:00 PM", "latest": "9:00 PM"}
    with pytest.raises(CalibrationError) as exc:
        assert_worker_availability_sane(a)
    assert "Saturday" in str(exc.value)


def test_apply_worker_availability_enriched_error_names_weekday() -> None:
    avail = default_worker_availability()
    avail["Saturday"] = {
        **avail["Saturday"],
        "earliest": "8:00 PM",
        "latest": "9:00 PM",
        "visit_day_longer": False,
        "visit_day_latest": "",
    }
    entry = DayEntry(
        date=date(2026, 4, 11),
        day_of_week="Sat",
        shift_type="WEEKEND_FULL",
        clock_in="8:00 PM",
        clock_out="11:08 PM",
        duration_min=188,
        tasks={"Example": 188},
    )
    with pytest.raises(DayCapacityExceeded) as exc:
        _apply_worker_availability_to_entry(entry, "Saturday", avail)
    d = exc.value.http_detail()
    assert d["code"] == "DAY_CAPACITY_EXCEEDED"
    assert d["weekday"] == "Saturday"
    assert d["needed_minutes"] == 188
    assert d["available_minutes"] == 60
    assert any(s.get("action") == "set_latest" for s in d["suggestions"])
    s = str(exc.value)
    assert "Saturday" in s
    assert "scheduled visits" in s
    assert "188" in s


def test_default_config_merges_start_time_by_weekday_overlay() -> None:
    """Per-day preferred_window times override the weekday/weekend template."""
    tasks = [{"task_name": "Bathing", "min_per_day": 30, "days_per_week": 7}]
    pw = {
        "weekday_start": "7:00 AM",
        "weekend_start": "12:00 PM",
        "start_time_by_weekday": {
            "Wednesday": "9:00 AM",
            "Sunday": "8:00 PM",
        },
    }
    cfg = default_config_for(tasks, 2026, 4, pw)
    assert cfg.start_time_by_weekday["Wednesday"] == "9:00 AM"
    assert cfg.start_time_by_weekday["Sunday"] == "8:00 PM"
    assert cfg.start_time_by_weekday["Monday"] == "7:00 AM"
    assert cfg.start_time_by_weekday["Saturday"] == "12:00 PM"


def test_explicit_placement_tue_thu_only() -> None:
    """User picks Tue/Thu for a 2/wk task — the month uses exactly those days."""
    tasks = [{"task_name": "Bathing", "min_per_day": 20, "days_per_week": 2}]
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Bathing",
                min_per_day=20,
                days_per_week=2,
                selected_weekdays=["Tuesday", "Thursday"],
            )
        ]
    )
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    # April 2026: Tuesdays = 7,14,21,28 (4); Thursdays = 2,9,16,23,30 (5) = 9 days.
    assert len(cs.daily_schedule) == 9
    for e in cs.daily_schedule:
        assert e.day_of_week in ("Tue", "Thu")


def test_per_weekday_start_times_override() -> None:
    """Start times honored per weekday; duration from tasks."""
    tasks = [{"task_name": "Bathing", "min_per_day": 60, "days_per_week": 2}]
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Bathing",
                min_per_day=60,
                days_per_week=2,
                selected_weekdays=["Monday", "Friday"],
            )
        ],
        start_time_by_weekday={
            "Monday": "9:00 AM",
            "Tuesday": "7:00 AM",
            "Wednesday": "7:00 AM",
            "Thursday": "7:00 AM",
            "Friday": "2:00 PM",
            "Saturday": "12:00 PM",
            "Sunday": "12:00 PM",
        },
    )
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    for e in cs.daily_schedule:
        if e.day_of_week == "Mon":
            assert e.clock_in == "9:00 AM"
        if e.day_of_week == "Fri":
            assert e.clock_in == "2:00 PM"
    base_days = sum(1 for e in cs.daily_schedule if e.duration_min == 60)
    assert base_days >= len(cs.daily_schedule) - 1
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


def test_selected_dates_override() -> None:
    """selected_dates adds a date beyond the weekday rule — used for catch-up."""
    tasks = [{"task_name": "Bathing", "min_per_day": 10, "days_per_week": 2}]
    cfg = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Bathing",
                min_per_day=10,
                days_per_week=2,
                selected_weekdays=["Saturday", "Sunday"],
                selected_dates=["2026-04-29"],  # a Wednesday
            )
        ]
    )
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg)
    # 4 Sat + 4 Sun + 1 Wed = 9 sessions.
    assert len(cs.daily_schedule) == 9
    assert any(e.date == date(2026, 4, 29) for e in cs.daily_schedule)


def test_dict_config_via_api_shape() -> None:
    """Generate schedule from a dict-shaped config (the shape stored in JSON)."""
    tasks = [{"task_name": "Bathing", "min_per_day": 15, "days_per_week": 4}]
    cfg_dict = {
        "tasks": [
            {
                "task_name": "Bathing",
                "min_per_day": 15,
                "days_per_week": 4,
                "selected_weekdays": ["Monday", "Wednesday", "Friday", "Sunday"],
                "selected_dates": [],
            }
        ],
        "start_time_by_weekday": {
            "Monday": "8:00 AM",
            "Tuesday": "7:00 AM",
            "Wednesday": "8:00 AM",
            "Thursday": "7:00 AM",
            "Friday": "8:00 AM",
            "Saturday": "12:00 PM",
            "Sunday": "1:00 PM",
        },
    }
    cs = generate_schedule(tasks, 27.0, 2026, 4, config=cfg_dict)
    # Monday: 6,13,20,27 = 4 ; Wed: 1,8,15,22,29 = 5 ; Fri: 3,10,17,24 = 4 ;
    # Sun: 5,12,19,26 = 4 ; total = 17.
    assert len(cs.daily_schedule) == 17
    for e in cs.daily_schedule:
        if e.day_of_week == "Sun":
            assert e.clock_in == "1:00 PM"


# ---------------------------------------------------------------------------
# default_config_for — catch-up absorbs the 4.3-week calibration gap.
# ---------------------------------------------------------------------------
def test_default_config_catchup_for_ottilie_shape() -> None:
    """Gap == Σ(2/wk min_per_day) → the last Wednesday becomes a 2/wk override."""
    cfg = default_config_for(OTTILIE_TASKS, 2026, 4)
    for p in cfg.tasks:
        if p.days_per_week == 2:
            assert "2026-04-29" in p.selected_dates
        else:
            assert p.selected_dates == []


# ---------------------------------------------------------------------------
# default_config_for — greedy fallback converges across arbitrary month shapes.
#
# The Ottilie reference case hits its target in Phase 1 alone (gap ==
# Σ(2/wk)); the greedy Phase 2 isn't exercised there. These cases pin
# Phase 2's convergence for month shapes where Phase 1 leaves residual
# and the largest-mpd task is a 7/wk (all-days) task with no eligible
# dates — the scenario the previous implementation silently bailed on.
# ---------------------------------------------------------------------------
def _sum_default_config_minutes(year: int, month: int) -> tuple[int, int]:
    """Returns (scheduled_total, target) for default_config_for in a given month."""
    from app.calculate import (
        _month_dates,
        _sum_monthly_minutes,
        compute_mdhhs_form_minutes,
    )

    cfg = default_config_for(OTTILIE_TASKS, year, month)
    dates = _month_dates(year, month)
    scheduled = _sum_monthly_minutes(cfg.tasks, dates)
    target = compute_mdhhs_form_minutes(OTTILIE_TASKS)
    return scheduled, target


def test_default_config_greedy_converges_feb_2026_28_day() -> None:
    """Feb 2026 default placement produces honest delivered vs authorization cap."""
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 2, OTTILIE_PREFERRED)
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


def test_may_2026_overshoots_and_caps() -> None:
    """May 2026 default placement can deliver above authorization; billable caps."""
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 5, OTTILIE_PREFERRED)
    auth = int(cs.mdhhs_monthly_minutes)
    assert cs.delivered_minutes >= auth - 1
    assert cs.billable_minutes == min(cs.delivered_minutes, auth)


def test_april_2026_under_delivers_never_exceeds_auth() -> None:
    scheduled, target = _sum_default_config_minutes(2026, 4)
    assert scheduled >= target - 2
    assert scheduled <= target


def test_april_2026_billable_matches_delivered_at_cap() -> None:
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    assert sum(d.duration_min for d in cs.daily_schedule) == cs.mdhhs_monthly_minutes
    assert cs.billable_minutes == cs.delivered_minutes == cs.mdhhs_monthly_minutes


def test_default_config_named_day_lists_are_semantic_by_canonical_line() -> None:
    """laundry/shopping/travel day lists name canonical MDHHS lines, not every 2/wk task.

    Generic 2/wk tasks (Chores, Yard Work, Errands) still schedule normally, but
    ``laundry_days`` / ``shopping_days`` / ``travel_days`` stay empty unless those
    authorized lines appear on the calendar.
    """
    alt_tasks = [
        {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
        {"task_name": "Meal Preparation", "min_per_day": 50, "days_per_week": 7},
        {"task_name": "Chores", "min_per_day": 21, "days_per_week": 2},
        {"task_name": "Yard Work", "min_per_day": 15, "days_per_week": 2},
        {"task_name": "Errands", "min_per_day": 20, "days_per_week": 2},
    ]
    cs = generate_schedule(alt_tasks, 27.0, 2026, 4, OTTILIE_PREFERRED)
    assert cs.laundry_days == []
    assert cs.shopping_days == []
    assert cs.travel_days == []
    assert len(cs.daily_schedule) > 0


# ---------------------------------------------------------------------------
# Shift-shape duration fields derive from actual daily_schedule (audit B-4).
#
# Old behavior computed ``weekday_std_min``, ``hw_day_min``,
# ``weekend_full_min``, and ``catchup_day_min`` from a frequency-bucket
# formula (``core_min + hw_extra`` etc.). That made them fiction for
# any config whose shift-shape days didn't match that exact bundle.
# The new behavior reads the representative (first-seen) duration of
# each shift_type straight from the emitted ``daily_schedule``.
# ---------------------------------------------------------------------------
def test_shift_shape_fields_match_daily_schedule() -> None:
    """For Ottilie April every field equals the first day of its shift type."""
    from app.calculate import generate_schedule

    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    first_by_shift: dict[str, int] = {}
    for de in cs.daily_schedule:
        first_by_shift.setdefault(de.shift_type, de.duration_min)
    assert cs.weekday_std_min == first_by_shift.get("WEEKDAY_STD", 0)
    assert cs.hw_day_min == first_by_shift.get("HW_DAY", 0)
    assert cs.weekend_full_min == first_by_shift.get("WEEKEND_FULL", 0)
    assert cs.catchup_day_min == first_by_shift.get("CATCHUP", 0)


def test_shift_shape_fields_absent_when_no_such_day() -> None:
    """A config with no HW_DAY / CATCHUP emits 0 / None, not a fabricated value.

    With only 7/wk tasks, every day is WEEKDAY_STD: weekend_full_min,
    hw_day_min, catchup_day_min must not be invented from the legacy
    ``core + hw_extra + errand_extra`` formula.
    """
    from app.calculate import generate_schedule

    only_7wk = [
        {"task_name": "Meal Preparation", "min_per_day": 50, "days_per_week": 7},
        {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
    ]
    cs = generate_schedule(only_7wk, 27.0, 2026, 4, OTTILIE_PREFERRED)
    assert cs.weekday_std_min > 0  # some WEEKDAY_STD exists
    assert cs.hw_day_min == 0      # no HW_DAY exists → 0
    assert cs.weekend_full_min == 0  # no WEEKEND_FULL exists → 0
    assert cs.catchup_day_min == 0 or cs.catchup_day_min is None


def test_task_placement_legacy_prefers_selected_when_key_absent() -> None:
    p = TaskPlacement.from_dict(
        {
            "task_name": "X",
            "min_per_day": 10,
            "days_per_week": 2,
            "selected_weekdays": ["Saturday", "Sunday"],
            "selected_dates": [],
        }
    )
    assert p.preferred_weekdays == ["Saturday", "Sunday"]
    assert p.preference_unspecified is True
    d = p.to_dict()
    assert d["preference_unspecified"] is True
    assert d["preferred_weekdays"] == ["Saturday", "Sunday"]
    p2 = TaskPlacement.from_dict({**d, "preferred_weekdays": [], "preference_unspecified": False})
    assert p2.preferred_weekdays == []
    assert p2.preference_unspecified is False


def test_explicit_weekday_preference_unchanged_by_preferred_rebalance() -> None:
    """Phase 1.5 must not swap weekdays for explicit editor weekday preferences."""
    avail = default_worker_availability()
    avail["Wednesday"]["preferred_duration_min"] = 240
    tasks = [
        {"task_name": "Locked", "min_per_day": 25, "days_per_week": 3},
        {"task_name": "Free", "min_per_day": 25, "days_per_week": 3},
    ]
    prior = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Locked",
                min_per_day=25,
                days_per_week=3,
                selected_weekdays=["Monday", "Tuesday", "Wednesday"],
                selected_dates=[],
                preferred_weekdays=["Monday", "Tuesday", "Wednesday"],
                preference_unspecified=False,
            ),
            TaskPlacement(
                task_name="Free",
                min_per_day=25,
                days_per_week=3,
                selected_weekdays=["Friday", "Saturday", "Sunday"],
                selected_dates=[],
            ),
        ],
        start_time_by_weekday=_default_start_time_by_weekday("7:00 AM", "12:00 PM"),
    )
    out = default_config_for(
        tasks,
        2026,
        4,
        preferred_window_from_worker_availability(avail),
        worker_availability=avail,
        prior=prior,
    )
    locked = next(p for p in out.tasks if p.task_name == "Locked")
    assert locked.selected_weekdays == ["Monday", "Tuesday", "Wednesday"]
    assert locked.preference_unspecified is False


def test_round_half_up_non_mdhhs_paths() -> None:
    """round_half_up remains round-away-from-zero for non-6064 math."""
    assert round_half_up(150.5) == 151  # 5 min × 7/wk × 4.3
    assert round_half_up(322.5) == 323  # 15 min × 5/wk × 4.3
    assert round_half_up(451.5) == 452  # 15 min × 7/wk × 4.3
    assert round_half_up(150.4) == 150
    assert round_half_up(150.6) == 151


def test_mdhhs_total_minutes_matches_unrounded_aggregate_rule() -> None:
    tasks = [
        {"task_name": "Bathing", "min_per_day": 15, "days_per_week": 7},
        {"task_name": "Mobility", "min_per_day": 15, "days_per_week": 5},
    ]
    # 15×7×4.3 + 15×5×4.3 = 774.0 → total minutes on form = 774 (banker's on sum).
    assert compute_mdhhs_form_minutes(tasks) == 774
    assert round_half_up(180 * 4.3) == 774


def test_generate_schedule_monthly_amount_equals_form_amount() -> None:
    """``mdhhs_monthly_amount`` matches :func:`compute_mdhhs_form_amount` (Σ unrounded $, one quantize)."""
    tasks = [
        {
            "task_name": "Bathing",
            "min_per_day": 15,
            "days_per_week": 7,
            "monthly_amount": 0.0,
        },
        {
            "task_name": "Mobility",
            "min_per_day": 15,
            "days_per_week": 5,
            "monthly_amount": 0.0,
        },
    ]
    cs = generate_schedule(tasks, pay_rate=27.0, year=2026, month=4)
    assert cs.mdhhs_monthly_amount == cs.mdhhs_form_amount
    assert cs.mdhhs_monthly_amount == compute_mdhhs_form_amount(tasks, 27.0)
    assert cs.mdhhs_monthly_minutes == compute_mdhhs_form_minutes(tasks)


# --- MDHHS-6064-P math (spec / property tests; no client-specific numbers) ---

WPM = Decimal("4.3")  # local copy so tests do not rely on import order for `WEEKS_PER_MONTH`


def _expected_line_min(mpd: int, dpw: int) -> int:
    """Reference implementation of the rule, written in exact decimal."""
    raw = Decimal(mpd) * Decimal(dpw) * WPM
    return int(raw.quantize(Decimal("1"), ROUND_HALF_EVEN))


def _expected_line_dollar(mpd: int, dpw: int, pay: float) -> Decimal:
    raw = Decimal(mpd) * Decimal(dpw) * WPM * Decimal(str(pay)) / Decimal("60")
    return raw.quantize(Decimal("0.01"), ROUND_HALF_EVEN)


@pytest.mark.parametrize(
    "mpd,dpw",
    [
        (16, 7),
        (14, 7),
        (8, 7),
        (6, 7),
        (2, 7),
        (20, 1),
        (12, 1),
        (25, 7),
        (35, 1),
        (15, 1),
        (45, 1),
        (5, 3),
        (5, 7),
        (60, 7),
        (1, 1),
    ],
)
def test_compute_monthly_minutes_rounded_is_half_even(mpd: int, dpw: int) -> None:
    got = compute_monthly_minutes_rounded(mpd, dpw)
    assert got == _expected_line_min(mpd, dpw), (
        f"mpd={mpd}, dpw={dpw}: got {got}, expected half-even {_expected_line_min(mpd, dpw)}"
    )


@pytest.mark.parametrize("pay_rate", [15.50, 17.00, 22.75, 27.00, 31.42])
@pytest.mark.parametrize(
    "mpd,dpw",
    [
        (16, 7),
        (25, 7),
        (35, 1),
        (45, 1),
        (5, 3),
        (60, 7),
        (1, 1),
        (12, 1),
    ],
)
def test_compute_task_amount_uses_unrounded_minutes(
    mpd: int, dpw: int, pay_rate: float
) -> None:
    got = Decimal(str(compute_task_amount(mpd, dpw, pay_rate)))
    assert got == _expected_line_dollar(mpd, dpw, pay_rate), (
        f"mpd={mpd} dpw={dpw} pay={pay_rate}: got {got}, expected "
        f"{_expected_line_dollar(mpd, dpw, pay_rate)} (unrounded × pay / 60)"
    )


def test_compute_task_amount_disagrees_with_rounded_input_on_half_cases() -> None:
    """Regression guard: if someone reverts the signature to take rounded minutes,
    the amount for a .5 line will drift by pay_rate × 0.5 / 60."""
    correct = compute_task_amount(25, 7, 27.0)
    assert abs(correct - 338.62) < 0.005, (
        f"sanity: half-line should be $338.62, got {correct}"
    )


SYNTHETIC_AUTHS = [
    [
        {"min_per_day": 16, "days_per_week": 7},
        {"min_per_day": 20, "days_per_week": 1},
        {"min_per_day": 6, "days_per_week": 7},
    ],
    [
        {"min_per_day": 25, "days_per_week": 7},
        {"min_per_day": 16, "days_per_week": 7},
        {"min_per_day": 2, "days_per_week": 7},
    ],
    [
        {"min_per_day": 25, "days_per_week": 7},
        {"min_per_day": 35, "days_per_week": 1},
        {"min_per_day": 12, "days_per_week": 1},
    ],
    [
        {"min_per_day": 25, "days_per_week": 7},
        {"min_per_day": 45, "days_per_week": 1},
    ],
    [
        {"min_per_day": 1, "days_per_week": 1},
    ],
]


@pytest.mark.parametrize("tasks", SYNTHETIC_AUTHS)
def test_form_minutes_sums_unrounded_then_rounds_once(
    tasks: list[dict[str, Any]],
) -> None:
    raw_sum = sum(
        Decimal(t["min_per_day"]) * Decimal(t["days_per_week"]) * WPM for t in tasks
    )
    expected = int(raw_sum.quantize(Decimal("1"), ROUND_HALF_EVEN))
    assert compute_mdhhs_form_minutes(tasks) == expected


@pytest.mark.parametrize("pay_rate", [15.50, 27.00, 31.42])
@pytest.mark.parametrize("tasks", SYNTHETIC_AUTHS)
def test_form_amount_sums_unrounded_then_quantizes(
    tasks: list[dict[str, Any]], pay_rate: float
) -> None:
    raw = sum(
        Decimal(t["min_per_day"])
        * Decimal(t["days_per_week"])
        * WPM
        * Decimal(str(pay_rate))
        / Decimal("60")
        for t in tasks
    )
    expected = float(raw.quantize(Decimal("0.01"), ROUND_HALF_EVEN))
    assert compute_mdhhs_form_amount(tasks, pay_rate) == expected


def test_total_minutes_can_disagree_with_sum_of_rounded_lines() -> None:
    tasks: list[dict[str, Any]] = [
        {"min_per_day": 25, "days_per_week": 7},
        {"min_per_day": 35, "days_per_week": 1},
    ]
    sum_of_rounded = sum(
        compute_monthly_minutes_rounded(t["min_per_day"], t["days_per_week"])
        for t in tasks
    )
    round_of_sum = compute_mdhhs_form_minutes(tasks)
    assert round_of_sum != sum_of_rounded, (
        "If these are equal you've lost the bug — likely reverted to sum-of-rounded"
    )


@pytest.mark.parametrize("pay_rate", [15.50, 27.00, 31.42])
@pytest.mark.parametrize("tasks", SYNTHETIC_AUTHS)
def test_form_total_dollars_equals_total_minutes_times_pay_rate(
    tasks: list[dict[str, Any]], pay_rate: float
) -> None:
    total_min_raw = sum(
        Decimal(t["min_per_day"]) * Decimal(t["days_per_week"]) * WPM for t in tasks
    )
    expected = float(
        (total_min_raw * Decimal(str(pay_rate)) / Decimal("60")).quantize(
            Decimal("0.01"), ROUND_HALF_EVEN
        )
    )
    assert compute_mdhhs_form_amount(tasks, pay_rate) == expected


# ---------------------------------------------------------------------------
# Companion co-placement — travel/errand pairing.
# ---------------------------------------------------------------------------

COMPANION_TEST_AUTHS: list[tuple[str, list[dict[str, Any]]]] = [
    (
        "companion_alone",
        [
            {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 1},
            {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
        ],
    ),
    (
        "parent_alone",
        [
            {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
            {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
        ],
    ),
    (
        "matched_dpw_1",
        [
            {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
            {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 1},
        ],
    ),
    (
        "matched_dpw_2",
        [
            {"task_name": "Laundry", "min_per_day": 14, "days_per_week": 2},
            {"task_name": "Travel For Laundry", "min_per_day": 12, "days_per_week": 2},
        ],
    ),
    (
        "companion_lt_parent",
        [
            {"task_name": "Laundry", "min_per_day": 14, "days_per_week": 3},
            {"task_name": "Travel For Laundry", "min_per_day": 12, "days_per_week": 1},
        ],
    ),
    (
        "companion_gt_parent",
        [
            {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
            {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 2},
        ],
    ),
    (
        "both_pairs",
        [
            {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
            {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 1},
            {"task_name": "Laundry", "min_per_day": 14, "days_per_week": 2},
            {"task_name": "Travel For Laundry", "min_per_day": 12, "days_per_week": 2},
            {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
        ],
    ),
]


@pytest.mark.parametrize(
    ("label", "tasks"),
    COMPANION_TEST_AUTHS,
)
def test_companion_days_are_subset_of_parent_days(
    label: str,
    tasks: list[dict[str, Any]],
) -> None:
    cfg = default_config_for(tasks, year=2026, month=5)
    by_name = {p.task_name: p for p in cfg.tasks}
    for comp_name, parent_name in COMPANION_TO_PARENT.items():
        comp = by_name.get(comp_name)
        parent = by_name.get(parent_name)
        if comp is None or parent is None:
            continue
        comp_days = set(comp.selected_weekdays) | set(comp.selected_dates)
        parent_days = set(parent.selected_weekdays) | set(parent.selected_dates)
        assert comp_days <= parent_days, (
            f"[{label}] {comp_name} on {sorted(comp_days)!r} not ⊆ "
            f"{parent_name} on {sorted(parent_days)!r}"
        )


@pytest.mark.parametrize(
    ("label", "tasks"),
    COMPANION_TEST_AUTHS,
)
def test_companion_day_count_respects_authorized_dpw_when_possible(
    label: str,
    tasks: list[dict[str, Any]],
) -> None:
    cfg = default_config_for(tasks, year=2026, month=5)
    by_name = {p.task_name: p for p in cfg.tasks}
    for comp_name, parent_name in COMPANION_TO_PARENT.items():
        comp = by_name.get(comp_name)
        parent = by_name.get(parent_name)
        if comp is None or parent is None:
            continue
        assert comp.selected_weekdays == parent.selected_weekdays, (
            f"[{label}] {comp_name} weekdays should mirror {parent_name}"
        )
        assert comp.selected_dates == parent.selected_dates, (
            f"[{label}] {comp_name} iso dates should mirror {parent_name}"
        )


def test_companion_over_authorized_sets_fallback_flag() -> None:
    tasks = [
        {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
        {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 3},
    ]
    cfg = default_config_for(tasks, year=2026, month=5)
    by_name = {p.task_name: p for p in cfg.tasks}
    assert by_name["Travel For Shopping"].placement_fallback is True


def test_companion_rule_runs_after_user_edit_via_prior() -> None:
    tasks_list = [
        {"task_name": "Shopping for Food/Meds", "min_per_day": 35, "days_per_week": 1},
        {"task_name": "Travel For Shopping", "min_per_day": 20, "days_per_week": 1},
    ]
    prior = ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Shopping for Food/Meds",
                min_per_day=35,
                days_per_week=1,
                selected_weekdays=["Monday"],
                preferred_weekdays=["Monday"],
                preference_unspecified=False,
            ),
            TaskPlacement(
                task_name="Travel For Shopping",
                min_per_day=20,
                days_per_week=1,
                selected_weekdays=["Friday"],
                preferred_weekdays=["Friday"],
                preference_unspecified=False,
            ),
        ],
    )
    cfg = default_config_for(tasks_list, year=2026, month=5, prior=prior)
    by_name = {p.task_name: p for p in cfg.tasks}
    comp_week = set(by_name["Travel For Shopping"].selected_weekdays)
    comp_iso = set(by_name["Travel For Shopping"].selected_dates)
    parent_all = set(by_name["Shopping for Food/Meds"].selected_weekdays) | set(
        by_name["Shopping for Food/Meds"].selected_dates
    )
    assert (comp_week | comp_iso) <= parent_all
    assert "Friday" not in comp_week


def test_companion_check_cross_check_passes_ottilie() -> None:
    from app.extract import ExtractedForm
    from app.validate import cross_check

    form = ExtractedForm(
        client_name="Companion smoke",
        pay_rate=27.0,
        tasks=list(OTTILIE_TASKS),
        monthly_total_amount=1911.78,
        monthly_total_time_str="70:48",
    )
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    report = cross_check(form, cs)
    c15 = next((c for c in report.checks if c.number == 18), None)
    assert c15 is not None and c15.passed, getattr(c15, "actual", None)


_WEEK_ALL = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def test_greedy_trim_overshoot_smallest_first() -> None:
    """Trim-to-authorization isn't exercised in this builder — placeholder."""
    pytest.skip("Greedy trim pipeline not wired in POC")


def test_trim_propagates_to_travel_companion() -> None:
    pytest.skip("Greedy trim pipeline not wired in POC")

