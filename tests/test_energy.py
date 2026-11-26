"""Energy accounting and WebSocket behavior with real Home Assistant fixtures."""

import asyncio
import json
import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pytest


def test_meter_corrections_and_resets() -> None:
    """Count independently resetting meters without counting small corrections twice."""
    pytest.importorskip("homeassistant")
    from custom_components.plant_light_scheduler.energy import record_reading

    meter = {"kwh": 0.0, "last": None, "reset": None}
    assert record_reading(meter, 10, None)
    assert record_reading(meter, 11, None)
    assert not record_reading(meter, 10.9, None)
    assert not record_reading(meter, 11, None)
    assert record_reading(meter, 0.3, None)
    assert record_reading(meter, 0.5, None)
    assert record_reading(meter, 0.2, "next cycle")
    assert meter["kwh"] == pytest.approx(1.7)


def test_energy_tracking_and_commands(tmp_path: Path) -> None:
    """Verify the energy report, reassignment, permissions, and persisted reset."""
    pytest.importorskip("homeassistant")
    from homeassistant.components.websocket_api import ActiveConnection
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry

    from custom_components.plant_light_scheduler.const import DATA_ENERGY, DOMAIN
    from custom_components.plant_light_scheduler.energy import PlantLightEnergy
    from custom_components.plant_light_scheduler.energy_websocket import (
        async_register_energy_commands,
    )
    from custom_components.plant_light_scheduler.scheduler import PlantLightScheduler

    async def exercise() -> None:
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        scheduler = PlantLightScheduler(hass)
        tracker = PlantLightEnergy(hass, scheduler)
        device_registry.async_setup(hass)
        await device_registry.async_load(hass)
        await tracker.registry.async_load()
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
        hass.data[DOMAIN] = {DATA_ENERGY: tracker}
        async_register_energy_commands(hass)
        replies: asyncio.Queue = asyncio.Queue()
        account = SimpleNamespace(
            name="Test user",
            is_admin=True,
            permissions=SimpleNamespace(
                check_entity=lambda entity_id, _: entity_id.endswith("_a")
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

        def state(kind, name, value, unit):
            attributes = {"device_class": kind, "unit_of_measurement": unit}
            if kind == "energy":
                attributes["state_class"] = "total_increasing"
            hass.states.async_set(f"sensor.{kind}_{name}", str(value), attributes)

        for name in "abc":
            device = device_registry.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={("test", name)}
            )
            tracker.registry.async_get_or_create(
                "switch",
                "test",
                f"switch_{name}",
                suggested_object_id=f"plug_{name}",
                device_id=device.id,
            )
            for kind, unit in (("power", "W"), ("energy", "kWh")):
                tracker.registry.async_get_or_create(
                    "sensor",
                    "test",
                    f"{kind}_{name}",
                    original_device_class=kind,
                    unit_of_measurement=unit,
                    suggested_object_id=f"{kind}_{name}",
                    device_id=device.id,
                )
        state("power", "a", 12, "W")
        state("power", "b", 0.008, "kW")
        state("power", "c", 0, "W")
        state("energy", "a", 5, "kWh")
        state("energy", "b", 2000, "Wh")
        state("energy", "c", 10, "kWh")
        now = 1_800_000_000
        with (
            patch("custom_components.plant_light_scheduler.energy.time", lambda: now),
            patch.object(scheduler, "async_reconcile"),
        ):
            fields = {"on": "08:00", "off": "21:00", "enabled": False}
            north = await scheduler.async_create(
                {
                    **fields,
                    "name": "North",
                    "entities": ["switch.plug_a", "switch.plug_b"],
                }
            )
            south = await scheduler.async_create(
                {**fields, "entities": ["switch.plug_c"]}
            )
            await tracker.async_start()
            report = await send("get")
            assert report["rate"] is None
            assert report["rows"][0]["watts"] == 20
            assert report["rows"][1]["watts"] == 0
            assert report["rows"][0]["warming_up"]
            assert report["totals"]["daily_kwh"] is None
            assert report["sources"]["switch.plug_b"]["energy"]["detected"] == (
                "sensor.energy_b"
            )

            now += 86_399
            state("energy", "a", 5.6, "kWh")
            state("energy", "b", 2400, "Wh")
            state("energy", "c", 10.25, "kWh")
            await hass.async_block_till_done()
            assert (await send("get"))["totals"]["daily_kwh"] is None
            now += 1
            report = await send("get")
            assert report["rows"][0]["daily_kwh"] == pytest.approx(1)
            assert report["rows"][1]["daily_kwh"] == pytest.approx(0.25)
            assert report["totals"]["monthly_kwh"] == pytest.approx(37.5)
            await send("set_rate", rate="0.225")
            await send("set_rate", rate="NaN", error_code="invalid_format")

            state("energy", "b", "unavailable", "Wh")
            state("power", "b", "unavailable", "kW")
            await hass.async_block_till_done()
            report = await send("get")
            assert report["rows"][0]["watts"] == 12
            assert report["rows"][0]["power_partial"]
            assert report["rows"][0]["energy_partial"]
            assert report["rows"][0]["daily_kwh"] == pytest.approx(1)
            state("energy", "b", 2450, "Wh")
            await hass.async_block_till_done()
            assert (await send("get"))["rows"][0]["daily_kwh"] == pytest.approx(1.05)
            account.is_admin = False
            original_check = account.permissions.check_entity
            account.permissions.check_entity = (
                lambda entity_id, _: entity_id != "sensor.energy_b"
            )
            report = await send("get")
            assert "sources" not in report
            assert report["rows"][0]["energy_partial"]
            assert report["rows"][0]["daily_kwh"] == pytest.approx(0.6)
            account.permissions.check_entity = original_check
            account.is_admin = True

            await send(
                "set_sources",
                switch="switch.plug_c",
                power="",
                energy="sensor.energy_b",
            )
            report = await send("get")
            assert report["sources"]["switch.plug_c"]["energy"]["conflict"]
            assert report["rows"][0]["energy_partial"]
            await send("set_sources", switch="switch.plug_c", power="", energy="")
            await scheduler.async_update(
                north["id"], {**fields, "entities": ["switch.plug_b"]}
            )
            state("energy", "a", 6.5, "kWh")
            await hass.async_block_till_done()
            await scheduler.async_update(
                south["id"], {**fields, "entities": ["switch.plug_a", "switch.plug_c"]}
            )
            state("energy", "a", 6.7, "kWh")
            await hass.async_block_till_done()
            report = await send("get")
            assert report["rows"][0]["daily_kwh"] == pytest.approx(1.05)
            assert report["rows"][1]["daily_kwh"] == pytest.approx(0.45)

            await tracker.async_stop()
            tracker = PlantLightEnergy(hass, scheduler)
            hass.data[DOMAIN][DATA_ENERGY] = tracker
            await tracker.async_start()
            report = await send("get")
            assert report["rate"] == 0.225
            assert report["totals"]["daily_kwh"] == pytest.approx(1.5)
            account.is_admin = False
            assert (await send("get"))["rows"] == []
            await send("reset", error_code="unauthorized")
            account.is_admin = True

            state("energy", "a", "unavailable", "kWh")
            await hass.async_block_till_done()
            await send("reset")
            state("energy", "a", 8, "kWh")
            await hass.async_block_till_done()
            report = await send("get")
            assert report["totals"]["daily_kwh"] is None
            assert report["rate"] == 0.225
            now += 86_400
            report = await send("get")
            assert report["totals"]["daily_kwh"] == 0
            assert report["totals"]["watts"] == 12
            await tracker.async_stop()

    asyncio.run(exercise())
