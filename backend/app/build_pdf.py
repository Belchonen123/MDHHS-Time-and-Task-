"""Render plan-of-care submission PDFs (5-page, letter, ReportLab platypus).

The PDF mirrors the XLSX reconciliation layout minus the Schedule Math tab
(too verbose for print) and the Instructions tab (not part of submission).

    Page 1 — Summary (MDHHS auth table, calibrated allocation table, billing status badge)
    Page 2 — Weekly pattern + deviation call-out
    Page 3–4 — Daily schedule (color-coded by shift type)
    Page 5 — Task reconciliation + validation checks + footer
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .build_xlsx import _money_from_minutes
from .calculate import (
    CalibratedSchedule,
    compute_mdhhs_form_amount,
    compute_mdhhs_form_minutes,
    compute_monthly_minutes_rounded,
    compute_task_amount,
    schedule_to_dict,
)
from .extract import ExtractedForm
from .validate import ValidationReport

# --- Palette ----------------------------------------------------------------
NAVY = colors.HexColor("#1F4E78")
MED_BLUE = colors.HexColor("#2E75B6")
LIGHT_BLUE = colors.HexColor("#BDD7EE")
TOTAL_YEL = colors.HexColor("#FFF2CC")
WEEKEND = colors.HexColor("#FCE4D6")
HW_ROW = colors.HexColor("#FFF9E6")
CATCHUP_ROW = colors.HexColor("#FFD79A")
PASS_GREEN = colors.HexColor("#C8E6C9")
FAIL_RED = colors.HexColor("#FFCDD2")
BIG_GREEN = colors.HexColor("#4CAF50")
BIG_RED = colors.HexColor("#E53935")
ORANGE = colors.HexColor("#FB8C00")
ZEBRA = colors.HexColor("#F6F8FA")

MAX_TASKS = 12
SUN_SAT_FULL = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
SUN_SAT_SHORT = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

# Short forms used in the dense daily-schedule column. Unknown names pass through.
_TASK_SHORT = {
    "Meal Preparation": "MealPrep",
    "Shopping for Food/Meds": "Shopping",
    "Travel For Shopping": "Travel",
    "Transferring": "Transfer",
    "Medication": "Meds",
    "Housework": "HW",
}


def _shorten_task(name: str) -> str:
    return _TASK_SHORT.get(name, name)


# --- Helpers ----------------------------------------------------------------


def _fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_hm(total_min: int | None) -> str:
    """Signed ``HH:MM`` (zero-padded hours) — matches printed MDHHS-6064-P.

    Empty / None / 0 returns empty string. Negative durations keep a leading ``-``.
    Every duration cell in the Summary / Weekly Pattern / Schedule grids routes
    through this helper alongside raw ``… min`` text where present.
    """
    if total_min is None:
        return ""
    m = int(total_min)
    if m == 0:
        return ""
    sign = "-" if m < 0 else ""
    h, r = divmod(abs(m), 60)
    return f"{sign}{h:02d}:{r:02d}"


def _para_xml(s: Any) -> str:
    return escape(str(s or ""), entities={"'": "&apos;", '"': "&quot;"})


def _as_schedule_dict(cs: Any) -> dict[str, Any]:
    if isinstance(cs, CalibratedSchedule):
        return schedule_to_dict(cs)
    if isinstance(cs, dict):
        return cs
    raise TypeError(
        f"calibrated_schedule must be CalibratedSchedule or dict, got {type(cs).__name__}"
    )


def _truncate(s: str, n: int) -> str:
    s = str(s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _task_auth_monthly_min(task: dict[str, Any]) -> int:
    """round_half_up(mpd × dpw × 4.3) — matches the MDHHS per-line monthly time."""
    mpd = int(task.get("min_per_day", 0) or 0)
    dpw = int(task.get("days_per_week", 0) or 0)
    return compute_monthly_minutes_rounded(mpd, dpw)


def _sum_task_minutes_and_days(
    daily: list[dict[str, Any]], task_name: str
) -> tuple[int, int]:
    mins = 0
    days = 0
    for d in daily:
        v = int((d.get("tasks") or {}).get(task_name, 0) or 0)
        if v > 0:
            mins += v
            days += 1
    return mins, days


def _shift_color(shift_type: str, dow_full: str) -> colors.Color | None:
    if shift_type == "CATCHUP":
        return CATCHUP_ROW
    if shift_type == "WEEKEND_FULL" or dow_full in ("Saturday", "Sunday"):
        return WEEKEND
    if shift_type == "HW_DAY":
        return HW_ROW
    return None


def _parse_iso_date(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


# --- Styles -----------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    out = {
        "title": ParagraphStyle(
            "title", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=16, leading=18, alignment=TA_CENTER, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=10.5, leading=12, spaceBefore=6, spaceAfter=3,
            textColor=NAVY,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=11, spaceBefore=4, spaceAfter=2,
            textColor=NAVY,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=12,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=9.5,
        ),
        "tiny": ParagraphStyle(
            "tiny", parent=base["Normal"], fontName="Helvetica",
            fontSize=7, leading=8,
        ),
        "badge": ParagraphStyle(
            "badge", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, alignment=TA_CENTER, textColor=colors.white,
        ),
        "callout": ParagraphStyle(
            "callout", parent=base["Normal"], fontName="Helvetica",
            fontSize=9, leading=11, alignment=TA_LEFT, textColor=colors.HexColor("#5D3A00"),
        ),
        "foot": ParagraphStyle(
            "foot", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=10, textColor=colors.grey, alignment=TA_CENTER,
        ),
    }
    return out


def _header_table_style(col_count: int, header_rows: int = 1) -> TableStyle:
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, header_rows - 1), 8.5),
            ("TEXTCOLOR", (0, 0), (-1, header_rows - 1), colors.white),
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), NAVY),
            ("ALIGN", (0, 0), (-1, header_rows - 1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTNAME", (0, header_rows), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, header_rows), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )


# --- Page builders ----------------------------------------------------------


def _page1_summary(
    form: ExtractedForm,
    sd: dict[str, Any],
    st: dict[str, ParagraphStyle],
    report: ValidationReport,
) -> list[Any]:
    pay = float(form.pay_rate)
    tasks = list(form.tasks)
    month_name = str(sd.get("month_name") or "")
    year = int(sd.get("year") or 0)

    auth_mm = int(sd.get("mdhhs_monthly_minutes") or 0)
    auth_amt = float(sd.get("mdhhs_monthly_amount") or 0.0)
    daily = list(sd.get("daily_schedule") or [])
    sched_mm = sd.get("delivered_minutes")
    if sched_mm is None:
        sched_mm = sum(int(d.get("duration_min", 0) or 0) for d in daily)
    else:
        sched_mm = int(sched_mm)
    sched_amt = sd.get("delivered_amount")
    if sched_amt is None:
        sched_amt = float(
            (
                Decimal(sched_mm) * Decimal(str(pay)) / Decimal("60")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        )
    else:
        sched_amt = float(sched_amt)

    bill_mm = sd.get("billable_minutes")
    if bill_mm is None:
        bill_mm = min(sched_mm, auth_mm)
    else:
        bill_mm = int(bill_mm)
    bill_amt = sd.get("billable_amount")
    if bill_amt is None:
        bill_amt = float(
            min(
                Decimal(str(sched_amt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
                Decimal(str(auth_amt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
            )
        )
    else:
        bill_amt = float(bill_amt)

    story: list[Any] = []

    # --- Title + header strip
    story.append(Paragraph(
        f"Plan of Care — {_para_xml(form.client_name or 'Client')} "
        f"— {_para_xml(month_name)} {year}",
        st["title"],
    ))

    hdr_rows = [
        [
            Paragraph("<b>Client</b>", st["small"]),
            Paragraph(_para_xml(form.client_name or "—"), st["small"]),
            Paragraph("<b>Client ID</b>", st["small"]),
            Paragraph(_para_xml(form.client_id or "—"), st["small"]),
            Paragraph("<b>Case #</b>", st["small"]),
            Paragraph(_para_xml(form.case_number or "—"), st["small"]),
        ],
        [
            Paragraph("<b>County</b>", st["small"]),
            Paragraph(_para_xml(form.county_name or "—"), st["small"]),
            Paragraph("<b>ASW</b>", st["small"]),
            Paragraph(_para_xml(form.asw_name or "—"), st["small"]),
            Paragraph("<b>Pay rate</b>", st["small"]),
            Paragraph(f"{_fmt_money(pay)}/hr", st["small"]),
        ],
    ]
    hdr = Table(hdr_rows, colWidths=[0.75 * inch, 1.9 * inch, 0.75 * inch, 1.5 * inch, 0.75 * inch, 1.75 * inch])
    hdr.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BLUE),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_BLUE),
        ("BACKGROUND", (4, 0), (4, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(hdr)

    # --- Section 1: MDHHS Authorization
    story.append(Paragraph("Section 1 — MDHHS Authorization (from the 6064-P PDF)", st["h2"]))
    sec1_head = ["Task", "Min / day", "Days / wk", "Monthly time", "Monthly $"]
    sec1_rows: list[list[Any]] = [sec1_head]
    total_auth_min = compute_mdhhs_form_minutes(tasks)
    total_auth_amt = compute_mdhhs_form_amount(tasks, pay)
    for i in range(MAX_TASKS):
        if i < len(tasks):
            t = tasks[i]
            nm = str(t.get("task_name") or "")
            mpd = int(t.get("min_per_day", 0) or 0)
            dpw = int(t.get("days_per_week", 0) or 0)
            amm = _task_auth_monthly_min(t) if nm else 0
            amt = float(t.get("monthly_amount") or 0.0)
            if not amt and amm:
                amt = compute_task_amount(mpd, dpw, pay)
            sec1_rows.append([
                Paragraph(_para_xml(nm), st["small"]) if nm else "",
                f"{mpd}" if nm else "",
                f"{dpw}" if nm else "",
                (_fmt_hm(amm) or "—") if nm else "",
                _fmt_money(amt) if nm else "",
            ])
        else:
            sec1_rows.append(["", "", "", "", ""])
    sec1_rows.append([
        Paragraph("<b>TOTAL</b>", st["small"]),
        "", "",
        _fmt_hm(total_auth_min) or "—",
        _fmt_money(total_auth_amt),
    ])
    t1 = Table(
        sec1_rows,
        colWidths=[2.6 * inch, 0.8 * inch, 0.8 * inch, 1.2 * inch, 1.0 * inch],
    )
    s1 = _header_table_style(5)
    s1.add("ALIGN", (1, 1), (-1, -1), "RIGHT")
    s1.add("ALIGN", (0, 1), (0, -1), "LEFT")
    s1.add("BACKGROUND", (0, -1), (-1, -1), TOTAL_YEL)
    s1.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    t1.setStyle(s1)
    story.append(t1)

    # --- Section 2: Scheduled Allocation
    story.append(Paragraph(
        f"Section 2 — {_para_xml(month_name)} Scheduled Allocation (calibrated to this calendar month)",
        st["h2"],
    ))
    sec2_head = ["Task", "Sched days", "Sched minutes", "Sched time", "Sched $"]
    sec2_rows: list[list[Any]] = [sec2_head]
    t_sched_total_min = 0
    t_sched_total_days = 0
    for i in range(MAX_TASKS):
        if i < len(tasks):
            t = tasks[i]
            nm = str(t.get("task_name") or "")
            if nm:
                smins, sdays = _sum_task_minutes_and_days(daily, nm)
                t_sched_total_min += smins
                t_sched_total_days += sdays
                samt = _money_from_minutes(smins, pay)
                sec2_rows.append([
                    Paragraph(_para_xml(nm), st["small"]),
                    f"{sdays}",
                    f"{smins}",
                    _fmt_hm(smins) or "—",
                    _fmt_money(samt),
                ])
            else:
                sec2_rows.append(["", "", "", "", ""])
        else:
            sec2_rows.append(["", "", "", "", ""])
    sec2_rows.append([
        Paragraph("<b>TOTAL</b>", st["small"]),
        f"{t_sched_total_days}",
        f"{t_sched_total_min}",
        _fmt_hm(t_sched_total_min) or "—",
        _fmt_money(bill_amt),
    ])
    t2 = Table(
        sec2_rows,
        colWidths=[2.6 * inch, 0.9 * inch, 1.2 * inch, 0.8 * inch, 0.9 * inch],
    )
    s2 = _header_table_style(5)
    s2.add("ALIGN", (1, 1), (-1, -1), "RIGHT")
    s2.add("ALIGN", (0, 1), (0, -1), "LEFT")
    s2.add("BACKGROUND", (0, -1), (-1, -1), TOTAL_YEL)
    s2.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    t2.setStyle(s2)
    story.append(t2)

    # --- Section 3: Reconciliation badge
    story.append(Paragraph("Section 3 — Reconciliation", st["h2"]))
    vstat = getattr(report, "validation_status", "INVALID")
    badge_labels = {
        "BILLABLE_EXACT": "BILLABLE EXACT ✓",
        "BILLABLE_AT_CAP": "BILLABLE AT CAP ✓",
        "BILLABLE_UNDER_CAP": "BILLABLE UNDER CAP ✓",
        "INVALID": "INVALID ✗",
    }
    badge_text = badge_labels.get(vstat, vstat)
    st_ok = vstat.startswith("BILLABLE")

    delta_mm = sched_mm - auth_mm
    delta_amt = float(
        (Decimal(str(sched_amt)) - Decimal(str(auth_amt))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
    )
    recon_rows = [
        [
            "",
            "Authorized (6064-P)",
            "Delivered (calendar)",
            "Billable (invoice)",
            "Δ delivered−auth",
        ],
        [
            "Monthly minutes",
            _fmt_hm(auth_mm) or "—",
            _fmt_hm(sched_mm) or "—",
            _fmt_hm(bill_mm) or "—",
            f"{delta_mm:+d}",
        ],
        [
            "Monthly $",
            _fmt_money(auth_amt),
            _fmt_money(sched_amt),
            _fmt_money(bill_amt),
            f"{delta_amt:+,.2f}",
        ],
    ]
    t3 = Table(
        recon_rows,
        colWidths=[1.35 * inch, 1.35 * inch, 1.35 * inch, 1.35 * inch, 1.1 * inch],
    )
    s3 = _header_table_style(5)
    s3.add("ALIGN", (1, 1), (-1, -1), "RIGHT")
    s3.add("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold")
    s3.add("BACKGROUND", (0, 1), (0, -1), LIGHT_BLUE)
    t3.setStyle(s3)

    badge_bg = BIG_GREEN if st_ok else BIG_RED
    badge = Table(
        [[Paragraph(badge_text, st["badge"])]],
        colWidths=[6.5 * inch],
        rowHeights=[0.45 * inch],
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), badge_bg),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 1, badge_bg),
    ]))
    # Keep the reconciliation table and the badge together so the badge
    # never orphans onto the next page.
    story.append(KeepTogether([t3, Spacer(1, 0.08 * inch), badge]))

    return story


def _page2_weekly_pattern(
    sd: dict[str, Any],
    st: dict[str, ParagraphStyle],
) -> list[Any]:
    weekly = dict(sd.get("weekly_pattern") or sd.get("days") or {})
    story: list[Any] = []
    story.append(Paragraph("Weekly Pattern", st["title"]))
    story.append(Paragraph(
        "Day-of-week pattern applied to every full week in the month "
        "(before calendar-level calibration).",
        st["small"],
    ))
    story.append(Spacer(1, 0.08 * inch))

    head = ["Day", "Clock in", "Clock out", "Duration", "Tasks", "Minutes"]
    rows: list[list[Any]] = [head]
    total_min = 0
    for idx, dname in enumerate(SUN_SAT_FULL):
        info = weekly.get(dname) or {}
        start = str(info.get("start", "—") or "—")
        end = str(info.get("end", "—") or "—")
        mins = int(info.get("minutes", 0) or 0)
        total_min += mins
        tlist = list(info.get("tasks") or [])
        rows.append([
            Paragraph(f"<b>{SUN_SAT_SHORT[idx]}</b>", st["small"]),
            start,
            end,
            _fmt_hm(mins) or "—",
            Paragraph(
                _para_xml(", ".join(str(t) for t in tlist)) or "<i>—</i>",
                st["small"],
            ),
            f"{mins}" if mins else "—",
        ])
    rows.append([
        Paragraph("<b>Weekly total</b>", st["small"]),
        "", "", _fmt_hm(total_min) or "—", "", f"{total_min}",
    ])
    t = Table(
        rows,
        colWidths=[0.6 * inch, 0.75 * inch, 0.75 * inch, 0.75 * inch, 3.7 * inch, 0.8 * inch],
    )
    s = _header_table_style(6)
    s.add("ALIGN", (1, 1), (3, -1), "CENTER")
    s.add("ALIGN", (5, 1), (5, -1), "RIGHT")
    s.add("VALIGN", (0, 1), (-1, -1), "TOP")
    # Weekend shading for Sun (row 1) and Sat (row 7)
    s.add("BACKGROUND", (0, 1), (-1, 1), WEEKEND)
    s.add("BACKGROUND", (0, 7), (-1, 7), WEEKEND)
    s.add("BACKGROUND", (0, -1), (-1, -1), TOTAL_YEL)
    s.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    t.setStyle(s)
    story.append(t)

    # --- Deviation call-out
    catchup_date = sd.get("catchup_date")
    if catchup_date:
        story.append(Spacer(1, 0.18 * inch))
        body = _deviation_callout_text(sd)
        callout = Table(
            [[Paragraph(body, st["callout"])]],
            colWidths=[7.25 * inch],
        )
        callout.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF3E0")),
            ("BOX", (0, 0), (-1, -1), 1.5, ORANGE),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(callout)

    return story


def _deviation_callout_text(sd: dict[str, Any]) -> str:
    """Render the deviation callout body directly from daily_schedule.

    The prior copy hardcoded the Ottilie default-config catchup shape —
    "carries the daily (7/wk) and errand (2/wk) tasks only, Housework
    (3/wk) fully met and excluded". That narrative is wrong for any
    plan whose catchup has a different composition (a user-added
    single-task override, an arbitrary mixed bundle, etc.). We now
    render from what the catchup day *actually* carries:

    * ``{date}`` — human-readable catchup date.
    * ``{duration}`` — actual ``duration_min`` of the catchup row.
    * ``{task_list}`` — comma-joined list of task names from the
      catchup row's ``tasks`` map, in insertion order.
    """
    catchup_date = sd.get("catchup_date")
    parsed = _parse_iso_date(catchup_date)
    d_str = parsed.strftime("%A, %B %d, %Y") if parsed else str(catchup_date)

    iso = str(catchup_date)
    daily = list(sd.get("daily_schedule") or [])
    catch_entry: dict[str, Any] | None = None
    for d in daily:
        if str(d.get("date") or "") == iso:
            catch_entry = d
            break

    if catch_entry is None:
        duration = int(sd.get("catchup_day_min") or 0)
        task_phrase = "the plan's calibration tasks"
    else:
        duration = int(catch_entry.get("duration_min") or 0)
        tmap = catch_entry.get("tasks") or {}
        names = [str(k) for k, v in tmap.items() if int(v or 0) > 0]
        if not names:
            task_phrase = "no tasks recorded (empty catchup row)"
        elif len(names) == 1:
            task_phrase = _para_xml(names[0])
        elif len(names) == 2:
            task_phrase = f"{_para_xml(names[0])} and {_para_xml(names[1])}"
        else:
            task_phrase = (
                ", ".join(_para_xml(n) for n in names[:-1])
                + ", and "
                + _para_xml(names[-1])
            )

    return (
        f"<b>Deviation day:</b> {_para_xml(d_str)} carries <b>{duration} minutes</b> "
        f"with {task_phrase} — an MDHHS-style pattern adjustment. "
        "<b>Authorization</b> is the billing cap (6064-P). "
        "<b>Delivered</b> minutes are projected from the weekly pattern across "
        "this calendar month. <b>Billable</b> = min(delivered, authorized) "
        "per ASM&nbsp;144 / MSA-1904."
    )


def _page3_4_daily(
    form: ExtractedForm,
    sd: dict[str, Any],
    st: dict[str, ParagraphStyle],
) -> list[Any]:
    pay = float(form.pay_rate)
    daily = list(sd.get("daily_schedule") or [])
    month_name = str(sd.get("month_name") or "")
    year = int(sd.get("year") or 0)

    story: list[Any] = []
    story.append(Paragraph(
        f"Daily Schedule — {_para_xml(month_name)} {year}",
        st["title"],
    ))
    story.append(Paragraph(
        "Row colors: weekend = peach; housework day = pale yellow; "
        "catchup (deviation) day = orange.",
        st["small"],
    ))
    story.append(Spacer(1, 0.08 * inch))

    head = ["#", "Date", "DoW", "Shift", "In", "Out", "Dur", "Tasks (min)", "$"]
    rows: list[list[Any]] = [head]
    auth_mm = int(sd.get("mdhhs_monthly_minutes") or 0)
    auth_amt = float(sd.get("mdhhs_monthly_amount") or 0.0)
    total_min = 0
    total_cost_dec = Decimal("0")

    row_bg: list[tuple[int, colors.Color]] = []
    for i, d in enumerate(daily, start=1):
        dt = _parse_iso_date(d.get("date"))
        dow_full = str(d.get("day_of_week", "") or (dt.strftime("%A") if dt else ""))
        date_s = dt.strftime("%b %d") if dt else str(d.get("date", ""))
        dow_short = dow_full[:3] if dow_full else ""
        shift = str(d.get("shift_type", ""))
        ci = str(d.get("clock_in", "") or "")
        co = str(d.get("clock_out", "") or "")
        dur = int(d.get("duration_min", 0) or 0)
        total_min += dur
        cost = _money_from_minutes(dur, pay)
        total_cost_dec += Decimal(str(cost))
        tmap = d.get("tasks") or {}
        task_txt = ", ".join(
            _shorten_task(n) for n, m in tmap.items() if int(m or 0) > 0
        )
        task_txt = _truncate(task_txt, 95)
        rows.append([
            str(i),
            date_s,
            dow_short,
            shift.replace("_", " ").title() if shift else "",
            ci, co, _fmt_hm(dur) or "—",
            task_txt,
            _fmt_money(cost),
        ])
        shade = _shift_color(shift, dow_full)
        if shade is not None:
            row_bg.append((i, shade))

    total_cost = float(
        total_cost_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )
    total_row_idx = len(rows)
    rows.append([
        "Delivered total", "", "", "", "", "",
        _fmt_hm(total_min) or "—", f"{total_min} min", _fmt_money(total_cost),
    ])
    auth_row_idx = len(rows)
    rows.append([
        "Authorized (6064-P cap)", "", "", "", "", "",
        _fmt_hm(auth_mm) or "—", f"{auth_mm} min", _fmt_money(auth_amt),
    ])
    del_amt_g = sd.get("delivered_amount")
    if del_amt_g is None:
        del_amt_g = float(
            (Decimal(total_min) * Decimal(str(pay)) / Decimal("60")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
        )
    else:
        del_amt_g = float(del_amt_g)
    bill_mm = sd.get("billable_minutes")
    if bill_mm is None:
        bill_mm = min(total_min, auth_mm)
    else:
        bill_mm = int(bill_mm)
    bill_amt_g = sd.get("billable_amount")
    if bill_amt_g is None:
        bill_amt_g = float(
            min(
                Decimal(str(del_amt_g)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                ),
                Decimal(str(auth_amt)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                ),
            )
        )
    else:
        bill_amt_g = float(bill_amt_g)
    bill_row_idx = len(rows)
    rows.append([
        "Billable (cap applied)", "", "", "", "", "",
        _fmt_hm(bill_mm) or "—", f"{bill_mm} min", _fmt_money(bill_amt_g),
    ])
    var_row_idx = len(rows)
    var_min = total_min - auth_mm
    var_amt = float(
        (Decimal(str(del_amt_g)) - Decimal(str(auth_amt))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
    )
    cal_label = (
        "Calendar overshoot (non-billable)"
        if var_min > 0
        else "Under-delivery (bill actual)"
        if var_min < 0
        else "At authorization"
    )
    rows.append([
        cal_label, "", "", "", "", "",
        _fmt_hm(var_min) or "—", f"{var_min:+d} min", f"{var_amt:+,.2f}",
    ])

    # Widen "Dur" for fixed-width HH:MM (e.g. "03:08", "44:26").
    col_widths = [
        0.3 * inch, 0.6 * inch, 0.4 * inch, 0.75 * inch, 0.6 * inch,
        0.6 * inch, 0.7 * inch, 3.0 * inch, 0.55 * inch,
    ]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    s = TableStyle([
        # Header
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        # Body
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (1, 1), (2, -1), "CENTER"),
        ("ALIGN", (4, 1), (6, -1), "CENTER"),
        ("ALIGN", (8, 1), (8, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.2, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ])
    # Color-coded body rows
    for idx, bg in row_bg:
        s.add("BACKGROUND", (0, idx), (-1, idx), bg)
    # Totals / auth / variance rows
    s.add("BACKGROUND", (0, total_row_idx), (-1, total_row_idx), TOTAL_YEL)
    s.add("FONTNAME", (0, total_row_idx), (-1, total_row_idx), "Helvetica-Bold")
    s.add("BACKGROUND", (0, auth_row_idx), (-1, auth_row_idx), LIGHT_BLUE)
    s.add("FONTNAME", (0, auth_row_idx), (-1, auth_row_idx), "Helvetica-Bold")
    s.add("BACKGROUND", (0, bill_row_idx), (-1, bill_row_idx), TOTAL_YEL)
    s.add("FONTNAME", (0, bill_row_idx), (-1, bill_row_idx), "Helvetica-Bold")
    variance_bg = (
        PASS_GREEN if var_min == 0 and abs(var_amt) <= 0.02 else LIGHT_BLUE
    )
    s.add("BACKGROUND", (0, var_row_idx), (-1, var_row_idx), variance_bg)
    s.add("FONTNAME", (0, var_row_idx), (-1, var_row_idx), "Helvetica-Bold")
    s.add("LINEABOVE", (0, total_row_idx), (-1, total_row_idx), 1, colors.black)
    tbl.setStyle(s)
    story.append(tbl)
    return story


def _page5_reconciliation(
    form: ExtractedForm,
    sd: dict[str, Any],
    report: ValidationReport,
    st: dict[str, ParagraphStyle],
) -> list[Any]:
    pay = float(form.pay_rate)
    tasks = list(form.tasks)
    daily = list(sd.get("daily_schedule") or [])

    story: list[Any] = []
    story.append(Paragraph("Task Reconciliation", st["title"]))
    story.append(Paragraph(
        "Per-task comparison: authorized minutes (round_half_up(mpd × dpw × 4.3)) "
        "vs. scheduled minutes (sum across the calibrated daily schedule).",
        st["small"],
    ))
    story.append(Spacer(1, 0.05 * inch))

    head = ["Task", "Auth min", "Sched min", "Sched days", "Variance", "Status"]
    rows: list[list[Any]] = [head]
    pass_row_flags: list[bool] = []
    total_auth = 0
    total_sched = 0
    for t in tasks:
        nm = str(t.get("task_name") or "")
        if not nm:
            continue
        auth = _task_auth_monthly_min(t)
        sched, days = _sum_task_minutes_and_days(daily, nm)
        var = sched - auth
        total_auth += auth
        total_sched += sched
        ok = abs(var) <= max(1, int(t.get("min_per_day", 0) or 0)) + 1
        pass_row_flags.append(ok)
        rows.append([
            Paragraph(_para_xml(nm), st["small"]),
            f"{auth}",
            f"{sched}",
            f"{days}",
            f"{var:+d}",
            "✓" if ok else "✗",
        ])
    sched_mm = int(sd.get("mdhhs_monthly_minutes") or 0)
    total_var = total_sched - sched_mm
    rows.append([
        Paragraph("<b>TOTAL</b>", st["small"]),
        f"{total_auth}",
        f"{total_sched}",
        "",
        f"{total_var:+d}",
        "✓" if total_var == 0 else "✗",
    ])

    tr = Table(
        rows,
        colWidths=[2.6 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 0.8 * inch, 0.7 * inch],
    )
    s = _header_table_style(6)
    s.add("ALIGN", (1, 1), (-1, -1), "RIGHT")
    s.add("ALIGN", (0, 1), (0, -1), "LEFT")
    s.add("ALIGN", (5, 1), (5, -1), "CENTER")
    s.add("BACKGROUND", (0, -1), (-1, -1), TOTAL_YEL)
    s.add("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")
    for i, ok in enumerate(pass_row_flags, start=1):
        s.add("BACKGROUND", (5, i), (5, i), PASS_GREEN if ok else FAIL_RED)
    s.add("BACKGROUND", (5, len(rows) - 1), (5, len(rows) - 1),
          PASS_GREEN if total_var == 0 else FAIL_RED)
    tr.setStyle(s)
    story.append(tr)

    # --- Validation checks table
    story.append(Paragraph("Cross-Check Validation (11 checks)", st["h2"]))
    vhead = ["#", "Check", "Expected", "Actual", "Tolerance", "Status"]
    vrows: list[list[Any]] = [vhead]
    for c in report.checks:
        vrows.append([
            str(c.number),
            Paragraph(_para_xml(c.name), st["small"]),
            Paragraph(_para_xml(_truncate(str(c.expected), 110)), st["tiny"]),
            Paragraph(_para_xml(_truncate(str(c.actual), 110)), st["tiny"]),
            Paragraph(_para_xml(_truncate(str(c.tolerance), 35)), st["tiny"]),
            "✓ PASS" if c.passed else "✗ FAIL",
        ])
    vtbl = Table(
        vrows,
        colWidths=[0.3 * inch, 2.2 * inch, 1.8 * inch, 1.8 * inch, 0.9 * inch, 0.8 * inch],
        repeatRows=1,
    )
    vs = _header_table_style(6)
    vs.add("FONTSIZE", (0, 1), (-1, -1), 8)
    vs.add("VALIGN", (0, 1), (-1, -1), "TOP")
    vs.add("ALIGN", (0, 1), (0, -1), "CENTER")
    vs.add("ALIGN", (5, 1), (5, -1), "CENTER")
    for i, c in enumerate(report.checks, start=1):
        bg = PASS_GREEN if c.passed else FAIL_RED
        vs.add("BACKGROUND", (5, i), (5, i), bg)
        if i % 2 == 0:
            vs.add("BACKGROUND", (0, i), (4, i), ZEBRA)
    vtbl.setStyle(vs)
    story.append(vtbl)

    # --- Footer
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    n = len(report.checks)
    passed = sum(1 for c in report.checks if c.passed)
    if passed == n and n > 0:
        foot_text = f"Generated {ts}. All {n} checks passed."
    else:
        foot_text = f"Generated {ts}. {passed} of {n} checks passed; {n - passed} failed."
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(foot_text, st["foot"]))
    if report.warnings:
        story.append(Spacer(1, 0.05 * inch))
        story.append(Paragraph(
            "Warnings: " + _para_xml("; ".join(report.warnings[:6])),
            st["foot"],
        ))
    return story


# --- Public entrypoint -------------------------------------------------------


def build_pdf(
    extracted_form: ExtractedForm,
    calibrated_schedule: Any,
    validation_report: ValidationReport,
    output_path: str | Path,
) -> None:
    """
    Write a 5-page letter-sized submission PDF.

    Parameters
    ----------
    extracted_form
        Parsed ``ExtractedForm`` produced by :mod:`extract`.
    calibrated_schedule
        Either a :class:`CalibratedSchedule` from :func:`calculate.generate_schedule`
        or its ``as_dict()`` payload.
    validation_report
        A :class:`ValidationReport` from :func:`validate.cross_check`.
    output_path
        Destination file (parent directory will be created if missing).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sd = _as_schedule_dict(calibrated_schedule)
    st = _styles()

    story: list[Any] = []
    # Page 1
    story.extend(_page1_summary(extracted_form, sd, st, validation_report))
    story.append(PageBreak())
    # Page 2
    story.extend(_page2_weekly_pattern(sd, st))
    story.append(PageBreak())
    # Page 3–4
    story.extend(_page3_4_daily(extracted_form, sd, st))
    story.append(PageBreak())
    # Page 5
    story.extend(_page5_reconciliation(extracted_form, sd, validation_report, st))

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"Plan of Care — {extracted_form.client_name or 'Client'}",
        author="mdhhs-poc-builder",
    )
    doc.build(story)
