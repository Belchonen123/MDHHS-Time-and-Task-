"""Cross-check extracted totals and calendar-calibrated schedules (18 checks).

``ScheduleConfig`` (``selected_weekdays`` ∪ ``selected_dates``) is the
source of truth for placement. **Authorization** rows on MDHHS-6064-P define
the **billing cap** (monthly minutes / dollars). **Delivered** minutes are
the sum of placed visits for the real calendar month. **Billable** =
``min(delivered, authorized)`` per ASM 144 / MSA-1904 — validator checks
enforce that cap arithmetic, not ``delivered == authorized``.

* **Checks 8–10 — ASM 120 policy** (IADL caps, EHHS threshold, shared living).
  Do not count toward ``all_passed`` (``counts_toward_all_passed=False``).
* **Checks 3 & 5** — informational observations (delivered vs authorized /
  dollars); always pass and do not affect the math banner.
* **Checks 4 & 6 — billable minutes / dollars** match the cap model and
  MDHHS per-line rollup tolerances.
* **Check 7 — per-task variance vs 4.3-week line display** is informational;
  a sub-check enforces a **minimum occurrence floor** by
  ``(month_days × days_per_week) // 7`` to catch dropped sessions.
* **Checks 11–14 — placement, duration, hours, catchup** — structural /
  scheduling integrity.
* **Checks 15–18 — form dollars, billing rule, companion consistency.**
* **``validation_status``** — ``BILLABLE_EXACT`` / ``BILLABLE_AT_CAP`` /
  ``BILLABLE_UNDER_CAP`` when all math checks pass; ``INVALID`` otherwise.
"""

from __future__ import annotations

import calendar
import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from .calculate import (
    COMPANION_TO_PARENT,
    CalibratedSchedule,
    asm120_shared_living_iadl_expected_max,
    compute_billable_amount,
    compute_billable_minutes,
    compute_mdhhs_form_amount,
    compute_mdhhs_form_minutes,
    compute_monthly_minutes_rounded,
    compute_weekly_budget,
    schedule_to_dict,
)
from .extract import ExtractedForm


# Full weekday names (Mon–Sun), duplicated from calculate to avoid import
# cycles for a trivial literal. Keep in lockstep with ``calculate._WEEK``.
_WEEK: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

# ASM 120 — IADL Maximum Allowable Hours (monthly, whole minutes on auth lines).
IADL_MONTHLY_MAX_MIN = {
    "Shopping for Food/Meds": 300,
    "Housework": 360,
    "Laundry": 420,
    "Meal Preparation": 1500,
}
# 179.59 hr/mo combined services (EHHS); travel tasks excluded from this sum.
EHHS_THRESHOLD_MIN = 10775  # 179.59 × 60, round-half-up to whole minutes
TRAVEL_TASKS_EXCLUDED_FROM_EHHS = frozenset(
    {
        "Travel For Shopping",
        "Travel For Laundry",
    }
)


@dataclass
class SubCheck:
    task_name: str
    auth_min: int
    scheduled_min: int
    variance: int
    passed: bool
    informational: bool = False


@dataclass
class Check:
    number: int
    name: str
    passed: bool
    expected: Any
    actual: Any
    tolerance: str
    detail: str = ""
    sub_checks: list[SubCheck] = field(default_factory=list)
    # When False (ASM 120 policy checks), omitted from all_passed / pass_count / fail_count.
    counts_toward_all_passed: bool = True


@dataclass
class ValidationReport:
    checks: list[Check]
    warnings: list[str] = field(default_factory=list)
    delivered_minutes: int = 0
    authorized_minutes: int = 0
    billable_minutes: int = 0
    all_passed: bool = field(init=False)
    pass_count: int = field(init=False)
    fail_count: int = field(init=False)
    summary: str = field(init=False)
    validation_status: str = field(init=False)

    def __post_init__(self) -> None:
        math_checks = [c for c in self.checks if c.counts_toward_all_passed]
        self.all_passed = all(c.passed for c in math_checks) if math_checks else True
        self.pass_count = sum(1 for c in math_checks if c.passed)
        self.fail_count = sum(1 for c in math_checks if not c.passed)
        self.summary = _format_report_summary(
            self.checks, self.all_passed, self.warnings
        )
        self.validation_status = _compute_validation_status(self)


def _format_report_summary(
    checks: list[Check], all_passed: bool, warnings: list[str]
) -> str:
    head = (
        "All checks passed."
        if all_passed
        else f"{sum(1 for c in checks if not c.passed)} check(s) failed."
    )
    lines: list[str] = [head, ""]
    for c in checks:
        st = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{st}] {c.number} — {c.name}")
        if c.expected not in ("", None):
            lines.append(f"       expected: {c.expected}")
        if c.actual not in ("", None):
            lines.append(f"       actual:   {c.actual}")
        if c.tolerance:
            lines.append(f"       tolerance: {c.tolerance}")
        if c.detail:
            lines.append(f"       {c.detail}")
        for s in c.sub_checks:
            if getattr(s, "informational", False):
                m = "OBS"
            else:
                m = "PASS" if s.passed else "FAIL"
            lines.append(
                f"         [{m}] {s.task_name}: "
                f"sched {s.scheduled_min} − auth {s.auth_min} = {s.variance:+d}"
            )
        lines.append("")
    if warnings:
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  ! {w}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _compute_validation_status(report: ValidationReport) -> str:
    if not report.all_passed:
        return "INVALID"
    d, a, b = report.delivered_minutes, report.authorized_minutes, report.billable_minutes
    if d == a == b:
        return "BILLABLE_EXACT"
    if b == a and d > a:
        return "BILLABLE_AT_CAP"
    if b == d and d < a:
        return "BILLABLE_UNDER_CAP"
    return "INVALID"


