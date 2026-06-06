#!/usr/bin/env python3
"""
Quick test: call get_active_clients(interval_stats=True) and print
the first client object so we can see if any traffic fields appear.

Usage (inside the container):
  python3 test_interval_stats.py

Usage (on host, if deps are installed):
  RUCKUS_HOST=x RUCKUS_USER=x RUCKUS_PASS=x python3 test_interval_stats.py
"""
import asyncio
import json
import os

from aioruckus import AjaxSession

HOST = os.environ["RUCKUS_HOST"]
USER = os.environ["RUCKUS_USER"]
PASS = os.environ["RUCKUS_PASS"]


async def main():
    async with AjaxSession.async_create(HOST, USER, PASS) as session:
        clients = await session.api.get_active_clients(interval_stats=True)

    print(f"Total clients: {len(clients)}")
    print()

    if not clients:
        print("No clients connected.")
        return

    # Print first client — all keys visible
    first = clients[0]
    print("=== First client (all fields) ===")
    print(json.dumps(first, indent=2, default=str))

    # Summarise which new keys appear across ALL clients
    all_keys = set()
    for cl in clients:
        all_keys.update(cl.keys())

    baseline = {
        "mac", "vap-mac", "vap-nasid", "wlan-id", "ap-name", "status",
        "ext-status", "first-assoc", "vlan", "called-station-id-type", "ssid",
        "favourite", "blocked", "iot", "wlan", "role-id", "channel",
        "description", "dvcinfo-group", "channelization", "ieee80211-radio-type",
        "radio-type-text", "radio-band", "rssi", "received-signal-strength",
        "noise-floor", "num-interval-stats", "location", "auth-method",
        "acct-multi-session-id", "acct-session-id", "ap", "dpsk-id", "user",
        "ip", "ipv6", "dvcinfo", "dvctype", "model", "hostname", "oldname",
        "radio-type", "rssi-level", "health-level", "display-health-level",
        "group-id", "encryption", "wpa-passphrase-len",
    }

    new_keys = all_keys - baseline
    print()
    if new_keys:
        print(f"=== NEW keys vs LEVEL='1' baseline ({len(new_keys)} found) ===")
        for k in sorted(new_keys):
            # Show a sample value
            sample = next((cl[k] for cl in clients if k in cl), None)
            print(f"  {k!r}: {sample!r}")
    else:
        print("=== No new keys vs baseline — interval_stats=True adds nothing ===")

    # Check num-interval-stats values
    nis_values = set(cl.get("num-interval-stats", "missing") for cl in clients)
    print(f"\nnum-interval-stats values seen: {nis_values}")


asyncio.run(main())
