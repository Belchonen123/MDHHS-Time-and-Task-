"""FastAPI routes for the mdhhs-poc-builder API."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .build_pdf import build_pdf
from .build_xlsx import build_xlsx
from .calculate import (
    CalibrationError,
    DayCapacityExceeded,
    ScheduleConfig,
    TaskPlacement,
    assert_worker_availability_sane,
    authorization_exceeds_weekly_worker_capacity,
    default_config_for,
    default_worker_availability,
    generate_schedule,
    parse_worker_availability,
    preferred_window_from_worker_availability,
    preflight_headroom_percent,
    visit_weekdays_union_from_tasks,
    weekly_worker_capacity_preflight,
)
from .db import Client, Plan, SessionLocal, STORAGE_DIR
from .extract import ExtractedForm, extract_from_pdf
from .llm_refine import refine_extracted_form
from .models import (
    ArtifactPayload,
    CheckOut,
    ClientDetailResponse,
    ClientListItem,
    ClientResponse,
    HealthResponse,
    LatestPlanSummary,
    PatchClientBody,
    PatchPlanBody,
    PatchPlanConfigBody,
    PlanPreviewBody,
    PlanResponse,
    PreviewCalibrateBody,
    PreviewCalibrateOut,
    PreviewParseOut,
    RerunBody,
    ScheduleConfigIn,
    SubCheckOut,
    UploadResponse,
    ValidateScheduleBody,
    ValidationReportOut,
)
from .validate import (
    ValidationReport,
    cross_check,
    validation_report_from_dict,
    validation_report_to_dict,
)

router = APIRouter(prefix="" if os.getenv("VERCEL") else "/api")
logger = logging.getLogger(__name__)


def get_db() -> Any:
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()


Db = Annotated[Session, Depends(get_db)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def safe_dir_name(client_id: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", (client_id or "").strip())[:120]
    return s or "client"


def default_preferred_window(pay_rate: float) -> dict[str, Any]:
    return {
        "weekday_start": "1:00 PM",
        "weekend_start": "1:00 PM",
        "pay_rate": float(pay_rate),
    }


def _schedule_year_month_from_form(ex: ExtractedForm) -> tuple[int, int]:
    """Use authorization date when present; otherwise UTC "today"."""
    s = (ex.auth_date or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        month_n = int(m.group(1))
        _day = int(m.group(2))
        y = int(m.group(3))
        if y < 100:
            y = 2000 + y if y < 50 else 1900 + y
        if 1 <= month_n <= 12:
            return y, month_n
    now = datetime.now(timezone.utc)
    return now.year, now.month


def _current_year_month() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    return now.year, now.month


def _validated_year_month(year: int | None, month: int | None) -> tuple[int, int]:
    cy, cm = _current_year_month()
    y = int(year) if year else cy
    m = int(month) if month else cm
    if not (1900 <= y <= 9999):
        raise HTTPException(status_code=400, detail=f"Invalid year: {y}")
    if not (1 <= m <= 12):
        raise HTTPException(status_code=400, detail=f"Invalid month: {m}")
    return y, m


def _schedule_time_window(pay_rate: float) -> dict[str, str]:
    p = default_preferred_window(pay_rate)
    return {
        "weekday_start": str(p["weekday_start"]),
        "weekend_start": str(p["weekend_start"]),
    }


def _extraction_is_empty(ex: ExtractedForm) -> bool:
    has_id = bool((ex.client_id or "").strip())
    has_name = bool((ex.client_name or "").strip())
    has_tasks = bool(ex.tasks)
    has_rate = float(ex.pay_rate or 0.0) > 0.0
    return not (has_id or has_name or has_tasks or has_rate)


def extracted_to_client_fields(ex: ExtractedForm) -> dict[str, Any]:
    cid = (ex.client_id or "").strip()
    return {
        "client_id": cid,
        "client_name": ex.client_name or "",
        "case_number": ex.case_number or "",
        "county": ex.county_name or "",
        "asw_name": ex.asw_name or "",
        "asw_email": ex.asw_email or "",
        "asw_phone": ex.asw_phone or "",
        "pay_rate": float(ex.pay_rate or 0.0),
        "provider_name": (ex.provider_name or "").strip(),
        "auth_date": (ex.auth_date or "").strip(),
    }


def _ensure_public_client_slug(db: Session, client: Client) -> None:
    """Persisted slug for URLs when the PDF omits MDHHS client ID (still merges on '')."""
    if (client.client_id or "").strip():
        return
    slug = f"draft_{client.id}"
    client.client_id = slug
    db.flush()


def upsert_client(db: Session, ex: ExtractedForm) -> Client:
    fields = extracted_to_client_fields(ex)
    cid = fields["client_id"]
    row = db.scalars(select(Client).where(Client.client_id == cid)).first()
    if row:
        row.client_name = fields["client_name"]
        row.case_number = fields["case_number"]
        row.county = fields["county"]
        row.asw_name = fields["asw_name"]
        row.asw_email = fields["asw_email"]
        row.asw_phone = fields["asw_phone"]
        row.pay_rate = fields["pay_rate"]
        row.provider_name = fields["provider_name"]
        row.auth_date = fields["auth_date"]
        row.updated_at = _now()
        db.flush()
        _ensure_public_client_slug(db, row)
        return row
    row = Client(**fields, created_at=_now(), updated_at=_now())
    db.add(row)
    db.flush()
    _ensure_public_client_slug(db, row)
    return row


def rebuild_extracted_form(client: Client, tasks: list[dict[str, Any]]) -> ExtractedForm:
    total = sum(float(t.get("monthly_amount", 0) or 0) for t in tasks)
    return ExtractedForm(
        client_name=client.client_name,
        client_id=client.client_id,
        county_name=client.county,
        case_number=client.case_number,
        asw_name=client.asw_name,
        asw_email=client.asw_email,
        asw_phone=client.asw_phone,
        auth_date=str(getattr(client, "auth_date", "") or ""),
        provider_name=str(getattr(client, "provider_name", "") or ""),
        pay_rate=client.pay_rate,
        tasks=tasks,
        monthly_total_time_str="",
        monthly_total_amount=total,
    )


def _schedule_config_from_body(
    body_config: ScheduleConfigIn | None,
) -> ScheduleConfig | None:
    """Translate a Pydantic ScheduleConfigIn to a calculate.ScheduleConfig.

    Returns ``None`` when nothing was supplied so the caller falls back to
    the default generator.
    """
    if body_config is None:
        return None
    return ScheduleConfig.from_dict(body_config.model_dump())


def _editor_prior_from_patch(
    body: ScheduleConfigIn,
    auth_tasks: list[dict[str, Any]],
) -> ScheduleConfig:
    """Editor ``selected_weekdays`` / ``selected_dates`` encode user intent only."""
    rows_by_name = {str(r.task_name).strip(): r for r in body.tasks}
    out_tasks: list[TaskPlacement] = []
    for t in auth_tasks:
        name = str(t.get("task_name", "") or "").strip()
        if not name:
            continue
        row = rows_by_name.get(name)
        if row is not None:
            pref_w = [str(x) for x in row.selected_weekdays]
            pref_d = [str(x) for x in row.selected_dates]
            unspecified = False
        else:
            pref_w, pref_d, unspecified = [], [], True
        out_tasks.append(
            TaskPlacement(
                task_name=name,
                min_per_day=int(t.get("min_per_day") or 0),
                days_per_week=int(t.get("days_per_week") or 0),
                selected_weekdays=[],
                selected_dates=[],
                preferred_weekdays=pref_w,
                preferred_dates=pref_d,
                placement_overrides=[],
                preference_unspecified=unspecified,
            )
        )
    times_cfg = ScheduleConfig.from_dict(
        {
            "tasks": [],
            "start_time_by_weekday": dict(body.start_time_by_weekday or {}),
        }
    )
    return ScheduleConfig(tasks=out_tasks, start_time_by_weekday=times_cfg.start_time_by_weekday)


def _plan_record_from_pipeline(
    *,
    client: Client,
    version: int,
    year: int,
    month: int,
    schedule: dict[str, Any],
    report: Any,
    rel_source: str,
    rel_xlsx: str,
    rel_pdf: str,
    tasks_json: str,
    schedule_json: str,
    config_json: str = "{}",
    notes: str | None = None,
) -> Plan:
    vdict = validation_report_to_dict(report)
    validation_json = json.dumps(
        vdict, ensure_ascii=False, allow_nan=False, default=str
    )
    return Plan(
        client_row_id=client.id,
        version=version,
        year=int(year),
        month=int(month),
        weekly_minutes=int(schedule.get("weekly_minutes", 0)),
        monthly_minutes=float(schedule.get("monthly_minutes", 0)),
        monthly_amount=float(schedule.get("monthly_amount", 0)),
        schedule_json=schedule_json,
        tasks_json=tasks_json,
        config_json=config_json,
        validation_json=validation_json,
        validation_passed=bool(vdict.get("all_passed")),
        source_pdf_path=rel_source,
        xlsx_path=rel_xlsx,
        pdf_path=rel_pdf,
        weekly_schedule_path="",
        notes=notes,
        created_at=_now(),
    )


def _client_availability_parsed(c: Client) -> dict[str, dict[str, Any]]:
    raw = getattr(c, "availability_json", None) or "{}"
    try:
        j: Any = json.loads(raw)
    except json.JSONDecodeError:
        return default_worker_availability()
    return parse_worker_availability(j)


def _client_cross_check(
    form: ExtractedForm,
    sched: dict[str, Any],
    client: Client | None,
) -> ValidationReport:
    """Run validate.cross_check with ASM 120 client flags from the ORM row."""
    if client is None:
        return cross_check(form, sched)
    return cross_check(
        form,
        sched,
        shared_living=bool(getattr(client, "shared_living", False)),
        iadl_separate_documented=bool(getattr(client, "iadl_separate_documented", False)),
    )


def orm_client_to_pydantic(c: Client) -> ClientResponse:
    avail_map = _client_availability_parsed(c)
    return ClientResponse(
        id=c.id,
        client_id=c.client_id,
        client_name=c.client_name,
        case_number=c.case_number,
        county=c.county,
        asw_name=c.asw_name,
        asw_email=c.asw_email,
        asw_phone=c.asw_phone,
        pay_rate=c.pay_rate,
        provider_name=str(getattr(c, "provider_name", "") or ""),
        auth_date=str(getattr(c, "auth_date", "") or ""),
        created_at=c.created_at,
        updated_at=c.updated_at,
        availability=dict(avail_map),
        shared_living=bool(getattr(c, "shared_living", False)),
        iadl_separate_documented=bool(getattr(c, "iadl_separate_documented", False)),
    )


def _validation_report_for_plan(p: Plan) -> ValidationReport:
    try:
        vraw = json.loads(p.validation_json or "{}")
    except json.JSONDecodeError:
        vraw = {}
    if vraw and vraw.get("checks"):
        try:
            return validation_report_from_dict(vraw)
        except (KeyError, TypeError, ValueError) as e:
            return validation_report_from_dict(
                {
                    "checks": [],
                    "all_passed": False,
                    "summary": f"Could not read stored validation report ({type(e).__name__}: {e}).",
                }
            )
    return validation_report_from_dict({"checks": [], "all_passed": True, "summary": ""})


def orm_plan_to_pydantic(p: Plan) -> PlanResponse:
    try:
        sched = json.loads(p.schedule_json or "{}")
    except json.JSONDecodeError:
        sched = {}
    if not isinstance(sched, dict):
        sched = {}
    try:
        tasks = json.loads(p.tasks_json or "[]")
    except json.JSONDecodeError:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []
    # Prefer the dedicated config column, fall back to the value embedded in
    # the schedule_json payload (older plans wrote it inside "config" there).
    config_dict: dict[str, Any] = {}
    config_raw = getattr(p, "config_json", None) or "{}"
    try:
        maybe = json.loads(config_raw)
        if isinstance(maybe, dict):
            config_dict = maybe
    except json.JSONDecodeError:
        config_dict = {}
    if not config_dict:
        sc = sched.get("config")
        if isinstance(sc, dict):
            config_dict = sc

    vr = _validation_report_for_plan(p)
    vout = _report_to_out(vr)
    try:
        form_amt = float(sched.get("mdhhs_form_amount") or 0.0)
    except (TypeError, ValueError):
        form_amt = 0.0
    y = int(p.year or 0)
    m = int(p.month or 0)
    if not (y and m):
        try:
            y_s = int(sched.get("year") or 0)
            m_s = int(sched.get("month") or 0)
        except (TypeError, ValueError):
            y_s, m_s = 0, 0
        if y_s and m_s:
            y, m = y_s, m_s
    return PlanResponse(
        id=p.id,
        client_row_id=p.client_row_id,
        version=p.version,
        weekly_minutes=p.weekly_minutes,
        monthly_minutes=p.monthly_minutes,
        monthly_amount=p.monthly_amount,
        mdhhs_form_amount=form_amt,
        delivered_minutes=int(sched.get("delivered_minutes") or 0),
        delivered_amount=float(sched.get("delivered_amount") or 0.0),
        billable_minutes=int(sched.get("billable_minutes") or 0),
        billable_amount=float(sched.get("billable_amount") or 0.0),
        year=y,
        month=m,
        schedule=sched,
        tasks=tasks,
        config=config_dict,
        validation=vout,
        validation_passed=p.validation_passed,
        source_pdf_path=p.source_pdf_path,
        xlsx_path=p.xlsx_path,
        pdf_path=p.pdf_path,
        notes=p.notes,
        created_at=p.created_at,
    )


def _latest_plan_for_client(db: Session, client_row_id: int) -> Plan | None:
    return db.scalars(
        select(Plan)
        .where(Plan.client_row_id == client_row_id)
        .order_by(Plan.version.desc())
        .limit(1)
    ).first()


def _resolve_plan_stored_config(
    p: Plan,
    tasks: list[dict[str, Any]],
    worker_avail: dict[str, dict[str, Any]],
    y: int,
    m: int,
) -> ScheduleConfig:
    config_dict: dict[str, Any] = {}
    config_raw = getattr(p, "config_json", None) or "{}"
    try:
        maybe = json.loads(config_raw)
        if isinstance(maybe, dict):
            config_dict = maybe
    except json.JSONDecodeError:
        config_dict = {}
    if not config_dict:
        try:
            sched = json.loads(p.schedule_json or "{}")
        except json.JSONDecodeError:
            sched = {}
        sc = sched.get("config") if isinstance(sched, dict) else None
        if isinstance(sc, dict):
            config_dict = sc
    cfg_tasks = (
        config_dict.get("tasks") if isinstance(config_dict.get("tasks"), list) else []
    )
    if cfg_tasks:
        prior = ScheduleConfig.from_dict(config_dict)
        pw = preferred_window_from_worker_availability(worker_avail)
        return default_config_for(
            tasks,
            y,
            m,
            pw,
            worker_availability=worker_avail,
            prior=prior,
        )
    pw = preferred_window_from_worker_availability(worker_avail)
    return default_config_for(tasks, y, m, pw, worker_availability=worker_avail)


def _calibrate_plan_with_availability(
    c: Client,
    p: Plan,
    worker_avail: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], ValidationReport, ExtractedForm]:
    tasks_raw = json.loads(p.tasks_json or "[]")
    if not isinstance(tasks_raw, list):
        tasks_raw = []
    tasks = [dict(t) for t in tasks_raw if isinstance(t, dict)]
    form = rebuild_extracted_form(c, tasks)
    y = int(p.year or 0)
    m = int(p.month or 0)
    if not (y and m):
        try:
            sched_embed = json.loads(p.schedule_json or "{}")
        except json.JSONDecodeError:
            sched_embed = {}
        y = y or int(sched_embed.get("year") or 0)
        m = m or int(sched_embed.get("month") or 0)
    if not (y and m):
        raise HTTPException(
            status_code=400,
            detail="Plan is missing year/month — re-run the schedule first.",
        )

    cfg = _resolve_plan_stored_config(p, tasks, worker_avail, y, m)
    try:
        cal = generate_schedule(
            tasks,
            float(c.pay_rate or 0.0),
            y,
            m,
            _schedule_time_window(c.pay_rate or 0.0),
            config=cfg,
            worker_availability=worker_avail,
        )
        sched = cal.as_dict()
        report = _client_cross_check(form, sched, c)
    except DayCapacityExceeded as e:
        raise HTTPException(status_code=422, detail=e.http_detail()) from e
    except (CalibrationError, ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Schedule or calibration error: {type(e).__name__}: {e}",
        ) from e
    cfg_out: dict[str, Any] = cal.config.to_dict() if cal.config is not None else {}
    return sched, cfg_out, report, form


def _plan_response_from_live_calibration(
    *,
    p: Plan,
    sched: dict[str, Any],
    report: ValidationReport,
    config_dict: dict[str, Any],
    strip_artifact_paths: bool,
) -> PlanResponse:
    vout = _report_to_out(report)
    try:
        form_amt = float(sched.get("mdhhs_form_amount") or 0.0)
    except (TypeError, ValueError):
        form_amt = 0.0
    try:
        tasks = json.loads(p.tasks_json or "[]")
    except json.JSONDecodeError:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []
    y = int(p.year or 0)
    m = int(p.month or 0)
    if not (y and m):
        try:
            y_s = int(sched.get("year") or 0)
            m_s = int(sched.get("month") or 0)
        except (TypeError, ValueError):
            y_s, m_s = 0, 0
        if y_s and m_s:
            y, m = y_s, m_s
    xp = "" if strip_artifact_paths else (p.xlsx_path or "")
    pp = "" if strip_artifact_paths else (p.pdf_path or "")
    return PlanResponse(
        id=p.id,
        client_row_id=p.client_row_id,
        version=p.version,
        weekly_minutes=int(sched.get("weekly_minutes", 0)),
        monthly_minutes=float(sched.get("monthly_minutes", 0)),
        monthly_amount=float(sched.get("monthly_amount", 0)),
        mdhhs_form_amount=form_amt,
        delivered_minutes=int(sched.get("delivered_minutes") or 0),
        delivered_amount=float(sched.get("delivered_amount") or 0.0),
        billable_minutes=int(sched.get("billable_minutes") or 0),
        billable_amount=float(sched.get("billable_amount") or 0.0),
        year=y,
        month=m,
        schedule=sched,
        tasks=tasks,
        config=config_dict,
        validation=vout,
        validation_passed=bool(report.all_passed),
        source_pdf_path=p.source_pdf_path,
        xlsx_path=xp,
        pdf_path=pp,
        notes=p.notes,
        created_at=p.created_at,
    )


def _next_version(db: Session, client: Client) -> int:
    m = db.execute(
        select(func.coalesce(func.max(Plan.version), 0)).where(Plan.client_row_id == client.id)
    ).scalar()
    return int(m or 0) + 1


def _abs_storage(rel: str) -> Path:
    base = Path(STORAGE_DIR).resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid storage path") from e
    return p


def _first_existing_source_pdf_for_client(
    db: Session, client_row_id: int
) -> tuple[str, Path] | None:
    """Latest plan row (by version) that points at a PDF still on disk, if any."""
    plans = db.scalars(
        select(Plan)
        .where(Plan.client_row_id == client_row_id)
        .order_by(Plan.version.desc())
    ).all()
    for plan_row in plans:
        rel = (plan_row.source_pdf_path or "").strip()
        if not rel:
            continue
        abs_p = _abs_storage(rel)
        if abs_p.is_file():
            return (rel, abs_p)
    return None


def _emit_outputs(
    form: ExtractedForm,
    schedule: dict[str, Any],
    report: Any,
    xlsx_path: Path,
    pdf_path: Path,
) -> None:
    build_xlsx(form, schedule, report, xlsx_path)
    build_pdf(form, schedule, report, pdf_path)


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _artifact_payload(path: Path, filename: str, media_type: str) -> ArtifactPayload | None:
    try:
        if not path.is_file():
            return None
        return ArtifactPayload(
            filename=filename,
            media_type=media_type,
            base64=base64.b64encode(path.read_bytes()).decode("ascii"),
        )
    except OSError:
        logger.exception("Failed to attach generated artifact payload: %s", path)
        return None


def _vercel_upload_artifacts(
    client_id: str,
    version: int,
    xlsx_path: Path,
    pdf_path: Path,
) -> dict[str, ArtifactPayload]:
    """Attach small generated files to upload responses so live downloads survive /tmp resets."""
    if not os.getenv("VERCEL"):
        return {}
    artifacts: dict[str, ArtifactPayload] = {}
    xlsx = _artifact_payload(
        xlsx_path,
        f"{client_id}-v{version}-plan.xlsx",
        XLSX_MEDIA_TYPE,
    )
    if xlsx is not None:
        artifacts["xlsx"] = xlsx
        artifacts["weekly"] = ArtifactPayload(
            filename=f"{client_id}-v{version}-weekly.xlsx",
            media_type=xlsx.media_type,
            base64=xlsx.base64,
        )
    pdf = _artifact_payload(pdf_path, f"{client_id}-v{version}-plan.pdf", "application/pdf")
    if pdf is not None:
        artifacts["pdf"] = pdf
    return artifacts


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.post("/clients/preview-parse", response_model=PreviewParseOut)
async def preview_parse_pdf(file: UploadFile = File(...)) -> PreviewParseOut:
    """Extract task table from a PDF only — no database write or calibration."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A PDF file is required")
    data = await file.read()
    tmp = Path(STORAGE_DIR) / f"_preview_{uuid.uuid4().hex}.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        try:
            ex = extract_from_pdf(str(tmp))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse PDF: {type(e).__name__}: {e}",
            ) from e
    finally:
        if tmp.exists():
            tmp.unlink()

    ex = refine_extracted_form(ex, use_llm=False)
    if _extraction_is_empty(ex):
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not read any text from this PDF — tried native extraction "
                "and OCR. If it's a scan, try a higher-resolution copy; otherwise "
                "use the original searchable PDF from the MDHHS portal."
            ),
        )
    return PreviewParseOut(
        client_name=ex.client_name or "",
        client_id=(ex.client_id or "").strip(),
        pay_rate=float(ex.pay_rate or 0.0),
        monthly_total_amount=float(ex.monthly_total_amount or 0.0),
        tasks=[dict(t) for t in ex.tasks],
    )


