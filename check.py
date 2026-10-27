import os, requests

PART = os.environ["PART"]
ZIP = os.environ["ZIP"]
TOPIC = "aryak-duo-256-night-sky"

r = requests.get("https://www.apple.com/shop/fulfillment-messages",
    headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
    params={"pl": "true", "mts.0": "regular", "parts.0": PART, "location": ZIP})
stores = r.json()["body"]["content"]["pickupMessage"]["stores"]

hits = [f"{s['storeName']} ({s.get('storedistance','?')} mi)"
        for s in stores
        if s["partsAvailability"].get(PART, {}).get("pickupDisplay") == "available"]

if hits:
    requests.post(f"https://ntfy.sh/{TOPIC}",
        data=("In stock: " + ", ".join(hits)).encode(),
        headers={"Title": "iPhone Duo Night Sky 256GB", "Priority": "urgent",
                 "Click": "https://www.apple.com/shop/buy-iphone"})
print(hits or "None available")
