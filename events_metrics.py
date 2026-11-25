"""Turn Unleashed alarms and events into Prometheus-style series.

Stdlib only so it can be tested without aioruckus or prometheus_client.

The controller only hands back its newest N events, so counters are kept in
this process: every event is counted once (deduplicated by its fields), and
the first fetch after start-up is a baseline that marks history as seen
without counting it, so an exporter restart does not look like a burst of AP
reboots.

A brand-new series is first exposed at 0 and its count lands on the next
fetch. Prometheus' increase() cannot see the jump from "no series" to 1, so
without this the first event of every new code or client would be lost.
"""
import re

ALARM_ACTIVE_HOURS = 24
CLIENT_TTL = 7 * 86400
_AUTH_FAIL = re.compile(r"(auth|assoc)\w*_?fail", re.I)


def _num(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def active_alarms(alarms, *, now, hours=ALARM_ACTIVE_HOURS):
    """{(code, severity, ap): 1} for alarms raised in the last `hours`.

    Unleashed alarms carry no cleared state, so "active" means recent.
    """
    cutoff = now - hours * 3600
    out = {}
    for a in alarms:
        if _num(a.get("time")) < cutoff:
            continue
        key = (str(a.get("name") or "unknown"), str(_num(a.get("severity"))),
               str(a.get("ap-name") or a.get("ap") or ""))
        out[key] = 1
    return out


def _key(e):
    return (e.get("time"), e.get("msg"), e.get("mac"), e.get("ap"), e.get("lmsg"))


class EventCounters:
    def __init__(self):
        self.events = {}         # code -> count
        self.auth_failures = {}  # mac -> count
        self._client_seen = {}   # mac -> last event time
        self._pending_events = {}
        self._pending_auth = {}
        self._seen = set()
        self._baselined = False

    @staticmethod
    def _add(counts, pending, label, n=1):
        if label in pending or label not in counts:
            counts.setdefault(label, 0)
            pending[label] = pending.get(label, 0) + n
        else:
            counts[label] += n

    def update(self, events, *, now):
        # Last fetch's new series get their real value now.
        for counts, pending in ((self.events, self._pending_events), (self.auth_failures, self._pending_auth)):
            for label, n in pending.items():
                counts[label] += n
            pending.clear()

        keys = {_key(e) for e in events}
        if self._baselined:
            for e in events:
                if _key(e) in self._seen:
                    continue
                code = str(e.get("msg") or "unknown")
                self._add(self.events, self._pending_events, code)
                mac = e.get("mac")
                if mac and _AUTH_FAIL.search(code):
                    self._add(self.auth_failures, self._pending_auth, mac)
                    self._client_seen[mac] = max(self._client_seen.get(mac, 0), _num(e.get("time"), now))
        self._baselined = True
        # Only keys still in the controller's window can come back.
        self._seen = keys

        for mac, seen in list(self._client_seen.items()):
            if now - seen > CLIENT_TTL:
                del self._client_seen[mac]
                self.auth_failures.pop(mac, None)
                self._pending_auth.pop(mac, None)
