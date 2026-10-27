import os, sys, requests

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TOPIC = "aryak-duo-256-night-sky"
MAX_MILES = 50

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                  "Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone",
}

r = requests.get("https://www.apple.com/shop/retail/pickup-message",
    headers=headers, timeout=20,
    params={"parts.0": PART, "location": ZIP})

print("Status:", r.status_code)
try:
    stores = r.json()["body"]["stores"]
except Exception:
    print("Apple did not return data. First 300 chars:")
    print(r.text[:300])
    sys.exit(0)

hits = []
for st in stores:
    miles = float(st.get("storedistance", 999))
    info = st.get("partsAvailability", {}).get(PART, {})
    print(f"{st['storeName']} ({miles} mi): {info.get('pickupDisplay')}")
    if miles <= MAX_MILES and info.get("pickupDisplay") == "available":
        hits.append(f"{st['storeName']} ({miles} mi)")

if hits:
    requests.post(f"https://ntfy.sh/{TOPIC}",
        data=("In stock: " + ", ".join(hits)).encode(),
        headers={"Title": "iPhone Duo Night Sky 256GB", "Priority": "urgent",
                 "Click": "https://www.apple.com/shop/buy-iphone"})
print(hits or "None available nearby")
