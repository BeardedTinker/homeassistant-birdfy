# Birdfy for Home Assistant

An unofficial Home Assistant integration for Birdfy feeder cameras. It supports
authenticated Moments, legacy Highlight/Recap share links, new-arrival events,
compact entities, and a bundled Lovelace card.

## Features

- Discovers recognized Birdfy feeders from a Birdfy account.
- Polls each feeder's Moments feed every five minutes.
- Fires `birdfy_new_moment` once for each newly detected arrival.
- Exposes compact daily counters, the latest species, and the latest image.
- Bundles an English/Croatian Moments card with image and video playback.
- Retains Highlight and monthly Recap support through optional share UUIDs.

The full feed and temporary signed media URLs are not stored in sensor
attributes. The card requests at most 20 Moments when it is opened and caches
the response in memory for 60 seconds.

## Installation

### HACS

1. In HACS, open **Integrations**.
2. Add `https://github.com/BeardedTinker/homeassistant-birdfy` as a custom
   **Integration** repository.
3. Install **Birdfy**.
4. Restart Home Assistant.
5. Add the integration under **Settings > Devices & services**.

### Manual

Copy `custom_components/birdfy` into Home Assistant's `custom_components`
directory, restart Home Assistant, and add the integration from the UI.

## Configuration

At least one of these configuration methods is required:

- **Birdfy account email and password** enable authenticated Moments, feeder
  discovery, new-arrival events, compact Moments entities, and the card.
- **Highlight UUID** enables the legacy shared Highlights sensors.
- **Recap UUID** enables the legacy monthly Recap sensors.

The account password is converted to the digest required by Birdfy before it is
stored. Renewable authentication tokens are refreshed and persisted by the
integration.

### Finding share UUIDs

Open **Highlights** or **Recap** in the Birdfy app and copy the value after
`uuid=` from the generated share URL:

```text
https://highlight.birdfy.com/?uuid=YOUR_UUID
https://recap.birdfy.com/?uuid=YOUR_UUID
```

Account login and share UUIDs can be used together.

## Moments entities

Each authenticated feeder creates:

- **Moments Today**
- **Bird Visits Today**
- **Moment Species Today**
- **Last Moment Species**
- **Last Moment** image

Entity attributes contain only bounded metadata. The latest image is served
through Home Assistant's image proxy.

## Bundled dashboard card

The integration registers `custom:birdfy-moments-card` automatically when
Lovelace uses storage-managed resources. Add it with the visual card editor and
select a Birdfy feeder.

Example YAML:

```yaml
type: custom:birdfy-moments-card
device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
title: Garden Birdfy
limit: 20
moments_entity: sensor.garden_birdfy_moments_today
visits_entity: sensor.garden_birdfy_bird_visits_today
species_count_entity: sensor.garden_birdfy_moment_species_today
latest_species_entity: sensor.garden_birdfy_last_moment_species
latest_image_entity: image.garden_birdfy_last_moment
```

For YAML-managed Lovelace resources, add this module manually:

```yaml
url: /birdfy/birdfy-moments-card.js?v=1.2.3
type: module
```

The optional summary entities let the closed card show current counts, species,
and the Home Assistant image-proxy URL. The card requests the full feed and
temporary Birdfy media URLs only when the modal is opened. It does not use
entity-state feed attributes, `localStorage`, or `sessionStorage`.

## New Moment event

The integration fires `birdfy_new_moment` with:

- `serial_number`
- `device_name`
- `alarm_id`
- `species`
- `ai_labels`
- `detection_time`
- `image_url`
- `video_url`

Example notification automation:

```yaml
alias: Birdfy - Notify on new Moment
triggers:
  - trigger: event
    event_type: birdfy_new_moment
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "Birdfy: {{ trigger.event.data.species }}"
      message: >
        New Moment at {{ trigger.event.data.device_name }}
        ({{ trigger.event.data.detection_time }}).
      data:
        image: "{{ trigger.event.data.image_url }}"
mode: queued
max: 10
```

Media URLs in the event are temporary Birdfy URLs intended for immediate
automation use.

## Update intervals

- Authenticated Moments: 5 minutes
- Shared Highlights: 15 minutes
- Monthly Recap: 60 minutes

## Support

Report issues at
<https://github.com/BeardedTinker/homeassistant-birdfy/issues>.

This project is unofficial and is not affiliated with Birdfy or Netvue.

## License

MIT
