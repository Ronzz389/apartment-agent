import os
import re
import html
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# פילטרים שלך
MIN_PRICE = 5500
MAX_PRICE = 7300
MIN_ROOMS = 1.5
MAX_ROOMS = 3.0
MIN_FLOOR = 1

# שכונות (IDs) — 3 פידים נפרדים, כותרת שונה לכל אזור
# אם תרצי להוסיף גם "רמת אביב ג׳" וכו' — אפשר להוסיף כאן עוד FEED
FEEDS = [
    {"name": "🌿 צפון ישן", "neighborhood_id": 1483},
    {"name": "☕ לב העיר", "neighborhood_id": 1520},
    {"name": "🌳 רמת אביב", "neighborhood_id": 197},
]

# בסיס URL ליד2 שכירות ת"א
BASE_URL = "https://www.yad2.co.il/realestate/rent"

# פסילות
EXCLUDE_KEYWORDS = ["מרתף", "מרתפים", "סמי מרתף"]

# חשד מתווך – שולחים אבל מסמנים
BROKER_HINTS = [
    "תיווך",
    "מתווך",
    "משרד תיווך",
    "דמי תיווך",
    "עמלת תיווך",
    "בלעדיות",
    "סוכנות",
    "agent",
    "broker",
    "לתיאום ביקור",
]
NO_BROKER_HINTS = ["ללא תיווך", "בלי תיווך", "פרטי", "מפרטי"]

# מניעת כפילויות
SEEN_FILE = "seen.txt"


def build_rss_url(neighborhood_id: int) -> str:
    # אנחנו "מלמדים" את יד2 לסנן מראש: ת"א + שכונה + מחיר + חדרים + rss=1
    params = {
        "topArea": 2,
        "area": 1,
        "city": 5000,
        "neighborhood": neighborhood_id,
        "minPrice": MIN_PRICE,
        "maxPrice": MAX_PRICE,
        "minRooms": MIN_ROOMS,
        "maxRooms": MAX_ROOMS,
        "rss": 1,
    }
    return f"{BASE_URL}?{urlencode(params)}"


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    r.raise_for_status()


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(seen: set[str]):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for x in sorted(seen):
            f.write(x + "\n")


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)       # להסיר HTML אם מופיע
    s = re.sub(r"\s+", " ", s).strip()
    return s


def contains_excluded(text: str) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in EXCLUDE_KEYWORDS)


def broker_suspected(text: str) -> bool:
    t = text.lower()
    if any(x.lower() in t for x in NO_BROKER_HINTS):
        return False
    return any(x.lower() in t for x in BROKER_HINTS)


def extract_price(text: str) -> int | None:
    # "מחיר 7,200" / "שכד: 6800"
    m = re.search(r"(?:מחיר|שכ\"?ד|שכד)\s*[:\-]?\s*([\d,]{4,})", text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def extract_rooms(text: str) -> float | None:
    # "2 חדרים" / "2.5 חדרים"
    m = re.search(r"(\d+(?:\.\d+)?)\s*חדר", text)
    return float(m.group(1)) if m else None


def extract_floor(text: str) -> int | None:
    # "קומה 2" / "קרקע" (=0)
    if re.search(r"\bקרקע\b", text):
        return 0
    m = re.search(r"קומה\s*[:\-]?\s*(\d+)", text)
    return int(m.group(1)) if m else None


def fetch_rss_items(rss_url: str) -> list[dict]:
    r = requests.get(rss_url, timeout=30)
    r.raise_for_status()

    root = ET.fromstring(r.content)

    # RSS סטנדרטי: channel/item
    items = []
    for item in root.findall("./channel/item"):
        title = clean_text(item.findtext("title", default=""))
        link = clean_text(item.findtext("link", default=""))
        desc = clean_text(item.findtext("description", default=""))
        if link:
            items.append({"title": title, "link": link, "desc": desc})
    return items


def main():
    seen = load_seen()
    sent = 0

    for feed in FEEDS:
        feed_name = feed["name"]
        rss_url = build_rss_url(feed["neighborhood_id"])

        try:
            items = fetch_rss_items(rss_url)
        except Exception as e:
            send_telegram(f"⚠️ שגיאת משיכה בפיד {feed_name}\n{type(e).__name__}: {e}")
            continue

        for it in items:
            link = it["link"]
            if link in seen:
                continue

            title = it["title"]
            desc = it["desc"]
            full_text = f"{title} {desc}"

            # פסילות
            if contains_excluded(full_text):
                seen.add(link)
                continue

            # חילוצים (Double-check למרות שה-URL כבר מסנן)
            price = extract_price(full_text)
            rooms = extract_rooms(full_text)
            floor = extract_floor(full_text)

            if price is not None and not (MIN_PRICE <= price <= MAX_PRICE):
                seen.add(link)
                continue
            if rooms is not None and not (MIN_ROOMS <= rooms <= MAX_ROOMS):
                seen.add(link)
                continue

            # קומה: נסנן רק אם הצלחנו לחלץ. אם לא מופיע ב-RSS, לא נפסול כדי לא לפספס.
            if floor is not None and floor < MIN_FLOOR:
                seen.add(link)
                continue

            suspected = broker_suspected(full_text)
            header = feed_name
            if suspected:
                header = f"⚠️ חשד מתווך | {header}"

            lines = [f"🏠 {header}", title if title else "(כותרת לא זמינה)"]

            details = []
            if price is not None:
                details.append(f"💰 {price:,} ₪")
            if rooms is not None:
                details.append(f"🛏 {rooms} חדרים")
            if floor is not None:
                details.append(f"🧱 קומה {floor}")
            if details:
                lines.append(" | ".join(details))

            lines.append("")
            lines.append(link)

            send_telegram("\n".join(lines))

            seen.add(link)
            sent += 1

            # לא להציף
            if sent >= 10:
                break

        if sent >= 10:
            break

    save_seen(seen)

    # כברירת מחדל אני לא שולחת "אין חדשות" כל ריצה כדי לא להציק.
    # אם את רוצה בכל זאת, אפשר להדליק עם SECRET בשם SEND_HEARTBEAT="1"
    if sent == 0 and os.environ.get("SEND_HEARTBEAT", "") == "1":
        send_telegram("✅ Apartment Agent רץ – אין מודעות חדשות שמתאימות כרגע.")


if __name__ == "__main__":
    main()
