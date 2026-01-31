import json
import os
import re
import time
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://www.yad2.co.il"


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_item_url(href: str) -> Tuple[str, str]:
    """
    Convert href -> (full_url, uid)
    uid is the part after /realestate/item/ up to ? or end.
    """
    if not href:
        return "", ""

    # Make full URL
    full = href.strip()
    if full.startswith("//"):
        full = "https:" + full
    elif full.startswith("/"):
        full = urljoin(BASE, full)
    elif full.startswith("http://") or full.startswith("https://"):
        pass
    else:
        # relative without leading slash
        full = urljoin(BASE + "/", full)

    # Keep only yad2 domain (safety)
    try:
        host = urlparse(full).netloc
        if "yad2.co.il" not in host:
            return "", ""
    except Exception:
        return "", ""

    if "/realestate/item/" not in full:
        return "", ""

    uid = full.split("/realestate/item/", 1)[1]
    uid = uid.split("?", 1)[0].strip().strip("/")

    # uid in yad2 is alphanumeric like yxpmvxkt (not numeric!)
    if not uid or len(uid) < 5:
        return "", ""

    # Normalize URL without tracking query params
    full_clean = urljoin(BASE, f"/realestate/item/{uid}")
    return full_clean, uid


def extract_item_links(html: str) -> List[Tuple[str, str]]:
    """
    Returns list of (url, uid) from a Yad2 rent results HTML.
    """
    soup = BeautifulSoup(html, "html.parser")

    found: List[Tuple[str, str]] = []
    seen_uids: Set[str] = set()

    # First: extract <a href="..."> links
    for a in soup.find_all("a", href=True):
        url, uid = normalize_item_url(a["href"])
        if url and uid and uid not in seen_uids:
            found.append((url, uid))
            seen_uids.add(uid)

    # Fallback: regex scan (in case links appear in scripts or data attributes)
    if not found:
        # Capture /realestate/item/<id>
        matches = set(re.findall(r"/realestate/item/([a-zA-Z0-9_-]{5,})", html))
        for uid in matches:
            url = urljoin(BASE, f"/realestate/item/{uid}")
            if uid not in seen_uids:
                found.append((url, uid))
                seen_uids.add(uid)

    return found


def fetch(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": BASE + "/",
    }

    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def telegram_send(bot_token: str, chat_id: str, text: str, disable_preview: bool = False):
    api = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    resp = requests.post(api, json=payload, timeout=30)
    resp.raise_for_status()


def main():
    # Secrets from GitHub Actions
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
    CHAT_ID = os.getenv("CHAT_ID", "").strip()

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID environment variables.")

    config = load_json("config.json", {})
    sources: List[Dict] = config.get("sources", [])

    if not sources:
        raise RuntimeError("config.json must include sources list.")

    seen_state = load_json("seen_ids.json", {"seen": []})
    seen: Set[str] = set(seen_state.get("seen", []))

    total_new = 0
    debug_lines = []

    for src in sources:
        name = src.get("name", "חיפוש")
        url = src.get("url")
        if not url:
            continue

        # polite delay between sources
        time.sleep(1.2)

        try:
            html = fetch(url)
            links = extract_item_links(html)

            debug_lines.append(f"{name}: נמצאו בעמוד {len(links)} מודעות (לפני סינון כפילויות).")

            new_links = [(u, uid) for (u, uid) in links if uid not in seen]

            if new_links:
                # Send header per source
                telegram_send(BOT_TOKEN, CHAT_ID, f"🏡 עדכונים – {name}\nנמצאו {len(new_links)} מודעות חדשות:")

                # Send links
                for (u, uid) in new_links:
                    telegram_send(BOT_TOKEN, CHAT_ID, u, disable_preview=True)
                    seen.add(uid)
                    total_new += 1
                    # small pause to avoid telegram rate limits
                    time.sleep(0.4)
            else:
                telegram_send(BOT_TOKEN, CHAT_ID, f"✅ {name}: אין מודעות חדשות כרגע.", disable_preview=True)

        except Exception as e:
            # Send error but keep running for other sources
            telegram_send(BOT_TOKEN, CHAT_ID, f"⚠️ {name}: שגיאה בסריקה:\n{type(e).__name__}: {e}", disable_preview=True)

    # Save state back
    save_json("seen_ids.json", {"seen": sorted(seen)})

    # Optional debug summary (comment out if you don't want it)
    summary = "📌 סיכום ריצה:\n" + "\n".join(debug_lines) + f"\nסה״כ חדשות שנשלחו: {total_new}"
    telegram_send(BOT_TOKEN, CHAT_ID, summary, disable_preview=True)


if __name__ == "__main__":
    main()
