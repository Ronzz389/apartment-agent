import os
import json
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.yad2.co.il"
# חיפוש רחב לת"א להשכרה (פשוט להתחיל מפה ולסנן בקוד)
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
    # שולחות Plain Text בלבד כדי להימנע מ-ParseError
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
    # "קומה 3" / "קומה קרקע" / "קומה 1"
    if "קומה" not in text:
        return None
    if "קרקע" in text:
        return 0
    m = re.search(r"קומה\s*([0-9]+)", text)
    if not m:
        return None
    return int(m.group(1))


def parse_listings(html: str):
    """
    יד2 דינמי, אבל הקישורים למודעות מופיעים ב-HTML כ<a href="/realestate/item/...">
    ניקח כל מה שנראה כמו מודעה + נשלוף ממנו טקסט.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)

    items = []
    for a in anchors:
        href = a["href"]
        if not href.startswith("/realestate/item/"):
            continue

        link = urljoin(BASE, href)
        text = normalize(a.get_text(" ", strip=True))

        # לפעמים הטקסט על ה-a ריק, אז ננסה לקחת גם מההורה הקרוב
        if len(text) < 10:
            parent_text = normalize(a.parent.get_text(" ", strip=True)) if a.parent else ""
            text = parent_text or text

        # מזהה יציב: המספר בסוף /realestate/item/XXXXXXX
        m = re.search(r"/realestate/item/(\d+)", href)
        item_id = m.group(1) if m else link

        items.append({
            "id": item_id,
            "link": link,
            "text": text
        })

    # להסיר כפילויות לפי id
    uniq = {}
    for it in items:
        uniq[it["id"]] = it
    return list(uniq.values())


def matches_filters(item, cfg, area_cfg):
    t = item["text"]

    # שכונה/אזור לפי מילות מפתח
    if area_cfg["neighborhood_keywords"]:
        if not any(k in t for k in area_cfg["neighborhood_keywords"]):
            return False

    price = extract_number(t)
    rooms = extract_rooms(t)
    floor = extract_floor(t)

    # מחיר: אם אין מחיר בטקסט — לא נפסול (כדי לא לפספס), אבל נעדיף כאלה עם מחיר
    if price is not None:
        if price < cfg["price_min"] or price > cfg["price_max"]:
            return False

    # חדרים
    if rooms is not None:
        if rooms < cfg["rooms_min"] or rooms > cfg["rooms_max"]:
            return False

    # קומה
    if floor is not None:
        if floor < cfg["min_floor"]:
            return False

    return True


def score_item(item, cfg):
    # ניקוד קל כדי להעדיף "עורפי/שקט/פנימי" אם מופיע
    t = item["text"]
    score = 0
    for kw in cfg.get("quiet_keywords", []):
        if kw in t:
            score += 1
    # תעדוף מודעות שיש בהן מחיר (כדי שהן אמיתיות/ברורות יותר)
    if extract_number(t) is not None:
        score += 1
    return score


def main():
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID env vars (set them in GitHub Secrets).")

    cfg = load_config()
    seen = load_seen()

    html = fetch_html(SEARCH_URL)
    listings = parse_listings(html)

    new_hits_by_area = []
    for area in cfg["areas"]:
        hits = []
        for it in listings:
            if it["id"] in seen:
                continue
            if matches_filters(it, cfg, area):
                hits.append(it)

        hits.sort(key=lambda x: score_item(x, cfg), reverse=True)
        if hits:
            new_hits_by_area.append((area["title"], hits[:10]))  # עד 10 לכל אזור

    if not new_hits_by_area:
        # שימי לב: זה “אין חדש מאז ריצה קודמת”, לא “אין דירות באתר”
        tg_send(token, chat_id, "אין מודעות חדשות שעברו את הסינון בריצה הזו 🙂")
        return

    # שליחה מסודרת: כותרת אזור + קישורים
    for title, hits in new_hits_by_area:
        lines = [title]
        for it in hits:
            lines.append(f"- {it['link']}")
            seen.add(it["id"])

        tg_send(token, chat_id, "\n".join(lines))
        time.sleep(1)

    save_seen(seen)


if __name__ == "__main__":
    main()
