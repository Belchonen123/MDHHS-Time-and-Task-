"""Optional Claude-assisted ScheduleConfig suggestions during plan re-run."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .calculate import ScheduleConfig, TaskPlacement

logger = logging.getLogger(__name__)

_WEEK = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def merge_llm_config_into_base(
    raw: dict[str, Any] | None,
    authoritative_tasks: list[dict[str, Any]],
    base: ScheduleConfig,
) -> ScheduleConfig:
    """Apply LLM JSON on top of base; task minutes/dpw always come from OCR tasks."""
    if not raw or not isinstance(raw, dict):
        return base

    fb_by_name = {t.task_name: t for t in base.tasks}
    llm_by_name: dict[str, dict[str, Any]] = {}
    for row in raw.get("tasks") or []:
        if isinstance(row, dict) and row.get("task_name"):
            llm_by_name[str(row["task_name"]).strip()] = row

    placements: list[TaskPlacement] = []
    for auth in authoritative_tasks:
        name = str(auth.get("task_name", "") or "").strip()
        if not name:
            continue
        mpd = int(auth.get("min_per_day") or 0)
        dpw = int(auth.get("days_per_week") or 0)
        row = llm_by_name.get(name)
        fb = fb_by_name.get(name)
        if row is None:
            if fb:
                placements.append(
                    TaskPlacement(
                        task_name=name,
                        min_per_day=mpd,
                        days_per_week=dpw,
                        selected_weekdays=list(fb.selected_weekdays),
                        selected_dates=list(fb.selected_dates),
                        placement_fallback=fb.placement_fallback,
                        preferred_weekdays=list(fb.preferred_weekdays),
                        preferred_dates=list(fb.preferred_dates),
                        placement_overrides=list(fb.placement_overrides),
                        preference_unspecified=fb.preference_unspecified,
                    )
                )
            else:
                placements.append(
                    TaskPlacement(
                        task_name=name,
                        min_per_day=mpd,
                        days_per_week=dpw,
                        selected_weekdays=[],
                        selected_dates=[],
                        preferred_weekdays=[],
                        preferred_dates=[],
                        placement_overrides=[],
                        preference_unspecified=True,
                    )
                )
            continue

        sw = [
            str(x) for x in (row.get("selected_weekdays") or []) if str(x) in _WEEK
        ]
        sw = list(dict.fromkeys(sw))
        sd = [str(x) for x in (row.get("selected_dates") or []) if x]
        sw_from_fb = False

        # Take the first dpw weekdays from the model before treating undersupply
        # as a mismatch fallback (otherwise len(sw)>dpw never reaches truncation).
        if dpw > 0 and len(sw) > dpw:
            sw = sw[:dpw]

        if dpw > 0 and len(sw) != dpw:
            logger.info(
                "LLM weekday count mismatch for %s: got %d want %d, using base",
                name,
                len(sw),
                dpw,
            )
            if fb:
                sw = list(fb.selected_weekdays)
                sd = list(fb.selected_dates)
                sw_from_fb = True

        if sw_from_fb:
            placement_fallback = fb.placement_fallback if fb else False
        elif "placement_fallback" in row:
            placement_fallback = bool(row.get("placement_fallback"))
        else:
            placement_fallback = fb.placement_fallback if fb else False

        placements.append(
            TaskPlacement(
                task_name=name,
                min_per_day=mpd,
                days_per_week=dpw,
                selected_weekdays=sw,
                selected_dates=sd,
                placement_fallback=placement_fallback,
                preferred_weekdays=list(sw),
                preferred_dates=list(sd),
                placement_overrides=[],
                preference_unspecified=sw_from_fb,
            )
        )

    times = dict(base.start_time_by_weekday)
    rt = raw.get("start_time_by_weekday")
    if isinstance(rt, dict):
        for k, v in rt.items():
            ks = str(k)
            vs = str(v).strip() if v is not None else ""
            if ks in times and vs:
                times[ks] = vs

    return ScheduleConfig(tasks=placements, start_time_by_weekday=times)


def optimize_schedule_config_with_llm(
    *,
    tasks: list[dict[str, Any]],
    year: int,
    month: int,
    preferred_window: dict[str, Any],
    base_config: ScheduleConfig,
    user_notes: str,
) -> ScheduleConfig:
    """Call Anthropic; merge JSON suggestions onto base_config."""
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set; add it to the backend environment "
            "to use Claude schedule suggestions."
        )

    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ValueError("anthropic package is not installed") from e

    model = (os.environ.get("ANTHROPIC_MODEL") or "").strip() or "claude-sonnet-4-20250514"

    task_summary = [
        {
            "task_name": t.get("task_name"),
            "min_per_day": t.get("min_per_day"),
            "days_per_week": t.get("days_per_week"),
        }
        for t in tasks
    ]

    system = (
        "You optimize home care visit schedules. Output a single JSON object only — "
        "no markdown fences, no commentary. Schema:\n"
        '{"tasks":[{"task_name":string,"selected_weekdays":['
        '"Monday"|...|"Sunday",...],"selected_dates":["YYYY-MM-DD",...]}...],'
        '"start_time_by_weekday":{"Monday":"h:MM AM/PM",...}}\n\n'
        "Rules:\n"
        "- Include every task from input tasks exactly once (task_name must match).\n"
        "- Do not output min_per_day or days_per_week; those are fixed server-side.\n"
        "- selected_weekdays: full English day names; count must equal that task's "
        "days_per_week from the input (unless days_per_week is 0).\n"
        "- selected_dates: optional ISO dates in the service month for catch-up visits.\n"
        "- start_time_by_weekday: include all seven keys with AM/PM times.\n"
        "- Apply user_notes when compatible with days_per_week and the calendar.\n"
    )

    payload = {
        "service_month": {"year": year, "month": month},
        "preferred_window": preferred_window,
        "tasks": task_summary,
        "current_config": base_config.to_dict(),
        "user_notes": (user_notes or "").strip() or "(none)",
    }

    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            ],
        )
    except Exception as e:
        logger.exception("Anthropic schedule request failed")
        raise ValueError(f"Claude request failed: {e}") from e

    text_parts: list[str] = []
    for block in msg.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
    text = _strip_code_fence("".join(text_parts))

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("LLM schedule JSON parse failed: %s", e)
        raise ValueError(
            f"Claude returned invalid JSON for schedule suggestions: {e}"
        ) from e

    return merge_llm_config_into_base(data, tasks, base_config)
