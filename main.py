import os
import re
import html
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# יד2 RSS לת"א שכירות (כל תל אביב)
RSS_URL = "https://www.yad2.co.il/realestate/rent?topArea=2&area=1&city=5000&rss=1"

# פילטרים שלך
MIN_PRICE = 5500
MAX_PRICE = 7300
MIN_ROOMS = 1.5
MAX_ROOMS = 3.0
MIN_FLOOR = 1

# כמו שסיכמנו: שם המשתנה
AREA_KEYWORDS = [
    "צפון ישן",
    "לב העיר",
    "באזל",
    "נורדאו",
    "דיזנגוף",
    "בן גוריון",
    "ארלוזורוב",
    "אבן גבירול",
    "כיכר רבין",
    "כיכר דיזנגוף",
    "פרישמן",
    "גורדון",
    "בוגרשוב",
    "הירקון",
    "הטיילת",
    "שדרות חן",
    "רוטשילד",
    "שינקין",
]

EXCLUDE_KEYWORDS = [
    "מרתף",
    "מרתפים",
    "סמי מרתף",
]

SEEN_FILE = "seen.txt"


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text})
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


def strip_html(s: str) -> str:
    # RSS description לפעמים מגיע עם HTML
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_price(text: str) -> int | None:
    # מחפש "מחיר 7,200" או "7200"
    m = re.search(r"(?:מחיר|שכ\"?ד|שכד)\s*[:\-]?\s*([\d,]{4,})", text)
    if not m:
        m = re.search(r"\b([\d,]{4,})\b", text)  # fallback
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def extract_rooms(text: str) -> float | None:
    # "2 חדרים" / "2.5 חדרים"
    m = re.search(r"(\d+(?:\.\d+)?)\s*חדר", text)
    return float(m.group(1)) if m else None


def extract_floor(text: str) -> int | None:
    # "קומה 2" / "קומה: 5"
    # מתייחס גם ל"קרקע" = 0
    if re.search(r"\bקרקע\b", text):
        return 0
    m = re.search(r"קומה\s*[:\-]?\s*(\d+)", text)
    return int(m.group(1)) if m else None


def contains_area_keyword(text: str) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in AREA_KEYWORDS)


def contains_excluded(text: str) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in EXCLUDE_KEYWORDS)


def main():
    seen = load_seen()

    resp = requests.get(RSS_URL, timeout=30)
    resp.raise_for_status()

    # parse RSS פשוט בלי ספריות חיצוניות
    # עובד כי זה XML סטנדרטי
    items = re.findall(r"<item>(.*?)</item>", resp.text, flags=re.S)

    sent_count = 0

    for raw_item in items:
        link_m = re.search(r"<link>(.*?)</link>", raw_item)
        title_m = re.search(r"<title>(.*?)</title>", raw_item, flags=re.S)
        desc_m = re.search(r"<description>(.*?)</description>", raw_item, flags=re.S)

        if not link_m:
            continue

        link = strip_html(link_m.group(1))
        title = strip_html(title_m.group(1) if title_m else "")
        desc = strip_html(desc_m.group(1) if desc_m else "")

        if link in seen:
            continue

        full_text = f"{title} {desc}"

        # אזור
        if not contains_area_keyword(full_text):
            continue

        # פסילות
        if contains_excluded(full_text):
            continue

        # חילוצים
        price = extract_price(full_text)
        rooms = extract_rooms(full_text)
        floor = extract_floor(full_text)

        # סינונים (אם לא הצלחנו לחלץ ערך — לא נפסול עליו, כדי לא לפספס דירות טובות)
        if price is not None and not (MIN_PRICE <= price <= MAX_PRICE):
            continue
        if rooms is not None and not (MIN_ROOMS <= rooms <= MAX_ROOMS):
            continue
        if floor is not None and floor < MIN_FLOOR:
            continue

        # הודעה
        msg_lines = [
            "🏠 דירה חדשה שעומדת בפילטרים שלך!",
            title if title else "(כותרת לא זמינה)",
        ]
        details = []
        if price is not None:
            details.append(f"💰 {price:,} ₪")
        if rooms is not None:
            details.append(f"🛏 {rooms} חדרים")
        if floor is not None:
            details.append(f"🧱 קומה {floor}")
        if details:
            msg_lines.append(" | ".join(details))

        msg_lines.append("")
        msg_lines.append(link)

        send_telegram("\n".join(msg_lines))

        seen.add(link)
        sent_count += 1

        # לא להציף: מקסימום 10 הודעות בריצה
        if sent_count >= 10:
            break

    save_seen(seen)

    if sent_count == 0:
        # שקט—לא חובה, אבל זה עוזר לדעת שהסוכן חי
        send_telegram("✅ Apartment Agent רץ – אין מודעות חדשות שמתאימות כרגע.")


if __name__ == "__main__":
    main()
