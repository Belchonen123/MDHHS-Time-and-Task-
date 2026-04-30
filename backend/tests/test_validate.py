"""Tests for the validate module (14 checks: math + ASM 120 policy).

Ottilie Smith — April 2026 regression lives under ``math`` passing checks.
"""

import copy
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location(
    "_test_calculate_for_validate", _ROOT / "tests" / "test_calculate.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
OTTILIE_TASKS = _mod.OTTILIE_TASKS
OTTILIE_PREFERRED = _mod.OTTILIE_PREFERRED

from app.calculate import (
    compute_monthly_minutes_rounded,
    compute_mdhhs_form_amount,
    compute_task_amount,
    default_config_for,
    generate_schedule,
)
from app.extract import ExtractedForm
from app.validate import (
    EHHS_THRESHOLD_MIN,
    cross_check,
    parse_form_monthly_time_to_minutes,
)


def _check(r, number: int):
    for c in r.checks:
        if c.number == number:
            return c
    raise AssertionError(f"check {number} missing")


def _fmt_monthly_mdhhm(mm_r: int) -> str:
    """MDHHS line time column from whole-minute-rounded monthly duration."""
    return f"{mm_r // 60}:{mm_r % 60:02d}"


def make_ottilie_extracted_form() -> ExtractedForm:
    pay = 27.0
    rows: list[dict] = []
    for t in OTTILIE_TASKS:
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        mm_r = compute_monthly_minutes_rounded(mpd, dpw)
        rows.append(
            {
                **t,
                "monthly_time_str": _fmt_monthly_mdhhm(mm_r),
                "monthly_amount": compute_task_amount(mpd, dpw, pay),
            }
        )
    total = compute_mdhhs_form_amount(rows, pay)
    return ExtractedForm(pay_rate=pay, tasks=rows, monthly_total_amount=total)


def make_ottilie_schedule_dict() -> dict:
    """April 2026 calibrated schedule dict (as stored in the DB)."""
    return generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED).as_dict()


def _minimal_form_and_schedule(
    tasks: list,
    *,
    pay: float = 27.0,
    year: int = 2026,
    month: int = 4,
) -> tuple[ExtractedForm, dict]:
    """Single-plan helper for ASM policy tests — default placement + seeded form lines."""
    rows: list[dict] = []
    for t in tasks:
        mpd = int(t["min_per_day"])
        dpw = int(t["days_per_week"])
        mm_r = compute_monthly_minutes_rounded(mpd, dpw)
        rows.append(
            {
                **t,
                "monthly_time_str": _fmt_monthly_mdhhm(mm_r),
                "monthly_amount": compute_task_amount(mpd, dpw, pay),
            }
        )
    total = compute_mdhhs_form_amount(rows, pay)
    form = ExtractedForm(pay_rate=pay, tasks=rows, monthly_total_amount=total)
    cfg = default_config_for(tasks, year, month)
    sched = generate_schedule(tasks, pay, year, month, config=cfg).as_dict()
    return form, sched


# --- happy path -------------------------------------------------------------


def test_ottilie_math_passes_asm120_flags_meal_prep_cap() -> None:
    """Math banner green; Meal Preparation exceeds ASM 120 IADL monthly cap (+policy check 6)."""
    form = make_ottilie_extracted_form()
    sched = make_ottilie_schedule_dict()
    r = cross_check(form, sched)
    assert len(r.checks) == 18
    assert r.all_passed
    assert r.pass_count == 13
    assert r.fail_count == 0
    assert r.validation_status == "BILLABLE_EXACT"
    assert any("Meal Preparation" in w or "ASM 120 IADL" in w for w in r.warnings)
    assert [c.number for c in r.checks] == list(range(1, 19))
    asm6 = _check(r, 8)
    assert not asm6.passed
    assert "Meal Preparation" in str(asm6.detail)
    assert _check(r, 9).passed
    for c in r.checks:
        if c.counts_toward_all_passed:
            assert c.passed, f"{c.number} {c.name}: {c.actual}"


def test_check5_sub_checks_shape_and_totals() -> None:
    form = make_ottilie_extracted_form()
    sched = make_ottilie_schedule_dict()
    r = cross_check(form, sched)
    c7 = _check(r, 7)
    assert len(c7.sub_checks) == len(OTTILIE_TASKS) + 1
    floor = c7.sub_checks[-1]
    assert "floor" in floor.task_name.lower()
    assert floor.passed
    hw = next(s for s in c7.sub_checks if s.task_name == "Housework")
    assert hw.scheduled_min == 144
    assert hw.auth_min == 155
    assert hw.variance == -11
    assert hw.informational


def test_accepts_calibrated_schedule_instance() -> None:
    form = make_ottilie_extracted_form()
    cs = generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED)
    r = cross_check(form, cs)
    assert r.all_passed