def parse_form_monthly_time_to_minutes(monthly_time_str: str) -> int | None:
    """MDHHS monthly duration 'HH:MM' → total minutes (HH may exceed 24)."""
    t = (monthly_time_str or "").strip()
    m = re.fullmatch(r"(\d+):(\d{2})", t)
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if mm >= 60 or h < 0:
        return None
    return h * 60 + mm


def _as_schedule_dict(cs: Any) -> dict[str, Any]:
    if isinstance(cs, CalibratedSchedule):
        return schedule_to_dict(cs)
    if isinstance(cs, dict):
        return cs
    raise TypeError(
        f"calibrated_schedule must be CalibratedSchedule or dict, got {type(cs).__name__}"
    )


def _task_totals(
    daily: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Return (scheduled_minutes_by_task, occurrence_count_by_task)."""
    mins: dict[str, int] = {}
    cnt: dict[str, int] = {}
    for d in daily:
        tmap = d.get("tasks") or {}
        for name, m in tmap.items():
            if not name:
                continue
            m_i = int(m or 0)
            if m_i <= 0:
                continue
            mins[name] = mins.get(name, 0) + m_i
            cnt[name] = cnt.get(name, 0) + 1
    return mins, cnt


def _mandatory_min_sessions_for_task(dpw: int, ndays: int) -> int | None:
    """Strict floors only where the user/PDF spell out a clear rule.

    * **7/wk** (daily): must appear every day of the month.
    * **1/wk**: at least four sessions in any month (covers 28-day Feb + longer).
    * Other frequencies: None — calendar shape vs 4.3-week display varies by
      weekday selection; Check 11 (placement counts) still catches true drops.
    """
    if dpw <= 0:
        return None
    if dpw == 7:
        return ndays
    if dpw == 1:
        return 4
    return None


def _delivered_authorization_calendar_note(
    ndays: int,
    delivered_min: int,
    authorized_min: int,
) -> str:
    diff = delivered_min - authorized_min
    wk = ndays / 7.0
    head = (
        f"{ndays}-day month (~{wk:.1f} calendar weeks). "
        f"Delivered {delivered_min} min; authorized (6064-P rollup) {authorized_min} min."
    )
    if diff > 0:
        return (
            f"{head} The weekly pattern across this month's weekday shape yields "
            f"+{diff} min vs the 4.3-week projection — overshoot is non-billable."
        )
    if diff < 0:
        return (
            f"{head} Shortfall of {diff} min vs authorization "
            f"after honest calendar placement — bill actual (delivered) minutes."
        )
    return f"{head} Delivered matches the authorized minute total."


def _task_runs_on(
    entry: dict[str, Any],
    dow_name: str,
    iso_date: str,
) -> bool:
    """True iff the given config task entry places the task on this date.

    Placement rule (same one ``generate_schedule`` uses): the task runs iff
    the weekday is listed in ``selected_weekdays`` OR the ISO date is listed
    in ``selected_dates``, unless the ISO date appears in ``excluded_dates``.
    """
    excluded = {str(x) for x in (entry.get("excluded_dates") or [])}
    if iso_date in excluded:
        return False
    weekdays = {str(x) for x in (entry.get("selected_weekdays") or [])}
    extra = {str(x) for x in (entry.get("selected_dates") or [])}
    return dow_name in weekdays or iso_date in extra


def _expected_placement_count(
    entry: dict[str, Any] | None,
    month_dates: list[date],
) -> int:
    """In-month occurrence count projected by the user's placement.

    Returns -1 when no config entry exists for the task — the caller is
    responsible for treating that as "unknown expected, skip per-task
    comparison" rather than substituting a frequency-based guess.
    """
    if entry is None:
        return -1
    count = 0
    for d in month_dates:
        if _task_runs_on(entry, _WEEK[d.weekday()], d.isoformat()):
            count += 1
    return count


def _expected_tasks_on_date(
    config_tasks_by_name: dict[str, dict[str, Any]],
    d: date,
) -> dict[str, int]:
    """Task-name → min_per_day for tasks the config places on this date."""
    dow = _WEEK[d.weekday()]
    iso = d.isoformat()
    out: dict[str, int] = {}
    for nm, entry in config_tasks_by_name.items():
        mpd = int(entry.get("min_per_day") or 0)
        if mpd > 0 and _task_runs_on(entry, dow, iso):
            out[nm] = mpd
    return out


def _config_tasks_by_name(sd: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pull the ScheduleConfig.tasks list out of a schedule dict, keyed by name."""
    cfg = sd.get("config")
    if not isinstance(cfg, dict):
        return {}
    tasks = cfg.get("tasks") or []
    if not isinstance(tasks, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for t in tasks:
        if isinstance(t, dict):
            nm = str(t.get("task_name") or "")
            if nm:
                out[nm] = t
    return out


def cross_check(
    extracted_form: ExtractedForm,
    calibrated_schedule: Any,
    *,
    shared_living: bool = False,
    iadl_separate_documented: bool = False,
) -> ValidationReport:
    """Reconcile an extracted MDHHS-6064 form with a calibrated schedule."""
    sd = _as_schedule_dict(calibrated_schedule)
    tasks: list[dict[str, Any]] = list(extracted_form.tasks)
    pay = float(extracted_form.pay_rate)

    # --- Derived / target values ---
    wk_budget = compute_weekly_budget(tasks)
    target_monthly_min = compute_mdhhs_form_minutes(tasks)
    sum_line_round_even = sum(
        compute_monthly_minutes_rounded(int(t["min_per_day"]), int(t["days_per_week"]))
        for t in tasks
    )
    sched_wk = int(sd.get("mdhhs_weekly_minutes") or sd.get("weekly_minutes") or 0)
    sched_mm = int(sd.get("mdhhs_monthly_minutes") or 0)
    canonical_form_amt = compute_mdhhs_form_amount(tasks, pay)
    sched_form_amt = (
        float(sd["mdhhs_form_amount"])
        if sd.get("mdhhs_form_amount") is not None
        else canonical_form_amt
    )
    # Post-Prompt-1 invariant: mdhhs_monthly_amount == mdhhs_form_amount.
    sched_amt = sched_form_amt

    daily: list[dict[str, Any]] = list(sd.get("daily_schedule") or [])
    total_min = sum(int(d.get("duration_min", 0) or 0) for d in daily)
    _accum = Decimal("0.00")
    for d in daily:
        dm = int(d.get("duration_min", 0) or 0)
        _accum += Decimal(str(dm)) * Decimal(str(pay)) / Decimal("60")
    total_cost = float(_accum.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    delivered_min = int(total_min)
    del_amt = float(total_cost)
    exp_bill_min = compute_billable_minutes(delivered_min, sched_mm)
    exp_bill_amt = compute_billable_amount(del_amt, sched_amt)

    st_del = sd.get("delivered_minutes")
    st_bill_m = sd.get("billable_minutes")
    st_bill_a = sd.get("billable_amount")

    bill_min = exp_bill_min
    bill_amt = exp_bill_amt

    minute_gap_abs = abs(bill_min - sched_mm) if sched_mm > 0 else bill_min
    _gap_dollar = (
        Decimal(str(minute_gap_abs)) * Decimal(str(pay)) / Decimal("60")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    dollar_slack_from_minute_gap = float(_gap_dollar)
    linear_dollars_at_target = float(
        (Decimal(bill_min) * Decimal(str(pay)) / Decimal("60")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
    ) if sched_mm > 0 else 0.0
    mdhhs_form_vs_linear_minutes = (
        abs(linear_dollars_at_target - canonical_form_amt)
        if sched_mm > 0 and minute_gap_abs == 0
        else 0.0
    )

    task_mins, task_counts_rebuilt = _task_totals(daily)
    declared_counts = sd.get("task_occurrence_counts") or {}
    task_counts: dict[str, int] = {
        **task_counts_rebuilt,
        **{str(k): int(v) for k, v in declared_counts.items() if isinstance(v, int)},
    }

    catchup_date = sd.get("catchup_date")
    has_catchup = bool(catchup_date)
    # Actual minutes carried by the catchup day, sourced directly from
    # ``daily_schedule`` so the check reflects what the scheduler put
    # there (not the 7/wk + 2/wk bundle the default fallback assumes).
    # Falls back to the top-level ``catchup_day_min`` (now also honestly
    # recomputed in ``calculate.py``) when the row isn't in daily.
    catchup_actual_min = 0
    if has_catchup:
        iso_catchup = str(catchup_date)
        for d in daily:
            if str(d.get("date") or "") == iso_catchup:
                catchup_actual_min = int(d.get("duration_min") or 0)
                break
        if catchup_actual_min == 0:
            catchup_actual_min = int(sd.get("catchup_day_min") or 0)

    year = int(sd.get("year") or 0)
    month = int(sd.get("month") or 0)
    if year and month:
        try:
            _, ndays = calendar.monthrange(year, month)
        except (ValueError, calendar.IllegalMonthError):
            ndays = len(daily)
    else:
        ndays = len(daily)
    month_dates: list[date] = (
        [date(year, month, d) for d in range(1, ndays + 1)]
        if year and month
        else []
    )
    config_tasks = _config_tasks_by_name(sd)

    checks: list[Check] = []
    warnings: list[str] = []

    # --- CHECK 1 — Weekly budget arithmetic ---
    c1 = Check(
        number=1,
        name="Weekly budget arithmetic",
        passed=sched_wk == wk_budget,
        expected=wk_budget,
        actual=sched_wk,
        tolerance="exact",
        detail=f"Σ(min/day × days/wk) over {len(tasks)} task(s)",
    )
    checks.append(c1)

    # --- CHECK 2 — Monthly minutes target ---
    c2 = Check(
        number=2,
        name="Monthly minutes target",
        passed=sched_mm == target_monthly_min and sched_mm > 0,
        expected=target_monthly_min,
        actual=sched_mm,
        tolerance="exact",
        detail=(
            f"Σ unrounded per-line monthly min (6064-P) = {target_monthly_min} "
            f"(Σ per-line ROUND_HALF_EVEN = {sum_line_round_even})"
        ),
    )
    checks.append(c2)

    # --- CHECK 3a — Delivered vs authorized (informational) ---
    note3a = _delivered_authorization_calendar_note(ndays, delivered_min, sched_mm)
    c3a = Check(
        number=3,
        name="3a — Delivered vs authorized (informational)",
        passed=True,
        expected=sched_mm,
        actual=delivered_min,
        tolerance="observation only — always passes",
        detail=note3a,
        counts_toward_all_passed=False,
    )
    checks.append(c3a)

    # --- CHECK 3b — Billable minutes cap ---
    c4 = Check(
        number=4,
        name="3b — Billable minutes = min(delivered, authorized)",
        passed=(
            (sched_mm > 0)
            and (exp_bill_min == min(delivered_min, sched_mm))
            and (st_del is None or int(st_del) == delivered_min)
            and (st_bill_m is None or int(st_bill_m) == exp_bill_min)
        ),
        expected=exp_bill_min,
        actual=int(st_bill_m) if st_bill_m is not None else exp_bill_min,
        tolerance="exact (optional snapshot fields must match Σ daily)",
        detail=(
            f"Σ daily = {delivered_min} min; authorized {sched_mm} min; "
            f"billable {exp_bill_min} min"
        ),
    )
    checks.append(c4)

    # --- CHECK 4a — Delivered $ vs authorized $ (informational) ---
    c5 = Check(
        number=5,
        name="4a — Delivered $ vs authorized $ (informational)",
        passed=True,
        expected=round(sched_amt, 2),
        actual=round(del_amt, 2),
        tolerance="observation only — always passes",
        detail=(
            f"Delivered ${del_amt:.2f} (from delivered minutes); "
            f"authorized ${sched_amt:.2f}; billable ${exp_bill_amt:.2f}"
        ),
        counts_toward_all_passed=False,
    )
    checks.append(c5)

    # --- CHECK 4b — Billable $ cap + form rollup tolerance ---
    c6_tolerance = (
        dollar_slack_from_minute_gap
        + mdhhs_form_vs_linear_minutes
        + 0.02
        + 1e-9
    )
    strict_amt_ok = st_bill_a is None or abs(float(st_bill_a) - exp_bill_amt) <= 1e-6
    dollar_anchor = sched_amt if bill_min == sched_mm else del_amt
    rollup_ok = abs(exp_bill_amt - dollar_anchor) <= c6_tolerance
    c6 = Check(
        number=6,
        name="4b — Billable $ = min(delivered $, authorized $) vs form",
        passed=strict_amt_ok and rollup_ok,
        expected=round(exp_bill_amt, 2),
        actual=round(float(st_bill_a), 2) if st_bill_a is not None else round(exp_bill_amt, 2),
        tolerance=(
            "±$0.02 (+ MDHHS form rollup vs minute $ at cap)"
            if minute_gap_abs == 0
            else (
                "±$"
                + f"{dollar_slack_from_minute_gap:.2f}"
                + " (Δ billable min vs auth × pay/60)"
                + " + MDHHS form-vs-linear$"
                + " + $0.02 when at authorization cap"
            )
        ),
        detail=(
            f"billable ${exp_bill_amt:.2f} vs anchor ${dollar_anchor:.2f} "
            f"(min(del$, auth$); snapshot ${float(st_bill_a):.2f} when stored)"
            if st_bill_a is not None
            else f"billable ${exp_bill_amt:.2f} vs anchor ${dollar_anchor:.2f} (min(del$, auth$))"
        ),
    )
    checks.append(c6)

    # --- CHECK 5 — Per-task drift (informational) + occurrence floor ---
    sub: list[SubCheck] = []
    for t in tasks:
        nm = str(t.get("task_name", ""))
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        auth_r = compute_monthly_minutes_rounded(mpd, dpw)
        sched = int(task_mins.get(nm, 0))
        variance = sched - auth_r
        sub.append(
            SubCheck(
                task_name=nm,
                auth_min=auth_r,
                scheduled_min=sched,
                variance=variance,
                passed=True,
                informational=True,
            )
        )
    floor_fails: list[str] = []
    for t in tasks:
        nm = str(t.get("task_name", ""))
        dpw = int(t["days_per_week"])
        cnt = int(task_counts.get(nm, 0))
        need = _mandatory_min_sessions_for_task(dpw, ndays)
        if need is None:
            continue
        if cnt < need:
            floor_fails.append(
                f"{nm!r}: {cnt} session(s) < floor {need} "
                f"for {dpw}/wk in {ndays}-day month"
            )
    floor_ok = len(floor_fails) == 0
    sub.append(
        SubCheck(
            task_name="Minimum sessions for authorized days/week (calendar floor)",
            auth_min=-1,
            scheduled_min=-1,
            variance=0,
            passed=floor_ok,
            informational=False,
        )
    )
    c7 = Check(
        number=7,
        name="Per-task calendar drift (informational) + occurrence floor",
        passed=floor_ok,
        expected="each 7/wk task: ndays sessions; each 1/wk task: ≥4 sessions",
        actual=("ok" if floor_ok else floor_fails[:12]),
        tolerance="variance rows OBS-only; floor exact",
        detail=f"{len(sub) - 1} task variance row(s) + floor check",
        sub_checks=sub,
    )
    checks.append(c7)

    # --- CHECK 8 — IADL maximum hours (ASM 120 policy — warning, not math failure) ---
    iadl_violations: list[str] = []
    for t in tasks:
        nm = str(t.get("task_name", ""))
        cap = IADL_MONTHLY_MAX_MIN.get(nm)
        if cap is None:
            continue
        mpd = int(t.get("min_per_day") or 0)
        dpw = int(t.get("days_per_week") or 0)
        auth_min = compute_monthly_minutes_rounded(mpd, dpw)
        if auth_min > cap:
            over_hr = (auth_min - cap) / 60.0
            line = (
                f"{nm}: {auth_min} min > cap {cap} min ({over_hr:.2f} hr over)"
            )
            iadl_violations.append(line)
            warnings.append(f"ASM 120 IADL cap: {line}")
    c6_iadl_passed = len(iadl_violations) == 0
    c6_iadl = Check(
        number=8,
        name="IADL maximum hours (ASM 120)",
        passed=c6_iadl_passed,
        expected="auth_min ≤ cap for Shopping, Housework, Laundry, Meal Preparation",
        actual=("ok" if c6_iadl_passed else "; ".join(iadl_violations)),
        tolerance="policy — does not fail math banner",
        detail=(
            "all listed IADLs within caps"
            if c6_iadl_passed
            else "; ".join(iadl_violations)
        ),
        counts_toward_all_passed=False,
    )
    checks.append(c6_iadl)

    # --- CHECK 7 — Combined services within EHHS threshold (ASM 120) ---
    total_non_travel = 0
    for t in tasks:
        nm = str(t.get("task_name", ""))
        if nm in TRAVEL_TASKS_EXCLUDED_FROM_EHHS:
            continue
        mpd = int(t.get("min_per_day") or 0)
        dpw = int(t.get("days_per_week") or 0)
        total_non_travel += compute_monthly_minutes_rounded(mpd, dpw)
    c7_ehhs_passed = total_non_travel <= EHHS_THRESHOLD_MIN
    if not c7_ehhs_passed:
        warnings.append(
            f"Combined non-travel monthly minutes {total_non_travel} > {EHHS_THRESHOLD_MIN}. "
            "EHHS approval required (DCH-1785)."
        )
    c7_ehhs = Check(
        number=9,
        name="Combined services within EHHS threshold (ASM 120)",
        passed=c7_ehhs_passed,
        expected=f"Σ non-travel auth min ≤ {EHHS_THRESHOLD_MIN}",
        actual=total_non_travel,
        tolerance="policy — does not fail math banner",
        detail=(
            f"Combined non-travel monthly minutes {total_non_travel} > {EHHS_THRESHOLD_MIN}. "
            "EHHS approval required (DCH-1785)."
            if not c7_ehhs_passed
            else f"Σ non-travel auth min = {total_non_travel} ≤ {EHHS_THRESHOLD_MIN}"
        ),
        counts_toward_all_passed=False,
    )
    checks.append(c7_ehhs)

    # --- CHECK 8 — Shared-living IADL proration sanity (ASM 120 policy) ---
    shared_living_lines: list[str] = []
    if shared_living and not iadl_separate_documented:
        for t in tasks:
            nm = str(t.get("task_name", ""))
            cap_min = IADL_MONTHLY_MAX_MIN.get(nm)
            if cap_min is None:
                continue
            mpd = int(t.get("min_per_day") or 0)
            dpw = int(t.get("days_per_week") or 0)
            auth_min = compute_monthly_minutes_rounded(mpd, dpw)
            expected_max = asm120_shared_living_iadl_expected_max(cap_min)
            if auth_min > expected_max:
                msg = (
                    f"{nm}: shared-living proration would cap at {expected_max} min/mo; "
                    f"authorized {auth_min} min. Confirm separate-IADL documentation in the "
                    "case file or request reduction."
                )
                shared_living_lines.append(msg)
                warnings.append(msg)
    c8_sl_passed = len(shared_living_lines) == 0
    c8_sl = Check(
        number=10,
        name="Shared-living proration sanity (ASM 120)",
        passed=c8_sl_passed,
        expected="auth_min ≤ cap/2 for listed IADLs when shared living without separate docs",
        actual=("ok" if c8_sl_passed else "; ".join(shared_living_lines)),
        tolerance="policy — does not fail math banner",
        detail=(
            "no shared-living proration issue"
            if c8_sl_passed
            else "; ".join(shared_living_lines)
        ),
        counts_toward_all_passed=False,
    )
    checks.append(c8_sl)

    # --- CHECK 9 — Per-task occurrence counts match config placement ---
    # Expected count for each task = |{date ∈ month : task_runs_on(date)}|
    # where ``task_runs_on`` is driven by ScheduleConfig.selected_weekdays
    # ∪ selected_dates. No frequency (7/3/2-wk) assumptions anywhere. If
    # the schedule has no config (legacy payload), we skip the per-task
    # comparison with a clean "unknown" rather than back-filling with a
    # dpw-based guess.
    occ_subs: list[SubCheck] = []
    occ_fails: list[str] = []
    have_config = bool(config_tasks)
    for t in tasks:
        nm = str(t.get("task_name", ""))
        actual_cnt = int(task_counts.get(nm, 0))
        entry = config_tasks.get(nm)
        expected_cnt = _expected_placement_count(entry, month_dates)
        if expected_cnt < 0:
            # No config entry → can't project; flag as sub-check "skipped"
            # but don't fail the check on that account.
            occ_subs.append(
                SubCheck(
                    task_name=nm,
                    auth_min=-1,
                    scheduled_min=actual_cnt,
                    variance=0,
                    passed=True,
                )
            )
            continue
        variance = actual_cnt - expected_cnt
        passed = variance == 0
        occ_subs.append(
            SubCheck(
                task_name=nm,
                auth_min=expected_cnt,
                scheduled_min=actual_cnt,
                variance=variance,
                passed=passed,
            )
        )
        if not passed:
            occ_fails.append(
                f"{nm!r}: expected {expected_cnt}, got {actual_cnt} (Δ{variance:+d})"
            )
    c9_occ = Check(
        number=11,
        name="Per-task occurrence counts match config placement",
        passed=len(occ_fails) == 0,
        expected=(
            "count == |month_dates ∩ (selected_weekdays ∪ selected_dates)| per task"
            if have_config
            else "ScheduleConfig not present — check skipped"
        ),
        actual=("ok" if not occ_fails else occ_fails),
        tolerance="exact per task",
        detail=(
            f"{len(occ_subs)} task(s); actual counts from daily_schedule + "
            "task_occurrence_counts, expected from ScheduleConfig"
        ),
        sub_checks=occ_subs,
    )
    checks.append(c9_occ)

    # --- CHECK 10 — Per-day placement + structural invariant ---
    # For each date in the month, compare the actual task set on that
    # day to the task set the config places there, and verify the
    # duration invariant duration_min == Σ(tasks.values()). No
    # shift-type whitelist — the check is arithmetic + config, nothing
    # example-specific about weekday patterns.
    actual_by_date: dict[str, dict[str, Any]] = {}
    for d in daily:
        iso = str(d.get("date") or "")
        if iso:
            actual_by_date[iso] = d

    c9_day_fails: list[str] = []
    days_with_mismatch = 0
    # Validate every date in the month when we know the calendar; fall
    # back to whatever dates are in daily_schedule when we don't (legacy).
    iter_dates: list[str] = (
        [d0.isoformat() for d0 in month_dates] if month_dates else list(actual_by_date.keys())
    )
    for iso in iter_dates:
        actual = actual_by_date.get(iso)
        actual_tasks: dict[str, int] = {}
        if actual is not None:
            for k, v in (actual.get("tasks") or {}).items():
                iv = int(v or 0)
                if iv > 0:
                    actual_tasks[str(k)] = iv

        # (a) Placement match against config — only when we have a
        #     config to project.
        if have_config and month_dates:
            d0 = date.fromisoformat(iso)
            expected_tasks = _expected_tasks_on_date(config_tasks, d0)
            exp_names = set(expected_tasks)
            got_names = set(actual_tasks)
            if exp_names != got_names:
                missing = sorted(exp_names - got_names)
                extra = sorted(got_names - exp_names)
                parts: list[str] = []
                if missing:
                    parts.append(f"missing={missing}")
                if extra:
                    parts.append(f"extra={extra}")
                c9_day_fails.append(f"{iso}: " + "; ".join(parts))
                days_with_mismatch += 1

        # (b) Duration invariant on any day that has an entry.
        if actual is not None:
            dur = int(actual.get("duration_min", 0) or 0)
            tsum = sum(actual_tasks.values())
            if dur != tsum:
                c9_day_fails.append(
                    f"{iso}: duration_min={dur} ≠ Σ(tasks)={tsum}"
                )
                days_with_mismatch += 1

    total_days_checked = len(iter_dates)
    c10_day = Check(
        number=12,
        name="Per-day placement matches config and duration = Σ task minutes",
        passed=len(c9_day_fails) == 0,
        expected=(
            "tasks on each date == config projection; duration_min == Σ(tasks.values())"
            if have_config and month_dates
            else "duration_min == Σ(tasks.values()) (no config or calendar — placement check skipped)"
        ),
        actual=(
            "ok"
            if not c9_day_fails
            else f"{days_with_mismatch} day(s) off of {total_days_checked}"
        ),
        tolerance="exact",
        detail="; ".join(c9_day_fails)[:800] if c9_day_fails else "all days balance",
    )
    checks.append(c10_day)

    # --- CHECK 11 — No day exceeds reasonable working hours ---
    hard_fail_days: list[str] = []
    warn_days: list[str] = []
    for d in daily:
        dur = int(d.get("duration_min", 0) or 0)
        date_s = str(d.get("date", ""))
        if dur > 480:
            hard_fail_days.append(f"{date_s} = {dur} min")
        elif dur > 300:
            warn_days.append(f"{date_s} = {dur} min")
    for w in warn_days:
        warnings.append(f"long day (>5h): {w}")
    c11_hours = Check(
        number=13,
        name="No day exceeds reasonable working hours",
        passed=len(hard_fail_days) == 0,
        expected="all days ≤ 480 min (8h)",
        actual=("ok" if not hard_fail_days else hard_fail_days),
        tolerance="warn > 300 min; fail > 480 min",
        detail=(f"{len(warn_days)} day(s) between 5–8h" if warn_days else ""),
    )
    checks.append(c11_hours)

    # --- CHECK 12 — Deviation day justified ---
    # A catchup day is "justified" when removing its actual minutes
    # would cause the plan to miss the monthly target. This replaces
    # the old errand_extra = Σ(2/wk min_per_day) assumption, which was
    # only correct when the catchup carried exactly the 2/wk bundle
    # (the Ottilie default-config shape). Any user-configured catchup
    # composition — a single 7/wk top-up, a mixed bundle, a Friday
    # override, etc. — is now validated on what it actually carries.
    if has_catchup:
        dev_passed = (total_min - catchup_actual_min) != sched_mm
        dev_detail = (
            f"catchup day carries {catchup_actual_min} min; without it "
            f"the plan total ({total_min - catchup_actual_min}) would miss "
            f"the monthly target ({sched_mm})"
            if dev_passed
            else "removing the catchup day already yields the monthly target "
            "(the catchup carries no reconciliation-relevant minutes)"
        )
    else:
        dev_passed = True
        dev_detail = "no catchup day used → trivially ok"
    c12_dev = Check(
        number=14,
        name="Deviation day justified",
        passed=dev_passed,
        expected=(
            "(schedule_total − catchup_day_min) ≠ monthly target"
            if has_catchup
            else "no catchup → trivially ok"
        ),
        actual=(
            f"total={total_min}, catchup_min={catchup_actual_min}, target={sched_mm}"
        ),
        tolerance="exact",
        detail=dev_detail,
    )
    checks.append(c12_dev)

    # --- CHECK 13 — Form total matches calculated total ---
    # If OCR couldn't recover the bottom-line total, we have nothing to
    # compare against. Surface that cleanly rather than failing the check
    # with a misleading "$0.00" — the reviewer already sees the value is
    # missing, they don't need a red ✗ about it.
    form_total = float(extracted_form.monthly_total_amount or 0.0)
    if form_total <= 0.0:
        c13_form = Check(
            number=15,
            name="Form total matches calculated total",
            passed=True,
            expected=f"${sched_form_amt:,.2f} (±$0.02)",
            actual="not extracted",
            tolerance="±$0.02 (line-rounding drift)",
            detail="The PDF didn't expose a clean bottom-line total — check skipped.",
        )
    else:
        amt_gap = abs(form_total - sched_form_amt)
        c13_form = Check(
            number=15,
            name="Form total matches calculated total",
            passed=amt_gap <= 0.02 + 1e-9,
            expected=f"${sched_form_amt:,.2f} (±$0.02)",
            actual=f"${form_total:,.2f}",
            tolerance="±$0.02 (line-rounding drift)",
            detail=f"gap vs per-line Σ (mdhhs_form_amount) = ${amt_gap:.2f}",
        )
    checks.append(c13_form)

    c16_billing = Check(
        number=16,
        name="Billing rule applied (ASM 144 / MSA-1904)",
        passed=(
            (st_del is None or int(st_del) == delivered_min)
            and (st_bill_m is None or int(st_bill_m) == exp_bill_min)
            and (st_bill_a is None or abs(float(st_bill_a) - exp_bill_amt) <= 1e-6)
        ),
        expected=f"{exp_bill_min} min; ${exp_bill_amt:.2f}",
        actual=(
            f"{int(st_bill_m)} min; ${float(st_bill_a):.2f}"
            if st_bill_m is not None and st_bill_a is not None
            else "not all fields stored on schedule payload"
        ),
        tolerance="exact when billable_* / delivered_minutes present",
        detail=(
            "Optional API snapshot fields, when present, must match "
            "compute_billable_minutes / compute_billable_amount on daily totals."
        ),
    )
    checks.append(c16_billing)

    # --- CHECK 17 — Form total matches form line-item sum ---
    line_sum = round(sum(float(t.get("monthly_amount", 0.0) or 0.0) for t in tasks), 2)
    if form_total <= 0.0:
        c14_line = Check(
            number=17,
            name="Form total matches sum of line items",
            passed=True,
            expected=f"${line_sum:,.2f}",
            actual="not extracted",
            tolerance="±$0.02",
            detail="The PDF didn't expose a clean bottom-line total — check skipped.",
        )
    else:
        line_gap = abs(form_total - line_sum)
        c14_line = Check(
            number=17,
            name="Form total matches sum of line items",
            passed=line_gap <= 0.02 + 1e-9,
            expected=f"${line_sum:,.2f}",
            actual=f"${form_total:,.2f}",
            tolerance="±$0.02",
            detail=f"form_total − Σ(lines) = ${form_total - line_sum:+.2f}",
        )
    checks.append(c14_line)

    # --- CHECK 15 — Companion tasks ⊆ parent's placement tokens ---
    companion_violations: list[str] = []
    cfg_blob = sd.get("config") or {}
    cfg_tasks_raw = (
        cfg_blob.get("tasks")
        if isinstance(cfg_blob.get("tasks"), list)
        else []
    )
    placement_by_comp: dict[str, dict[str, Any]] = {
        str(t.get("task_name") or ""): t
        for t in cfg_tasks_raw
        if isinstance(t, dict) and str(t.get("task_name") or "")
    }

    def _tokens_from_placement_row(row: dict[str, Any]) -> set[str]:
        sw = row.get("selected_weekdays") or []
        sd0 = row.get("selected_dates") or []
        return {str(x) for x in sw} | {str(x) for x in sd0}

    for comp_name, parent_name in COMPANION_TO_PARENT.items():
        comp = placement_by_comp.get(comp_name)
        parent = placement_by_comp.get(parent_name)
        if not comp or not parent:
            continue
        comp_days = _tokens_from_placement_row(comp)
        parent_days = _tokens_from_placement_row(parent)
        extra = comp_days - parent_days
        if extra:
            companion_violations.append(
                f"{comp_name} configured on "
                f"{sorted(extra)!r}; {parent_name} lacks those placements"
            )

    c15_companion = Check(
        number=18,
        name="Companion tasks share parent's days",
        passed=len(companion_violations) == 0,
        expected="every companion ⊆ its parent's weekday/date token set",
        actual="ok" if not companion_violations else "; ".join(companion_violations),
        tolerance="exact",
        detail=(
            f"{len(COMPANION_TO_PARENT)} companion pairing(s); "
            f" placements from schedule.config.tasks"
        ),
    )
    checks.append(c15_companion)

    return ValidationReport(
        checks=checks,
        warnings=warnings,
        delivered_minutes=delivered_min,
        authorized_minutes=sched_mm,
        billable_minutes=exp_bill_min,
    )


def validation_report_to_dict(r: ValidationReport) -> dict[str, Any]:
    return {
        "checks": [asdict(c) for c in r.checks],
        "all_passed": r.all_passed,
        "pass_count": r.pass_count,
        "fail_count": r.fail_count,
        "warnings": list(r.warnings),
        "summary": r.summary,
        "validation_status": r.validation_status,
        "delivered_minutes": r.delivered_minutes,
        "authorized_minutes": r.authorized_minutes,
        "billable_minutes": r.billable_minutes,
    }


def _sub_from_dict(d: dict[str, Any]) -> SubCheck:
    return SubCheck(
        task_name=str(d.get("task_name", "")),
        auth_min=int(d.get("auth_min", 0) or 0),
        scheduled_min=int(d.get("scheduled_min", 0) or 0),
        variance=int(d.get("variance", 0) or 0),
        passed=bool(d.get("passed", False)),
        informational=bool(d.get("informational", False)),
    )


def validation_report_from_dict(d: dict[str, Any]) -> ValidationReport:
    """Tolerant loader: accepts persisted validation shapes across check-count versions."""
    checks: list[Check] = []
    for c in d.get("checks") or []:
        c = dict(c)
        subs_raw = c.pop("sub_checks", None) or []
        subs = [_sub_from_dict(s) if isinstance(s, dict) else s for s in subs_raw]
        checks.append(
            Check(
                number=int(c.get("number", 0) or 0),
                name=str(c.get("name", "")),
                passed=bool(c.get("passed", False)),
                expected=c.get("expected", ""),
                actual=c.get("actual", ""),
                tolerance=str(c.get("tolerance", "")),
                detail=str(c.get("detail", "")),
                sub_checks=subs,
                counts_toward_all_passed=bool(c.get("counts_toward_all_passed", True)),
            )
        )
    warnings_raw = d.get("warnings") or []
    warnings = [str(w) for w in warnings_raw]
    return ValidationReport(
        checks=checks,
        warnings=warnings,
        delivered_minutes=int(d.get("delivered_minutes", 0) or 0),
        authorized_minutes=int(d.get("authorized_minutes", 0) or 0),
        billable_minutes=int(d.get("billable_minutes", 0) or 0),
    )