@router.post("/clients/preview-calibrate", response_model=PreviewCalibrateOut)
def preview_calibrate(body: PreviewCalibrateBody) -> PreviewCalibrateOut:
    """Dry-run default placement + schedule generation — no DB or artifacts."""
    tasks = [dict(t) for t in body.tasks if isinstance(t, dict)]
    avail = parse_worker_availability(body.availability)
    try:
        assert_worker_availability_sane(avail)
    except CalibrationError as e:
        return PreviewCalibrateOut(
            schedule_ok=False,
            availability_error=str(e),
        )

    auth_cap = authorization_exceeds_weekly_worker_capacity(avail, tasks)
    if auth_cap:
        visit_union = visit_weekdays_union_from_tasks(tasks)
        base_c, ext_c, tot_c = weekly_worker_capacity_preflight(avail, visit_union)
        need = sum(
            int(t.get("min_per_day", 0) or 0) * int(t.get("days_per_week", 0) or 0)
            for t in tasks
        )
        hr = preflight_headroom_percent(need, tot_c)
        return PreviewCalibrateOut(
            weekly_authorized_minutes=need,
            weekly_base_capacity_minutes=base_c,
            weekly_extension_minutes=ext_c,
            weekly_total_capacity_minutes=tot_c,
            headroom_percent=hr,
            schedule_ok=False,
            authorization_capacity_error=auth_cap,
        )

    cy, cm = _current_year_month()
    y = int(body.year) if body.year is not None else cy
    m = int(body.month) if body.month is not None else cm
    y, m = _validated_year_month(y, m)

    visit_union = visit_weekdays_union_from_tasks(tasks)
    base_c, ext_c, tot_c = weekly_worker_capacity_preflight(avail, visit_union)
    need = sum(
        int(t.get("min_per_day", 0) or 0) * int(t.get("days_per_week", 0) or 0)
        for t in tasks
    )
    hr = preflight_headroom_percent(need, tot_c)

    if not tasks:
        return PreviewCalibrateOut(
            weekly_authorized_minutes=0,
            weekly_base_capacity_minutes=base_c,
            weekly_extension_minutes=ext_c,
            weekly_total_capacity_minutes=tot_c,
            headroom_percent=hr,
            schedule_ok=True,
        )

    pw = preferred_window_from_worker_availability(avail)
    pr = float(body.pay_rate or 0.0)
    try:
        cfg = default_config_for(
            tasks,
            y,
            m,
            pw,
            worker_availability=avail,
        )
        generate_schedule(
            tasks,
            pr,
            y,
            m,
            _schedule_time_window(pr),
            config=cfg,
            worker_availability=avail,
        )
    except DayCapacityExceeded as e:
        return PreviewCalibrateOut(
            weekly_authorized_minutes=need,
            weekly_base_capacity_minutes=base_c,
            weekly_extension_minutes=ext_c,
            weekly_total_capacity_minutes=tot_c,
            headroom_percent=hr,
            schedule_ok=False,
            day_capacity=e.http_detail(),
        )
    except (CalibrationError, ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        return PreviewCalibrateOut(
            weekly_authorized_minutes=need,
            weekly_base_capacity_minutes=base_c,
            weekly_extension_minutes=ext_c,
            weekly_total_capacity_minutes=tot_c,
            headroom_percent=hr,
            schedule_ok=False,
            availability_error=f"{type(e).__name__}: {e}",
        )

    return PreviewCalibrateOut(
        weekly_authorized_minutes=need,
        weekly_base_capacity_minutes=base_c,
        weekly_extension_minutes=ext_c,
        weekly_total_capacity_minutes=tot_c,
        headroom_percent=hr,
        schedule_ok=True,
    )


@router.post("/clients/upload", response_model=UploadResponse)
async def upload_client_pdf(
    db: Db,
    file: UploadFile = File(...),
    # Explicit service-month targets. Optional so curl smoke tests keep
    # working without them; defaults to current UTC year/month.
    year: int | None = Form(default=None),
    month: int | None = Form(default=None),
    # Optional JSON map: Monday..Sunday -> { "earliest": "7:00 AM", "latest": "8:00 PM" }
    availability_json: str | None = Form(default=None),
) -> UploadResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="A PDF file is required")
    y, m = _validated_year_month(year, month)
    data = await file.read()
    tmp = Path(STORAGE_DIR) / f"_tmp_{uuid.uuid4().hex}.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        try:
            ex = extract_from_pdf(str(tmp))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse PDF: {type(e).__name__}: {e}",
            ) from e
    finally:
        if tmp.exists():
            tmp.unlink()

    ex = refine_extracted_form(ex, use_llm=False)
    if _extraction_is_empty(ex):
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not read any text from this PDF — tried native extraction "
                "and OCR. If it's a scan, try a higher-resolution copy; otherwise "
                "use the original searchable PDF from the MDHHS portal."
            ),
        )
    client = upsert_client(db, ex)
    if availability_json and str(availability_json).strip():
        try:
            raw_avail: Any = json.loads(availability_json)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail="Invalid availability JSON",
            ) from e
        avail = parse_worker_availability(raw_avail)
    else:
        existing_raw = getattr(client, "availability_json", None) or "{}"
        try:
            existing_obj: Any = json.loads(existing_raw)
        except json.JSONDecodeError:
            existing_obj = {}
        if isinstance(existing_obj, dict) and existing_obj:
            avail = parse_worker_availability(existing_obj)
        else:
            avail = default_worker_availability()
    try:
        assert_worker_availability_sane(avail)
    except CalibrationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    auth_cap = authorization_exceeds_weekly_worker_capacity(
        avail, [dict(t) for t in ex.tasks]
    )
    if auth_cap:
        raise HTTPException(status_code=422, detail=auth_cap) from None
    client.availability_json = json.dumps(avail, ensure_ascii=False)
    db.flush()
    sdir = safe_dir_name(client.client_id)
    ver = _next_version(db, client)
    base = STORAGE_DIR / sdir
    base.mkdir(parents=True, exist_ok=True)
    rel_source = f"{sdir}/source_v{ver}.pdf"
    abs_source = STORAGE_DIR / rel_source
    abs_source.write_bytes(data)

    try:
        # Fresh upload → default placement + per-day start times from worker availability.
        pw = preferred_window_from_worker_availability(avail)
        cfg = default_config_for(
            [dict(t) for t in ex.tasks],
            y,
            m,
            pw,
            worker_availability=avail,
        )
        cal = generate_schedule(
            [dict(t) for t in ex.tasks],
            float(ex.pay_rate),
            y,
            m,
            pw,
            config=cfg,
            worker_availability=avail,
        )
        sched = cal.as_dict()
        report = _client_cross_check(ex, sched, client)
    except DayCapacityExceeded as e:
        raise HTTPException(status_code=422, detail=e.http_detail()) from e
    except (CalibrationError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Schedule or validation error: {type(e).__name__}: {e}",
        ) from e
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to build schedule: {e}"
        ) from e

    rel_xlsx = f"{sdir}/plan_v{ver}.xlsx"
    rel_pdf = f"{sdir}/plan_v{ver}.pdf"
    abs_x = STORAGE_DIR / rel_xlsx
    abs_p = STORAGE_DIR / rel_pdf
    try:
        _emit_outputs(ex, sched, report, abs_x, abs_p)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to write plan files: {e}"
        ) from e
    except Exception as e:
        logger.exception("build_xlsx/build_pdf failed during upload")
        raise HTTPException(
            status_code=500,
            detail=f"Could not build Excel/PDF: {type(e).__name__}: {e}",
        ) from e

    try:
        tasks_json = json.dumps(
            ex.tasks, ensure_ascii=False, allow_nan=False, default=str
        )
        schedule_json = json.dumps(
            sched, ensure_ascii=False, allow_nan=False, default=str
        )
        config_json = json.dumps(
            cfg.to_dict(), ensure_ascii=False, allow_nan=False, default=str
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=500, detail=f"Could not serialize plan JSON: {e}"
        ) from e

    plan = _plan_record_from_pipeline(
        client=client,
        version=ver,
        year=y,
        month=m,
        schedule=sched,
        report=report,
        rel_source=rel_source,
        rel_xlsx=rel_xlsx,
        rel_pdf=rel_pdf,
        tasks_json=tasks_json,
        schedule_json=schedule_json,
        config_json=config_json,
    )
    db.add(plan)
    try:
        db.commit()
    except Exception as e:
        logger.exception("Database commit failed on upload")
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Database error: {type(e).__name__}: {e}"
        ) from e
    db.refresh(client)
    db.refresh(plan)
    return UploadResponse(
        client=orm_client_to_pydantic(client),
        plan=orm_plan_to_pydantic(plan),
        artifacts=_vercel_upload_artifacts(client.client_id, plan.version, abs_x, abs_p),
    )


