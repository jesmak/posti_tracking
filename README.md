# Posti package tracking for Home Assistant

## What is it?

A custom component that lists the coming and recently delivered packages of an [OmaPosti](https://oma.posti.fi/)
account. There's no need to add packages by hand: the list comes from the packages in your account, and it's updated
every 10 minutes.

The sensor's attributes list the packages in the same format as [Matkahuolto package
tracking](https://github.com/jesmak/matkahuolto_tracking), so
[package-tracker-card](https://github.com/jesmak/package-tracker-card) can show packages from both.

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

## Sensor

The state is the time a package of the account last changed, such as when it was received, moved or delivered. The
sensor is named after the account, for example `sensor.posti_matti_meikalainen_example_com`.

The `packages` attribute lists the packages, and each package has:

| Key                                         | Description                                                 |
| ------------------------------------------- | ----------------------------------------------------------- |
| `shipment_number`                           | The tracking number, or Posti's shipment number without one |
| `status`                                    | The package's status, below                                 |
| `raw_status`                                | Posti's shipment phase, such as `READY_FOR_PICKUP`          |
| `origin`, `origin_city`                     | The sender and its city                                     |
| `destination`, `destination_city`           | The pickup point, or the receiver without one, and the city |
| `shipment_date`                             | When Posti received the shipment's details                  |
| `latest_event`                              | The latest event, in the chosen language when Posti has it  |
| `latest_event_city`, `latest_event_country` | Where the latest event happened                             |
| `latest_event_date`                         | When the package last changed                               |
| `source`                                    | Always `Posti`                                              |

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

Times are ISO 8601 in UTC. Packages without events, which Posti has only been told about, aren't listed. The packages
aren't stored in the recorder, only the state. The sensor is unavailable while Posti can't be reached.

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
| `sensor.py`                    | The sensor                                                |
| `translations/<language>.json` | Home Assistant UI texts                                   |
