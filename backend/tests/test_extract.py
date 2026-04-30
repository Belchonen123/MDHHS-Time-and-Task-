"""Tests for backend/app/extract.py.

Covers:
    * test_ottilie_smith_pdf: end-to-end extraction against the real
      MDHHS-6064-P fixture. Skipped automatically if the fixture PDF is
      missing so the rest of the suite still runs; to run it, drop
      ``backend/tests/fixtures/ottilie_smith.pdf`` in place.
    * test_group_n_not_name: regression for Bug A (regex named group
      rename from ``name`` to ``n``). Proves ``_parse_task_from_line``
      returns a populated dict without raising ``IndexError``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.calculate import compute_task_amount
from app.extract import (
    _compute_task_amounts,
    _extract_monthly_totals,
    _parse_task_from_line,
    extract_from_pdf,
)


FIXTURE = Path(__file__).parent / "fixtures" / "ottilie_smith.pdf"
LATISHA_FIXTURE = Path(__file__).parent / "fixtures" / "latisha_avery.pdf"


def test_latisha_golden_text_paired_headers() -> None:
    """MDHHS-6064-P Section 1/2 layout: values on the line below paired labels."""
    from app.extract import (
        _extract_paired_fields,
        _extract_pay_rate_loose,
        _extract_provider_name,
    )

    text = """
    Section 1 — Client Information
    Client Name Client ID Number
    Latisha Avery 33241250
    County Name Case Number
    82-WAYNE 721343-1
    Provider Pay Rate Section
    Provider Name
    Alegria Home Care
    Adult Services Worker (ASW) Phone
    734-555-0100
    Pay Rate — Home Care Worker
    $27.00 Per Hour — Home Care Workers
    Date
    04/28/2026
    Adult Services Worker (ASW) Name
    M Montgomery
    """
    paired = _extract_paired_fields(text)
    assert paired["client_name"] == "Latisha Avery"
    assert paired["client_id"] == "33241250"
    assert paired["case_number"] == "721343-1"
    assert paired["county_name"] == "82-WAYNE"
    assert paired["auth_date"] == "04/28/2026"

    assert paired.get("provider_name") == "Alegria Home Care"
    assert _extract_pay_rate_loose(text) == pytest.approx(27.0, abs=0.01)


@pytest.mark.skipif(
    not LATISHA_FIXTURE.exists(),
    reason="Drop backend/tests/fixtures/latisha_avery.pdf to run full PDF extraction.",
)
def test_latisha_avery_pdf_when_present() -> None:
    form = extract_from_pdf(LATISHA_FIXTURE)

    assert form.client_name == "Latisha Avery"
    assert form.client_id == "33241250"
    assert form.county_name == "82-WAYNE"
    assert form.case_number == "721343-1"
    assert form.provider_name == "Alegria Home Care"
    assert form.asw_name == "M Montgomery"
    assert form.pay_rate == pytest.approx(27.00, abs=0.01)
    assert form.auth_date == "04/28/2026"


def test_normalize_hhmm_does_not_mangle_currency_tokens() -> None:
    """Dollar mounts like ``216.72`` must stay numeric — not ``216:72``."""
    from app.extract import _normalize_token_hhmm

    assert _normalize_token_hhmm("216.72") == "216.72"
    assert _normalize_token_hhmm("70.48") == "70:48"


def test_extract_monthly_totals_accepts_ocr_dot_in_roll_up_time() -> None:
    """OCR often emits ``70.48`` instead of ``70:48`` for the Monthly Total Time row."""
    txt = "Monthly Total Time 70.48 $1,911.78"
    mt, tot = _extract_monthly_totals(txt)
    assert mt == "70:48"
    assert abs(tot - 1911.78) < 0.01




@pytest.mark.skipif(
    not FIXTURE.exists(),
    reason=(
        "Drop backend/tests/fixtures/ottilie_smith.pdf to run the full "
        "extraction gate (see prompt: VERIFICATION GATE)."
    ),
)
def test_ottilie_smith_pdf() -> None:
    form = extract_from_pdf(FIXTURE)

    assert form.client_name == "Ottilie Smith"
    assert form.client_id == "80738972"
    assert form.county_name.upper().startswith("82")
    assert "WAYNE" in form.county_name.upper()
    assert form.case_number == "350195-2"

    assert form.asw_name == "K Abdelkhaliq"
    assert form.asw_phone == "313-407-4457"
    assert form.asw_email == "AbdelkhaliqK@michigan.gov"

    assert form.provider_name == "Alegria Home Care"
    assert form.pay_rate == 27.00

    assert len(form.tasks) == 12
    assert form.monthly_total_time_str == "70:48"
    assert form.monthly_total_amount == pytest.approx(1911.78, abs=0.01)
    assert sum(t["monthly_amount"] for t in form.tasks) == pytest.approx(
        1911.78, abs=0.05
    )

    bathing = next(t for t in form.tasks if t["task_name"] == "Bathing")
    assert bathing["min_per_day"] == 16
    assert bathing["days_per_week"] == 7
    assert bathing["monthly_amount"] == pytest.approx(216.72, abs=0.02)


def test_group_n_not_name() -> None:
    """Regression for Bug A: regex group is ``n`` and ``_parse_task_from_line``
    must read it as ``n`` without raising IndexError. A successful dict return
    proves the rename is consistent across regex and parser.
    """
    line = "Bathing 00:16 7 days per week 8:02 $216.72"
    parsed = _parse_task_from_line(line)
    assert isinstance(parsed, dict), "regex/parser mismatch — Bug A has regressed"
    assert parsed["task_name"] == "Bathing"
    assert parsed["min_per_day"] == 16
    assert parsed["days_per_week"] == 7
    assert parsed["monthly_time_str"] == "8:02"
    assert parsed["monthly_amount"] == pytest.approx(216.72, abs=0.01)


def test_compute_task_amounts_fills_missing() -> None:
    """Belt-and-suspenders: sub-$1 / missing amounts recomputed like the form."""
    # Expected line $ uses unrounded mpd × dpw × 4.3 × pay / 60 (cents HALF_EVEN).
    tasks = [
        {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7, "monthly_amount": 0.0},
        {"task_name": "Dressing", "min_per_day": 10, "days_per_week": 7, "monthly_amount": 0.5},
        {"task_name": "Mobility", "min_per_day": 5, "days_per_week": 7, "monthly_amount": 99.99},
    ]
    result = _compute_task_amounts(tasks, pay_rate=27.00)
    bathing = next(t for t in result if t["task_name"] == "Bathing")
    dressing = next(t for t in result if t["task_name"] == "Dressing")
    mobility = next(t for t in result if t["task_name"] == "Mobility")

    assert bathing["monthly_amount"] == pytest.approx(
        compute_task_amount(16, 7, 27.0), abs=0.01
    )
    assert dressing["monthly_amount"] == pytest.approx(
        compute_task_amount(10, 7, 27.0), abs=0.01
    )
    assert mobility["monthly_amount"] == 99.99


def test_compute_task_amounts_leaves_reasonable_values_alone() -> None:
    tasks = [
        {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7, "monthly_amount": 216.72},
    ]
    result = _compute_task_amounts(tasks, pay_rate=27.00)
    assert result[0]["monthly_amount"] == 216.72


def test_eating_feeding_line_extracts() -> None:
    line = "Eating/Feeding 00:15 7 days per week 53:45 $537.50"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Eating/Feeding"
    assert parsed["min_per_day"] == 15
    assert parsed["days_per_week"] == 7
    assert parsed["monthly_amount"] == pytest.approx(537.50, abs=0.01)


def test_eating_alias_resolves_to_eating_feeding() -> None:
    line = "Eating 00:15 7 53:45 $537.50"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Eating/Feeding"


def test_travel_for_laundry_line_canonical() -> None:
    line = "Travel for Laundry 00:30 1 02:09 $32.25"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Travel For Laundry"
    assert parsed["min_per_day"] == 30


def test_wound_care_line_extracts() -> None:
    line = "Wound Care 00:20 5 07:10 $107.50"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Wound Care"


def test_meal_prep_and_cleanup_alias() -> None:
    line = "Meal Preparation and Cleanup 00:45 7 13:41 $589.73"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Meal Preparation"


def test_shopping_alias_single_word_maps_to_canonical_task() -> None:
    """OCR often emits only ``Shopping …`` instead of ``Shopping for Food/Meds …``."""
    line = "Shopping 00:30 7 days per week 9:06 $407.82"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Shopping for Food/Meds"


def test_travel_for_shopping_still_not_mapped_to_iadl_shopping() -> None:
    """Long alternation prefixes must beat the short ``Shopping`` alias."""
    line = "Travel For Shopping 00:20 2 07:54 $357.76"
    parsed = _parse_task_from_line(line)
    assert parsed is not None
    assert parsed["task_name"] == "Travel For Shopping"
