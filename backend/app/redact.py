"""Strip or mask protected health information (PHI) from extracted content."""

from __future__ import annotations

import re
from typing import Any

from .extract import ExtractedForm

_FORBIDDEN_KEYS = frozenset(
    {
        "client_name",
        "client_id",
        "case_number",
        "address",
        "county_name",
        "asw_name",
        "asw_email",
        "asw_phone",
        "provider_name",
        "dob",
        "ssn",
        "phone",
        "email",
    }
)

_PHONE_RE = re.compile(r"\d{3}-\d{3}-\d{4}")
_EMAIL_RE = re.compile(r"[\w.]+@[\w.]+")
_LONG_DIGITS_RE = re.compile(r"^\d{7,}$")


def redact_for_api(
    extracted_form: ExtractedForm,
    *,
    preferred_schedule: Any = None,
) -> dict[str, Any]:
    """
    Build a JSON-serializable dict with only non-PHI fields safe to send to an
    API (e.g. Claude). Does not include names, IDs, contact info, or dates of
    auth.

    ``preferred_schedule`` is optional; pass it when the user supplies a
    schedule object separately from PDF extraction.
    """
    payload: dict[str, Any] = {
        "pay_rate": extracted_form.pay_rate,
        "tasks": [
            {
                "task_name": t.get("task_name", ""),
                "min_per_day": int(t.get("min_per_day", 0)),
                "days_per_week": int(t.get("days_per_week", 0)),
            }
            for t in extracted_form.tasks
        ],
    }
    if preferred_schedule is not None:
        payload["preferred_schedule"] = preferred_schedule
    return payload


def assert_no_phi(payload: dict[str, Any]) -> None:
    """
    Recursively verify that *payload* contains no obvious PHI keys or values.
    Intended as a final guard immediately before an external API call.
    """

    def _check_string(s: str, where: str) -> None:
        if _PHONE_RE.search(s):
            raise ValueError(f"Possible phone number in value at {where!r}")
        if _EMAIL_RE.search(s):
            raise ValueError(f"Possible email in value at {where!r}")
        if _LONG_DIGITS_RE.match(s.strip()):
            raise ValueError(f"Possible numeric identifier in value at {where!r}")

    def _check_scalar(value: Any, where: str) -> None:
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, str):
            _check_string(value, where)
            return
        if isinstance(value, int) and not isinstance(value, bool):
            if _LONG_DIGITS_RE.match(str(value)):
                raise ValueError(f"Possible numeric identifier in value at {where!r}")
            return

    def _walk(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in _FORBIDDEN_KEYS:
                    raise ValueError(f"Forbidden key {key!r} at {path!r}")
                loc = f"{path}.{key}" if path else key
                if isinstance(val, (dict, list)):
                    _walk(val, loc)
                else:
                    _check_scalar(val, loc)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                loc = f"{path}[{i}]"
                if isinstance(item, (dict, list)):
                    _walk(item, loc)
                else:
                    _check_scalar(item, loc)
        else:
            _check_scalar(obj, path)

    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    _walk(payload, "")
