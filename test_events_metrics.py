#!/usr/bin/env python3
"""Offline test for events_metrics (stdlib; the /metrics wiring test also needs requirements.txt, else it skips).

Usage: python3 test_events_metrics.py
"""
import asyncio
import os
import time

from events_metrics import CLIENT_TTL, PENDING_SECONDS as P, EventCounters, active_alarms

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


def scrape(c, events, now, limit=300):
    """What one /metrics call does."""
    c.settle(now=now)
    c.update(events, now=now, limit=limit)


def test_first_fetch_is_baseline():
    c = EventCounters()
    scrape(c, [ev(NOW - 100, "MSG_AP_rebooted"), ev(NOW - 50, "MSG_client_repeat_auth_fail", mac="aa")], NOW)
    assert c.events == {} and c.auth_failures == {}


def test_new_series_held_at_zero_then_count():
    c = EventCounters()
    base = [ev(NOW - 100, "MSG_AP_rebooted")]
    scrape(c, base, NOW)
    batch = base + [ev(NOW + 10, "MSG_AP_rebooted"), ev(NOW + 20, "MSG_AP_rebooted"),
                    ev(NOW + 30, "MSG_client_repeat_auth_fail", mac="aa")]
    scrape(c, batch, NOW + 60)
    assert c.events == {"MSG_AP_rebooted": 0, "MSG_client_repeat_auth_fail": 0}
    assert c.auth_failures == {"aa": 0}
    # More calls inside the hold (another reader, a retried scrape): still 0.
    scrape(c, batch, NOW + 61)
    scrape(c, batch, NOW + 60 + P - 1)
    assert c.events["MSG_AP_rebooted"] == 0 and c.auth_failures == {"aa": 0}
    scrape(c, batch, NOW + 60 + P)  # same batch again: nothing double-counted
    assert c.events == {"MSG_AP_rebooted": 2, "MSG_client_repeat_auth_fail": 1}
    assert c.auth_failures == {"aa": 1}
    scrape(c, batch + [ev(NOW + 130, "MSG_client_assoc_fail", mac="aa")], NOW + 300)
    assert c.auth_failures == {"aa": 2}  # existing series counts immediately
    assert c.events["MSG_client_assoc_fail"] == 0


def test_held_count_lands_even_when_event_fetch_fails():
    c = EventCounters()
    scrape(c, [], NOW)
    scrape(c, [ev(NOW, "MSG_AP_rebooted")], NOW)
    c.settle(now=NOW + P)  # fetch failed: only settle runs
    assert c.events == {"MSG_AP_rebooted": 1}


def test_empty_or_short_batch_does_not_cause_recount():
    c = EventCounters()
    full = [ev(NOW - i, "MSG_AP_rebooted") for i in range(10)]
    scrape(c, full, NOW)  # baseline
    scrape(c, [], NOW + 60)  # controller hiccup
    scrape(c, full[:3], NOW + 120)  # short answer
    scrape(c, full, NOW + 180)
    c.settle(now=NOW + 1000)
    assert c.events == {}


def test_full_batch_slides_window():
    c = EventCounters()
    old = [ev(NOW - 10 + i, "MSG_AP_rebooted") for i in range(5)]
    scrape(c, old, NOW, limit=5)
    slid = old[2:] + [ev(NOW + 1, "MSG_AP_rebooted"), ev(NOW + 2, "MSG_AP_rebooted")]
    scrape(c, slid, NOW + 60, limit=5)
    assert len(c._seen) == 5  # the two oldest dropped
    scrape(c, slid, NOW + 60 + P, limit=5)
    assert c.events == {"MSG_AP_rebooted": 2}


def test_non_auth_events_with_mac_not_counted_as_failures():
    c = EventCounters()
    scrape(c, [], NOW)
    scrape(c, [ev(NOW + 1, "MSG_client_join", mac="bb")], NOW + 2)
    c.settle(now=NOW + 2 + P)
    assert c.auth_failures == {} and c.events == {"MSG_client_join": 1}


def test_stale_clients_dropped():
    c = EventCounters()
    scrape(c, [], NOW)
    scrape(c, [ev(NOW, "MSG_client_repeat_auth_fail", mac="aa")], NOW)
    c.settle(now=NOW + P)
    assert c.auth_failures == {"aa": 1}
    c.settle(now=NOW + CLIENT_TTL + 1)
    assert c.auth_failures == {}
    assert c.events == {"MSG_client_repeat_auth_fail": 1}  # per-code counter stays


def test_identical_events_in_one_batch_both_count():
    c = EventCounters()
    scrape(c, [], NOW)
    same = [ev(NOW, "MSG_client_repeat_auth_fail", mac="aa")] * 2
    scrape(c, same, NOW)
    scrape(c, same, NOW + P)  # same two again: no recount
    assert c.auth_failures == {"aa": 2}
    scrape(c, same + same[:1], NOW + P + 60)  # a third identical one arrives
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

    clock = [t]
    real_time = r.time.time
    r.time.time = lambda: clock[0]
    real = r.AjaxSession.async_create
    r.AjaxSession.async_create = staticmethod(lambda *a, **k: Session())
    r._event_counters = EventCounters()
    try:
        for _ in range(3):
            out = asyncio.run(r.collect_metrics()).decode()
            clock[0] += P
    finally:
        r.AjaxSession.async_create = real
        r.time.time = real_time
    assert 'ruckus_events_total{code="MSG_AP_rebooted"} 1.0' in out, out
    assert "ruckus_alarm_active{" not in out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
