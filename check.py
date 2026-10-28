import os, json, csv, time, datetime, requests
from zoneinfo import ZoneInfo

PART = os.environ["PART"]          # the main colour (Night Sky), from the repo secret
ZIP = os.environ["ZIP"]
TEST = os.environ.get("TEST") == "true"
# Try mode: check a different part number for real (no fake stock),
# send real alerts, but leave the saved data untouched.
TRY_PART = os.environ.get("TRY_PART", "").strip().upper()
DRY = TEST or bool(TRY_PART)  # never save data in test or try mode

# Every colour being watched. Apple answers for all of them in one request.
COLOURS = {"MK1F4LL/A": "Night Sky", "MK1E4LL/A": "Star White"}
PARTS = [TRY_PART] if TRY_PART else [PART] + [p for p in COLOURS if p != PART]
PRIMARY = PART


def colour(part):
    return COLOURS.get(part, part)


TOPIC = "aryak-duo-256-night-sky"
MAX_MILES = 50          # only stores this close send stock alerts
REGIONAL_MILES = 400    # stores beyond MAX_MILES up to this send a soft (silent) update instead
GAP = 60                # seconds between checks normally
BURST_GAP = 20          # seconds between checks during a restock wave
BURST_MINUTES = 30      # how long burst mode lasts after the latest restock in a wave
WAVE_WINDOW = 20        # minutes: restocks at this many stores within this window = a wave
WAVE_STORES = 2
WAVE_COOLDOWN = 60      # minutes between wave heads-ups
RUN_SECONDS = 540       # keep checking for 9 min, longer than the 5-min trigger,
                        # so the next run is always queued and starts the moment this one ends
HISTORY = 90            # checks kept for the lap chart
TOKEN_EXPIRES = datetime.date(2026, 11, 26)  # GitHub token used by cron-job.org

TZ = ZoneInfo("America/Chicago")
DATA_DIR = os.environ.get("DATA_DIR", ".")   # the workflow points this at the data branch
STATE_FILE = os.path.join(DATA_DIR, "state.json")
LOG_FILE = os.path.join(DATA_DIR, "restocks.csv")
LOG_FIELDS = ["time", "store", "miles", "status", "part"]

BAG = "https://www.apple.com/shop/bag"
SITE = "https://aryakvsathye.github.io/duo-tracker/"
BUY = "https://www.apple.com/shop/buy-iphone"

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


def push(title, msg, priority="urgent", actions=None, click=BAG):
    h = {"Title": title, "Priority": priority, "Click": click}
    if actions:
        h["Actions"] = actions
    try:
        requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode(), headers=h, timeout=15)
    except Exception as e:
        print(f"ntfy error: {e}")