@router.get("/clients", response_model=list[ClientListItem])
def list_clients(db: Db) -> list[ClientListItem]:
    clients = list(db.scalars(select(Client).order_by(Client.updated_at.desc())).all())
    out: list[ClientListItem] = []
    for c in clients:
        p = db.scalars(
            select(Plan)
            .where(Plan.client_row_id == c.id)
            .order_by(Plan.version.desc())
            .limit(1)
        ).first()
        latest = None
        if p:
            latest = LatestPlanSummary(
                version=p.version,
                monthly_amount=p.monthly_amount,
                validation_passed=p.validation_passed,
                created_at=p.created_at,
                has_source_pdf=bool((p.source_pdf_path or "").strip()),
            )
        out.append(
            ClientListItem(
                client_id=c.client_id,
                client_name=c.client_name,
                latest_plan=latest,
                updated_at=c.updated_at,
            )
        )
    return out


def _get_client_by_string_id(db: Session, client_id: str) -> Client:
    c = db.scalars(select(Client).where(Client.client_id == client_id)).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return c


def _get_plan(client: Client, version: int, db: Session) -> Plan:
    p = db.scalars(
        select(Plan).where(Plan.client_row_id == client.id, Plan.version == version)
    ).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return p


def _latest_tasks_for_client(db: Session, client_row_id: int) -> list[dict[str, Any]] | None:
    p = db.scalars(
        select(Plan)
        .where(Plan.client_row_id == client_row_id)
        .order_by(Plan.version.desc())
        .limit(1)
    ).first()
    if p is None:
        return None
    try:
        raw: Any = json.loads(p.tasks_json or "[]")
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, list) else None


