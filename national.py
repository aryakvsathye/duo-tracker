"""National tracker: Duo pickup stock across ~50 US metros, grouped into tiers.

Runs separately from check.py (the Kansas City bot) so it can never slow it down.
Each request asks Apple for the 12 stores nearest one metro's ZIP. Instead of a
sweep every 5 minutes, each run checks continuously at a steady one request every
3 seconds: every metro comes round about every 2.5 minutes, and metros in a
Census division with a live restock wave every minute. Each store is assigned to
its closest metro, and every change between in stock and sold out is logged.
"""
import os, json, csv, time, datetime, statistics, subprocess, requests
from zoneinfo import ZoneInfo

PART = os.environ["PART"]          # main colour (Night Sky)
# Every colour tracked; Apple answers for all of them in one request per metro
COLOURS = {"MK1F4LL/A": "Night Sky", "MK1E4LL/A": "Star White"}
PARTS = [PART] + [p for p in COLOURS if p != PART]
TOPIC = "aryak-duo-256-night-sky"
TZ = ZoneInfo("America/Chicago")
DATA_DIR = os.environ.get("DATA_DIR", ".")
OUT_DIR = os.path.join(DATA_DIR, "national")
STATE_FILE = os.path.join(OUT_DIR, "state.json")
LOG_FILE = os.path.join(OUT_DIR, "events.csv")

SPACING = 3.0          # fastest pace: seconds between requests to Apple
MAX_SPACING = 7.0      # slowest pace it backs off to
# The pace adjusts itself to what Apple tolerates: every block slows it by 1 s,
# and every 2 hours without one speeds it up by 0.5 s (never faster than SPACING)
SPEED_UP_AFTER = 7200
RUN_SECONDS = 270      # each run checks for 4.5 min (runs are triggered every 5 min)
CYCLE = 150            # each metro is checked about this often (50 metros x 3 s)
BURST_INTERVAL = 60    # ...and this often if its division has had a restock recently
BURST_MINUTES = 30     # a division stays "hot" this long after its latest restock
MAX_BURST_DIVS = 2     # at most this many divisions get the faster checks at once
NIGHT_INTERVAL = 1800  # 10pm-7am Central: each metro every 30 min, no bursts
RETRY_WAIT = 10        # seconds before retrying a metro that failed once
SKIP_TURNS = 1         # a metro that fails even after its retry sits out its next turn
ABORT_AFTER = 5        # this many metros failing in a row = Apple is refusing everything:
SLOW_MINUTES = 20      # end the run, then check every metro only every 5 min for 20 min
SLOW_INTERVAL = 300    # (Apple's blocks usually last 10-15 min)
SAVE_EVERY = 45        # save to GitHub at least this often during a run...
SAVE_AFTER_CHANGE = 15 # ...and within this many seconds of a stock change
ASSIGN_MILES = 50      # a store belongs to a metro if it's this close to the metro's ZIP
WAVE_GAP_MIN = 45      # restocks in the same region closer together than this form a wave
WAVE_MIN = 2           # restocks needed for a regional wave

# State of each metro's ZIP, to know its Census division
METRO_STATE = {
    "New York": "NY", "Los Angeles": "CA", "Chicago": "IL", "Dallas": "TX", "Houston": "TX",
    "Washington": "DC", "Philadelphia": "PA", "Atlanta": "GA", "Miami": "FL", "Phoenix": "AZ",
    "Boston": "MA", "San Francisco": "CA", "Detroit": "MI", "Seattle": "WA", "Minneapolis": "MN",
    "San Diego": "CA", "Tampa": "FL", "Denver": "CO", "Baltimore": "MD", "St. Louis": "MO",
    "Orlando": "FL", "Charlotte": "NC", "San Antonio": "TX", "Portland": "OR", "Sacramento": "CA",
    "Pittsburgh": "PA", "Austin": "TX", "Las Vegas": "NV", "Cincinnati": "OH", "Kansas City": "KS",
    "Columbus": "OH", "Indianapolis": "IN", "Cleveland": "OH", "Nashville": "TN", "Raleigh": "NC",
    "Oklahoma City": "OK", "Tulsa": "OK", "Omaha": "NE", "Des Moines": "IA", "Little Rock": "AR",
    "Memphis": "TN", "Madison": "WI", "Albuquerque": "NM", "Salt Lake City": "UT", "Louisville": "KY",
    "Birmingham": "AL", "Richmond": "VA", "Honolulu": "HI", "Tucson": "AZ", "Buffalo": "NY",
}

