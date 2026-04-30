"""Unit tests for LLM schedule merge helper (no live Anthropic calls)."""

from __future__ import annotations

from app.calculate import ScheduleConfig, TaskPlacement, _default_start_time_by_weekday
from app.llm_schedule import merge_llm_config_into_base


def _base_config() -> ScheduleConfig:
    return ScheduleConfig(
        tasks=[
            TaskPlacement(
                task_name="Bathing",
                min_per_day=30,
                days_per_week=3,
                selected_weekdays=["Monday", "Wednesday", "Friday"],
                selected_dates=[],
            ),
            TaskPlacement(
                task_name="Grooming",
                min_per_day=15,
                days_per_week=2,
                selected_weekdays=["Saturday", "Sunday"],
                selected_dates=[],
            ),
        ],
        start_time_by_weekday=_default_start_time_by_weekday("7:00 AM", "12:00 PM"),
    )


def test_merge_llm_updates_weekdays_and_times() -> None:
    base = _base_config()
    raw = {
        "tasks": [
            {
                "task_name": "Bathing",
                "selected_weekdays": ["Tuesday", "Thursday", "Saturday"],
                "selected_dates": [],
            },
            {
                "task_name": "Grooming",
                "selected_weekdays": ["Monday", "Friday"],
                "selected_dates": [],
            },
        ],
        "start_time_by_weekday": {
            "Monday": "8:00 AM",
            "Tuesday": "8:00 AM",
            "Wednesday": "8:00 AM",
            "Thursday": "8:00 AM",
            "Friday": "8:00 AM",
            "Saturday": "1:00 PM",
            "Sunday": "1:00 PM",
        },
    }
    auth = [
        {"task_name": "Bathing", "min_per_day": 30, "days_per_week": 3},
        {"task_name": "Grooming", "min_per_day": 15, "days_per_week": 2},
    ]
    out = merge_llm_config_into_base(raw, auth, base)
    assert out.tasks[0].selected_weekdays == ["Tuesday", "Thursday", "Saturday"]
    assert out.tasks[0].min_per_day == 30
    assert out.tasks[1].selected_weekdays == ["Monday", "Friday"]
    assert out.start_time_by_weekday["Monday"] == "8:00 AM"
    assert out.start_time_by_weekday["Saturday"] == "1:00 PM"


def test_merge_llm_weekday_count_mismatch_falls_back() -> None:
    base = _base_config()
    raw = {
        "tasks": [
            {
                "task_name": "Bathing",
                "selected_weekdays": ["Monday"],
                "selected_dates": [],
            },
            {
                "task_name": "Grooming",
                "selected_weekdays": ["Monday"],
                "selected_dates": [],
            },
        ],
    }
    auth = [
        {"task_name": "Bathing", "min_per_day": 30, "days_per_week": 3},
        {"task_name": "Grooming", "min_per_day": 15, "days_per_week": 2},
    ]
    out = merge_llm_config_into_base(raw, auth, base)
    assert out.tasks[0].selected_weekdays == ["Monday", "Wednesday", "Friday"]
    assert out.tasks[1].selected_weekdays == ["Saturday", "Sunday"]
