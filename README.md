# ruckus-unleashed-exporter
![Image Build Status](https://img.shields.io/github/actions/workflow/status/ccmpbll/ruckus-unleashed-exporter/docker-image.yml?branch=main) ![Docker Image Size](https://img.shields.io/docker/image-size/ccmpbll/ruckus-unleashed-exporter/latest) ![Docker Pulls](https://img.shields.io/docker/pulls/ccmpbll/ruckus-unleashed-exporter.svg) ![License](https://img.shields.io/badge/License-GPLv3-blue.svg)

Prometheus exporter and Loki event pusher for Ruckus Unleashed access points.

Scrapes the Unleashed AJAX API via [aioruckus](https://github.com/ms264556/aioruckus) and exposes metrics for Prometheus. Optionally pushes events and alarms to Loki.

## Quick Start

```bash
docker run -d \
  --name ruckus-exporter \
  --restart unless-stopped \
  -p 9785:9785 \
  -e RUCKUS_HOST=192.168.1.5 \
  -e RUCKUS_USER=admin \
  -e RUCKUS_PASSWORD=your_password \
  ccmpbll/ruckus-unleashed-exporter
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `RUCKUS_HOST` | Yes | — | IP or hostname of the Unleashed master AP |
| `RUCKUS_PASSWORD` | Yes | — | Unleashed admin password |
| `RUCKUS_USER` | Yes | — | Unleashed admin username |
| `EXPORTER_PORT` | No | `9785` | Port to expose Prometheus metrics on |
| `LOKI_URL` | No | — | Loki push endpoint (e.g. `http://loki:3100/loki/api/v1/push`). Leave unset to disable. |
| `LOKI_JOB` | No | `ruckus_unleashed` | Job label applied to all Loki streams |
| `LOG_LEVEL` | No | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## With Loki

```bash
docker run -d \
  --name ruckus-exporter \
  --restart unless-stopped \
  -p 9785:9785 \
  -e RUCKUS_HOST=192.168.1.5 \
  -e RUCKUS_USER=admin \
  -e RUCKUS_PASSWORD=your_password \
  -e LOKI_URL=http://loki:3100/loki/api/v1/push \
  ccmpbll/ruckus-unleashed-exporter
```

## Prometheus Scrape Config

Each scrape triggers a live API call to the Unleashed controller. Set `scrape_timeout` high enough to cover the round trip — 30s is a safe default. `scrape_interval` controls how often metrics are collected.

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: ruckus-unleashed
    scrape_interval: 60s
    scrape_timeout: 30s
    static_configs:
      - targets:
          - ruckus-exporter:9785
```

## Endpoints

| Endpoint | Description |
|---|---|
| `/metrics` | Prometheus metrics |
| `/health` | Health check (returns `ok`) |

## Metrics

### System

| Metric | Description |
|---|---|
| `ruckus_system_info` | System identity: name, ip, model, serial, firmware, hardware_version |
| `ruckus_system_cpu_percent` | CPU utilization |
| `ruckus_system_memory_percent` | Memory utilization |
| `ruckus_system_ap_count` | Number of APs |
| `ruckus_system_client_count` | Number of connected clients |

### Per-AP

| Metric | Labels | Description |
|---|---|---|
| `ruckus_ap_status` | `ap_mac`, `ap_name`, `ap_model` | Connection status (1=connected) |
| `ruckus_ap_client_count` | `ap_mac`, `ap_name` | Clients connected to this AP |
| `ruckus_ap_uptime_seconds` | `ap_mac`, `ap_name` | AP uptime in seconds |
| `ruckus_ap_lan_rx_bytes` | `ap_mac`, `ap_name` | LAN interface RX bytes |
| `ruckus_ap_lan_tx_bytes` | `ap_mac`, `ap_name` | LAN interface TX bytes |

### Per-Radio

| Metric | Labels | Description |
|---|---|---|
| `ruckus_radio_client_count` | `ap_mac`, `ap_name`, `radio_band`, `channel` | Clients on this radio |
| `ruckus_radio_tx_power` | `ap_mac`, `ap_name`, `radio_band` | TX power relative to max (0=Full/Auto, -1 to -10=reduction steps, -24=min) |
| `ruckus_radio_noise_floor_dbm` | `ap_mac`, `ap_name`, `radio_band` | Noise floor in dBm |
| `ruckus_radio_phy_errors_total` | `ap_mac`, `ap_name`, `radio_band` | PHY errors |
| `ruckus_radio_channel_utilization_percent` | `ap_mac`, `ap_name`, `radio_band` | Airtime busy % |
| `ruckus_radio_airtime_rx_percent` | `ap_mac`, `ap_name`, `radio_band` | Airtime RX % |
| `ruckus_radio_airtime_tx_percent` | `ap_mac`, `ap_name`, `radio_band` | Airtime TX % |
| `ruckus_radio_tx_bytes` | `ap_mac`, `ap_name`, `radio_band` | Radio total TX bytes |
| `ruckus_radio_rx_bytes` | `ap_mac`, `ap_name`, `radio_band` | Radio total RX bytes |
| `ruckus_radio_tx_retries_total` | `ap_mac`, `ap_name`, `radio_band` | TX retries |

### Per-Client

| Metric | Labels | Description |
|---|---|---|
| `ruckus_client_rssi_dbm` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Signal strength in dBm |
| `ruckus_client_noise_floor_dbm` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Noise floor in dBm |

### Per-VAP (SSID)

| Metric | Labels | Description |
|---|---|---|
| `ruckus_vap_client_count` | `ap_mac`, `ssid`, `radio_band`, `bssid` | Clients on this VAP |
| `ruckus_vap_tx_bytes` | `ap_mac`, `ssid`, `radio_band` | TX bytes |
| `ruckus_vap_rx_bytes` | `ap_mac`, `ssid`, `radio_band` | RX bytes |
| `ruckus_vap_tx_packets_total` | `ap_mac`, `ssid`, `radio_band` | TX packets |
| `ruckus_vap_rx_packets_total` | `ap_mac`, `ssid`, `radio_band` | RX packets |
| `ruckus_vap_tx_errors_total` | `ap_mac`, `ssid`, `radio_band` | TX errors |
| `ruckus_vap_rx_errors_total` | `ap_mac`, `ssid`, `radio_band` | RX errors |

### Events / Alarms (Loki)

Events and alarms are pushed to Loki as log streams when `LOKI_URL` is set. Prometheus counters are also maintained:

| Metric | Labels | Description |
|---|---|---|
| `ruckus_events_total` | `event_type` | Total events observed |
| `ruckus_alarms_total` | `severity` | Total alarms observed |

## Tested On

- Ruckus R850, Unleashed 200.18.7.101.244

