"""National sweep: Duo pickup stock across ~50 US metros, grouped into tiers.

Runs separately from check.py (the Kansas City bot) so it can never slow it down.
Each sweep asks Apple for the 12 stores nearest each metro's ZIP, assigns every
store to its closest metro, and logs each change between in stock and sold out.
"""
import os, json, csv, time, datetime, statistics, requests
from zoneinfo import ZoneInfo

PART = os.environ["PART"]
TOPIC = "aryak-duo-256-night-sky"
TZ = ZoneInfo("America/Chicago")
DATA_DIR = os.environ.get("DATA_DIR", ".")
OUT_DIR = os.path.join(DATA_DIR, "national")
STATE_FILE = os.path.join(OUT_DIR, "state.json")
LOG_FILE = os.path.join(OUT_DIR, "events.csv")

SPACING = 2.0          # seconds between requests to Apple
MAX_FAILS = 2          # consecutive failures before giving up on a sweep
BACKOFF_MIN = 30       # minutes to pause after Apple starts refusing
ASSIGN_MILES = 50      # a store belongs to a metro if it's this close to the metro's ZIP
WAVE_GAP_MIN = 45      # restocks closer together than this belong to the same wave

# Tiers by metro population (approximate): T1 >= 4.5M, T2 1.5-4.5M, T3 < 1.5M
METROS = [
    # T1
    ("New York", "10019", 1), ("Los Angeles", "90067", 1), ("Chicago", "60611", 1),
    ("Dallas", "75225", 1), ("Houston", "77056", 1), ("Washington", "20001", 1),
    ("Philadelphia", "19103", 1), ("Atlanta", "30326", 1), ("Miami", "33131", 1),
    ("Phoenix", "85016", 1), ("Boston", "02116", 1), ("San Francisco", "94108", 1),
    # T2
    ("Detroit", "48084", 2), ("Seattle", "98004", 2), ("Minneapolis", "55425", 2),
    ("San Diego", "92108", 2), ("Tampa", "33607", 2), ("Denver", "80206", 2),
    ("Baltimore", "21204", 2), ("St. Louis", "63117", 2), ("Orlando", "32819", 2),
    ("Charlotte", "28211", 2), ("San Antonio", "78216", 2), ("Portland", "97225", 2),
    ("Sacramento", "95815", 2), ("Pittsburgh", "15237", 2), ("Austin", "78746", 2),
    ("Las Vegas", "89109", 2), ("Cincinnati", "45236", 2), ("Kansas City", "66217", 2),
    ("Columbus", "43219", 2), ("Indianapolis", "46240", 2), ("Cleveland", "44122", 2),
    ("Nashville", "37215", 2), ("Raleigh", "27612", 2),
    # T3
    ("Oklahoma City", "73118", 3), ("Tulsa", "74133", 3), ("Omaha", "68118", 3),
    ("Des Moines", "50266", 3), ("Little Rock", "72223", 3), ("Memphis", "38138", 3),
    ("Madison", "53705", 3), ("Albuquerque", "87110", 3), ("Salt Lake City", "84111", 3),
    ("Louisville", "40222", 3), ("Birmingham", "35243", 3), ("Richmond", "23233", 3),
    ("Honolulu", "96814", 3), ("Tucson", "85718", 3), ("Buffalo", "14225", 3),
]

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                  "Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone",
}


def now():
    return datetime.datetime.now(TZ)


def push(title, msg, priority="low", click="https://github.com"):
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode(),
                      headers={"Title": title, "Priority": priority, "Click": click}, timeout=15)
    except Exception as e:
        print(f"ntfy error: {e}")


