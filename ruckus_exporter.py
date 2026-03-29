#!/usr/bin/env python3
"""
Ruckus Unleashed Prometheus Exporter

Scrapes wireless stats from the Unleashed AJAX API via aioruckus on every
Prometheus scrape request.

Environment Variables:
  RUCKUS_HOST       - IP or hostname of Unleashed AP (required)
  RUCKUS_USER       - Unleashed admin username (required)
  RUCKUS_PASS       - Unleashed admin password (required)
  EXPORTER_PORT     - Prometheus metrics port (default: 9785)
  LOG_LEVEL         - Logging level (default: INFO)
  DEBUG_DUMP        - Set to 1 to log raw API responses on first scrape (default: 0)
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time

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
RUCKUS_PASS = os.environ.get("RUCKUS_PASS", "")
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "9785"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
DEBUG_DUMP = os.environ.get("DEBUG_DUMP", "").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ruckus_exporter")

# ---------------------------------------------------------------------------
# Scrape lock (prevents overlapping scrapes)
# ---------------------------------------------------------------------------
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


def _band_from_str(s: str) -> str:
    """Normalise radio-band strings like '2.4g', '5g', '6g' to standard labels."""
    b = s.lower().strip()
    if b.startswith("2.4"):
        return "2.4GHz"
    if b.startswith("5"):
        return "5GHz"
    if b.startswith("6"):
        return "6GHz"
    return b


def _radio_band(data: dict) -> str:
    """Return band label from any dict that has a radio-band or channel field."""
    rb = str(data.get("radio-band", ""))
    if rb:
        return _band_from_str(rb)
    channel = _safe_int(data.get("channel", 0))
    if 1 <= channel <= 14:
        return "2.4GHz"
    if 36 <= channel <= 177:
        return "5GHz"
    if channel > 177:
        return "6GHz"
    return f"unknown-ch{channel}"


def _mem_percent(avail: str, total: str) -> float:
    t = _safe_float(total)
    a = _safe_float(avail)
    if t <= 0:
        return 0.0
    return round((t - a) / t * 100, 1)


# ---------------------------------------------------------------------------
# Metric Collection (called on every Prometheus scrape)
# ---------------------------------------------------------------------------

async def collect_metrics() -> bytes:
    """Connect to Unleashed via aioruckus, collect all metrics, return Prometheus text."""
    registry = CollectorRegistry()

    # -- Scrape health --
    scrape_success = Gauge("ruckus_scrape_success", "1 if last scrape succeeded", registry=registry)
    scrape_duration = Gauge("ruckus_scrape_duration_seconds", "Duration of last scrape", registry=registry)

    # -- System --
    system_info = Info("ruckus_system", "Unleashed system information", registry=registry)
    system_cpu = Gauge("ruckus_system_cpu_percent", "Master AP CPU utilization", registry=registry)
    system_memory = Gauge("ruckus_system_memory_percent", "Master AP memory utilization", registry=registry)
    system_num_ap = Gauge("ruckus_system_ap_count", "Number of APs", registry=registry)
    system_num_clients = Gauge("ruckus_system_client_count", "Number of connected clients", registry=registry)

    # -- Per-AP --
    ap_reboot = Gauge("ruckus_ap_reboot_total", "Cumulative AP reboot count by reason",
                      ["ap_mac", "ap_name", "reason"], registry=registry)
    ap_status = Gauge("ruckus_ap_status", "AP connection status (1=connected)",
                      ["ap_mac", "ap_name", "ap_model"], registry=registry)
    ap_clients = Gauge("ruckus_ap_client_count", "Clients connected to this AP",
                       ["ap_mac", "ap_name"], registry=registry)
    ap_uptime = Gauge("ruckus_ap_uptime_seconds", "AP uptime in seconds",
                      ["ap_mac", "ap_name"], registry=registry)
    ap_lan_rx_bytes = Gauge("ruckus_ap_lan_rx_bytes_total", "Total cumulative AP LAN interface RX bytes",
                            ["ap_mac", "ap_name"], registry=registry)
    ap_lan_tx_bytes = Gauge("ruckus_ap_lan_tx_bytes_total", "Total cumulative AP LAN interface TX bytes",
                            ["ap_mac", "ap_name"], registry=registry)

    # -- Per-radio --
    radio_clients = Gauge("ruckus_radio_client_count", "Clients on this radio",
                          ["ap_mac", "ap_name", "radio_band", "channel"], registry=registry)
    radio_tx_power = Gauge("ruckus_radio_tx_power", "Radio TX power relative to max (0=Full/Auto, negative=reduced)",
                           ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_noise_floor = Gauge("ruckus_radio_noise_floor_dbm", "Radio noise floor in dBm",
                              ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_phy_errors = Gauge("ruckus_radio_phy_errors_total", "Radio PHY errors",
                             ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_channel_utilization = Gauge("ruckus_radio_channel_utilization_percent", "Airtime busy %",
                                      ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_airtime_rx = Gauge("ruckus_radio_airtime_rx_percent", "Airtime RX %",
                             ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_airtime_tx = Gauge("ruckus_radio_airtime_tx_percent", "Airtime TX %",
                             ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_tx_bytes = Gauge("ruckus_radio_tx_bytes_total", "Total cumulative radio TX bytes",
                           ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_rx_bytes = Gauge("ruckus_radio_rx_bytes_total", "Total cumulative radio RX bytes",
                           ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_tx_retries = Gauge("ruckus_radio_tx_retries_total", "Radio TX retries",
                             ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_tx_packets = Gauge("ruckus_radio_tx_packets_total", "Radio cumulative TX packet count",
                             ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_tx_failures = Gauge("ruckus_radio_tx_failures_total", "Radio cumulative TX failure count (distinct from retries)",
                              ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_avg_rssi = Gauge("ruckus_radio_avg_rssi_dbm", "Average RSSI of all associated clients on this radio in dBm",
                           ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_channel_width = Gauge("ruckus_radio_channel_width_mhz", "Radio channel width in MHz",
                                ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_assoc_failures = Gauge("ruckus_radio_assoc_failures_total", "Cumulative client association failures on this radio",
                                 ["ap_mac", "ap_name", "radio_band"], registry=registry)
    radio_disassoc_abnormal = Gauge("ruckus_radio_disassoc_abnormal_total", "Cumulative abnormal client disassociations on this radio",
                                    ["ap_mac", "ap_name", "radio_band"], registry=registry)

    # -- Per-client --
    client_rssi = Gauge("ruckus_client_rssi_dbm", "Client signal strength in dBm",
                        ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry)
    client_noise_floor = Gauge("ruckus_client_noise_floor_dbm", "Client noise floor in dBm",
                               ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry)
    client_snr = Gauge("ruckus_client_snr_db", "Client signal-to-noise ratio in dB",
                       ["client_mac", "client_name", "ap_mac", "ssid", "radio_band"], registry=registry)

    # -- Per-VAP --
    vap_clients = Gauge("ruckus_vap_client_count", "Clients on this VAP",
                        ["ap_mac", "ssid", "radio_band", "bssid"], registry=registry)
    vap_tx_bytes = Gauge("ruckus_vap_tx_bytes_total", "Total cumulative VAP TX bytes",
                         ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_rx_bytes = Gauge("ruckus_vap_rx_bytes_total", "Total cumulative VAP RX bytes",
                         ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_tx_pkts = Gauge("ruckus_vap_tx_packets_total", "VAP TX packets",
                        ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_rx_pkts = Gauge("ruckus_vap_rx_packets_total", "VAP RX packets",
                        ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_tx_errors = Gauge("ruckus_vap_tx_errors_total", "VAP TX errors",
                          ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_rx_errors = Gauge("ruckus_vap_rx_errors_total", "VAP RX errors",
                          ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_tx_drop_pkts = Gauge("ruckus_vap_tx_drop_packets_total", "Cumulative TX data drop packet count on this VAP",
                             ["ap_mac", "ssid", "radio_band"], registry=registry)
    vap_rx_drop_pkts = Gauge("ruckus_vap_rx_drop_packets_total", "Cumulative RX drop packet count on this VAP",
                             ["ap_mac", "ssid", "radio_band"], registry=registry)

    start = time.monotonic()
    ap_count = 0
    client_count = 0

    try:
        async with AjaxSession.async_create(
            RUCKUS_HOST, RUCKUS_USER, RUCKUS_PASS
        ) as session:
            api = session.api

            # -----------------------------------------------------------
            # System Info (sysinfo for name/IP, ap_stats for everything else)
            # -----------------------------------------------------------
            try:
                sysinfo = await api.get_system_info(SystemStat.ALL)
                if DEBUG_DUMP:
                    log.info("DEBUG sysinfo identity: %s", json.dumps(sysinfo.get("identity", {}), default=str))
                    log.info("DEBUG sysinfo mgmt-ip: %s", json.dumps(sysinfo.get("mgmt-ip", {}), default=str))
                identity = sysinfo.get("identity", {})
                mgmt_ip = sysinfo.get("mgmt-ip", {})
                sys_name = str(identity.get("name", ""))
                sys_ip = str(mgmt_ip.get("ip", RUCKUS_HOST))
            except Exception as e:
                log.error("Error fetching sysinfo: %s", e)
                sys_name = ""
                sys_ip = RUCKUS_HOST

            # -----------------------------------------------------------
            # AP Stats
            # -----------------------------------------------------------
            try:
                ap_stats_list = await api.get_ap_stats()
                ap_count = len(ap_stats_list)

                if DEBUG_DUMP and ap_stats_list:
                    ap0 = ap_stats_list[0]
                    top = {k: v for k, v in ap0.items() if not isinstance(v, (list, dict))}
                    log.info("DEBUG ap_stats[0] top-level: %s", json.dumps(top, default=str))
                    for i, r in enumerate(ap0.get("radio", [])):
                        log.info("DEBUG ap_stats[0] radio[%d]: %s", i, json.dumps(r, default=str))

                master_ap = next(
                    (ap for ap in ap_stats_list if ap.get("role") == "master"),
                    ap_stats_list[0] if ap_stats_list else {}
                )

                system_info.info({
                    "name": sys_name,
                    "ip": sys_ip,
                    "model": str(master_ap.get("model", "")),
                    "serial": str(master_ap.get("serial-number", "")),
                    "firmware": str(master_ap.get("firmware-version", "")),
                    "hardware_version": str(master_ap.get("hardware-version", "")),
                })
                system_cpu.set(_safe_float(master_ap.get("cpu_util", 0)))
                system_memory.set(_mem_percent(
                    master_ap.get("mem_avail", "0"),
                    master_ap.get("mem_total", "0"),
                ))

                total_clients = 0
                for ap in ap_stats_list:
                    mac = ap.get("mac", "unknown")
                    name = ap.get("devname", mac)
                    model = ap.get("model", "unknown")
                    is_connected = 1 if str(ap.get("state", "0")) == "1" else 0
                    ap_client_count = _safe_int(ap.get("num-sta", 0))

                    ap_status.labels(ap_mac=mac, ap_name=name, ap_model=model).set(is_connected)
                    ap_clients.labels(ap_mac=mac, ap_name=name).set(ap_client_count)
                    ap_uptime.labels(ap_mac=mac, ap_name=name).set(_safe_float(ap.get("uptime", 0)))
                    ap_lan_rx_bytes.labels(ap_mac=mac, ap_name=name).set(_safe_float(ap.get("lan_stats_rx_byte", 0)))
                    ap_lan_tx_bytes.labels(ap_mac=mac, ap_name=name).set(_safe_float(ap.get("lan_stats_tx_byte", 0)))
                    reboot_reasons = {
                        "application":  "application-reboot-counter",
                        "user":         "user-reboot-counter",
                        "reset_button": "reset-button-reboot-counter",
                        "kernel_panic": "kernel-panic-reboot-counter",
                        "watchdog":     "watchdog-reboot-counter",
                        "powercycle":   "powercycle-reboot-counter",
                    }
                    for reason, field in reboot_reasons.items():
                        ap_reboot.labels(ap_mac=mac, ap_name=name, reason=reason).set(
                            _safe_int(ap.get(field, 0))
                        )
                    total_clients += ap_client_count

                    radios = ap.get("radio", [])
                    if isinstance(radios, dict):
                        radios = [radios]
                    for radio in radios:
                        if not isinstance(radio, dict):
                            continue
                        band = _radio_band(radio)
                        channel = str(radio.get("channel", "0"))

                        radio_clients.labels(ap_mac=mac, ap_name=name, radio_band=band, channel=channel).set(
                            _safe_int(radio.get("num-sta", 0))
                        )
                        _txp = _safe_float(radio.get("tx-power", 0))
                        radio_tx_power.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            -_txp if _txp != 0 else 0
                        )
                        radio_noise_floor.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("noisefloor", 0))
                        )
                        radio_phy_errors.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("phyerr", 0))
                        )
                        radio_channel_utilization.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("airtime-busy", 0)) / 10
                        )
                        radio_airtime_rx.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("airtime-rx", 0)) / 10
                        )
                        radio_airtime_tx.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("airtime-tx", 0)) / 10
                        )
                        radio_tx_bytes.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("total-tx-bytes", 0))
                        )
                        radio_rx_bytes.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("total-rx-bytes", 0))
                        )
                        radio_tx_retries.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("radio-total-retries", 0))
                        )
                        radio_tx_packets.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_int(radio.get("radio-total-tx-pkts", 0))
                        )
                        radio_tx_failures.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_int(radio.get("radio-total-tx-fail", 0))
                        )
                        radio_avg_rssi.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_float(radio.get("avg-rssi", 0))
                        )
                        radio_channel_width.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_int(radio.get("channelization", 0))
                        )
                        radio_assoc_failures.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_int(radio.get("mgmt-assoc-fail", 0))
                        )
                        radio_disassoc_abnormal.labels(ap_mac=mac, ap_name=name, radio_band=band).set(
                            _safe_int(radio.get("mgmt-disassoc-abnormal", 0))
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

                if DEBUG_DUMP and clients:
                    log.info("DEBUG clients[0]: %s", json.dumps(clients[0], default=str))

                for cl in clients:
                    cl_mac = cl.get("mac", "unknown")
                    cl_name = cl.get("hostname") or cl.get("user") or cl_mac
                    cl_ap = cl.get("ap", "unknown")
                    cl_ssid = cl.get("ssid") or cl.get("wlan", "unknown")
                    cl_band = _radio_band(cl)

                    labels = dict(client_mac=cl_mac, client_name=cl_name,
                                  ap_mac=cl_ap, ssid=cl_ssid, radio_band=cl_band)

                    rssi_val = _safe_float(cl.get("received-signal-strength", 0))
                    nf_val = _safe_float(cl.get("noise-floor", 0))
                    client_rssi.labels(**labels).set(rssi_val)
                    client_noise_floor.labels(**labels).set(nf_val)
                    if rssi_val != 0 and nf_val != 0:
                        client_snr.labels(**labels).set(rssi_val - nf_val)

            except Exception as e:
                log.error("Error collecting client stats: %s", e)

            # -----------------------------------------------------------
            # VAP / Per-SSID Stats
            # -----------------------------------------------------------
            try:
                vaps = await api.get_vap_stats()

                if DEBUG_DUMP and vaps:
                    log.info("DEBUG vaps[0]: %s", json.dumps(vaps[0], default=str))

                for vap in vaps:
                    v_ap = vap.get("ap", "unknown")
                    v_ssid = vap.get("ssid") or vap.get("wlan", "unknown")
                    v_bssid = vap.get("bssid", "unknown")
                    v_band = _radio_band(vap)

                    vap_clients.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band, bssid=v_bssid).set(
                        _safe_int(vap.get("num-sta", 0))
                    )
                    vap_tx_bytes.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("tx-bytes", 0))
                    )
                    vap_rx_bytes.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("rx-bytes", 0))
                    )
                    vap_tx_pkts.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("tx-pkts", 0))
                    )
                    vap_rx_pkts.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("rx-pkts", 0))
                    )
                    vap_tx_errors.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("tx-errors", 0))
                    )
                    vap_rx_errors.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_float(vap.get("rx-errors", 0))
                    )
                    vap_tx_drop_pkts.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_int(vap.get("tx-data-drop-pkts", 0))
                    )
                    vap_rx_drop_pkts.labels(ap_mac=v_ap, ssid=v_ssid, radio_band=v_band).set(
                        _safe_int(vap.get("rx-drop-pkt", 0))
                    )

            except Exception as e:
                log.error("Error collecting VAP stats: %s", e)

        scrape_success.set(1)

    except Exception as e:
        scrape_success.set(0)
        log.error("Scrape failed: %s", e, exc_info=True)

    duration = time.monotonic() - start
    scrape_duration.set(duration)
    log.info("Scrape complete in %.1fs — APs=%d clients=%d", duration, ap_count, client_count)

    return generate_latest(registry)


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

async def metrics_handler(request):
    async with _get_scrape_lock():
        output = await collect_metrics()
    return web.Response(body=output, headers={"Content-Type": CONTENT_TYPE_LATEST})


async def health_handler(request):
    return web.Response(text="ok")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    if not RUCKUS_HOST or not RUCKUS_USER or not RUCKUS_PASS:
        log.error("RUCKUS_HOST, RUCKUS_USER, and RUCKUS_PASS environment variables are required")
        sys.exit(1)

    log.info("Starting Ruckus Unleashed exporter")
    log.info("  Target: %s (user: %s)", RUCKUS_HOST, RUCKUS_USER)
    log.info("  Metrics port: %d", EXPORTER_PORT)

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
