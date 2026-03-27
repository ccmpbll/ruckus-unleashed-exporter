#!/usr/bin/env python3
"""
Ruckus Unleashed Prometheus Exporter + Loki Event Pusher

Scrapes wireless stats from the Unleashed AJAX API via aioruckus on every
Prometheus scrape request, and pushes events/alarms to Loki.

Environment Variables:
  RUCKUS_HOST       - IP or hostname of Unleashed AP (required)
  RUCKUS_USER       - Unleashed admin username (required)
  RUCKUS_PASSWORD   - Unleashed admin password (required)
  EXPORTER_PORT     - Prometheus metrics port (default: 9785)
  LOKI_URL          - Loki push endpoint (optional, e.g. http://loki:3100/loki/api/v1/push)
  LOKI_JOB          - Loki job label (default: ruckus_unleashed)
  LOG_LEVEL         - Logging level (default: INFO)
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time

import aiohttp
from aiohttp import web
from aioruckus import AjaxSession, SystemStat
from prometheus_client import (
    CollectorRegistry,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RUCKUS_HOST = os.environ.get("RUCKUS_HOST", "")
RUCKUS_USER = os.environ.get("RUCKUS_USER", "")
RUCKUS_PASSWORD = os.environ.get("RUCKUS_PASSWORD", "")
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "9785"))
LOKI_URL = os.environ.get("LOKI_URL", "")
LOKI_JOB = os.environ.get("LOKI_JOB", "ruckus_unleashed")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ruckus_exporter")

# ---------------------------------------------------------------------------
# Persistent state (survives across scrapes)
# ---------------------------------------------------------------------------

# Loki dedup: only push events/alarms newer than what we've already seen
_last_event_time = 0
_last_alarm_time = 0

# Cumulative event/alarm counts since process start
_event_counts: dict[str, int] = {}
_alarm_counts: dict[str, int] = {}

# Prevents concurrent API sessions if Prometheus fires overlapping scrapes
_scrape_lock: asyncio.Lock | None = None


def _get_scrape_lock() -> asyncio.Lock:
    global _scrape_lock
    if _scrape_lock is None:
        _scrape_lock = asyncio.Lock()
    return _scrape_lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _radio_band(radio_data: dict) -> str:
    channel = _safe_int(radio_data.get("channel", radio_data.get("@channel", 0)))
    if 1 <= channel <= 14:
        return "2.4GHz"
    elif 36 <= channel <= 177:
        return "5GHz"
    elif channel > 177:
        return "6GHz"
    radio_id = str(radio_data.get("@radio", radio_data.get("radio", "")))
    if radio_id in ("wifi0", "0", "a/b/g/n"):
        return "2.4GHz"
    elif radio_id in ("wifi1", "1", "a/n/ac"):
        return "5GHz"
    return f"unknown-ch{channel}"


def _client_band(client: dict) -> str:
    radio_type = str(client.get("radio-type", client.get("@radio-type", "")))
    if "11b" in radio_type or "11g" in radio_type or "2.4" in radio_type:
        return "2.4GHz"
    if "11a" in radio_type or "11n/a" in radio_type or "11ac" in radio_type or "11ax-5" in radio_type or "5" in radio_type:
        return "5GHz"
    channel = _safe_int(client.get("channel", client.get("@channel", 0)))
    if 1 <= channel <= 14:
        return "2.4GHz"
    elif channel >= 36:
        return "5GHz"
    return "unknown"


# ---------------------------------------------------------------------------
# Loki Pusher
# ---------------------------------------------------------------------------

async def push_to_loki(entries: list[dict], labels: dict):
    if not LOKI_URL or not entries:
        return

    values = []
    for entry in entries:
        ts = entry.get("_ts", int(time.time()))
        ts_ns = str(int(ts) * 1_000_000_000)
        msg = entry.get("_msg", json.dumps(entry, default=str))
        values.append([ts_ns, msg])

    payload = {"streams": [{"stream": labels, "values": values}]}

    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                LOKI_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    log.warning("Loki push failed (%s): %s", resp.status, body[:200])
                else:
                    log.debug("Pushed %d entries to Loki", len(values))
    except Exception as e:
        log.warning("Loki push error: %s", e)


async def process_events(api):
    global _last_event_time, _last_alarm_time

    # --- Events ---
    try:
        all_events = await api.get_all_events(limit=100)
        new_events = []
        max_ts = _last_event_time
        for ev in all_events:
            ev_time = _safe_int(ev.get("time", ev.get("@time", 0)))
            if ev_time > _last_event_time:
                ev_type = ev.get("type", ev.get("@type", "unknown"))
                _event_counts[ev_type] = _event_counts.get(ev_type, 0) + 1
                new_events.append({
                    "_ts": ev_time,
                    "_msg": json.dumps(ev, default=str),
                })
                max_ts = max(max_ts, ev_time)
        _last_event_time = max_ts
        if new_events:
            await push_to_loki(new_events, {
                "job": LOKI_JOB,
                "host": RUCKUS_HOST,
                "log_type": "event",
            })
            log.info("Pushed %d new events to Loki", len(new_events))
    except Exception as e:
        log.error("Error fetching events: %s", e)

    # --- Alarms ---
    try:
        all_alarms = await api.get_all_alarms(limit=50)
        new_alarms = []
        max_ts = _last_alarm_time
        for alarm in all_alarms:
            alarm_time = _safe_int(alarm.get("time", alarm.get("@time", 0)))
            if alarm_time > _last_alarm_time:
                severity = alarm.get("severity", alarm.get("@severity", "unknown"))
                _alarm_counts[severity] = _alarm_counts.get(severity, 0) + 1
                new_alarms.append({
                    "_ts": alarm_time,
                    "_msg": json.dumps(alarm, default=str),
                })
                max_ts = max(max_ts, alarm_time)
        _last_alarm_time = max_ts
        if new_alarms:
            await push_to_loki(new_alarms, {
                "job": LOKI_JOB,
                "host": RUCKUS_HOST,
                "log_type": "alarm",
            })
            log.info("Pushed %d new alarms to Loki", len(new_alarms))
    except Exception as e:
        log.error("Error fetching alarms: %s", e)


# ---------------------------------------------------------------------------
# Metric Collection (called on every Prometheus scrape)
# ---------------------------------------------------------------------------

async def collect_metrics() -> bytes:
    """Connect to Unleashed via aioruckus, collect all metrics, return Prometheus text."""
    registry = CollectorRegistry()

    # -- Scrape health --
    scrape_success = Gauge(
        "ruckus_scrape_success", "Whether the last scrape succeeded (1=yes, 0=no)",
        registry=registry,
    )
    scrape_duration = Gauge(
        "ruckus_scrape_duration_seconds", "Duration of the last scrape in seconds",
        registry=registry,
    )

    # -- System --
    system_info = Info("ruckus_system", "Unleashed system information", registry=registry)
    system_cpu = Gauge("ruckus_system_cpu_percent", "CPU utilization", registry=registry)
    system_memory = Gauge("ruckus_system_memory_percent", "Memory utilization", registry=registry)
    system_num_ap = Gauge("ruckus_system_ap_count", "Number of APs", registry=registry)
    system_num_clients = Gauge("ruckus_system_client_count", "Number of authorized clients", registry=registry)

    # -- Per-AP --
    ap_status = Gauge(
        "ruckus_ap_status", "AP connection status (1=connected, 0=other)",
        ["ap_mac", "ap_name", "ap_model"], registry=registry,
    )
    ap_clients = Gauge(
        "ruckus_ap_client_count", "Number of clients connected to this AP",
        ["ap_mac", "ap_name"], registry=registry,
    )

    # -- Per-radio --
    radio_clients = Gauge(
        "ruckus_radio_client_count", "Number of clients on this radio",
        ["ap_mac", "ap_name", "radio_band", "channel"], registry=registry,
    )
    radio_tx_power = Gauge(
        "ruckus_radio_tx_power_dbm", "Radio transmit power in dBm",
        ["ap_mac", "ap_name", "radio_band"], registry=registry,
    )
    radio_noise_floor = Gauge(
        "ruckus_radio_noise_floor_dbm", "Radio noise floor in dBm",
        ["ap_mac", "ap_name", "radio_band"], registry=registry,
    )
    radio_phy_errors = Gauge(
        "ruckus_radio_phy_errors_total", "Radio physical layer errors",
        ["ap_mac", "ap_name", "radio_band"], registry=registry,
    )
    radio_channel_utilization = Gauge(
        "ruckus_radio_channel_utilization_percent", "Channel utilization (airtime busy)",
        ["ap_mac", "ap_name", "radio_band"], registry=registry,
    )

    # -- Per-client --
    client_rssi = Gauge(
        "ruckus_client_rssi_dbm", "Client RSSI signal strength",
        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry,
    )
    client_tx_rate = Gauge(
        "ruckus_client_tx_rate_mbps", "Client TX data rate in Mbps",
        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry,
    )
    client_rx_rate = Gauge(
        "ruckus_client_rx_rate_mbps", "Client RX data rate in Mbps",
        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry,
    )
    client_tx_bytes = Gauge(
        "ruckus_client_tx_bytes", "Client TX bytes",
        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry,
    )
    client_rx_bytes = Gauge(
        "ruckus_client_rx_bytes", "Client RX bytes",
        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry,
    )

    # -- Per-VAP --
    vap_clients = Gauge(
        "ruckus_vap_client_count", "Number of clients on this VAP",
        ["ap_mac", "ssid", "radio_band", "bssid"], registry=registry,
    )
    vap_tx_bytes = Gauge(
        "ruckus_vap_tx_bytes", "VAP total TX bytes",
        ["ap_mac", "ssid", "radio_band"], registry=registry,
    )
    vap_rx_bytes = Gauge(
        "ruckus_vap_rx_bytes", "VAP total RX bytes",
        ["ap_mac", "ssid", "radio_band"], registry=registry,
    )

    # -- Event/alarm counts (cumulative since process start) --
    events_seen = Gauge(
        "ruckus_events_total", "Total events observed by type since process start",
        ["event_type"], registry=registry,
    )
    alarms_seen = Gauge(
        "ruckus_alarms_total", "Total alarms observed by severity since process start",
        ["severity"], registry=registry,
    )

    start = time.monotonic()
    ap_count = 0
    client_count = 0

    try:
        async with AjaxSession.async_create(
            RUCKUS_HOST, RUCKUS_USER, RUCKUS_PASSWORD
        ) as session:
            api = session.api

            # -----------------------------------------------------------
            # System Info
            # -----------------------------------------------------------
            try:
                sysinfo = await api.get_system_info(SystemStat.ALL)
                identity = sysinfo.get("identity", {})
                sys_stats = sysinfo.get("sysinfo", {})

                system_info.info({
                    "name": str(identity.get("name", "")),
                    "model": str(identity.get("model", "")),
                    "serial": str(identity.get("serial", "")),
                    "version": str(identity.get("version", "")),
                    "country_code": str(identity.get("country-code", "")),
                    "ip": str(identity.get("ip-addr", RUCKUS_HOST)),
                })
                system_cpu.set(_safe_float(sys_stats.get("cpu", 0)))
                system_memory.set(_safe_float(sys_stats.get("memory", 0)))
            except Exception as e:
                log.error("Error collecting system info: %s", e)

            # -----------------------------------------------------------
            # AP Stats
            # -----------------------------------------------------------
            try:
                ap_stats_list = await api.get_ap_stats()
                ap_count = len(ap_stats_list)
                total_clients = 0

                for ap in ap_stats_list:
                    mac = ap.get("mac", ap.get("@mac", "unknown"))
                    name = ap.get("devname", ap.get("@devname", mac))
                    model = ap.get("model", ap.get("@model", "unknown"))
                    status_val = ap.get("status", ap.get("@status", ""))
                    is_connected = 1 if str(status_val).lower() in ("1", "connected") else 0

                    ap_status.labels(ap_mac=mac, ap_name=name, ap_model=model).set(is_connected)

                    ap_client_count = _safe_int(ap.get("client", ap.get("@client", 0)))
                    ap_clients.labels(ap_mac=mac, ap_name=name).set(ap_client_count)
                    total_clients += ap_client_count

                    radios = ap.get("radio", [])
                    if isinstance(radios, dict):
                        radios = [radios]

                    for radio in radios:
                        if not isinstance(radio, dict):
                            continue
                        band = _radio_band(radio)
                        channel = str(radio.get("channel", radio.get("@channel", "0")))

                        radio_clients.labels(ap_mac=mac, ap_name=name, radio_band=band, channel=channel).set(
                            _safe_int(radio.get("client", radio.get("@client", 0)))
                        )
                        radio_tx_power.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("tx-power", radio.get("@tx-power", 0)))
                        )
                        radio_noise_floor.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("noise-floor", radio.get("@noise-floor", 0)))
                        )
                        radio_phy_errors.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("phy-error", radio.get("@phy-error", 0)))
                        )
                        radio_channel_utilization.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(
                                radio.get("channel-utilization",
                                radio.get("@channel-utilization",
                                radio.get("airtime",
                                radio.get("@airtime", 0))))
                            )
                        )

                system_num_ap.set(ap_count)
                system_num_clients.set(total_clients)

            except Exception as e:
                log.error("Error collecting AP stats: %s", e)

            # -----------------------------------------------------------
            # Active Clients
            # -----------------------------------------------------------
            try:
                clients = await api.get_active_clients()
                client_count = len(clients)

                for cl in clients:
                    cl_mac = cl.get("mac", cl.get("@mac", "unknown"))
                    cl_name = cl.get("hostname", cl.get("@hostname",
                              cl.get("user", cl.get("@user", cl_mac))))
                    cl_ap = cl.get("ap", cl.get("@ap", "unknown"))
                    cl_ssid = cl.get("ssid", cl.get("@ssid",
                              cl.get("wlan", cl.get("@wlan", "unknown"))))
                    cl_band = _client_band(cl)

                    labels = dict(client_mac=cl_mac, client_name=cl_name,
                                  ap_mac=cl_ap, ssid=cl_ssid, radio_band=cl_band)

                    client_rssi.labels(**labels).set(_safe_float(cl.get("signal", cl.get("@signal", 0))))
                    client_tx_rate.labels(**labels).set(_safe_float(cl.get("tx-rate", cl.get("@tx-rate", 0))))
                    client_rx_rate.labels(**labels).set(_safe_float(cl.get("rx-rate", cl.get("@rx-rate", 0))))
                    client_tx_bytes.labels(**labels).set(_safe_float(cl.get("tx-bytes", cl.get("@tx-bytes", 0))))
                    client_rx_bytes.labels(**labels).set(_safe_float(cl.get("rx-bytes", cl.get("@rx-bytes", 0))))

            except Exception as e:
                log.error("Error collecting client stats: %s", e)

            # -----------------------------------------------------------
            # VAP / Per-SSID Stats
            # -----------------------------------------------------------
            try:
                vaps = await api.get_vap_stats()

                for vap in vaps:
                    v_ap = vap.get("ap-mac", vap.get("@ap-mac",
                           vap.get("mac", vap.get("@mac", "unknown"))))
                    v_ssid = vap.get("ssid", vap.get("@ssid",
                             vap.get("wlan", vap.get("@wlan", "unknown"))))
                    v_bssid = vap.get("bssid", vap.get("@bssid", "unknown"))
                    v_band = _radio_band(vap)

                    vap_clients.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band, bssid=v_bssid).set(
                        _safe_int(vap.get("client", vap.get("@client", 0)))
                    )
                    vap_tx_bytes.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("tx-bytes", vap.get("@tx-bytes", 0)))
                    )
                    vap_rx_bytes.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("rx-bytes", vap.get("@rx-bytes", 0)))
                    )

            except Exception as e:
                log.error("Error collecting VAP stats: %s", e)

            # -----------------------------------------------------------
            # Events + Alarms -> Loki
            # -----------------------------------------------------------
            await process_events(api)

        scrape_success.set(1)

    except Exception as e:
        scrape_success.set(0)
        log.error("Scrape failed: %s", e, exc_info=True)

    duration = time.monotonic() - start
    scrape_duration.set(duration)
    log.info("Scrape complete in %.1fs — APs=%d clients=%d", duration, ap_count, client_count)

    for ev_type, count in _event_counts.items():
        events_seen.labels(event_type=ev_type).set(count)
    for severity, count in _alarm_counts.items():
        alarms_seen.labels(severity=severity).set(count)

    return generate_latest(registry)


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

async def metrics_handler(request):
    async with _get_scrape_lock():
        output = await collect_metrics()
    return web.Response(body=output, content_type=CONTENT_TYPE_LATEST)


async def health_handler(request):
    return web.Response(text="ok")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    if not RUCKUS_HOST or not RUCKUS_USER or not RUCKUS_PASSWORD:
        log.error("RUCKUS_HOST, RUCKUS_USER, and RUCKUS_PASSWORD environment variables are required")
        sys.exit(1)

    log.info("Starting Ruckus Unleashed exporter")
    log.info("  Target: %s (user: %s)", RUCKUS_HOST, RUCKUS_USER)
    log.info("  Metrics port: %d", EXPORTER_PORT)
    log.info("  Loki: %s", LOKI_URL or "disabled")

    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", EXPORTER_PORT)
    await site.start()
    log.info("Listening on :%d", EXPORTER_PORT)

    await asyncio.Event().wait()


if __name__ == "__main__":
    def _shutdown(sig, frame):
        log.info("Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    asyncio.run(main())
