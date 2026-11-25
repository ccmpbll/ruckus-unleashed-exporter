#!/usr/bin/env python3
"""Offline test for events_metrics (stdlib; the /metrics wiring test also needs requirements.txt, else it skips).

Usage: python3 test_events_metrics.py
"""
import asyncio
import os
import time

from events_metrics import CLIENT_TTL, EventCounters, active_alarms

NOW = 1_800_000_000
H = 3600


def ev(t, msg, **kw):
    return {"msg": msg, "time": str(t), "lmsg": f"{msg} {t}", **kw}


def test_active_alarms_recent_only():
    alarms = [
        {"name": "AP Lost Contact", "severity": "2", "time": str(NOW - H), "ap-name": "R850"},
        {"name": "Old", "severity": "1", "time": str(NOW - 30 * H)},
        {"name": "AP Lost Contact", "severity": "2", "time": str(NOW - 2 * H), "ap-name": "R850"},
    ]
    assert active_alarms(alarms, now=NOW) == {("AP Lost Contact", "2", "R850"): 1}
    assert active_alarms([{}], now=NOW) == {}


def test_first_fetch_is_baseline():
    c = EventCounters()
    c.update([ev(NOW - 100, "MSG_AP_rebooted"), ev(NOW - 50, "MSG_client_repeat_auth_fail", mac="aa")], now=NOW)
    assert c.events == {} and c.auth_failures == {}


def test_new_series_start_at_zero_then_count():
    c = EventCounters()
    base = [ev(NOW - 100, "MSG_AP_rebooted")]
    c.update(base, now=NOW)
    batch = base + [ev(NOW + 10, "MSG_AP_rebooted"), ev(NOW + 20, "MSG_AP_rebooted"),
                    ev(NOW + 30, "MSG_client_repeat_auth_fail", mac="aa")]
    c.update(batch, now=NOW + 60)
    assert c.events == {"MSG_AP_rebooted": 0, "MSG_client_repeat_auth_fail": 0}
    assert c.auth_failures == {"aa": 0}
    c.update(batch, now=NOW + 120)  # same batch again: nothing double-counted
    assert c.events == {"MSG_AP_rebooted": 2, "MSG_client_repeat_auth_fail": 1}
    assert c.auth_failures == {"aa": 1}
    c.update(batch + [ev(NOW + 130, "MSG_client_assoc_fail", mac="aa")], now=NOW + 180)
    assert c.auth_failures == {"aa": 2}  # existing series counts immediately
    assert c.events["MSG_client_assoc_fail"] == 0


def test_non_auth_events_with_mac_not_counted_as_failures():
    c = EventCounters()
    c.update([], now=NOW)
    c.update([ev(NOW + 1, "MSG_client_join", mac="bb")], now=NOW + 2)
    c.update([], now=NOW + 3)
    assert c.auth_failures == {} and c.events == {"MSG_client_join": 1}


def test_stale_clients_dropped():
    c = EventCounters()
    c.update([], now=NOW)
    c.update([ev(NOW, "MSG_client_repeat_auth_fail", mac="aa")], now=NOW)
    c.update([], now=NOW + 60)
    assert c.auth_failures == {"aa": 1}
    c.update([], now=NOW + CLIENT_TTL + 1)
    assert c.auth_failures == {}
    assert c.events == {"MSG_client_repeat_auth_fail": 1}  # per-code counter stays


def test_identical_events_in_one_batch_both_count():
    c = EventCounters()
    c.update([], now=NOW)
    same = [ev(NOW, "MSG_client_repeat_auth_fail", mac="aa")] * 2
    c.update(same, now=NOW)
    c.update(same, now=NOW + 60)  # same two again: no recount
    assert c.auth_failures == {"aa": 2}
    c.update(same + same[:1], now=NOW + 120)  # a third identical one arrives
    assert c.auth_failures == {"aa": 3}


def test_metrics_keep_event_counters_when_alarms_fail():
    """Wiring: needs aiohttp/aioruckus/prometheus_client (the Docker image has them)."""
    try:
        os.environ.setdefault("RUCKUS_HOST", "x")
        import ruckus_exporter as r
    except ImportError:
        print("skip test_metrics_keep_event_counters_when_alarms_fail (deps missing)")
        return
    t = int(time.time())
    batches = [[], [ev(t, "MSG_AP_rebooted")], [ev(t, "MSG_AP_rebooted")]]

    class Api:
        async def get_alarms(self, limit):
            raise RuntimeError("no alarms API")

        async def get_events(self, limit):
            return batches.pop(0)

        def __getattr__(self, name):
            async def empty(*a, **k):
                return {} if name == "get_system_info" else []
            return empty

    class Session:
        api = Api()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    real = r.AjaxSession.async_create
    r.AjaxSession.async_create = staticmethod(lambda *a, **k: Session())
    r._event_counters = EventCounters()
    try:
        for _ in range(3):
            out = asyncio.run(r.collect_metrics()).decode()
    finally:
        r.AjaxSession.async_create = real
    assert 'ruckus_events_total{code="MSG_AP_rebooted"} 1.0' in out, out
    assert "ruckus_alarm_active{" not in out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
