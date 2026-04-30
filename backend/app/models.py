"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PreferredWindowIn(BaseModel):
    weekday_start: str = Field(..., description="e.g. '7:00 AM'")
    weekend_start: str = Field(..., description="e.g. '12:00 PM'")
    # Optional per-day overrides (full names Monday..Sunday). Merged onto the
    # weekday/weekend template when building default ScheduleConfig.start_time_by_weekday.
    start_time_by_weekday: dict[str, str] = Field(default_factory=dict)


class TaskPlacementIn(BaseModel):
    """One task's placement inside a ScheduleConfig — see calculate.TaskPlacement."""

    model_config = ConfigDict(extra="ignore")

    task_name: str
    min_per_day: int = Field(..., ge=0)
    days_per_week: int = Field(..., ge=0, le=7)
    selected_weekdays: list[str] = Field(default_factory=list)
    selected_dates: list[str] = Field(default_factory=list)
    excluded_dates: list[str] = Field(default_factory=list)
    placement_fallback: bool = False
    preferred_weekdays: list[str] = Field(default_factory=list)
    preferred_dates: list[str] = Field(default_factory=list)
    placement_overrides: list[dict[str, Any]] = Field(default_factory=list)
    preference_unspecified: bool = True


class ScheduleConfigIn(BaseModel):
    """Editor payload for placing tasks on days + picking per-weekday start times."""

    tasks: list[TaskPlacementIn] = Field(default_factory=list)
    start_time_by_weekday: dict[str, str] = Field(default_factory=dict)


class RerunBody(BaseModel):
    preferred_window: PreferredWindowIn
    use_llm: bool = False
    # Free-text constraints / preferences for Claude when use_llm is true
    # (e.g. "No visits Friday", "Prefer mornings Mon–Wed").
    llm_notes: str | None = Field(default=None, max_length=12_000)
    # Optional service month override. If omitted, the server falls back to
    # the auth-date in the PDF, and finally to the current UTC year/month.
    year: int | None = Field(default=None, ge=1900, le=9999)
    month: int | None = Field(default=None, ge=1, le=12)
    # Optional ScheduleConfig. When omitted, the backend regenerates the
    # weekend-weighted defaults from the re-OCR'd tasks.
    config: ScheduleConfigIn | None = None


class PatchPlanBody(BaseModel):
    schedule: dict[str, Any]
    notes: str | None = None


class PreviewParseOut(BaseModel):
    """PDF extraction only — no DB or schedule (upload preflight)."""

    client_name: str = ""
    client_id: str = ""
    pay_rate: float = 0.0
    monthly_total_amount: float = 0.0
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class PreviewCalibrateBody(BaseModel):
    """Dry-run calibration for upload preflight — no persistence."""

    tasks: list[dict[str, Any]] = Field(default_factory=list)
    availability: dict[str, Any] = Field(default_factory=dict)
    year: int | None = Field(default=None, ge=1900, le=9999)
    month: int | None = Field(default=None, ge=1, le=12)
    pay_rate: float = 0.0


class PreviewCalibrateOut(BaseModel):
    weekly_authorized_minutes: int = 0
    weekly_base_capacity_minutes: int = 0
    weekly_extension_minutes: int = 0
    weekly_total_capacity_minutes: int = 0
    headroom_percent: int = 0
    schedule_ok: bool = True
    day_capacity: dict[str, Any] | None = None
    availability_error: str | None = None
    authorization_capacity_error: dict[str, Any] | None = None


class PatchPlanConfigBody(BaseModel):
    """Body for PATCH /plans/{v}/config — runs the editor's round-trip."""

    config: ScheduleConfigIn
    # When True, replace task placement with a fresh ``default_config_for`` seed
    # (availability-driven); merge ``start_time_by_weekday`` from ``config``.
    reseed_placement: bool = False


class ValidateScheduleBody(BaseModel):
    """Dry-run: run cross_check without persisting."""

    schedule: dict[str, Any]


class SubCheckOut(BaseModel):
    task_name: str
    auth_min: int
    scheduled_min: int
    variance: int
    passed: bool
    informational: bool = False


class CheckOut(BaseModel):
    name: str
    passed: bool
    expected: Any
    actual: Any
    tolerance: str
    detail: str = ""
    number: int = 0
    sub_checks: list[SubCheckOut] = Field(default_factory=list)


class ValidationReportOut(BaseModel):
    checks: list[CheckOut]
    all_passed: bool
    summary: str
    pass_count: int = 0
    fail_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    validation_status: str = "INVALID"
    delivered_minutes: int = 0
    authorized_minutes: int = 0
    billable_minutes: int = 0


class LatestPlanSummary(BaseModel):
    version: int
    monthly_amount: float
    validation_passed: bool
    created_at: datetime
    has_source_pdf: bool = False


class ClientListItem(BaseModel):
    client_id: str
    client_name: str
    latest_plan: LatestPlanSummary | None
    updated_at: datetime


class PlanResponse(BaseModel):
    id: int
    client_row_id: int
    version: int
    weekly_minutes: int
    monthly_minutes: float
    monthly_amount: float
    mdhhs_form_amount: float = 0.0
    delivered_minutes: int = 0
    delivered_amount: float = 0.0
    billable_minutes: int = 0
    billable_amount: float = 0.0
    # The calendar month this plan is calibrated against. Zero means legacy
    # plans written before the column existed — the UI treats 0 as "unknown".
    year: int = 0
    month: int = 0
    schedule: dict[str, Any]
    tasks: list[dict[str, Any]]
    # ScheduleConfig used to generate this schedule (editor source-of-truth).
    # Empty dict on legacy plans pre-dating the editor.
    config: dict[str, Any] = Field(default_factory=dict)
    validation: ValidationReportOut
    validation_passed: bool
    source_pdf_path: str
    xlsx_path: str
    pdf_path: str
    notes: str | None
    created_at: datetime


class ClientResponse(BaseModel):
    id: int
    client_id: str
    client_name: str
    case_number: str
    county: str
    asw_name: str
    asw_email: str
    asw_phone: str
    pay_rate: float
    provider_name: str = ""
    auth_date: str = ""
    created_at: datetime
    updated_at: datetime
    # Full day names -> {"earliest": "7:00 AM", "latest": "8:00 PM"}
    availability: dict[str, Any] = Field(default_factory=dict)
    shared_living: bool = False  # other adults reside; ASM 120 IADL proration may apply
    iadl_separate_documented: bool = False  # IADLs done separately → no proration


class PatchClientBody(BaseModel):
    availability: dict[str, Any]
    shared_living: bool | None = None
    iadl_separate_documented: bool | None = None


class PlanPreviewBody(BaseModel):
    """Dry-run availability against a plan — no DB or artifact writes."""

    availability: dict[str, Any]


class UploadResponse(BaseModel):
    client: ClientResponse
    plan: PlanResponse


class ClientDetailResponse(BaseModel):
    client: ClientResponse
    plans: list[PlanResponse]


class HealthResponse(BaseModel):
    status: str
    version: str