@router.get("/clients/{client_id}", response_model=ClientDetailResponse)
def get_client_detail(client_id: str, db: Db) -> ClientDetailResponse:
    c = _get_client_by_string_id(db, client_id)
    plans = list(
        db.scalars(
            select(Plan).where(Plan.client_row_id == c.id).order_by(Plan.version.asc())
        ).all()
    )
    return ClientDetailResponse(
        client=orm_client_to_pydantic(c),
        plans=[orm_plan_to_pydantic(p) for p in plans],
    )


@router.patch("/clients/{client_id}")
def patch_client_profile(
    client_id: str,
    body: PatchClientBody,
    db: Db,
    regenerate: bool = Query(False),
) -> ClientResponse | PlanResponse:
    """Update client-level worker availability (earliest/latest per weekday).

    With ``?regenerate=true``, also re-calibrates the **latest** saved plan for
    this client, re-emits xlsx/pdf artifacts, and returns that
    :class:`PlanResponse` (same shape as ``PATCH …/config``).
    """
    c = _get_client_by_string_id(db, client_id)
    merged = parse_worker_availability(body.availability)
    try:
        assert_worker_availability_sane(merged)
    except CalibrationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    auth_cap = authorization_exceeds_weekly_worker_capacity(
        merged, _latest_tasks_for_client(db, c.id)
    )
    if auth_cap:
        raise HTTPException(status_code=422, detail=auth_cap) from None

    if body.shared_living is not None:
        c.shared_living = bool(body.shared_living)
    if body.iadl_separate_documented is not None:
        c.iadl_separate_documented = bool(body.iadl_separate_documented)

    p: Plan | None = None
    sched: dict[str, Any] = {}
    cfg_out: dict[str, Any] = {}
    report: ValidationReport | None = None
    form: ExtractedForm | None = None
    if regenerate:
        p = _latest_plan_for_client(db, c.id)
        if p is None:
            raise HTTPException(
                status_code=400,
                detail="No saved plan to regenerate — upload a PDF first.",
            )
        sched, cfg_out, report, form = _calibrate_plan_with_availability(c, p, merged)
        assert report is not None and form is not None

    c.availability_json = json.dumps(merged, ensure_ascii=False)
    c.updated_at = _now()

    if regenerate and p is not None and report is not None and form is not None:
        if p.xlsx_path and p.pdf_path:
            try:
                abs_x = _abs_storage(p.xlsx_path)
                abs_pdf = _abs_storage(p.pdf_path)
                _emit_outputs(form, sched, report, abs_x, abs_pdf)
            except (OSError, HTTPException):
                logger.exception(
                    "build_xlsx/build_pdf failed during availability regenerate"
                )

        p.schedule_json = json.dumps(sched, ensure_ascii=False, default=str)
        p.config_json = json.dumps(cfg_out, ensure_ascii=False, default=str)
        p.validation_json = json.dumps(
            validation_report_to_dict(report), ensure_ascii=False, default=str
        )
        p.validation_passed = bool(report.all_passed)
        p.weekly_minutes = int(sched.get("weekly_minutes", 0))
        p.monthly_minutes = float(sched.get("monthly_minutes", 0))
        p.monthly_amount = float(sched.get("monthly_amount", 0))

    db.commit()
    db.refresh(c)
    if regenerate and p is not None:
        db.refresh(p)
        return orm_plan_to_pydantic(p)
    return orm_client_to_pydantic(c)


