"""Reduce Unleashed alarms, events and rogue APs to a compact summary.

Stdlib only so it can be tested without aioruckus installed.
"""
import time
from collections import Counter

MAX_GROUPS = 20
MAX_OPEN_ROGUES = 5


def _num(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def summarize_events(alarms, events, rogues, *, now, hours, event_limit):
    """Return a JSON-serialisable summary of the last `hours` hours."""
    cutoff = now - hours * 3600

    recent_alarms = sorted(
        (a for a in alarms if _num(a.get("time")) >= cutoff),
        key=lambda a: -_num(a.get("time")),
    )
    alarm_out = [
        {
            "time": _iso(_num(a["time"])),
            "name": a.get("name"),
            "severity": _num(a.get("severity")),
            "ap": a.get("ap-name") or a.get("ap"),
            "message": (a.get("lmsg") or a.get("msg") or "")[:240],
            "occurs": _num(a.get("occurs"), 1),
        }
        for a in recent_alarms
    ]

    groups = {}
    in_window = [e for e in events if _num(e.get("time")) >= cutoff]
    for e in in_window:
        code = e.get("msg", "unknown")
        g = groups.setdefault(
            code,
            {"code": code, "count": 0, "severity": 0, "category": e.get("c"),
             "latest": 0, "example": "", "clients": Counter()},
        )
        t = _num(e.get("time"))
        g["count"] += 1
        g["severity"] = max(g["severity"], _num(e.get("severity")))
        if t >= g["latest"]:
            g["latest"] = t
            g["example"] = (e.get("lmsg") or "")[:240]
        if e.get("mac"):
            g["clients"][e["mac"]] += 1

    event_out = []
    for g in sorted(groups.values(), key=lambda g: -g["count"])[:MAX_GROUPS]:
        event_out.append({
            "code": g["code"],
            "count": g["count"],
            "severity": g["severity"],
            "category": g["category"],
            "latest": _iso(g["latest"]),
            "example": g["example"],
            "top_clients": [{"mac": m, "count": n} for m, n in g["clients"].most_common(3)],
        })

    # The API returns the newest `event_limit` events; if even the oldest one is
    # inside the window, older in-window events may be missing.
    oldest = min((_num(e.get("time")) for e in events), default=now)
    truncated = len(events) >= event_limit and oldest >= cutoff

    open_rogues = [r for r in rogues if str(r.get("is-open", "")).lower() == "open"]
    rogue_out = {
        "total": len(rogues),
        "seen_in_window": sum(1 for r in rogues if _num(r.get("last-seen")) >= cutoff),
        "open_networks": [
            {"ssid": r.get("ssid"), "channel": r.get("channel"), "last_seen": _iso(_num(r.get("last-seen")))}
            for r in sorted(open_rogues, key=lambda r: -_num(r.get("last-seen")))[:MAX_OPEN_ROGUES]
        ],
        "open_total": len(open_rogues),
    }

    return {
        "window_hours": hours,
        "generated_at": _iso(now),
        "alarms": alarm_out,
        "alarms_older_than_window": len(alarms) - len(alarm_out),
        "events": event_out,
        "events_in_window": len(in_window),
        "events_may_be_truncated": truncated,
        "rogues": rogue_out,
    }