def parse_time(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def read_log():
    try:
        with open(LOG_FILE, newline="") as f:
            return [r for r in csv.DictReader(f) if r.get("time")]
    except FileNotFoundError:
        return []


FIELDS = ["time", "store", "store_id", "city", "state", "metro", "tier", "miles", "status"]


def log_events(rows):
    if not rows:
        return
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if new_file:
            w.writeheader()
        w.writerows(rows)


# ---------- analysis (shared by the weekly summary; the page does the same in JS) ----------
def analyse(rows, since=None):
    rows = [r for r in rows if r["tier"] in ("1", "2", "3")
            and (since is None or parse_time(r["time"]) >= since)]
    # durations: first "available" to the next "unavailable" per store
    opened, spans = {}, []
    for r in rows:
        if r["status"] == "available":
            opened.setdefault(r["store_id"], r)
        elif r["status"] == "unavailable" and r["store_id"] in opened:
            start = opened.pop(r["store_id"])
            mins = max(1, round((parse_time(r["time"]) - parse_time(start["time"])).total_seconds() / 60))
            spans.append((start["tier"], mins))
    restocks = [r for r in rows if r["status"] == "available"]
    # waves: restocks within WAVE_GAP_MIN of the previous one
    waves, cur = [], []
    for r in restocks:
        t = parse_time(r["time"])
        if cur and (t - parse_time(cur[-1]["time"])).total_seconds() / 60 > WAVE_GAP_MIN:
            waves.append(cur); cur = []
        cur.append(r)
    if cur:
        waves.append(cur)
    lead = {"1": [], "2": [], "3": []}   # minutes after the wave's first restock
    for w in waves:
        if len(w) < 3:
            continue
        first = parse_time(w[0]["time"])
        seen = set()
        for r in w:
            if r["tier"] not in seen:
                seen.add(r["tier"])
                lead[r["tier"]].append((parse_time(r["time"]) - first).total_seconds() / 60)
    hours = {"1": [0] * 24, "2": [0] * 24, "3": [0] * 24}
    for r in restocks:
        hours[r["tier"]][parse_time(r["time"]).hour] += 1
    return {
        "restocks": {t: sum(1 for r in restocks if r["tier"] == t) for t in "123"},
        "durations": {t: [m for tt, m in spans if tt == t] for t in "123"},
        "lead": lead, "hours": hours, "waves": len([w for w in waves if len(w) >= 3]),
    }


def weekly_summary(rows, at):
    a = analyse(rows, since=at - datetime.timedelta(days=7))
    total = sum(a["restocks"].values())
    if not total:
        return "No Duo restocks seen across the 50 metros this week."
    parts = [f"{total} restocks across 50 metros this week "
             f"(T1 {a['restocks']['1']}, T2 {a['restocks']['2']}, T3 {a['restocks']['3']}), "
             f"{a['waves']} wave{'s' if a['waves'] != 1 else ''}."]
    leads = {t: statistics.median(v) for t, v in a["lead"].items() if v}
    if len(leads) >= 2:
        order = sorted(leads, key=leads.get)
        parts.append("Usually first: " + ", then ".join(
            f"T{t}" + (f" (+{round(leads[t])} min)" if leads[t] >= 1 else "") for t in order) + ".")
    durs = {t: statistics.median(v) for t, v in a["durations"].items() if v}
    if durs:
        parts.append("Stock lasts about " + ", ".join(f"T{t} {round(m)} min" for t, m in sorted(durs.items())) + ".")
    all_hours = [sum(a["hours"][t][h] for t in "123") for h in range(24)]
    peak = max(range(24), key=lambda h: all_hours[h])
    parts.append(f"Busiest hour: {peak % 12 or 12}{'am' if peak < 12 else 'pm'} Central.")
    kc = [r for r in rows if r["metro"] == "Kansas City" and r["status"] == "available"
          and parse_time(r["time"]) >= at - datetime.timedelta(days=7)]
    parts.append(f"Kansas City: {len(kc)} restock{'s' if len(kc) != 1 else ''}.")
    return " ".join(parts)


# ---------- sweep ----------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("stores", {})

    t = now()
    night = t.hour >= 22 or t.hour < 7
    if night and t.minute % 30 >= 10:
        print("Night: sweeping every 30 min, skipping this one")
        return
    backoff = state.get("backoff_until")
    if backoff and t < datetime.datetime.fromisoformat(backoff):
        print(f"Backing off until {backoff}")
        return

    best = {}   # store_id -> (miles, metro, tier, store dict)
    fails, ok, sweep_start = 0, 0, time.monotonic()
    for i, (metro, zip_code, tier) in enumerate(METROS):
        if i:
            time.sleep(SPACING)
        try:
            r = requests.get("https://www.apple.com/shop/retail/pickup-message",
                             headers=headers, timeout=20, params={"parts.0": PART, "location": zip_code})
            stores = r.json()["body"]["stores"]
            fails, ok = 0, ok + 1
        except Exception as e:
            fails += 1
            print(f"{metro}: failed ({e})")
            if fails >= MAX_FAILS:
                state["backoff_until"] = (now() + datetime.timedelta(minutes=BACKOFF_MIN)).isoformat()
                state["last_error"] = f"{now().isoformat(timespec='minutes')}: stopped after {metro}"
                print("Apple is refusing requests, backing off")
                break
            continue
        for st in stores:
            sid = st.get("storeNumber") or st["storeName"]
            miles = float(st.get("storedistance", 999))
            if sid not in best or miles < best[sid][0]:
                best[sid] = (miles, metro, tier, st)

    stamp = now().strftime("%Y-%m-%d %H:%M")
    events = []
    for sid, (miles, metro, tier, st) in best.items():
        info = st.get("partsAvailability", {}).get(PART, {})
        status = info.get("pickupDisplay", "unknown")
        assigned = miles <= ASSIGN_MILES
        rec = {
            "name": st["storeName"], "city": st.get("city", ""), "state": st.get("state", ""),
            "lat": st.get("storelatitude"), "lon": st.get("storelongitude"),
            "metro": metro if assigned else "", "tier": tier if assigned else 0,
            "miles": round(miles, 1), "status": status,
        }
        prev = state["stores"].get(sid, {}).get("status")
        if prev is not None and prev != status and status in ("available", "unavailable"):
            events.append({"time": stamp, "store": rec["name"], "store_id": sid, "city": rec["city"],
                           "state": rec["state"], "metro": rec["metro"], "tier": rec["tier"],
                           "miles": rec["miles"], "status": status})
        if status == "available" and prev != "available":
            rec["since"] = stamp
        elif status == "available":
            rec["since"] = state["stores"].get(sid, {}).get("since", stamp)
        state["stores"][sid] = rec

    log_events(events)
    state["last_sweep"] = now().isoformat()
    state["sweep_seconds"] = round(time.monotonic() - sweep_start)
    state["metros_ok"] = ok
    state["metros"] = [{"metro": m, "zip": z, "tier": tr} for m, z, tr in METROS]
    if ok == len(METROS):
        state.pop("backoff_until", None)
    print(f"Swept {ok}/{len(METROS)} metros, {len(best)} stores, {len(events)} changes "
          f"in {state['sweep_seconds']} s")

    # Weekly summary: Sunday after 6pm Central, once per week
    t = now()
    week = t.strftime("%G-W%V")
    if t.weekday() == 6 and t.hour >= 18 and state.get("last_weekly") != week:
        push("Duo restocks: week in review", weekly_summary(read_log(), t),
             priority="default", click="https://aryakvsathye.github.io/duo-tracker/national.html")
        state["last_weekly"] = week

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


if __name__ == "__main__":
    main()
