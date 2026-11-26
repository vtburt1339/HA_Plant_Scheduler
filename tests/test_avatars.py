"""Check the avatar artwork and stable one-time assignments."""

import asyncio
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

SPRITE = (
    Path(__file__).parents[1]
    / "custom_components"
    / "plant_light_scheduler"
    / "frontend"
    / "plant-avatars.svg"
)


def test_avatar_sheet_has_twenty_flat_color_designs() -> None:
    """Keep the SVG symbol IDs aligned with the numeric avatar pool."""
    root = ElementTree.parse(SPRITE).getroot()
    icons = root.findall(".//{*}symbol")
    assert [icon.get("id") for icon in icons] == [f"plant-{i}" for i in range(20)]
    assert all(icon.get("viewBox") == "0 0 64 64" for icon in icons)
    assert not root.findall(".//{*}linearGradient")
    assert not root.findall(".//{*}radialGradient")


def test_avatar_assignments_survive_edits_deletion_and_reload(tmp_path: Path) -> None:
    """Backfill legacy schedules, reserve unique IDs, and fall back at exhaustion."""
    pytest.importorskip("homeassistant")
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

    from custom_components.plant_light_scheduler.const import (
        AVATAR_COUNT,
        STORAGE_KEY,
        STORAGE_VERSION,
    )
    from custom_components.plant_light_scheduler.scheduler import PlantLightScheduler

    async def exercise() -> None:
        hass = HomeAssistant(str(tmp_path))
        store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        fields = {"on": "08:00", "off": "21:00", "enabled": True}
        legacy = {"id": "old", "entities": ["switch.old"], **fields}
        await store.async_save({"schedules": [legacy]})
        scheduler = PlantLightScheduler(hass)
        await scheduler.async_load()
        original = scheduler.schedules[0]["avatar"]
        again = PlantLightScheduler(hass)
        await again.async_load()
        assert again.schedules[0] == {**legacy, "avatar": original}
        scheduler = again

        with patch.object(scheduler, "async_reconcile"):
            preferred = (original + 1) % AVATAR_COUNT
            manual = await scheduler.async_create(
                {**fields, "entities": ["switch.manual"], "avatar": preferred}
            )
            assert manual["avatar"] == preferred
            # Locking must hold even if two dashboard clients create concurrently.
            new = await asyncio.gather(
                *(
                    scheduler.async_create({**fields, "entities": [f"switch.new_{i}"]})
                    for i in range(AVATAR_COUNT - 2)
                )
            )
            assert {original, preferred, *(s["avatar"] for s in new)} == set(
                range(AVATAR_COUNT)
            )
            updated = await scheduler.async_update(
                "old", {**legacy, "name": "First plant", "enabled": False}
            )
            assert updated["avatar"] == original
            await scheduler.async_delete("old")
        scheduler = PlantLightScheduler(hass)
        await scheduler.async_load()
        with patch.object(scheduler, "async_reconcile"):
            fallback = await scheduler.async_create(
                {**fields, "entities": ["switch.fallback"]}
            )
            duplicate = await scheduler.async_create(
                {**fields, "entities": ["switch.duplicate"], "avatar": preferred}
            )
        assert "avatar" not in fallback
        assert duplicate["avatar"] == preferred
        assert (await store.async_load())["used_avatars"] == list(range(AVATAR_COUNT))

    asyncio.run(exercise())
