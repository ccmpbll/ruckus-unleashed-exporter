#!/usr/bin/env python3
"""Offline test for events_summary.summarize_events (stdlib only).

Records mirror the shapes returned by aioruckus get_alarms / get_events /
get_active_rogues on an Unleashed R850.

Usage: python3 test_events_summary.py
"""
from events_summary import summarize_events

NOW = 1_800_000_000
H = 3600


def alarm(age_h, name, **kw):
    return {"name": name, "severity": "1", "time": str(NOW - int(age_h * H)),
            "lmsg": f"{name} happened", "occurs": "1", **kw}


def event(age_h, msg, **kw):
    return {"msg": msg, "severity": "2", "time": str(NOW - int(age_h * H)), "c": "user",
            "lmsg": f"{msg} text", **kw}


def run(alarms, events, rogues, hours=24, limit=300):
    return summarize_events(alarms, events, rogues, now=NOW, hours=hours, event_limit=limit)


def test_alarms_filtered_to_window_and_counted():
    out = run([alarm(2, "AP Has Joined", **{"ap-name": "R850"}), alarm(24 * 6, "ICX AV Profile Model Update Failed")], [], [])
    assert [a["name"] for a in out["alarms"]] == ["AP Has Joined"]
    assert out["alarms"][0]["ap"] == "R850"
    assert out["alarms_older_than_window"] == 1


def test_events_grouped_with_top_clients():
    evs = [event(1, "MSG_client_repeat_auth_fail", mac="aa:aa"), event(2, "MSG_client_repeat_auth_fail", mac="aa:aa"),
           event(3, "MSG_client_repeat_auth_fail", mac="bb:bb"), event(4, "MSG_ad_hoc_network_detected", severity="3"),
           event(30, "MSG_client_repeat_auth_fail", mac="cc:cc")]  # outside window
    out = run([], evs, [])
    assert out["events_in_window"] == 4
    top = out["events"][0]
    assert top["code"] == "MSG_client_repeat_auth_fail" and top["count"] == 3
    assert top["top_clients"][0] == {"mac": "aa:aa", "count": 2}
    assert all(c["mac"] != "cc:cc" for c in top["top_clients"])
    adhoc = next(e for e in out["events"] if e["code"] == "MSG_ad_hoc_network_detected")
    assert adhoc["severity"] == 3


def test_truncation_flag():
    evs = [event(0.1 * i, "X") for i in range(1, 11)]
    assert run([], evs, [], limit=10)["events_may_be_truncated"] is True    # oldest still inside window
    assert run([], evs, [], limit=300)["events_may_be_truncated"] is False  # fewer than the limit
    evs.append(event(40, "Y"))
    assert run([], evs, [], limit=11)["events_may_be_truncated"] is False   # window fully covered


def test_rogue_summary():
    rogues = [
        {"mac": "1", "ssid": "DIRECT-10-AndroidAP", "is-open": "Encrypted", "last-seen": str(NOW - H), "channel": "36"},
        {"mac": "2", "ssid": "FreeWifi", "is-open": "Open", "last-seen": str(NOW - 2 * H), "channel": "6"},
        {"mac": "3", "ssid": "Old", "is-open": "Encrypted", "last-seen": str(NOW - 200 * H), "channel": "1"},
    ]
    out = run([], [], rogues)["rogues"]
    assert out["total"] == 3 and out["seen_in_window"] == 2 and out["open_total"] == 1
    assert out["open_networks"][0]["ssid"] == "FreeWifi"


def test_empty_and_malformed_input():
    out = run([], [], [])
    assert out["alarms"] == [] and out["events"] == [] and out["rogues"]["total"] == 0
    out = run([{"name": "x"}], [{"msg": "y"}, {}], [{}])  # missing time/severity fields must not raise
    assert out["alarms"] == [] and out["events"] == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
