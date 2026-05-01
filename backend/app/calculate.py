"""Month-calibrated MDHHS schedules built from a user-editable ScheduleConfig.

Placement authority
-------------------
Authorization (``min_per_day``, ``days_per_week``, monthly totals) is
immutable. ``ScheduleConfig.tasks[].selected_weekdays`` ∪ ``selected_dates``,
minus any ``excluded_dates``, are the **actual** placement ``generate_schedule``
uses. Optional ``preferred_weekdays`` / ``preferred_dates`` capture editor intent;
``default_config_for`` reconciles prefs into ``selected_*`` when
seeding or when the API PATCH handler re-runs placement.
``placement_overrides`` records moves when a preferred day could not
fit. ``generate_schedule`` never infers placement from task frequency;
it only reads ``selected_*`` per date.

Legacy fallback
---------------
``default_config_for`` is the *only* path that invents a placement —
and it runs exclusively when the plan has no persisted config
(``generate_schedule(..., config=None)``). Inside that fallback we do
two things:

1. Seed ``selected_weekdays`` from a weekend-weighted template
   (``default_weekdays_for_dpw``).
2. Optionally add a **single deviation-day** bundle when the 4.3-week
   projection leaves a gap at least as large as ``Σ(2/wk min_per_day)``
   — the MDHHS catch-up convention (last Wednesday when possible). This
   does **not** force Σ scheduled calendar minutes to equal
   ``compute_mdhhs_form_minutes``: that form total is an authorization cap
   (ASM 144), while delivered minutes vary by month shape.

Derived display fields
----------------------
``shift_type`` on each daily row, and the ``hw_days`` / ``laundry_days``
/ ``shopping_days`` / ``travel_days`` fields on ``CalibratedSchedule``,
are derived for **display only**.

* **Shift labels** (``WEEKDAY_STD`` / ``HW_DAY`` / ``WEEKEND_FULL`` /
  ``CATCHUP``) combine the task mix on the day with frequency sets
  ``daily_names``, ``three_names``, ``two_names`` built from **each
  task's authorized** ``days_per_week`` (not from hardcoded task-name
  buckets). Eating/Feeding at 7/wk appears in ``daily_names`` the same
  way as Bathing or Medication.

* **Named day lists**: ``hw_days`` lists dates that host **any** task
  authorized at 3/wk; ``laundry_days``, ``shopping_days``, and
  ``travel_days`` list dates that host the canonical **Laundry**,
  **Shopping for Food/Meds**, or either **Travel For …** line
  respectively — so travel is not conflated with shopping/laundry even
  when all share a 2/wk frequency.

Complex-care task names (see ``extract.COMPLEX_CARE_TASK_NAMES``) are never
assigned default 7/3/2 buckets by name; placement follows authorization
and ``ScheduleConfig`` only.
"""

from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# Display-only: which canonical MDHHS lines populate ``laundry_days`` /
# ``shopping_days`` / ``travel_days`` metadata (placement is never inferred
# from these — only from ``ScheduleConfig`` + authorized dpw/mpd).
DISPLAY_TRAVEL_TASK_NAMES: frozenset[str] = frozenset(
    {"Travel For Shopping", "Travel For Laundry"}
)
DISPLAY_SHOPPING_TASK_NAMES: frozenset[str] = frozenset({"Shopping for Food/Meds"})
DISPLAY_LAUNDRY_TASK_NAMES: frozenset[str] = frozenset({"Laundry"})

# Tasks that the cap-aligned trim never reduces. 1/wk tasks already
# fall 4× per month (under-delivered vs 4.3-week auth), so trimming
# them would distort per-task delivered counts. Only 7/wk tasks
# absorb the calendar-overshoot variance.
NEVER_TRIM_TASK_NAMES: frozenset[str] = (
    DISPLAY_SHOPPING_TASK_NAMES
    | DISPLAY_LAUNDRY_TASK_NAMES
    | DISPLAY_TRAVEL_TASK_NAMES
)

# Floor: a task's per-day minutes are never reduced below this fraction
# of its current value. Keeps each trim within natural visit-duration
# variance and prevents the policy from gutting any task to zero.
TRIM_TASK_MIN_FRACTION: float = 0.5

# Companion-task pairing: each key runs only on calendar days that also host its parent.
COMPANION_TO_PARENT: dict[str, str] = {
    "Travel For Shopping": "Shopping for Food/Meds",
    "Travel For Laundry": "Laundry",
}

WEEKS_PER_MONTH = 4.3


