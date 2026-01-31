import json
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

BASE = "https://www.yad2.co.il"
ITEM_RE = re.compile(r"^/realestate/item/")

SEEN_FILE = "seen.json"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config():
    cfg = load_json("config.json", {})
    # env overrides
    search_url = os.getenv("SEARCH_URL") or cfg.get("search_url")
    if not search_url:
        raise RuntimeError("Missing SEARCH_URL (env) and config.json.search_url")

    scroll_rounds = int(os.getenv("SCROLL_ROUNDS") or cfg.get("scroll_rounds", 6))
    max_links = int(os.getenv("MAX_LINKS_TO_SEND") or cfg.get("max_links_to_send", 20))

    return {
        "search_url": search_url,
        "scroll_rounds": scroll_rounds,
        "max_links_to_send": max_links,
    }


def extract_item_links(page):
    # Grab all anchors and filter those that look like listing items
    hrefs = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
    )

    links = []
    for href in hrefs:
        if ITEM_RE.match(href):
            full = urljoin(BASE, href)
            links.append(full)

    # de-dupe while preserving order
    seen = set()
    uniq = []
    for l in links:
        if l not in seen:
            uniq.append(l)
            seen.add(l)
    return uniq


def try_close_popups(page):
    # Yad2 sometimes shows cookie/consent dialogs.
    # We'll try a few common Hebrew buttons. If none exist, no harm.
    candidates = ["הבנתי", "מאשר", "אישור", "מסכימ", "קבל", "Accept", "I agree"]
    for text in candidates:
        try:
            btn = page.get_by_role("button", name=re.compile(text))
            if btn.count() > 0:
                btn.first.click(timeout=1500)
                time.sleep(0.3)
        except Exception:
            pass


def fetch_links_with_browser(search_url, scroll_rounds):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="he-IL",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        try_close_popups(page)

        # Wait for any content to load
        page.wait_for_timeout(1500)

        # Scroll to load more results (infinite scroll / lazy load)
        for _ in range(scroll_rounds):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1200)

        links = extract_item_links(page)

        context.close()
        browser.close()

        return links


def send_telegram_message(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    cfg = get_config()

    bot_token = os.getenv("BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError("Missing BOT_TOKEN or CHAT_ID env vars (GitHub Secrets).")

    seen_data = load_json(SEEN_FILE, {"seen": []})
    seen_set = set(seen_data.get("seen", []))

    links = fetch_links_with_browser(cfg["search_url"], cfg["scroll_rounds"])

    # Filter new only
    new_links = [l for l in links if l not in seen_set]

    # cap sends
    new_links = new_links[: cfg["max_links_to_send"]]

    if not links:
        # diagnostic message
        send_telegram_message(
            bot_token,
            chat_id,
            "⚠️ לא הצלחתי לחלץ לינקים מהעמוד (0). זה בדרך כלל אומר שהדף נטען בצורה שונה/חסם בוטים. תגידי לי ונקשיח עוד."
        )
        return

    if not new_links:
        send_telegram_message(
            bot_token,
            chat_id,
            f"✅ ריצה הסתיימה. נמצאו {len(links)} מודעות בעמוד. חדשות שלא נשלחו: 0"
        )
        return

    # Send each new link
    header = "🏠 מודעות חדשות ביד2:"
    send_telegram_message(bot_token, chat_id, header)

    for l in new_links:
        send_telegram_message(bot_token, chat_id, l)
        seen_set.add(l)

    # persist seen
    save_json(SEEN_FILE, {"seen": list(seen_set)})

    send_telegram_message(
        bot_token,
        chat_id,
        f"✅ נשלחו {len(new_links)} קישורים חדשים. (סה\"כ נשמרו למניעת כפילויות: {len(seen_set)})"
    )


if __name__ == "__main__":
    main()
