"""Read live power and estimate costs for the current schedules."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from math import isfinite
from typing import Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import PowerConverter

from .const import DOMAIN
from .logic import time_to_minutes
from .scheduler import PlantLightScheduler, Schedule

MINUTES_PER_DAY = 1_440
MONTH_DAYS = 30


def power_in_watts(state: State | None) -> float | None:
    """Convert a valid power reading to watts."""
    if state is None or state.attributes.get("device_class") != "power":
        return None
    unit = state.attributes.get("unit_of_measurement")
    if not isinstance(unit, str) or unit not in PowerConverter.VALID_UNITS:
        return None
    try:
        watts = PowerConverter.convert(float(state.state), unit, "W")
    except (ValueError, OverflowError):
        return None
    return watts if isfinite(watts) and watts >= 0 else None


def projected_cost(
    schedule: Schedule, watts: float | None, rate: float | None
) -> tuple[float | None, float | None]:
    """Estimate the cost of this load repeating the same schedule daily."""
    if rate is None:
        return None, None
    if not schedule.get("enabled", True):
        return 0, 0
    if watts is None:
        return None, None
    minutes = (
        time_to_minutes(schedule["off"]) - time_to_minutes(schedule["on"])
    ) % MINUTES_PER_DAY
    daily = watts / 1000 * (minutes / 60) * rate
    monthly = daily * MONTH_DAYS
    return (daily, monthly) if isfinite(monthly) else (None, None)


class PlantLightPower:
    """Store settings and resolve live sources on demand."""

    def __init__(self, hass: HomeAssistant, scheduler: PlantLightScheduler) -> None:
        self.hass = hass
        self.scheduler = scheduler
        self.registry = er.async_get(hass)
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.power", atomic_writes=True
        )
        self.rate: float | None = None
        self.overrides: dict[str, str] = {}

    async def async_load(self) -> None:
        """Load settings, bringing forward rate and live sources on upgrade."""
        stored = await self._store.async_load()
        if stored is not None:
            self.rate = stored.get("rate")
            self.overrides = stored.get("overrides", {})
            return

        legacy = await Store(
            self.hass, 1, f"{DOMAIN}.energy", read_only=True
        ).async_load()
        if legacy is not None:
            self.rate = legacy.get("rate")
            self.overrides = {
                switch: sources["power"]
                for switch, sources in legacy.get("overrides", {}).items()
                if sources.get("power")
            }
            await self._save()

    async def _save(self) -> None:
        await self._store.async_save(
            {"rate": self.rate, "overrides": dict(self.overrides)}
        )

    async def async_set_rate(self, rate: str) -> None:
        """Store a finite nonnegative flat rate, or clear it."""
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

    def _ref(self, entity_id: str) -> str:
        entry = self.registry.async_get(entity_id)
        return entry.id if entry else entity_id

    def _entity_id(self, ref: str) -> str | None:
        entry = self.registry.async_get(ref)
        if entry:
            return entry.entity_id
        return ref if ref.startswith("sensor.") else None

    def _valid_source(self, entity_id: str) -> bool:
        if not entity_id.startswith("sensor."):
            return False
        entry = self.registry.async_get(entity_id)
        if entry and entry.disabled_by is not None:
            return False
        state = self.hass.states.get(entity_id)
        attrs = state.attributes if state else {}
        device_class = attrs.get("device_class")
        if device_class is None and entry:
            device_class = entry.device_class or entry.original_device_class
        unit = attrs.get("unit_of_measurement") or (
            entry.unit_of_measurement if entry else None
        )
        return device_class == "power" and (
            unit is None
            or (isinstance(unit, str) and unit in PowerConverter.VALID_UNITS)
        )

    def _source(self, switch: str) -> str | None:
        if override := self.overrides.get(self._ref(switch)):
            return self._entity_id(override)
        entry = self.registry.async_get(switch)
        if not entry or not entry.device_id:
            return None
        candidates = [
            candidate.entity_id
            for candidate in er.async_entries_for_device(self.registry, entry.device_id)
            if self._valid_source(candidate.entity_id)
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def async_set_source(self, switch: str, power: str) -> None:
        """Choose a live meter for the switch, or return it to automatic matching."""
        if not any(switch in item["entities"] for item in self.scheduler.schedules):
            raise ValueError("This switch is no longer on a schedule")
        switch_ref = self._ref(switch)
        if power:
            if not self._valid_source(power):
                raise ValueError("Select a compatible power sensor")
            self.overrides[switch_ref] = self._ref(power)
        else:
            self.overrides.pop(switch_ref, None)
        await self._save()

    @callback
    def report(self, can_read: Callable[[str], bool], admin: bool) -> dict[str, Any]:
        """Report current watts, costs, and settings without collecting samples."""
        schedules = self.scheduler.schedules
        switches = {switch for item in schedules for switch in item["entities"]}
        sources = {switch: self._source(switch) for switch in switches}
        owners: dict[str, set[str]] = defaultdict(set)
        for schedule in schedules:
            for switch in schedule["entities"]:
                if meter := sources[switch]:
                    owners[meter].add(schedule["id"])
        conflicts = {meter for meter, ids in owners.items() if len(ids) > 1}
        rows = []
        for schedule in schedules:
            if not all(can_read(switch) for switch in schedule["entities"]):
                continue
            meter_ids = {sources[switch] for switch in schedule["entities"]}
            known = {
                meter: value
                for meter in meter_ids
                if meter and meter not in conflicts and can_read(meter)
                if (value := power_in_watts(self.hass.states.get(meter))) is not None
            }
            watts = sum(known.values()) if known else None
            daily, monthly = projected_cost(schedule, watts, self.rate)
            partial = len(known) < len(meter_ids)
            rows.append(
                {
                    "id": schedule["id"],
                    "watts": watts,
                    "day_cost": daily,
                    "month_cost": monthly,
                    "power_partial": partial,
                    "cost_partial": self.rate is not None
                    and schedule.get("enabled", True)
                    and (partial or daily is None),
                }
            )
        totals: dict[str, float | bool | None] = {}
        for key in ("watts", "day_cost", "month_cost"):
            values = [row[key] for row in rows if row[key] is not None]
            totals[key] = sum(values) if values else None
        for key in ("power_partial", "cost_partial"):
            totals[key] = any(row[key] for row in rows)
        report: dict[str, Any] = {
            "rows": rows,
            "totals": totals,
            "rate": self.rate,
            "currency": self.hass.config.currency,
        }
        if admin:
            report["sources"] = {
                switch: {
                    "detected": meter,
                    "override": self._entity_id(override)
                    if (override := self.overrides.get(self._ref(switch)))
                    else None,
                    "conflict": meter in conflicts,
                }
                for switch, meter in sources.items()
            }
        return report
