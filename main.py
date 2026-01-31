import os
import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.yad2.co.il"
SEEN_FILE = "seen_ids.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120 Safari/537.36",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8"
}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def tg_send(bot_token: str, chat_id: str, text: str):
    # שולחים Plain Text בלבד (ללא parse_mode) כדי למנוע תקלות פורמט
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def normalize_url(u: str) -> str:
    # משאירים URL יציב בלי fragment
    p = urlparse(u)
    return p._replace(fragment="").geturl()


def extract_item_id(url: str) -> str:
    m = re.search(r"/realestate/item/(\d+)", url)
    return m.group(1) if m else url


def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def extract_listing_links(search_html: str, search_url: str) -> list[str]:
    """
    מוצאים את כל הלינקים של מודעות מתוך עמוד התוצאות.
    """
    soup = BeautifulSoup(search_html, "lxml")
    links = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/realestate/item/" in href:
            full = normalize_url(urljoin(BASE, href))
            links.add(full)

    # fallback קטן אם Yad2 שינו משהו והלינקים יושבים בסקריפטים
    if not links:
        for m in re.findall(r'"/realestate/item/\d+[^"]*"', search_html):
            href = m.strip('"')
            full = normalize_url(urljoin(BASE, href))
            links.add(full)

    return sorted(links)


def extract_title_from_item_page(item_html: str) -> str:
    """
    לא חובה. אם נמצא כותרת – נצרף אותה להודעה.
    אם לא – נשלח רק URL.
    """
    soup = BeautifulSoup(item_html, "lxml")

    # נסיון 1: title של הדף
    t = (soup.title.string if soup.title and soup.title.string else "").strip()
    if t:
        t = re.sub(r"\s+", " ", t)
        return t[:120]

    # נסיון 2: h1
    h1 = soup.find("h1")
    if h1:
        txt = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
        return txt[:120]

    return ""


def main():
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID (set in GitHub Secrets).")

    cfg = load_json("config.json", {})
    searches = cfg.get("searches", [])
    max_new_per_run = int(cfg.get("max_new_per_run", 15))

    seen = load_json(SEEN_FILE, {"ids": []})
    seen_ids = set(seen.get("ids", []))

    total_sent = 0
    debug = []

    for s in searches:
        name = s.get("name", "חיפוש")
        url = s.get("url")
        if not url:
            continue

        html = fetch(url)
        links = extract_listing_links(html, url)

        debug.append(f"{name}: נמצאו {len(links)} מודעות בעמוד התוצאות")

        # רק החדשים
        new_links = []
        for l in links:
            item_id = extract_item_id(l)
            if item_id not in seen_ids:
                new_links.append((item_id, l))

        debug.append(f"{name}: חדשות שלא נשלחו עדיין: {len(new_links)}")

        # שולחים עד max_new_per_run לכל הריצה (לא להציף)
        for item_id, link in new_links:
            if total_sent >= max_new_per_run:
                break

            title = ""
            try:
                item_html = fetch(link)  # "נכנס לדירה"
                title = extract_title_from_item_page(item_html)
            except Exception:
                # אם כניסה למודעה נכשלה, עדיין נשלח את הלינק
                title = ""

            msg = f"{name}\n{title + chr(10) if title else ''}{link}"
            tg_send(bot_token, chat_id, msg)

            seen_ids.add(item_id)
            total_sent += 1
            time.sleep(0.6)

    # סיכום קצר (כדי שתדעי שזה עובד גם אם אין חדשים)
    summary = f"✅ ריצה הסתיימה. נשלחו {total_sent} מודעות חדשות.\n" + "\n".join(debug)
    tg_send(bot_token, chat_id, summary)

    save_json(SEEN_FILE, {"ids": sorted(seen_ids)})


if __name__ == "__main__":
    main()

