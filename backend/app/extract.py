"""Parse MDHHS-6064-P Provider Time and Task Management PDFs (pdfplumber, regex)."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

import pdfplumber

logger = logging.getLogger(__name__)

# Authorized task names (canonical spellings — downstream uses task_name verbatim).
# Ordering: ADLs, IADLs, Travel (EHHS cap exclusions), Complex Care (_merge_tasks order).
KNOWN_TASKS: tuple[str, ...] = (
    # Personal Care (ADL)
    "Eating/Feeding",
    "Bathing",
    "Dressing",
    "Grooming",  # MDHHS-6064-P / invoice wording — do not alias to "Hygiene" on exports
    "Mobility",
    "Toileting",
    "Transferring",
    # Home Help (IADL)
    "Housework",
    "Laundry",
    "Meal Preparation",
    "Shopping for Food/Meds",
    "Taking Medication",
    # Travel (excluded from 179.59-hr EHHS cap — flagged downstream)
    "Travel For Shopping",
    "Travel For Laundry",
    # Complex Care (ASM 120)
    "Catheter Care",
    "Colostomy Care",
    "Bowel Program",
    "Suctioning",
    "Specialized Skin Care",
    "Range of Motion",
    "Peritoneal Dialysis",
    "Wound Care",
    "Respiratory Treatment",
    "Ventilator Care",
    "Injections",
)

# ASM 120 complex-care lines — frequency is client-specific; placement must never
# assume a default 7/3/2 bucket from task name alone (see calculate.generate_schedule).
COMPLEX_CARE_TASK_NAMES: frozenset[str] = frozenset(
    KNOWN_TASKS[KNOWN_TASKS.index("Catheter Care") :]
)

# Form-language variants from MDHHS-6064-P PDFs → canonical KNOWN_TASKS name (lower-cased lookup).
TASK_ALIASES: dict[str, str] = {
    "Eating": "Eating/Feeding",
    "Feeding": "Eating/Feeding",
    "Medication": "Taking Medication",
    "Meal Preparation and Cleanup": "Meal Preparation",
    "Meal Prep": "Meal Preparation",
    "Shopping for Food and Medication": "Shopping for Food/Meds",
    "Shopping/Errands": "Shopping for Food/Meds",
    # OCR / narrow columns often truncate the label to one word ("Shopping …").
    "Shopping": "Shopping for Food/Meds",
    "Light Housework": "Housework",
    "Travel for Shopping": "Travel For Shopping",
    "Travel for Laundry": "Travel For Laundry",
    "Catheter": "Catheter Care",
    "Catheters or Leg Bags": "Catheter Care",
    "Colostomy": "Colostomy Care",
    "Range of Motion Exercises": "Range of Motion",
    "ROM": "Range of Motion",
    "Wound": "Wound Care",
    "Respiratory": "Respiratory Treatment",
    "Ventilator": "Ventilator Care",
    "Ventilators": "Ventilator Care",
    "Injection": "Injections",
    "Skin Care": "Specialized Skin Care",
}

_ALIAS_LOWER_TO_CANONICAL: dict[str, str] = {
    alias.strip().lower(): canonical for alias, canonical in TASK_ALIASES.items()
}

# Longest-first alternation includes every canonical name and alias key (see TASK_ALIASES).
_TASK_REGEX_NAMES: tuple[str, ...] = tuple(
    sorted(
        frozenset(KNOWN_TASKS).union(TASK_ALIASES.keys()),
        key=len,
        reverse=True,
    )
)

_TASK_NAME_ALT = "|".join(re.escape(n) for n in _TASK_REGEX_NAMES)

# Fallback scan order for lines where the capturing group lacks a canonical match.
_TASK_MATCH_ORDER: tuple[str, ...] = _TASK_REGEX_NAMES


def canonical_task_name(matched_fragment: str) -> str | None:
    """
    Normalize a matched task label (case-insensitive) to canonical KNOWN_TASKS spelling.
    Returns None only when neither a canonical nor an alias resolves.
    """
    normalized = " ".join((matched_fragment or "").strip().split())
    if not normalized:
        return None
    key_lower = normalized.lower()
    for kt in KNOWN_TASKS:
        if kt.lower() == key_lower:
            return kt
    canon = _ALIAS_LOWER_TO_CANONICAL.get(key_lower)
    if canon is not None:
        return canon
    return None


@dataclass
class ExtractedForm:
    """Structured fields from an MDHHS-6064-P authorization PDF."""

    client_name: str = ""
    client_id: str = ""
    county_name: str = ""
    case_number: str = ""
    asw_name: str = ""
    asw_email: str = ""
    asw_phone: str = ""
    auth_date: str = ""
    provider_name: str = ""
    pay_rate: float = 0.0
    tasks: list[dict[str, Any]] = field(default_factory=list)
    monthly_total_time_str: str = ""
    monthly_total_amount: float = 0.0


# --- Small parsers -----------------------------------------------------------


def parse_hhmm_to_minutes(s: str) -> int | None:
    """
    Parse a clock-style HH:MM string into total minutes.
    e.g. "00:16" -> 16, "01:00" -> 60.
    """
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if mm >= 60 or hh > 23:
        return None
    return hh * 60 + mm


def parse_days_per_week(s: str) -> int | None:
    """
    Parse phrases like "7 days per week", "3 days a week", or a lone "7" context.
    """
    t = (s or "").strip()
    m = re.search(
        r"(\d{1,2})\s*days?(?:\s*per|\s*\/\s*|\s*a)?\s*week",
        t,
        re.I,
    )
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d{1,2})", t)
    if m:
        d = int(m.group(1))
        if 0 <= d <= 7:
            return d
    return None


def parse_dollars(s: str) -> float | None:
    """Parse a currency string to float, e.g. '$216.72' or '1,911.78'."""
    if not s or not str(s).strip():
        return None
    t = re.sub(r"[^\d.\-]", "", str(s).replace(",", ""))
    if not t or t in "-.":
        return None
    try:
        return float(Decimal(t))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def normalize_duration_hhmm(s: str) -> str:
    """
    Validate MDHHS-style H:MM / HH:MM / HHH:MM strings used for per-task monthly
    time and bottom-line monthly totals (hours may exceed 24, e.g. 70:48).
    When valid, returns the stripped original string to preserve padding for comparison.
    """
    t = (s or "").strip()
    m = re.fullmatch(r"(\d+):(\d{2})", t)
    if not m:
        return ""
    if int(m.group(2)) >= 60:
        return ""
    return t


def _best(patterns: Sequence[tuple[re.Pattern[str], int]], text: str) -> str:
    for pat, group in patterns:
        m = pat.search(text)
        if m:
            g = m.group(group).strip()
            if g:
                return re.sub(r"\s+", " ", g)
    return ""


# --- Field regexes (defensive, multiple label variants) ------------------------


def _patterns_client() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(r"Client\s*Name[:\s#]+([^\n]+?)(?=\n(?:Medicaid|Client|ID|Case|Section|\Z))", re.I | re.S), 1),
        (re.compile(r"Name\s*of\s*Client[:\s#]+([^\n]+?)(?=\n)", re.I), 1),
    ]


def _patterns_client_id() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(r"Client\s*(?:ID|Identification(?:\s*Number|(?:\s*#)?)?)[:\s#]*(\d{6,20})", re.I), 1),
        (re.compile(r"MDHHS\s*Client[:\s#]*ID[:\s#]*(\d{6,20})", re.I), 1),
        (re.compile(r"Client\s*ID[:\s#]*(\d{4,20})\b", re.I), 1),
    ]


def _patterns_county() -> list[tuple[re.Pattern[str], int]]:
    return [
        (
            re.compile(
                r"County\s+Name\s*[:\s#]+\s*(.+?)(?=\s*(?:Case|Client|$|\n))",
                re.I | re.M,
            ),
            1,
        ),
        (
            re.compile(
                r"(?<!County\s)County[:\s#]+([\d]{1,4}-?[A-Za-z]{2,}|[A-Za-z][A-Za-z\-\s'.]*)"
                r"(?=\n|Case|$)",
                re.I | re.M,
            ),
            1,
        ),
        (
            re.compile(
                r"County[:\s#]+([^:\n]{2,}?)(?=\n|\s*case|Client|\Z)",
                re.I,
            ),
            1,
        ),
    ]


def _patterns_case() -> list[tuple[re.Pattern[str], int]]:
    return [
        (
            re.compile(
                r"Case\s+(?:No\.?|Number|#)\s*[:\s#]*"
                r"([\d\-]+(?:-[A-Za-z]+)?(?:\-\d+[A-Za-z]?)?|[A-Z0-9]{3,}-\d+[A-Za-z]?)",
                re.I,
            ),
            1,
        ),
        (
            re.compile(
                r"Case\s*(?:No|Number|#)?[:\s#]*([A-Z0-9\-\s]+?)"
                r"(?=\n|\s*client\s|provider|section|adult|\Z)",
                re.I,
            ),
            1,
        ),
    ]


def _patterns_asw_name() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(r"(?:ASW|Adult\s*Services\s*Worker)(?:\s*Name)?[:\s#]+([^\n]+?)(?=\n(?:E-?mail|Phone|@))", re.I), 1),
        (re.compile(r"Adult\s*Services[:\s#]+([^\n]+?)(?=\n(?:E-?mail|@))", re.I), 1),
    ]


_EMAIL_PAT = re.compile(
    r"(?:E-?mail|Email)[:\s#]*\s*([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63})",
    re.I,
)


def _phone_pat() -> re.Pattern[str]:
    return re.compile(
        r"(?:Phone|Cell|Fax|Tel|Telephone|ASW\s*Phone)[:\s#]*\s*"
        r"((?:\(\s*\d{3}\s*\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?:\s*(?:x|ext\.?)\s*\d+)?)",
        re.I,
    )


def _auth_date_pat() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(
            r"(?:Authorization|Auth|Auth\.)\s*Date[:\s#]*(\d{1,2}/\d{1,2}/\d{2,4})",
            re.I,
        ), 1),
        (re.compile(
            r"Date[:\s#]*(\d{1,2}/\d{1,2}/\d{2,4})(?=[^\n]*(?:Provider|ASW|Section|Worker))",
            re.I,
        ), 1),
    ]


def _patterns_provider() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(r"Provider(?:\s*Name)?[:\s#]+([^\n]+?)(?=\n(?:Pay|Rate|NPI|FEIN|Section))", re.I), 1),
    ]


def _patterns_pay_rate() -> list[tuple[re.Pattern[str], int]]:
    return [
        (re.compile(
            r"(?:Pay|Hourly|Provider)?\s*Rate[:\s#]*(?:\$|USD\s*)?([\d,]+\.?\d*)\s*(?:per|\/\s*)?\s*hour",
            re.I,
        ), 1),
        (re.compile(
            r"\$\s*([\d,]+\.?\d*)\s*(?:per|\/\s*)?\s*hr",
            re.I,
        ), 1),
    ]


def _coerce_auth_date(mdy: str) -> str:
    s = mdy.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if not m:
        return s
    mo, d, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y if int(y) < 50 else "19" + y
    return f"{int(mo):02d}/{int(d):02d}/{y}"


def _extract_loose_email(text: str) -> str:
    m = _EMAIL_PAT.search(text)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63})\b",
        text,
        re.I,
    )
    return m.group(1).strip() if m else ""


def _extract_loose_phone(text: str) -> str:
    m = _phone_pat().search(text)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"(\(\s*\d{3}\s*\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?:\s*(?:x|ext\.?)\s*\d+)?",
        text,
    )
    return m.group(0).strip() if m else ""


# --- Task row parsing ----------------------------------------------------------


def _build_task_line_regex() -> re.Pattern[str]:
    return re.compile(
        rf"(?P<n>{_TASK_NAME_ALT})"
        rf"\s+(?P<min>\d{{1,2}}:\d{{2}})\s+"
        rf"(?P<days>(?:\d+\s*days?\s*per\s*week)|(?:\d+))"
        r"\s+"
        rf"(?P<mt>\d+:\d{{2}})\s+"
        rf"(?P<amt>[\$]?\s*\d[\d,]*\.?\d*)",
        re.I,
    )


_TASK_LINE_RE = _build_task_line_regex()

# More tolerant: days phrase optional / shortened — `:` occasionally OCR'd as `.`
_TASK_LOOSE_RE = re.compile(
    rf"(?P<n>{_TASK_NAME_ALT})"
    r"\D+?"
    rf"(?P<min>\d{{1,3}}[.:]\d{{2}})"
    r"\D+?"
    r"(?P<days>\d+)(?:\s*days?)?"
    r"\D+?"
    rf"(?P<mt>\d+[.:]\d{{2}})"
    r"\D+?"
    r"(?P<amt>[\$]?\s*\d[\d,]*\.?\d*)",
    re.I,
)


def _normalize_token_hhmm(token: str) -> str:
    """Map OCR ``02.05`` / ``70.48`` tokens into colon form; avoid mangling dollar ``216.72``."""
    t = (token or "").strip().replace(",", "")
    if not re.fullmatch(r"\d+\.\d{2}", t):
        return t.strip()
    a, sep, b = t.partition(".")
    try:
        hi = int(a)
        lo = int(b)
    except ValueError:
        return t.strip()
    if lo >= 60 or hi > 499:
        # Likely currency (minute part would be nonsense as clock minutes).
        return t.strip()
    return f"{a}:{b}"


def _row_to_str(row: Sequence[str | None]) -> str:
    parts = [re.sub(r"\s+", " ", (c or "").strip()) for c in row if c and str(c).strip()]
    return " ".join(parts)


def _parse_task_from_line(line: str) -> dict[str, Any] | None:
    line = re.sub(r"[ \t]+", " ", line.strip())
    m = _TASK_LINE_RE.search(line) or _TASK_LOOSE_RE.search(line)
    if not m:
        return None
    raw_name = m.group("n").strip()
    name = canonical_task_name(raw_name)
    if name is None:
        for lbl in _TASK_MATCH_ORDER:
            ll = line.lower()
            lbl_l = lbl.lower()
            if ll.startswith(lbl_l) or lbl_l in ll:
                name = canonical_task_name(lbl)
                if name is not None:
                    break
    if name is None:
        return None
    min_s = _normalize_token_hhmm(m.group("min"))
    days_s = m.group("days")
    raw_mt = _normalize_token_hhmm(m.group("mt").strip())
    if not normalize_duration_hhmm(raw_mt):
        return None
    mpd = parse_hhmm_to_minutes(min_s)
    if mpd is None:
        return None
    dpw = parse_days_per_week(str(days_s))
    if dpw is None and days_s.isdigit():
        v = int(days_s)
        if 0 <= v <= 7:
            dpw = v
    if dpw is None:
        return None
    amt = parse_dollars(m.group("amt"))
    if amt is None:
        return None
    return {
        "task_name": name,
        "min_per_day": int(mpd),
        "days_per_week": int(dpw),
        "monthly_time_str": raw_mt,
        "monthly_amount": float(amt),
    }


def _extract_tasks_from_text(text: str) -> list[dict[str, Any]]:
    """Harvest task rows — line-by-line *and* whole-text (cramped OCR / multi-row PDFs).

    IMPORTANT: Always run the sliding-window pass; some PDF emitters concatenate
    several tasks onto one visual line — an early-return after the newline pass
    would skip those downstream.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        t = _parse_task_from_line(line)
        if t and t["task_name"] not in seen:
            seen.add(t["task_name"])
            out.append(t)
    for m in _TASK_LOOSE_RE.finditer(text):
        d = _parse_task_from_line(m.group(0))
        if d and d["task_name"] not in seen:
            seen.add(d["task_name"])
            out.append(d)
    return out