@router.get(
    "/clients/{client_id}/plans/{version}",
    response_model=PlanResponse,
)
def get_plan_version(client_id: str, version: int, db: Db) -> PlanResponse:
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    return orm_plan_to_pydantic(p)


@router.patch(
    "/clients/{client_id}/plans/{version}",
    response_model=PlanResponse,
)
def patch_plan(
    client_id: str,
    version: int,
    body: PatchPlanBody,
    db: Db,
) -> PlanResponse:
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    tasks = json.loads(p.tasks_json or "[]")
    form = rebuild_extracted_form(c, tasks)
    sched = body.schedule
    report = _client_cross_check(form, sched, c)
    abs_x = _abs_storage(p.xlsx_path)
    abs_pdf = _abs_storage(p.pdf_path)
    _emit_outputs(form, sched, report, abs_x, abs_pdf)
    p.schedule_json = json.dumps(sched, ensure_ascii=False)
    p.validation_json = json.dumps(validation_report_to_dict(report), ensure_ascii=False)
    p.validation_passed = bool(report.all_passed)
    p.weekly_minutes = int(sched.get("weekly_minutes", 0))
    p.monthly_minutes = float(sched.get("monthly_minutes", 0))
    p.monthly_amount = float(sched.get("monthly_amount", 0))
    if body.notes is not None:
        p.notes = body.notes
    db.commit()
    db.refresh(p)
    return orm_plan_to_pydantic(p)


