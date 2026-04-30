"""Tests for redact module."""

import sys
from pathlib import Path

# backend/ on path for `import app.*`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.extract import ExtractedForm
from app.redact import assert_no_phi, redact_for_api


def test_redact_produces_clean_payload() -> None:
    form = ExtractedForm(
        client_name="Jane Doe",
        client_id="1234567890",
        county_name="Wayne",
        case_number="ABC-99-0001",
        asw_name="Worker Person",
        asw_email="asw@example.org",
        asw_phone="313-555-1234",
        auth_date="04/15/2025",
        provider_name="ACME Home Health",
        pay_rate=18.5,
        tasks=[
            {
                "task_name": "Bathing",
                "min_per_day": 16,
                "days_per_week": 7,
                "monthly_time_str": "08:02",
                "monthly_amount": 148.62,
            }
        ],
        monthly_total_time_str="08:02",
        monthly_total_amount=148.62,
    )
    payload = redact_for_api(form)
    assert set(payload.keys()) == {"pay_rate", "tasks"}
    assert payload["pay_rate"] == 18.5
    assert payload["tasks"] == [
        {"task_name": "Bathing", "min_per_day": 16, "days_per_week": 7}
    ]
    assert "client_name" not in payload
    assert "monthly_time_str" not in str(payload)
    assert_no_phi(payload)


def test_redact_with_preferred_schedule() -> None:
    form = ExtractedForm(
        pay_rate=20.0,
        tasks=[{"task_name": "Laundry", "min_per_day": 30, "days_per_week": 3}],
    )
    sched = {"weekdays_morning": True}
    payload = redact_for_api(form, preferred_schedule=sched)
    assert payload["preferred_schedule"] == sched
    assert_no_phi(payload)


def test_assert_no_phi_rejects_forbidden_key() -> None:
    with pytest.raises(ValueError, match="Forbidden key"):
        assert_no_phi({"pay_rate": 1.0, "client_id": "x"})


def test_assert_no_phi_rejects_phone_pattern() -> None:
    with pytest.raises(ValueError, match="phone"):
        assert_no_phi({"pay_rate": 1.0, "note": "call 555-123-4567 today"})


def test_assert_no_phi_rejects_email_pattern() -> None:
    with pytest.raises(ValueError, match="email"):
        assert_no_phi({"pay_rate": 1.0, "hint": "x@y.com"})


def test_assert_no_phi_rejects_long_numeric_string() -> None:
    with pytest.raises(ValueError, match="numeric identifier"):
        assert_no_phi({"pay_rate": 1.0, "ref": "1234567"})
