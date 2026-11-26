# Plant Light Scheduler

A Home Assistant custom integration and dashboard card for creating simple daily plant-light schedules. Each schedule can control one or many `switch` entities, has an on time and an off time, and can be enabled or paused from the card.

The scheduler runs in Home Assistant, not in the browser. Schedules continue to work when the dashboard is closed and are restored after a restart.

## Features

- Home Assistant-themed dashboard card with light and dark theme support
- Searchable entity picker filtered to `switch` entities
- Multiple switches per schedule
- Daily on/off times, including schedules that cross midnight
- Enable/disable toggle on each schedule
- Add, edit, delete, and delete-confirmation flows
- One schedule per switch, with an **In Use** label for switches already assigned
- Persistent storage in Home Assistant's `.storage` directory
- Admin-only schedule changes; other users can view schedules

## Install

### HACS custom repository

1. In HACS, open **Integrations**, then the three-dot menu and **Custom repositories**.
2. Add this repository URL with the category **Integration**.
3. Install **Plant Light Scheduler** and restart Home Assistant.

### Manual

Copy `custom_components/plant_light_scheduler` into your Home Assistant configuration's `custom_components` directory and restart Home Assistant.

### Set up the integration

Go to **Settings → Devices & services**. If **Plant Light Scheduler** already appears there with a service entry, continue to [Add the card](#add-the-card). Otherwise, select **Add integration**, search for **Plant Light Scheduler**, and add it once.

If you see “Only one Plant Light Scheduler instance can be configured,” close the dialog and use the existing service. You can create multiple schedules from the dashboard card.

## Add the card

Refresh Home Assistant in your browser after setting up the integration. Open the dashboard where you want the card, select **Edit dashboard → Add card**, and search for **Plant Light Scheduler**. Once the card is added, use **Add schedule** on the card to create a schedule.

You can also add it in YAML:

```yaml
type: custom:plant-light-scheduler-card
title: Plant Light Scheduler
```

If the card is not present after the first restart, add this JavaScript module under **Settings → Dashboards → Resources**, then refresh the browser:

```text
/plant_light_scheduler/plant-light-scheduler-card.js?v=0.1.3
```

Choose **JavaScript module** as the resource type. This manual resource path also guarantees that Home Assistant loads the card before rendering a dashboard.

## Scheduling behavior

- Use the clock button beside **On** or **Off** to pick an hour and minute. You can also type a 24-hour time, such as `08:00` or `0800`.
- The **on** minute is included and the **off** minute is excluded. `08:00 → 21:00` is on starting at 08:00 and off starting at 21:00.
- An overnight range such as `18:00 → 06:00` works across midnight.
- Home Assistant reconciles enabled schedules at startup and when a schedule is saved.
- At normal runtime, the integration acts at schedule boundaries. A manual switch change is therefore allowed until the next on/off boundary.
- A switch is reserved by its schedule even while paused. Remove the switch from its existing schedule or delete that schedule to use it elsewhere.
- If an older installation already has overlapping schedules for a switch, that switch stays on while any enabled schedule needs it.
- Disabling or deleting a schedule stops future control and intentionally leaves switches at their current state.
- Times use the Home Assistant server's configured local timezone.

## Development

The frontend is plain JavaScript and needs no build step.

```bash
python3 -m pytest
node --check custom_components/plant_light_scheduler/frontend/plant-light-scheduler-card.js
python3 -m compileall -q custom_components
```

## License

MIT

