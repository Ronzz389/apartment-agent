import os
import json
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.yad2.co.il"
SEARCH_URL = "https://www.yad2.co.il/realestate/rent?area=1&city=5000&topArea=2"

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}

SEEN_PATH = ".cache/seen.json"


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_seen():
    os.makedirs(".cache", exist_ok=True)
    if not os.path.exists(SEEN_PATH):
        return set()
    with open(SEEN_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen(seen_set):
    os.makedirs(".cache", exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_set)), f, ensure_ascii=False, indent=2)


def tg_send(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_number(text: str):
    if not text:
        return None
    m = re.search(r"([\d,]+)", text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def extract_rooms(text: str):
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*חדר", text)
    if not m:
        return None
    return float(m.group(1))


def extract_floor(text: str):
    if not text:
        return None
    if "קרקע" in text:
        return 0
    m = re.search(r"קומה\s*([0-9]+)", text)
    if not m:
        return None
    return int(m.group(1))


def collect_listing_links(search_html: str):
    """
    אוספים רק לינקים למודעות מעמוד התוצאות.
    בלי להסתמך על טקסט בעמוד (כי הוא לפעמים לא קיים/חלקי).
    """
    soup = BeautifulSoup(search_html, "html.parser")
    anchors = soup.find_all("a", href=True)

    ids_to_link = {}
    for a in anchors:
        href = a["href"]
        if not href.startswith("/realestate/item/"):
            continue
        m = re.search(r"/realestate/item/(\d+)", href)
        if not m:
            continue
        item_id = m.group(1)
        ids_to_link[item_id] = urljoin(BASE, href)

    return [{"id": k, "link": v} for k, v in ids_to_link.items()]


def parse_details_from_item_page(item_html: str):
    """
    מנסים להוציא פרטים מתוך עמוד המודעה עצמו.
    גם אם לא מצליחים להוציא הכל – לא מפסלים.
    """
    soup = BeautifulSoup(item_html, "html.parser")
    full_text = normalize(soup.get_text(" ", strip=True))

    price = None
    # הרבה פעמים מחיר מופיע עם ₪
    m_price = re.search(r"([\d,]+)\s*₪", full_text)
    if m_price:
        price = int(m_price.group(1).replace(",", ""))

    rooms = extract_rooms(full_text)
    floor = extract_floor(full_text)

    # שכונה: קשה לחלץ תמיד. ננסה בכמה דרכים:
    neighborhood = None
    # לפעמים יש "שכונה: ..."
    m_n = re.search(r"שכונ[הת]\s*[:\-]\s*([^|,]+)", full_text)
    if m_n:
        neighborhood = normalize(m_n.group(1))

    return {
        "price": price,
        "rooms": rooms,
        "floor": floor,
        "neighborhood_text": full_text,   # נשמור טקסט לחיפוש מילות שכונה
    }


def passes_filters(details, cfg, area_cfg):
    # מחיר
    if details["price"] is not None:
        if details["price"] < cfg["price_min"] or details["price"] > cfg["price_max"]:
            return False

    # חדרים
    if details["rooms"] is not None:
        if details["rooms"] < cfg["rooms_min"] or details["rooms"] > cfg["rooms_max"]:
            return False

    # קומה
    if details["floor"] is not None:
        if details["floor"] < cfg["min_floor"]:
            return False

    # שכונה/אזור:
    # אם לא מצליחים למצוא — לא נפסול (כדי לא לפספס).
    # אבל אם כן יש טקסט, נבדוק מילות שכונה.
    text = details.get("neighborhood_text") or ""
    keys = area_cfg.get("neighborhood_keywords") or []
    if keys:
        if text and not any(k in text for k in keys):
            # אם יש לנו טקסט ולא מצאנו שכונה – נחשב "לא מתאים"
            return False

    return True


def main():
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    debug = os.environ.get("DEBUG", "1") == "1"

    if not token or not chat_id:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID env vars (set them in GitHub Secrets).")

    cfg = load_config()
    seen = load_seen()

    search_html = fetch_html(SEARCH_URL)
    links = collect_listing_links(search_html)

    # נבדוק הרבה לינקים כדי להימנע מפספוסים
    links = links[:80]

    total_links = len(links)
    new_links = [x for x in links if x["id"] not in seen]

    debug_lines = []
    debug_lines.append(f"Debug: נמצאו {total_links} מודעות בעמוד, מתוכן {len(new_links)} חדשות (לא נראו).")

    results_by_area = []

    for area in cfg["areas"]:
        hits = []
        checked = 0

        for it in new_links:
            checked += 1
            try:
                item_html = fetch_html(it["link"])
                details = parse_details_from_item_page(item_html)
            except Exception:
                # אם מודעה נחסמת/נכשלת – פשוט נמשיך
                continue

            if passes_filters(details, cfg, area):
                hits.append(it)

            # לא להעמיס: מקס 35 בדיקות לכל אזור
            if checked >= 35:
                break

            time.sleep(0.5)

        if hits:
            results_by_area.append((area["title"], hits[:10]))

    if debug:
        tg_send(token, chat_id, "\n".join(debug_lines))
        time.sleep(1)

    if not results_by_area:
        tg_send(token, chat_id, "לא נמצאו דירות שעברו סינון בריצה הזו. אם זה לא הגיוני – נרחיב עוד יותר את הקריטריונים/נשנה מקור נתונים.")
        return

    for title, hits in results_by_area:
        lines = [title]
        for it in hits:
            lines.append(it["link"])
            seen.add(it["id"])
        tg_send(token, chat_id, "\n".join(lines))
        time.sleep(1)

    save_seen(seen)


if __name__ == "__main__":
    main()
