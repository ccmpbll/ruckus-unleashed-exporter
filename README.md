# ruckus-unleashed-exporter

Prometheus exporter and Loki event pusher for Ruckus Unleashed access points.

Scrapes the Unleashed AJAX API via [aioruckus](https://github.com/ms264556/aioruckus) and exposes metrics for Prometheus. Optionally pushes events and alarms to Loki.

## Quick Start

```bash
docker run -d \
  --name ruckus-exporter \
  --restart unless-stopped \
  -p 9785:9785 \
  -e RUCKUS_HOST=10.42.11.5 \
  -e RUCKUS_PASSWORD=your_password \
  ccampbell/ruckus-unleashed-exporter
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `RUCKUS_HOST` | Yes | — | IP or hostname of the Unleashed master AP |
| `RUCKUS_PASSWORD` | Yes | — | Unleashed admin password |
| `RUCKUS_USER` | No | `admin` | Unleashed admin username |
| `EXPORTER_PORT` | No | `9785` | Port to expose Prometheus metrics on |
| `POLL_INTERVAL` | No | `60` | Seconds between data collection cycles |
| `LOKI_URL` | No | — | Loki push endpoint (e.g. `http://loki:3100/loki/api/v1/push`). Leave unset to disable. |
| `LOKI_JOB` | No | `ruckus_unleashed` | Job label applied to all Loki streams |
| `LOG_LEVEL` | No | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## With Loki

```bash
docker run -d \
  --name ruckus-exporter \
  --restart unless-stopped \
  -p 9785:9785 \
  -e RUCKUS_HOST=10.42.11.5 \
  -e RUCKUS_PASSWORD=your_password \
  -e LOKI_URL=http://loki:3100/loki/api/v1/push \
  ccampbell/ruckus-unleashed-exporter
```

## Prometheus Scrape Config

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: ruckus-unleashed
    scrape_interval: 65s
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
| `ruckus_system_info` | System identity (name, model, serial, firmware version) |
| `ruckus_system_cpu_percent` | CPU utilization |
| `ruckus_system_memory_percent` | Memory utilization |
| `ruckus_system_ap_count` | Number of APs |
| `ruckus_system_client_count` | Number of connected clients |

### Per-AP

| Metric | Labels | Description |
|---|---|---|
| `ruckus_ap_status` | `ap_mac`, `ap_name`, `ap_model` | Connection status (1=connected) |
| `ruckus_ap_client_count` | `ap_mac`, `ap_name` | Clients connected to this AP |

### Per-Radio

| Metric | Labels | Description |
|---|---|---|
| `ruckus_radio_client_count` | `ap_mac`, `ap_name`, `radio_band`, `channel` | Clients on this radio |
| `ruckus_radio_tx_power_dbm` | `ap_mac`, `ap_name`, `radio_band` | Transmit power |
| `ruckus_radio_noise_floor_dbm` | `ap_mac`, `ap_name`, `radio_band` | Noise floor |
| `ruckus_radio_phy_errors_total` | `ap_mac`, `ap_name`, `radio_band` | Physical layer errors |
| `ruckus_radio_channel_utilization_percent` | `ap_mac`, `ap_name`, `radio_band` | Airtime utilization |

### Per-Client

| Metric | Labels | Description |
|---|---|---|
| `ruckus_client_rssi_dbm` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Signal strength |
| `ruckus_client_tx_rate_mbps` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | TX data rate |
| `ruckus_client_rx_rate_mbps` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | RX data rate |
| `ruckus_client_tx_bytes` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Total TX bytes |
| `ruckus_client_rx_bytes` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Total RX bytes |

### Per-VAP (SSID)

| Metric | Labels | Description |
|---|---|---|
| `ruckus_vap_client_count` | `ap_mac`, `ssid`, `radio_band`, `bssid` | Clients on this VAP |
| `ruckus_vap_tx_bytes` | `ap_mac`, `ssid`, `radio_band` | Total TX bytes |
| `ruckus_vap_rx_bytes` | `ap_mac`, `ssid`, `radio_band` | Total RX bytes |

### Events / Alarms (Loki)

Events and alarms are pushed to Loki as log streams when `LOKI_URL` is set. Prometheus counters are also maintained:

| Metric | Labels | Description |
|---|---|---|
| `ruckus_events_total` | `event_type` | Total events observed |
| `ruckus_alarms_total` | `severity` | Total alarms observed |

## Tested On

- Ruckus R850, Unleashed 200.18.7.x