@router.patch(
    "/clients/{client_id}/plans/{version}/config",
    response_model=PlanResponse,
)
def patch_plan_config(
    client_id: str,
    version: int,
    body: PatchPlanConfigBody,
    db: Db,
) -> PlanResponse:
    """Update the ScheduleConfig on an existing plan, regenerate, revalidate.

    The editor calls this (debounced) on every checkbox / time change so the
    reconciliation banner updates live without a fresh PDF upload.
    """
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    tasks = json.loads(p.tasks_json or "[]")
    form = rebuild_extracted_form(c, tasks)

    # Year/month come from the row (never from the editor payload) — the
    # ScheduleConfig is orthogonal to the calendar.
    y = int(p.year or 0)
    m = int(p.month or 0)
    if not (y and m):
        # Legacy plans — try embedded schedule, then current month.
        try:
            sched_embed = json.loads(p.schedule_json or "{}")
        except json.JSONDecodeError:
            sched_embed = {}
        y = y or int(sched_embed.get("year") or 0)
        m = m or int(sched_embed.get("month") or 0)
    if not (y and m):
        raise HTTPException(
            status_code=400,
            detail="Plan is missing year/month — re-run the schedule first.",
        )

    if body.reseed_placement:
        wv = _client_availability_parsed(c)
        pw = preferred_window_from_worker_availability(wv)
        cfg = default_config_for(
            [dict(t) for t in tasks],
            y,
            m,
            pw,
            worker_availability=wv,
            prior=None,
        )
        incoming_times = body.config.start_time_by_weekday or {}
        if isinstance(incoming_times, dict):
            for k, v in incoming_times.items():
                if (
                    isinstance(k, str)
                    and k in cfg.start_time_by_weekday
                    and isinstance(v, str)
                    and v.strip()
                ):
                    cfg.start_time_by_weekday[k] = v.strip()
    else:
        wv = _client_availability_parsed(c)
        pw = preferred_window_from_worker_availability(wv)
        prior = _editor_prior_from_patch(body.config, [dict(t) for t in tasks])
        cfg = default_config_for(
            [dict(t) for t in tasks],
            y,
            m,
            pw,
            worker_availability=wv,
            prior=prior,
        )
        incoming_times = body.config.start_time_by_weekday or {}
        if isinstance(incoming_times, dict):
            for k, v in incoming_times.items():
                if (
                    isinstance(k, str)
                    and k in cfg.start_time_by_weekday
                    and isinstance(v, str)
                    and v.strip()
                ):
                    cfg.start_time_by_weekday[k] = v.strip()
    try:
        cal = generate_schedule(
            [dict(t) for t in tasks],
            float(c.pay_rate or 0.0),
            y,
            m,
            _schedule_time_window(c.pay_rate or 0.0),
            config=cfg,
            worker_availability=_client_availability_parsed(c),
        )
        sched = cal.as_dict()
        report = _client_cross_check(form, sched, c)
    except DayCapacityExceeded as e:
        raise HTTPException(status_code=422, detail=e.http_detail()) from e
    except (CalibrationError, ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Schedule or validation error: {type(e).__name__}: {e}",
        ) from e

    # Re-emit artifacts at the same paths so downloads stay in sync with the
    # live schedule. Non-fatal if artifact paths haven't been allocated yet.
    if p.xlsx_path and p.pdf_path:
        try:
            abs_x = _abs_storage(p.xlsx_path)
            abs_pdf = _abs_storage(p.pdf_path)
            _emit_outputs(form, sched, report, abs_x, abs_pdf)
        except (OSError, HTTPException):
            logger.exception("build_xlsx/build_pdf failed during config patch")

    p.schedule_json = json.dumps(sched, ensure_ascii=False, default=str)
    cfg_dict = cal.config.to_dict() if cal.config is not None else {}
    p.config_json = json.dumps(cfg_dict, ensure_ascii=False, default=str)
    p.validation_json = json.dumps(
        validation_report_to_dict(report), ensure_ascii=False, default=str
    )
    p.validation_passed = bool(report.all_passed)
    p.weekly_minutes = int(sched.get("weekly_minutes", 0))
    p.monthly_minutes = float(sched.get("monthly_minutes", 0))
    p.monthly_amount = float(sched.get("monthly_amount", 0))
    db.commit()
    db.refresh(p)
    return orm_plan_to_pydantic(p)