def test_parse_form_monthly_time() -> None:
    assert parse_form_monthly_time_to_minutes("08:02") == 8 * 60 + 2
    assert parse_form_monthly_time_to_minutes("70:48") == 70 * 60 + 48
    assert parse_form_monthly_time_to_minutes("bad") is None


# --- corruption / negative cases --------------------------------------------


def test_check1_fails_on_bad_weekly_budget() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["mdhhs_weekly_minutes"] = 1
    sched["weekly_minutes"] = 1
    r = cross_check(form, sched)
    assert not _check(r, 1).passed


def test_check2_fails_on_bad_monthly_target() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["mdhhs_monthly_minutes"] = 9999
    r = cross_check(form, sched)
    assert not _check(r, 2).passed


def test_check4_fails_when_billable_minutes_snapshot_wrong() -> None:
    """Stale billable_minutes vs Σ daily must fail check 3b."""
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["daily_schedule"][0]["duration_min"] = 0
    sched["billable_minutes"] = 4248
    sched["delivered_minutes"] = sum(
        int(d.get("duration_min", 0) or 0) for d in sched["daily_schedule"]
    )
    r = cross_check(form, sched)
    assert not _check(r, 4).passed


def test_check6_fails_when_dollar_total_off() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["mdhhs_form_amount"] = 1.0
    sched["mdhhs_monthly_amount"] = 1.0
    sched["monthly_amount"] = 1.0
    r = cross_check(form, sched)
    assert not _check(r, 6).passed


def test_check_6_no_longer_swallows_45_cent_drift() -> None:
    """Billable $ rollup tolerates small MDHHS drift, not $0.45 cracks."""
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    r0 = cross_check(form, sched)
    cal_dollar = float(_check(r0, 6).expected)
    sched["mdhhs_form_amount"] = cal_dollar - 0.45
    sched["mdhhs_monthly_amount"] = sched["mdhhs_form_amount"]
    sched["monthly_amount"] = sched["mdhhs_form_amount"]
    r = cross_check(form, sched)
    assert not _check(r, 6).passed


def test_check_4_passes_when_per_line_path_is_used() -> None:
    """Schedule dict carries per-line mdhhs_form_amount; billable math matches."""
    form = make_ottilie_extracted_form()
    sched = make_ottilie_schedule_dict()
    assert "mdhhs_form_amount" in sched
    r = cross_check(form, sched)
    assert r.all_passed
    assert _check(r, 4).passed


def test_check7_fails_when_task_occurrence_floor_broken() -> None:
    """Dropping Housework from one HW day trips occurrence floor or placement."""
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    for d in sched["daily_schedule"]:
        if d["shift_type"] == "HW_DAY":
            d["tasks"] = {k: v for k, v in d["tasks"].items() if k != "Housework"}
            d["duration_min"] -= 12
            break
    cnt: dict[str, int] = {}
    for d in sched["daily_schedule"]:
        for k, v in (d.get("tasks") or {}).items():
            if v:
                cnt[k] = cnt.get(k, 0) + 1
    sched["task_occurrence_counts"] = cnt

    r = cross_check(form, sched)

    c7 = _check(r, 7)
    hw = next(s for s in c7.sub_checks if s.task_name == "Housework")
    assert hw.scheduled_min == 132  # 11 × 12

    c11 = _check(r, 11)
    assert not c11.passed


def test_check11_fails_when_occurrence_count_wrong() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["task_occurrence_counts"] = dict(sched.get("task_occurrence_counts") or {})
    sched["task_occurrence_counts"]["Housework"] = 5  # should be 12
    r = cross_check(form, sched)
    assert not _check(r, 11).passed


def test_check12_fails_on_shift_type_invariant() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    for d in sched["daily_schedule"]:
        if d["shift_type"] == "WEEKDAY_STD":
            d["duration_min"] = 999  # invalid for this shift type
            break
    r = cross_check(form, sched)
    assert not _check(r, 12).passed


def test_check13_warns_on_long_day_and_fails_over_eight_hours() -> None:
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["daily_schedule"][0]["duration_min"] = 360  # 6h → warning
    sched["daily_schedule"][1]["duration_min"] = 600  # 10h → fail
    r = cross_check(form, sched)
    assert not _check(r, 13).passed
    assert any("long day" in w for w in r.warnings)


def test_check14_passes_when_no_catchup() -> None:
    """Any month without a catchup day should pass check 14 trivially."""
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched["catchup_date"] = None
    sched["catchup_day_min"] = None
    r = cross_check(form, sched)
    assert _check(r, 14).passed


def test_check15_fails_when_form_dollars_diverge() -> None:
    form = make_ottilie_extracted_form()
    form.monthly_total_amount = 2000.00  # $87+ away from per-line Σ (mdhhs_form_amount)
    r = cross_check(form, make_ottilie_schedule_dict())
    assert not _check(r, 15).passed


