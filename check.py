import os, time, datetime, requests

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TEST = os.environ.get("TEST") == "true"
TOPIC = "aryak-duo-256-night-sky"
MAX_MILES = 50
CHECKS = 1 if TEST else 10
GAP = 60

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

def push(title, msg, priority="urgent", actions=None, click=BAG):
    h = {"Title": title, "Priority": priority, "Click": click}
    if actions:
        h["Actions"] = actions
    requests.post(f"https://ntfy.sh/{TOPIC}", data=msg.encode(), headers=h)

alerted, failures = set(), 0
for i in range(CHECKS):
    try:
        r = requests.get("https://www.apple.com/shop/retail/pickup-message",
            headers=headers, timeout=20,
            params={"parts.0": PART, "location": ZIP})
        stores = r.json()["body"]["stores"]
        for st in stores:
            miles = float(st.get("storedistance", 999))
            info = st.get("partsAvailability", {}).get(PART, {})
            name = st["storeName"]
            in_stock = info.get("pickupDisplay") == "available" or TEST
            if miles <= MAX_MILES and in_stock and name not in alerted:
                tag = "[TEST] " if TEST else ""
                push(f"{tag}Duo in stock: {name}",
                     f"Night Sky 256GB at Apple {name} ({miles} mi). "
                     f"Tap to open your bag, choose pickup at {name}.",
                     actions=f"view, Open Bag, {BAG}; view, Buy page, {BUY}")
                alerted.add(name)
        print(f"Check {i+1}: {r.status_code}, alerted: {sorted(alerted) or 'none'}")
    except Exception as e:
        failures += 1
        print(f"Check {i+1}: error {e}")
    if i < CHECKS - 1:
        time.sleep(GAP)

if failures == CHECKS and not TEST and datetime.datetime.utcnow().minute < 5:
    push("Duo checker failing",
         "All checks failed this run. Apple may be blocking it.",
         priority="low", click="https://github.com")