# The 9 US Census divisions
DIVISIONS = {
    "New England": "CT ME MA NH RI VT",
    "Middle Atlantic": "NJ NY PA",
    "East North Central": "IL IN MI OH WI",
    "West North Central": "IA KS MN MO NE ND SD",
    "South Atlantic": "DE DC FL GA MD NC SC VA WV",
    "East South Central": "AL KY MS TN",
    "West South Central": "AR LA OK TX",
    "Mountain": "AZ CO ID MT NV NM UT WY",
    "Pacific": "AK CA HI OR WA",
}
REGION = {s: d for d, states in DIVISIONS.items() for s in states.split()}

# Fallback time zone per state, used only if Apple didn't say (split states use the main zone)
STATE_TZ = {s: "America/New_York" for s in "CT ME MA NH RI VT NJ NY PA DE DC FL GA MD NC SC VA WV MI OH IN KY".split()}
STATE_TZ.update({s: "America/Chicago" for s in "IL WI IA KS MN MO NE ND SD AL MS TN AR LA OK TX".split()})
STATE_TZ.update({s: "America/Denver" for s in "CO MT NM UT WY ID".split()})
STATE_TZ.update({"AZ": "America/Phoenix", "CA": "America/Los_Angeles", "NV": "America/Los_Angeles",
                 "OR": "America/Los_Angeles", "WA": "America/Los_Angeles", "AK": "America/Anchorage",
                 "HI": "Pacific/Honolulu"})

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
            rows = [r for r in csv.DictReader(f) if r.get("time")]
    except FileNotFoundError:
        return []
    for r in rows:                      # rows from before colours were tracked are Night Sky
        r["part"] = (r.get("part") or "").strip() or PART
    return rows


FIELDS = ["time", "store", "store_id", "city", "state", "metro", "tier", "miles", "status", "part"]


