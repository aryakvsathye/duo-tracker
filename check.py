import os, sys, requests

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TOPIC = "aryak-duo-256-night-sky"

headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                  "Mobile/15E148 Safari/604.1",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.apple.com/shop/buy-iphone",
}

s = requests.Session()
s.get("https://www.apple.com/shop/buy-iphone", headers=headers, timeout=20)
r = s.get("https://www.apple.com/shop/fulfillment-messages",
    headers=headers, timeout=20,
    params={"fae": "true", "pl": "true", "mts.0": "regular",
            "parts.0": PART, "location": ZIP})

print("Status:", r.status_code)
try:
    stores = r.json()["body"]["content"]["pickupMessage"]["stores"]
except Exception:
    print("Apple did not return data. First 300 chars:")
    print(r.text[:300])
    sys.exit(0)

hits = [f"{st['storeName']} ({st.get('storedistance','?')} mi)"
        for st in stores
        if st["partsAvailability"].get(PART, {}).get("pickupDisplay") == "available"]

if hits:
    requests.post(f"https://ntfy.sh/{TOPIC}",
        data=("In stock: " + ", ".join(hits)).encode(),
        headers={"Title": "iPhone Duo Night Sky 256GB", "Priority": "urgent",
                 "Click": "https://www.apple.com/shop/buy-iphone"})
print(hits or f"None available across {len(stores)} stores")
