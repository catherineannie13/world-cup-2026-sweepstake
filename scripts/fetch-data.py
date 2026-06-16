#!/usr/bin/env python3
"""
Fetch 2026 World Cup data from TheSportsDB and merge into data/wc.json.

Designed to run on a schedule (GitHub Action) from ONE place, so the free API key
is hit only a handful of times per hour instead of from every viewer's browser.

Resilience rules:
- We MERGE every response into a map keyed by idEvent — partial/throttled responses
  can never drop matches we've already captured.
- A finished result (has scores) is never overwritten by a later null/NS version,
  so a glitchy response can't blank out a score.
- data/wc.json therefore only ever grows or updates; it never regresses.
"""
import json, os, sys, time, urllib.request
from datetime import date, datetime, timedelta, timezone

API_KEY   = os.environ.get("TSDB_KEY", "123")
LEAGUE_ID = "4429"           # FIFA World Cup
SEASON    = "2026"
OUT       = os.path.join(os.path.dirname(__file__), "..", "data", "wc.json")
BASE      = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"

# Date window to sweep day-by-day (catches both recent results and upcoming fixtures).
# Defaults to a rolling window; override with WINDOW_START / WINDOW_END for a full seed.
START = os.environ.get("WINDOW_START")
END   = os.environ.get("WINDOW_END")


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
        # don't let a null/NS response clobber a result we already have
        if old and finished(old) and not finished(ev):
            continue
        if eid not in into:
            added += 1
        into[eid] = ev
    return added


def daterange(s, e):
    d0 = datetime.strptime(s, "%Y-%m-%d").date()
    d1 = datetime.strptime(e, "%Y-%m-%d").date()
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def main():
    # load existing
    events = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
            for ev in prev.get("events", []):
                if ev.get("idEvent"):
                    events[ev["idEvent"]] = ev
        except Exception as e:
            print(f"  ! could not read existing {OUT}: {e}", file=sys.stderr)
    print(f"start: {len(events)} events in store")

    # 1) bulk season fetch
    d = get(f"{BASE}/eventsseason.php?id={LEAGUE_ID}&s={SEASON}")
    if d:
        print(f"  season: +{merge(events, d.get('events'))} new")
    time.sleep(1)

    # 2) day-by-day sweep (rolling window, or full range when seeding)
    today = datetime.now(timezone.utc).date()
    start = START or (today - timedelta(days=2)).isoformat()
    end   = END   or (today + timedelta(days=10)).isoformat()
    for day in daterange(start, end):
        dd = get(f"{BASE}/eventsday.php?d={day}&l={LEAGUE_ID}")
        n = merge(events, dd.get("events")) if dd else 0
        if n:
            print(f"  {day}: +{n} new")
        time.sleep(0.4)  # be polite to the free API

    # write out, sorted by kickoff
    out_events = sorted(events.values(), key=lambda e: (e.get("strTimestamp") or e.get("dateEvent") or ""))
    payload = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "count": len(out_events), "events": out_events}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w"), ensure_ascii=False, indent=0)
    fin = sum(1 for e in out_events if finished(e))
    print(f"done: {len(out_events)} events ({fin} finished) -> {OUT}")


if __name__ == "__main__":
    main()
