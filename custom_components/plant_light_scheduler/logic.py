"""Pure scheduling logic for Plant Light Scheduler."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_time(value: object) -> str:
    """Validate and return a 24-hour HH:MM value."""
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        raise ValueError("Time must use 24-hour HH:MM format")
    return value


def time_to_minutes(value: str) -> int:
    """Convert HH:MM to minutes after midnight."""
    validate_time(value)
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def is_active(on_time: str, off_time: str, minute_of_day: int) -> bool:
    """Return whether a schedule should be active at a local-time minute."""
    if not 0 <= minute_of_day < 24 * 60:
        raise ValueError("minute_of_day must be between 0 and 1439")

    on_minute = time_to_minutes(on_time)
    off_minute = time_to_minutes(off_time)
    if on_minute == off_minute:
        return False
    if on_minute < off_minute:
        return on_minute <= minute_of_day < off_minute
    return minute_of_day >= on_minute or minute_of_day < off_minute


def entities_at_boundary(
    schedules: Iterable[Mapping[str, Any]], minute_of_day: int
) -> set[str]:
    """Return entities whose enabled schedule starts or stops this minute."""
    affected: set[str] = set()
    for schedule in schedules:
        if not schedule.get("enabled", True):
            continue
        if minute_of_day not in {
            time_to_minutes(schedule["on"]),
            time_to_minutes(schedule["off"]),
        }:
            continue
        affected.update(schedule["entities"])
    return affected


def desired_state(
    schedules: Iterable[Mapping[str, Any]], entity_id: str, minute_of_day: int
) -> bool:
    """Return true when any enabled schedule currently wants an entity on."""
    return any(
        schedule.get("enabled", True)
        and entity_id in schedule["entities"]
        and is_active(schedule["on"], schedule["off"], minute_of_day)
        for schedule in schedules
    )

