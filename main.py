import os
import re
import json
import time
import html
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.getenv("SEARCH_URL", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

SEEN_FILE = "seen_urls.json"
BASE = "https://www.yad2.co.il"


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def canonicalize(url: str) -> str:
    """
    מנקה פרמטרים מיותרים כדי שלא תקבלי אותה דירה עם וריאציות שונות של query.
    לדוגמה: opened-from-feed, component-type וכו'
    """
    p = urlparse(url)
    # נשאיר רק scheme+netloc+path
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.yad2.co.il/",
        "Connection": "keep-alive",
    }

    # Session עוזר לעוגיות בסיסיות
    with requests.Session() as s:
        r = s.get(url, headers=headers, timeout=25)
        r.raise_for_status()
        return r.text


def extract_item_urls(page_html: str) -> list[str]:
    """
    מחלץ URLs של מודעות:
    1) BeautifulSoup על תגיות <a>
    2) fallback עם Regex על כל הטקסט (אם האתר שם לינקים בתוך JS)
    """
    page_html = html.unescape(page_html)

    urls: set[str] = set()

    # 1) HTML anchors
    soup = BeautifulSoup(page_html, "lxml")
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if "/realestate/item/" in href:
            full = urljoin(BASE, href)
            urls.add(canonicalize(full))

    # 2) Regex fallback (תופס גם אם אין <a> “אמיתי”)
    # תופס: /realestate/item/xxxxxxxx
    for m in re.findall(r'\/realestate\/item\/[A-Za-z0-9]+', page_html):
        full = urljoin(BASE, m)
        urls.add(canonicalize(full))

    return sorted(urls)


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID env vars")

    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False}
    r = requests.post(api, json=payload, timeout=20)
    r.raise_for_status()


def main():
    if not SEARCH_URL:
        raise RuntimeError("Missing SEARCH_URL env var")

    seen = load_seen()

    html_text = fetch_html(SEARCH_URL)
    found_urls = extract_item_urls(html_text)

    # רק חדשים
    new_urls = [u for u in found_urls if u not in seen]

    # עדכון seen
    for u in new_urls:
        seen.add(u)
    save_seen(seen)

    if not new_urls:
        # כדי שתראי שזה באמת מחלץ משהו (debug שימושי)
        msg = f"✅ ריצה הסתיימה. נמצאו {len(found_urls)} מודעות בעמוד. חדשות שלא נשלחו: 0"
        send_telegram(msg)
        return

    # שולחים הודעה מסודרת עם לינקים
    header = f"🏠 נמצאו {len(new_urls)} מודעות חדשות (מתוך {len(found_urls)} בעמוד):\n"
    body = "\n".join(new_urls[:15])  # שלא נחטוף מגבלת הודעה; אפשר לשנות
    send_telegram(header + body)

    # אם יש יותר מ-15 – שולחים עוד הודעה
    rest = new_urls[15:]
    if rest:
        time.sleep(1)
        send_telegram("📌 המשך:\n" + "\n".join(rest[:15]))


if __name__ == "__main__":
    main()
