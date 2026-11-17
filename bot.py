import html
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# Секреты берутся из GitHub Secrets (не из кода)
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

# Твой фильтр List.am: Ереван, продажа квартир, от собственника
QUERY = "n=2%2C3%2C4%2C5%2C6%2C7%2C8%2C9%2C10%2C13%2C11&cmtype=0&crc=1"
MAX_PAGES = 5          # сколько страниц просматривать
MAX_SEND_PER_RUN = 40  # защита от спама, если что-то пошло не так
SEEN_FILE = "seen_ads.json"

ITEM_RE = re.compile(r"/item/(\d+)")


def page_url(n):
    if n == 1:
        return f"https://www.list.am/category/60?{QUERY}"
    return f"https://www.list.am/category/60/{n}?{QUERY}"


def send_telegram(text):
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(api, json=payload, timeout=20)
        data = r.json()
        if not data.get("ok"):
            print("Ошибка Telegram:", data)
            return False
        return True
    except Exception as e:
        print("Ошибка запроса в Telegram:", e)
        return False


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return list(json.load(f))
        except Exception:
            return []
    return []


def save_seen(seen_list):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-20000:], f)


def ad_id_of(tag):
    m = ITEM_RE.search(tag.get("href", ""))
    return m.group(1) if m else None


def parse_page(page_html):
    """Возвращает {ad_id: (текст карточки, ссылка)} в порядке на странице."""
    soup = BeautifulSoup(page_html, "html.parser")
    ads = {}
    for a in soup.find_all("a", href=True):
        ad_id = ad_id_of(a)
        if not ad_id or ad_id in ads:
            continue
        # поднимаемся вверх, пока в блоке только это одно объявление
        node = a
        while node.parent is not None and node.parent.name not in ("body", "html"):
            parent = node.parent
            ids = {ad_id_of(x) for x in parent.find_all("a", href=True)}
            ids.discard(None)
            if len(ids) > 1:
                break
            node = parent
        text = " ".join(node.get_text(" ", strip=True).split())
        if len(text) < 3:
            text = "Квартира в Ереване"
        ads[ad_id] = (text[:300], f"https://www.list.am/item/{ad_id}")
    return ads


def fetch_all():
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        for n in range(1, MAX_PAGES + 1):
            try:
                page.goto(page_url(n), timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                page.wait_for_timeout(1000)
                ads = parse_page(page.content())
            except Exception as e:
                print(f"Страница {n}: ошибка {e}")
                break
            print(f"Страница {n}: найдено {len(ads)}")
            if not ads:
                break
            for k, v in ads.items():
                found.setdefault(k, v)
            time.sleep(2)
        browser.close()
    return found


def main():
    first_run = not os.path.exists(SEEN_FILE)
    seen_list = load_seen()
    seen = set(seen_list)

    found = fetch_all()
    if not found:
        print("Объявления не найдены (возможно, сайт заблокировал запрос).")
        sys.exit(1)

    new_ids = [i for i in found if i not in seen]
    print(f"Всего на страницах: {len(found)}, новых: {len(new_ids)}")

    if first_run:
        save_seen(list(found.keys()))
        send_telegram(f"✅ Бот запущен. База сохранена: {len(found)} объявлений. "
                      "Дальше буду присылать только новые.")
        return

    to_send = list(reversed(new_ids))[-MAX_SEND_PER_RUN:]
    # старые первыми, самые свежие последними
    sent = 0
    for ad_id in to_send:
        text, url = found[ad_id]
        msg = (
            "🏠 <b>Новая квартира в Ереване (собственник)</b>\n\n"
            f"{html.escape(text)}\n\n"
            f"🔗 <a href='{url}'>Открыть на List.am</a>"
        )
        if send_telegram(msg):
            seen_list.append(ad_id)
            sent += 1
        time.sleep(0.5)

    # то, что выходит за лимит отправки, помечаем как увиденное, чтобы не копилось
    skipped = [i for i in new_ids if i not in to_send]
    seen_list.extend(skipped)
    save_seen(seen_list)
    print(f"Отправлено: {sent}")


if __name__ == "__main__":
    main()
