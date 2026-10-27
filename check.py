import os, time, requests

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TOPIC = "aryak-duo-256-night-sky"
MAX_MILES = 50
CHECKS = 10        # checks per run
GAP = 60           # seconds between checks

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                  "Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone",
}

alerted = set()
for i in range(CHECKS):
    try:
        r = requests.get("https://www.apple.com/shop/retail/pickup-message",
            headers=headers, timeout=20,
            params={"parts.0": PART, "location": ZIP})
        stores = r.json()["body"]["stores"]
        hits = []
        for st in stores:
            miles = float(st.get("storedistance", 999))
            info = st.get("partsAvailability", {}).get(PART, {})
            if miles <= MAX_MILES and info.get("pickupDisplay") == "available":
                hits.append(f"{st['storeName']} ({miles} mi)")
        new = [h for h in hits if h not in alerted]
        if new:
            requests.post(f"https://ntfy.sh/{TOPIC}",
                data=("In stock: " + ", ".join(new)).encode(),
                headers={"Title": "iPhone Duo Night Sky 256GB",
                         "Priority": "urgent",
                         "Click": "https://www.apple.com/shop/buy-iphone"})
            alerted.update(new)
        print(f"Check {i+1}: status {r.status_code}, {hits or 'none nearby'}")
    except Exception as e:
        print(f"Check {i+1}: error {e}")
    if i < CHECKS - 1:
        time.sleep(GAP)
