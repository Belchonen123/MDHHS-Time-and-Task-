"""Tests for the PDF plan-of-care renderer.

Focus area: deviation-day callout must describe the **actual** catchup
row from ``daily_schedule`` rather than the Ottilie-shaped hardcoded
"7/wk + 2/wk bundle" copy. The heavy PDF integration surface (layout,
fonts, page breaks) is exercised by the router-level smoke tests; this
file pins the narrative helper directly.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_spec = importlib.util.spec_from_file_location(
    "_test_calc_for_pdf", _ROOT / "tests" / "test_calculate.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
OTTILIE_TASKS = _mod.OTTILIE_TASKS
OTTILIE_PREFERRED = _mod.OTTILIE_PREFERRED

from decimal import ROUND_HALF_UP, Decimal

from app.build_pdf import _deviation_callout_text, build_pdf
from app.calculate import (
    WEEKS_PER_MONTH,
    compute_mdhhs_form_amount,
    compute_weekly_budget,
    generate_schedule,
    round_half_up,
)


def _ottilie_schedule_dict() -> dict:
    return generate_schedule(OTTILIE_TASKS, 27.0, 2026, 4, OTTILIE_PREFERRED).as_dict()


def test_deviation_callout_no_longer_names_housework_3wk_errand_2wk() -> None:
    """The callout must NOT include the old Ottilie-specific frequency phrasing.

    Regression guard: the prior text hardcoded
    "carries the daily (7/wk) and errand (2/wk) tasks only — Housework
    (3/wk) is already fully met". That narrative is wrong for any
    plan whose catchup has a different composition. The generalized
    helper describes whatever is actually on the catchup row, never
    the frequency buckets.
    """
    sd = _ottilie_schedule_dict()
    text = _deviation_callout_text(sd)
    assert "7/wk" not in text
    assert "2/wk" not in text
    assert "3/wk" not in text
    assert "fully met" not in text
    assert "excluded" not in text


def test_deviation_callout_lists_actual_catchup_tasks() -> None:
    """Callout must enumerate the real tasks on the catchup row, in insertion order."""
    sd = _ottilie_schedule_dict()
    catchup_iso = sd["catchup_date"]
    assert catchup_iso
    catch_entry = next(d for d in sd["daily_schedule"] if d["date"] == catchup_iso)
    task_names = [nm for nm, v in catch_entry["tasks"].items() if v]
    text = _deviation_callout_text(sd)
    for nm in task_names:
        assert nm in text, f"expected {nm!r} in callout for date {catchup_iso}: {text}"
    # Duration also mentioned as an absolute minutes value.
    assert str(int(catch_entry["duration_min"])) in text
    # Date, in human form, somewhere in the text.
    assert "April" in text and "2026" in text


def test_deviation_callout_handles_single_task_catchup() -> None:
    """A user-configured override with a single task renders cleanly."""
    sd = copy.deepcopy(_ottilie_schedule_dict())
    catchup_iso = sd["catchup_date"]
    for row in sd["daily_schedule"]:
        if row["date"] == catchup_iso:
            row["tasks"] = {"Meal Preparation": 50}
            row["duration_min"] = 50
            break
    text = _deviation_callout_text(sd)
    assert "Meal Preparation" in text
    assert "50 minutes" in text


def test_deviation_callout_handles_two_task_catchup_grammar() -> None:
    """Two-task catchup must read "A and B", not "A, and B"."""
    sd = copy.deepcopy(_ottilie_schedule_dict())
    catchup_iso = sd["catchup_date"]
    for row in sd["daily_schedule"]:
        if row["date"] == catchup_iso:
            row["tasks"] = {"Bathing": 16, "Medication": 2}
            row["duration_min"] = 18
            break
    text = _deviation_callout_text(sd)
    assert "Bathing and Medication" in text or "Medication and Bathing" in text
    assert ", and" not in text


def test_rendered_pdf_contains_hours_minutes_companion(tmp_path: Path) -> None:
    """Durations render as fixed-width ``HH:MM`` (matching MDHHS-6064-P).

    Every duration cell routes through ``_fmt_hm`` — Summary totals, Weekly
    Pattern grid, Schedule page ``Dur``, etc. Monthly total for Ottilie aligns
    to ``70:48`` for 4,248 minutes.
    """
    import pdfplumber

    from app.build_pdf import build_pdf
    from app.calculate import generate_schedule
    from app.extract import ExtractedForm
    from app.validate import cross_check

    form = ExtractedForm(
        client_name="Ottilie Smith",
        client_id="80738972",
        county_name="82-WAYNE",
        case_number="350195-2",
        asw_name="K Abdelkhaliq",
        asw_phone="313-407-4457",
        asw_email="AbdelkhaliqK@michigan.gov",
        auth_date="04/01/2026",
        provider_name="Alegria Home Care",
        pay_rate=27.00,
        tasks=list(OTTILIE_TASKS),
        monthly_total_time_str="70:48",
        monthly_total_amount=1911.78,
    )
    cs = generate_schedule(list(OTTILIE_TASKS), 27.0, 2026, 4, OTTILIE_PREFERRED)
    report = cross_check(form, cs)
    out = tmp_path / "ottilie_april_2026.pdf"
    build_pdf(form, cs.as_dict(), report, out)
    assert out.exists() and out.stat().st_size > 0

    text_parts: list[str] = []
    with pdfplumber.open(out) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    full_text = "\n".join(text_parts)

    # Monthly total 4,248 min = 70:48 HH:MM
    assert "70:48" in full_text, (
        f"Expected '70:48' in rendered PDF text; got first 500 chars: "
        f"{full_text[:500]!r}"
    )


def test_pdf_no_dollar_drift_on_half_minute_tasks(tmp_path: Path) -> None:
    """Section 1 TOTAL $ follows ``mdhhs_form_amount`` — not naive half-up aggregate $."""
    import pdfplumber

    from app.extract import ExtractedForm
    from app.validate import cross_check

    tasks = [
        {
            "task_name": "Meal Preparation",
            "min_per_day": 25,
            "days_per_week": 7,
            "monthly_amount": 0.0,
        },
    ]
    pay = 27.0
    cs = generate_schedule(tasks, pay, 2026, 4)
    per_line = compute_mdhhs_form_amount(tasks, pay)
    # Naive aggregate minutes use round-half-away-from-zero at the month level —
    # e.g. 752.5 → 753, which inflates payroll vs the MDHHS line math.
    agg_min = round_half_up(compute_weekly_budget(tasks) * WEEKS_PER_MONTH)
    naive_aggregate_dollars = float(
        (Decimal(agg_min) * Decimal(str(pay)) / Decimal("60")).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
    )
    assert abs(per_line - naive_aggregate_dollars) > 0.01, (
        "fixture should differ aggregate $ vs form Σ for this prompt"
    )

    form = ExtractedForm(
        client_name="Half-Minute Drift",
        pay_rate=pay,
        tasks=list(tasks),
        monthly_total_amount=per_line,
    )
    report = cross_check(form, cs.as_dict())
    out = tmp_path / "half_minute_drift.pdf"
    build_pdf(form, cs.as_dict(), report, out)

    text_parts: list[str] = []
    with pdfplumber.open(out) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    full_text = "\n".join(text_parts)

    want = f"${per_line:,.2f}"
    assert want in full_text, f"expected per-line total {want!r} in PDF text"

    bad = f"${naive_aggregate_dollars:,.2f}"
    assert bad not in full_text, (
        f"aggregate-style total {bad!r} should not appear as Section 1 TOTAL"
    )


def test_deviation_callout_handles_empty_catchup_row() -> None:
    """A zero-minute catchup row doesn't crash and surfaces it as empty."""
    sd = copy.deepcopy(_ottilie_schedule_dict())
    catchup_iso = sd["catchup_date"]
    for row in sd["daily_schedule"]:
        if row["date"] == catchup_iso:
            row["tasks"] = {}
            row["duration_min"] = 0
            break
    text = _deviation_callout_text(sd)
    assert "0 minutes" in text
    assert "empty catchup row" in text.lower()
