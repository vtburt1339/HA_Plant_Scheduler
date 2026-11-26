"""Persistent plant-light schedule manager."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import datetime
from random import choice
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STARTED,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import AVATAR_COUNT, STORAGE_KEY, STORAGE_VERSION
from .logic import desired_state, entities_at_boundary

_LOGGER = logging.getLogger(__name__)


class Schedule(TypedDict):
    """A stored plant-light schedule."""

    id: str
    entities: list[str]
    on: str
    off: str
    enabled: bool
    name: NotRequired[str]
    avatar: NotRequired[int]


class ScheduleStorage(TypedDict):
    """Schedule data and the avatars already issued on this installation."""

    schedules: list[Schedule]
    used_avatars: NotRequired[list[int]]


class PlantLightScheduler:
    """Store schedules and apply their switch state transitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self.schedules: list[Schedule] = []
        self._used_avatars: set[int] = set()
        self._store: Store[ScheduleStorage] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self._lock = asyncio.Lock()
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None

    async def async_load(self) -> None:
        """Load schedules from Home Assistant storage."""
        stored = await self._store.async_load() or {"schedules": []}
        self.schedules = list(stored.get("schedules", []))
        used = set(stored.get("used_avatars", []))
        self._used_avatars = used | {
            schedule["avatar"] for schedule in self.schedules if "avatar" in schedule
        }
        for schedule in self.schedules:
            if "avatar" not in schedule:
                self._assign_avatar(schedule)
        if self._used_avatars != used:
            await self._async_save()

    async def async_start(self) -> None:
        """Start watching local minute boundaries."""
        self._unsub_tick = async_track_time_change(
            self.hass, self._async_minute_tick, second=0
        )
        if self.hass.state is CoreState.running:
            await self.async_reconcile()
            return

        self._unsub_started = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, self._async_home_assistant_started
        )

    @callback
    def async_stop(self) -> None:
        """Stop listeners owned by the scheduler."""
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None
        if self._unsub_started:
            self._unsub_started()
            self._unsub_started = None

    async def async_create(self, data: dict[str, Any]) -> Schedule:
        """Create and persist a schedule."""
        schedule: Schedule = {
            "id": uuid4().hex,
            "entities": list(data["entities"]),
            "on": data["on"],
            "off": data["off"],
            "enabled": data.get("enabled", True),
        }
        if name := data.get("name"):
            schedule["name"] = name
        async with self._lock:
            self._check_entity_assignments(set(schedule["entities"]))
            self._assign_avatar(schedule)
            self.schedules.append(schedule)
            await self._async_save()
        await self.async_reconcile(schedule["entities"])
        return schedule

    async def async_update(self, schedule_id: str, data: dict[str, Any]) -> Schedule:
        """Update and persist a schedule."""
        async with self._lock:
            index = self._find_index(schedule_id)
            previous = self.schedules[index]
            previously_managed = set(previous["entities"])
            schedule: Schedule = {
                "id": schedule_id,
                "entities": list(data["entities"]),
                "on": data["on"],
                "off": data["off"],
                "enabled": data.get("enabled", True),
            }
            if name := data.get("name", previous.get("name")):
                schedule["name"] = name
            if "avatar" in previous:
                schedule["avatar"] = previous["avatar"]
            self._check_entity_assignments(
                set(schedule["entities"]) - previously_managed
            )
            self.schedules[index] = schedule
            await self._async_save()
        await self.async_reconcile(previously_managed | set(schedule["entities"]))
        return schedule

    async def async_delete(self, schedule_id: str) -> None:
        """Delete a schedule without changing now-unmanaged switch states."""
        async with self._lock:
            index = self._find_index(schedule_id)
            previously_managed = set(self.schedules[index]["entities"])
            del self.schedules[index]
            await self._async_save()
        # If another schedule still manages one of these entities, apply that
        # remaining schedule. Fully unmanaged entities are left unchanged.
        await self.async_reconcile(previously_managed)

    def _check_entity_assignments(self, new_entities: set[str]) -> None:
        """Reject newly assigned switches already used by another schedule."""
        conflicts = sorted(
            new_entities.intersection(
                entity_id
                for schedule in self.schedules
                for entity_id in schedule["entities"]
            )
        )
        if conflicts:
            raise ValueError(
                f"Already in use by another schedule: {', '.join(conflicts)}"
            )

    def _find_index(self, schedule_id: str) -> int:
        """Find a schedule index or raise KeyError."""
        for index, schedule in enumerate(self.schedules):
            if schedule["id"] == schedule_id:
                return index
        raise KeyError(schedule_id)

    def _assign_avatar(self, schedule: Schedule) -> None:
        """Draw an unused avatar and permanently reserve it."""
        available = [
            avatar for avatar in range(AVATAR_COUNT) if avatar not in self._used_avatars
        ]
        if available:
            avatar = choice(available)
            schedule["avatar"] = avatar
            self._used_avatars.add(avatar)

    async def _async_save(self) -> None:
        """Write the current schedules to storage."""
        await self._store.async_save(
            {
                "schedules": self.schedules,
                "used_avatars": sorted(self._used_avatars),
            }
        )

    async def _async_home_assistant_started(self, _event: Event) -> None:
        """Reconcile states once all integrations have started."""
        self._unsub_started = None
        await self.async_reconcile()

    async def _async_minute_tick(self, now: datetime) -> None:
        """Apply only schedules that reach a boundary in this minute."""
        minute_of_day = now.hour * 60 + now.minute
        affected = entities_at_boundary(self.schedules, minute_of_day)
        if affected:
            await self.async_reconcile(affected, now=now)

    async def async_reconcile(
        self, entities: Iterable[str] | None = None, *, now: datetime | None = None
    ) -> None:
        """Set managed switches to the state required by current schedules."""
        if now is None:
            now = dt_util.now()
        minute_of_day = now.hour * 60 + now.minute

        managed_entities = {
            entity_id
            for schedule in self.schedules
            if schedule.get("enabled", True)
            for entity_id in schedule["entities"]
        }
        target_entities = managed_entities if entities is None else set(entities)
        # Removed, disabled, or deleted entities are intentionally left alone.
        target_entities &= managed_entities

        turn_on: list[str] = []
        turn_off: list[str] = []
        for entity_id in sorted(target_entities):
            wants_on = desired_state(self.schedules, entity_id, minute_of_day)
            state = self.hass.states.get(entity_id)
            if wants_on and (state is None or state.state != STATE_ON):
                turn_on.append(entity_id)
            elif not wants_on and (state is None or state.state != STATE_OFF):
                turn_off.append(entity_id)

        if turn_on:
            _LOGGER.debug("Turning on scheduled switches: %s", turn_on)
            await self.hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: turn_on},
                blocking=False,
            )
        if turn_off:
            _LOGGER.debug("Turning off scheduled switches: %s", turn_off)
            await self.hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: turn_off},
                blocking=False,
            )
