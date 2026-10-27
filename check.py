import os, json, csv, time, datetime, requests
from zoneinfo import ZoneInfo

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TEST = os.environ.get("TEST") == "true"
TOPIC = "aryak-duo-256-night-sky"
MAX_MILES = 50
GAP = 60
TZ = ZoneInfo("America/Chicago")
STATE_FILE = "state.json"
LOG_FILE = "restocks.csv"

BAG = "https://www.apple.com/shop/bag"
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
    requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode(), headers=h)

def log_event(store, miles, status):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "store", "miles", "status"])
        w.writerow([now().strftime("%Y-%m-%d %H:%M"), store, miles, status])

# Load saved state
try:
    with open(STATE_FILE) as f:
        state = json.load(f)
except Exception:
    state = {}
state.setdefault("stock", {})
state.setdefault("checks", 0)
state.setdefault("errors", 0)
state.setdefault("events", 0)

# Night mode: 10pm-7am, roughly one check every 15 minutes
t = now()
night = t.hour >= 22 or t.hour < 7
if night and not TEST and t.minute % 15 >= 5:
    print("Night mode: skipping this run")
    raise SystemExit(0)
CHECKS = 1 if (TEST or night) else 10

alerted, failures = set(), 0
for i in range(CHECKS):
    try:
        r = requests.get("https://www.apple.com/shop/retail/pickup-message",
            headers=headers, timeout=20,
            params={"parts.0": PART, "location": ZIP})
        stores = r.json()["body"]["stores"]
        for st in stores:
            name = st["storeName"]
            miles = float(st.get("storedistance", 999))
            info = st.get("partsAvailability", {}).get(PART, {})
            status = info.get("pickupDisplay", "unknown")

            if not TEST:
                prev = state["stock"].get(name)
                if prev is not None and prev != status:
                    log_event(name, miles, status)
                    state["events"] += 1
                state["stock"][name] = status

            in_stock = status == "available" or TEST
            if miles <= MAX_MILES and in_stock and name not in alerted:
                tag = "[TEST] " if TEST else ""
                push(f"{tag}Duo in stock: {name}",
                     f"Night Sky 256GB at Apple {name} ({miles} mi). "
                     f"Tap to open your bag, choose pickup at {name}.",
                     actions=f"view, Open Bag, {BAG}; view, Buy page, {BUY}")
                alerted.add(name)
        state["checks"] += 1
        print(f"Check {i+1}: {r.status_code}, alerted: {sorted(alerted) or 'none'}")
    except Exception as e:
        failures += 1
        state["errors"] += 1
        print(f"Check {i+1}: error {e}")
    if i < CHECKS - 1:
        time.sleep(GAP)

t = now()

# Failure alert, roughly once an hour while broken
if failures == CHECKS and not TEST and t.minute < 5:
    push("Duo checker failing",
         "All checks failed this run. Apple may be blocking it.",
         priority="low", click="https://github.com")

# Daily heartbeat after 8am
today = t.strftime("%Y-%m-%d")
if not TEST and t.hour >= 8 and state.get("last_heartbeat") != today:
    push("Duo checker: still running",
         f"{state['checks']} checks, {state['errors']} errors, "
         f"{state['events']} stock changes since last update.",
         priority="low", click="https://github.com")
    state.update(checks=0, errors=0, events=0, last_heartbeat=today)

if not TEST:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)
