"""Turn Unleashed alarms and events into Prometheus-style series.

Stdlib only so it can be tested without aioruckus or prometheus_client.

The controller only hands back its newest N events, so counters are kept in
this process: every event is counted once (deduplicated by its fields), and
the first fetch after start-up is a baseline that marks history as seen
without counting it, so an exporter restart does not look like a burst of AP
reboots.

A brand-new series is first exposed at 0 and its count lands PENDING_SECONDS
later. Prometheus' increase() cannot see the jump from "no series" to 1, so
without this the first event of every new code or client would be lost.
"""
import re
from collections import Counter

ALARM_ACTIVE_HOURS = 24
CLIENT_TTL = 7 * 86400
PENDING_SECONDS = 150  # 2.5 scrape intervals at the README's 60s
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
        self._pending_events = {}  # label -> [count, first seen]
        self._pending_auth = {}
        self._seen = Counter()
        self._baselined = False

    @staticmethod
    def _add(counts, pending, label, now):
        if label in pending:
            pending[label][0] += 1
        elif label not in counts:
            counts[label] = 0
            pending[label] = [1, now]
        else:
            counts[label] += 1

    def settle(self, *, now):
        """Land held counts and drop stale clients. Call on every scrape.

        A new series is held at 0 for PENDING_SECONDS, not one call, so the 0
        survives a failed scrape or someone else reading /metrics.
        """
        for counts, pending in ((self.events, self._pending_events), (self.auth_failures, self._pending_auth)):
            for label, (n, since) in list(pending.items()):
                if now - since >= PENDING_SECONDS:
                    counts[label] += n
                    del pending[label]
        for mac, seen in list(self._client_seen.items()):
            if now - seen > CLIENT_TTL:
                del self._client_seen[mac]
                self.auth_failures.pop(mac, None)
                self._pending_auth.pop(mac, None)

    def update(self, events, *, now, limit):
        # Counter, not set: two identical events in one second are two events.
        keys = Counter(_key(e) for e in events)
        if self._baselined:
            room = self._seen.copy()
            for e in events:
                k = _key(e)
                if room[k] > 0:
                    room[k] -= 1
                    continue
                code = str(e.get("msg") or "unknown")
                self._add(self.events, self._pending_events, code, now)
                mac = e.get("mac")
                if mac and _AUTH_FAIL.search(code):
                    self._add(self.auth_failures, self._pending_auth, mac, now)
                    self._client_seen[mac] = max(self._client_seen.get(mac, 0), _num(e.get("time"), now))
        self._baselined = True
        # Merge, do not replace: an empty or short batch must not make the
        # next full one look new. Only a full batch (`limit` rows) proves the
        # window slid, so only then drop keys older than its oldest event.
        # Keys older than CLIENT_TTL go anyway, so the set stays bounded.
        floor = now - CLIENT_TTL
        if events and len(events) >= limit:
            floor = max(floor, min(_num(k[0]) for k in keys))
        merged = Counter({k: n for k, n in self._seen.items() if _num(k[0]) >= floor})
        merged |= keys  # per key, the larger count
        self._seen = merged