# ---------- restock log ----------
def parse_time(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def read_log():
    try:
        with open(LOG_FILE, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("time")]
    except FileNotFoundError:
        return []
    for r in rows:                      # rows from before colours were tracked are Night Sky
        r["part"] = (r.get("part") or "").strip() or PRIMARY
    return rows


def upgrade_log():
    """Add the 'part' column to a log written before both colours were tracked."""
    if DRY or not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE, newline="") as f:
        header = next(csv.reader(f), [])
    if "part" in header:
        return
    rows = read_log()
    with open(LOG_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("Restock log upgraded with a colour column")


upgrade_log()
log_rows = read_log()


def log_event(store, miles, status, part):
    stamp = now().strftime("%Y-%m-%d %H:%M")
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(LOG_FIELDS)
        w.writerow([stamp, store, miles, status, part])
    log_rows.append({"time": stamp, "store": store, "miles": str(miles), "status": status, "part": part})


def durations():
    """Minutes each restock lasted, per (store, colour): first 'available' to the next 'unavailable'."""
    opened, out = {}, {}
    for r in log_rows:
        key, t = (r["store"], r["part"]), parse_time(r["time"])
        if r["status"] == "available":
            opened.setdefault(key, t)
        elif r["status"] == "unavailable" and key in opened:
            mins = (t - opened.pop(key)).total_seconds() / 60
            out.setdefault(key, []).append(max(1, round(mins)))
    return out


def lasting_hint(store, part):
    d = durations()
    own = d.get((store, part))
    if own:
        if len(own) == 1:
            return f"Last time it lasted about {own[0]} min."
        return f"Restocks here have lasted {min(own)}–{max(own)} min."
    every = [m for ms in d.values() for m in ms]
    if every:
        return f"Restocks in the region have lasted {min(every)}–{max(every)} min."
    return ""


def wave_now(at):
    """Stores that restocked (either colour) within the last WAVE_WINDOW minutes, newest restock time."""
    cutoff = at - datetime.timedelta(minutes=WAVE_WINDOW)
    stores, latest = [], None
    for r in log_rows:
        if r["status"] != "available":
            continue
        t = parse_time(r["time"])
        if t >= cutoff:
            if r["store"] not in stores:
                stores.append(r["store"])
            latest = t if latest is None or t > latest else latest
    return stores, latest


def send_regional(found):
    """One quiet notification for every regional store that just got stock in this check."""
    by_store = {}
    for name, part, miles, city, st_code in found:
        e = by_store.setdefault(name, {"parts": [], "miles": miles, "where": ", ".join(x for x in (city, st_code) if x)})
        if part not in e["parts"]:
            e["parts"].append(part)
    lines = [f"{' and '.join(colour(p) for p in e['parts'])} at {n}"
             f"{', ' + e['where'] if e['where'] else ''} ({round(e['miles'])} mi)"
             for n, e in sorted(by_store.items(), key=lambda kv: kv[1]["miles"])]
    tag = "[TEST] " if TEST else ""
    title = (f"{tag}Regional restock: {next(iter(by_store))}" if len(by_store) == 1
             else f"{tag}Regional restocks at {len(by_store)} stores")
    push(title, "; ".join(lines) + ". Nothing in Kansas City yet, but it could follow, so keep your bag ready.",
         priority="low", click=SITE)
    print(f"Regional update: {lines}")


def store_details(st, parts, miles):
    hours = (st.get("storeHours") or {}).get("hours") or []
    quotes = {}
    for p in PARTS:
        info = (st.get("partsAvailability") or {}).get(p, {})
        quotes[p] = (((info.get("messageTypes") or {}).get("regular") or {}).get("storePickupQuote", ""))
    any_in = any(s == "available" for s in parts.values())
    return {
        "miles": miles,
        "status": "available" if any_in else (parts.get(PARTS[0]) or "unknown"),
        "parts": parts,
        "quotes": quotes,
        "quote": quotes.get(PARTS[0], ""),
        "phone": st.get("phoneNumber", ""),
        "lat": st.get("storelatitude"),
        "lon": st.get("storelongitude"),
        "address": (st.get("address") or {}).get("address2", ""),
        "city": st.get("city", ""),
        "hours": [{"days": h.get("storeDays", ""), "time": h.get("storeTimings", "")}
                  for h in hours],
    }


# ---------- load saved state ----------
try:
    with open(STATE_FILE) as f:
        state = json.load(f)
except Exception:
    state = {}
for key, default in [("stock", {}), ("nearby", {}), ("field", {}), ("history", []),
                     ("checks", 0), ("errors", 0), ("events", 0)]:
    state.setdefault(key, default)
# Older state kept one status per store (Night Sky); now it's one per colour
for name, v in list(state["stock"].items()):
    if isinstance(v, str):
        state["stock"][name] = {PRIMARY: v}

# Night mode: 10pm-7am, roughly one check every 15 minutes
t = now()
night = t.hour >= 22 or t.hour < 7
if night and not DRY and t.minute % 15 >= 5:
    print("Night mode: skipping this run")
    raise SystemExit(0)
single = DRY or night
run_start = time.monotonic()


def burst_active():
    until = (state.get("wave") or {}).get("until")
    return bool(until) and not night and now() < datetime.datetime.fromisoformat(until)


def update_wave():
    """Spot a regional restock wave, start burst mode and send one heads-up."""
    at = now()
    stores, latest = wave_now(at)
    if len(stores) >= WAVE_STORES:
        until = latest + datetime.timedelta(minutes=BURST_MINUTES)
        wave = state.get("wave") or {}
        if not wave.get("until") or datetime.datetime.fromisoformat(wave["until"]) < at:
            wave = {"since": at.isoformat()}
        wave.update(stores=stores, until=until.isoformat())
        state["wave"] = wave
        last = state.get("wave_alerted_at")
        quiet = last and at - datetime.datetime.fromisoformat(last) < datetime.timedelta(minutes=WAVE_COOLDOWN)
        nearby_green = any(s["status"] == "available" for s in state["nearby"].values())
        if not quiet and not nearby_green and not DRY:
            names = []
            for s in stores:
                city = (state["field"].get(s) or {}).get("city")
                names.append(f"{s} ({city})" if city else s)
            push("Restock wave in the region",
                 f"Duo restocks at {', '.join(names)} in the last {WAVE_WINDOW} min. "
                 f"Kansas City could be next, so have your bag ready. "
                 f"Checking every {BURST_GAP} s for the next {BURST_MINUTES} min.",
                 priority="default", click=BAG)
            state["wave_alerted_at"] = at.isoformat()
            print(f"Wave alert: {stores}")


# ---------- check loop ----------
alerted, failures, attempts = set(), 0, 0
params = {f"parts.{i}": p for i, p in enumerate(PARTS)}
params["location"] = ZIP
i = -1
while True:
    i += 1
    attempts += 1
    started = time.monotonic()
    lap = {"t": now().isoformat(timespec="seconds"), "ok": False, "ms": None, "code": None}
    try:
        r = requests.get("https://www.apple.com/shop/retail/pickup-message",
            headers=headers, timeout=20, params=params)
        lap["ms"] = round((time.monotonic() - started) * 1000)
        lap["code"] = r.status_code
        stores = r.json()["body"]["stores"]
        nearby, field, products = {}, {}, {}
        regional_new = []
        # Test mode fakes stock for both colours at the nearest store, plus one regional store
        by_dist = sorted(stores, key=lambda x: float(x.get("storedistance", 999)))
        test_near = next((x["storeName"] for x in by_dist if float(x.get("storedistance", 999)) <= MAX_MILES), None)
        test_far = next((x["storeName"] for x in by_dist if MAX_MILES < float(x.get("storedistance", 999)) <= REGIONAL_MILES), None)
        for st in stores:
            name = st["storeName"]
            miles = float(st.get("storedistance", 999))
            parts = {}
            for part in PARTS:
                info = (st.get("partsAvailability") or {}).get(part, {})
                status = info.get("pickupDisplay", "unknown")
                parts[part] = status
                title = (((info.get("messageTypes") or {}).get("regular") or {})
                         .get("storePickupProductTitle"))
                if title:
                    products[part] = title

                if not DRY:
                    prev = state["stock"].get(name, {}).get(part)
                    if prev is not None and prev != status:
                        log_event(name, miles, status, part)
                        state["events"] += 1
                        if status == "available" and MAX_MILES < miles <= REGIONAL_MILES:
                            regional_new.append((name, part, miles, st.get("city", ""), st.get("state", "")))
                if TEST and name == test_far:
                    regional_new.append((name, part, miles, st.get("city", ""), st.get("state", "")))

                if TRY_PART:
                    print(f"  {name} ({miles} mi): {status} [{title or part}]")

                in_stock = status == "available" or (TEST and name == test_near)
                if miles <= MAX_MILES and in_stock and (name, part) not in alerted:
                    if TRY_PART:
                        t_title = f"[TRY] {title or part} in stock: {name}"
                        body = (f"Try mode with {part}: Apple shows it available at {name} "
                                f"({miles} mi). Your Duo bot is working end to end.")
                    else:
                        tag = "[TEST] " if TEST else ""
                        t_title = f"{tag}Duo {colour(part)} in stock: {name}"
                        body = (f"{colour(part)} 256GB at Apple {name} ({miles} mi). "
                                f"Tap to open your bag, choose pickup at {name}. "
                                f"{lasting_hint(name, part)}").strip()
                    push(t_title, body, actions=f"view, Open Bag, {BAG}; view, Buy page, {BUY}")
                    alerted.add((name, part))

            if not DRY:
                state["stock"][name] = parts
                any_in = any(s == "available" for s in parts.values())
                if miles <= MAX_MILES:
                    nearby[name] = store_details(st, parts, miles)
                field[name] = {"miles": miles, "status": "available" if any_in else parts.get(PARTS[0], "unknown"),
                               "parts": parts, "city": st.get("city", ""), "state": st.get("state", "")}

        if regional_new:
            send_regional(regional_new)
        lap["ok"] = True
        lap["green"] = sorted(n for n, s in nearby.items() if s["status"] == "available")
        if not DRY:
            state["nearby"] = nearby
            state["field"] = field
            state["products"] = products     # Apple's own names for each part, to confirm part numbers
            state["colours"] = {p: colour(p) for p in PARTS}
            state["last_check"] = now().isoformat()
            update_wave()
        state["checks"] += 1
        print(f"Check {i+1}: {r.status_code} in {lap['ms']} ms, "
              f"alerted: {sorted(f'{n} ({colour(p)})' for n, p in alerted) or 'none'}"
              f"{', burst' if burst_active() else ''}")
    except Exception as e:
        failures += 1
        state["errors"] += 1
        if lap["ms"] is None:
            lap["ms"] = round((time.monotonic() - started) * 1000)
        print(f"Check {i+1}: error {e}")
    lap["burst"] = burst_active()
    if not DRY:
        state["history"] = (state["history"] + [lap])[-HISTORY:]
    gap = BURST_GAP if burst_active() else GAP
    # Stop when another full gap would run past the window
    if single or time.monotonic() - run_start + gap > RUN_SECONDS:
        break
    # Sleep so checks start evenly spaced, however long Apple took
    time.sleep(max(0, gap - (time.monotonic() - started)))

t = now()

# Failure alert, roughly once an hour while broken
if failures == attempts and not DRY and t.minute < 5:
    push("Duo checker failing",
         "All checks failed this run. Apple may be blocking it.",
         priority="low", click="https://github.com")

# Daily heartbeat after 8am, with a token warning in its last week
today = t.strftime("%Y-%m-%d")
if not DRY and t.hour >= 8 and state.get("last_heartbeat") != today:
    days_left = (TOKEN_EXPIRES - t.date()).days
    token_note = ""
    if days_left <= 7:
        token_note = (f" The GitHub token cron-job.org uses expires in {days_left} day"
                      f"{'' if days_left == 1 else 's'}. Make a new one so the bot keeps running."
                      if days_left > 0 else " The GitHub token cron-job.org uses has expired.")
    push("Duo checker: still running",
         f"{state['checks']} checks, {state['errors']} errors, "
         f"{state['events']} stock changes since last update.{token_note}",
         priority="default" if token_note else "low", click="https://github.com")
    state.update(checks=0, errors=0, events=0, last_heartbeat=today)

if not DRY:
    state["token_expires"] = TOKEN_EXPIRES.isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)