def _extract_tasks_from_tables(tables: Iterable[list[list[str | None]]]) -> list[dict[str, Any]]:
    lines: list[str] = []
    for table in tables:
        for row in table:
            s = _row_to_str(row)
            if s:
                lines.append(s)
    return _extract_tasks_from_text("\n".join(lines))


def _merge_tasks(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for seq in (a, b):
        for d in seq:
            name = d.get("task_name", "")
            if not name:
                continue
            by[name] = d
    return [by[k] for k in KNOWN_TASKS if k in by]


# --- Totals ------------------------------------------------------------------


def _coerce_roll_up_hhmm(frag: str) -> str:
    """Map OCR quirks (e.g. ``70.48``) to canonical ``HH:MM`` for roll-up totals."""
    t = frag.strip().replace(",", ".")
    if re.fullmatch(r"\d+\.\d{2}", t):
        parts = t.split(".", 1)
        return f"{parts[0]}:{parts[1]}"
    return t.strip()


def _extract_monthly_totals(text: str) -> tuple[str, float]:
    """Find bottom monthly H:MM total (e.g. 70:48) and currency total."""
    m = re.search(
        r"(?:(?:Monthly\s*)?Total(?:\s*(?:Monthly\s*)?Time)?|Roll-?up|Grand\s*Total)"
        r"[:\s#]*"
        r"([\d\s]+[:.]\d{2})"
        r"\s*"
        r"([\$]?\s*\d[\d,]*\.?\d{0,2})",
        text,
        re.I | re.S,
    )
    if m:
        hhmm_raw = _coerce_roll_up_hhmm(re.sub(r"\s+", "", m.group(1)))
        if normalize_duration_hhmm(hhmm_raw):
            am = parse_dollars(m.group(2))
            if am is not None:
                return (hhmm_raw.strip(), float(am))
    lines = [re.sub(r"\s+", " ", L.strip()) for L in text.splitlines() if L.strip()]
    for L in reversed(lines[-30:]):
        if "total" not in L.lower() and "month" not in L.lower():
            continue
        t_m = re.search(r"(?:^|\s)([\d]+[:.](?:\s*\d|\d)+\d{2})\b", L) or re.search(
            r"(\d+[:.](?:\s*\d{2}|\d{2}))\b", L.replace(" ", "")
        )
        if not t_m:
            t_m = re.search(r"(\d+:\d{2})\b", L)
        a_m = re.search(r"([\$]?\s*\d[\d,]*\.\d{2})\b", L)
        if t_m and a_m:
            hhmm = _coerce_roll_up_hhmm(t_m.group(1).replace(" ", ""))
            if normalize_duration_hhmm(hhmm):
                am = parse_dollars(a_m.group(1))
                if am is not None:
                    return (hhmm, float(am))
    for L in reversed(lines[-6:]):
        t_m = re.search(r"(\d+[:.](?:\s*)?\d{2})\b", re.sub(r"\s+", "", L))
        if not t_m:
            t_m = re.search(r"(\d+:\d{2})\b", L)
        a_m = re.search(r"([\$]?\s*\d[\d,]*\.\d{2,})\b", L)
        if t_m and a_m:
            hhmm = _coerce_roll_up_hhmm(t_m.group(1))
            if normalize_duration_hhmm(hhmm):
                am = parse_dollars(a_m.group(1))
                if am is not None and am >= 1.0:
                    return (hhmm, float(am))
    return ("", 0.0)


def _rapidocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _should_merge_ocr_for_tasks(
    n_tasks: int,
    *,
    mt: str,
    tot: float,
) -> bool:
    """When RapidOCR is cheap enough, supplement until we look 'saturated' with tasks."""
    if os.environ.get("MDHHS_OCR_TASK_SUPPLEMENT", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    if not _rapidocr_available():
        return False
    if n_tasks <= 0:
        return True
    if tot <= 0.0:
        return True
    if not (mt or "").strip():
        return True
    try:
        saturate_at = int(os.environ.get("MDHHS_EXTRACT_TASK_SATURATION", "15").strip())
        saturate_at = max(4, min(24, saturate_at))
    except ValueError:
        saturate_at = 15
    if n_tasks < saturate_at:
        return True
    return False


def _task_text_with_optional_ocr(
    path: Path,
    text: str,
    merged_task_count: int,
    *,
    mt: str,
    tot: float,
) -> str:
    if text.strip() and _should_merge_ocr_for_tasks(merged_task_count, mt=mt, tot=tot):
        try:
            dpi = int(os.environ.get("MDHHS_OCR_DPI", "").strip() or "280")
            dpi = max(180, min(400, dpi))
        except ValueError:
            dpi = 280
        otxt = _ocr_pdf_text(path, dpi=dpi)
        if len(otxt.strip()) >= 120:
            logger.info(
                "Merging RapidOCR text with native extract for fuller task/table coverage "
                "(%d task(s); roll-up OCR time=%r $=%.2f).",
                merged_task_count,
                mt[:12] if mt else mt,
                tot,
            )
            return text.rstrip() + "\n------ OCR ------\n" + otxt
    return text


def _apply_authoritative_roll_up_from_tasks(f: ExtractedForm) -> None:
    """Set roll-up monthly time / $ from parsed tasks + pay — aligns with workbook Σ lines.

    PDF bottom-line OCR is kept only for logging when it materially disagrees: if the
    parsed row set is incomplete, OCR $ may legitimately exceed our Σ lines.
    """
    tasks = list(f.tasks or [])
    if not tasks or f.pay_rate <= 0:
        return
    from .calculate import compute_mdhhs_form_amount, compute_mdhhs_form_minutes

    ocr_was = float(f.monthly_total_amount or 0.0)
    auth_amt = compute_mdhhs_form_amount(tasks, f.pay_rate)
    auth_mm = compute_mdhhs_form_minutes(tasks)
    hh = auth_mm // 60
    mm_part = auth_mm % 60
    roll = f"{hh}:{mm_part:02d}"

    f.monthly_total_amount = auth_amt
    f.monthly_total_time_str = roll

    if ocr_was >= auth_amt * 1.03:
        logger.warning(
            "Form roll-up OCR $%.2f exceeds Σ parsed lines $%.2f — probable missing "
            "task rows; reviewer should reconcile against the PDF authorization grid.",
            ocr_was,
            auth_amt,
        )


# --- MDHHS-6064-P paired field extraction ------------------------------------
#
# The MDHHS-6064-P lays fields out in header/value pairs where the VALUE lives
# on the line BELOW the label. Example as pdfplumber emits it:
#
#     Client Name Client ID Number
#     Ottilie Smith 80738972
#     County Name Case Number
#     82-WAYNE 350195-2
#     ASW Email Address
#     K Abdelkhaliq 313-407-4457
#     Adult Services Worker (ASW) Name
#     AbdelkhaliqK@michigan.gov
#
# Inline "Label: Value" regexes miss these, so we parse them positionally first
# and only fall back to the old patterns when the paired-field approach fails.


_PHONE_IN_LINE_RE = re.compile(
    r"(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?:\s*(?:x|ext\.?)\s*\d+)?"
)
_EMAIL_IN_LINE_RE = re.compile(
    r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}", re.I
)
_CASE_NUMBER_RE = re.compile(
    r"\b\d{5,}-\d+[A-Za-z]?\b|\b\d{3,}-\d+\b|\b\d{6,}-\d+\b",
)


def _split_client_name_id_row(row: str) -> tuple[str, str] | None:
    """Last token all-digits 6–20 wide → client_id; rest → client name."""
    row = re.sub(r"\s+", " ", (row or "").strip())
    if not row:
        return None
    toks = row.split()
    if not toks:
        return None
    last = toks[-1]
    if last.isdigit() and 6 <= len(last) <= 20:
        name = " ".join(toks[:-1]).strip()
        if name:
            return name, last
    m = re.match(r"^(.+?)\s+(\d{6,20})\s*$", row)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None


def _extract_paired_fields(text: str) -> dict[str, str]:
    """
    Parse MDHHS-6064-P header/value pairs where the value appears on the line
    below the header. Returns only the keys that were successfully extracted;
    callers should fall back to inline regex patterns for anything missing.
    """
    raw_lines = text.splitlines()
    lines = [re.sub(r"\s+", " ", L).strip() for L in raw_lines]

    def next_nonblank(start: int) -> tuple[int, str] | None:
        j = start + 1
        while j < len(lines):
            if lines[j]:
                return j, lines[j]
            j += 1
        return None

    out: dict[str, str] = {}
    n = len(lines)

    for i in range(n):
        L = lines[i]
        if not L:
            continue
        low = L.lower()

        if "client name" in low and "client id" in low and "number" in low:
            nx = next_nonblank(i)
            if nx and "client_name" not in out:
                _, row = nx
                split = _split_client_name_id_row(row)
                if split:
                    out["client_name"], out["client_id"] = split

        if "county name" in low and "case number" in low:
            nx = next_nonblank(i)
            if nx and "case_number" not in out:
                _, row = nx
                mcase = _CASE_NUMBER_RE.search(row)
                if mcase:
                    case = mcase.group(0).strip()
                    county = row[: mcase.start()].strip()
                    if county and county.lower() not in ("name", "county", "county name"):
                        out["county_name"] = county
                    out["case_number"] = case

        if re.search(r"provider\s+name\b", low) and not re.search(
            r"\bworker\b", low
        ):
            nx = next_nonblank(i)
            if nx and "provider_name" not in out:
                _, row = nx
                clean = row.strip()
                if clean and "@" not in clean and not clean.lower().startswith("pay"):
                    out["provider_name"] = clean

            j = i + 1
            scanned = 0
            while j < n and scanned < 6:
                row = lines[j]
                if row:
                    mph = re.match(
                        r"^(.+?)\s+((?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}"
                        r"(?:\s*(?:x|ext\.?)\s*\d+)?)\s*$",
                        row,
                    )
                    if mph and "asw_name" not in out:
                        nm = mph.group(1).strip()
                        if not re.search(
                            r"\b(adult\s*services|worker|email|address)\b", nm, re.I
                        ):
                            out["asw_name"] = nm
                            out["asw_phone"] = mph.group(2).strip()
                    mem = _EMAIL_IN_LINE_RE.search(row)
                    if mem and "asw_email" not in out:
                        out["asw_email"] = mem.group(0).strip()
                    scanned += 1
                j += 1

        if re.search(r"adult\s*services\s*worker.*\(asw\).*name", low):
            nx = next_nonblank(i)
            if nx and "asw_name" not in out:
                _, row = nx
                if (
                    "@" not in row
                    and not _PHONE_IN_LINE_RE.search(row)
                    and not re.search(r"\b(address|phone|email|provider|section)\b", row, re.I)
                ):
                    out["asw_name"] = row.strip()

        if re.fullmatch(r"(?:authorization\s+)?date", low):
            nx = next_nonblank(i)
            if nx and "auth_date" not in out:
                _, row = nx
                mdt = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", row)
                if mdt:
                    out["auth_date"] = _coerce_auth_date(mdt.group(1))

    return out


def _extract_provider_name(text: str) -> str:
    """
    Find the home-care agency name that sits at the top of the form (above the
    SECTION 1 boilerplate). Prefers anything ending in "Home Care"; falls back
    to the first meaningful alphabetic line before "MDHHS" or "SECTION 1".
    """
    lines = [re.sub(r"\s+", " ", L).strip() for L in text.splitlines()]
    head = []
    for L in lines[:60]:
        low = L.lower()
        if "mdhhs" in low or "section 1" in low or re.search(r"section\s*i\b", low):
            break
        head.append(L)

    for L in head:
        m = re.search(
            r"\b([A-Z][A-Za-z&.'\-]*(?:\s+[A-Z][A-Za-z&.'\-]*){0,4}\s+Home\s+Care)\b",
            L,
        )
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()

    for L in head:
        if not L:
            continue
        low = L.lower()
        if any(
            k in low
            for k in (
                "department", "health", "human services", "authorization",
                "provider pay", "client", "county", "case", "section",
                "adult services", "worker", "date of", "michigan",
                "time and task", "provider time",
            )
        ):
            continue
        if _PHONE_IN_LINE_RE.search(L) or "@" in L:
            continue
        if re.search(r"\d", L):
            continue
        if len(L) < 4 or len(L) > 80:
            continue
        if not re.search(r"[A-Za-z]{3,}", L):
            continue
        return L
    return ""


def _extract_pay_rate_loose(text: str) -> float:
    """
    Find "$DD.DD" anywhere near "Pay Rate" header. Allows the $-value to appear
    on a neighboring line (above or below the label), which is how MDHHS-6064-P
    renders when pdfplumber interleaves columns.
    """
    lines = text.splitlines()
    money_re = re.compile(r"\$\s*(\d+\.\d{2})")
    for i, L in enumerate(lines):
        if not re.search(r"pay\s*rate", L, re.I):
            continue
        window = lines[max(0, i - 2) : i + 4]
        for w in window:
            mm = money_re.search(w)
            if mm:
                try:
                    val = float(mm.group(1))
                except (TypeError, ValueError):
                    continue
                if 1.0 <= val <= 500.0:
                    return val
    return 0.0


_MPD_MAX = 1440


def _monthly_rounded_column_to_auth_minutes(s: str) -> int | None:
    """Task-row «Time/Month» H:HH as half-even rounded integers (minutes)."""
    raw = normalize_duration_hhmm(_normalize_token_hhmm(s))
    if not raw:
        return None
    try:
        h_s, mm_s = raw.split(":", 1)
        return int(h_s) * 60 + int(mm_s)
    except (ValueError, TypeError):
        return None


def _reconcile_min_per_day_from_monthly_column(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    When OCR puts the wrong token in «Duration», ``min_per_day`` no longer agrees
    with the printed Monthly Time column (mdhhs line uses half-even mpd × dpw × 4.3).

    Prefer the Monthly Time figures — they OCR more reliably — and overwrite
    ``min_per_day`` when we find a plausible match. Logs task names patched.
    """
    from .calculate import compute_monthly_minutes_rounded

    out: list[dict[str, Any]] = []

    for t in tasks:
        d = dict(t)
        nm = str(d.get("task_name") or "").strip()
        mt_s = _normalize_token_hhmm(str(d.get("monthly_time_str") or "").strip())
        auth_mm = _monthly_rounded_column_to_auth_minutes(mt_s)
        if auth_mm is None or auth_mm <= 0:
            out.append(d)
            continue

        try:
            dpw = int(d.get("days_per_week") or 0)
            mpd_raw = int(d.get("min_per_day") or 0)
        except (TypeError, ValueError):
            out.append(d)
            continue
        if dpw <= 0 or mpd_raw <= 0:
            out.append(d)
            continue

        modeled = compute_monthly_minutes_rounded(mpd_raw, dpw)
        if modeled == auth_mm:
            out.append(d)
            continue

        exact: list[int] = []
        for cand in range(1, _MPD_MAX + 1):
            if compute_monthly_minutes_rounded(cand, dpw) == auth_mm:
                exact.append(cand)

        picked: int | None = None
        if exact:
            picked = min(exact, key=lambda c: abs(c - mpd_raw))
        else:
            best_c: int | None = None
            best_diff = 10**9
            for cand in range(1, _MPD_MAX + 1):
                cm = compute_monthly_minutes_rounded(cand, dpw)
                delta = abs(cm - auth_mm)
                if delta < best_diff:
                    best_diff = delta
                    best_c = cand
            if best_c is not None and best_diff <= 2:
                picked = best_c

        if picked is not None and picked != mpd_raw and abs(picked - mpd_raw) <= 480:
            d["min_per_day"] = picked
            logger.info(
                "Reconciled %r duration: mpd %d → %d (Monthly Time implies %d min @ %dw/wk)",
                nm,
                mpd_raw,
                picked,
                auth_mm,
                dpw,
            )
        out.append(d)

    return out


def _compute_task_amounts(
    tasks: list[dict[str, Any]], pay_rate: float
) -> list[dict[str, Any]]:
    """
    Deterministic belt-and-suspenders for per-task ``monthly_amount``.

    OCR often mis-assigns dollar columns; Schedule / validation use
    :func:`~calculate.compute_task_amount` off ``min_per_day`` × ``days_per_week``.
    When pay rate is known and both structural fields are positive, always derive
    the line ``$`` from math (half-even cents) so results match authorization totals.
    """
    from .calculate import compute_task_amount

    out: list[dict[str, Any]] = []
    for t in tasks:
        d = dict(t)
        try:
            mpd = int(d.get("min_per_day") or 0)
            dpw = int(d.get("days_per_week") or 0)
        except (TypeError, ValueError):
            mpd, dpw = 0, 0

        if pay_rate and pay_rate > 0 and mpd > 0 and dpw > 0:
            d["monthly_amount"] = compute_task_amount(mpd, dpw, float(pay_rate))
        else:
            try:
                d["monthly_amount"] = float(d.get("monthly_amount") or 0.0)
            except (TypeError, ValueError):
                d["monthly_amount"] = 0.0

        out.append(d)
    return out


def _reject_spurious_header(s: str) -> str:
    """Drop label tails like lone ``Name`` that regexes sometimes capture as values."""
    t = re.sub(r"\s+", " ", (s or "").strip())
    if t.lower() in ("name", "county", "county name", "case number", "case"):
        return ""
    return t


# --- Public API --------------------------------------------------------------


def _pypdfium_text(path: Path) -> str:
    """
    Secondary text extraction using pypdfium2 (PDFium).

    Often recovers text from PDFs whose font encoding or ToUnicode map
    trips pdfplumber — it's cheap to try and requires no extra binaries
    because pypdfium2 is already a pdfplumber dependency.
    """
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — pypdfium2 is a pdfplumber dep
        return ""
    parts: list[str] = []
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as e:
        logger.debug("pypdfium2 failed to open %s: %s", path, e)
        return ""
    try:
        for i in range(len(doc)):
            page = doc[i]
            try:
                tp = page.get_textpage()
                try:
                    t = tp.get_text_range() or ""
                finally:
                    tp.close()
                if t.strip():
                    parts.append(t)
            finally:
                page.close()
    finally:
        doc.close()
    return "\n".join(parts)


def _ocr_pdf_text(path: Path, *, dpi: int = 220) -> str:
    """
    Last-resort OCR for image-only (scanned) PDFs.

    Uses pypdfium2 to rasterize pages and RapidOCR (ONNX Runtime) to
    recognize text. Both are pure-Python wheels — no Tesseract / Poppler
    install required. Returns ``""`` when RapidOCR is not installed so
    callers can treat OCR as an optional capability.

    DPI 220 is a compromise between accuracy and cold-start latency
    (~1-2 s/page on modern CPUs). Bump to 300 for poor-quality scans.
    """
    try:
        import numpy as np  # type: ignore[import-not-found]
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover — hard deps
        logger.debug("OCR dependencies missing: %s", e)
        return ""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError:
        logger.info(
            "RapidOCR not installed — skipping OCR fallback. "
            "pip install rapidocr-onnxruntime to enable."
        )
        return ""

    try:
        engine = RapidOCR()
    except Exception as e:  # model download / init can fail offline
        logger.warning("RapidOCR failed to initialize: %s", e)
        return ""

    scale = dpi / 72.0
    pages_text: list[str] = []
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as e:
        logger.warning("pypdfium2 could not open %s for OCR: %s", path, e)
        return ""
    try:
        n_pages = len(doc)
        logger.info("OCR'ing %d page(s) of %s (no text layer detected)", n_pages, path.name)
        for i in range(n_pages):
            page = doc[i]
            try:
                pil = page.render(scale=scale).to_pil().convert("RGB")
                arr = np.array(pil)
                result, _ = engine(arr)
            finally:
                page.close()
            if not result:
                continue
            # RapidOCR returns rows of [bbox, text, confidence], top-to-bottom.
            pages_text.append("\n".join(str(r[1]) for r in result))
    finally:
        doc.close()
    joined = "\n".join(pages_text)
    if joined.strip():
        logger.info("OCR recovered %d characters from %s", len(joined), path.name)
    return joined


def extract_from_pdf(path: str | Path) -> ExtractedForm:
    """
    Open an MDHHS-6064-P PDF, extract text and table-like rows, and map fields
    to :class:`ExtractedForm`. Does not call external LLMs.

    Extraction falls through a three-stage ladder:

    1. ``pdfplumber`` — preferred, preserves layout for table parsing.
    2. ``pypdfium2`` — recovers text from PDFs with quirky font encoding.
    3. ``RapidOCR`` — OCR for true image-only scanned PDFs (optional
       dependency; activates automatically if installed).

    Roll-up ``monthly_total_amount`` / ``monthly_total_time_str`` are aligned to
    per-line Σ (see :mod:`calculate`), not the OCR bottom line, so totals match
    the schedule/workbook math. Supplemental RapidOCR can merge extra task rows
    when few lines parse (see env ``MDHHS_OCR_TASK_SUPPLEMENT``,
    ``MDHHS_EXTRACT_TASK_SATURATION``, ``MDHHS_OCR_DPI``).
    """
    path = Path(path)
    full_text: list[str] = []
    all_tables: list[list[list[str | None]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full_text.append(t)
            for table in page.extract_tables() or []:
                if table:
                    all_tables.append(table)
    text = "\n".join(full_text)

    # Fallback 1: pypdfium2 direct text extraction — same-speed rescue path
    # for PDFs with unusual fonts that pdfplumber silently skips.
    if not text.strip():
        text = _pypdfium_text(path)

    # Fallback 2: OCR the rendered page images. Only runs when both native
    # text extractors returned nothing, because OCR is noticeably slower.
    if not text.strip():
        text = _ocr_pdf_text(path)

    if not text.strip():
        return ExtractedForm()

    f = ExtractedForm()

    paired = _extract_paired_fields(text)

    f.client_name = paired.get("client_name") or _best(_patterns_client(), text)
    f.client_id = paired.get("client_id") or _best(_patterns_client_id(), text)
    f.county_name = _reject_spurious_header(
        paired.get("county_name") or _best(_patterns_county(), text),
    )
    f.case_number = paired.get("case_number") or re.sub(
        r"\s+",
        " ",
        _best(_patterns_case(), text).strip(),
    )
    f.asw_name = paired.get("asw_name") or _best(_patterns_asw_name(), text)
    f.asw_email = (
        paired.get("asw_email")
        or _best([(_EMAIL_PAT, 1)], text)
        or _extract_loose_email(f.asw_name + " " + text)
    )
    f.asw_phone = (
        paired.get("asw_phone")
        or _best([(_phone_pat(), 1)], text)
        or _extract_loose_phone(text)
    )
    if paired.get("auth_date"):
        f.auth_date = paired["auth_date"]
    else:
        for pat, g in _auth_date_pat():
            m = pat.search(text)
            if m:
                f.auth_date = _coerce_auth_date(m.group(g))
                break

    f.provider_name = (
        paired.get("provider_name") or _extract_provider_name(text) or _best(_patterns_provider(), text)
    )

    pr_val = _extract_pay_rate_loose(text)
    if pr_val > 0:
        f.pay_rate = pr_val
    else:
        pr = _best(_patterns_pay_rate(), text)
        if pr:
            p = parse_dollars(pr) if re.search(r"\d", pr) else None
            if p is not None:
                f.pay_rate = float(p)

    t_from_text_pre = _extract_tasks_from_text(text)
    t_from_table_pre = _extract_tasks_from_tables(all_tables) if all_tables else []
    merged_pre = _merge_tasks(t_from_text_pre, t_from_table_pre)
    mt_hint, tot_hint = _extract_monthly_totals(text)
    text_tasks = _task_text_with_optional_ocr(
        path,
        text,
        len(merged_pre),
        mt=mt_hint,
        tot=tot_hint,
    )
    t_from_text = _extract_tasks_from_text(text_tasks)
    t_from_table = _extract_tasks_from_tables(all_tables) if all_tables else []
    f.tasks = _merge_tasks(t_from_text, t_from_table)

    mt, tot = _extract_monthly_totals(text_tasks)
    f.monthly_total_time_str = mt
    f.monthly_total_amount = float(tot)

    f.tasks = _reconcile_min_per_day_from_monthly_column(f.tasks)

    # Per-line $ from Duration × frequency (OCR $ column is unreliable).
    f.tasks = _compute_task_amounts(f.tasks, f.pay_rate)
    _apply_authoritative_roll_up_from_tasks(f)

    return f


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.extract <path-to.pdf>", file=sys.stderr)
        sys.exit(1)
    form = extract_from_pdf(sys.argv[1])
    print(json.dumps(asdict(form), indent=2, ensure_ascii=False))