@router.post(
    "/clients/{client_id}/plans/{version}/preview",
    response_model=PlanResponse,
)
def preview_plan_with_availability(
    client_id: str,
    version: int,
    body: PlanPreviewBody,
    db: Db,
) -> PlanResponse:
    """Dry-run worker availability against a plan: calibrate in memory only.

    Does not write the database or build xlsx/pdf. Response matches
    :class:`PlanResponse` with artifact paths cleared.
    """
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    merged = parse_worker_availability(body.availability)
    try:
        assert_worker_availability_sane(merged)
    except CalibrationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    auth_cap = authorization_exceeds_weekly_worker_capacity(
        merged, _latest_tasks_for_client(db, c.id)
    )
    if auth_cap:
        raise HTTPException(status_code=422, detail=auth_cap) from None
    sched, cfg_out, report, _form = _calibrate_plan_with_availability(c, p, merged)
    return _plan_response_from_live_calibration(
        p=p,
        sched=sched,
        report=report,
        config_dict=cfg_out,
        strip_artifact_paths=True,
    )


def _subcheck_from_raw(s: Any) -> SubCheckOut | None:
    if not isinstance(s, dict):
        return None
    try:
        return SubCheckOut(
            task_name=str(s.get("task_name", "")),
            auth_min=int(s.get("auth_min", 0) or 0),
            scheduled_min=int(s.get("scheduled_min", 0) or 0),
            variance=int(s.get("variance", 0) or 0),
            passed=bool(s.get("passed", False)),
            informational=bool(s.get("informational", False)),
        )
    except (TypeError, ValueError):
        return None


def _report_to_out(report: Any) -> ValidationReportOut:
    """Build API validation payload — tolerant of partial/legacy JSON."""
    try:
        d: dict[str, Any] = validation_report_to_dict(report)
    except Exception:
        d = {
            "checks": [],
            "all_passed": False,
            "pass_count": 0,
            "fail_count": 0,
            "warnings": [],
            "summary": "Could not serialize validation report.",
            "validation_status": "INVALID",
            "delivered_minutes": 0,
            "authorized_minutes": 0,
            "billable_minutes": 0,
        }
    rows: list[CheckOut] = []
    for c in d.get("checks") or []:
        if not isinstance(c, dict):
            continue
        subs: list[SubCheckOut] = []
        raw_subs = c.get("sub_checks")
        if isinstance(raw_subs, list):
            for s in raw_subs:
                sc = _subcheck_from_raw(s)
                if sc is not None:
                    subs.append(sc)
        rows.append(
            CheckOut(
                number=int(c.get("number", 0) or 0),
                name=str(c.get("name", "(unnamed check)")),
                passed=bool(c.get("passed", False)),
                expected=c.get("expected", ""),
                actual=c.get("actual", ""),
                tolerance=str(c.get("tolerance", "")),
                detail=str(c.get("detail", "")),
                sub_checks=subs,
            )
        )
    wr = d.get("warnings") or []
    warnings = [str(w) for w in wr] if isinstance(wr, list) else []
    return ValidationReportOut(
        checks=rows,
        all_passed=bool(d.get("all_passed", False)),
        summary=str(d.get("summary", "")),
        pass_count=int(d.get("pass_count", 0) or 0),
        fail_count=int(d.get("fail_count", 0) or 0),
        warnings=warnings,
        validation_status=str(d.get("validation_status", "INVALID")),
        delivered_minutes=int(d.get("delivered_minutes", 0) or 0),
        authorized_minutes=int(d.get("authorized_minutes", 0) or 0),
        billable_minutes=int(d.get("billable_minutes", 0) or 0),
    )


@router.post(
    "/clients/{client_id}/plans/{version}/validate",
    response_model=ValidationReportOut,
)
def validate_plan_dry(
    client_id: str,
    version: int,
    body: ValidateScheduleBody,
    db: Db,
) -> ValidationReportOut:
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    tasks = json.loads(p.tasks_json or "[]")
    form = rebuild_extracted_form(c, tasks)
    report = _client_cross_check(form, body.schedule, c)
    return _report_to_out(report)


