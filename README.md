# Posti package tracking for Home Assistant

Home Assistant integration that follows the packages of an OmaPosti account.

[![GitHub Release][releases-shield]][releases] [![GitHub Release Date][release-date-shield]][releases]

[![HACS][hacs-shield]][hacs] [![Home Assistant][home-assistant-shield]][home-assistant] [![License][license-shield]](LICENSE)

![Project Maintenance][maintenance-shield] [![GitHub Activity][commits-shield]][commits] [![Open bugs][bugs-shield]][bugs] [![Open enhancements][enhancements-shield]][enhancements]

## Support

Hey dude! Help me out for a couple of :beers: or a :coffee:!

[![coffee](https://www.buymeacoffee.com/assets/img/custom_images/black_img.png)](https://www.buymeacoffee.com/jesmak)

## What is it?

A custom component that lists the coming and recently delivered packages of an [OmaPosti](https://oma.posti.fi/)
account. There's no need to add packages by hand: the list comes from the packages in your account, and it's updated
every 10 minutes.

The sensor lists the packages in the format [package-tracker-card](https://github.com/jesmak/package-tracker-card)
reads, so the card can show them together with packages from other tracking integrations.

## Installation

### With HACS

1. Add this repository to HACS custom repositories with type **Integration**
2. Search for Posti package tracking in HACS and download it
3. Restart Home Assistant
4. Add the integration in Settings › Devices & services, with your OmaPosti user name and password

### Manual

1. Download the source code from the latest release
2. Copy the `custom_components/posti_tracking` folder to your Home Assistant installation's
   `config/custom_components` folder
3. Restart Home Assistant
4. Add the integration in Settings › Devices & services, with your OmaPosti user name and password

## Logging in

OmaPosti has no public API. The integration logs in the way the OmaPosti Android app does, with your user name and
password, and saves the tokens it gets. The tokens are renewed shortly before they expire; when that doesn't work, it
logs in again with the saved password. If Posti no longer accepts the password, Home Assistant asks for it on the
integration page.

Because it imitates the app, a change in Posti's login can stop the integration until it is updated.

## Settings

Each account is added separately. To change its settings or password later, choose **Reconfigure** from the account's
menu on the integration page; an empty password field keeps the saved password.

| Name                                       | Type    | Description                                                                      | Default                   |
| ------------------------------------------ | ------- | -------------------------------------------------------------------------------- | ------------------------- |
| User name                                  | string  | The email address you log in to OmaPosti with. The sensor is named after it      |                           |
| Password                                   | string  | Your OmaPosti password, saved in Home Assistant                                  |                           |
| Language                                   | enum    | Language of the package event descriptions: `fi` or `en`                         | Home Assistant's language |
| Undelivered packages first                 | boolean | When there are more packages than the maximum, undelivered ones are listed first | on                        |
| Maximum number of packages                 | number  | How many packages the sensor lists                                               | 5                         |
| Days until undelivered packages are hidden | days    | Counted from the latest event. Some packages stay in delivery for good           | 15                        |
| Days until delivered packages are hidden   | days    | Counted from the delivery. Returned packages count as delivered                  | 3                         |
| Pickup point and code                      | boolean | Adds the pickup point and its code to the packages. The code opens the locker    | off                       |

## Sensor

The state is the time a package of the account last changed, such as when it was received, moved or delivered. The
sensor is named after the account, for example `sensor.posti_matti_meikalainen_example_com`.

The `packages` attribute lists the packages, and each package has:

| Key                                         | Description                                                                    |
| ------------------------------------------- | ------------------------------------------------------------------------------ |
| `shipment_number`                           | The tracking number, or Posti's shipment number without one                    |
| `status`                                    | The package's status, below                                                    |
| `raw_status`                                | Posti's shipment phase, such as `READY_FOR_PICKUP`                             |
| `origin`, `origin_city`                     | The sender and its city                                                        |
| `destination`, `destination_city`           | The pickup point, or the receiver without one, and the city                    |
| `shipment_date`                             | When Posti received the shipment's details                                     |
| `latest_event`                              | The latest event, in the chosen language when Posti has it                     |
| `latest_event_city`, `latest_event_country` | Where the latest event happened                                                |
| `latest_event_date`                         | When the package last changed                                                  |
| `estimated_delivery`                        | When the package is expected, when the service says                            |
| `pickup_deadline`                           | How long it is kept at the pickup point, which Posti never tells: always empty |
| `weight`                                    | The package's weight in kilograms                                              |
| `package_count`                             | How many parcels the shipment has                                              |
| `pickup_point`                              | The pickup point, with **Pickup point and code** on                            |
| `pickup_code`                               | The code that opens the locker, with the same setting on                       |
| `source`                                    | Always `Posti`                                                                 |
| `tracking_url`                              | The package's page on posti.fi                                                 |

| `status` | Meaning                | Posti's phase        |
| -------- | ---------------------- | -------------------- |
| `1`      | Waiting                | `WAITING`            |
| `2`      | Received by Posti      | `RECEIVED`           |
| `3`      | In transport           | `IN_TRANSPORT`       |
| `4`      | In delivery            | `IN_DELIVERY`        |
| `5`      | Ready for pickup       | `READY_FOR_PICKUP`   |
| `0`      | Delivered              | `DELIVERED`          |
| `6`      | Returned to the sender | `RETURNED_TO_SENDER` |
| `7`      | Unknown                | any other phase      |

Posti leaves `pickup_deadline` empty; it is in the list so that packages look the same as other integrations' do.

The pickup point and its code are left out unless the account's **Pickup point and code** setting is turned on,
because the code alone opens the locker and anyone who can see your dashboard can read it. Change the setting with
**Reconfigure**.

Times are ISO 8601 in UTC. Packages without events, which Posti has only been told about, aren't listed. The packages
aren't stored in the recorder, only the state. The sensor is unavailable while Posti can't be reached.

## Counts

Two sensors count the packages, so a badge or an automation needs no templating:

| Sensor                    | What it counts                              |
| ------------------------- | ------------------------------------------- |
| Packages on the way       | Everything that hasn't finished its journey |
| Packages ready for pickup | The ones waiting at a pickup point          |

## Events

An event entity, **Package**, fires once for everything that happens to a package, so an automation can act on it
without watching the packages attribute. Several packages changing in one update fire one event each.

| Event type         | When it fires                                 |
| ------------------ | --------------------------------------------- |
| `new_package`      | A package the account hadn't seen before      |
| `moved`            | The package moved along, or a new event of it |
| `ready_for_pickup` | It is waiting to be picked up                 |
| `delivered`        | It has been delivered                         |
| `returned`         | It was returned to the sender                 |

The event carries the package it happened to: `shipment_number`, `status`, `raw_status`, `origin`, `destination`,
`destination_city`, `latest_event`, `latest_event_city`, `latest_event_date` and `source`.

Nothing fires for the packages that are already there when Home Assistant starts; they have not just happened.

```yaml
automation:
  - triggers:
      - trigger: state
        entity_id: event.posti_matti_meikalainen_example_com_package
        attribute: event_type
        to: ready_for_pickup
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.to_state.attributes.shipment_number }} is ready for pickup"
```

## Upgrading from 1.x

Nothing needs to be done: the account, its saved login, the sensor and its attributes carry over.

## Data

Package data: Posti Group Oyj, from the same service the OmaPosti app uses.

## Development

Requires Python 3.14.

```
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
.venv/bin/ruff check .
```

| Path                           | What it contains                                          |
| ------------------------------ | --------------------------------------------------------- |
| `__init__.py`                  | Setup, and giving entries of earlier versions a unique id |
| `config_flow.py`               | Adding an account, a new password and changing settings   |
| `login.py`                     | Logging in like the OmaPosti app                          |
| `api.py`                       | The shipments query and keeping the tokens valid          |
| `coordinator.py`               | Fetching the packages every 10 minutes                    |
| `shipments.py`                 | Turning shipments into the sensor's packages              |
| `sensor.py`                    | The sensors: the account's own, and the counts            |
| `event.py`                     | The event entity, one event per package change            |
| `changes.py`                   | What happened to the packages between two updates         |
| `translations/<language>.json` | Home Assistant UI texts                                   |

[releases-shield]: https://img.shields.io/github/release/jesmak/posti_tracking.svg?style=for-the-badge
[release-date-shield]: https://img.shields.io/github/release-date/jesmak/posti_tracking?style=for-the-badge
[releases]: https://github.com/jesmak/posti_tracking/releases
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[hacs]: https://hacs.xyz/docs/faq/custom_repositories/
[home-assistant-shield]: https://img.shields.io/badge/Home%20Assistant-UI%20setup-green.svg?style=for-the-badge
[home-assistant]: https://www.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/jesmak/posti_tracking.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/maintenance/yes/2026.svg?style=for-the-badge
[commits-shield]: https://img.shields.io/github/commit-activity/y/jesmak/posti_tracking.svg?style=for-the-badge
[commits]: https://github.com/jesmak/posti_tracking/commits/master
[bugs-shield]: https://img.shields.io/github/issues/jesmak/posti_tracking/bug?style=for-the-badge&label=bugs&color=red
[bugs]: https://github.com/jesmak/posti_tracking/labels/bug
[enhancements-shield]: https://img.shields.io/github/issues/jesmak/posti_tracking/enhancement?style=for-the-badge&label=enhancements&color=blue
[enhancements]: https://github.com/jesmak/posti_tracking/labels/enhancement
