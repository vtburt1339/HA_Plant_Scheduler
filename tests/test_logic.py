"""Tests for the dependency-free schedule calculations."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

LOGIC_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "plant_light_scheduler"
    / "logic.py"
)
SPEC = importlib.util.spec_from_file_location("plant_light_scheduler_logic", LOGIC_PATH)
assert SPEC and SPEC.loader
logic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(logic)


@pytest.mark.parametrize(
    ("minute", "expected"),
    [(7 * 60 + 59, False), (8 * 60, True), (20 * 60 + 59, True), (21 * 60, False)],
)
def test_daytime_schedule_boundaries(minute: int, expected: bool) -> None:
    """Daytime ranges include on and exclude off."""
    assert logic.is_active("08:00", "21:00", minute) is expected


@pytest.mark.parametrize(
    ("minute", "expected"),
    [
        (17 * 60 + 59, False),
        (18 * 60, True),
        (23 * 60, True),
        (5 * 60 + 59, True),
        (6 * 60, False),
    ],
)
def test_overnight_schedule(minute: int, expected: bool) -> None:
    """Ranges that cross midnight stay active through midnight."""
    assert logic.is_active("18:00", "06:00", minute) is expected


def test_overlapping_schedules_use_any_active_range() -> None:
    """One schedule ending does not turn off an entity another still needs."""
    schedules = [
        {
            "id": "first",
            "entities": ["switch.grow_light"],
            "on": "08:00",
            "off": "12:00",
            "enabled": True,
        },
        {
            "id": "second",
            "entities": ["switch.grow_light"],
            "on": "11:00",
            "off": "14:00",
            "enabled": True,
        },
    ]
    assert logic.desired_state(schedules, "switch.grow_light", 12 * 60)
    assert not logic.desired_state(schedules, "switch.grow_light", 14 * 60)


def test_disabled_schedule_is_ignored() -> None:
    """Disabled schedules affect neither desired state nor boundaries."""
    schedules = [
        {
            "id": "disabled",
            "entities": ["switch.grow_light"],
            "on": "08:00",
            "off": "21:00",
            "enabled": False,
        }
    ]
    assert not logic.desired_state(schedules, "switch.grow_light", 12 * 60)
    assert logic.entities_at_boundary(schedules, 8 * 60) == set()


def test_boundary_entities_are_deduplicated() -> None:
    """An entity shared by schedules is applied only once per boundary."""
    schedules = [
        {
            "id": "one",
            "entities": ["switch.a", "switch.b"],
            "on": "08:00",
            "off": "21:00",
            "enabled": True,
        },
        {
            "id": "two",
            "entities": ["switch.a"],
            "on": "08:00",
            "off": "18:00",
            "enabled": True,
        },
    ]
    assert logic.entities_at_boundary(schedules, 8 * 60) == {
        "switch.a",
        "switch.b",
    }


@pytest.mark.parametrize("value", ["8:00", "24:00", "12:60", "noon", None])
def test_invalid_times_are_rejected(value: object) -> None:
    """Only strict 24-hour HH:MM values are accepted."""
    with pytest.raises(ValueError):
        logic.validate_time(value)


def test_equal_times_are_never_active() -> None:
    """The pure function treats an equal range as disabled for safety."""
    assert not logic.is_active("08:00", "08:00", 8 * 60)

