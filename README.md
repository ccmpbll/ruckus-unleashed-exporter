# ruckus-unleashed-exporter
![Image Build Status](https://img.shields.io/github/actions/workflow/status/ccmpbll/ruckus-unleashed-exporter/docker-image.yml?branch=main) ![Docker Image Size](https://img.shields.io/docker/image-size/ccmpbll/ruckus-unleashed-exporter/latest) ![Docker Pulls](https://img.shields.io/docker/pulls/ccmpbll/ruckus-unleashed-exporter.svg) ![License](https://img.shields.io/badge/License-GPLv3-blue.svg)

Prometheus exporter for Ruckus Unleashed access points.

Scrapes the Unleashed AJAX API via [aioruckus](https://github.com/ms264556/aioruckus) and exposes metrics for Prometheus.

## Quick Start

```bash
docker run -d \
  --name ruckus-exporter \
  --restart unless-stopped \
  -p 9785:9785 \
  -e RUCKUS_HOST=192.168.1.5 \
  -e RUCKUS_USER=admin \
  -e RUCKUS_PASS=your_password \
  ccmpbll/ruckus-unleashed-exporter
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `RUCKUS_HOST` | Yes | — | IP or hostname of the Unleashed master AP |
| `RUCKUS_PASS` | Yes | — | Unleashed admin password |
| `RUCKUS_USER` | Yes | — | Unleashed admin username |
| `EXPORTER_PORT` | No | `9785` | Port to expose Prometheus metrics on |
| `LOG_LEVEL` | No | `INFO` | Log verbosity for exporter output: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

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
| `/debug` | Raw API response data from the last scrape (JSON). Sensitive fields (`wpa-passphrase`, `preSharedKey`, `psk`) are redacted. |

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
| `ruckus_ap_lan_rx_bytes_total` | `ap_mac`, `ap_name` | Total cumulative LAN interface RX bytes |
| `ruckus_ap_lan_tx_bytes_total` | `ap_mac`, `ap_name` | Total cumulative LAN interface TX bytes |
| `ruckus_ap_reboot_total` | `ap_mac`, `ap_name`, `reason` | Cumulative reboot count by reason (`application`, `user`, `reset_button`, `kernel_panic`, `watchdog`, `powercycle`) |
| `ruckus_ap_rogue_count` | `ap_mac`, `ap_name` | Number of rogue APs detected on the LAN by this AP |

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
| `ruckus_radio_tx_bytes_total` | `ap_mac`, `ap_name`, `radio_band` | Total cumulative radio TX bytes |
| `ruckus_radio_rx_bytes_total` | `ap_mac`, `ap_name`, `radio_band` | Total cumulative radio RX bytes |
| `ruckus_radio_tx_retries_total` | `ap_mac`, `ap_name`, `radio_band` | TX retries |
| `ruckus_radio_tx_packets_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative TX packet count |
| `ruckus_radio_tx_failures_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative TX failure count (distinct from retries) |
| `ruckus_radio_avg_rssi_dbm` | `ap_mac`, `ap_name`, `radio_band` | Average RSSI of all associated clients in dBm |
| `ruckus_radio_channel_width_mhz` | `ap_mac`, `ap_name`, `radio_band` | Channel width in MHz |
| `ruckus_radio_assoc_failures_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative client association failures |
| `ruckus_radio_disassoc_abnormal_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative abnormal client disassociations |
| `ruckus_radio_rx_packets_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative RX packet count |
| `ruckus_radio_rx_decrypt_errors_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative RX decryption errors (non-zero may indicate wrong credentials or deauth attacks) |
| `ruckus_radio_auth_failures_total` | `ap_mac`, `ap_name`, `radio_band` | Cumulative client authentication failures |

### Per-Client

| Metric | Labels | Description |
|---|---|---|
| `ruckus_client_rssi_dbm` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Signal strength in dBm |
| `ruckus_client_noise_floor_dbm` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Noise floor in dBm |
| `ruckus_client_snr_db` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Signal-to-noise ratio in dB (only set when both RSSI and noise floor are non-zero) |
| `ruckus_client_protocol_info` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Per-client negotiated protocol and connection attributes: `ieee80211_radio_type`, `encryption`, `auth_method`, `vlan`, `rssi_level`, `health_level`, `ip`, `ipv6`, `device_type`, `model`, `channelization`, `channel` |
| `ruckus_client_session_duration_seconds` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Seconds since client first associated in this session |
| `ruckus_client_session_rx_bytes` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Bytes received by this client since association |
| `ruckus_client_session_tx_bytes` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Bytes transmitted by this client since association |
| `ruckus_client_session_rx_packets` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Packets received by this client since association |
| `ruckus_client_session_tx_packets` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | Packets transmitted by this client since association |
| `ruckus_client_session_retries` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | TX retries since association |
| `ruckus_client_session_rx_crc_errors` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | RX CRC errors since association |
| `ruckus_client_session_tx_drop_data` | `client_mac`, `client_name`, `ap_mac`, `ssid`, `radio_band` | TX data frame drops since association |

> **Note on per-client session metrics:** These are Gauges, not Counters. The Unleashed API reports cumulative totals since the client's current association — they reset to zero each time a client disconnects and reconnects. `rate()` will work correctly during a stable session, but will produce a transient dip to zero at reconnect events. For most home and small-office deployments where clients reconnect infrequently, this is negligible in practice.

### Per-VAP (SSID)

| Metric | Labels | Description |
|---|---|---|
| `ruckus_vap_client_count` | `ap_mac`, `ssid`, `radio_band`, `bssid` | Clients on this VAP |
| `ruckus_vap_tx_bytes_total` | `ap_mac`, `ssid`, `radio_band` | Total cumulative TX bytes |
| `ruckus_vap_rx_bytes_total` | `ap_mac`, `ssid`, `radio_band` | Total cumulative RX bytes |
| `ruckus_vap_tx_packets_total` | `ap_mac`, `ssid`, `radio_band` | TX packets |
| `ruckus_vap_rx_packets_total` | `ap_mac`, `ssid`, `radio_band` | RX packets |
| `ruckus_vap_tx_errors_total` | `ap_mac`, `ssid`, `radio_band` | TX errors |
| `ruckus_vap_rx_errors_total` | `ap_mac`, `ssid`, `radio_band` | RX errors |
| `ruckus_vap_tx_drop_packets_total` | `ap_mac`, `ssid`, `radio_band` | Cumulative TX data drop packet count |
| `ruckus_vap_rx_drop_packets_total` | `ap_mac`, `ssid`, `radio_band` | Cumulative RX drop packet count |
| `ruckus_vap_status` | `ap_mac`, `ssid`, `radio_band`, `bssid` | VAP operational status (1=Up, 0=Down) |

## Grafana Dashboard

A pre-built dashboard is available at [`dashboards/ruckus-unleashed.json`](dashboards/ruckus-unleashed.json). Import it via **Dashboards → Import** in Grafana and select your Prometheus datasource when prompted.

### Overview Row

| Panel | Description |
|---|---|
| AP Status | Per-AP up/down status with green/red background |
| Connected Clients | Total client count — green below 50, yellow at 50+ |
| Master AP CPU | CPU utilisation % with green/yellow/red thresholds |
| Master AP Memory | Memory utilisation % with green/yellow/red thresholds |
| Rogue APs Detected | Per-AP rogue count — green at 0, orange at 1+, red at 5+ |
| AP Uptime | Per-AP uptime formatted as days/hours/minutes |
| Total APs | Count of APs currently reporting metrics |
| Scrape | Last scrape status (OK/FAIL) and duration |

### Radio Performance Row

| Panel | Description |
|---|---|
| Channel Utilization % | Airtime busy % per radio band over time |
| Radio Throughput | TX/RX bytes per second per radio band |
| Radio Packet Rate | TX/RX packets per second per radio band |
| Avg Client RSSI | Average client signal strength per radio band over time |

### Radio Health Row

| Panel | Description |
|---|---|
| TX Errors & Retries | TX retry and failure rates per radio band |
| RX Decrypt Errors & Auth Failures | Decrypt errors and auth failures per radio band — elevated values may indicate credential issues or deauth attacks |
| Abnormal Disassociations & PHY Errors | Abnormal disassoc and PHY error rates per radio band |

### VAP / SSID Row

| Panel | Description |
|---|---|
| Clients per VAP | Instant client count per SSID and band |
| VAP Throughput | TX/RX bytes per second per SSID |
| VAP TX Errors | TX and RX error rates per SSID |

### Connected Clients Row

| Panel | Description |
|---|---|
| Clients by Band | Pie chart — client count split by 2.4GHz vs 5GHz |
| Clients by Wi-Fi Protocol | Pie chart — client count grouped by 802.11 protocol (802.11n, 802.11ac, etc.) |

### Client Traffic Row

| Panel | Description |
|---|---|
| Client Throughput (Top 10) | Time series — per-client TX/RX rate (bytes/sec) for the top 10 clients by current throughput |
| Session TX by Client | Bar gauge — cumulative bytes transmitted per client since last association, sorted descending |
| Session RX by Client | Bar gauge — cumulative bytes received per client since last association, sorted descending |
| Session Duration | Bar gauge — how long each client has been continuously associated, sorted descending |

### Client Detail Row

| Panel | Description |
|---|---|
| Client Detail | Per-client table showing: Client, MAC, IP, SSID, Band, Channel, Width, RSSI, SNR, Protocol, Encryption, Auth, Device Type, Model, Signal Level, Health, VLAN, IPv6 |

## Known Potential Issues

- **Per-client session metrics reset on reconnect**: `ruckus_client_session_rx_bytes`, `ruckus_client_session_tx_bytes`, and related session metrics count from the client's most recent association event — not from AP boot. A client disconnect/reconnect resets them to zero. These are exposed as Gauges rather than Counters for this reason. `rate()` works correctly during a stable session but will show a brief dip to zero at reconnect events.

- **`ruckus_radio_tx_power` reports unexpected values in Auto mode**: When a radio is set to Auto TX power, the AP may report the current dynamically-chosen power level (e.g. `25` on 2.4GHz) rather than `0`. This causes the metric to show a negative value instead of `0`. Radios set to Full or a manual reduction step behave as expected.

- **`ruckus_radio_phy_errors_total` is not a true monotonic counter**: Despite the `_total` suffix, PHY error values appear to reflect a rolling measurement window rather than a cumulative count since boot. Values may decrease between scrapes. This is a limitation of what the Unleashed API exposes.

- **`ruckus_vap_tx_drop_packets_total` mirrors `ruckus_vap_tx_errors_total`**: The underlying API fields `tx-data-drop-pkts` and `tx-errors` return identical values and increment together. They appear to be the same counter exposed under two names. Both metrics are retained for completeness but should be treated as equivalent.

## Tested On

- Ruckus R850, Unleashed 200.18.7.101.244