@router.post(
    "/clients/{client_id}/plans/{version}/rerun",
    response_model=PlanResponse,
)
def rerun_plan(
    client_id: str,
    version: int,
    body: RerunBody,
    db: Db,
) -> PlanResponse:
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)

    abs_source: Path | None = None
    rel_from = (p.source_pdf_path or "").strip()
    if rel_from:
        abs_try = _abs_storage(rel_from)
        if abs_try.is_file():
            abs_source = abs_try
        else:
            logger.warning(
                "Plan v%s source PDF missing on disk (%s); trying other versions",
                version,
                rel_from,
            )
    if abs_source is None:
        alt = _first_existing_source_pdf_for_client(db, c.id)
        if alt:
            alt_rel, abs_source = alt
            logger.info(
                "Re-run v%s: using source PDF from another plan version (%s)",
                version,
                alt_rel,
            )

    ex: ExtractedForm
    rel_source: str

    if abs_source is not None:
        try:
            ex = extract_from_pdf(str(abs_source))
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse PDF: {type(e).__name__}: {e}",
            ) from e
        ex = refine_extracted_form(ex, use_llm=False)
        if _extraction_is_empty(ex):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not read any text from this PDF — tried native extraction "
                    "and OCR. If it's a scan, try a higher-resolution copy; otherwise "
                    "use the original searchable PDF from the MDHHS portal."
                ),
            )
        c = upsert_client(db, ex)
        sdir = safe_dir_name(c.client_id)
        new_ver = _next_version(db, c)
        rel_source = f"{sdir}/source_v{new_ver}.pdf"
        abs_new = STORAGE_DIR / rel_source
        abs_new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_source, abs_new)
    else:
        try:
            tasks = json.loads(p.tasks_json or "[]")
        except json.JSONDecodeError:
            tasks = []
        if not isinstance(tasks, list) or not tasks:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No authorization PDF is stored for this plan (or on disk), and "
                    "this version has no saved task rows to re-run from. Upload the "
                    "MDHHS PDF again, or select a plan version that still has a source file."
                ),
            )
        ex = rebuild_extracted_form(c, tasks)
        ex = refine_extracted_form(ex, use_llm=False)
        if _extraction_is_empty(ex):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Stored task data for this plan is too incomplete to rebuild. "
                    "Upload the authorization PDF again."
                ),
            )
        c = upsert_client(db, ex)
        sdir = safe_dir_name(c.client_id)
        new_ver = _next_version(db, c)
        rel_source = ""
        logger.info(
            "Re-run v%s: no PDF on file; using stored tasks only (new plan has no source PDF)",
            version,
        )

    pay = ex.pay_rate
    if body.year and body.month:
        y, m = _validated_year_month(body.year, body.month)
    else:
        y, m = _schedule_year_month_from_form(ex)
    pw: dict[str, Any] = {
        "weekday_start": body.preferred_window.weekday_start,
        "weekend_start": body.preferred_window.weekend_start,
    }
    _per_day = body.preferred_window.start_time_by_weekday
    if _per_day:
        pw["start_time_by_weekday"] = dict(_per_day)
    # If the caller supplied a ScheduleConfig, honor it; otherwise build
    # fresh defaults from the (possibly re-OCR'd) tasks.
    supplied_cfg = _schedule_config_from_body(body.config)
    if supplied_cfg is None:
        supplied_cfg = default_config_for(
            [dict(t) for t in ex.tasks],
            y,
            m,
            pw,
            worker_availability=_client_availability_parsed(c),
        )
    if body.use_llm:
        from .llm_schedule import optimize_schedule_config_with_llm

        notes = (body.llm_notes or "").strip()
        try:
            supplied_cfg = optimize_schedule_config_with_llm(
                tasks=[dict(t) for t in ex.tasks],
                year=y,
                month=m,
                preferred_window=pw,
                base_config=supplied_cfg,
                user_notes=notes,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        cal = generate_schedule(
            [dict(t) for t in ex.tasks],
            float(pay),
            y,
            m,
            pw,
            config=supplied_cfg,
            worker_availability=_client_availability_parsed(c),
        )
        sched = cal.as_dict()
        report = _client_cross_check(ex, sched, c)
    except DayCapacityExceeded as e:
        raise HTTPException(status_code=422, detail=e.http_detail()) from e
    except (CalibrationError, NotImplementedError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Schedule or validation error: {type(e).__name__}: {e}",
        ) from e
    rel_xlsx = f"{sdir}/plan_v{new_ver}.xlsx"
    rel_pdf = f"{sdir}/plan_v{new_ver}.pdf"
    try:
        _emit_outputs(
            ex,
            sched,
            report,
            STORAGE_DIR / rel_xlsx,
            STORAGE_DIR / rel_pdf,
        )
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to write plan files: {e}"
        ) from e
    except Exception as e:
        logger.exception("build_xlsx/build_pdf failed during rerun")
        raise HTTPException(
            status_code=500,
            detail=f"Could not build Excel/PDF: {type(e).__name__}: {e}",
        ) from e
    try:
        tj = json.dumps(ex.tasks, ensure_ascii=False, allow_nan=False, default=str)
        sj = json.dumps(sched, ensure_ascii=False, allow_nan=False, default=str)
        cj = json.dumps(
            cal.config.to_dict() if cal.config is not None else {},
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=500, detail=f"Could not serialize plan JSON: {e}"
        ) from e
    new_plan = _plan_record_from_pipeline(
        client=c,
        version=new_ver,
        year=y,
        month=m,
        schedule=sched,
        report=report,
        rel_source=rel_source,
        rel_xlsx=rel_xlsx,
        rel_pdf=rel_pdf,
        tasks_json=tj,
        schedule_json=sj,
        config_json=cj,
        notes=None,
    )
    db.add(new_plan)
    try:
        db.commit()
    except Exception as e:
        logger.exception("Database commit failed on rerun")
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Database error: {type(e).__name__}: {e}"
        ) from e
    db.refresh(new_plan)
    return orm_plan_to_pydantic(new_plan)


@router.get(
    "/clients/{client_id}/plans/{version}/download/{filetype}",
    response_class=FileResponse,
)
def download_artifact(
    client_id: str,
    version: int,
    filetype: Literal["xlsx", "pdf", "source", "weekly"],
    db: Db,
) -> FileResponse:
    c = _get_client_by_string_id(db, client_id)
    p = _get_plan(c, version, db)
    if filetype == "xlsx":
        rel = p.xlsx_path
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"plan_v{version}.xlsx"
    elif filetype == "weekly":
        # Client-facing grid is the "Weekly Schedule" tab inside the main workbook.
        rel = p.xlsx_path
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        name = f"plan_v{version}.xlsx"
    elif filetype == "pdf":
        rel = p.pdf_path
        media = "application/pdf"
        name = f"plan_v{version}.pdf"
    else:
        rel = p.source_pdf_path
        media = "application/pdf"
        name = f"source_v{version}.pdf"
    if not rel:
        raise HTTPException(
            status_code=404,
            detail=(
                "File not available: this plan has no stored workbook path "
                "(legacy or incomplete upload). Use Re-run plan on the client, "
                "or upload the authorization PDF again to emit artifacts."
            ),
        )
    path = _abs_storage(rel)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not on disk")
    return FileResponse(
        str(path),
        media_type=media,
        filename=name,
    )


@router.delete("/clients/{client_id}", status_code=204)
def delete_client(client_id: str, db: Db) -> Response:
    c = _get_client_by_string_id(db, client_id)
    sdir = safe_dir_name(c.client_id)
    plans = list(db.scalars(select(Plan).where(Plan.client_row_id == c.id)).all())
    db.delete(c)
    db.commit()
    for p in plans:
        for rel in (
            p.xlsx_path,
            p.pdf_path,
            p.source_pdf_path,
        ):
            if not rel:
                continue
            try:
                pth = _abs_storage(rel)
                if pth.is_file():
                    pth.unlink()
            except (HTTPException, OSError):
                pass
    d = STORAGE_DIR / sdir
    if d.is_dir():
        try:
            shutil.rmtree(d)
        except OSError:
            pass
    return Response(status_code=204)