def upgrade_log():
    """Add the 'part' column to a log written before both colours were tracked."""
    if not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE, newline="") as f:
        header = next(csv.reader(f), [])
    if "part" in header:
        return
    rows = read_log()
    with open(LOG_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("Event log upgraded with a colour column")


def log_events(rows):
    if not rows:
        return
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        if new_file:
            w.writeheader()
        w.writerows(rows)


# ---------- the Kansas City bot's checks ----------
# check.py watches the 12 stores nearest Kansas City every 20-60 s and logs to
# restocks.csv on the same branch. Its restocks are folded in here (and on the
# national page), so short restocks between national checks aren't missed.
KC_LOG = os.path.join(DATA_DIR, "restocks.csv")
KC_STATE = os.path.join(DATA_DIR, "state.json")


def kc_rows(stores):
    """The KC bot's log as national-style rows, matched to national stores by name."""
    try:
        with open(KC_LOG, newline="") as f:
            raw = [r for r in csv.DictReader(f) if r.get("time")]
    except FileNotFoundError:
        return []
    try:
        with open(KC_STATE) as f:
            kc_field = json.load(f).get("field", {})
    except Exception:
        kc_field = {}
    by_name = {v.get("name"): (sid, v) for sid, v in stores.items()}
    out = []
    for r in raw:
        name = r.get("store", "").strip()
        sid, v = by_name.get(name, (None, None))
        st_code = (v or {}).get("state") or (kc_field.get(name) or {}).get("state", "")
        if not st_code:
            continue
        out.append({"time": r["time"], "store": name, "store_id": sid or f"kc:{name}",
                    "city": (v or {}).get("city") or (kc_field.get(name) or {}).get("city", ""),
                    "state": st_code, "metro": (v or {}).get("metro", ""), "tier": str((v or {}).get("tier", 0)),
                    "miles": str((v or {}).get("miles", "")), "status": r.get("status", "").strip(),
                    "part": (r.get("part") or "").strip() or PART, "source": "kc"})
    return out


def merged_rows(national, stores):
    """National events plus the KC bot's, with repeats of the same change dropped:
    per store and colour, keep only the first of any run of the same status."""
    rows = sorted(national + kc_rows(stores), key=lambda r: r["time"])
    last, out = {}, []
    for r in rows:
        key = (r["store_id"], r.get("part", PART))
        if last.get(key) == r["status"]:
            continue
        last[key] = r["status"]
        out.append(r)
    return out


# ---------- analysis (shared by the weekly summary; the page does the same in JS) ----------
def local_time(r, stores):
    """The event's time in the store's own time zone."""
    tz = (stores.get(r["store_id"]) or {}).get("tz") or STATE_TZ.get(r.get("state", ""), "America/Chicago")
    try:
        zone = ZoneInfo(tz)
    except Exception:                   # an old-style name like US/Arizona this system lacks
        zone = ZoneInfo(STATE_TZ.get(r.get("state", ""), "America/Chicago"))
    return parse_time(r["time"]).astimezone(zone)


def analyse(rows, stores, since=None):
    every = [r for r in rows if since is None or parse_time(r["time"]) >= since]
    rows = [r for r in every if r["tier"] in ("1", "2", "3")]   # tier stats use the 50 metros only
    # durations: first "available" to the next "unavailable" per store and colour
    opened, spans = {}, []
    for r in rows:
        key = (r["store_id"], r.get("part", PART))
        if r["status"] == "available":
            opened.setdefault(key, r)
        elif r["status"] == "unavailable" and key in opened:
            start = opened.pop(key)
            mins = max(1, round((parse_time(r["time"]) - parse_time(start["time"])).total_seconds() / 60))
            spans.append((start["tier"], mins))
    # a store getting both colours in the same sweep counts as one restock for waves and hours
    seen, all_restocks = set(), []
    for r in every:                     # waves use every store, tiered or not
        if r["status"] == "available" and (r["store_id"], r["time"]) not in seen:
            seen.add((r["store_id"], r["time"]))
            all_restocks.append(r)
    restocks = [r for r in all_restocks if r["tier"] in ("1", "2", "3")]
    # waves: within one region, restocks within WAVE_GAP_MIN of the previous one
    waves = []
    for region in DIVISIONS:
        cur = []
        for r in (x for x in all_restocks if REGION.get(x.get("state", "")) == region):
            t = parse_time(r["time"])
            if cur and (t - parse_time(cur[-1]["time"])).total_seconds() / 60 > WAVE_GAP_MIN:
                waves.append(cur); cur = []
            cur.append(r)
        if cur:
            waves.append(cur)
    waves = [w for w in waves if len(w) >= WAVE_MIN]
    lead = {"1": [], "2": [], "3": []}   # minutes after the wave's first restock
    for w in waves:
        w = [r for r in w if r["tier"] in ("1", "2", "3")]
        if len({r["tier"] for r in w}) < 2:   # tier order only means something with 2+ tiers
            continue
        first = parse_time(w[0]["time"])
        seen = set()
        for r in w:
            if r["tier"] not in seen:
                seen.add(r["tier"])
                lead[r["tier"]].append((parse_time(r["time"]) - first).total_seconds() / 60)
    hours = {"1": [0] * 24, "2": [0] * 24, "3": [0] * 24}
    for r in restocks:
        hours[r["tier"]][local_time(r, stores).hour] += 1
    return {
        "restocks": {t: sum(1 for r in restocks if r["tier"] == t) for t in "123"},
        "durations": {t: [m for tt, m in spans if tt == t] for t in "123"},
        "lead": lead, "hours": hours, "waves": len(waves),
        "by_region": {g: sum(1 for w in waves if REGION.get(w[0].get("state", "")) == g)
                      for g in DIVISIONS},
    }


def weekly_summary(rows, at, stores):
    a = analyse(rows, stores, since=at - datetime.timedelta(days=7))
    total = sum(a["restocks"].values())
    if not total:
        return "No Duo restocks seen across the 50 metros this week."
    parts = [f"{total} restocks across 50 metros this week "
             f"(T1 {a['restocks']['1']}, T2 {a['restocks']['2']}, T3 {a['restocks']['3']}), "
             f"{a['waves']} regional wave{'s' if a['waves'] != 1 else ''}"
             + (" (" + ", ".join(f"{g} {n}" for g, n in a["by_region"].items() if n) + ")." if a["waves"] else ".")]
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
    parts.append(f"Busiest hour: {peak % 12 or 12}{'am' if peak < 12 else 'pm'} local time.")
    kc = [r for r in rows if r["metro"] == "Kansas City" and r["status"] == "available"
          and parse_time(r["time"]) >= at - datetime.timedelta(days=7)]
    parts.append(f"Kansas City: {len(kc)} restock{'s' if len(kc) != 1 else ''}.")
    return " ".join(parts)


# ---------- continuous checking ----------
def fetch(zip_code):
    """One request; returns (stores, None) or (None, short reason)."""
    try:
        r = requests.get("https://www.apple.com/shop/retail/pickup-message",
                         headers=headers, timeout=20,
                         params={**{f"parts.{i}": p for i, p in enumerate(PARTS)}, "location": zip_code})
    except Exception as e:
        return None, f"no response ({type(e).__name__})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json()["body"]["stores"], None
    except Exception:
        return None, "HTTP 200 but not store data"


def save_to_github(state):
    """Write the files and push them to the data branch mid-run, so the page is current."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)
    if not os.path.isdir(os.path.join(DATA_DIR, ".git")):
        return
    git = lambda *a: subprocess.run(["git", "-c", "user.name=duo-bot",
                                     "-c", "user.email=duo-bot@users.noreply.github.com", *a],
                                    cwd=DATA_DIR, capture_output=True, text=True, timeout=60)
    try:
        git("add", "national")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return
        git("commit", "-m", "Update national data")
        # Pick up the KC bot's latest saves too, so its restocks feed the hot divisions
        if git("pull", "--rebase", "origin", "data").returncode != 0:
            git("rebase", "--abort")
        for _ in range(3):
            if git("push", "origin", "HEAD:data").returncode == 0:
                return
            if git("pull", "--rebase", "origin", "data").returncode != 0:
                git("rebase", "--abort")
            time.sleep(2)
        print("Couldn't save mid-run; the end-of-run save will catch it")
    except Exception as e:
        print(f"Mid-run save failed ({e}); the end-of-run save will catch it")


def apply_result(state, metro, tier, stores, stamp, products):
    """Update every store in one metro's answer; return the stock changes it shows."""
    events = []
    for st in stores:
        sid = st.get("storeNumber") or st["storeName"]
        miles = float(st.get("storedistance", 999))
        old = state["stores"].get(sid) or {}
        # A store belongs to the closest metro that has found it. If it's already
        # assigned somewhere closer (or its own metro is sitting out), keep that.
        if old and old.get("miles") is not None and old["miles"] <= miles + 1:
            miles, metro_s, tier_s = old["miles"], old.get("metro", ""), old.get("tier", 0)
        else:
            metro_s, tier_s = (metro, tier) if miles <= ASSIGN_MILES else ("", 0)
        parts = {}
        for p in PARTS:
            info = (st.get("partsAvailability") or {}).get(p, {})
            parts[p] = info.get("pickupDisplay", "unknown")
            title = ((info.get("messageTypes") or {}).get("regular") or {}).get("storePickupProductTitle")
            if title:
                products[p] = title
        rec = {
            "name": st["storeName"], "city": st.get("city", ""), "state": st.get("state", ""),
            "lat": st.get("storelatitude"), "lon": st.get("storelongitude"),
            "metro": metro_s, "tier": tier_s, "miles": round(miles, 1),
            "status": "available" if "available" in parts.values() else parts.get(PART, "unknown"),
            "parts": parts,
            "tz": ((st.get("retailStore") or {}).get("timezone")
                   or STATE_TZ.get(st.get("state", ""), "America/Chicago")),
            "region": REGION.get(st.get("state", ""), ""),
        }
        old_parts = old.get("parts") or ({PART: old["status"]} if "status" in old else {})
        old_since = old.get("since_by_part") or ({PART: old["since"]} if old.get("since") else {})
        rec["since_by_part"] = {}
        for p, st_p in parts.items():
            prev = old_parts.get(p)
            if prev is not None and prev != st_p and st_p in ("available", "unavailable"):
                events.append({"time": stamp, "store": rec["name"], "store_id": sid, "city": rec["city"],
                               "state": rec["state"], "metro": rec["metro"], "tier": rec["tier"],
                               "miles": rec["miles"], "status": st_p, "part": p})
            if st_p == "available":
                rec["since_by_part"][p] = old_since.get(p, stamp) if prev == "available" else stamp
        if rec["since_by_part"]:
            rec["since"] = min(rec["since_by_part"].values())
        state["stores"][sid] = rec
    return events


def hot_divisions(rows, at):
    """Divisions with a restock in the last BURST_MINUTES, most recent first (max MAX_BURST_DIVS)."""
    latest = {}
    cutoff = at - datetime.timedelta(minutes=BURST_MINUTES)
    for r in rows:
        if r.get("status") != "available":
            continue
        t = parse_time(r["time"])
        div = REGION.get(r.get("state", ""))
        if div and t >= cutoff and (div not in latest or t > latest[div]):
            latest[div] = t
    return [d for d, _ in sorted(latest.items(), key=lambda kv: kv[1], reverse=True)][:MAX_BURST_DIVS]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    upgrade_log()
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    state.setdefault("stores", {})
    for old_key in ("backoff_until", "sweep_seconds"):
        state.pop(old_key, None)
    checked = state.setdefault("checked_at", {})        # metro -> last time it was asked
    ok_at = state.setdefault("ok_at", {})               # metro -> last time it answered
    skip = {m: (n if isinstance(n, int) else 1) for m, n in (state.get("metro_skip") or {}).items()}
    pace = float(state.get("pace") or SPACING)
    if state.get("pace_since") and (now() - datetime.datetime.fromisoformat(state["pace_since"])).total_seconds() > SPEED_UP_AFTER \
            and pace > SPACING:
        pace = max(SPACING, pace - 0.5)
        state["pace_since"] = now().isoformat()
        print(f"No blocks for 2 h: speeding up to one request every {pace:g} s")
    state["pace"] = pace
    state.setdefault("pace_since", now().isoformat())
    log = read_log()
    products, problems, skipped_now = {}, [], []
    requests_made, retried, fails_in_row = 0, 0, 0
    run_start = time.monotonic()
    last_save = run_start
    unsaved_change_at = None

    def due(metro, interval):
        last = checked.get(metro)
        return (datetime.datetime.fromisoformat(last) + datetime.timedelta(seconds=interval)) if last else now() - datetime.timedelta(days=1)

    while time.monotonic() - run_start < RUN_SECONDS:
        t = now()
        night = t.hour >= 22 or t.hour < 7
        slow = state.get("slow_until") and t < datetime.datetime.fromisoformat(state["slow_until"])
        hot = [] if (night or slow) else hot_divisions(merged_rows(log, state["stores"]), t)
        state["hot"] = hot

        def interval(metro):
            if night:
                return NIGHT_INTERVAL
            if slow:
                return SLOW_INTERVAL
            return BURST_INTERVAL if REGION.get(METRO_STATE.get(metro, "")) in hot else CYCLE

        # The most overdue metro goes next
        metro, zip_code, tier = min(METROS, key=lambda m: due(m[0], interval(m[0])))
        wait = (due(metro, interval(metro)) - t).total_seconds()
        if wait > 0:
            if time.monotonic() - run_start + wait > RUN_SECONDS:
                break
            time.sleep(min(wait, 30))
            continue

        if skip.get(metro):                              # failed last time: sit this turn out
            skip[metro] -= 1
            if not skip[metro]:
                del skip[metro]
            checked[metro] = now().isoformat()
            skipped_now.append(metro)
            continue

        stores, why = fetch(zip_code)
        requests_made += 1
        if stores is None:
            print(f"{metro}: {why}, retrying in {RETRY_WAIT} s")
            time.sleep(RETRY_WAIT)
            retried += 1
            stores, why = fetch(zip_code)
            requests_made += 1
        checked[metro] = now().isoformat()
        if stores is None:
            fails_in_row += 1
            problems.append(f"{metro}: {why}")
            skip[metro] = SKIP_TURNS
            print(f"{metro}: failed again ({why}), sitting out its next turn")
            if fails_in_row >= ABORT_AFTER:
                state["slow_until"] = (now() + datetime.timedelta(minutes=SLOW_MINUTES)).isoformat()
                state["pace"] = min(MAX_SPACING, pace + 1)
                state["pace_since"] = now().isoformat()
                state["blocks"] = (state.get("blocks") or [])[-19:] + [now().isoformat(timespec="minutes")]
                print(f"{ABORT_AFTER} metros in a row failed: ending this run, slowing down for {SLOW_MINUTES} min, "
                      f"then one request every {state['pace']:g} s")
                break
            time.sleep(pace)
            continue
        fails_in_row = 0
        ok_at[metro] = now().isoformat()
        events = apply_result(state, metro, tier, stores, now().strftime("%Y-%m-%d %H:%M"), products)
        if events:
            log_events(events)
            log.extend({**e, "tier": str(e["tier"]), "miles": str(e["miles"])} for e in events)
            unsaved_change_at = unsaved_change_at or time.monotonic()
            print(f"{metro}: {len(events)} change(s): " + ", ".join(f"{e['store']} {e['status']}" for e in events))

        state["last_sweep"] = now().isoformat()
        elapsed_since_save = time.monotonic() - last_save
        if (unsaved_change_at and time.monotonic() - unsaved_change_at >= SAVE_AFTER_CHANGE) or elapsed_since_save >= SAVE_EVERY:
            state["metros_ok"] = sum(1 for m, _, _ in METROS if m in ok_at and
                                     (now() - datetime.datetime.fromisoformat(ok_at[m])).total_seconds() < 2 * interval(m) + 60)
            save_to_github(state)
            last_save, unsaved_change_at = time.monotonic(), None
        time.sleep(pace)

    t = now()
    if products:
        state["products"] = products     # Apple's own names for each part, to confirm part numbers
    state["colours"] = {p: COLOURS.get(p, p) for p in PARTS}
    night = t.hour >= 22 or t.hour < 7
    window = (NIGHT_INTERVAL if night else CYCLE * 2) + 60
    state["metros_ok"] = sum(1 for m, _, _ in METROS
                             if m in ok_at and (t - datetime.datetime.fromisoformat(ok_at[m])).total_seconds() < window)
    state["retries"] = retried
    state["metro_skip"] = skip
    state["skipped"] = sorted(set(skipped_now))
    state["cycle_seconds"] = CYCLE
    if problems:
        state["last_error"] = f"{t.isoformat(timespec='minutes')}: " + "; ".join(problems[-5:])
    else:
        state.pop("last_error", None)
    state["metros"] = [{"metro": m, "zip": z, "tier": tr, "state": METRO_STATE.get(m, "")} for m, z, tr in METROS]
    print(f"Run done: {requests_made} requests, {state['metros_ok']}/{len(METROS)} metros answering, "
          f"hot divisions: {state.get('hot') or 'none'}{', SLOW MODE' if state.get('slow_until') and t < datetime.datetime.fromisoformat(state['slow_until']) else ''}")

    # Weekly summary: Sunday after 6pm Central, once per week
    t = now()
    week = t.strftime("%G-W%V")
    if t.weekday() == 6 and t.hour >= 18 and state.get("last_weekly") != week:
        push("Duo restocks: week in review", weekly_summary(merged_rows(read_log(), state["stores"]), t, state["stores"]),
             priority="default", click="https://aryakvsathye.github.io/duo-tracker/national.html")
        state["last_weekly"] = week

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


if __name__ == "__main__":
    main()
