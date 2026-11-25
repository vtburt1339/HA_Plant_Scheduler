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
- Safe overlap handling: a shared switch stays on while any active schedule needs it
- Persistent storage in Home Assistant's `.storage` directory
- Admin-only schedule changes; other users can view schedules

## Install

### HACS custom repository

1. In HACS, open **Integrations**, then the three-dot menu and **Custom repositories**.
2. Add this repository URL with the category **Integration**.
3. Install **Plant Light Scheduler** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **Plant Light Scheduler**, and add it.

### Manual

Copy `custom_components/plant_light_scheduler` into your Home Assistant configuration's `custom_components` directory, restart Home Assistant, and add the integration from **Settings → Devices & services**.

## Add the card

The integration registers the card automatically. Edit a dashboard, choose **Add card**, and search for **Plant Light Scheduler**.

You can also add it in YAML:

```yaml
type: custom:plant-light-scheduler-card
title: Plant Light Scheduler
```

If the card is not present after the first restart, add this JavaScript module under **Settings → Dashboards → Resources**, then refresh the browser:

```text
/plant_light_scheduler/plant-light-scheduler-card.js?v=0.1.0
```

Choose **JavaScript module** as the resource type. This manual resource path also guarantees that Home Assistant loads the card before rendering a dashboard.

## Scheduling behavior

- The **on** minute is included and the **off** minute is excluded. `08:00 → 21:00` is on starting at 08:00 and off starting at 21:00.
- An overnight range such as `18:00 → 06:00` works across midnight.
- Home Assistant reconciles enabled schedules at startup and when a schedule is saved.
- At normal runtime, the integration acts at schedule boundaries. A manual switch change is therefore allowed until the next on/off boundary.
- When schedules overlap for one switch, the switch turns off only when no enabled schedule is currently active.
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

