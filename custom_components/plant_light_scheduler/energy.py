"""Track energy from Home Assistant plug sensors independently of the dashboard."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from math import isfinite
from time import time
from typing import Any, NotRequired, TypedDict

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_state_added_domain,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import DOMAIN, SCHEDULES_CHANGED
from .scheduler import PlantLightScheduler, Schedule

SECONDS_PER_DAY = 86_400
ESTIMATE_DAYS = 30
KINDS = ("power", "energy")
CONVERTERS = {"power": (PowerConverter, "W"), "energy": (EnergyConverter, "kWh")}
TOTAL_CLASSES = {"total", "total_increasing"}


class Meter(TypedDict):
    """Meter checkpoint in kWh, with a separate baseline per assignment."""

    kwh: float
    last: float | None
    reset: str | None
    sample_at: NotRequired[float]


class History(TypedDict):
    """Energy accrued by one schedule since its first baseline."""

    started_at: float | None
    meters: dict[str, Meter]


def reading(state: State | None, kind: str) -> float | None:
    """Return a valid reading normalized to W or kWh."""
    if state is None:
        return None
    attributes = state.attributes
    converter, target = CONVERTERS[kind]
    unit = attributes.get("unit_of_measurement")
    if (
        attributes.get("device_class") != kind
        or not isinstance(unit, str)
        or unit not in converter.VALID_UNITS
    ):
        return None
    if kind == "energy" and attributes.get("state_class") not in TOTAL_CLASSES:
        return None
    try:
        value = converter.convert(float(state.state), unit, target)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if isfinite(value) and value >= 0 else None


def record_reading(meter: Meter, value: float, reset: str | None) -> bool:
    """Advance a meter, keeping the high-water mark across small corrections."""
    previous = meter["last"]
    if previous is None:
        meter.update(last=value, reset=reset)
    elif (reset is not None and reset != meter["reset"]) or value < previous * 0.9:
        meter.update(kwh=meter["kwh"] + value, last=value, reset=reset)
    elif value > previous:
        meter.update(kwh=meter["kwh"] + (value - previous), last=value, reset=reset)
    else:
        return False
    return True


class PlantLightEnergy:
    """Resolve sources, retain checkpoints, and report schedule energy."""

    def __init__(self, hass: HomeAssistant, scheduler: PlantLightScheduler) -> None:
        self.hass = hass
        self.scheduler = scheduler
        self.registry = er.async_get(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.energy", atomic_writes=True
        )
        self._save_pending = False
        self.rate: float | None = None
        self.overrides: dict[str, dict[str, str]] = {}
        self.history: dict[str, History] = {}
        self._sources: dict[str, dict[str, dict[str, str]]] = {}
        self._conflicts: dict[str, set[str]] = {kind: set() for kind in KINDS}
        self._tracked: frozenset[str] = frozenset()
        self._unsub_sensors: Callable[[], None] | None = None
        self._unsub: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        """Restore checkpoints and begin tracking even with no dashboard open."""
        stored = await self._store.async_load() or {}
        self.rate = stored.get("rate")
        self.overrides = stored.get("overrides", {})
        self.history = stored.get("history", {})
        self._unsub = [
            async_dispatcher_connect(self.hass, SCHEDULES_CHANGED, self._sync_sources),
            async_track_state_added_domain(self.hass, "sensor", self._registry_changed),
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed
            ),
        ]
        self._sync_sources()

    async def async_stop(self) -> None:
        """Release listeners and persist the latest readings on a reload."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._unsub_sensors:
            self._unsub_sensors()
            self._unsub_sensors = None
        self._tracked = frozenset()
        await self._save()

    @callback
    def _data(self) -> dict[str, Any]:
        return {"rate": self.rate, "overrides": self.overrides, "history": self.history}

    @callback
    def _pending_data(self) -> dict[str, Any]:
        self._save_pending = False
        return self._data()

    @callback
    def _schedule_save(self) -> None:
        # Batch steady sensor updates without continuously postponing the write.
        if not self._save_pending:
            self._save_pending = True
            self._store.async_delay_save(self._pending_data, 5)

    async def _save(self) -> None:
        await self._store.async_save(self._pending_data())

    def _ref(self, entity_id: str) -> str:
        entry = self.registry.async_get(entity_id)
        return entry.id if entry else entity_id

    def _entity_id(self, ref: str) -> str | None:
        entry = self.registry.async_get(ref)
        if entry:
            return entry.entity_id
        return ref if ref.startswith("sensor.") else None

    def _valid_source(self, entity_id: str, kind: str) -> bool:
        if not entity_id.startswith("sensor."):
            return False
        entry = self.registry.async_get(entity_id)
        if entry and entry.disabled_by is not None:
            return False
        state = self.hass.states.get(entity_id)
        attributes = state.attributes if state else {}
        device_class = attributes.get("device_class")
        if device_class is None and entry:
            device_class = entry.device_class or entry.original_device_class
        if device_class != kind:
            return False
        unit = attributes.get("unit_of_measurement") or (
            entry.unit_of_measurement if entry else None
        )
        converter, _ = CONVERTERS[kind]
        if unit is not None and (
            not isinstance(unit, str) or unit not in converter.VALID_UNITS
        ):
            return False
        state_class = attributes.get("state_class")
        if state and state.state not in {"unknown", "unavailable"}:
            return unit is not None and (
                kind != "energy" or state_class in TOTAL_CLASSES
            )
        # Registry metadata can identify offline candidates during startup.
        return kind != "energy" or state_class is None or state_class in TOTAL_CLASSES

    @callback
    def _registry_changed(self, _event: Event) -> None:
        self._sync_sources()

    @callback
    def _sync_sources(self) -> None:
        """Rebuild source mappings and reconcile meter ownership."""
        sources: dict[str, dict[str, dict[str, str]]] = {}
        owners: dict[str, dict[str, set[str]]] = {
            kind: defaultdict(set) for kind in KINDS
        }
        tracked: set[str] = set()
        active_ids = {schedule["id"] for schedule in self.scheduler.schedules}
        changed = False
        for schedule_id in self.history.keys() - active_ids:
            del self.history[schedule_id]
            changed = True

        for schedule in self.scheduler.schedules:
            schedule_id = schedule["id"]
            sources[schedule_id] = {}
            for switch in schedule["entities"]:
                entry = self.registry.async_get(switch)
                candidates = (
                    [
                        candidate.entity_id
                        for candidate in er.async_entries_for_device(
                            self.registry, entry.device_id
                        )
                        if candidate.entity_id.startswith("sensor.")
                    ]
                    if entry and entry.device_id
                    else []
                )
                tracked.update(candidates)
                selected = sources[schedule_id][switch] = {}
                overrides = self.overrides.get(self._ref(switch), {})
                for kind in KINDS:
                    if ref := overrides.get(kind):
                        if entity_id := self._entity_id(ref):
                            tracked.add(entity_id)
                    else:
                        matches = [
                            candidate
                            for candidate in candidates
                            if self._valid_source(candidate, kind)
                        ]
                        ref = self._ref(matches[0]) if len(matches) == 1 else ""
                    if ref:
                        selected[kind] = ref
                        owners[kind][ref].add(schedule_id)

        self._sources = sources
        self._conflicts = {
            kind: {ref for ref, schedules in owners[kind].items() if len(schedules) > 1}
            for kind in KINDS
        }
        for schedule_id, switches in sources.items():
            active = self._usable_refs(switches, "energy")
            if history := self.history.get(schedule_id):
                # Keep historical energy, but never bridge a removed assignment.
                for ref in history["meters"].keys() - active:
                    meter = history["meters"][ref]
                    if meter["last"] is not None:
                        meter.update(last=None, reset=None)
                        changed = True
            for ref in active:
                changed |= self._record(schedule_id, ref)

        if tracked != self._tracked:
            if self._unsub_sensors:
                self._unsub_sensors()
            self._tracked = frozenset(tracked)
            self._unsub_sensors = (
                async_track_state_change_event(self.hass, tracked, self._sensor_changed)
                if tracked
                else None
            )
        if changed:
            self._schedule_save()

    def _usable_refs(self, switches: dict[str, dict[str, str]], kind: str) -> set[str]:
        return {
            mapping[kind] for mapping in switches.values() if kind in mapping
        } - self._conflicts[kind]

    @callback
    def _sensor_changed(self, event: Event) -> None:
        # Availability and metadata can change which candidate is usable.
        old = event.data["old_state"]
        new = event.data["new_state"]
        if (
            old is None
            or new is None
            or any(
                old.attributes.get(key) != new.attributes.get(key)
                for key in ("device_class", "unit_of_measurement", "state_class")
            )
        ):
            self._sync_sources()
            return
        ref = self._ref(event.data["entity_id"])
        if ref in self._conflicts["energy"]:
            return
        for schedule_id, switches in self._sources.items():
            if any(mapping.get("energy") == ref for mapping in switches.values()):
                if self._record(schedule_id, ref, new):
                    self._schedule_save()

    @callback
    def _record(self, schedule_id: str, ref: str, state: State | None = None) -> bool:
        if state is None:
            if (entity_id := self._entity_id(ref)) is None:
                return False
            state = self.hass.states.get(entity_id)
        value = reading(state, "energy")
        if value is None or state is None:
            return False
        history = self.history.setdefault(
            schedule_id, {"started_at": time(), "meters": {}}
        )
        meter = history["meters"].setdefault(
            ref, {"kwh": 0.0, "last": None, "reset": None}
        )
        sample_at = state.last_updated_timestamp
        if meter["last"] is not None and sample_at < meter.get("sample_at", 0):
            return False
        meter["sample_at"] = sample_at
        reset = state.attributes.get("last_reset")
        return record_reading(meter, value, str(reset) if reset is not None else None)

    async def async_set_rate(self, rate: str) -> None:
        """Store a nonnegative flat rate, or clear a previously entered rate."""
        value = None
        if rate.strip():
            try:
                value = float(rate)
            except ValueError as err:
                raise ValueError("Enter a valid rate per kWh") from err
            if not isfinite(value) or value < 0:
                raise ValueError("Enter a finite, nonnegative rate per kWh")
        self.rate = value
        await self._save()

    async def async_set_sources(self, switch: str, power: str, energy: str) -> None:
        """Store explicit source choices for a scheduled switch."""
        if not any(
            switch in schedule["entities"] for schedule in self.scheduler.schedules
        ):
            raise ValueError("This switch is no longer on a schedule")
        selected: dict[str, str] = {}
        for kind, entity_id in (("power", power), ("energy", energy)):
            if entity_id:
                if not self._valid_source(entity_id, kind):
                    raise ValueError(f"Select a compatible {kind} sensor")
                selected[kind] = self._ref(entity_id)
        switch_ref = self._ref(switch)
        if selected:
            self.overrides[switch_ref] = selected
        else:
            self.overrides.pop(switch_ref, None)
        self._sync_sources()
        await self._save()

    async def async_reset(self) -> None:
        """Start fresh from currently available meters."""
        self.history.clear()
        self._sync_sources()
        await self._save()

    def _source_settings(
        self, switches: dict[str, dict[str, str]], kind: str, switch: str
    ) -> dict[str, str | bool | None]:
        ref = switches.get(switch, {}).get(kind)
        override = self.overrides.get(self._ref(switch), {}).get(kind)
        return {
            "detected": self._entity_id(ref) if ref else None,
            "override": self._entity_id(override) if override else None,
            "conflict": ref in self._conflicts[kind],
        }

    def _schedule_report(
        self,
        schedule: Schedule,
        can_read: Callable[[str], bool],
        now: float,
        admin: bool,
    ) -> dict[str, Any]:
        schedule_id = schedule["id"]
        switches = self._sources.get(schedule_id, {})
        powers = self._usable_refs(switches, "power")
        energies = self._usable_refs(switches, "energy")

        def available(ref: str, kind: str) -> float | None:
            entity_id = self._entity_id(ref)
            if not entity_id or not can_read(entity_id):
                return None
            return reading(self.hass.states.get(entity_id), kind)

        power_values = [
            value for ref in powers if (value := available(ref, "power")) is not None
        ]
        has_all_power = len(power_values) == len(powers) and all(
            switches.get(switch, {}).get("power") in powers
            for switch in schedule["entities"]
        )
        has_all_energy = all(
            switches.get(switch, {}).get("energy") in energies
            for switch in schedule["entities"]
        ) and all(available(ref, "energy") is not None for ref in energies)
        daily = None
        warming_up = False
        if (history := self.history.get(schedule_id)) and history[
            "started_at"
        ] is not None:
            days = (now - history["started_at"]) / SECONDS_PER_DAY
            counted = [
                meter["kwh"]
                for ref, meter in history["meters"].items()
                if admin
                or ((entity_id := self._entity_id(ref)) and can_read(entity_id))
            ]
            has_all_energy &= len(counted) == len(history["meters"])
            if counted:
                warming_up = days < 1
                if not warming_up:
                    daily = sum(counted) / days
        return {
            "id": schedule_id,
            "name": schedule.get("name"),
            "switches": schedule["entities"],
            "watts": sum(power_values) if power_values else None,
            "daily_kwh": daily,
            "monthly_kwh": daily * ESTIMATE_DAYS if daily is not None else None,
            "power_partial": not has_all_power,
            "energy_partial": not has_all_energy,
            "warming_up": warming_up,
        }

    @callback
    def report(self, can_read: Callable[[str], bool], admin: bool) -> dict[str, Any]:
        """Report only data from entities this user may read."""
        now = time()
        rows = [
            self._schedule_report(schedule, can_read, now, admin)
            for schedule in self.scheduler.schedules
            if all(can_read(entity_id) for entity_id in schedule["entities"])
        ]
        totals: dict[str, float | bool | None] = {}
        for metric in ("watts", "daily_kwh", "monthly_kwh"):
            values = [row[metric] for row in rows if row[metric] is not None]
            totals[metric] = sum(values) if values else None
        totals["power_partial"] = any(row["power_partial"] for row in rows)
        totals["energy_partial"] = any(
            row["energy_partial"] or row["daily_kwh"] is None for row in rows
        )
        totals["warming_up"] = bool(rows) and all(row["warming_up"] for row in rows)
        report = {
            "rows": rows,
            "totals": totals,
            "rate": self.rate,
            "currency": self.hass.config.currency,
        }
        if admin:
            report["sources"] = {
                switch: {
                    kind: self._source_settings(
                        self._sources.get(row["id"], {}), kind, switch
                    )
                    for kind in KINDS
                }
                for row in rows
                for switch in row["switches"]
            }
        return report
