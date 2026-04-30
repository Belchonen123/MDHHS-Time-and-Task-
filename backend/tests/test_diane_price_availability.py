"""Diane Price–style mix: default_config respects per-day availability caps."""

from __future__ import annotations

import copy

from app.calculate import (
    WEEKS_PER_MONTH,
    compute_weekly_budget,
    default_config_for,
    default_worker_availability,
    generate_schedule,
    preferred_window_from_worker_availability,
    weekday_capacity_minutes,
    _WEEK,
)

DIANE_TASKS: list[dict] = [
    {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7},
    {"task_name": "Dressing", "min_per_day": 14, "days_per_week": 7},
    {"task_name": "Grooming", "min_per_day": 8, "days_per_week": 3},
    {"task_name": "Housework", "min_per_day": 12, "days_per_week": 7},
    {"task_name": "Laundry", "min_per_day": 30, "days_per_week": 2},
    {"task_name": "Travel-Laundry", "min_per_day": 4, "days_per_week": 2},
    {"task_name": "Meal-Prep", "min_per_day": 40, "days_per_week": 7},
    {"task_name": "Shopping", "min_per_day": 30, "days_per_week": 2},
    {"task_name": "Travel-Shopping", "min_per_day": 18, "days_per_week": 2},
]


def _weekday_template_load(cfg_tasks) -> dict[str, int]:
    load = {d: 0 for d in _WEEK}
    for p in cfg_tasks:
        for d in p.selected_weekdays:
            load[d] += int(p.min_per_day)
    return load


def test_diane_price_default_availability_capacity_and_april_minutes() -> None:
    avail = default_worker_availability()
    pw = preferred_window_from_worker_availability(avail)
    cfg = default_config_for(
        DIANE_TASKS, 2026, 4, pw, worker_availability=avail
    )
    cap = {d: weekday_capacity_minutes(avail, d) for d in _WEEK}
    load = _weekday_template_load(cfg.tasks)
    for d in _WEEK:
        assert load[d] <= cap[d], (
            f"{d}: template load {load[d]} exceeds capacity {cap[d]}"
        )

    pay = 25.0
    cal = generate_schedule(
        DIANE_TASKS,
        pay,
        2026,
        4,
        pw,
        config=cfg,
        worker_availability=avail,
    )
    weekly_budget = compute_weekly_budget(DIANE_TASKS)
    target = round(weekly_budget * WEEKS_PER_MONTH)
    actual = sum(e.duration_min for e in cal.daily_schedule)
    max_mpd = max(int(t["min_per_day"]) for t in DIANE_TASKS)
    assert abs(actual - target) <= max_mpd


def test_diane_price_narrow_sunday_moves_twos_off_sunday() -> None:
    avail = copy.deepcopy(default_worker_availability())
    avail["Sunday"] = {
        "earliest": "12:00 PM",
        "latest": "2:00 PM",
        "visit_day_longer": False,
        "visit_day_latest": "",
    }
    pw = preferred_window_from_worker_availability(avail)
    cfg = default_config_for(
        DIANE_TASKS, 2026, 4, pw, worker_availability=avail
    )
    twos_names = {"Laundry", "Shopping", "Travel-Laundry", "Travel-Shopping"}
    for p in cfg.tasks:
        if p.task_name in twos_names:
            assert "Sunday" not in p.selected_weekdays, (
                f"{p.task_name} should not use Sunday under 2h Sunday cap"
            )

    load = _weekday_template_load(cfg.tasks)
    assert load["Sunday"] <= weekday_capacity_minutes(avail, "Sunday")

    cal = generate_schedule(
        DIANE_TASKS,
        25.0,
        2026,
        4,
        pw,
        config=cfg,
        worker_availability=avail,
    )
    assert len(cal.daily_schedule) == 30


def _mean_duration_on_dow(cal, dow: str) -> float:
    mins = [e.duration_min for e in cal.daily_schedule if e.day_of_week == dow]
    return sum(mins) / len(mins) if mins else 0.0


def _legacy_asymmetric_avail_for_pref_test():
    """7 AM / 12 PM Mon–Sat/Sun shapes headroom differently (see preferred_duration tuning)."""
    avail: dict = {}
    for d in _WEEK:
        e = "12:00 PM" if d in ("Saturday", "Sunday") else "7:00 AM"
        avail[d] = {
            "earliest": e,
            "latest": "8:00 PM",
            "visit_day_longer": False,
            "visit_day_latest": "",
            "preferred_duration_min": None,
        }
    return avail


def test_diane_price_wednesday_preferred_shift_rebalances_load() -> None:
    """Wednesday preferred_duration_min pulls more minutes onto Wed vs baseline; monthly total stable."""
    avail_base = _legacy_asymmetric_avail_for_pref_test()
    avail_pref = copy.deepcopy(avail_base)
    avail_pref["Wednesday"] = {
        **avail_pref["Wednesday"],
        "preferred_duration_min": 180,
    }
    pw_base = preferred_window_from_worker_availability(avail_base)
    pw_pref = preferred_window_from_worker_availability(avail_pref)

    cfg_base = default_config_for(
        DIANE_TASKS, 2026, 4, pw_base, worker_availability=avail_base
    )
    cfg_pref = default_config_for(
        DIANE_TASKS, 2026, 4, pw_pref, worker_availability=avail_pref
    )

    cal_base = generate_schedule(
        DIANE_TASKS,
        25.0,
        2026,
        4,
        pw_base,
        config=cfg_base,
        worker_availability=avail_base,
    )
    cal_pref = generate_schedule(
        DIANE_TASKS,
        25.0,
        2026,
        4,
        pw_pref,
        config=cfg_pref,
        worker_availability=avail_pref,
    )

    wed_base = _mean_duration_on_dow(cal_base, "Wed")
    wed_pref = _mean_duration_on_dow(cal_pref, "Wed")
    # Strict monthly trim (``excluded_dates``) tightens capacity slack; a +3 pt
    # delta still proves Wednesday ``preferred_duration_min`` steers load.
    assert wed_pref >= wed_base + 3, (
        f"expected Wednesday mean duration to rise materially: {wed_base} -> {wed_pref}"
    )

    thu_base = _mean_duration_on_dow(cal_base, "Thu")
    thu_pref = _mean_duration_on_dow(cal_pref, "Thu")
    assert thu_pref < thu_base - 2, (
        f"expected Thursday mean to drop: {thu_base} -> {thu_pref}"
    )

    weekly_budget = compute_weekly_budget(DIANE_TASKS)
    target = round(weekly_budget * WEEKS_PER_MONTH)
    actual = sum(e.duration_min for e in cal_pref.daily_schedule)
    max_mpd = max(int(t["min_per_day"]) for t in DIANE_TASKS)
    assert abs(actual - target) <= max_mpd