def round_half_up(x: float) -> int:
    """Round-half-away-from-zero — used outside MDHHS monthly minute lines (percent, ratios)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_half_even(x: float | Decimal) -> int:
    """MDHHS-6064-P-style banker's rounding to integer minutes.

    Per-line monthly minutes on the printed 6064-P round .5 cases
    to even (verified empirically against multiple authorizations):
    25 × 7 × 4.3 = 752.5 → 752 (form: 12:32)
    35 × 1 × 4.3 = 150.5 → 150 (form: 02:30)
    """
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def compute_monthly_minutes_rounded(min_per_day: int, days_per_week: int) -> int:
    """Per-line monthly minutes as printed on the MDHHS-6064-P (banker's rounding).

    Use this *only* for the displayed Time/Month value on the form.
    For the form's $ amount, use ``compute_task_amount`` (unrounded input).
    For the form's monthly-minutes TOTAL, use ``compute_mdhhs_form_minutes``.
    """
    return round_half_even(
        Decimal(min_per_day) * Decimal(days_per_week) * Decimal(str(WEEKS_PER_MONTH))
    )


def compute_task_amount(min_per_day: int, days_per_week: int, pay_rate: float) -> float:
    """Per-line $ as printed on the MDHHS-6064-P.

    Computed from UNROUNDED monthly minutes (mpd × dpw × 4.3) × pay_rate / 60,
    then quantized to cents with HALF_EVEN. Verified against the printed form:
    Bathing 16/7 @ $27 → 481.6 × 27/60 = $216.72 (form value), NOT 482 × 27/60 = $216.90.
    """
    raw = (
        Decimal(min_per_day)
        * Decimal(days_per_week)
        * Decimal(str(WEEKS_PER_MONTH))
        * Decimal(str(pay_rate))
        / Decimal("60")
    )
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def compute_mdhhs_form_minutes(tasks: list[dict[str, Any]]) -> int:
    """Σ of UNROUNDED per-task monthly minutes (mpd × dpw × 4.3), then half-even rounded."""
    raw = sum(
        Decimal(int(t["min_per_day"]))
        * Decimal(int(t["days_per_week"]))
        * Decimal(str(WEEKS_PER_MONTH))
        for t in tasks
    )
    return round_half_even(raw)


def compute_mdhhs_form_amount(tasks: list[dict[str, Any]], pay_rate: float) -> float:
    """Σ of UNROUNDED per-task $, then quantized to cents (matches the form's TOTAL $)."""
    pr = Decimal(str(pay_rate))
    raw = sum(
        Decimal(int(t["min_per_day"]))
        * Decimal(int(t["days_per_week"]))
        * Decimal(str(WEEKS_PER_MONTH))
        * pr
        / Decimal("60")
        for t in tasks
    )
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def asm120_shared_living_iadl_expected_max(cap_monthly_min: int) -> int:
    """Half of ASM 120 monthly IADL allowance when shared-living ½ proration applies."""
    return int(cap_monthly_min) // 2


# Full day names (calendar order Mon–Sun), used by build_xlsx / API / config
_WEEK: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

DOW_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class CalibrationError(Exception):
    """Raised for genuinely impossible inputs (empty task list, bad year/month)."""


class DayCapacityExceeded(CalibrationError):
    """Scheduled minutes for one calendar day exceed the worker window for that weekday."""

    def __init__(
        self,
        *,
        weekday: str,
        needed_minutes: int,
        available_minutes: int,
        earliest: str,
        latest: str,
        suggestions: list[dict[str, Any]],
        message: str,
    ) -> None:
        super().__init__(message)
        self.weekday = weekday
        self.needed_minutes = int(needed_minutes)
        self.available_minutes = int(available_minutes)
        self.earliest = earliest
        self.latest = latest
        self.suggestions = suggestions

    def http_detail(self) -> dict[str, Any]:
        return {
            "code": "DAY_CAPACITY_EXCEEDED",
            "weekday": self.weekday,
            "needed_minutes": self.needed_minutes,
            "available_minutes": self.available_minutes,
            "earliest": self.earliest,
            "latest": self.latest,
            "message": str(self),
            "suggestions": self.suggestions,
        }


def compute_monthly_minutes(min_per_day: int, days_per_week: int) -> float:
    return min_per_day * days_per_week * WEEKS_PER_MONTH


def compute_weekly_budget(tasks: list[dict[str, Any]]) -> int:
    return sum(int(t["min_per_day"]) * int(t["days_per_week"]) for t in tasks)


def compute_monthly_total(weekly_budget: int) -> float:
    """Unrounded aggregate (weekly_budget × 4.3). Use :func:`compute_mdhhs_form_minutes` for form TOTAL."""
    return weekly_budget * WEEKS_PER_MONTH


# ---------------------------------------------------------------------------
# Weekday defaults — weekend-weighted so small dpw values hit weekends first.
# ---------------------------------------------------------------------------
def default_weekdays_for_dpw(days_per_week: int) -> list[str]:
    """Returns the weekday list a task defaults to for a given frequency.

    Rule: weekend-weighted. 1/wk → Sun; 2/wk → Sat+Sun; 3/wk → Fri+Sat+Sun;
    4/wk → Thu..Sun; 5/wk → Wed..Sun; 6/wk → Tue..Sun; 7/wk → every day.
    """
    d = int(days_per_week)
    if d <= 0:
        return []
    if d >= 7:
        return list(_WEEK)
    return list(_WEEK[7 - d :])


def _parse_ampm(s: str) -> int:
    t = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*([AP]M)", t, re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid time: {s!r}")
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if not (0 <= mi <= 59) or not (1 <= h <= 12):
        raise ValueError(f"Invalid time: {s!r}")
    if ap == "AM":
        h24 = 0 if h == 12 else h
    else:
        h24 = 12 if h == 12 else h + 12
    return h24 * 60 + mi


def _format_ampm(minutes: int) -> str:
    m = int(minutes) % (24 * 60)
    h24, mi = m // 60, m % 60
    if h24 == 0:
        h12, suffix = 12, "AM"
    elif 1 <= h24 <= 11:
        h12, suffix = h24, "AM"
    elif h24 == 12:
        h12, suffix = 12, "PM"
    else:
        h12, suffix = h24 - 12, "PM"
    return f"{h12}:{mi:02d} {suffix}"


def _add_minutes(start: int, visit_minutes: int) -> int:
    return start + visit_minutes


# ---------------------------------------------------------------------------
# Worker availability — per-day earliest / latest work window (caregiver).
# ---------------------------------------------------------------------------
def default_worker_availability() -> dict[str, dict[str, Any]]:
    """Default Mon–Sun 1p–8p (full day names)."""
    row: dict[str, Any] = {
        "earliest": "1:00 PM",
        "latest": "8:00 PM",
        "visit_day_longer": False,
        "visit_day_latest": "",
        "preferred_duration_min": None,
    }
    return {name: dict(row) for name in _WEEK}


def parse_worker_availability(raw: Any) -> dict[str, dict[str, Any]]:
    """Merge user JSON onto :func:`default_worker_availability`."""
    base = default_worker_availability()
    if not isinstance(raw, dict):
        return base
    for k, v in raw.items():
        if k not in base or not isinstance(v, dict):
            continue
        row = dict(base[k])
        e = v.get("earliest")
        if isinstance(e, str) and e.strip():
            row["earliest"] = e.strip()
        lat = v.get("latest")
        if isinstance(lat, str) and lat.strip():
            row["latest"] = lat.strip()
        v_long = v.get("visit_day_longer")
        if isinstance(v_long, bool):
            row["visit_day_longer"] = v_long
        vdl = v.get("visit_day_latest")
        if isinstance(vdl, str) and vdl.strip():
            row["visit_day_latest"] = vdl.strip()
        pdm = v.get("preferred_duration_min")
        if pdm is None or pdm == "":
            row["preferred_duration_min"] = None
        elif isinstance(pdm, (int, float)) and not isinstance(pdm, bool):
            row["preferred_duration_min"] = int(pdm)
        base[k] = row
    for _d, win in base.items():
        try:
            a, b = _parse_ampm(win["earliest"]), _parse_ampm(win["latest"])
        except ValueError:
            continue
        if a > b:
            win["earliest"], win["latest"] = win["latest"], win["earliest"]
        if win.get("visit_day_longer") and (win.get("visit_day_latest") or "").strip():
            try:
                ext = _parse_ampm(str(win["visit_day_latest"]))
                b2 = _parse_ampm(str(win["latest"]))
                if ext < b2:
                    win["visit_day_latest"] = str(win["latest"])
            except ValueError:
                win["visit_day_latest"] = ""
    return base


def effective_visit_latest_for_worker_day(win: dict[str, Any]) -> str:
    """Latest clock-out bound for a calendar day that has scheduled visits."""
    base_latest = str(win.get("latest") or "8:00 PM")
    if not win.get("visit_day_longer"):
        return base_latest
    ext = (win.get("visit_day_latest") or "").strip()
    if not ext:
        ext = "10:00 PM"
    try:
        if _parse_ampm(ext) < _parse_ampm(base_latest):
            return base_latest
    except ValueError:
        return base_latest
    return ext


def weekday_capacity_minutes(
    avail: dict[str, dict[str, Any]], weekday: str
) -> int:
    """Minutes between earliest and the visit-day end for ``avail[weekday]``."""
    win = avail.get(weekday) or {}
    earliest_s = str(win.get("earliest") or "1:00 PM")
    upper = effective_visit_latest_for_worker_day(win)
    try:
        e0 = _parse_ampm(earliest_s)
        u0 = _parse_ampm(upper)
    except ValueError:
        return 0
    return max(0, u0 - e0)


def weekday_base_capacity_minutes(
    avail: dict[str, dict[str, Any]], weekday: str
) -> int:
    """Minutes between earliest and regular «To» only (no visit-day extension)."""
    win = avail.get(weekday) or {}
    earliest_s = str(win.get("earliest") or "1:00 PM")
    latest_s = str(win.get("latest") or "8:00 PM")
    try:
        e0 = _parse_ampm(earliest_s)
        l0 = _parse_ampm(latest_s)
    except ValueError:
        return 0
    return max(0, l0 - e0)


def visit_weekdays_union_from_tasks(tasks: list[dict[str, Any]]) -> set[str]:
    """Union of template weekdays implied by each task's ``days_per_week``."""
    s: set[str] = set()
    for t in tasks:
        dpw = int(t.get("days_per_week", 0) or 0)
        for d in default_weekdays_for_dpw(dpw):
            s.add(d)
    return s


def weekly_worker_capacity_preflight(
    avail: dict[str, dict[str, Any]],
    visit_weekdays: set[str],
) -> tuple[int, int, int]:
    """Sums per-visit-day capacity: (base_to_only, visit_extension_extra, total).

    Base uses regular «To»; total uses :func:`weekday_capacity_minutes` (visit-day
    end). Only weekdays in ``visit_weekdays`` contribute — same heuristic as
    the upload preflight bar.
    """
    base_sum = 0
    ext_sum = 0
    for d in _WEEK:
        if d not in visit_weekdays:
            continue
        b = weekday_base_capacity_minutes(avail, d)
        full = weekday_capacity_minutes(avail, d)
        base_sum += b
        ext_sum += max(0, full - b)
    return base_sum, ext_sum, base_sum + ext_sum


def preflight_headroom_percent(weekly_need: int, weekly_capacity: int) -> int:
    """Positive = headroom vs need; negative = deficit as % of need."""
    need = int(weekly_need)
    cap = int(weekly_capacity)
    if need <= 0:
        return 100 if cap > 0 else 0
    if cap >= need:
        return round_half_up((cap - need) / need * 100)
    return -round_half_up((need - cap) / need * 100)


# Minimum span (minutes) between availability «From» and the visit-day end; catches
# reversed From/To and other unusable rows before schedule generation.
MIN_WORKER_AVAILABILITY_SPAN_MINUTES = 120


def assert_worker_availability_sane(
    avail: dict[str, dict[str, Any]],
    *,
    min_span_minutes: int = MIN_WORKER_AVAILABILITY_SPAN_MINUTES,
) -> None:
    """Raise ``CalibrationError`` if any weekday's visit-day window is too narrow."""
    for d in _WEEK:
        win = avail[d]
        earliest_s = str(win.get("earliest") or "1:00 PM")
        try:
            e0 = _parse_ampm(earliest_s)
            u0 = _parse_ampm(effective_visit_latest_for_worker_day(win))
        except ValueError:
            raise CalibrationError(
                f"{d}: could not parse worker availability times "
                f"(From={earliest_s!r}, end={effective_visit_latest_for_worker_day(win)!r})."
            ) from None
        span = u0 - e0
        if span < int(min_span_minutes):
            upper = effective_visit_latest_for_worker_day(win)
            raise CalibrationError(
                f"{d}: worker availability only allows {span} minutes on that weekday "
                f"(from {earliest_s} through {upper}). "
                f"Each day needs at least {min_span_minutes} minutes between «From» and the end time used when "
                f"that day has visits. Widen the range or fix reversed From/To—reversed times are swapped and can "
                f"leave a very short window."
            )


def authorization_exceeds_weekly_worker_capacity(
    avail: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """422 detail when authorized weekly minutes exceed total worker weekly capacity.

    ``preferred_duration_min`` is a soft target only — it never produces a 422.
    """
    if not tasks:
        return None
    visit_union = visit_weekdays_union_from_tasks(tasks)
    _, _, tot_c = weekly_worker_capacity_preflight(avail, visit_union)
    need = sum(
        int(t.get("min_per_day", 0) or 0) * int(t.get("days_per_week", 0) or 0)
        for t in tasks
    )
    if need <= tot_c:
        return None
    return {
        "code": "AUTHORIZATION_EXCEEDS_WEEKLY_CAPACITY",
        "weekly_authorized_minutes": need,
        "weekly_total_capacity_minutes": tot_c,
        "message": (
            f"Authorized tasks need {need} min/week but worker availability only "
            f"allows about {tot_c} min/week across visit weekdays — widen windows "
            f"or reduce authorization."
        ),
    }


def preferred_window_from_worker_availability(
    avail: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build ``preferred_window`` for :func:`default_config_for` from availability."""
    st = {d: avail[d]["earliest"] for d in _WEEK}
    return {
        "weekday_start": avail["Monday"]["earliest"],
        "weekend_start": avail["Saturday"]["earliest"],
        "start_time_by_weekday": st,
    }


def _day_capacity_suggestions(
    dow_full: str,
    entry: "DayEntry",
    win: dict[str, Any],
    earliest_s: str,
    upper: str,
    span: int,
) -> list[dict[str, Any]]:
    """Actionable fixes when ``entry.duration_min`` does not fit ``[earliest_s, upper]``."""
    needed = int(entry.duration_min)
    if needed <= span:
        return []
    try:
        e0 = _parse_ampm(earliest_s)
    except ValueError:
        e0 = 0
    need_end = e0 + needed
    max_end = 23 * 60 + 59
    need_end_clamped = min(need_end, max_end)
    suggested_latest = _format_ampm(need_end_clamped)

    suggestions: list[dict[str, Any]] = []

    suggestions.append(
        {
            "label": f"Widen {dow_full} regular end to {suggested_latest}",
            "action": "set_latest",
            "weekday": dow_full,
            "latest": suggested_latest,
        }
    )

    v_long = bool(win.get("visit_day_longer"))
    if not v_long:
        suggestions.append(
            {
                "label": (
                    f"Enable Longer on visit days for {dow_full} "
                    f"and set Latest (if visits) to {suggested_latest}"
                ),
                "action": "visit_day_longer",
                "weekday": dow_full,
                "visit_day_longer": True,
                "visit_day_latest": suggested_latest,
            }
        )
    else:
        try:
            u0 = _parse_ampm(upper)
        except ValueError:
            u0 = e0
        if need_end_clamped > u0:
            suggestions.append(
                {
                    "label": (
                        f"Extend Latest (if visits) on {dow_full} to {suggested_latest}"
                    ),
                    "action": "extend_visit_day_latest",
                    "weekday": dow_full,
                    "visit_day_longer": True,
                    "visit_day_latest": suggested_latest,
                }
            )

    if entry.tasks:
        deficit = needed - span
        if deficit > 0:
            task_name, _mpd = max(entry.tasks.items(), key=lambda kv: kv[1])
            suggestions.append(
                {
                    "label": (
                        f"Reduce {task_name} on {dow_full} by {deficit} min "
                        f"(largest task that day)"
                    ),
                    "action": "reduce_task",
                    "weekday": dow_full,
                    "task_name": task_name,
                    "reduce_by": deficit,
                }
            )

    return suggestions


def clamp_shift_to_availability(
    clock_in: str,
    duration_min: int,
    earliest: str,
    latest: str,
) -> tuple[str, str]:
    """Pick clock-in/out so the block fits in [earliest, latest]."""
    try:
        e0 = _parse_ampm(earliest)
        l0 = _parse_ampm(latest)
        s0 = _parse_ampm(clock_in)
    except ValueError as err:
        raise CalibrationError(f"Invalid time in availability or schedule: {err}") from err
    dur = int(duration_min)
    end0 = s0 + dur
    if end0 <= l0 and s0 >= e0:
        return clock_in, _format_ampm(end0)
    s1 = e0
    end1 = s1 + dur
    if end1 <= l0:
        return _format_ampm(s1), _format_ampm(end1)
    s2 = l0 - dur
    if s2 >= e0:
        return _format_ampm(s2), _format_ampm(l0)
    raise CalibrationError(
        f"This day's visits need {dur} minutes but only {l0 - e0} minutes fit "
        f"between {earliest} and {latest}."
    )


# ---------------------------------------------------------------------------
# ScheduleConfig — the editable source of truth for day/time placement.
# ---------------------------------------------------------------------------
@dataclass
class TaskPlacement:
    task_name: str
    min_per_day: int
    days_per_week: int
    # Weekday names (e.g. ["Friday", "Saturday", "Sunday"]) the task runs on.
    selected_weekdays: list[str] = field(default_factory=list)
    # Optional month-specific dates (ISO YYYY-MM-DD). A date listed here also
    # runs the task even if its weekday isn't in selected_weekdays — used by
    # ``default_config_for`` to add a catch-up day that absorbs the
    # calibration gap for weekend-only (2/wk) tasks.
    selected_dates: list[str] = field(default_factory=list)
    # ISO dates omitted for this month (overshoot trim) even if the weekday or
    # selected_dates would otherwise schedule the task.
    excluded_dates: list[str] = field(default_factory=list)
    # True when ``default_config_for`` fell back to ``default_weekdays_for_dpw``
    # because no capacity-feasible weekday set existed.
    placement_fallback: bool = False
    # User intent (editor / PATCH). Empty means "no explicit weekday preference".
    preferred_weekdays: list[str] = field(default_factory=list)
    preferred_dates: list[str] = field(default_factory=list)
    # Audit trail when the scheduler could not honor a preferred weekday.
    placement_overrides: list[dict[str, Any]] = field(default_factory=list)
    # When True, preferred_* were inferred from legacy data (key absent in JSON)
    # and should behave like auto placement for catch-up / rebalance / greedy.
    preference_unspecified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "min_per_day": int(self.min_per_day),
            "days_per_week": int(self.days_per_week),
            "selected_weekdays": list(self.selected_weekdays),
            "selected_dates": list(self.selected_dates),
            "excluded_dates": list(self.excluded_dates),
            "placement_fallback": bool(self.placement_fallback),
            "preferred_weekdays": list(self.preferred_weekdays),
            "preferred_dates": list(self.preferred_dates),
            "placement_overrides": [dict(o) for o in self.placement_overrides],
            "preference_unspecified": bool(self.preference_unspecified),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskPlacement":
        sel = [str(x) for x in d.get("selected_weekdays", []) or []]
        sdt = [str(x) for x in d.get("selected_dates", []) or []]
        excl = [str(x) for x in d.get("excluded_dates", []) or []]
        raw_po = d.get("placement_overrides")
        if raw_po is None:
            po: list[dict[str, Any]] = []
        else:
            po = [dict(x) for x in raw_po if isinstance(x, dict)]
        if "preferred_weekdays" in d:
            pref_w = [str(x) for x in d.get("preferred_weekdays") or []]
            pref_unspec = False
        else:
            pref_w = list(sel)
            pref_unspec = True
        if "preferred_dates" in d:
            pref_d = [str(x) for x in d.get("preferred_dates") or []]
        else:
            pref_d = list(sdt)
        raw_pu = d.get("preference_unspecified")
        if raw_pu is None:
            preference_unspecified = pref_unspec
        else:
            preference_unspecified = bool(raw_pu)
        return cls(
            task_name=str(d.get("task_name", "")),
            min_per_day=int(d.get("min_per_day", 0) or 0),
            days_per_week=int(d.get("days_per_week", 0) or 0),
            selected_weekdays=sel,
            selected_dates=sdt,
            excluded_dates=excl,
            placement_fallback=bool(d.get("placement_fallback", False)),
            preferred_weekdays=pref_w,
            preferred_dates=pref_d,
            placement_overrides=po,
            preference_unspecified=preference_unspecified,
        )


def _placement_runs_on_date(p: TaskPlacement, d0: date) -> bool:
    """True iff ``p`` schedules on ``d0`` (weekday template, ISO extras, exclusions)."""
    iso = d0.isoformat()
    if iso in p.excluded_dates:
        return False
    dow = _full_dow_name(d0)
    return (dow in p.selected_weekdays) or (iso in p.selected_dates)


def _default_start_time_by_weekday(
    weekday_start: str = "1:00 PM",
    weekend_start: str = "1:00 PM",
) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _WEEK:
        out[name] = weekend_start if name in ("Saturday", "Sunday") else weekday_start
    return out


@dataclass
class ScheduleConfig:
    """User-editable schedule shape."""

    tasks: list[TaskPlacement] = field(default_factory=list)
    # One entry per weekday: "Monday" -> "1:00 PM".
    start_time_by_weekday: dict[str, str] = field(
        default_factory=_default_start_time_by_weekday
    )
    # When ``preferred_duration_min`` could not be met (capacity clamp or ±10% band).
    weekday_override_log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "start_time_by_weekday": dict(self.start_time_by_weekday),
            "weekday_override_log": [dict(x) for x in self.weekday_override_log],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScheduleConfig":
        tasks_raw = d.get("tasks") or []
        tasks = [TaskPlacement.from_dict(t) for t in tasks_raw if isinstance(t, dict)]
        times_raw = d.get("start_time_by_weekday") or {}
        base = _default_start_time_by_weekday()
        if isinstance(times_raw, dict):
            for k, v in times_raw.items():
                if isinstance(k, str) and k in base and isinstance(v, str) and v:
                    base[k] = v
        wol_raw = d.get("weekday_override_log")
        wol: list[dict[str, Any]] = []
        if isinstance(wol_raw, list):
            wol = [dict(x) for x in wol_raw if isinstance(x, dict)]
        return cls(
            tasks=tasks,
            start_time_by_weekday=base,
            weekday_override_log=wol,
        )


def _date_to_short_dow(d: date) -> str:
    return DOW_SHORT[d.weekday()]


def _full_dow_name(d: date) -> str:
    return _WEEK[d.weekday()]


def _month_dates(year: int, month: int) -> list[date]:
    _, nd = calendar.monthrange(year, month)
    return [date(year, month, d) for d in range(1, nd + 1)]


def _pick_weekdays_by_headroom(
    mpd: int,
    dpw: int,
    cap: dict[str, int],
    load: dict[str, int],
) -> tuple[list[str], bool]:
    """Auto-place: take the top ``dpw`` weekdays that fit ``mpd``.

    Rank feasible days by remaining capacity ``cap - load`` (descending), then
    by raw ``cap`` (descending), then Mon→Sun for stable ties. Matches MDHHS
    convention — longer shifts land where the worker has the most headroom.

    Falls back to ``default_weekdays_for_dpw`` (and ``placement_fallback=True``)
    when fewer than ``dpw`` days can absorb ``mpd``.
    """
    if dpw <= 0:
        return [], False
    template = default_weekdays_for_dpw(dpw)
    if mpd <= 0:
        return list(template), False

    feasible = [d for d in _WEEK if cap[d] - load[d] >= mpd]
    # For one-day-per-week tasks, prefer days with the least accumulated load first
    # so independent 1/wk errands do not all collapse onto the same weekday when
    # worker caps are asymmetric (spread before max-headroom tie-break).
    if dpw == 1:
        feasible.sort(
            key=lambda d: (load[d], -(cap[d] - load[d]), _WEEK.index(d)),
        )
    else:
        feasible.sort(
            key=lambda d: (cap[d] - load[d], cap[d], -_WEEK.index(d)),
            reverse=True,
        )
    if len(feasible) < dpw:
        return list(template), True
    chosen = sorted(feasible[:dpw], key=lambda d: _WEEK.index(d))
    return chosen, False


_BIG_CAP = 10**9


def _cap_minutes(cap: dict[str, int] | None, d: str) -> int:
    if cap is None:
        return _BIG_CAP
    return int(cap[d])


def _slack(cap: dict[str, int] | None, load: dict[str, int], d: str) -> int:
    return _cap_minutes(cap, d) - load[d]


def _explicit_editor_weekday_preference(p: TaskPlacement) -> bool:
    """Catch-up / rebalance / greedy skip when the user set real weekday picks."""
    return (not p.preference_unspecified) and bool(p.preferred_weekdays)


def _place_one_task_weekdays(
    task_name: str,
    mpd: int,
    dpw: int,
    preferred_weekdays: list[str],
    preference_unspecified: bool,
    cap: dict[str, int] | None,
    load: dict[str, int],
) -> TaskPlacement:
    """Two-pass weekday placement for a single task (Pass A prefs, Pass B fill)."""
    overrides: list[dict[str, Any]] = []
    cap_use: dict[str, int] = {d: _cap_minutes(cap, d) for d in _WEEK}
    pref_w = [d for d in preferred_weekdays if d in _WEEK]

    if dpw <= 0:
        return TaskPlacement(
            task_name=task_name,
            min_per_day=mpd,
            days_per_week=dpw,
            selected_weekdays=[],
            selected_dates=[],
            preferred_weekdays=list(pref_w),
            preferred_dates=[],
            placement_overrides=overrides,
            preference_unspecified=preference_unspecified,
        )

    if mpd <= 0:
        template = default_weekdays_for_dpw(dpw)
        return TaskPlacement(
            task_name=task_name,
            min_per_day=mpd,
            days_per_week=dpw,
            selected_weekdays=list(template),
            selected_dates=[],
            preferred_weekdays=list(pref_w),
            preferred_dates=[],
            placement_overrides=overrides,
            preference_unspecified=preference_unspecified,
        )

    # Pure auto: no explicit user preference list — headroom-ranked picker.
    if preference_unspecified and not pref_w:
        # Without availability data, preserve the legacy behaviour: each task
        # gets the weekend-weighted template independently — no cross-task load.
        if cap is None:
            template = default_weekdays_for_dpw(dpw) if dpw > 0 else []
            return TaskPlacement(
                task_name=task_name,
                min_per_day=mpd,
                days_per_week=dpw,
                selected_weekdays=list(template),
                selected_dates=[],
                placement_fallback=False,
                preferred_weekdays=[],
                preferred_dates=[],
                placement_overrides=overrides,
                preference_unspecified=True,
            )
        chosen, fb = _pick_weekdays_by_headroom(mpd, dpw, cap_use, load)
        for d in chosen:
            load[d] += mpd
        return TaskPlacement(
            task_name=task_name,
            min_per_day=mpd,
            days_per_week=dpw,
            selected_weekdays=chosen,
            selected_dates=[],
            placement_fallback=fb,
            preferred_weekdays=[],
            preferred_dates=[],
            placement_overrides=overrides,
            preference_unspecified=True,
        )

    chosen: list[str] = []
    chosen_set: set[str] = set()
    failed_pref_info: list[tuple[str, int, int]] = []

    for d in pref_w:
        if len(chosen) >= dpw:
            break
        if d in chosen_set:
            continue
        if load[d] + mpd <= cap_use[d]:
            load[d] += mpd
            chosen.append(d)
            chosen_set.add(d)
        else:
            failed_pref_info.append((d, load[d], cap_use[d]))

    remaining = dpw - len(chosen)
    pool = [d for d in _WEEK if d not in chosen_set]
    pool.sort(
        key=lambda w: (_slack(cap, load, w), _cap_minutes(cap, w), -_WEEK.index(w)),
        reverse=True,
    )
    fail_i = 0
    for d in pool:
        if remaining <= 0:
            break
        if load[d] + mpd > cap_use[d]:
            continue
        load[d] += mpd
        chosen.append(d)
        chosen_set.add(d)
        if fail_i < len(failed_pref_info):
            pref_day, lu, ccap = failed_pref_info[fail_i]
            overrides.append(
                {
                    "preferred": pref_day,
                    "placed_on": d,
                    "reason": (
                        f"{pref_day} capacity {ccap} min; this task needs {mpd} min "
                        f"but {pref_day} already had {lu} min of higher-priority tasks."
                    ),
                }
            )
            fail_i += 1
        remaining -= 1

    placement_fallback = len(chosen) < dpw

    chosen_sorted = sorted(chosen[:dpw], key=lambda x: _WEEK.index(x))
    return TaskPlacement(
        task_name=task_name,
        min_per_day=mpd,
        days_per_week=dpw,
        selected_weekdays=chosen_sorted,
        selected_dates=[],
        placement_fallback=placement_fallback,
        preferred_weekdays=list(pref_w),
        preferred_dates=[],
        placement_overrides=overrides,
        preference_unspecified=preference_unspecified,
    )


def _day_minutes_on_date(placements: list[TaskPlacement], d0: date) -> int:
    return sum(
        int(p.min_per_day)
        for p in placements
        if _placement_runs_on_date(p, d0)
    )


def _monthly_totals_by_weekday(
    placements: list[TaskPlacement], dates: list[date]
) -> tuple[dict[str, int], dict[str, int]]:
    s = {d: 0 for d in _WEEK}
    n = {d: 0 for d in _WEEK}
    for d0 in dates:
        w = _full_dow_name(d0)
        n[w] += 1
        s[w] += _day_minutes_on_date(placements, d0)
    return s, n


def _avg_for_weekday(s: dict[str, int], n: dict[str, int], w: str) -> float:
    if n[w] <= 0:
        return 0.0
    return s[w] / n[w]


def _preferred_duration_map(
    worker_availability: dict[str, dict[str, Any]] | None,
) -> dict[str, int]:
    out: dict[str, int] = {}
    if not worker_availability:
        return out
    for d in _WEEK:
        win = worker_availability.get(d) or {}
        raw = win.get("preferred_duration_min")
        if raw is None or raw == "":
            continue
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[d] = v
    return out


def _all_days_within_capacity(
    placements: list[TaskPlacement],
    dates: list[date],
    cap: dict[str, int],
) -> bool:
    for d0 in dates:
        w = _full_dow_name(d0)
        if _day_minutes_on_date(placements, d0) > cap[w]:
            return False
    return True


def _replace_weekday_in_placement(
    p: TaskPlacement, old_d: str, new_d: str
) -> bool:
    days = list(p.selected_weekdays)
    if old_d not in days or new_d in days:
        return False
    days = [new_d if x == old_d else x for x in days]
    days = sorted(days, key=lambda x: _WEEK.index(x))
    if len(days) != int(p.days_per_week):
        return False
    p.selected_weekdays = days
    return True


def _rebalance_to_preferred_shift_lengths(
    placements: list[TaskPlacement],
    dates: list[date],
    worker_availability: dict[str, dict[str, Any]] | None,
    cap: dict[str, int] | None,
) -> None:
    """Phase 1.5 — swap non-7/wk weekdays toward soft per-day shift targets.

    Each weekday with ``preferred_duration_min`` aims for an average daily
    load within ±10% of ``min(preferred, day capacity)``. Swaps preserve
    calendar weekday occurrence counts so monthly minutes stay stable.
    """
    pref = _preferred_duration_map(worker_availability)
    if not pref or not cap:
        return

    for _ in range(12):
        improved = False
        sums, counts = _monthly_totals_by_weekday(placements, dates)

        for w_tgt in _WEEK:
            if w_tgt not in pref:
                continue
            if counts[w_tgt] <= 0:
                continue
            w_eff = min(float(pref[w_tgt]), float(cap[w_tgt]))
            avg_here = _avg_for_weekday(sums, counts, w_tgt)
            if avg_here + 1e-6 >= w_eff * 0.9:
                continue
            donors: list[tuple[str, float]] = []
            for d_donor in _WEEK:
                if d_donor == w_tgt or counts[d_donor] <= 0:
                    continue
                avg_d = _avg_for_weekday(sums, counts, d_donor)
                p_d = pref.get(d_donor)
                if p_d is not None:
                    d_eff = min(float(p_d), float(cap[d_donor]))
                    if avg_d <= d_eff * 1.1 + 1e-6:
                        continue
                donors.append((d_donor, avg_d))
            donors.sort(key=lambda t: -t[1])
            for d_donor, _ in donors:
                if counts[d_donor] != counts[w_tgt]:
                    continue
                candidates = sorted(
                    [
                        p
                        for p in placements
                        if not _explicit_editor_weekday_preference(p)
                        and d_donor in p.selected_weekdays
                        and w_tgt not in p.selected_weekdays
                        and 0 < int(p.days_per_week) < 7
                    ],
                    key=lambda p: -int(p.min_per_day),
                )
                for p in candidates:
                    old_days = list(p.selected_weekdays)
                    if not _replace_weekday_in_placement(p, d_donor, w_tgt):
                        continue
                    if not _all_days_within_capacity(placements, dates, cap):
                        p.selected_weekdays = old_days
                        continue
                    improved = True
                    sums, counts = _monthly_totals_by_weekday(placements, dates)
                    break
                if improved:
                    break
            if improved:
                break

        if not improved:
            sums, counts = _monthly_totals_by_weekday(placements, dates)
            for w_over in _WEEK:
                if w_over not in pref:
                    continue
                if counts[w_over] <= 0:
                    continue
                w_eff = min(float(pref[w_over]), float(cap[w_over]))
                if _avg_for_weekday(sums, counts, w_over) <= w_eff * 1.1 + 1e-6:
                    continue
                candidates = sorted(
                    [
                        p
                        for p in placements
                        if not _explicit_editor_weekday_preference(p)
                        and w_over in p.selected_weekdays
                        and 0 < int(p.days_per_week) < 7
                    ],
                    key=lambda p: int(p.min_per_day),
                )
                reduced = False
                for p in candidates:
                    for x in _WEEK:
                        if x == w_over or x in p.selected_weekdays:
                            continue
                        if counts[w_over] != counts[x]:
                            continue
                        old_days = list(p.selected_weekdays)
                        if not _replace_weekday_in_placement(p, w_over, x):
                            continue
                        if not _all_days_within_capacity(placements, dates, cap):
                            p.selected_weekdays = old_days
                            continue
                        improved = True
                        reduced = True
                        sums, counts = _monthly_totals_by_weekday(placements, dates)
                        break
                    if reduced:
                        break
                if reduced:
                    break

        if not improved:
            break


def _weekday_minutes_from_daily_schedule(
    daily_schedule: list[DayEntry], dates: list[date]
) -> tuple[dict[str, int], dict[str, int]]:
    """Sum visit minutes per calendar weekday and count dates in month per weekday."""
    by_date = {de.date: int(de.duration_min) for de in daily_schedule}
    s = {d: 0 for d in _WEEK}
    n = {d: 0 for d in _WEEK}
    for d0 in dates:
        w = _full_dow_name(d0)
        n[w] += 1
        s[w] += by_date.get(d0, 0)
    return s, n


def _build_weekday_duration_override_log(
    daily_schedule: list[DayEntry],
    dates: list[date],
    worker_availability: dict[str, dict[str, Any]] | None,
    cap: dict[str, int] | None,
) -> list[dict[str, Any]]:
    pref = _preferred_duration_map(worker_availability)
    if not pref:
        return []
    sums, counts = _weekday_minutes_from_daily_schedule(daily_schedule, dates)
    out: list[dict[str, Any]] = []
    for w in _WEEK:
        if w not in pref:
            continue
        if counts[w] <= 0:
            continue
        raw_pref = int(pref[w])
        avg = _avg_for_weekday(sums, counts, w)
        actual_int = round_half_up(avg)
        cap_w = int(cap[w]) if cap else _BIG_CAP
        effective = float(min(raw_pref, cap_w))
        lo, hi = effective * 0.9, effective * 1.1
        in_band = lo - 1e-6 <= avg <= hi + 1e-6
        exceeds_cap = raw_pref > cap_w
        if not exceeds_cap and in_band:
            continue
        if exceeds_cap:
            reason = (
                f"Your target ({raw_pref} min) exceeds the {cap_w}-minute worker "
                f"window on {w}; actual average is {actual_int} min on visit days "
                f"this month."
            )
        else:
            reason = (
                f"Authorization + higher-priority preferences require "
                f"{actual_int} min on {w}."
            )
        out.append(
            {
                "weekday": w,
                "preferred_duration": raw_pref,
                "actual_duration": actual_int,
                "reason": reason,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Default config generation — weekend-weighted placement + optional deviation day.
# ---------------------------------------------------------------------------
def default_config_for(
    tasks: list[dict[str, Any]],
    year: int,
    month: int,
    preferred_window: dict[str, Any] | None = None,
    *,
    worker_availability: dict[str, dict[str, Any]] | None = None,
    prior: ScheduleConfig | None = None,
) -> ScheduleConfig:
    """Bootstrap a ``ScheduleConfig`` for plans that don't already have one.

    **Legacy / fallback path only.** ``generate_schedule`` is strictly
    config-driven — placement comes from
    ``ScheduleConfig.tasks[].selected_weekdays`` plus ``selected_dates``.
    This helper exists for two cases only:

    1. Fresh uploads, where the API seeds a default config from the OCR
       task list before handing it to the editor.
    2. Legacy plans persisted before the editor existed, which reach
       ``generate_schedule`` with ``config=None`` and need a sensible
       default so the schedule still calibrates.

    All *month-specific* logic (weekday templates and the optional Phase 1
    deviation-day bundle) lives inside this function and
    inside this function only. Callers outside the "no-config" fallback
    MUST NOT rely on any of this behavior — if they want deterministic
    placement they should build an explicit ``ScheduleConfig``.

    When ``prior`` is set, each task's ``preferred_weekdays`` /
    ``preferred_dates`` (and ``preference_unspecified``) feed Pass A + B;
    ``selected_weekdays`` is always recomputed. Explicit editor preferences
    skip catch-up date injection / Phase 1.5 swaps.

    Heuristic used here (pure defaults — user edits replace it):

    * Each task is seeded with Pass A (honor ``preferred_weekdays`` in order)
      then Pass B (fill remaining days by descending slack / capacity), or —
      when there are no explicit preferences — via ``default_weekdays_for_dpw``
      / ``_pick_weekdays_by_headroom`` with template fallback flagged by
      ``placement_fallback``.
    * **Phase 1.** If the 4.3-week projection leaves a gap at least
      ``Σ(2/wk min_per_day)``, route that full 2/wk bundle onto a
      single extra weekday — preferring the last non-default Wednesday
      (the conventional MDHHS deviation-day position), otherwise any
      last weekday not already hosting every 2/wk task.
    * **Phase 1.5.** Optional per-weekday ``preferred_duration_min`` values
      (from worker availability) steer non-7/wk template weekdays: we
      swap days in ``selected_weekdays`` when it tightens shift lengths toward
      capacity **without** changing how many times each calendar weekday
      appears in the month.

    Phase 2 first snaps companion travel onto its parent errand pattern, then
    adds default-only ISO-date overrides until delivered minutes match the
    4.3-week form total when the remaining gap is reducible.

    ASM 120 **complex-care** lines (see ``extract.COMPLEX_CARE_TASK_NAMES``)
    never receive a hidden 7/3/2 default from task name alone — only the
    authorized ``min_per_day`` / ``days_per_week`` shape the placement.
    """
    pw = dict(preferred_window or {})
    wk_start = str(pw.get("weekday_start", "1:00 PM"))
    we_start = str(pw.get("weekend_start", "1:00 PM"))
    start_map = _default_start_time_by_weekday(wk_start, we_start)
    overlay = pw.get("start_time_by_weekday")
    if isinstance(overlay, dict):
        for k, v in overlay.items():
            if (
                isinstance(k, str)
                and k in start_map
                and isinstance(v, str)
                and v.strip()
            ):
                start_map[k] = v.strip()

    prior_by_name: dict[str, TaskPlacement] = {}
    if prior:
        for lp in prior.tasks:
            prior_by_name[str(lp.task_name or "")] = lp

    load: dict[str, int] = {d: 0 for d in _WEEK}
    cap: dict[str, int] | None = None
    if worker_availability:
        cap = {d: weekday_capacity_minutes(worker_availability, d) for d in _WEEK}
    task_rows: list[tuple[int, str, int, int]] = []
    for i, t in enumerate(tasks):
        task_rows.append(
            (
                i,
                str(t.get("task_name") or ""),
                int(t.get("min_per_day") or 0),
                int(t.get("days_per_week") or 0),
            )
        )
    ordered: list[TaskPlacement | None] = [None] * len(tasks)
    # With availability, process largest min/day first so capacity is respected;
    # without it, preserve PDF / task-list order (Ottilie April 2026 regression).
    row_order = (
        sorted(task_rows, key=lambda r: (-r[2], r[0]))
        if worker_availability
        else task_rows
    )
    for i, name, mpd, dpw in row_order:
        pt = prior_by_name.get(name)
        if pt:
            pref_w = list(pt.preferred_weekdays)
            pref_d = list(pt.preferred_dates)
            unspec = bool(pt.preference_unspecified)
        else:
            pref_w, pref_d, unspec = [], [], True
        eff_load = load if cap is not None else {d: 0 for d in _WEEK}
        p = _place_one_task_weekdays(
            name, mpd, dpw, pref_w, unspec, cap, eff_load
        )
        p.preferred_dates = list(pref_d)
        p.selected_dates = list(pref_d)
        ordered[i] = p

    placements = [ordered[i] for i in range(len(tasks)) if ordered[i] is not None]

    try:
        dates = _month_dates(year, month)
    except (ValueError, calendar.IllegalMonthError):
        _coplace_companions(placements, [])
        return ScheduleConfig(tasks=placements, start_time_by_weekday=start_map)
    if not dates:
        _coplace_companions(placements, [])
        return ScheduleConfig(tasks=placements, start_time_by_weekday=start_map)

    weekly_budget = compute_weekly_budget(tasks)
    target_monthly_min = compute_mdhhs_form_minutes(tasks)
    scheduled = _sum_monthly_minutes(placements, dates)
    gap = target_monthly_min - scheduled
    twos_total = sum(int(t["min_per_day"]) for t in tasks if int(t["days_per_week"]) == 2)
    twos_default_weekdays = set(default_weekdays_for_dpw(2))

    # Phase 1 — full 2/wk catch-up day. If the 4.3-week projection
    # leaves a gap at least the size of the 2/wk bundle, route that
    # bundle onto a single extra weekday so the reviewer sees one
    # cleanly labeled CATCHUP row rather than a dozen scattered
    # per-task overrides. Preferred weekday is the last non-default
    # Wednesday (MDHHS convention); we fall back to the last weekday
    # of the month that isn't already a default 2/wk day.
    if gap > 0 and twos_total > 0 and gap >= twos_total:
        candidate: date | None = None
        for d in reversed(dates):
            if d.weekday() == 2 and _full_dow_name(d) not in twos_default_weekdays:
                candidate = d
                break
        if candidate is None:
            for d in reversed(dates):
                if _full_dow_name(d) not in twos_default_weekdays:
                    candidate = d
                    break
        if (
            candidate is not None
            and cap is not None
            and cap[_full_dow_name(candidate)] - load[_full_dow_name(candidate)]
            < twos_total
        ):
            candidate = None
        if candidate is not None:
            iso = candidate.isoformat()
            for p in placements:
                if _explicit_editor_weekday_preference(p):
                    continue
                if p.days_per_week == 2 and iso not in p.selected_dates:
                    p.selected_dates = [*p.selected_dates, iso]
            scheduled += twos_total
            gap = target_monthly_min - scheduled

    # Phase 1.5 — optional rebalance toward preferred shift lengths (availability).
    _rebalance_to_preferred_shift_lengths(
        placements, dates, worker_availability, cap
    )

    # Phase 2 — companion co-placement (domain: travel rides with errand parent).
    _coplace_companions(placements, dates)
    _greedy_close_gap(placements, dates, target_monthly_min)
    _coplace_companions(placements, dates)

    return ScheduleConfig(tasks=placements, start_time_by_weekday=start_map)


def _greedy_close_gap(
    placements: list[TaskPlacement],
    dates: list[date],
    target_monthly_min: int,
    max_rounds: int = 64,
) -> None:
    """Add extra per-task session overrides until the monthly gap can't shrink.

    Edits ``placements`` in place. On every round we walk the tasks in
    descending ``min_per_day`` order and pick the first one that (a) fits
    the remaining gap and (b) has at least one un-placed date — i.e.
    largest-fit *with an eligible date*, rather than largest-fit-or-bust.
    The previous implementation bailed out as soon as the single
    largest-mpd task had no open date left (common for 7/wk tasks whose
    ``selected_weekdays`` already covers every day of the month), which
    left reducible gaps unclosed on month shapes other than the
    reference (Ottilie April 2026) case.

    The round-robin loop still terminates on one of three conditions:

    * ``gap <= 0`` — the fallback calibrated exactly.
    * No candidate task has an mpd ≤ gap — the residual is smaller than
      every available task's per-day minutes; it will surface as a
      visible Check 5 variance, which is the honest signal that the
      editor needs user input.
    * No candidate with a feasible mpd has an un-placed date — the
      month is saturated for every task that could close the gap.

    ``max_rounds`` bounds worst-case work. With the default (64) this
    easily absorbs the largest gaps we've seen while staying O(month).
    """
    for _ in range(max_rounds):
        scheduled = _sum_monthly_minutes(placements, dates)
        gap = target_monthly_min - scheduled
        if gap <= 0:
            return
        candidates = sorted(
            (
                p
                for p in placements
                if not _explicit_editor_weekday_preference(p)
                and 0 < int(p.min_per_day) <= gap
            ),
            key=lambda p: int(p.min_per_day),
            reverse=True,
        )
        placed = False
        for p in candidates:
            chosen: date | None = None
            for d in reversed(dates):
                if _placement_runs_on_date(p, d):
                    continue
                chosen = d
                break
            if chosen is None:
                # This task is saturated — try the next-smaller mpd rather
                # than abandoning the round.
                continue
            p.selected_dates = [*p.selected_dates, chosen.isoformat()]
            placed = True
            break
        if not placed:
            return  # no reducible task remains — residual stays


def _coplace_companions(
    placements: list[TaskPlacement], _dates: list[date] | None
) -> None:
    """Snap each companion task's calendar tokens onto its parent's lists.

    The companion simply **reuses** the parent's ``selected_weekdays`` and
    ``selected_dates`` so every errand day that hosts the parent also hosts
    travel (recurrence + catch-up ISO rows). If the companion's authorized
    ``days_per_week`` exceeds the parent's template + ISO token count, we
    flag ``placement_fallback`` — the schedule engine may still need to honor
    the lower monthly occurrence shape elsewhere.
    """
    by_name: dict[str, TaskPlacement] = {p.task_name: p for p in placements if p.task_name}

    for companion_name, parent_name in COMPANION_TO_PARENT.items():
        comp = by_name.get(companion_name)
        parent = by_name.get(parent_name)
        if comp is None or parent is None:
            continue

        prev_weekdays = list(comp.selected_weekdays)
        prev_dates = list(comp.selected_dates)
        prev_pref_w = list(comp.preferred_weekdays)
        prev_pref_d = list(comp.preferred_dates)

        parent_week_list = list(parent.selected_weekdays)
        parent_dates_list = list(parent.selected_dates)

        buddy_dpw = max(int(comp.days_per_week or 0), 0)
        parent_slots = len(parent_week_list) + len(parent_dates_list)

        new_weekdays = list(parent_week_list)
        new_dates = list(parent_dates_list)

        comp.selected_weekdays = new_weekdays
        comp.selected_dates = new_dates
        comp.excluded_dates = list(parent.excluded_dates)
        comp.preferred_weekdays = list(new_weekdays)
        comp.preferred_dates = list(new_dates)
        comp.preference_unspecified = False

        if prev_weekdays != new_weekdays or prev_dates != new_dates:
            comp.placement_overrides = [
                *comp.placement_overrides,
                {
                    "preferred": ",".join(prev_pref_w + prev_pref_d)
                    or ",".join(prev_weekdays + prev_dates)
                    or "(unset)",
                    "placed_on": ",".join(new_weekdays + new_dates) or "(none)",
                    "reason": (
                        f"{companion_name} co-locates with {parent_name} — "
                        f"a companion task's days must be a subset of its parent's days."
                    ),
                },
            ]

        if buddy_dpw > parent_slots:
            comp.placement_fallback = True


def _sum_monthly_minutes(
    placements: list[TaskPlacement],
    dates: list[date],
) -> int:
    total = 0
    for d in dates:
        for p in placements:
            if _placement_runs_on_date(p, d):
                total += int(p.min_per_day)
    return total


# ---------------------------------------------------------------------------
# Calibrated schedule — output shape.
# ---------------------------------------------------------------------------
ShiftType = Literal["WEEKDAY_STD", "HW_DAY", "WEEKEND_FULL", "CATCHUP"]


@dataclass
class DayEntry:
    date: date
    day_of_week: str
    shift_type: ShiftType
    clock_in: str
    clock_out: str
    duration_min: int
    tasks: dict[str, int]


def compute_delivered_minutes(daily_schedule: list[DayEntry]) -> int:
    """Σ duration_min across the daily schedule — calendar-shaped delivery projection."""
    return sum(int(d.duration_min) for d in daily_schedule)


def compute_delivered_amount(daily_schedule: list[DayEntry], pay_rate: float) -> float:
    """Delivered minutes × pay_rate / 60, half-even-quantized to cents."""
    raw = Decimal(compute_delivered_minutes(daily_schedule)) * Decimal(
        str(pay_rate)
    ) / Decimal("60")
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def compute_billable_minutes(delivered_min: int, authorized_min: int) -> int:
    """min(delivered, authorized) — MSA-1904 invoice minutes cap per ASM 144."""
    return min(int(delivered_min), int(authorized_min))


def compute_billable_amount(delivered_amount: float, authorized_amount: float) -> float:
    """min(delivered, authorized) on the dollar side, preserving cent precision."""
    d = Decimal(str(delivered_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    a = Decimal(str(authorized_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return float(min(d, a))


def _apply_worker_availability_to_entry(
    entry: DayEntry,
    dow_full: str,
    worker_availability: dict[str, dict[str, Any]] | None,
) -> None:
    if not worker_availability or dow_full not in worker_availability:
        return
    win = worker_availability[dow_full]
    earliest_s = str(win.get("earliest") or "1:00 PM")
    upper = effective_visit_latest_for_worker_day(win)
    try:
        entry.clock_in, entry.clock_out = clamp_shift_to_availability(
            entry.clock_in,
            entry.duration_min,
            earliest_s,
            upper,
        )
    except CalibrationError as err:
        if "minutes fit between" not in str(err):
            raise
        try:
            span = _parse_ampm(upper) - _parse_ampm(earliest_s)
        except ValueError:
            span = 0
        needed = int(entry.duration_min)
        suggestions = _day_capacity_suggestions(
            dow_full, entry, win, earliest_s, upper, span
        )
        msg = (
            f"{dow_full}: scheduled visits on this day total {needed} minutes, "
            f"but worker availability for that weekday only allows {span} minutes "
            f"from {earliest_s} through {upper}."
        )
        raise DayCapacityExceeded(
            weekday=dow_full,
            needed_minutes=needed,
            available_minutes=span,
            earliest=earliest_s,
            latest=upper,
            suggestions=suggestions,
            message=msg,
        ) from err


@dataclass
class CalibratedSchedule:
    """Month-calibrated schedule output.

    Shift-shape duration fields (``weekday_std_min``, ``hw_day_min``,
    ``weekend_full_min``, ``catchup_day_min``) are **honest derivatives**
    of ``daily_schedule``: each is the duration of the first emitted
    day of that shift type (or 0/None if no such day exists this
    month). They are display-only summaries; business logic must not
    assume them to equal any formulaic bucket sum such as
    ``core + errand_extra``. When days of the same shift type have
    varying durations (an arbitrary config can produce this), treat
    these fields as a representative sample, not a monthly total.
    """

    year: int
    month: int
    month_name: str
    pay_rate: float
    mdhhs_weekly_minutes: int
    mdhhs_monthly_minutes: int
    mdhhs_monthly_amount: float
    mdhhs_form_amount: float
    weekday_std_min: int
    hw_day_min: int
    weekend_full_min: int
    catchup_day_min: Optional[int]
    hw_days: list[date]
    laundry_days: list[date]
    shopping_days: list[date]
    travel_days: list[date]
    catchup_date: Optional[date]
    delivered_minutes: int = 0
    delivered_amount: float = 0.0
    billable_minutes: int = 0
    billable_amount: float = 0.0
    daily_schedule: list[DayEntry] = field(default_factory=list)
    weekly_pattern: dict[str, Any] = field(default_factory=dict)
    cumulative_by_day: list[tuple[date, int, int, str]] = field(default_factory=list)
    schedule_trim_log: list[dict[str, Any]] = field(default_factory=list)
    config: Optional["ScheduleConfig"] = None

    def as_dict(self) -> dict[str, Any]:
        """API / JSON: includes legacy `days` for weekly view + new monthly fields."""
        return schedule_to_dict(self)


def schedule_to_dict(cs: CalibratedSchedule) -> dict[str, Any]:
    """Build the dict stored in plan JSON and passed to build_xlsx / validate."""
    task_counts: dict[str, int] = {}
    for de in cs.daily_schedule:
        for nm, mins in de.tasks.items():
            if mins > 0:
                task_counts[nm] = task_counts.get(nm, 0) + 1
    d: dict[str, Any] = {
        "year": cs.year,
        "month": cs.month,
        "month_name": cs.month_name,
        "pay_rate": cs.pay_rate,
        "weekly_minutes": cs.mdhhs_weekly_minutes,
        "monthly_minutes": float(cs.mdhhs_monthly_minutes),
        "mdhhs_monthly_minutes": cs.mdhhs_monthly_minutes,
        "mdhhs_monthly_amount": cs.mdhhs_monthly_amount,
        "mdhhs_form_amount": cs.mdhhs_form_amount,
        "delivered_minutes": cs.delivered_minutes,
        "delivered_amount": cs.delivered_amount,
        "billable_minutes": cs.billable_minutes,
        "billable_amount": cs.billable_amount,
        "monthly_amount": cs.mdhhs_monthly_amount,
        "weekday_std_min": cs.weekday_std_min,
        "hw_day_min": cs.hw_day_min,
        "weekend_full_min": cs.weekend_full_min,
        "catchup_day_min": cs.catchup_day_min,
        "catchup_date": cs.catchup_date.isoformat() if cs.catchup_date else None,
        "hw_days": [x.isoformat() for x in cs.hw_days],
        "laundry_days": [x.isoformat() for x in cs.laundry_days],
        "shopping_days": [x.isoformat() for x in cs.shopping_days],
        "travel_days": [x.isoformat() for x in cs.travel_days],
        "days": cs.weekly_pattern,
        "weekly_pattern": cs.weekly_pattern,
        "task_occurrence_counts": task_counts,
        "daily_schedule": [
            {
                "date": e.date.isoformat(),
                "day_of_week": e.day_of_week,
                "shift_type": e.shift_type,
                "clock_in": e.clock_in,
                "clock_out": e.clock_out,
                "duration_min": e.duration_min,
                "tasks": dict(e.tasks),
            }
            for e in cs.daily_schedule
        ],
        "cumulative_by_day": [
            (dt.isoformat(), sub, cum, hhm) for dt, sub, cum, hhm in cs.cumulative_by_day
        ],
        # Always emit the config — including an empty dict on the legacy
        # no-config path — so the JSON shape is stable and the editor
        # can round-trip it verbatim (tasks, selected_weekdays,
        # selected_dates, start_time_by_weekday all preserved).
        "config": cs.config.to_dict() if cs.config is not None else {},
        "schedule_trim_log": list(cs.schedule_trim_log or []),
    }
    return d


# ---------------------------------------------------------------------------
# Mechanical scheduler — honors ScheduleConfig placement.
# ---------------------------------------------------------------------------
def _trim_schedule_to_authorized(
    daily_schedule: list[DayEntry],
    authorized_minutes: int,
    *,
    worker_availability: dict[str, dict[str, Any]] | None,
    trim_exempt_names: frozenset[str],
) -> tuple[list[dict[str, Any]], int]:
    """Trim minutes off the last week of the month until delivered = auth.

    Walks daily_schedule in REVERSE date order (so the last calendar
    week absorbs the variance) and on each day reduces the largest
    non-1/wk task until either the gap is closed OR that task hits
    its 50% floor. Updates duration_min and clock_out for each
    touched day. clock_in is never changed.

    Returns (trim_log, residual_overshoot):
        trim_log — one entry per touched day with date, task,
                   minutes_shaved, new_task_min, new_duration_min.
        residual_overshoot — 0 when fully closed; positive when
                   constraints prevented a full close (caller logs
                   a warning and lets the existing billable cap
                   handle the remainder).
    """
    delivered = sum(int(d.duration_min) for d in daily_schedule)
    gap = delivered - int(authorized_minutes)
    if gap <= 0:
        return ([], 0)

    trim_log: list[dict[str, Any]] = []
    if not daily_schedule:
        return (trim_log, gap)
    # Only the last calendar week (final 7 days of the month) absorbs trim —
    # matches MSA invoicing expectation for end-of-month variance.
    last_dt = daily_schedule[-1].date
    cutoff = last_dt - timedelta(days=6)
    # daily_schedule is date-sorted ascending; iterate reversed for
    # last-week-first absorption.
    for day in reversed(daily_schedule):
        if gap <= 0:
            break
        if day.date < cutoff:
            break
        while gap > 0:
            eligible = sorted(
                (
                    (name, int(mins))
                    for name, mins in day.tasks.items()
                    if name not in trim_exempt_names and int(mins) > 0
                ),
                key=lambda kv: -kv[1],
            )
            if not eligible:
                break
            target_name, current_min = eligible[0]
            floor = max(1, int(current_min * TRIM_TASK_MIN_FRACTION))
            absorbable = current_min - floor
            if absorbable <= 0:
                break
            shave = min(absorbable, gap)
            new_task_min = current_min - shave
            day.tasks[target_name] = new_task_min
            day.duration_min = int(day.duration_min) - shave
            # Recompute clock_out — preserves clock_in
            if worker_availability:
                _apply_worker_availability_to_entry(
                    day,
                    _full_dow_name(day.date),
                    worker_availability,
                )
            else:
                start_min = _parse_ampm(day.clock_in)
                day.clock_out = _format_ampm(
                    _add_minutes(start_min, day.duration_min)
                )
            trim_log.append(
                {
                    "date": day.date.isoformat(),
                    "task": target_name,
                    "minutes_shaved": shave,
                    "new_task_min": new_task_min,
                    "new_duration_min": day.duration_min,
                }
            )
            gap -= shave

    return (trim_log, gap)


def _derive_shift_type(
    task_names: set[str],
    daily_names: set[str],
    three_names: set[str],
    two_names: set[str],
) -> ShiftType:
    """Map a day's task mix to a display-only shift label.

    These four values are *display hints* — the XLSX/PDF renderers use
    them for fill colours and row highlighting. No placement logic,
    calibration logic, or validation logic may pattern-match on these
    strings; they're derived from the task-frequency sets already
    available on the day and have no authority over scheduling.
    """
    has_three = bool(task_names & three_names)
    has_two = bool(task_names & two_names)
    if has_three and has_two:
        return "WEEKEND_FULL"
    if has_three:
        return "HW_DAY"
    if has_two:
        return "CATCHUP"
    return "WEEKDAY_STD"


def generate_schedule(
    tasks: list[dict[str, Any]],
    pay_rate: float,
    year: int,
    month: int,
    preferred_window: dict[str, Any] | None = None,
    config: ScheduleConfig | dict[str, Any] | None = None,
    *,
    worker_availability: dict[str, dict[str, Any]] | None = None,
    trim_to_authorized: bool = True,
) -> CalibratedSchedule:
    """Build a month of daily visits from a ``ScheduleConfig``.

    Placement is driven **exclusively** by the supplied config:
    ``config.tasks[].selected_weekdays`` plus ``config.tasks[].selected_dates``
    are the source of truth for every task occurrence on every date. No
    example-specific weekday patterns, frequency-based day inference,
    or catch-up heuristics influence placement here — those all belong
    exclusively to ``default_config_for``.

    ``config`` may be a :class:`ScheduleConfig`, a dict in the same
    shape (as persisted in ``schedule.config`` JSON), or ``None``:

    * ``ScheduleConfig`` — used verbatim. Nothing is inferred; every
      task that runs on a date must either have that weekday in
      ``selected_weekdays`` or have that ISO date in ``selected_dates``.
    * ``dict`` — normalized via :meth:`ScheduleConfig.from_dict`.
      Unknown keys are ignored; an explicit empty ``tasks`` list yields
      an empty ``daily_schedule`` and is **not** silently replaced by
      defaults.
    * ``None`` — legacy fallback: the plan was persisted before the
      config editor existed. We bootstrap a config via
      :func:`default_config_for` (the only path that invents
      placements) and the caller's ``preferred_window`` is honored as
      the start-time map.

    Guarantees consumed elsewhere:

    * For every emitted day ``duration_min == Σ tasks.values()``.
    * ``task_occurrence_counts`` (rebuilt in ``schedule_to_dict``)
      equals the count of dates where each task's config projection
      was true — no frequency back-fill.
    * The emitted ``"config"`` key in ``schedule_to_dict`` preserves
      ``selected_weekdays`` and ``selected_dates`` verbatim so the
      editor and the next generation round-trip exactly.
    """
    if not tasks:
        raise CalibrationError("No tasks supplied — cannot build a schedule")
    try:
        calendar.monthrange(int(year), int(month))
    except (ValueError, calendar.IllegalMonthError) as e:
        raise CalibrationError(f"Invalid year/month: {year}-{month}") from e

    if isinstance(config, ScheduleConfig):
        cfg = config
    elif isinstance(config, dict):
        cfg = ScheduleConfig.from_dict(config)
    elif config is None:
        # Legacy plan — no config was ever persisted. Bootstrap placement;
        # monthly Σ may differ from the form cap until billable reconciliation.
        cfg = default_config_for(
            tasks,
            year,
            month,
            preferred_window,
            worker_availability=worker_availability,
        )
    else:
        raise TypeError(
            f"config must be ScheduleConfig | dict | None, got {type(config).__name__}"
        )

    pr = float(pay_rate)
    weekly_budget = compute_weekly_budget(tasks)
    # Per-line summation is authoritative — matches the MDHHS-6064-P form.
    # The aggregate path (weekly_budget × 4.3) drifts by ±1 min when any
    # task's mpd×dpw×4.3 ends in .5.
    mdhhs_monthly_minutes = compute_mdhhs_form_minutes(tasks)
    mdhhs_form_amount = compute_mdhhs_form_amount(tasks, pr)
    mdhhs_monthly_amount = mdhhs_form_amount  # one number, not two

    daily_names = {
        str(t.get("task_name", "")) for t in tasks if int(t["days_per_week"]) == 7
    }
    # Includes Eating/Feeding when authorized 7/wk — shift typing only; no name overrides.
    three_names = {
        str(t.get("task_name", "")) for t in tasks if int(t["days_per_week"]) == 3
    }
    two_names = {
        str(t.get("task_name", "")) for t in tasks if int(t["days_per_week"]) == 2
    }

    dates = _month_dates(year, month)

    _coplace_companions(cfg.tasks, dates)
    if worker_availability:
        cap_rb = {
            d: weekday_capacity_minutes(worker_availability, d) for d in _WEEK
        }
        _rebalance_to_preferred_shift_lengths(
            cfg.tasks, dates, worker_availability, cap_rb
        )

    # Build each day strictly from the config. A task runs on a date iff
    # ``_placement_runs_on_date`` (weekday ∪ selected_dates ∖ excluded_dates).
    daily_schedule: list[DayEntry] = []
    for d0 in dates:
        dow = _full_dow_name(d0)
        tmap: dict[str, int] = {}
        for p in cfg.tasks:
            if _placement_runs_on_date(p, d0) and int(p.min_per_day) > 0:
                tmap[p.task_name] = int(p.min_per_day)
        if not tmap:
            continue
        duration = sum(tmap.values())
        start_txt = cfg.start_time_by_weekday.get(dow, "1:00 PM")
        start = _parse_ampm(start_txt)
        end = _add_minutes(start, duration)
        task_names = set(tmap.keys())
        shift = _derive_shift_type(task_names, daily_names, three_names, two_names)
        entry = DayEntry(
            date=d0,
            day_of_week=_date_to_short_dow(d0),
            shift_type=shift,
            clock_in=_format_ampm(start),
            clock_out=_format_ampm(end),
            duration_min=duration,
            tasks=tmap,
        )
        _apply_worker_availability_to_entry(entry, dow, worker_availability)
        daily_schedule.append(entry)

    cap_for_log: dict[str, int] | None = None
    if worker_availability:
        cap_for_log = {
            d: weekday_capacity_minutes(worker_availability, d) for d in _WEEK
        }
    cfg.weekday_override_log = _build_weekday_duration_override_log(
        daily_schedule, dates, worker_availability, cap_for_log
    )

    one_wk_names = frozenset(
        str(t["task_name"])
        for t in tasks
        if int(t.get("days_per_week", 0) or 0) == 1
    )
    trim_exempt = NEVER_TRIM_TASK_NAMES | one_wk_names

    schedule_trim_log: list[dict[str, Any]] = []
    if trim_to_authorized:
        schedule_trim_log, _residual_overshoot = _trim_schedule_to_authorized(
            daily_schedule,
            int(mdhhs_monthly_minutes),
            worker_availability=worker_availability,
            trim_exempt_names=trim_exempt,
        )
        if _residual_overshoot > 0:
            logger.warning(
                "Cap-aligned trim could not fully close gap: "
                f"{_residual_overshoot} min remain over authorized "
                f"({mdhhs_monthly_minutes}). Billable layer will cap."
            )

    # Date lists for workbook metadata: ``hw_days`` = any 3/wk-authorized task;
    # laundry / shopping / travel = dates that host those **canonical** MDHHS
    # line items (not every generic 2/wk task — see ``DISPLAY_*_TASK_NAMES``).
    hw_days_derived: list[date] = sorted(
        e.date for e in daily_schedule if set(e.tasks.keys()) & three_names
    )
    laundry_days_derived: list[date] = sorted(
        e.date
        for e in daily_schedule
        if set(e.tasks.keys()) & DISPLAY_LAUNDRY_TASK_NAMES
    )
    shopping_days_derived: list[date] = sorted(
        e.date
        for e in daily_schedule
        if set(e.tasks.keys()) & DISPLAY_SHOPPING_TASK_NAMES
    )
    travel_days_derived: list[date] = sorted(
        e.date
        for e in daily_schedule
        if set(e.tasks.keys()) & DISPLAY_TRAVEL_TASK_NAMES
    )

    catchup_date: date | None = None
    for e in daily_schedule:
        if e.shift_type == "CATCHUP":
            catchup_date = e.date
            break

    # Representative per-day minutes for each shift type — computed
    # from the *actual* emitted daily_schedule rather than from a
    # 7/3/2-wk frequency-bucket formula. These fields are display-only
    # summaries; for plans whose days of a given shift type are
    # non-uniform the first-seen day is used as the representative.
    # For any plan whose shift type doesn't appear this month we emit
    # 0 (or None for catchup) so consumers see "no such day" rather
    # than a fabricated duration.
    _first_by_shift: dict[str, int] = {}
    for e in daily_schedule:
        _first_by_shift.setdefault(e.shift_type, e.duration_min)
    weekday_std_min = int(_first_by_shift.get("WEEKDAY_STD", 0))
    hw_day_min = int(_first_by_shift.get("HW_DAY", 0))
    weekend_full_min = int(_first_by_shift.get("WEEKEND_FULL", 0))
    catchup_day_min_maybe = int(_first_by_shift.get("CATCHUP", 0))

    cum: list[tuple[date, int, int, str]] = []
    running = 0
    for de in daily_schedule:
        running += de.duration_min
        h, m = running // 60, running % 60
        cum.append((de.date, de.duration_min, running, f"{h:02d}:{m:02d}"))

    by_dow: dict[str, DayEntry] = {}
    for de in daily_schedule:
        full = _full_dow_name(de.date)
        if full not in by_dow:
            by_dow[full] = de
    weekly_pattern: dict[str, Any] = {}
    for name in _WEEK:
        de2 = by_dow.get(name)
        if de2 is None:
            continue
        weekly_pattern[name] = {
            "start": de2.clock_in,
            "end": de2.clock_out,
            "minutes": de2.duration_min,
            "tasks": [k for k, v in de2.tasks.items() if v > 0],
        }

    delivered_minutes = compute_delivered_minutes(daily_schedule)
    delivered_amount = compute_delivered_amount(daily_schedule, pr)
    billable_minutes = compute_billable_minutes(
        delivered_minutes, mdhhs_monthly_minutes
    )
    billable_amount = compute_billable_amount(delivered_amount, mdhhs_form_amount)

    return CalibratedSchedule(
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        pay_rate=pr,
        mdhhs_weekly_minutes=int(weekly_budget),
        mdhhs_monthly_minutes=mdhhs_monthly_minutes,
        mdhhs_monthly_amount=mdhhs_monthly_amount,
        mdhhs_form_amount=mdhhs_form_amount,
        delivered_minutes=delivered_minutes,
        delivered_amount=delivered_amount,
        billable_minutes=billable_minutes,
        billable_amount=billable_amount,
        weekday_std_min=weekday_std_min,
        hw_day_min=hw_day_min,
        weekend_full_min=weekend_full_min,
        catchup_day_min=catchup_day_min_maybe if catchup_date else None,
        hw_days=hw_days_derived,
        laundry_days=laundry_days_derived,
        shopping_days=shopping_days_derived,
        travel_days=travel_days_derived,
        catchup_date=catchup_date,
        daily_schedule=daily_schedule,
        weekly_pattern=weekly_pattern,
        cumulative_by_day=cum,
        schedule_trim_log=schedule_trim_log,
        config=cfg,
    )
