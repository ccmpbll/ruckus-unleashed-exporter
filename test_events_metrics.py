#!/usr/bin/env python3
"""Offline test for events_metrics (stdlib only).

Usage: python3 test_events_metrics.py
"""
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
