#!/usr/bin/env python3
"""
Fetch 2026 World Cup data from TheSportsDB and merge into data/wc.json.

Runs on a schedule (GitHub Action) from ONE place so the free API key is hit only
a handful of times per hour, never from viewers' browsers.

Why fetch BY ROUND: the free key caps eventsday at ~3 results and eventsseason at
~15 when throttled, which left gaps. eventsround returns the FULL matchday (24
matches per group round), so 3 calls get the entire 72-match group stage cleanly.
Knockout rounds are fetched by their round codes (harmless if not yet populated),
with eventsseason as a catch-all backup.

Resilience:
- MERGE every response into a map keyed by idEvent — a partial/throttled response
  can never drop matches we've already captured.
- A finished result is never overwritten by a later null/NS version.
- data/wc.json therefore only grows or updates; it never regresses.
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

API_KEY   = os.environ.get("TSDB_KEY", "123")
LEAGUE_ID = "4429"           # FIFA World Cup
SEASON    = "2026"
OUT       = os.path.join(os.path.dirname(__file__), "..", "data", "wc.json")
BASE      = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"

GROUP_ROUNDS = [1, 2, 3]
# TheSportsDB codes KO rounds INCONSISTENTLY (R32=32, R16=16, QF=125, …), so guessing codes is
# unreliable. These are the ones seen so far; the KO_DAYS date-sweep below is the real safety net —
# it captures knockout matches whatever their round code, since KO days have very few matches.
KO_ROUNDS = [32, 16, 8, 4, 125, 150, 160, 170, 180]
# Sweep the knockout window day-by-day (few matches/day → no per-day result cap issue).
KO_DAYS = ("2026-07-01", "2026-07-20")


def get(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wc-sweepstake-updater"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries - 1:
                time.sleep(3 * (attempt + 1))  # back off on rate limit
                continue
            print(f"  ! fetch failed: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  ! fetch failed: {e}", file=sys.stderr)
            return None


def finished(ev):
    return ev.get("intHomeScore") not in (None, "") and ev.get("intAwayScore") not in (None, "")


def merge(into, events):
    added = 0
    for ev in events or []:
        eid = ev.get("idEvent")
        if not eid:
            continue
        old = into.get(eid)
        if old and finished(old) and not finished(ev):
            continue  # don't let a null/NS response clobber a result we already have
        if eid not in into:
            added += 1
        into[eid] = ev
    return added


def main():
    events = {}
    if os.path.exists(OUT):
        try:
            for ev in json.load(open(OUT)).get("events", []):
                if ev.get("idEvent"):
                    events[ev["idEvent"]] = ev
        except Exception as e:
            print(f"  ! could not read existing {OUT}: {e}", file=sys.stderr)
    print(f"start: {len(events)} events in store")

    # bulk season fetch (catch-all)
    d = get(f"{BASE}/eventsseason.php?id={LEAGUE_ID}&s={SEASON}")
    if d:
        print(f"  season: +{merge(events, d.get('events'))} new ({len(d.get('events') or [])} returned)")
    time.sleep(0.6)

    # by-round: full matchdays for group stage + knockout rounds
    for r in GROUP_ROUNDS + KO_ROUNDS:
        rr = get(f"{BASE}/eventsround.php?id={LEAGUE_ID}&r={r}&s={SEASON}")
        n_ret = len(rr.get("events") or []) if rr else 0
        n_new = merge(events, rr.get("events")) if rr else 0
        if n_ret:
            print(f"  round {r}: +{n_new} new ({n_ret} returned)")
        time.sleep(0.5)

    # knockout day-sweep: catches QF/SF/Final/3rd whatever their round code
    d0 = datetime.strptime(KO_DAYS[0], "%Y-%m-%d").date()
    d1 = datetime.strptime(KO_DAYS[1], "%Y-%m-%d").date()
    day = d0
    while day <= d1:
        dd = get(f"{BASE}/eventsday.php?d={day.isoformat()}&l={LEAGUE_ID}")
        n_new = merge(events, dd.get("events")) if dd else 0
        if n_new:
            print(f"  {day.isoformat()}: +{n_new} new")
        day += timedelta(days=1)
        time.sleep(0.4)

    out_events = sorted(events.values(), key=lambda e: (e.get("strTimestamp") or e.get("dateEvent") or ""))
    payload = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "count": len(out_events), "events": out_events}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w"), ensure_ascii=False, indent=0)
    fin = sum(1 for e in out_events if finished(e))
    print(f"done: {len(out_events)} events ({fin} finished) -> {OUT}")


if __name__ == "__main__":
    main()
