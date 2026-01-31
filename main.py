import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

YAD2_BASE = "https://www.yad2.co.il"

STATE_DIR = "state"
SEEN_FILE = os.path.join(STATE_DIR, "seen_ids.json")


def load_seen() -> set[str]:
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(map(str, data.get("seen_ids", [])))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen_ids": sorted(seen)}, f, ensure_ascii=False, indent=2)


def telegram_send(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def fetch_listing_urls(search_url: str) -> list[str]:
    """
    הכי פשוט:
    - נכנסות ל-URL של החיפוש שלך
    - מוצאות לינקים של מודעות מה-HTML
    - מחזירות רשימה של URL-ים ייחודיים
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    }

    r = requests.get(search_url, headers=headers, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    urls = set()

    # 1) כל הלינקים בעמוד
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        # דילוג על עוגנים/ג'אווהסקריפט
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        full = urljoin(YAD2_BASE, href)

        # סינון גס: רק לינקים שנראים כמו מודעת נדל"ן
        # (ביד2 המבנים משתנים, אז אנחנו מאפשרות כמה דפוסים)
        if "yad2.co.il" not in full:
            continue
        if "/realestate/" not in full:
            continue

        # הרבה מודעות כוללות מספרים (ID) ב-URL
        if re.search(r"\d{5,}", full):
            urls.add(full)

    return sorted(urls)


def extract_id(url: str) -> str:
    """
    מנסה לחלץ ID יציב מה-URL כדי להימנע מכפילויות.
    אם אין, משתמשת ב-URL עצמו.
    """
    m = re.search(r"(\d{5,})", url)
    return m.group(1) if m else url


def main():
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID in environment variables (GitHub Secrets).")

    # ה-URL המדויק שלך:
    search_url = "https://www.yad2.co.il/realestate/rent?minPrice=5500&maxPrice=7300&minRooms=1.5&maxRooms=3&minFloor=1&imageOnly=1&priceOnly=1&multiNeighborhood=1520%2C1483%2C197&zoom=12"

    seen = load_seen()

    all_urls = fetch_listing_urls(search_url)

    new_urls = []
    for u in all_urls:
        uid = extract_id(u)
        if uid not in seen:
            new_urls.append(u)
            seen.add(uid)

    save_seen(seen)

    if not new_urls:
        telegram_send(bot_token, chat_id, "✅ ריצה הסתיימה. לא נמצאו מודעות חדשות בחיפוש הזה.")
        return

    # שולחות עד 10 קישורים בהודעה אחת כדי לא להציף
    chunks = [new_urls[i:i+10] for i in range(0, len(new_urls), 10)]

    for idx, chunk in enumerate(chunks, start=1):
        lines = [
            "🏠 נמצאו מודעות חדשות (ת\"א – צפון ישן / לב העיר / רמת אביב):",
            *chunk
        ]
        telegram_send(bot_token, chat_id, "\n".join(lines))
        time.sleep(1)  # קטן כדי לא להיתקע על rate limit

    telegram_send(bot_token, chat_id, f"✅ נשלחו {len(new_urls)} מודעות חדשות.")


if __name__ == "__main__":
    main()
