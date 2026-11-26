"""Tests for live power readings, projections, and settings migration."""

import asyncio
import json
import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize(
    ("on", "off", "watts", "enabled", "rate", "expected"),
    [
        ("07:00", "22:00", 85, True, 0.1082, 0.137955),
        ("21:30", "06:15", 100, True, 0.2, 0.175),
        ("07:00", "22:00", 0, True, 0.2, 0),
        ("07:00", "22:00", 85, False, 0.2, 0),
        ("07:00", "22:00", None, False, 0.2, 0),
        ("07:00", "22:00", None, True, 0.2, None),
        ("07:00", "22:00", 85, True, None, None),
    ],
)
def test_projected_cost(on, off, watts, enabled, rate, expected) -> None:
    """Project current watts across the schedule, including overnight windows."""
    pytest.importorskip("homeassistant")
    from custom_components.plant_light_scheduler.power import projected_cost

    schedule = {"id": "test", "entities": [], "on": on, "off": off, "enabled": enabled}
    day, month = projected_cost(schedule, watts, rate)
    if expected is None:
        assert (day, month) == (None, None)
    else:
        assert day == pytest.approx(expected)
        assert month == pytest.approx(expected * 30)


def test_power_report_and_settings_upgrade(tmp_path: Path) -> None:
    """Exercise real Home Assistant states, persistence, and WebSocket validation."""
    pytest.importorskip("homeassistant")
    from homeassistant.components.websocket_api import ActiveConnection
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry
    from homeassistant.helpers.storage import Store

    from custom_components.plant_light_scheduler.const import DATA_POWER, DOMAIN
    from custom_components.plant_light_scheduler.energy_websocket import (
        async_register_energy_commands,
    )
    from custom_components.plant_light_scheduler.power import PlantLightPower
    from custom_components.plant_light_scheduler.scheduler import PlantLightScheduler

    async def exercise() -> None:
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        scheduler = PlantLightScheduler(hass)
        power = PlantLightPower(hass, scheduler)
        device_registry.async_setup(hass)
        await device_registry.async_load(hass)
        await power.registry.async_load()
        entry = ConfigEntry(
            domain="test",
            title="Test plugs",
            data={},
            version=1,
            minor_version=1,
            options={},
            source="user",
            unique_id=None,
            subentries_data=None,
            discovery_keys=MappingProxyType({}),
        )
        with patch.object(hass.config_entries, "async_setup"):
            await hass.config_entries.async_add(entry)
        hass.data[DOMAIN] = {DATA_POWER: power}
        async_register_energy_commands(hass)
        replies: asyncio.Queue = asyncio.Queue()
        account = SimpleNamespace(
            name="Test user",
            is_admin=True,
            permissions=SimpleNamespace(
                check_entity=lambda entity_id, _: entity_id
                not in {"switch.plug_c", "sensor.power_b"}
            ),
        )
        connection = ActiveConnection(
            logging.getLogger(__name__), hass, replies.put_nowait, account, None, None
        )

        async def send(command, *, error_code=None, **fields):
            message_id = connection.last_id + 1
            connection.async_handle(
                {"id": message_id, "type": f"{DOMAIN}/energy/{command}", **fields}
            )
            result = await asyncio.wait_for(replies.get(), timeout=5)
            reply = result if isinstance(result, dict) else json.loads(result)
            assert reply["id"] == message_id
            if error_code:
                assert not reply["success"]
                assert reply["error"]["code"] == error_code
                return None
            assert reply["success"], reply.get("error")
            return reply["result"]

        def set_watts(suffix, value, unit="W"):
            hass.states.async_set(
                f"sensor.power_{suffix}",
                str(value),
                {"device_class": "power", "unit_of_measurement": unit},
            )

        for suffix in "abc":
            device = device_registry.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("test", suffix)}
            )
            power.registry.async_get_or_create(
                "switch",
                "test",
                f"switch_{suffix}",
                suggested_object_id=f"plug_{suffix}",
                device_id=device.id,
            )
            power.registry.async_get_or_create(
                "sensor",
                "test",
                f"power_{suffix}",
                original_device_class="power",
                unit_of_measurement="W",
                suggested_object_id=f"power_{suffix}",
                device_id=device.id,
            )
        set_watts("a", 12)
        set_watts("b", 0.008, "kW")
        set_watts("c", 5)
        device_a = power.registry.async_get("switch.plug_a")
        legacy_data = {
            "rate": 0.2,
            "overrides": {
                device_a.id: {"power": "sensor.power_a", "energy": "sensor.kwh"}
            },
            "history": {"old-schedule": {"started_at": 100, "meters": {}}},
        }
        legacy_store = Store(hass, 1, f"{DOMAIN}.energy")
        await legacy_store.async_save(legacy_data)
        await power.async_load()

        with patch.object(scheduler, "async_reconcile"):
            north = await scheduler.async_create(
                {
                    "entities": ["switch.plug_a", "switch.plug_b"],
                    "on": "06:00",
                    "off": "18:00",
                }
            )
            await scheduler.async_create(
                {
                    "entities": ["switch.plug_c"],
                    "on": "09:00",
                    "off": "18:00",
                    "enabled": False,
                }
            )
            report = await send("get")
            assert report["rate"] == 0.2
            assert report["sources"]["switch.plug_a"]["override"] == "sensor.power_a"
            assert report["sources"]["switch.plug_b"]["detected"] == "sensor.power_b"
            assert report["rows"][0]["watts"] == 20
            assert report["rows"][0]["day_cost"] == pytest.approx(0.048)
            assert report["rows"][1]["watts"] == 5
            assert report["rows"][1]["day_cost"] == 0
            assert report["totals"]["watts"] == 25
            assert report["totals"]["month_cost"] == pytest.approx(1.44)
            assert not report["totals"]["power_partial"]
            set_watts("b", "unavailable", "kW")
            report = await send("get")
            assert report["rows"][0]["watts"] == 12
            assert report["rows"][0]["cost_partial"]
            set_watts("b", 0)
            assert not (await send("get"))["rows"][0]["power_partial"]

            # One power meter on a multi-switch row is counted once.
            await send("set_sources", switch="switch.plug_b", power="sensor.power_a")
            report = await send("get")
            assert report["rows"][0]["watts"] == 12
            assert not report["rows"][0]["power_partial"]
            await send("set_sources", switch="switch.plug_c", power="sensor.power_a")
            report = await send("get")
            assert report["sources"]["switch.plug_c"]["conflict"]
            assert report["totals"]["watts"] is None
            assert report["totals"]["cost_partial"]
            await send("set_sources", switch="switch.plug_b", power="")
            await send("set_sources", switch="switch.plug_c", power="")
            await send("set_rate", rate="0.25")
            await send("set_rate", rate="NaN", error_code="invalid_format")

            # Reports reflect a schedule edit immediately and leave the old store alone.
            await scheduler.async_update(north["id"], {**north, "off": "16:00"})
            assert (await send("get"))["rows"][0]["day_cost"] == pytest.approx(0.03)
            restarted = PlantLightPower(hass, scheduler)
            await restarted.async_load()
            assert restarted.rate == 0.25
            assert restarted.overrides == {device_a.id: "sensor.power_a"}
            assert await legacy_store.async_load() == legacy_data

            account.is_admin = False
            report = await send("get")
            assert "sources" not in report
            assert len(report["rows"]) == 1
            assert report["totals"]["watts"] == 12
            assert report["totals"]["power_partial"]
            await send("set_rate", rate="0", error_code="unauthorized")
            await send("reset", error_code="unknown_command")

    asyncio.run(exercise())