def test_check17_fails_on_line_sum_mismatch() -> None:
    form = make_ottilie_extracted_form()
    # Corrupt one line amount so Σ(lines) ≠ form total
    form.tasks[0] = {**form.tasks[0], "monthly_amount": form.tasks[0]["monthly_amount"] + 50.0}
    r = cross_check(form, make_ottilie_schedule_dict())
    assert not _check(r, 17).passed


# --- Check 14 — catchup composition generalization (audit B-5) ---------------
def test_check14_uses_actual_catchup_duration_not_2wk_sum() -> None:
    """Check 14 must accept any catchup composition, not just the 2/wk bundle.

    Regression for the old behavior that computed
    ``errand_extra = Σ(2/wk min_per_day)`` and asked whether
    ``total - errand_extra`` missed target. For a catchup day whose
    minutes differ from that sum (e.g. any user-configured override),
    the old check would flip to fail even when the catchup day
    honestly closes the monthly gap. The new check reads the actual
    ``duration_min`` of the catchup row and validates that removing
    it would miss target.
    """
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    # Synthetic composition no longer matches ScheduleConfig — drop config so
    # validation focuses on catchup arithmetic, not placement tokens.
    sched.pop("config", None)

    catchup_iso = sched["catchup_date"]
    assert catchup_iso

    # Swap the catchup row's composition to something non-Ottilie: a
    # single long task instead of the 7/wk + 2/wk bundle. Keep the
    # total minutes identical so delivered totals still reconcile.
    for row in sched["daily_schedule"]:
        if row["date"] == catchup_iso:
            original_min = int(row["duration_min"])
            row["tasks"] = {"Housework": original_min}
            row["duration_min"] = original_min
            break
    # Rebuild task_occurrence_counts so Check 11 stays consistent for
    # this synthetic edit (not the subject of this test).
    rebuilt: dict = {}
    for row in sched["daily_schedule"]:
        for nm in row.get("tasks", {}):
            rebuilt[nm] = rebuilt.get(nm, 0) + 1
    sched["task_occurrence_counts"] = rebuilt
    sched["catchup_day_min"] = int(original_min)

    r = cross_check(form, sched)
    c14 = _check(r, 14)
    assert c14.passed, f"Check 14 should pass on non-Ottilie catchup: {c14.actual}"


def test_check14_fails_when_catchup_is_a_spurious_marker() -> None:
    """A catchup row that contributes zero minutes yet leaves total == target is spurious.

    To construct this we zero the catchup row's contribution *and*
    top up a non-catchup day by the same amount so the total still
    reconciles to target. Check 14 then asks: does removing the
    catchup from the total miss target? With catchup_min = 0,
    total − 0 == target, so the answer is "no" — the catchup label
    is a no-op and the check must fail.
    """
    form = make_ottilie_extracted_form()
    sched = copy.deepcopy(make_ottilie_schedule_dict())
    sched.pop("config", None)
    catchup_iso = sched["catchup_date"]
    assert catchup_iso

    catch_row = next(r for r in sched["daily_schedule"] if r["date"] == catchup_iso)
    catch_min = int(catch_row["duration_min"])
    topup_row = next(r for r in sched["daily_schedule"] if r["date"] != catchup_iso)
    topup_row["duration_min"] = int(topup_row["duration_min"]) + catch_min
    topup_row["tasks"]["Medication"] = int(topup_row["tasks"].get("Medication", 0)) + catch_min
    catch_row["tasks"] = {}
    catch_row["duration_min"] = 0
    sched["catchup_day_min"] = 0

    r = cross_check(form, sched)
    c14 = _check(r, 14)
    assert not c14.passed, f"Expected Check 14 to flag spurious catchup: {c14.actual}"
    assert "no reconciliation-relevant minutes" in c14.detail


def test_validate_summary_reports_actual_mismatches() -> None:
    """Validation summary surfaces real variances, not assumed frequency counts.

    Intentionally perturb one task's minutes on a single day without
    updating ``duration_min`` on that day. Check 10's per-day duration
    invariant must catch the mismatch with the offending date in its
    detail, and Check 5's per-task sub_check for that task must show
    a variance shifted by exactly the perturbation amount — neither
    check may silently ignore the edit or re-derive numbers from a
    frequency template.
    """
    form = make_ottilie_extracted_form()

    # Baseline variances for the pristine Ottilie schedule so we can
    # assert *deltas* rather than absolute values (which depend on
    # per-task tolerance math).
    pristine = cross_check(form, make_ottilie_schedule_dict())
    c7_pristine = _check(pristine, 7)
    hw_pristine = next(
        s for s in c7_pristine.sub_checks if s.task_name == "Housework"
    )

    sched = copy.deepcopy(make_ottilie_schedule_dict())
    target_task = "Housework"
    target_date = None
    for row in sched["daily_schedule"]:
        if target_task in row.get("tasks", {}):
            target_date = row["date"]
            row["tasks"][target_task] = int(row["tasks"][target_task]) + 7
            break
    assert target_date is not None
    r = cross_check(form, sched)
    c7 = _check(r, 7)
    hw_sub = next(s for s in c7.sub_checks if s.task_name == target_task)
    # Scheduled minutes for Housework must be exactly +7 over pristine.
    assert hw_sub.scheduled_min == hw_pristine.scheduled_min + 7
    assert hw_sub.variance == hw_pristine.variance + 7
    # Check 12 (duration invariant) must fail and name the exact date.
    c10_dur = _check(r, 12)
    assert not c10_dur.passed
    assert target_date in str(c10_dur.detail)


