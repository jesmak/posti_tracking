# Posti package tracking for Home Assistant

## What is it?

A custom component that integrates with Posti to retrieve information about coming and recently delivered packages.
The big idea of this integration is that one does not have to manually add packages in order to track them, but the list
is updated automatically by retrieving tracking data from the user's account.

## Installation

### With HACS

1. Add this repository to HACS custom repositories
2. Search for Matkahuolto tracking in HACS and install
3. Restart Home Assistant
4. Enter your account credentials and configre other settings as you wish

### Manual

1. Download source code from latest release tag
2. Copy custom_components/posti_tracking folder to your Home Assistant installation's config/custom_components folder.
3. Restart Home Assistant
4. Configure the integration by adding a new integration in settings/integrations page of Home Assistant
5. Enter your account credentials and configre other settings as you wish

### Integration settings

| Name                         | Type    | Requirement  | Description                                          | Default             |
| ---------------------------- | ------- | ------------ | ---------------------------------------------------- | ------------------- |
| username                     | string  | **Required** | Username of your Posti account (your email)          |                     |
| password                     | string  | **Required** | Password of your Posti account                       |                     |
| language                     | string  | **Required** | Used language (fi or en)                             | `en`                |
| prioritize_undelivered       | boolean | **Required** | Toggle this if you want undelivered packages to be shown first, when there are more than the maximum allowed amount of packages available | `true`              |
| max_shipments                | int     | **Required** | Maximum number of packages to retrieve               | `5`                 |
| stale_shipment_day_limit     | int     | **Required** | After how many days a stalled shipment gets hidden? Sometimes shipments stay in "in delivery" state indefinitely | `15`              |
| completed_shipment_day_shown | int     | **Required** | After how many days are completed deliveries hidden? | `3`                 |