# --- ASM 120 policy (checks 6–8 — do not flip math banner) ----------------------


def test_asm120_check6_shopping_exceeds_iadl_monthly_cap() -> None:
    """60 min × 7/wk × 4.3 ≫ 300 min ASM cap → policy check 8 fails + warning."""
    tasks = [{"task_name": "Shopping for Food/Meds", "min_per_day": 60, "days_per_week": 7}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched)
    assert r.all_passed
    c6 = _check(r, 8)
    assert not c6.passed
    assert "Shopping for Food/Meds" in str(c6.detail)
    assert "300" in str(c6.detail)
    assert any("ASM 120 IADL" in w for w in r.warnings)


def test_asm120_check7_non_travel_exceeds_ehhs_combined_cap() -> None:
    """~200 hr/mo authorized on one non-travel ADL exceeds 179.59 hr EHHS floor."""
    tasks = [{"task_name": "Bathing", "min_per_day": 400, "days_per_week": 7}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched)
    c7 = _check(r, 9)
    assert not c7.passed
    assert "EHHS approval required (DCH-1785)." in c7.detail
    assert EHHS_THRESHOLD_MIN == 10775
    assert any("EHHS approval required (DCH-1785)" in w for w in r.warnings)
    assert r.all_passed


def test_asm120_check7_travel_excluded_from_ehhs_combined_sum() -> None:
    """Travel line minutes do not count toward the EHHS combined threshold."""
    tasks = [
        {"task_name": "Housework", "min_per_day": 349, "days_per_week": 7},
        {"task_name": "Travel For Shopping", "min_per_day": 50, "days_per_week": 7},
    ]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched)
    c7 = _check(r, 9)
    nt_only = compute_monthly_minutes_rounded(349, 7)
    assert c7.actual == nt_only
    assert c7.passed
    with_travel = nt_only + compute_monthly_minutes_rounded(50, 7)
    assert with_travel > EHHS_THRESHOLD_MIN  # naive total would violate EHHS
    assert r.all_passed


def test_asm120_check6_uncapped_taking_medication_skipped() -> None:
    """Taking Medication has no per-line IADL cap in ASM 120 § maximum table."""
    tasks = [{"task_name": "Taking Medication", "min_per_day": 60, "days_per_week": 7}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched)
    c6 = _check(r, 8)
    assert c6.passed
    assert str(c6.detail) == "all listed IADLs within caps"


def test_asm120_check8_warns_when_shared_living_without_separate_docs() -> None:
    """Authorized Housework exceeds prorated IADL ceiling when shared living is flagged."""
    # 16×5 → 344 min/mo rounds (within 360 Housework cap; well above half-cap 180).
    tasks = [{"task_name": "Housework", "min_per_day": 16, "days_per_week": 5}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched, shared_living=True, iadl_separate_documented=False)
    assert r.all_passed
    c8 = _check(r, 10)
    assert not c8.passed
    assert any("Housework" in w and "180" in w and "344" in w for w in r.warnings)
    assert (
        "shared-living proration would cap at 180 min/mo" in "".join(r.warnings)
    )


def test_asm120_check8_silent_when_separate_documented_even_if_shared_living() -> None:
    # High-enough authorization that would warn under shared living if not waived.
    tasks = [{"task_name": "Housework", "min_per_day": 6, "days_per_week": 7}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched, shared_living=True, iadl_separate_documented=True)
    assert _check(r, 10).passed
    assert not any("shared-living proration would cap at" in w for w in r.warnings)


def test_asm120_check8_silent_when_not_shared_living() -> None:
    tasks = [{"task_name": "Housework", "min_per_day": 6, "days_per_week": 7}]
    form, sched = _minimal_form_and_schedule(tasks)
    r = cross_check(form, sched, shared_living=False, iadl_separate_documented=False)
    assert _check(r, 10).passed
    assert not any("shared-living proration would cap at" in w for w in r.warnings)
